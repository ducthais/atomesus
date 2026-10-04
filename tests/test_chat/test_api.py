from types import SimpleNamespace

import pytest

from src.agents.nodes.common import AgentDeps
from src.api import routes
from src.chat import api as chat_api
from src.main import app
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.policy import load_policy
from src.run_report import FIXTURES_DIR, RunReport, save_run_report

READY = "3f6c1a2e-8b4d-4c7a-9e21-0d5b7a1c9f10"
BLOCKED = "c4b8f0d3-1e2a-4f5b-b7c9-6a0e3d2f1b50"


def _fixture(name: str) -> RunReport:
    return RunReport.model_validate_json((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Sổ bằng chứng tạm có sẵn RunReport mẫu; agent không gọi LLM thật."""
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    for path in FIXTURES_DIR.glob("*.json"):
        save_run_report(config.store_path, RunReport.model_validate_json(path.read_text(encoding="utf-8")))
    state = SimpleNamespace(config=config, policy=load_policy())
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: AgentDeps(
        llm=lambda *_: "{}", policy=state.policy, settings=config
    )
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    yield state
    app.dependency_overrides.pop(auth.settings, None)
    app.dependency_overrides.pop(routes.agent_deps, None)


async def _login(client, user_id: str) -> None:
    username = "engineer" if user_id == "minh" else "reviewer"
    password = "Engineer@123" if user_id == "minh" else "Reviewer@123"
    assert (
        await client.post("/api/v1/pipeline/session", json={"username": username, "password": password})
    ).status_code == 200


@pytest.mark.asyncio
async def test_chat_requires_session(client, env):
    assert (await client.post("/api/v1/chat", json={"message": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_new_request_runs_agent_as_session_user_and_logs_messages(client, env, monkeypatch):
    calls = []

    def fake_run_agent(request, created_by, deps, store_path):
        calls.append((request, created_by))
        report = _fixture("ready_for_review.json").model_copy(update={"run_id": "new-run", "created_by": created_by})
        save_run_report(store_path, report)
        return report

    monkeypatch.setattr(chat_api, "run_agent", fake_run_agent)
    await _login(client, "minh")

    response = await client.post("/api/v1/chat", json={"message": "  Doanh thu theo ngày  "})

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "new_request"
    assert body["run_id"] == "new-run"
    assert "READY_FOR_REVIEW" in body["reply"]
    assert calls == [("Doanh thu theo ngày", "minh")]
    messages = (await client.get("/api/v1/runs/new-run/messages")).json()
    assert [(m["role"], m["author"], m["intent"]) for m in messages] == [
        ("user", "minh", "new_request"),
        ("assistant", "agent", "new_request"),
    ]


@pytest.mark.asyncio
async def test_reviewer_cannot_create_run_from_chat(client, env, monkeypatch):
    monkeypatch.setattr(chat_api, "run_agent", lambda *_: pytest.fail("không được chạy agent"))
    await _login(client, "an")

    assert (await client.post("/api/v1/chat", json={"message": "tạo pipeline"})).status_code == 403


@pytest.mark.asyncio
async def test_ask_answers_from_run_report_without_changing_status(client, env):
    await _login(client, "an")

    rows = (await client.post("/api/v1/chat", json={"message": "Nguồn có bao nhiêu dòng?", "run_id": READY})).json()
    why = (await client.post("/api/v1/chat", json={"message": "Vì sao bị chặn?", "run_id": BLOCKED})).json()

    assert rows["intent"] == "ask"
    assert "12480" in rows["reply"] and "checks[row_reconcile].observed" in rows["reply"]
    assert "customer" in why["reply"] and "semantic" in why["reply"]
    assert (await client.get(f"/api/v1/runs/{READY}")).json()["status"] == "READY_FOR_REVIEW"
    assert len((await client.get(f"/api/v1/runs/{READY}/messages")).json()) == 2


@pytest.mark.asyncio
async def test_ask_unknown_run_returns_404(client, env):
    await _login(client, "an")

    assert (await client.post("/api/v1/chat", json={"message": "?", "run_id": "nope"})).status_code == 404
    assert (await client.get("/api/v1/runs/nope/messages")).status_code == 404


@pytest.mark.asyncio
async def test_ask_limit_is_read_from_budgets(client, env):
    per_run = env.policy.budgets.per_run.model_copy(update={"max_chat_calls": 1})
    env.policy = env.policy.model_copy(update={"budgets": env.policy.budgets.model_copy(update={"per_run": per_run})})
    await _login(client, "an")

    first = await client.post("/api/v1/chat", json={"message": "trạng thái?", "run_id": READY})
    second = await client.post("/api/v1/chat", json={"message": "trạng thái?", "run_id": READY})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "max_chat_calls" in second.json()["detail"]


@pytest.mark.asyncio
async def test_chat_rejects_identity_fields_from_client(client, env):
    await _login(client, "minh")

    response = await client.post("/api/v1/chat", json={"message": "x", "run_id": READY, "user": "an"})

    assert response.status_code == 422
