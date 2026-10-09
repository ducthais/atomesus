import asyncio
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.agents import budget, regression
from src.agents.nodes.codegen import _user_message
from src.agents.service import PlanNotAwaitingError, plan_agent, start_confirm
from src.agents.timeout import raise_if_phase_cancelled, with_timeout
from src.codegen import deploy, render, sandbox
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.run_report import RunReport
from src.run_report.store import abandon_unfinished, get_run_report, save_run_report
from src.spec.models import Spec
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps


def test_stale_copy_cannot_confirm_the_same_plan_twice(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(FakeLLM(PLAN, SPEC))
    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)

    start_confirm(planned, "minh", agent, store)

    with pytest.raises(PlanNotAwaitingError):
        start_confirm(planned, "minh", agent, store)


def test_daily_reservation_is_atomic(tmp_path):
    settings = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    barrier = threading.Barrier(3)
    outcomes = []

    def reserve(run_id):
        barrier.wait()
        outcomes.append(budget.reserve_daily(settings, run_id, 1, "scan", 0.75, 1.0)[0])

    threads = [threading.Thread(target=reserve, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == [False, True]


def test_concurrent_report_saves_record_cost_once(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    base = base.model_copy(update={"run_id": "same", "cost": base.cost.model_copy(update={"usd": 0})})
    save_run_report(store, base)
    charged = base.model_copy(update={"cost": base.cost.model_copy(update={"usd": 0.4})})
    barrier = threading.Barrier(3)

    def save():
        barrier.wait()
        save_run_report(store, charged)

    threads = [threading.Thread(target=save) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert budget.spent_today(PipelineSettings(_env_file=None, store_path=store)) == pytest.approx(0.4)


def test_timeout_report_keeps_unsettled_llm_reservation(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    settings = PipelineSettings(_env_file=None, store_path=store)
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    report = base.model_copy(update={"run_id": "timeout", "cost": base.cost.model_copy(update={"usd": 0})})
    save_run_report(store, report)
    assert budget.reserve_daily(settings, report.run_id, report.version, "llm:propose:1", 0.05, 1.0)[0]

    save_run_report(store, report.model_copy(update={"status": "INFRA_ERROR"}))

    assert budget.spent_today(settings) == pytest.approx(0.05)


def test_repeated_stale_save_after_cancel_records_actual_cost_once(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    settings = PipelineSettings(_env_file=None, store_path=store)
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    base = base.model_copy(update={"run_id": "cancelled", "cost": base.cost.model_copy(update={"usd": 0})})
    save_run_report(store, base.model_copy(update={"status": "CANCELLED"}))
    stale = base.model_copy(update={"cost": base.cost.model_copy(update={"usd": 0.4})})

    save_run_report(store, stale)
    save_run_report(store, stale)

    assert get_run_report(store, base.run_id).status == "CANCELLED"
    assert budget.spent_today(settings) == pytest.approx(0.4)


def test_blocked_scan_estimate_is_not_recorded_as_spend(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    settings = PipelineSettings(_env_file=None, store_path=store)
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    blocked = base.model_copy(update={"run_id": "blocked", "status": "BLOCKED_BUDGET", "approval": None})

    save_run_report(store, blocked)

    assert budget.spent_today(settings) == 0


def test_spend_is_recorded_when_it_happens_not_when_run_was_created(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    old = base.model_copy(update={"run_id": "old", "created_at": datetime.now(UTC) - timedelta(days=1)})
    save_run_report(store, old.model_copy(update={"cost": old.cost.model_copy(update={"usd": 0})}))
    save_run_report(store, old.model_copy(update={"cost": old.cost.model_copy(update={"usd": 0.4})}))

    assert budget.spent_today(PipelineSettings(_env_file=None, store_path=store)) == pytest.approx(0.4)


def test_failed_dbt_parse_keeps_previous_deployment(tmp_path, monkeypatch):
    spec = Spec.model_validate(SPEC)
    files = render.render(spec, load_policy())
    target = tmp_path / spec.transform.model_name
    target.mkdir()
    (target / "marker.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(deploy, "prepare_project", lambda root, _files: root.mkdir(parents=True))
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "parse failed"),
    )

    with pytest.raises(OSError, match="dbt parse"):
        deploy.deploy_local(spec, files, str(tmp_path))

    assert (target / "marker.txt").read_text(encoding="utf-8") == "old"


def test_llm_prompt_does_not_contain_raw_sample_values():
    snapshot = {
        "orders": {
            "columns": {"customer_email": "text", "amount": "numeric"},
            "sample_rows": [{"customer_email": "private@example.com", "amount": "123"}],
        }
    }
    state = {"user_request": "Doanh thu", "plan": {**PLAN, "domain": "sales"}}

    prompt = _user_message(state, deps(FakeLLM()), snapshot)

    assert "private@example.com" not in prompt and "sample_rows" not in prompt
    assert '"distinct_samples": 1' in prompt and '"max_length": 19' in prompt


def test_unmeasurable_regression_is_not_marked_better():
    parent = RunReport.model_validate_json(Path("fixtures/run_reports/deployed.json").read_text(encoding="utf-8"))
    report = parent.model_copy(
        update={
            "trigger": "regression",
            "regression": {"rules": [{"rule": "runtime_regression"}]},
        }
    )

    assert regression.compare(report, parent, None, None) == [
        {"rule": "runtime_regression", "better": False, "detail": "không đo được bản đang chạy để so sánh"}
    ]


def test_startup_only_abandons_stale_runs(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    running = base.model_copy(update={"run_id": "running", "status": "GENERATING", "approval": None})
    save_run_report(store, running)
    assert abandon_unfinished(store, stale_before=datetime.now(UTC) - timedelta(minutes=30)) == []
    assert get_run_report(store, "running").status == "GENERATING"


@pytest.mark.asyncio
async def test_startup_sweeper_eventually_abandons_fresh_orphan(tmp_path, monkeypatch):
    from src import main

    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    running = base.model_copy(update={"run_id": "orphan", "status": "GENERATING", "approval": None})
    save_run_report(store, running)
    sleeps = 0

    async def one_iteration(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError
        time.sleep(0.01)  # cutoff phải muộn hơn updated_at ngay cả khi clock có độ phân giải thấp

    monkeypatch.setattr(main.asyncio, "sleep", one_iteration)
    with pytest.raises(asyncio.CancelledError):
        await main.sweep_unfinished(store, 0)

    assert get_run_report(store, "orphan").status == "INTERNAL_ERROR"


def test_cooperative_phase_work_stops_after_timeout():
    stopped = threading.Event()

    def work(_state):
        while True:
            try:
                raise_if_phase_cancelled()
            except TimeoutError:
                stopped.set()
                raise
            time.sleep(0.005)

    update = with_timeout(work, "budget", 0.02)({"error_trace": []})
    assert update["status"] == "INFRA_ERROR"
    assert stopped.wait(0.2)


def test_phase_timeout_kills_active_subprocess(tmp_path):
    marker = tmp_path / "orphan.txt"

    def work(_state):
        command = [
            sys.executable,
            "-c",
            f"import pathlib,time;time.sleep(.3);pathlib.Path({str(marker)!r}).write_text('orphan')",
        ]
        sandbox._run(command, tmp_path, dict(os.environ), 5, "test")
        return {}

    update = with_timeout(work, "test", 0.02)({"error_trace": []})
    time.sleep(0.4)

    assert update["status"] == "INFRA_ERROR"
    assert not marker.exists()
