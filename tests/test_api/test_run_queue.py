"""Đọc tiến độ chờ qua API, hủy run đang chờ và giới hạn fan-out lập kế hoạch."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.agents import service
from src.api import routes
from src.codegen import sandbox
from src.main import app
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.run_report import get_run_report
from src.run_report.store import is_run_cancelled
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, passing_sandbox


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_queued_progress_is_readable_and_cancellation_removes_the_waiter(client, tmp_path, monkeypatch, cancel):
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"), max_parallel_sandbox=1)
    calls, queued = [], threading.Event()

    def run(*args, **kwargs):
        calls.append("sandbox")
        return passing_sandbox(*args, **kwargs)

    agent = deps(FakeLLM(PLAN, SPEC), sandbox=run)
    agent.settings = config
    agent.cancelled = lambda run_id: is_run_cancelled(config.store_path, run_id)
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    original = service._record_sandbox_queue

    def record(state, store, position):
        original(state, store, position)
        queued.set()

    monkeypatch.setattr(service, "_record_sandbox_queue", record)
    try:
        assert (await client.get("/api/v1/agent/runs/missing")).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        assert (await client.get("/api/v1/agent/runs/missing")).status_code == 404
        _started, plan_job = service.start_agent("Doanh thu theo ngày", "minh", agent, config.store_path)
        planned = plan_job()
        started, job = service.start_confirm(planned, "minh", agent, config.store_path)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with sandbox.sandbox_slot(config):
                future = pool.submit(job)
                assert queued.wait(5)
                response = await client.get(f"/api/v1/agent/runs/{started.run_id}")
                assert response.status_code == 200
                body = response.json()
                assert body["status"] == "VALIDATING" and not calls
                assert body["timeline"][-1]["detail"] == "Đang xếp hàng chạy thử (vị trí 1)"
                if cancel:
                    cancelled = await client.post(f"/api/v1/runs/{started.run_id}/cancel")
                    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
                    assert future.result(5).status == "CANCELLED"
                    assert not calls
                    # Hủy thoát khỏi hàng trước khi run giữ chỗ kết thúc.
                    with sandbox._sandbox_condition:
                        assert sandbox._sandbox_condition.wait_for(lambda: not sandbox._sandbox_waiters, timeout=5)
            done = future.result(5)
        assert done.status == ("CANCELLED" if cancel else "READY_FOR_REVIEW")
        assert calls == ([] if cancel else ["sandbox"])
        persisted = get_run_report(config.store_path, started.run_id)
        queue_events = [event for event in persisted.timeline if event.event == "sandbox_queued"]
        assert len(queue_events) == 1
        assert queue_events[0].detail == "Đang xếp hàng chạy thử (vị trí 1)"
    finally:
        app.dependency_overrides.pop(auth.settings, None)


@pytest.mark.parametrize("limit", [1, 3, 7])
def test_batch_planning_never_exceeds_configured_workers(limit):
    config = PipelineSettings(_env_file=None, max_parallel_planning=limit)
    lock, entered, release = threading.Lock(), threading.Event(), threading.Event()
    active, peak, completed = 0, 0, 0

    def job():
        nonlocal active, peak, completed
        with lock:
            active += 1
            peak = max(peak, active)
            if active == limit:
                entered.set()
        try:
            assert release.wait(5)
        finally:
            with lock:
                active -= 1
                completed += 1

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(routes._run_batch_jobs, [job] * 20, config)
        try:
            assert entered.wait(5)
        finally:
            release.set()
        future.result(5)
    assert peak == limit and completed == 20
    routes._run_batch_jobs([], config)
