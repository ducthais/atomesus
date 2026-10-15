import asyncio
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents import budget, regression, timeout
from src.agents.nodes.codegen import _user_message
from src.agents.nodes.common import CancellationRequestedError
from src.agents.service import PlanNotAwaitingError, plan_agent, start_confirm
from src.agents.timeout import raise_if_phase_cancelled, with_timeout
from src.codegen import render, sandbox
from src.deployer import install
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.run_report import RunReport
from src.run_report.router import _decide
from src.run_report.store import (
    abandon_unfinished,
    get_run_report,
    is_run_cancelled,
    save_run_report,
    transition_run_report,
)
from src.spec.models import Spec
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps


def test_stale_copy_cannot_confirm_the_same_plan_twice(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(FakeLLM(PLAN, SPEC))
    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)

    start_confirm(planned, "minh", agent, store)

    with pytest.raises(PlanNotAwaitingError):
        start_confirm(planned, "minh", agent, store)


@pytest.mark.parametrize("raises", [False, True])
def test_invoke_cleans_only_its_checkpoint_thread(monkeypatch, raises):
    from src.agents import service

    class Checkpointer:
        def __init__(self):
            self.checkpoints = {"other:planner": "keep"}

        def delete_thread(self, thread_id):
            self.checkpoints.pop(thread_id, None)

    checkpointer = Checkpointer()

    class Graph:
        def stream(self, initial, config, stream_mode):
            thread_id = config["configurable"]["thread_id"]
            checkpointer.checkpoints[thread_id] = "run data"
            yield initial
            if raises:
                raise RuntimeError("graph failed")

    monkeypatch.setattr(service, "build_graph", lambda *args, **kwargs: Graph())
    state = service._invoke(
        SimpleNamespace(checkpointer=checkpointer),
        {"run_id": "finished", "status": "GENERATING"},
        stop_after_spec=False,
    )

    assert checkpointer.checkpoints == {"other:planner": "keep"}
    assert state["status"] == ("INTERNAL_ERROR" if raises else "GENERATING")


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
    monkeypatch.setattr(install, "prepare_project", lambda root, _files: root.mkdir(parents=True))
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "parse failed"),
    )

    with pytest.raises(OSError, match="dbt parse"):
        install.install_project(spec.transform.model_name, files, str(tmp_path))

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


@pytest.mark.asyncio
async def test_sweeper_survives_a_failed_pass(monkeypatch):
    import sqlite3

    from src import main

    calls = []

    def flaky(_store, stale_before):
        calls.append(stale_before)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return []

    async def two_iterations(_seconds):
        if len(calls) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "abandon_unfinished", flaky)
    monkeypatch.setattr(main.asyncio, "sleep", two_iterations)
    with pytest.raises(asyncio.CancelledError):
        await main.sweep_unfinished("unused", 0)

    assert len(calls) == 2


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


def test_user_cancel_stops_running_phase_and_kills_subprocess(tmp_path, monkeypatch):
    monkeypatch.setattr(timeout, "CANCEL_POLL_SEC", 0.02)
    marker = tmp_path / "orphan.txt"
    cancel = threading.Event()

    def work(_state):
        command = [
            sys.executable,
            "-c",
            f"import pathlib,time;time.sleep(2);pathlib.Path({str(marker)!r}).write_text('orphan')",
        ]
        threading.Timer(0.3, cancel.set).start()  # hủy khi tiến trình con đã chạy
        sandbox._run(command, tmp_path, dict(os.environ), 30, "test")
        return {}

    with pytest.raises(CancellationRequestedError):
        with_timeout(work, "test", 30, cancelled=lambda _state: cancel.is_set())({"run_id": "r"})
    time.sleep(2.5)  # biên rộng: taskkill chậm khi máy tải nặng vẫn phải giết kịp trước lúc con ghi file

    assert not marker.exists()


def test_cancelled_run_stops_graph_phase(tmp_path, monkeypatch):
    monkeypatch.setattr(timeout, "CANCEL_POLL_SEC", 0.02)
    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    save_run_report(store, base.model_copy(update={"run_id": "c", "status": "CANCELLED", "approval": None}))
    agent = deps(FakeLLM())
    agent.cancelled = lambda run_id: is_run_cancelled(store, run_id)
    started = time.monotonic()

    with pytest.raises(CancellationRequestedError):
        timeout.timed(agent, "test_run", lambda _state: time.sleep(5) or {})({"run_id": "c"})

    assert time.monotonic() - started < 2


def test_concurrent_review_decisions_only_one_wins(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    save_run_report(store, base)
    # Hai quyết định hợp lệ như router ghi (APPROVED bắt buộc có chữ ký approval).
    reviewer = {"id": "an"}
    decisions = [
        _decide(base, "approved", reviewer, "", status="APPROVED", stage_reached="review"),
        _decide(base, "rejected", reviewer, "", status="REJECTED", stage_reached="review"),
    ]
    barrier = threading.Barrier(3)
    outcomes = []

    def decide(report):
        barrier.wait()
        outcomes.append(transition_run_report(store, report, "READY_FOR_REVIEW", base.version, base.files_hash))

    threads = [threading.Thread(target=decide, args=(report,)) for report in decisions]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == [False, True]
    assert get_run_report(store, base.run_id).status in {"APPROVED", "REJECTED"}


def test_transition_rejects_changed_files_hash(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    save_run_report(store, base)

    approved = base.model_copy(update={"status": "APPROVED"})
    assert not transition_run_report(store, approved, "READY_FOR_REVIEW", base.version, "sha256:khac")
    assert not transition_run_report(store, approved, "READY_FOR_REVIEW", base.version + 1, base.files_hash)
    assert get_run_report(store, base.run_id).status == "READY_FOR_REVIEW"


def test_dbt_parse_timeout_keeps_previous_deployment_and_no_leftovers(tmp_path, monkeypatch):
    spec = Spec.model_validate(SPEC)
    files = render.render(spec, load_policy())
    target = tmp_path / spec.transform.model_name
    target.mkdir()
    (target / "marker.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(install, "prepare_project", lambda root, _files: root.mkdir(parents=True))

    def hang(*_args, **kwargs):
        assert kwargs.get("timeout") == install.DBT_PARSE_TIMEOUT_SEC
        raise subprocess.TimeoutExpired("dbt parse", kwargs.get("timeout"))

    monkeypatch.setattr(install.subprocess, "run", hang)

    with pytest.raises(subprocess.TimeoutExpired):
        install.install_project(spec.transform.model_name, files, str(tmp_path))

    assert (target / "marker.txt").read_text(encoding="utf-8") == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == [spec.transform.model_name]


def test_abandon_skips_run_that_progressed_since_listing(tmp_path, monkeypatch):
    from src.run_report import store as run_store

    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(Path("fixtures/run_reports/ready_for_review.json").read_text(encoding="utf-8"))
    running = base.model_copy(update={"run_id": "live", "status": "GENERATING", "approval": None})
    save_run_report(store, running)
    listed = run_store.list_run_reports

    def list_then_progress(path, status=None, limit=50):
        found = listed(path, status, limit)
        if status == "GENERATING" and found:  # worker khác chạy xong run ngay sau lúc liệt kê
            save_run_report(store, running.model_copy(update={"status": "READY_FOR_REVIEW"}))
        return found

    monkeypatch.setattr(run_store, "list_run_reports", list_then_progress)

    assert abandon_unfinished(store, stale_before=datetime.now(UTC) + timedelta(minutes=1)) == []
    assert get_run_report(store, "live").status == "READY_FOR_REVIEW"


@pytest.mark.asyncio
async def test_reviewer_cannot_call_monitor_or_catalog_mutations(client, tmp_path, monkeypatch):
    from src.main import app
    from src.monitor import alerts
    from src.pipeline import auth

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    # Cảnh báo của minh: an (Reviewer demo) thấy được nhưng không được xử lý. Cảnh báo không thấy được là 404 trước khi
    # xét vai, không lộ cảnh báo đó có tồn tại.
    alert = alerts.raise_alert(config.store_path, "daily_sales", "source_stalled", "Nguồn ngừng", owner="minh")
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        for url in (
            "/api/v1/monitor/collect",
            "/api/v1/monitor/check",
            f"/api/v1/monitor/alerts/{alert['id']}/resolve",
            "/api/v1/catalog/datasets/sales/fct_daily_sales/reconcile",
        ):
            assert (await client.post(url)).status_code == 403, url
        assert alerts.get_alert(config.store_path, alert["id"])["resolved_at"] is None
        assert (await client.post(f"/api/v1/monitor/alerts/{alert['id'] + 1}/resolve")).status_code == 404
    finally:
        app.dependency_overrides.pop(auth.settings, None)


def test_phase_timeout_kills_active_subprocess(tmp_path):
    marker = tmp_path / "orphan.txt"

    def work(_state):
        command = [
            sys.executable,
            "-c",
            f"import pathlib,time;time.sleep(2);pathlib.Path({str(marker)!r}).write_text('orphan')",
        ]
        sandbox._run(command, tmp_path, dict(os.environ), 30, "test")
        return {}

    update = with_timeout(work, "test", 0.3)({"error_trace": []})
    time.sleep(2.5)  # biên rộng: taskkill chậm khi máy tải nặng vẫn phải giết kịp trước lúc con ghi file

    assert update["status"] == "INFRA_ERROR"
    assert not marker.exists()
