import threading
from types import SimpleNamespace

import pytest

from src.agents import budget
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.run_report.store import list_run_reports, reserve_budget_batch, spent_today
from tests.test_agents.test_graph import deps, tiered_llm


def test_batch_estimate_uses_policy_and_current_daily_balance(tmp_path):
    from src.policy.loader import load_policy

    settings = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    policy = load_policy()
    assert budget.reserve_daily(settings, "existing", 1, "scan", 0.4, policy.budgets.per_day.max_usd)[0]

    estimate = budget.estimate_batch(3, policy, settings)

    assert estimate["reservation_usd_per_run"] == policy.budgets.per_run.max_usd
    assert estimate["estimated_usd"] == pytest.approx(3 * policy.budgets.per_run.max_usd)
    assert estimate["spent_today_usd"] == pytest.approx(0.4)
    assert estimate["remaining_after_usd"] == pytest.approx(
        policy.budgets.per_day.max_usd - 0.4 - estimate["estimated_usd"]
    )
    assert estimate["max_batch_runs"] == policy.budgets.per_day.max_batch_runs
    assert estimate["allowed"] is True


def test_batch_reservation_is_all_or_none(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    assert reserve_budget_batch(store, [("used", 1, "scan", 0.95)], 1.0)[0]

    accepted, current = reserve_budget_batch(
        store,
        [("first", 1, "llm:plan:1", 0.03), ("second", 1, "llm:plan:1", 0.03)],
        1.0,
    )

    assert accepted is False and current == pytest.approx(0.95)
    assert spent_today(store) == pytest.approx(0.95), "mẻ bị chặn không được giữ chỗ một phần"


@pytest.mark.asyncio
async def test_batch_api_creates_every_run_after_one_budget_preflight(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        available = await client.get("/api/v1/agent/runs/batch/budget")
        assert available.status_code == 200
        assert available.json()["max_batch_runs"] >= 2

        response = await client.post(
            "/api/v1/agent/runs/batch",
            json={
                "requests": [
                    {
                        "request": "Doanh thu theo ngày",
                        "sources": [{"source_ref": "sales_pg", "tables": ["orders"]}],
                    },
                    {
                        "request": "Số đơn theo ngày",
                        "sources": [{"source_ref": "sales_pg", "tables": ["orders"]}],
                    },
                ]
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert len(body["runs"]) == 2
        assert len({run["run_id"] for run in body["runs"]}) == 2
        assert body["budget"]["estimated_usd"] == pytest.approx(0.1)
        persisted = list_run_reports(config.store_path)
        assert len(persisted) == 2
        assert all((run.status, run.stage_reached) == ("DRAFT", "confirm") for run in persisted)
        assert all(run.source_scope == [{"source_ref": "sales_pg", "tables": ["orders"]}] for run in persisted)
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


@pytest.mark.asyncio
async def test_batch_api_blocks_the_whole_batch_when_daily_balance_is_too_low(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    assert budget.reserve_daily(config, "existing", 1, "scan", 1.95, 2.0)[0]
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        response = await client.post(
            "/api/v1/agent/runs/batch",
            json={"requests": [{"request": "Pipeline A"}, {"request": "Pipeline B"}]},
        )

        assert response.status_code == 409
        assert "không tạo pipeline nào" in response.json()["detail"]
        assert list_run_reports(config.store_path) == []
        assert spent_today(config.store_path) == pytest.approx(1.95)
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


@pytest.mark.asyncio
async def test_batch_api_uses_existing_reservations_at_exact_daily_limit(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    agent = deps(tiered_llm)
    agent.settings = config
    agent.spent_today = budget.spent_today
    agent.reserve_daily = budget.reserve_daily
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: agent
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    assert budget.reserve_daily(config, "existing", 1, "scan", 1.9, 2.0)[0]
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        response = await client.post(
            "/api/v1/agent/runs/batch",
            json={"requests": [{"request": "Pipeline A"}, {"request": "Pipeline B"}]},
        )

        assert response.status_code == 201
        persisted = list_run_reports(config.store_path)
        assert len(persisted) == 2
        assert all((run.status, run.stage_reached) == ("DRAFT", "confirm") for run in persisted)
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


@pytest.mark.asyncio
async def test_batch_api_rolls_back_started_runs_when_initialization_fails(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    real_start = routes.start_agent
    calls = 0

    def fail_second_start(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("không thể khởi tạo run thứ hai")
        return real_start(*args, **kwargs)

    monkeypatch.setattr(routes, "start_agent", fail_second_start)
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        with pytest.raises(RuntimeError, match="run thứ hai"):
            await client.post(
                "/api/v1/agent/runs/batch",
                json={"requests": [{"request": "Pipeline A"}, {"request": "Pipeline B"}]},
            )

        assert list_run_reports(config.store_path) == []
        assert spent_today(config.store_path) == pytest.approx(0.0)
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


@pytest.mark.asyncio
async def test_batch_api_fans_out_jobs_concurrently(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    rendezvous = threading.Barrier(2, timeout=2)
    real_start = routes.start_agent

    def concurrent_start(*args, **kwargs):
        started, job_to_run = real_start(*args, **kwargs)

        def job():
            rendezvous.wait()
            return job_to_run()

        return started, job

    monkeypatch.setattr(routes, "start_agent", concurrent_start)
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        response = await client.post(
            "/api/v1/agent/runs/batch",
            json={"requests": [{"request": "Pipeline A"}, {"request": "Pipeline B"}]},
        )

        assert response.status_code == 201
        assert all(run.status == "DRAFT" for run in list_run_reports(config.store_path))
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)
