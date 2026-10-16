"""GET /policy: chỉ Admin nhận nội dung file YAML; Engineer/Reviewer chỉ nhận trần và domain của mình."""

from types import SimpleNamespace

import pytest

from src.main import app
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.policy import POLICY_DIR

LIMITS = {"max_llm_calls", "max_spec_attempts", "max_sql_attempts", "max_chat_calls", "max_usd_per_day"}


@pytest.fixture
def logged_in(client, tmp_path, monkeypatch):
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))

    async def login(username: str, password: str):
        response = await client.post("/api/v1/pipeline/session", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        return client

    yield login
    app.dependency_overrides.pop(auth.settings, None)


@pytest.mark.asyncio
async def test_admin_sees_policy_files_and_limits(logged_in):
    client = await logged_in("admin", "Admin@123")

    response = await client.get("/api/v1/policy")

    assert response.status_code == 200
    body = response.json()
    assert set(body["files"]) == {"domains.yml", "budgets.yml", "objectives.yml"}
    for filename, content in body["files"].items():
        assert content == (POLICY_DIR / filename).read_text(encoding="utf-8")
    assert "steward" in response.text and "sandbox_pg" in response.text
    assert set(body["limits"]) == LIMITS


@pytest.mark.asyncio
@pytest.mark.parametrize("username,password", [("engineer", "Engineer@123"), ("reviewer", "Reviewer@123")])
async def test_engineer_and_reviewer_get_limits_without_policy_files(logged_in, username, password):
    client = await logged_in(username, password)

    response = await client.get("/api/v1/policy")

    assert response.status_code == 200
    body = response.json()
    assert body["files"] == {}
    assert "steward" not in response.text and "sandbox_pg" not in response.text
    assert ".yml" not in response.text
    assert set(body["limits"]) == LIMITS and body["limits"]["max_chat_calls"] > 0
    assert isinstance(body["user_domains"], list)


@pytest.mark.asyncio
async def test_policy_still_requires_session(logged_in, client):
    assert (await client.get("/api/v1/policy")).status_code == 401
