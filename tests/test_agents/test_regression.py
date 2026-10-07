"""Run sửa của Day 2: vào thẳng codegen với Spec đang chạy, so với bản đang chạy, chỉ gửi duyệt khi tốt hơn."""

import json

import pytest

from src.agents import regression
from src.codegen.sandbox import SandboxResult
from src.run_report import Check, GateResult, RunReport, get_run_report, save_run_report
from src.run_report.store import FIXTURES_DIR, save_run_files
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, with_column

EVIDENCE = {
    "model": "fct_daily_orders",
    "detected_at": "2026-10-06T10:00:00+00:00",
    "rules": [
        {
            "rule": "runtime_regression",
            "observed": 9.0,
            "threshold": 7.5,
            "baseline": 5.0,
            "detail": "đang chậm hơn 80.0% so với lúc deploy",
            "stalled_sources": [],
        }
    ],
}


def sandbox_taking(*seconds):
    """Lần gọi thứ nhất là bản đề xuất (trong graph), lần thứ hai là bản đang chạy chạy lại để so."""
    durations = list(seconds)

    def run(spec, files, policy, settings):
        return SandboxResult(
            "READY_FOR_REVIEW",
            "t_validate",
            gates=[GateResult(name="post_run", result="pass")],
            checks=[Check(name="dbt_test", result="pass", observed={"passed": 5, "failed": 0})],
            duration_sec=durations.pop(0),
        )

    return run


@pytest.fixture
def parent(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    report = RunReport.model_validate_json((FIXTURES_DIR / "deployed.json").read_text(encoding="utf-8"))
    report = report.model_copy(update={"spec": SPEC, "plan": PLAN})
    save_run_report(store, report)
    save_run_files(store, report.run_id, report.version, {"ingestion/x.py": "print('đang chạy')"})
    return report, store


def test_better_fix_waits_for_review_with_a_three_number_label(parent):
    report, store = parent
    llm = FakeLLM(SPEC)  # chỉ codegen được gọi: run sửa bỏ qua planner
    started, job = regression.start_regression(report, EVIDENCE, deps(llm, sandbox=sandbox_taking(1.5, 4.0)), store)

    assert started.status == "GENERATING" and started.trigger == "regression" and started.created_by == "system"
    done = job()

    assert done.status == "READY_FOR_REVIEW" and done.parent_run_id == report.run_id
    assert [call["tier"] for call in llm.calls] == ["strong"]
    asked = llm.calls[0]["user"]["sửa_suy_giảm"]
    assert asked["spec_đang_chạy"] == SPEC and asked["bằng_chứng"][0]["rule"] == "runtime_regression"
    verdict = done.regression["verdict"]
    assert verdict["improved"] is True and verdict["comparison"][0]["gain_pct"] == 62.5
    assert "Bản sửa cho `fct_daily_orders`" in verdict["label"] and "đang chậm hơn 80.0%" in verdict["label"]
    assert get_run_report(store, done.run_id).status == "READY_FOR_REVIEW"


def test_fix_that_is_not_faster_is_only_flagged(parent):
    report, store = parent
    _started, job = regression.start_regression(
        report, EVIDENCE, deps(FakeLLM(SPEC), sandbox=sandbox_taking(3.0, 2.0)), store
    )

    done = job()

    assert done.status == "NO_IMPROVEMENT" and done.regression["verdict"]["improved"] is False
    assert get_run_report(store, done.run_id).status == "NO_IMPROVEMENT"


def test_unchanged_spec_is_no_improvement(parent):
    report, store = parent
    candidate = report.model_copy(
        update={
            "run_id": "fix",
            "trigger": "regression",
            "status": "READY_FOR_REVIEW",
            "approval": None,
            "regression": EVIDENCE,
            "created_by": "system",
        }
    )

    judged = regression.judge(candidate, report, deps(FakeLLM()), store)

    assert judged.status == "NO_IMPROVEMENT" and "giữ nguyên Spec" in judged.regression["verdict"]["reason"]


def test_fix_that_hits_the_spec_ceiling_stays_needs_human(parent):
    report, store = parent
    wrong = with_column(SPEC, 2, **{"from": "amount"})  # cột không có ở nguồn, lần nào cũng sai
    _started, job = regression.start_regression(report, EVIDENCE, deps(FakeLLM(wrong, wrong, wrong)), store)

    done = job()

    assert done.status == "NEEDS_HUMAN" and "max_spec_attempts" in json.dumps(done.error_trace, ensure_ascii=False)
    assert "verdict" not in done.regression
