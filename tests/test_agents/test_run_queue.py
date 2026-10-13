"""Hàng FIFO chạy thử: sandbox giả, đồng bộ bằng sự kiện, không cần dlt/dbt thật."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from src.agents import manual_optimize, regression, service, timeout
from src.agents.nodes.optimize import optimize_node
from src.agents.nodes.sandbox_run import sandbox_run_node
from src.codegen import sandbox
from src.pipeline.settings import PipelineSettings
from src.policy.loader import POLICY_DIR, POLICY_FILES, load_policy
from src.run_report import RunReport, save_run_report
from src.run_report.store import FIXTURES_DIR, is_run_cancelled, save_run_files
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, passing_sandbox
from tests.test_agents.test_optimizer import fake_sandbox


@pytest.fixture
def config(tmp_path):
    return PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"), max_parallel_sandbox=1)


@pytest.fixture
def fake_clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(sandbox, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(timeout, "CANCEL_POLL_SEC", 0.01)
    return now


def _queued(monkeypatch):
    events = {name: threading.Event() for name in ("second", "third")}
    positions = []
    original = service._record_sandbox_queue

    def record(state, store, position):
        original(state, store, position)
        positions.append((state["run_id"], position))
        events[state["run_id"]].set()

    monkeypatch.setattr(service, "_record_sandbox_queue", record)
    return events, positions


def _agent(config, run):
    agent = deps(FakeLLM(), sandbox=run)
    agent.settings = config
    agent.cancelled = lambda run_id: is_run_cancelled(config.store_path, run_id)
    return agent


def _state(run_id):
    return {
        "run_id": run_id,
        "version": 1,
        "spec": SPEC,
        "generated_files": {"id": run_id},
        "status": "VALIDATING",
        "timeline": [],
    }


def test_fifo_wait_is_excluded_from_phase_timeout(config, monkeypatch, fake_clock):
    queued, positions = _queued(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def first(_state):
        entered.set()
        assert release.wait(5)
        return {}

    def run(spec, files, policy, settings):
        calls.append(files["id"])
        return passing_sandbox(spec, files, policy, settings)

    agent = _agent(config, run)
    node = sandbox_run_node(agent)

    # Trần một giây nhưng đồng hồ giả sẽ đi qua 100 giây trong lúc xếp hàng.
    def phase(state):
        return timeout.with_timeout(
            lambda s: _in_slot(config, node, s),
            "t_validate",
            1,
            queue_progress=lambda s, k: service._record_sandbox_queue(s, config.store_path, k),
        )(state)

    with ThreadPoolExecutor(max_workers=3) as pool:
        holder = pool.submit(timeout.timed(agent, "test_run", first), {})
        try:
            assert entered.wait(5)
            second = pool.submit(phase, _state("second"))
            assert queued["second"].wait(5)
            third = pool.submit(phase, _state("third"))
            assert queued["third"].wait(5)
            fake_clock[0] = 100.0
            assert not calls and not second.done() and not third.done()
        finally:
            release.set()
        assert holder.result(5) == {}
        assert second.result(5)["status"] == third.result(5)["status"] == "READY_FOR_REVIEW"
    assert calls == ["second", "third"]
    assert positions[:2] == [("second", 1), ("third", 2)]


def _in_slot(config, node, state):
    with sandbox.sandbox_slot(config):
        return node(state)


@pytest.mark.parametrize("error", [RuntimeError("hỏng"), TimeoutError("quá hạn")])
def test_sandbox_errors_release_the_slot(config, monkeypatch, error):
    entered, release = threading.Event(), threading.Event()
    queued, _positions = _queued(monkeypatch)
    calls = []

    def broken(_state):
        entered.set()
        assert release.wait(5)
        raise error

    agent = _agent(config, passing_sandbox)
    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(timeout.timed(agent, "test_run", broken), {})
        try:
            assert entered.wait(5)
            after = pool.submit(
                timeout.timed(agent, "test_run", lambda _s: calls.append("second") or {}), _state("second")
            )
            assert queued["second"].wait(5)
            assert calls == []
        finally:
            release.set()
        with pytest.raises(type(error), match=str(error)):
            failed.result(5)
        assert after.result(5) == {} and calls == ["second"]


def test_phase_timeout_keeps_slot_until_sandbox_cleanup_finishes(config, monkeypatch, fake_clock):
    entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()
    queued, _positions = _queued(monkeypatch)
    calls = []

    def running(_state):
        entered.set()
        try:
            while True:
                sandbox.raise_if_phase_cancelled()
                release.wait(0.01)
        finally:
            cleanup.set()
            assert release.wait(5)

    agent = _agent(config, passing_sandbox)
    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(timeout.timed(agent, "test_run", running), {})
        try:
            assert entered.wait(5)
            fake_clock[0] = 10_000.0
            assert failed.result(5)["status"] == "INFRA_ERROR"
            assert cleanup.wait(5)
            after = pool.submit(
                timeout.timed(agent, "test_run", lambda _s: calls.append("second") or {}), _state("second")
            )
            assert queued["second"].wait(5)
            assert calls == []
        finally:
            release.set()
        assert after.result(5) == {} and calls == ["second"]


@pytest.mark.parametrize("kind", ["optimizer", "manual_optimize", "regression"])
def test_optimizer_and_regression_use_the_same_queue(config, monkeypatch, kind):
    parent = RunReport.model_validate_json((FIXTURES_DIR / "deployed.json").read_text(encoding="utf-8"))
    parent = parent.model_copy(update={"spec": SPEC, "plan": PLAN, "user_request": "Doanh thu theo ngày"})
    save_run_report(config.store_path, parent)
    save_run_files(config.store_path, parent.run_id, parent.version, {"file": "text"})
    run = fake_sandbox({"table": 1.0, "incremental": 0.3})
    agent = _agent(config, run)
    queued = threading.Event()
    original = service._record_sandbox_queue

    def record(state, store, position):
        original(state, store, position)
        queued.set()

    monkeypatch.setattr(service, "_record_sandbox_queue", record)
    if kind == "optimizer":
        state = {
            **_state("second"),
            "status": "READY_FOR_REVIEW",
            "checks": [{"name": "staging_reconcile", "source_id": "orders", "observed": {"staging": 50_000}}],
        }

        def job():
            return timeout.timed(agent, "optimize", optimize_node(agent))(state)
    elif kind == "manual_optimize":
        _started, job = manual_optimize.start_manual_optimize(parent, "minh", agent, config.store_path)
    else:

        def job():
            return regression._rerun_current(parent, agent, config.store_path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with sandbox.sandbox_slot(config):
            future = pool.submit(job)
            assert queued.wait(5)
            assert run.calls == []
        assert future.result(5) is not None
    assert run.calls
    if kind == "optimizer":
        assert run.calls == [
            ("table", agent.policy.budgets.optimizer.rerun_samples),
            ("incremental", agent.policy.budgets.optimizer.rerun_samples),
        ]


def test_legacy_budgets_without_batch_limit_loads_with_default(tmp_path):
    for name in POLICY_FILES:
        raw = yaml.safe_load((POLICY_DIR / f"{name}.yml").read_text(encoding="utf-8"))
        if name == "budgets":
            raw["per_day"].pop("max_batch_runs", None)
        (tmp_path / f"{name}.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_policy(tmp_path).budgets.per_day.max_batch_runs == 20


def test_parallelism_settings_read_environment_and_reject_zero(monkeypatch):
    monkeypatch.setenv("PIPELINE_MAX_PARALLEL_SANDBOX", "2")
    monkeypatch.setenv("PIPELINE_MAX_PARALLEL_PLANNING", "4")
    config = PipelineSettings(_env_file=None)
    assert (config.max_parallel_sandbox, config.max_parallel_planning) == (2, 4)
    for field in ("max_parallel_sandbox", "max_parallel_planning"):
        with pytest.raises(ValidationError):
            PipelineSettings(_env_file=None, **{field: 0})
