"""Mỗi người chỉ thấy run của mình; Reviewer thấy run của Engineer đã mời (ADR-0001 D1, D2, bất biến 9)."""

import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.agents.nodes.common import AgentDeps
from src.api import monitor, routes
from src.main import app
from src.monitor import alerts
from src.pipeline import api, auth, store
from src.pipeline.settings import PipelineSettings
from src.policy import load_policy
from src.run_report import FIXTURES_DIR, RunReport, save_run_report
from src.run_report.router import pipeline_github, request_change_deps
from src.run_report.store import save_run_files

READY = "3f6c1a2e-8b4d-4c7a-9e21-0d5b7a1c9f10"
PASSWORD = "StrongPass9"
USERS = {"eng.a": "Engineer", "eng.b": "Engineer", "rev.one": "Reviewer"}


def _fixture(name: str) -> RunReport:
    return RunReport.model_validate_json((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _with_model(report: RunReport, model: str, **update) -> RunReport:
    spec = {**report.spec, "transform": {**report.spec["transform"], "model_name": model}}
    return report.model_copy(update={"spec": spec, **update})


@pytest_asyncio.fixture
async def env(client, tmp_path, monkeypatch):
    """Hai Engineer (A, B), một Reviewer chưa liên kết, run của từng người, run hệ thống và cảnh báo Day 2."""
    from src.codegen.render import file_hashes, render
    from src.spec import SPEC_FIXTURES_DIR, Spec

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    config.deploy_dir = str(tmp_path / "dagster_local")
    (tmp_path / "dagster_local" / "b_model").mkdir(parents=True)  # pipeline của B đang deploy
    environment = SimpleNamespace(app_env="test")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "get_settings", lambda: environment)
    for path in FIXTURES_DIR.glob("*.json"):
        save_run_report(config.store_path, RunReport.model_validate_json(path.read_text(encoding="utf-8")))
    ids = {}
    for username, role in USERS.items():
        ids[username] = store.create_user(
            config.store_path,
            user_id=f"user_{username.replace('.', '_')}",
            username=username,
            name=username,
            role=role,
            password_hash=auth.hash_password(PASSWORD),
            created_at=time.time(),
            email=f"{username}@example.com",
        )["id"]

    spec = Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    files = render(spec, load_policy())
    hashes, files_hash = file_hashes(files)
    ready = _fixture("ready_for_review.json").model_copy(
        update={"spec": spec.model_dump(by_alias=True, mode="json"), "files": hashes, "files_hash": files_hash}
    )
    run_a = _with_model(ready, "a_model", run_id="run-a", created_by=ids["eng.a"])
    run_b = _with_model(ready, "b_model", run_id="run-b", created_by=ids["eng.b"])
    deployed_b = _with_model(_fixture("deployed.json"), "b_model", run_id="run-b-dep", created_by=ids["eng.b"])
    system_b = run_b.model_copy(
        update={"run_id": "run-sys", "created_by": "system", "trigger": "regression", "parent_run_id": "run-b-dep"}
    )
    for report in (run_a, run_b, deployed_b, system_b):
        save_run_report(config.store_path, report)
        save_run_files(config.store_path, report.run_id, report.version, files)
    alerts.raise_alert(config.store_path, "b_model", "needs_human", "B cần sửa", run_id="run-sys")
    alerts.raise_alert(config.store_path, "a_model", "no_improvement", "A gắn cờ", run_id="run-a")

    deps = AgentDeps(llm=lambda *_: "{}", policy=load_policy(), settings=config)
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps
    app.dependency_overrides[monitor.monitor_deps] = lambda: deps
    app.dependency_overrides[request_change_deps] = lambda: deps
    app.dependency_overrides[pipeline_github] = lambda: None
    try:
        yield SimpleNamespace(client=client, config=config, ids=ids, files_hash=files_hash)
    finally:
        for dependency in (
            auth.settings,
            routes.agent_deps,
            monitor.monitor_deps,
            request_change_deps,
            pipeline_github,
        ):
            app.dependency_overrides.pop(dependency, None)


async def _login(env, username: str, password: str = PASSWORD) -> None:
    await env.client.delete("/api/v1/pipeline/session")
    response = await env.client.post("/api/v1/pipeline/session", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


async def _run_ids(env) -> set[str]:
    response = await env.client.get("/api/v1/runs")
    assert response.status_code == 200
    return {run["run_id"] for run in response.json()}


async def _invite(env) -> str:
    response = await env.client.post("/api/v1/pipeline/invites")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["path"].endswith(body["token"])
    return body["token"]


async def _blocked_reads(env, run_id: str) -> None:
    client = env.client
    for path in ("", "/compare", "/files", "/messages"):
        assert (await client.get(f"/api/v1/runs/{run_id}{path}")).status_code == 404, path
    chat = await client.post("/api/v1/chat", json={"message": "Run này đọc bảng nào?", "run_id": run_id})
    assert chat.status_code == 404


async def _blocked_reviews(env, run_id: str) -> None:
    client, review = env.client, {"files_hash": env.files_hash, "comment": "ok"}
    for action in ("approve", "reject"):
        assert (await client.post(f"/api/v1/runs/{run_id}/{action}", json=review)).status_code == 404, action
    changes = await client.post(f"/api/v1/runs/{run_id}/request-changes", json={"feedback": "Sửa cột"})
    assert changes.status_code == 404
    assert (await client.post(f"/api/v1/runs/{run_id}/deploy")).status_code == 404
    batch = await client.post(
        "/api/v1/runs/batch-review",
        json={"decision": "approve", "items": [{"run_id": run_id, "files_hash": env.files_hash}]},
    )
    assert batch.json()["results"] == [
        {"run_id": run_id, "ok": False, "status": None, "message": f"Không tìm thấy run {run_id}"}
    ]


@pytest.mark.asyncio
async def test_engineer_sees_only_own_runs_and_every_endpoint_hides_others(env):
    await _login(env, "eng.a")

    assert await _run_ids(env) == {"run-a"}
    for run_id in ("run-b", "run-sys", "run-b-dep", READY):
        await _blocked_reads(env, run_id)
    client = env.client
    assert (await client.get("/api/v1/runs/run-a/compare", params={"base": "run-b"})).status_code == 404
    assert (await client.post("/api/v1/runs/run-b/cancel")).status_code == 404
    assert (await client.post("/api/v1/agent/runs/run-b/confirm-plan", json={})).status_code == 404
    assert (await client.post("/api/v1/agent/runs/run-b/continue", json={"spec": {}})).status_code == 404
    revise = await client.post("/api/v1/chat", json={"message": "Đổi cột doanh thu thành net", "run_id": "run-b"})
    assert revise.status_code == 404
    parent = await client.post("/api/v1/agent/runs", json={"request": "Doanh thu", "parent_run_id": "run-b"})
    assert parent.status_code == 404
    # Pipeline, số đo, cảnh báo của B.
    assert (await client.get("/api/v1/monitor/pipelines")).json() == []
    assert (await client.get("/api/v1/monitor/pipelines/b_model")).status_code == 404
    assert (await client.post("/api/v1/pipelines/b_model/optimize")).status_code == 404
    assert {alert["model"] for alert in (await client.get("/api/v1/monitor/alerts")).json()} == {"a_model"}
    b_alert = alerts.list_alerts(env.config.store_path, "b_model")[0]["id"]
    assert (await client.post(f"/api/v1/monitor/alerts/{b_alert}/resolve")).status_code == 404
    assert alerts.get_alert(env.config.store_path, b_alert)["resolved_at"] is None


@pytest.mark.asyncio
async def test_system_run_belongs_to_owner_of_parent(env):
    await _login(env, "eng.b")

    assert await _run_ids(env) == {"run-b", "run-b-dep", "run-sys"}
    assert (await env.client.get("/api/v1/runs/run-sys")).status_code == 200
    assert (await env.client.get("/api/v1/monitor/pipelines/b_model")).status_code == 200
    assert [row["model"] for row in (await env.client.get("/api/v1/monitor/pipelines")).json()] == ["b_model"]
    assert {alert["model"] for alert in (await env.client.get("/api/v1/monitor/alerts")).json()} == {"b_model"}


@pytest.mark.asyncio
async def test_unlinked_reviewer_sees_nothing_and_cannot_review(env):
    await _login(env, "rev.one")

    assert await _run_ids(env) == set()
    await _blocked_reads(env, "run-a")
    await _blocked_reviews(env, "run-a")
    assert (await env.client.get("/api/v1/monitor/alerts")).json() == []


@pytest.mark.asyncio
async def test_accepted_invite_grants_review_and_unlinking_revokes_it_immediately(env):
    await _login(env, "eng.a")
    token = await _invite(env)

    await _login(env, "rev.one")
    assert (await env.client.post("/api/v1/pipeline/invites/accept", json={"token": token})).status_code == 200
    assert await _run_ids(env) == {"run-a"}, "chỉ thấy run của Engineer đã mời, không thấy run của B"
    await _blocked_reviews(env, "run-b")
    assert (await env.client.get("/api/v1/runs/run-a/files")).status_code == 200
    approved = await env.client.post("/api/v1/runs/run-a/approve", json={"files_hash": env.files_hash})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["reviewer"] == env.ids["rev.one"]

    await _login(env, "eng.a")
    reviewers = (await env.client.get("/api/v1/pipeline/reviewers")).json()
    assert [(item["id"], item["demo"]) for item in reviewers] == [(env.ids["rev.one"], False)]
    assert (await env.client.delete(f"/api/v1/pipeline/reviewers/{env.ids['rev.one']}")).status_code == 200
    assert (await env.client.delete(f"/api/v1/pipeline/reviewers/{env.ids['rev.one']}")).status_code == 404

    await _login(env, "rev.one")
    assert await _run_ids(env) == set()
    assert (await env.client.get("/api/v1/runs/run-a")).status_code == 404


@pytest.mark.asyncio
async def test_invite_token_is_single_use_expires_and_rejects_engineers(env):
    await _login(env, "eng.a")
    token = await _invite(env)
    expired = await _invite(env)
    with store.database(env.config.store_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviewer_invites WHERE token_hash = ?", (token,)).fetchone()[0] == 0
        conn.execute(
            "UPDATE reviewer_invites SET expires = ? WHERE token_hash = ?", (time.time() - 1, api._invite_hash(expired))
        )

    engineer = await env.client.post("/api/v1/pipeline/invites/accept", json={"token": token})
    assert engineer.status_code == 400 and "Engineer" in engineer.json()["detail"]

    await _login(env, "rev.one")
    accept = "/api/v1/pipeline/invites/accept"
    assert (await env.client.post(accept, json={"token": token})).status_code == 200
    reused = await env.client.post(accept, json={"token": token})
    assert reused.status_code == 410
    assert (await env.client.post(accept, json={"token": expired})).status_code == 410
    unknown = await env.client.post(accept, json={"token": "x" * 43})
    assert unknown.status_code == 404
    for response in (reused, unknown):
        assert "eng.a" not in response.text and env.ids["eng.a"] not in response.text
    with store.database(env.config.store_path) as conn:
        assert conn.execute(
            "SELECT used_by FROM reviewer_invites WHERE token_hash = ?", (api._invite_hash(token),)
        ).fetchone() == (env.ids["rev.one"],)


@pytest.mark.asyncio
@pytest.mark.parametrize("verification", [False, True], ids=["no-verification", "email-verification"])
async def test_register_with_invite_creates_linked_reviewer(env, verification):
    env.config.registration_email_verification = verification
    await _login(env, "eng.a")
    token = await _invite(env)
    await env.client.delete("/api/v1/pipeline/session")
    payload = {"name": "Linh", "email": "linh@example.com", "username": "linh.review", "password": PASSWORD}

    bad = await env.client.post("/api/v1/pipeline/register", json={**payload, "invite_token": "y" * 43})
    assert bad.status_code == 404, "token sai không tạo tài khoản và không giữ lượt của IP"
    response = await env.client.post("/api/v1/pipeline/register", json={**payload, "invite_token": token})
    if verification:
        assert response.status_code == 202, response.text
        response = await env.client.post(
            "/api/v1/pipeline/register/verify",
            json={"email": payload["email"], "code": response.json()["development_code"]},
        )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "Reviewer"
    assert await _run_ids(env) == {"run-a"}

    await env.client.delete("/api/v1/pipeline/session")
    with store.database(env.config.store_path) as conn:
        conn.execute("DELETE FROM registration_ips")
    again = {**payload, "email": "khac@example.com", "username": "khac.user", "invite_token": token}
    assert (await env.client.post("/api/v1/pipeline/register", json=again)).status_code == 410


@pytest.mark.asyncio
async def test_only_engineers_manage_invites(env):
    await _login(env, "rev.one")
    assert (await env.client.post("/api/v1/pipeline/invites")).status_code == 403
    assert (await env.client.get("/api/v1/pipeline/reviewers")).status_code == 403


@pytest.mark.asyncio
async def test_demo_pair_keeps_working_and_admin_sees_everything(env):
    await _login(env, "reviewer", "Reviewer@123")
    demo = await _run_ids(env)
    assert READY in demo and "c4b8f0d3-1e2a-4f5b-b7c9-6a0e3d2f1b50" in demo, "run của minh và dữ liệu cũ"
    assert not demo & {"run-a", "run-b", "run-sys", "run-b-dep"}

    await _login(env, "engineer", "Engineer@123")
    assert await _run_ids(env) == demo
    reviewers = (await env.client.get("/api/v1/pipeline/reviewers")).json()
    assert [(item["id"], item["demo"]) for item in reviewers] == [("an", True)]
    assert (await env.client.delete("/api/v1/pipeline/reviewers/an")).status_code == 409

    await _login(env, "admin", "Admin@123")
    assert await _run_ids(env) == demo | {"run-a", "run-b", "run-sys", "run-b-dep"}
    assert (await env.client.get("/api/v1/runs/run-b/files")).status_code == 200
    assert {alert["model"] for alert in (await env.client.get("/api/v1/monitor/alerts")).json()} == {
        "a_model",
        "b_model",
    }
