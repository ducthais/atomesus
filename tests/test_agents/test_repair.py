"""Tự sửa sau khi chạy thử (SPEC §11.2, §11.3): bảng định tuyến và hai vòng spec / SQL trong graph."""

import json

from src.agents import router
from src.agents.graph import build_graph
from src.codegen.sandbox import SandboxResult
from src.policy.loader import load_policy
from src.run_report import Check, GateResult
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, passing_sandbox, small_cost


def _failed(*checks: Check, stage: str = "t_validate", error: str = "Kiểm tra không đạt") -> SandboxResult:
    return SandboxResult(
        "NEEDS_HUMAN",
        stage,
        gates=[GateResult(name="post_run", result="fail", detail="lỗi")],
        checks=list(checks),
        error_trace=[{"stage": stage, "error": error}],
    )


def sandbox_sequence(*results: SandboxResult):
    """Lần lượt trả các kết quả dựng sẵn, ghi lại Spec được chạy thử mỗi lần."""
    queue, seen = list(results), []

    def run(spec, files, policy, settings):
        seen.append(spec.model_dump(by_alias=True, mode="json"))
        return queue.pop(0) if queue else passing_sandbox(spec, files, policy, settings)

    run.seen = seen
    return run


def _invoke(llm, sandbox, policy=None, estimate_cost=small_cost) -> dict:
    graph = build_graph(deps(llm, policy=policy, sandbox=sandbox, estimate_cost=estimate_cost))
    return graph.invoke(
        {"run_id": "r1", "version": 1, "user_request": "Doanh thu theo ngày", "llm_calls": 0, "error_trace": []}
    )


METRIC_MISMATCH = Check(
    name="metric_reconcile",
    result="fail",
    observed={
        "mismatch_count": 3,
        "missing_count": 0,
        "extra_count": 0,
        "mismatched": [{"key": {"order_date": "2026-01-01"}, "metric": "revenue", "gold": "2000", "source": "1000"}],
        "totals": {"revenue": {"gold": "6000", "source": "3000"}},
        "shift_hours": None,
    },
)


def test_metric_mismatch_goes_round_the_sql_loop_and_only_sql_changes():
    fixed = json.loads(json.dumps(SPEC))
    fixed["transform"]["metrics"][0]["expr"] = "SUM(DISTINCT amount_vnd)"
    fixed["transform"]["model_name"] = "doi_ten_khong_duoc_nhan"  # ngoài phần SQL: phải bị bỏ qua
    fixed["schedule"]["cron"] = "0 5 * * *"
    costs = []
    sandbox = sandbox_sequence(_failed(METRIC_MISMATCH))

    def counting_cost(spec, policy, settings):
        costs.append(1)
        return small_cost(spec, policy, settings)

    llm = FakeLLM(PLAN, SPEC, fixed)
    state = _invoke(llm, sandbox, estimate_cost=counting_cost)

    assert state["status"] == "READY_FOR_REVIEW" and state["attempts"] == {"spec": 0, "sql": 1}
    prompt = llm.calls[-1]["user"]
    assert "sql_fix" in prompt["yêu_cầu_sửa"] and "metric_reconcile" in prompt["lỗi_chạy_thử"][0]
    assert "2026-01-01" in prompt["lỗi_chạy_thử"][0], "kèm ví dụ dòng lệch để LLM biết lệch ở đâu"
    second = sandbox.seen[1]
    assert second["transform"]["metrics"][0]["expr"] == "SUM(DISTINCT amount_vnd)"
    assert second["transform"]["model_name"] == "fct_daily_orders" and second["schedule"] == sandbox.seen[0]["schedule"]
    assert len(costs) == 1, "vòng SQL render lại ngay (P6), không đi lại cổng budget / arbitrate"
    assert any("tự sửa biểu thức SQL" in (h.get("reason") or "") for h in state["spec_history"])
    assert not any(g["name"] == "post_run" and g["result"] == "fail" for g in state["gates"])


def test_staging_rejects_go_back_to_propose_with_reasons_and_samples():
    rejects = Check(
        name="staging_reconcile",
        source_id="orders",
        result="fail",
        observed={
            "bronze": 100,
            "staging": 70,
            "duplicates": 0,
            "rejected": 30,
            "reject_pct": 30.0,
            "max_reject_pct": 1.0,
            "reject_reasons": {"date_format": 30},
            "reject_samples": [{"_reject_reason": "date_format", "ordered_at_text": "01.02.2026"}],
        },
    )
    sandbox = sandbox_sequence(_failed(rejects))
    llm = FakeLLM(PLAN, SPEC, SPEC)
    state = _invoke(llm, sandbox)

    assert state["status"] == "READY_FOR_REVIEW" and state["attempts"]["spec"] == 1
    error = llm.calls[-1]["user"]["lỗi_chạy_thử"][0]
    assert "nguồn orders" in error and "date_format: 30 dòng" in error and "01.02.2026" in error
    assert "date_formats" in error, "kèm hướng sửa của bảng định tuyến"


def test_timezone_shift_stops_without_calling_the_llm_again():
    shifted = Check(name="metric_reconcile", result="fail", observed={**METRIC_MISMATCH.observed, "shift_hours": 7})
    llm = FakeLLM(PLAN, SPEC)
    state = _invoke(llm, sandbox_sequence(_failed(shifted)))

    assert state["status"] == "NEEDS_HUMAN" and len(llm.calls) == 2 and state["attempts"]["sql"] == 0


def test_infra_error_is_not_retried():
    down = SandboxResult("INFRA_ERROR", "el_run", error_trace=[{"stage": "el_run", "error": "Không kết nối được"}])
    llm = FakeLLM(PLAN, SPEC)
    state = _invoke(llm, sandbox_sequence(down))

    assert state["status"] == "INFRA_ERROR" and len(llm.calls) == 2


def test_loop_limit_is_read_from_budgets():
    policy = load_policy()
    per_run = policy.budgets.per_run.model_copy(update={"max_sql_attempts": 0})
    policy = policy.model_copy(update={"budgets": policy.budgets.model_copy(update={"per_run": per_run})})
    llm = FakeLLM(PLAN, SPEC)
    state = _invoke(llm, sandbox_sequence(_failed(METRIC_MISMATCH)), policy=policy)

    assert state["status"] == "NEEDS_HUMAN" and len(llm.calls) == 2
    assert "max_sql_attempts" in state["error_trace"][-1]["error"]


def test_spec_fix_wins_when_both_loops_are_needed():
    rejects = Check(name="staging_reconcile", source_id="orders", result="fail", observed={"bronze": 0})
    decision = router.decide(router.classify({"stage_reached": "t_validate", "checks": [
        rejects.model_dump(), METRIC_MISMATCH.model_dump()]}))  # fmt: skip
    assert decision.goto == "propose" and [f.kind for f in decision.failures] == ["staging_rejects", "metric_reconcile"]


def test_dbt_model_error_and_unknown_failures():
    assert router.decide(router.classify({"stage_reached": "t_run", "error_trace": [{"error": "x"}]})).goto == "sql_fix"
    assert (
        router.decide(router.classify({"stage_reached": "el_run", "error_trace": [{"error": "x"}]})).goto == "propose"
    )
    assert router.decide([]).goto == "stop", "lỗi không phân loại được thì dừng, không đoán"


def test_routing_table_is_complete_data():
    policy = load_policy()
    assert len({(r.phase, r.kind) for r in router.ROUTES}) == len(router.ROUTES)
    for route in router.ROUTES:
        assert route.goto in ("propose", "sql_fix", "stop")
        if route.goto == "stop":
            assert route.status or route.phase == "P13", route
        else:
            assert router.limit(route.goto, policy) >= 0
    assert router.limit("sql_fix", policy) == policy.budgets.per_run.max_sql_attempts
