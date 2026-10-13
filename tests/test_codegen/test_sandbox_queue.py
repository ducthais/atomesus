"""Entry point sandbox dùng một hàng đợi dù caller không đi qua graph."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.agents.timeout import with_timeout
from src.codegen import sandbox
from src.pipeline.settings import PipelineSettings


@pytest.mark.parametrize("limit", [1, 2])
def test_direct_sandbox_calls_share_slots_and_preserve_fifo(monkeypatch, limit):
    config = PipelineSettings(_env_file=None, max_parallel_sandbox=limit)
    lock, entered, release, queued = threading.Lock(), threading.Event(), threading.Event(), threading.Event()
    active, peak, calls = 0, 0, []

    def fake(spec, files, policy, settings, rerun_model):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(spec.id)
            if active == limit:
                entered.set()
        try:
            assert release.wait(5)
            return sandbox.SandboxResult("READY_FOR_REVIEW", "t_validate")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(sandbox, "_run_in_sandbox", fake)

    def run(index):
        return sandbox.run_in_sandbox(SimpleNamespace(id=index), {}, None, config)

    with ThreadPoolExecutor(max_workers=limit + 1) as pool:
        holders = [pool.submit(run, index) for index in range(limit)]
        try:
            assert entered.wait(5)
            after = pool.submit(
                with_timeout(
                    lambda _s: {"result": run(limit)}, "t_validate", 5, queue_progress=lambda _s, _k: queued.set()
                ),
                {},
            )
            assert queued.wait(5)
            assert len(calls) == limit
        finally:
            release.set()
        assert all(future.result(5).status == "READY_FOR_REVIEW" for future in holders)
        assert after.result(5)["result"].status == "READY_FOR_REVIEW"
    assert peak == limit and calls[-1] == limit and active == 0


def test_waiter_uses_condition_and_limits_cancellation_checks(monkeypatch):
    config = PipelineSettings(_env_file=None, max_parallel_sandbox=1)
    now = [0.0]
    checks, waits = [], []
    monkeypatch.setattr(sandbox, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class CancelledError(Exception):
        pass

    def check_cancelled():
        checks.append(now[0])
        if now[0] >= 3:
            raise CancelledError

    def wait_for(predicate, timeout):
        # Một lần chờ Condition có thể bị đánh thức nhiều lần; không đọc cờ hủy mỗi lần đó.
        assert not predicate()
        waits.append(timeout)
        now[0] += 0.05

    monkeypatch.setattr(sandbox._sandbox_condition, "wait_for", wait_for)
    with sandbox.sandbox_slot(config):

        def waiting():
            with sandbox.sandbox_wait_context(sandbox.SandboxWaitClock(), check_cancelled, lambda _k: None):
                with sandbox.sandbox_slot(config):
                    pytest.fail("Run đang chờ bị hủy không được chạy sandbox")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(CancelledError):
                pool.submit(waiting).result(5)
        assert sandbox._sandbox_waiters == sandbox.deque()
    assert len(waits) >= 60
    assert len(checks) == 4
    assert all(later - earlier >= 1 for earlier, later in zip(checks, checks[1:]))
