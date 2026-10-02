"""Graph planner → codegen → render → test_run với LLM, nguồn và sandbox giả (không gọi mạng, không cần Postgres)."""

import json
from types import SimpleNamespace

import pytest

from src.agents import service
from src.agents.graph import build_graph
from src.agents.nodes.common import AgentDeps
from src.agents.service import run_agent, start_agent
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


def passing_sandbox(spec, files, policy, settings):
    return SandboxResult(
        "READY_FOR_REVIEW",
        "t_validate",
        gates=[GateResult(name="post_run", result="pass", detail="3/3 phép kiểm tra đạt")],
        checks=[Check(name="dbt_test", result="pass", observed={"passed": 5, "failed": 0})],
        duration_sec=1.5,
    )


def deps(llm, policy=None, sandbox=passing_sandbox):
    return AgentDeps(
        llm=llm,
        policy=policy or load_policy(),
        settings=SimpleNamespace(),
        catalog=lambda policy, settings: CATALOG,
        snapshot=lambda plan, policy, settings: SNAPSHOT,
        sandbox=sandbox,
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
    assert llm.calls[1]["user"]["schema_snapshot"] == SNAPSHOT  # LLM luôn nhận schema thật
    assert state["llm_calls"] == 2 and state["domain"] == "sales"
    assert len(state["generated_files"]) == 5 and set(state["files"]) == set(state["generated_files"])
    assert state["files_hash"].startswith("sha256:")
    assert [g["name"] for g in state["gates"]] == ["spec_schema", "schema_real", "time_grain", "ast_guard", "post_run"]


def test_invalid_spec_goes_back_to_codegen_with_specific_error():
    wrong = with_column(SPEC, 2, **{"from": "amount"})  # cột không có ở nguồn
    llm = FakeLLM(PLAN, wrong, SPEC)
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW"
    assert state["attempts"]["spec"] == 1 and state["llm_calls"] == 3
    feedback = llm.calls[2]["user"]["lỗi_lần_trước"]
    assert any("cột 'amount' không có ở nguồn" in error for error in feedback)
    assert [h["errors"] == [] for h in state["spec_history"]] == [False, True]


def test_spec_loop_stops_after_policy_limit():
    no_formats = with_column(SPEC, 3, date_formats=None)  # chuỗi → date mà thiếu date_formats
    llm = FakeLLM(PLAN, no_formats, no_formats, no_formats)
    state = run(llm)
    assert state["status"] == "NEEDS_HUMAN" and state["stage_reached"] == "static"
    assert state["attempts"]["spec"] == 3 and state["llm_calls"] == 4
    assert "max_spec_attempts" in state["error_trace"][-1]["error"]
    assert "generated_files" not in state


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
    state = run(FakeLLM(plan))
    assert state["status"] == "NEEDS_HUMAN" and message in state["error_trace"][-1]["error"]


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

    state = run(FakeLLM(PLAN, SPEC), sandbox=failing)
    assert state["status"] == "NEEDS_HUMAN" and state["error_trace"][-1]["error"] == "dbt lỗi"


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


def test_progress_is_saved_after_each_step_and_background_job_finishes(tmp_path, monkeypatch):
    store = str(tmp_path / "runs.sqlite3")
    seen = []
    original = service.save_run_report

    def recording(path, report):
        seen.append((report.status, report.stage_reached))
        original(path, report)

    monkeypatch.setattr(service, "save_run_report", recording)
    started, job = start_agent("Doanh thu theo ngày", "minh", deps(FakeLLM(PLAN, SPEC)), store)
    assert started.status == "GENERATING" and get_run_report(store, started.run_id).status == "GENERATING"
    done = job()
    assert done.status == "READY_FOR_REVIEW" and get_run_report(store, started.run_id).status == "READY_FOR_REVIEW"
    assert ("GENERATING", "plan") in seen and ("VALIDATING", "static") in seen, seen


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
    app.dependency_overrides[routes.agent_deps] = lambda: deps(FakeLLM(PLAN, SPEC))
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        assert (await client.post("/api/v1/agent/runs", json={"request": "x"})).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"user_id": "an"})
        assert (await client.post("/api/v1/agent/runs", json={"request": "x"})).status_code == 403
        await client.post("/api/v1/pipeline/session", json={"user_id": "minh"})
        assert (await client.post("/api/v1/agent/runs", json={"request": ""})).status_code == 422
        for sources, message in [
            ([{"source_ref": "nope"}], "không có trong domains.yml"),
            ([{"source_ref": "sales_api"}], "chưa hỗ trợ"),
        ]:
            bad = await client.post("/api/v1/agent/runs", json={"request": "x", "sources": sources})
            assert bad.status_code == 422 and message in bad.json()["detail"]
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
        compared = (await client.get(f"/api/v1/runs/{child.json()['run_id']}/compare")).json()
        assert compared["available"] is True and compared["base"]["run_id"] == scoped.json()["run_id"]
        response = await client.post("/api/v1/agent/runs", json={"request": "Doanh thu theo ngày"})
        assert response.status_code == 201 and response.json()["status"] == "GENERATING"
        report = (await client.get(f"/api/v1/runs/{response.json()['run_id']}")).json()
        assert report["status"] == "READY_FOR_REVIEW" and report["created_by"] == "minh"
        files = (await client.get(f"/api/v1/runs/{report['run_id']}/files")).json()
        assert files["version"] == 1 and set(files["files"]) == set(report["files"])
        assert (await client.get("/api/v1/runs/missing/files")).status_code == 404
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)
