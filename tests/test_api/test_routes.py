import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/agent/status")
    assert response.status_code == 200
    assert response.json()["graph"] == ["planner", "codegen", "render", "test_run"]


@pytest.mark.asyncio
async def test_policy_requires_session_and_returns_files_and_limits(client, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.main import app
    from src.pipeline import auth
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        assert (await client.get("/api/v1/policy")).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"user_id": "an"})
        body = (await client.get("/api/v1/policy")).json()
        assert set(body["files"]) == {"domains.yml", "budgets.yml", "objectives.yml"}
        assert "max_llm_calls: 6" in body["files"]["budgets.yml"]
        assert body["limits"] == {
            "max_llm_calls": 6,
            "max_spec_attempts": 2,
            "max_sql_attempts": 2,
            "max_usd_per_day": 2.0,
        }
    finally:
        app.dependency_overrides.pop(auth.settings, None)
