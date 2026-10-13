"""Graph planner → codegen → render → test_run với LLM, nguồn và sandbox giả (không gọi mạng, không cần Postgres)."""

import json
from types import SimpleNamespace

import pytest

from src.agents import service
from src.agents.graph import build_graph
from src.agents.nodes.common import AgentDeps
from src.agents.service import confirm_plan, run_agent, start_agent
from src.codegen.sandbox import SandboxResult
from src.pipeline import auth
from src.policy.loader import load_policy
from src.run_report import Check, GateResult, get_run_report
from src.run_report.store import get_run_files
from src.spec import SPEC_FIXTURES_DIR

SPEC = json.loads((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
PLAN = {
    "domain": "sales",
    "summary": "Doanh thu và số đơn theo ngày từ đơn hàng Postgres",
    "model_name": "fct_daily_orders",
    "sources": [{"id": "orders", "source_ref": "sales_pg", "object": "orders", "purpose": "đơn hàng"}],
    "grain": ["ordered_at_text"],
    "metrics": [{"name": "revenue", "definition": "tổng amount_text"}],
    "schedule": "0 2 * * *",
    "question": None,
}
CATALOG = {
    "sales": {
        "sources": {
            "sales_pg": {
                "kind": "postgres",
                "available": True,
                "objects": {"orders": ["order_id", "customer_id", "amount_text", "ordered_at_text", "status"]},
            },
            "sales_api": {"kind": "rest_api", "available": False, "reason": "chưa hỗ trợ ở Sprint 1"},
        },
        "entities": {},
    }
}
SNAPSHOT = {
    "orders": {
        "source_ref": "sales_pg",
        "kind": "postgres",
        "table": "orders",
        "columns": {
            "order_id": "integer",
            "customer_id": "integer",
            "amount_text": "text",
            "ordered_at_text": "text",
            "status": "text",
        },
        "sample_rows": [{"order_id": "1", "amount_text": "1000", "ordered_at_text": "2026-01-01"}],
    }
}


def with_column(spec: dict, index: int, **changes) -> dict:
    spec = json.loads(json.dumps(spec))
    spec["sources"][0]["staging"]["columns"][index].update(changes)
    return spec


class FakeLLM:
    """Trả lần lượt các câu trả lời dựng sẵn; ghi lại tier và nội dung để kiểm tra prompt."""

    def __init__(self, *answers):
        self.answers = [a if isinstance(a, str) else json.dumps(a) for a in answers]
        self.calls = []

    def __call__(self, tier, system, user):
        self.calls.append({"tier": tier, "system": system, "user": json.loads(user)})
        return self.answers.pop(0)


def tiered_llm(tier, system, user):
    """Cho test API: mỗi request tạo deps mới, nên trả theo tier thay vì theo thứ tự (planner rẻ, codegen mạnh)."""
    return json.dumps(PLAN if tier == "cheap" else SPEC)


def passing_sandbox(spec, files, policy, settings, rerun_model=0):
    return SandboxResult(
        "READY_FOR_REVIEW",
        "t_validate",
        gates=[GateResult(name="post_run", result="pass", detail="3/3 phép kiểm tra đạt")],
        checks=[Check(name="dbt_test", result="pass", observed={"passed": 5, "failed": 0})],
        duration_sec=1.5,
    )


def small_cost(spec, policy, settings):
    """Ước tính chi phí giả cho test: vài KB mỗi nguồn, không đọc nguồn thật."""
    sources = {source.id: 4096 for source in spec.sources}
    total = sum(sources.values()) * 2
    return {"sources": sources, "model_bytes": total // 2, "bytes": total, "usd": 1e-8, "estimated": True}


def deps(llm, policy=None, sandbox=passing_sandbox, estimate_cost=small_cost, spent_today=lambda settings: 0.0):
    return AgentDeps(
        llm=llm,
        policy=policy or load_policy(),
        settings=SimpleNamespace(),
        catalog=lambda policy, settings: CATALOG,
        snapshot=lambda plan, policy, settings: SNAPSHOT,
        sandbox=sandbox,
        estimate_cost=estimate_cost,
        spent_today=spent_today,
    )


def run(llm, scope=None, **kwargs) -> dict:
    return build_graph(deps(llm, **kwargs)).invoke(
        {
            "run_id": "r1",
            "version": 1,
            "user_request": "Doanh thu theo ngày",
            "llm_calls": 0,
            "error_trace": [],
            "source_scope": scope,
        }
    )


def test_happy_path_runs_four_steps():
    llm = FakeLLM(PLAN, SPEC)
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW" and state["stage_reached"] == "t_validate"
    assert [c["tier"] for c in llm.calls] == ["cheap", "strong"]
    prompt_snapshot = llm.calls[1]["user"]["schema_snapshot"]
    assert prompt_snapshot["orders"]["columns"] == SNAPSHOT["orders"]["columns"]
    assert prompt_snapshot["orders"]["value_profiles"]["ordered_at_text"]["date_formats"] == ["%Y-%m-%d"]
    assert "sample_rows" not in prompt_snapshot["orders"]
    assert state["llm_calls"] == 2 and state["domain"] == "sales"
    assert len(state["generated_files"]) == 5 and set(state["files"]) == set(state["generated_files"])
    assert state["files_hash"].startswith("sha256:")
    assert [g["name"] for g in state["gates"]] == [
        "semantic",
        "spec_schema",
        "schema_real",
        "currency",
        "join_fanout",
        "filter_values",
        "time_grain",
        "ast_guard",
        "budget",
        "arbitrate",
        "post_run",
    ]


@pytest.mark.parametrize("scope", [None, [{"source_ref": "sales_pg", "tables": ["orders"]}]])
def test_source_name_as_domain_continues_through_graph_without_plan_retry(scope):
    llm = FakeLLM({**PLAN, "domain": "sales_pg"}, SPEC)
    state = run(llm, scope=scope)

    assert (state["status"], state["stage_reached"], state["llm_calls"]) == ("READY_FOR_REVIEW", "t_validate", 2)
    assert state["domain"] == state["plan"]["domain"] == "sales"
    assert [call["tier"] for call in llm.calls] == ["cheap", "strong"]
    assert llm.calls[1]["user"]["kế_hoạch"]["domain"] == "sales"


def test_invalid_spec_goes_back_to_codegen_with_specific_error():
    wrong = with_column(SPEC, 2, **{"from": "amount"})  # cột không có ở nguồn
    llm = FakeLLM(PLAN, wrong, SPEC)
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW"
    assert state["attempts"]["spec"] == 1 and state["llm_calls"] == 3
    feedback = llm.calls[2]["user"]["lỗi_lần_trước"]
    assert any("cột 'amount' không có ở nguồn" in error for error in feedback)
    attempts = [
        h for h in state["spec_history"] if "attempt" in h
    ]  # bỏ mục P5 đổi lịch chạy (không phải lần viết Spec)
    assert [h["errors"] == [] for h in attempts] == [False, True]


def test_spec_loop_stops_after_policy_limit():
    wrong = with_column(SPEC, 2, **{"from": "amount"})  # cột không có ở nguồn: lỗi không sửa tất định được
    llm = FakeLLM(PLAN, wrong, wrong, wrong)
    state = run(llm)
    assert state["status"] == "NEEDS_HUMAN" and state["stage_reached"] == "static"
    assert state["attempts"]["spec"] == 3 and state["llm_calls"] == 4
    assert "max_spec_attempts" in state["error_trace"][-1]["error"]
    assert "generated_files" not in state


@pytest.mark.parametrize("given", [None, []])
def test_missing_date_formats_are_filled_from_sample_rows(given):
    """LLM bỏ hoặc để rỗng `date_formats` cho cột chuỗi → ngày: suy tất định từ dòng mẫu, không tốn vòng sửa Spec."""
    llm = FakeLLM(PLAN, with_column(SPEC, 3, date_formats=given))
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW" and state["attempts"]["spec"] == 0 and state["llm_calls"] == 2
    column = next(c for c in state["spec"]["sources"][0]["staging"]["columns"] if c["from"] == "ordered_at_text")
    assert column["date_formats"] == ["%Y-%m-%d"]


def test_semantic_conflict_wins_over_a_clarifying_question():
    """Yêu cầu vừa có câu hỏi làm rõ vừa có thực thể hai nghĩa: chặn ngay, vì trả lời câu hỏi không gỡ được xung đột."""
    asking = {**PLAN, "question": "Gộp theo tháng bằng cột nào?"}
    state = build_graph(deps(FakeLLM(asking))).invoke(
        {"run_id": "r1", "version": 1, "user_request": "Tổng tiền mỗi khách đã chi", "llm_calls": 0, "error_trace": []}
    )
    assert state["status"] == "BLOCKED_SEMANTIC_CONFLICT" and state["stage_reached"] == "resolve"
    assert [g["name"] for g in state["gates"]] == ["semantic"] and state["llm_calls"] == 1


def test_plan_whose_concepts_are_not_in_the_request_asks_what_to_measure():
    """ "Báo cáo về bán hàng" không nói đo gì: LLM tự nghĩ ra chỉ số thì hệ thống hỏi lại, không chạy."""
    invented = {**PLAN, "concepts": [{"term": "số tiền bán hàng", "column": "orders.amount_text"}]}
    state = build_graph(deps(FakeLLM(invented))).invoke(
        {"run_id": "r1", "version": 1, "user_request": "Làm báo cáo về bán hàng", "llm_calls": 0, "error_trace": []}
    )
    assert state["status"] == "NEEDS_HUMAN" and "chưa nêu cần đo chỉ số nào" in state["error_trace"][-1]["question"]


def test_concepts_named_in_the_request_go_ahead():
    from src.agents.nodes.planner import grounded

    assert grounded("doanh thu", "Doanh thu theo ngày") and grounded("tổng tiền hoàn", "Tổng tiền hoàn của đơn")
    assert grounded("khách hàng", "Theo từng khách hàng: tổng tiền đơn hàng")
    assert not grounded("số tiền bán hàng", "Làm cho tôi một báo cáo về bán hàng")
    planned = {**PLAN, "concepts": [{"term": "doanh thu", "column": "orders.amount_text"}]}
    assert run(FakeLLM(planned, SPEC))["status"] == "READY_FOR_REVIEW"


def test_plain_clarifying_question_still_waits_for_the_user():
    asking = {**PLAN, "question": "Đo chỉ số nào?"}
    state = run(FakeLLM(asking))
    assert state["status"] == "NEEDS_HUMAN" and state["error_trace"][-1]["question"] == "Đo chỉ số nào?"


def test_mixed_date_styles_get_every_format_they_need():
    from src.agents.nodes.codegen import _fix_date_formats, covering_formats

    refunded_at = ["2026-01-05", "2026-01-21", "21/01/2026", "02/02/2026", "15/02/2026", "không rõ"]
    # 02/02/2026 parse được cả ngày/tháng lẫn tháng/ngày; 21/01/2026 chỉ là ngày/tháng → chọn %d/%m/%Y, không %m/%d/%Y
    assert set(covering_formats(refunded_at)) == {"%Y-%m-%d", "%d/%m/%Y"}
    column = {"from": "refunded_at", "as": "refunded_date", "type": "date", "date_formats": ["%Y-%m-%d"]}
    _fix_date_formats(column, refunded_at)
    assert column["date_formats"] == ["%Y-%m-%d", "%d/%m/%Y"], "giữ định dạng LLM chọn, thêm định dạng còn thiếu"


def test_llm_call_cap_comes_from_budgets():
    policy = load_policy()
    per_run = policy.budgets.per_run.model_copy(update={"max_llm_calls": 1})
    policy = policy.model_copy(update={"budgets": policy.budgets.model_copy(update={"per_run": per_run})})
    state = run(FakeLLM(PLAN, SPEC), policy=policy)
    assert state["status"] == "NEEDS_HUMAN" and state["stage_reached"] == "propose"
    assert state["llm_calls"] == 1 and "max_llm_calls" in state["error_trace"][-1]["error"]


def test_unsafe_expression_is_blocked_without_render():
    unsafe = json.loads(json.dumps(SPEC))
    unsafe["transform"]["filters"] = ["status <> '{{ env_var(\"OPENAI_API_KEY\") }}'"]
    state = run(FakeLLM(PLAN, unsafe))
    assert state["status"] == "BLOCKED_AST" and "generated_files" not in state
    assert state["gates"][-1]["name"] == "ast_guard" and state["gates"][-1]["result"] == "fail"


def test_plan_question_stops_for_human():
    state = run(FakeLLM({**PLAN, "question": "Doanh thu tính trước hay sau hoàn tiền?"}))
    assert state["status"] == "NEEDS_HUMAN" and state["stage_reached"] == "plan"
    assert state["error_trace"][-1]["question"] == "Doanh thu tính trước hay sau hoàn tiền?"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"source_ref": "sales_pg", "object": "refunds"}, "bảng 'refunds' không có"),
        ({"source_ref": "sales_api", "object": "payments"}, "chưa hỗ trợ ở Sprint 1"),
        ({"source_ref": "mkt_api", "object": "leads"}, "không thuộc domain 'sales'"),
    ],
)
def test_plan_must_point_at_readable_sources(source, message):
    plan = {**PLAN, "sources": [{"id": "x", **source}]}
    llm = FakeLLM(plan, plan)
    state = run(llm)
    assert state["status"] == "NEEDS_HUMAN" and message in state["error_trace"][-1]["error"]
    assert state["llm_calls"] == 2 and any(message in e for e in llm.calls[1]["user"]["lỗi_lần_trước"])


CUSTOMERS = ["customer_id", "name", "region"]
JOIN_CUSTOMERS = [{"left": "orders.customer_id", "right": "customers.customer_id", "type": "left"}]


def test_plan_retries_once_when_join_points_at_a_column_instead_of_a_table(monkeypatch):
    # run 870fe465: LLM coi cột `district` của address là một bảng. Lần sửa nhận gợi ý cụ thể rồi qua được.
    monkeypatch.setitem(CATALOG["sales"]["sources"]["sales_pg"]["objects"], "customers", CUSTOMERS)
    bad = {**PLAN, "joins": [{"left": "orders.customer_id", "right": "region.region", "type": "inner"}]}
    llm = FakeLLM(bad, PLAN, SPEC)
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW" and state["llm_calls"] == 3
    feedback = llm.calls[1]["user"]["lỗi_lần_trước"]
    assert any("'region' là cột của bảng customers" in e for e in feedback)
    assert llm.calls[1]["user"]["kế_hoạch_lần_trước"]["joins"][0]["right"] == "region.region"


def test_plan_join_to_selected_table_missing_from_sources_is_completed_without_llm(monkeypatch):
    # Model rẻ hay chỉ ghi bảng chính vào sources: bảng có trong phạm vi đã chọn thì tự thêm, không tốn lượt LLM.
    monkeypatch.setitem(CATALOG["sales"]["sources"]["sales_pg"]["objects"], "customers", CUSTOMERS)
    llm = FakeLLM({**PLAN, "joins": JOIN_CUSTOMERS}, SPEC)
    state = run(llm, scope=[{"source_ref": "sales_pg", "tables": ["orders", "customers"]}])
    assert state["status"] == "READY_FOR_REVIEW" and state["llm_calls"] == 2
    assert [s["object"] for s in state["plan"]["sources"]] == ["orders", "customers"]


def test_plan_needing_a_table_outside_the_selection_stops_with_what_to_select(monkeypatch):
    # Người dùng chỉ chọn orders mà yêu cầu cần customers: LLM không được tự mở rộng phạm vi, dừng ngay ở kế hoạch.
    monkeypatch.setitem(CATALOG["sales"]["sources"]["sales_pg"]["objects"], "customers", CUSTOMERS)
    state = run(FakeLLM({**PLAN, "joins": JOIN_CUSTOMERS}), scope=[{"source_ref": "sales_pg", "tables": ["orders"]}])
    assert state["status"] == "NEEDS_HUMAN" and state["stage_reached"] == "plan" and state["llm_calls"] == 1
    error = state["error_trace"][-1]["error"]
    assert "ngoài phạm vi đã chọn: sales_pg.customers" in error and "Chọn thêm" in error
    assert state["error_trace"][-1]["need_tables"] == ["sales_pg.customers"]


def test_join_must_follow_a_foreign_key(monkeypatch):
    # Hai cột có thật nhưng không có quan hệ (payment.customer_id = address.address_id): số liệu sẽ sai, gửi lại LLM.
    sales_pg = CATALOG["sales"]["sources"]["sales_pg"]
    monkeypatch.setitem(sales_pg["objects"], "customers", CUSTOMERS)
    monkeypatch.setitem(sales_pg, "relations", ["orders.customer_id -> customers.customer_id"])
    customers = {"id": "customers", "source_ref": "sales_pg", "object": "customers"}
    wrong = {
        **PLAN,
        "sources": [*PLAN["sources"], customers],
        "joins": [{"left": "orders.order_id", "right": "customers.customer_id", "type": "left"}],
    }
    llm = FakeLLM(wrong, PLAN, SPEC)
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW"
    feedback = llm.calls[1]["user"]["lỗi_lần_trước"]
    assert any("không khớp khóa ngoại" in e and "orders.customer_id -> customers.customer_id" in e for e in feedback)
    assert llm.calls[0]["user"]["danh_mục"]["sales"]["sources"]["sales_pg"]["relations"]


def test_narrowed_catalog_keeps_only_relations_between_selected_tables(monkeypatch):
    sales_pg = CATALOG["sales"]["sources"]["sales_pg"]
    monkeypatch.setitem(sales_pg["objects"], "customers", CUSTOMERS)
    monkeypatch.setitem(sales_pg, "relations", ["orders.customer_id -> customers.customer_id"])
    llm = FakeLLM(PLAN, SPEC)
    run(llm, scope=[{"source_ref": "sales_pg", "tables": ["orders"]}])
    assert llm.calls[0]["user"]["danh_mục"]["sales"]["sources"]["sales_pg"]["relations"] == []


def test_concept_without_a_column_becomes_a_question_instead_of_another_metric():
    # Yêu cầu "theo khu vực" mà chỉ có bảng đơn hàng: LLM đánh dấu column null, hệ thống hỏi thay vì gộp theo ngày.
    plan = {
        **PLAN,
        "concepts": [{"term": "khu vực", "column": None}, {"term": "doanh thu", "column": "orders.amount_text"}],
    }
    state = run(FakeLLM(plan))
    assert state["status"] == "NEEDS_HUMAN" and state["stage_reached"] == "plan" and state["llm_calls"] == 1
    assert "không có cột nào thể hiện: khu vực" in state["error_trace"][-1]["question"]


def test_concept_pointing_at_unknown_column_is_sent_back():
    bad = {**PLAN, "concepts": [{"term": "khu vực", "column": "orders.region"}]}
    llm = FakeLLM(bad, PLAN, SPEC)
    assert run(llm)["status"] == "READY_FOR_REVIEW"
    assert any("concepts 'khu vực'" in e for e in llm.calls[1]["user"]["lỗi_lần_trước"])


def test_codegen_declares_forgotten_join_keys_in_staging():
    # run 3f485a6d: LLM ghép address.city_id nhưng 3 lần liền không khai báo cột này ở staging của address.
    from src.agents.nodes.codegen import _add_key_columns

    raw = {
        "sources": [
            {"id": "address", "staging": {"columns": [{"from": "address_id", "as": "address_id", "type": "integer"}]}},
            {"id": "city", "staging": {"columns": [{"from": "city_id", "as": "city_id", "type": "integer"}]}},
        ],
        "transform": {
            "from": "address",
            "joins": [{"source": "city", "on": [["address.city_id", "city.city_id"]]}],
            "grain": ["city.city"],
        },
    }
    snapshot = {
        "address": {"columns": {"address_id": "integer", "city_id": "smallint"}},
        "city": {"columns": {"city_id": "integer", "city": "text"}},
    }
    fixed = _add_key_columns(raw, snapshot)
    assert {"from": "city_id", "as": "city_id", "type": "integer"} in fixed["sources"][0]["staging"]["columns"]
    assert {"from": "city", "as": "city", "type": "varchar"} in fixed["sources"][1]["staging"]["columns"]
    assert len(fixed["sources"][1]["staging"]["columns"]) == 2  # city_id đã có, không thêm trùng


def test_codegen_maps_postgres_type_names_in_staging():
    from src.agents.nodes.codegen import _add_key_columns

    raw = {
        "sources": [
            {
                "id": "p",
                "staging": {
                    "columns": [
                        {"from": "a", "as": "a", "type": "smallint"},
                        {"from": "b", "as": "b", "type": "decimal(18,2)"},
                    ]
                },
            }
        ],
        "transform": {"joins": [], "grain": []},
    }
    types = [c["type"] for c in _add_key_columns(raw, {})["sources"][0]["staging"]["columns"]]
    assert types == ["integer", "decimal(18,2)"]


def test_plan_accepts_null_lists_from_llm():
    state = run(FakeLLM({**PLAN, "joins": None, "filters": None, "schedule": None}, SPEC))
    assert state["status"] == "READY_FOR_REVIEW" and state["plan"]["filters"] == []
    assert state["plan"]["schedule"] == "0 2 * * *"


def test_malformed_plan_gets_one_retry():
    llm = FakeLLM("không phải JSON", PLAN, SPEC)
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW" and state["llm_calls"] == 3
    assert llm.calls[1]["user"]["lỗi_lần_trước"]


def test_plan_self_join_is_sent_back(monkeypatch):
    bad = {**PLAN, "joins": [{"left": "orders.customer_id", "right": "orders.customer_id", "type": "inner"}]}
    llm = FakeLLM(bad, PLAN, SPEC)
    assert run(llm)["status"] == "READY_FOR_REVIEW"
    assert any("chính nó" in e for e in llm.calls[1]["user"]["lỗi_lần_trước"])


def test_plan_join_with_unknown_column_is_rejected(monkeypatch):
    monkeypatch.setitem(CATALOG["sales"]["sources"]["sales_pg"]["objects"], "customers", CUSTOMERS)
    customers = {"id": "customers", "source_ref": "sales_pg", "object": "customers"}
    bad = {
        **PLAN,
        "sources": [*PLAN["sources"], customers],
        "joins": [{"left": "orders.cust_id", "right": "customers.customer_id", "type": "left"}],
    }
    state = run(FakeLLM(bad, bad))
    assert state["status"] == "NEEDS_HUMAN"
    assert "cột 'cust_id' không có trong bảng 'orders'" in state["error_trace"][-1]["error"]


def test_selected_sources_narrow_what_planner_sees():
    llm = FakeLLM(PLAN, SPEC)
    state = run(llm, scope=[{"source_ref": "sales_pg", "tables": ["orders"]}])
    assert state["status"] == "READY_FOR_REVIEW"
    seen = llm.calls[0]["user"]["danh_mục"]
    assert list(seen) == ["sales"] and list(seen["sales"]["sources"]) == ["sales_pg"]
    assert list(seen["sales"]["sources"]["sales_pg"]["objects"]) == ["orders"]


def test_plan_outside_selected_sources_stops():
    plan = {**PLAN, "sources": [{"id": "orders", "source_ref": "sales_pg", "object": "orders"}]}
    state = run(FakeLLM(plan), scope=[{"source_ref": "sales_pg", "tables": ["customers"]}])
    assert state["status"] == "NEEDS_HUMAN" and "sales_pg.customers" in state["error_trace"][-1]["error"]
    assert state["llm_calls"] == 0  # bảng đã chọn không tồn tại: dừng trước khi gọi LLM


def test_llm_failure_is_infra_error():
    def broken(tier, system, user):
        raise TimeoutError("timed out")

    state = run(broken)
    assert state["status"] == "INFRA_ERROR" and state["stage_reached"] == "plan" and state["llm_calls"] == 0


def test_sandbox_failure_is_reported():
    def failing(spec, files, policy, settings):
        return SandboxResult("NEEDS_HUMAN", "t_run", error_trace=[{"stage": "t_run", "error": "dbt lỗi"}])

    llm = FakeLLM(PLAN, SPEC, SPEC, SPEC)  # dbt lỗi → vòng SQL, tối đa budgets.yml max_sql_attempts (2) lần
    state = run(llm, sandbox=failing)
    assert state["status"] == "NEEDS_HUMAN" and state["attempts"]["sql"] == 2 and len(llm.calls) == 4
    assert "dbt lỗi" in state["error_trace"][-2]["error"] and "max_sql_attempts" in state["error_trace"][-1]["error"]


def test_service_saves_report_and_files(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    report = run_agent("Doanh thu theo ngày", "minh", deps(FakeLLM(PLAN, SPEC)), store)
    assert report.status == "READY_FOR_REVIEW" and report.created_by == "minh"
    assert (
        report.plan["model_name"] == "fct_daily_orders" and report.spec["transform"]["model_name"] == "fct_daily_orders"
    )
    assert report.timing.baseline_sec == 1.5 and report.llm_calls == 2
    assert get_run_report(store, report.run_id) == report
    files = get_run_files(store, report.run_id, 1)
    assert set(files) == set(report.files) and len(files) == 5
    assert report.source_scope is None


def test_service_records_the_tables_the_user_selected(tmp_path):
    store, scope = str(tmp_path / "runs.sqlite3"), [{"source_ref": "sales_pg", "tables": ["orders"]}]
    report = run_agent("Doanh thu theo ngày", "minh", deps(FakeLLM(PLAN, SPEC)), store, sources=scope)
    assert get_run_report(store, report.run_id).source_scope == scope


def test_spec_with_field_errors_also_reports_undeclared_join_sources():
    # run 870fe465: lỗi kiểu cột che mất lỗi "joins nhắc nguồn chưa khai báo", LLM tốn thêm một lượt sửa.
    from src.agents.validate import validate_spec

    broken = with_column(SPEC, 1, type="smallint")
    broken["transform"]["joins"] = [
        {"source": "customers", "type": "left", "on": [["orders.customer_id", "customers.customer_id"]]}
    ]
    result = validate_spec(broken, SNAPSHOT, load_policy())
    assert any("smallint" in e for e in result.errors)
    assert any("đang có ['customers']" in e and "khai báo đủ ở sources" in e for e in result.errors)


def test_progress_is_saved_after_each_step_and_background_job_finishes(tmp_path, monkeypatch):
    store = str(tmp_path / "runs.sqlite3")
    seen = []
    original = service.save_run_report
    original_transition = service.transition_run_report

    def recording(path, report):
        seen.append((report.status, report.stage_reached))
        original(path, report)

    def recording_transition(path, report, *args, **kwargs):
        seen.append((report.status, report.stage_reached))
        return original_transition(path, report, *args, **kwargs)

    monkeypatch.setattr(service, "save_run_report", recording)
    monkeypatch.setattr(service, "transition_run_report", recording_transition)
    agent = deps(FakeLLM(PLAN, SPEC))
    started, job = start_agent("Doanh thu theo ngày", "minh", agent, store)
    assert started.status == "GENERATING" and get_run_report(store, started.run_id).status == "GENERATING"
    planned = job()
    assert (planned.status, planned.stage_reached) == ("DRAFT", "confirm")
    assert planned.spec is None and planned.llm_calls == 1, "chưa xác nhận thì chưa viết Spec (FR-03)"
    done = confirm_plan(planned, "minh", agent, store)
    assert done.status == "READY_FOR_REVIEW" and get_run_report(store, started.run_id).status == "READY_FOR_REVIEW"
    assert ("GENERATING", "plan") in seen and ("PLAN_CONFIRMED", "confirm") in seen, seen
    assert ("VALIDATING", "static") in seen, seen
    assert [e.event for e in done.timeline] == ["submitted", "plan_ready", "plan_confirmed", "spec_ready", "finished"]


def test_background_crash_still_closes_the_run(tmp_path, monkeypatch):
    store = str(tmp_path / "runs.sqlite3")
    started, job = start_agent("x", "minh", deps(FakeLLM(PLAN, SPEC)), store)

    def crash(*_args, **_kwargs):
        raise RuntimeError("hỏng")

    monkeypatch.setattr(service, "_run", crash)
    job()
    report = get_run_report(store, started.run_id)
    assert report.status == "INTERNAL_ERROR" and "hỏng" in report.error_trace[-1]["error"]


def test_abandoned_runs_are_closed_on_startup(tmp_path):
    from src.run_report.store import abandon_unfinished

    store = str(tmp_path / "runs.sqlite3")
    started, _job = start_agent("x", "minh", deps(FakeLLM(PLAN, SPEC)), store)  # backend tắt trước khi job chạy
    assert abandon_unfinished(store) == [started.run_id]
    assert get_run_report(store, started.run_id).status == "INTERNAL_ERROR"
    assert abandon_unfinished(store) == []


def test_service_records_unexpected_crash(tmp_path):
    def crashing(spec, files, policy, settings):
        raise RuntimeError("boom")

    store = str(tmp_path / "runs.sqlite3")
    report = run_agent("x", "minh", deps(FakeLLM(PLAN, SPEC), sandbox=crashing), store)
    assert report.status == "INTERNAL_ERROR" and "boom" in report.error_trace[-1]["error"]
    assert get_run_report(store, report.run_id).status == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_agent_api_requires_engineer_and_saves_run(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        assert (await client.post("/api/v1/agent/runs", json={"request": "x"})).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        assert (await client.post("/api/v1/agent/runs", json={"request": "x"})).status_code == 403
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        assert (await client.post("/api/v1/agent/runs", json={"request": ""})).status_code == 422
        bad = await client.post("/api/v1/agent/runs", json={"request": "x", "sources": [{"source_ref": "nope"}]})
        assert bad.status_code == 422 and "không có trong domains.yml" in bad.json()["detail"]
        with monkeypatch.context() as patched:  # loại nguồn chưa chạy thử được thì chặn ngay
            patched.setattr(routes, "SUPPORTED_KINDS", {"postgres"})
            only_file = {"request": "x", "sources": [{"source_ref": "landing_sales"}]}
            bad = await client.post("/api/v1/agent/runs", json=only_file)
            assert bad.status_code == 422 and "chưa hỗ trợ" in bad.json()["detail"]
        scoped = await client.post(
            "/api/v1/agent/runs",
            json={"request": "Doanh thu", "sources": [{"source_ref": "sales_pg", "tables": ["orders"]}]},
        )
        assert scoped.status_code == 201
        scoped_run = (await client.get(f"/api/v1/runs/{scoped.json()['run_id']}")).json()
        assert scoped_run["plan"]["sources"][0]["source_ref"] == "sales_pg"
        child = await client.post(
            "/api/v1/agent/runs", json={"request": "Doanh thu", "parent_run_id": scoped.json()["run_id"]}
        )
        assert child.status_code == 201 and child.json()["parent_run_id"] == scoped.json()["run_id"]
        for started in (scoped, child):  # so sánh cần cả hai run đã sinh code
            await client.post(f"/api/v1/agent/runs/{started.json()['run_id']}/confirm-plan", json={})
        compared = (await client.get(f"/api/v1/runs/{child.json()['run_id']}/compare")).json()
        assert compared["available"] is True and compared["base"]["run_id"] == scoped.json()["run_id"]
        response = await client.post("/api/v1/agent/runs", json={"request": "Doanh thu theo ngày"})
        assert response.status_code == 201 and response.json()["status"] == "GENERATING"
        planned = (await client.get(f"/api/v1/runs/{response.json()['run_id']}")).json()
        assert (planned["status"], planned["stage_reached"], planned["spec"]) == ("DRAFT", "confirm", None)
        confirmed = await client.post(f"/api/v1/agent/runs/{planned['run_id']}/confirm-plan", json={})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "PLAN_CONFIRMED"
        report = (await client.get(f"/api/v1/runs/{response.json()['run_id']}")).json()
        assert report["status"] == "READY_FOR_REVIEW" and report["created_by"] == "minh"
        files = (await client.get(f"/api/v1/runs/{report['run_id']}/files")).json()
        assert files["version"] == 1 and set(files["files"]) == set(report["files"])
        assert (await client.get("/api/v1/runs/missing/files")).status_code == 404
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


def test_graph_with_checkpointer():
    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    llm = FakeLLM(PLAN, SPEC)
    g = build_graph(deps(llm), checkpointer=saver)
    config = {"configurable": {"thread_id": "test-thread-checkpointer"}}
    state = g.invoke(
        {
            "run_id": "test-thread-checkpointer",
            "version": 1,
            "user_request": "Doanh thu theo ngày",
            "llm_calls": 0,
            "error_trace": [],
        },
        config=config,
    )
    assert state["status"] == "READY_FOR_REVIEW"
    assert state["stage_reached"] == "t_validate"
    checkpoint = saver.get(config)
    assert checkpoint is not None
    assert checkpoint["channel_values"]["status"] == "READY_FOR_REVIEW"
