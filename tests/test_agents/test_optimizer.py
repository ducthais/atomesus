"""Optimizer P13 và luồng tối ưu thủ công UC-3: chỉ giữ bản vừa đúng vừa nhanh hơn; UC-3 không gọi LLM.

Sandbox giả trả thời gian chạy lại model gold và hồ sơ bảng gold theo `materialization` của Spec được chạy.
"""

import json

import pytest

from src.agents import manual_optimize, optimizer
from src.agents.graph import build_graph
from src.codegen.sandbox import SandboxResult
from src.policy.loader import load_policy
from src.run_report import Check, GateResult, RunReport, get_run_report, save_run_report
from src.run_report.store import FIXTURES_DIR, get_run_files
from src.spec.models import Spec
from tests.test_agents.test_graph import PLAN, SNAPSHOT, SPEC, FakeLLM, deps

PROFILE = [{"name": "order_date", "non_null": 30, "sum": None}, {"name": "revenue", "non_null": 30, "sum": "1000"}]


SAMPLES = load_policy().budgets.optimizer.rerun_samples


def fake_sandbox(
    rerun: dict[str, float],
    rows: int = 50_000,
    profiles: dict | None = None,
    status: dict | None = None,
    spread: dict | None = None,
):
    """Kết quả chạy thử theo materialization; ghi lại các lần gọi (materialization, rerun_model).

    `spread[kind]`: độ dao động ± quanh trung vị của các lần chạy lại (mặc định 0: mọi lần như nhau).
    """
    calls = []

    def run(spec, files, policy, settings, rerun_model=0):
        kind = spec.transform.materialization
        calls.append((kind, rerun_model))
        profile = (profiles or {}).get(kind, PROFILE)
        width = (spread or {}).get(kind, 0.0)
        samples = [rerun[kind] - width, rerun[kind], rerun[kind] + width] if rerun_model else []
        return SandboxResult(
            (status or {}).get(kind, "READY_FOR_REVIEW"),
            "t_validate",
            gates=[GateResult(name="post_run", result="pass", detail=f"bản {kind}")],
            checks=[
                Check(name="staging_reconcile", source_id="orders", result="pass", observed={"staging": rows}),
                Check(name="dbt_test", result="pass", observed={"passed": 5, "failed": 0}),
            ],
            duration_sec=10.0 if kind == "table" else 9.0,
            model_rerun_sec=rerun[kind] if rerun_model else None,
            rerun_samples=samples,
            gold_profile=PROFILE,
            rerun_profile=profile if rerun_model else None,
        )

    run.calls = calls
    return run


def _spec(**transform) -> Spec:
    raw = json.loads(json.dumps(SPEC))
    raw["transform"].update(transform)
    return Spec.model_validate(raw)


# ---------- luật ----------


def test_table_with_a_date_grain_and_enough_rows_becomes_incremental():
    first, second = optimizer.propose(_spec(), 50_000, load_policy())

    assert first.rule == "table_to_incremental" and first.candidate is not None
    transform = first.candidate["transform"]
    assert (transform["materialization"], transform["unique_key"], transform["partition_by"]) == (
        "incremental",
        ["order_date"],
        "order_date",
    )
    assert second.candidate is None and "chỉ áp cho model `incremental`" in second.reason


def test_rules_explain_why_they_do_not_apply():
    small = optimizer.propose(_spec(), 30, load_policy())[0]
    assert small.candidate is None and "dưới ngưỡng 10,000" in small.reason
    viewed = optimizer.propose(_spec(materialization="view"), 50_000, load_policy())[0]
    assert viewed.candidate is None and "`view`" in viewed.reason
    done = optimizer.propose(_spec(materialization="incremental"), 50_000, load_policy())[1]
    assert done.candidate is None and "đã có điều kiện trên `order_date`" in done.reason


def test_incremental_without_partition_gets_the_partition_filter():
    proposal = optimizer.propose(_spec(materialization="incremental", partition_by=None), 50_000, load_policy())[1]

    assert (
        proposal.rule == "missing_partition_filter" and proposal.candidate["transform"]["partition_by"] == "order_date"
    )


# ---------- chỉ giữ bản vừa đúng vừa nhanh hơn ----------


def _optimize(sandbox, **kwargs):
    d = deps(FakeLLM(), sandbox=sandbox, **kwargs)
    baseline = sandbox(_spec(), {}, d.policy, d.settings, rerun_model=3)
    return optimizer.optimize(SPEC, baseline, d, rows=50_000, run_id="r1", version=1)


def test_faster_and_identical_candidate_is_kept():
    outcome = _optimize(fake_sandbox({"table": 1.0, "incremental": 0.4}))

    step = outcome["steps"][0]
    assert step["applied"] is True and (step["before_sec"], step["after_sec"]) == (1.0, 0.4)
    assert "nhanh hơn 60.0%" in step["reason"]
    assert outcome["best"]["spec"]["transform"]["materialization"] == "incremental"


def test_candidate_that_is_not_fast_enough_is_dropped():
    outcome = _optimize(fake_sandbox({"table": 1.0, "incremental": 0.95}))

    assert outcome["best"] is None and "không nhanh hơn đủ" in outcome["steps"][0]["reason"]


def test_a_faster_median_inside_the_measurement_noise_is_dropped():
    """Trung vị nhanh hơn 30% nhưng các lần đo chồng lên nhau (eval 2026-10-13): chưa chắc nhanh hơn, không giữ."""
    noisy = fake_sandbox({"table": 0.28, "incremental": 0.2}, spread={"table": 0.06, "incremental": 0.1})

    outcome = _optimize(noisy)

    assert outcome["best"] is None and "nằm trong nhiễu đo" in outcome["steps"][0]["reason"]


def test_faster_candidate_with_a_different_gold_table_is_dropped():
    wrong = [{"name": "order_date", "non_null": 29, "sum": None}, {"name": "revenue", "non_null": 29, "sum": "990"}]
    outcome = _optimize(fake_sandbox({"table": 1.0, "incremental": 0.1}, profiles={"incremental": wrong}))

    assert outcome["best"] is None and "bảng gold khác bản gốc" in outcome["steps"][0]["reason"]


def test_candidate_that_fails_its_test_run_is_dropped():
    outcome = _optimize(fake_sandbox({"table": 1.0, "incremental": 0.1}, status={"incremental": "NEEDS_HUMAN"}))

    assert outcome["best"] is None and "chạy thử không đạt (NEEDS_HUMAN)" in outcome["steps"][0]["reason"]


def test_candidate_must_pass_the_budget_gate():
    def over_budget(spec, policy, settings):
        return {"sources": {"orders": 10**13}, "model_bytes": 10**13, "bytes": 2 * 10**13, "usd": 100.0}

    sandbox = fake_sandbox({"table": 1.0, "incremental": 0.1})
    outcome = _optimize(sandbox, estimate_cost=over_budget)

    assert outcome["best"] is None and outcome["steps"][0]["reason"].startswith("budget:")
    assert sandbox.calls == [("table", 3)], "bản ứng viên bị cổng chặn thì không chạy thử"


def test_no_extra_test_run_when_no_rule_applies():
    measured = []

    outcome = optimizer.optimize(SPEC, lambda: measured.append(1), deps(FakeLLM()), rows=30, run_id="r1", version=1)

    assert outcome["best"] is None and measured == [], "pipeline nhỏ không tốn thêm lần chạy thử nào"
    assert [s["rule"] for s in outcome["steps"]] == list(optimizer.RULES)


def test_same_output_compares_every_column():
    assert optimizer.same_output(PROFILE, list(reversed(PROFILE)))
    assert not optimizer.same_output(PROFILE, [*PROFILE[:1], {**PROFILE[1], "sum": "1001"}])
    assert not optimizer.same_output(PROFILE, None)


# ---------- P13 trong graph (UC-1) ----------


def _graph_run(sandbox):
    return build_graph(deps(FakeLLM(PLAN, SPEC), sandbox=sandbox)).invoke(
        {"run_id": "r1", "version": 1, "user_request": "Doanh thu theo ngày", "llm_calls": 0, "error_trace": []}
    )


def test_graph_keeps_the_optimized_version_for_review():
    state = _graph_run(fake_sandbox({"table": 1.0, "incremental": 0.3}))

    assert state["status"] == "READY_FOR_REVIEW" and state["stage_reached"] == "optimize"
    assert state["spec"]["transform"]["materialization"] == "incremental"
    gold = next(content for path, content in state["generated_files"].items() if "/gold/" in path)
    assert "materialized='incremental'" in gold
    assert state["optimizer"][0]["applied"] is True and state["optimized_sec"] == 9.0
    assert "P13 table_to_incremental" in state["spec_history"][-1]["reason"]
    assert state["llm_calls"] == 2, "optimizer không gọi LLM"


def test_graph_keeps_the_original_when_nothing_is_better():
    state = _graph_run(fake_sandbox({"table": 1.0, "incremental": 1.2}))

    assert state["status"] == "READY_FOR_REVIEW" and state["spec"]["transform"]["materialization"] == "table"
    assert state["optimizer"][0]["applied"] is False and "optimized_sec" not in state


def test_small_pipeline_skips_optimizer_without_extra_runs():
    sandbox = fake_sandbox({"table": 1.0, "incremental": 0.1}, rows=30)

    state = _graph_run(sandbox)

    assert state["status"] == "READY_FOR_REVIEW" and sandbox.calls == [("table", 0)]
    assert all(not s["applied"] for s in state["optimizer"])


# ---------- UC-3: tối ưu thủ công, không gọi LLM ----------


@pytest.fixture
def parent(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    report = RunReport.model_validate_json((FIXTURES_DIR / "deployed.json").read_text(encoding="utf-8"))
    report = report.model_copy(update={"spec": SPEC, "plan": PLAN, "user_request": "Doanh thu theo ngày"})
    save_run_report(store, report)
    return report, store


def test_manual_optimize_never_calls_the_llm_and_proposes_the_faster_version(parent):
    report, store = parent
    llm = FakeLLM()  # không có câu trả lời nào: gọi là hỏng ngay
    sandbox = fake_sandbox({"table": 1.0, "incremental": 0.3})
    started, job = manual_optimize.start_manual_optimize(report, "minh", deps(llm, sandbox=sandbox), store)

    assert started.status == "VALIDATING" and started.trigger == "manual_optimize" and started.created_by == "minh"
    done = job()

    assert done.status == "READY_FOR_REVIEW" and done.llm_calls == 0 and llm.calls == []
    assert done.parent_run_id == report.run_id and done.spec["transform"]["materialization"] == "incremental"
    assert done.optimizer[0].applied and (done.timing.baseline_sec, done.timing.optimized_sec) == (10.0, 9.0)
    files = get_run_files(store, done.run_id, done.version)
    assert any("materialized='incremental'" in content for content in files.values()), "bộ file đã lưu là bản tối ưu"
    assert get_run_report(store, done.run_id).status == "READY_FOR_REVIEW"


def test_manual_optimize_without_a_better_version_ends_with_no_improvement(parent):
    report, store = parent
    sandbox = fake_sandbox({"table": 1.0, "incremental": 0.1}, rows=30)

    done = manual_optimize.start_manual_optimize(report, "minh", deps(FakeLLM(), sandbox=sandbox), store)[1]()

    assert done.status == "NO_IMPROVEMENT" and done.stage_reached == "report" and done.llm_calls == 0
    assert done.spec == report.spec
    assert done.optimizer[0].rule == "table_to_incremental" and "dưới ngưỡng" in done.optimizer[0].reason


def test_manual_optimize_stops_when_the_source_schema_changed(parent):
    report, store = parent
    changed = {"orders": {**SNAPSHOT["orders"], "columns": {"order_id": "integer"}}}
    d = deps(FakeLLM(), sandbox=fake_sandbox({"table": 1.0, "incremental": 0.1}))
    d.snapshot = lambda plan, policy, settings: changed

    done = manual_optimize.start_manual_optimize(report, "minh", d, store)[1]()

    assert done.status == "NEEDS_HUMAN" and done.stage_reached == "static" and done.llm_calls == 0
    assert "Schema nguồn đã đổi" in done.error_trace[-1]["error"] and "UC-1" in done.error_trace[-1]["error"]


def test_manual_optimize_needs_the_running_version_to_pass_first(parent):
    report, store = parent
    sandbox = fake_sandbox({"table": 1.0, "incremental": 0.1}, status={"table": "NEEDS_HUMAN"})

    done = manual_optimize.start_manual_optimize(report, "minh", deps(FakeLLM(), sandbox=sandbox), store)[1]()

    assert done.status == "NEEDS_HUMAN" and sandbox.calls == [("table", SAMPLES)], "bản gốc hỏng thì không thử ứng viên"
    assert "không chạy thử đạt" in done.error_trace[-1]["error"]
