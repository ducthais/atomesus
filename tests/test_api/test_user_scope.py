"""Mỗi người chỉ thấy run của mình; Reviewer thấy run của Engineer đã mời (ADR-0001 D1, D2, bất biến 9)."""

import sqlite3
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.agents.nodes.common import AgentDeps
from src.api import monitor, routes
from src.main import app
from src.monitor import alerts, day2, detectors, runs
from src.orchestration import dagster
from src.pipeline import api, auth, connectors, store
from src.pipeline.settings import PipelineSettings
from src.policy import load_policy
from src.run_report import FIXTURES_DIR, RunReport, save_run_report
from src.run_report.access import LEGACY_OWNER
from src.run_report.router import pipeline_github, request_change_deps
from src.run_report.store import get_run_files, get_run_report, save_run_files

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
        await _blocked_reviews(env, run_id)  # 404 trước khi xét vai, không phải 403
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
async def test_compare_checks_base_before_returning_early_for_runs_without_spec(env):
    draft = _fixture("blocked_semantic.json").model_copy(
        update={"run_id": "run-a-draft", "created_by": env.ids["eng.a"], "status": "DRAFT"}
    )
    save_run_report(env.config.store_path, draft)
    await _login(env, "eng.a")

    for hidden in ("run-b", "run-sys", READY, "khong-co-run-nay"):
        response = await env.client.get("/api/v1/runs/run-a-draft/compare", params={"base": hidden})
        assert response.status_code == 404, hidden
        assert response.json()["detail"] == f"Không tìm thấy run {hidden}"
    own = await env.client.get("/api/v1/runs/run-a-draft/compare", params={"base": "run-a"})
    assert own.status_code == 200 and own.json()["available"] is False


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
    # Pipeline, cảnh báo không được thấy: 404 trước khi xét vai (Reviewer), không phải 403.
    b_alert = alerts.list_alerts(env.config.store_path, "b_model")[0]["id"]
    assert (await env.client.post(f"/api/v1/monitor/alerts/{b_alert}/resolve")).status_code == 404
    assert (await env.client.post("/api/v1/pipelines/b_model/optimize")).status_code == 404

    await _link(env, "eng.b")
    assert (await env.client.post(f"/api/v1/monitor/alerts/{b_alert}/resolve")).status_code == 403
    assert (await env.client.post("/api/v1/pipelines/b_model/optimize")).status_code == 403
    assert alerts.get_alert(env.config.store_path, b_alert)["resolved_at"] is None


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


def _deploy(env, model: str, owner: str, run_id: str, days_later: int = 1) -> RunReport:
    """Deployment của `owner` mới hơn deployment của B, để thành bản đang chạy của `model`."""
    deployed = _fixture("deployed.json")
    report = _with_model(
        deployed,
        model,
        run_id=run_id,
        created_by=env.ids[owner],
        created_at=deployed.created_at + timedelta(days=days_later),
    )
    save_run_report(env.config.store_path, report)
    (Path(env.config.deploy_dir) / model).mkdir(exist_ok=True)
    return report


@pytest.mark.asyncio
async def test_alert_permission_follows_its_run_and_owner_not_model_name(env):
    """Dữ liệu cũ: A đã deploy trùng tên `b_model` với B. Cảnh báo cũ của B (gắn run, hoặc ghi chủ B) vẫn chỉ của B;
    tên model lẫn hai chủ thì không ai ngoài Admin thấy pipeline đó."""
    _deploy(env, "b_model", "eng.a", "run-a-dep")
    store_path = env.config.store_path
    owned = alerts.raise_alert(store_path, "b_model", "source_stalled", "Nguồn của B", owner=env.ids["eng.b"])
    b_run = alerts.list_alerts(store_path, "b_model")[-1]
    assert b_run["run_id"] == "run-sys"

    await _login(env, "eng.a")
    assert (await env.client.get("/api/v1/monitor/pipelines")).json() == []
    listed = (await env.client.get("/api/v1/monitor/alerts", params={"model": "b_model"})).json()
    assert listed == []
    for alert in (owned, b_run):
        assert (await env.client.post(f"/api/v1/monitor/alerts/{alert['id']}/resolve")).status_code == 404
        assert alerts.get_alert(store_path, alert["id"])["resolved_at"] is None

    await _login(env, "eng.b")
    listed = (await env.client.get("/api/v1/monitor/alerts", params={"model": "b_model"})).json()
    assert {alert["id"] for alert in listed} == {owned["id"], b_run["id"]}
    assert (await env.client.post(f"/api/v1/monitor/alerts/{owned['id']}/resolve")).status_code == 200


@pytest.mark.asyncio
async def test_manual_check_and_collect_never_touch_other_peoples_pipelines(env, monkeypatch):
    finding = detectors.Finding("runtime_regression", 20.0, 15.0, 10.0, "chạy chậm")
    stalled = detectors.Finding("freshness_breach", 120.0, 60.0, None, "nguồn ngừng", stalled_sources=("orders",))
    monkeypatch.setattr(detectors, "detect", lambda *a: [finding, stalled])
    launched, read = [], []

    def launcher(deps, store_path, submit):
        return lambda parent, evidence: launched.append(parent.run_id) or parent

    monkeypatch.setattr(day2, "launcher", launcher)
    found = [{"runId": "dg-b", "jobName": "b_model_job", "status": "SUCCESS", "startTime": 1.8e9, "endTime": 1.8e9 + 9}]
    monkeypatch.setattr(runs, "fetch_runs", lambda url: found)
    monkeypatch.setattr(runs, "source_records", lambda *a: read.append(a) or {})
    monkeypatch.setattr(runs.budget, "estimate", lambda *a: {})
    store_path = env.config.store_path
    full_spec = get_run_report(store_path, "run-b").spec  # /collect dựng Spec đầy đủ của bản đang deploy
    save_run_report(store_path, get_run_report(store_path, "run-b-dep").model_copy(update={"spec": full_spec}))

    await _login(env, "eng.a")
    assert (await env.client.post("/api/v1/monitor/check")).json() == {"actions": []}
    assert (await env.client.post("/api/v1/monitor/collect")).json() == {"collected": 0, "runs": []}
    assert launched == [] and read == [], "không tạo run sửa, không đọc nguồn của pipeline B"
    assert [alert["kind"] for alert in alerts.list_alerts(store_path, "b_model")] == ["needs_human"]
    assert runs.recorded(store_path) == set()

    await _login(env, "eng.b")
    actions = (await env.client.post("/api/v1/monitor/check")).json()["actions"]
    assert {(action["model"], action["action"]) for action in actions} == {
        ("b_model", "alert"),
        ("b_model", "fix_started"),
    }
    assert launched == ["run-b-dep"]
    stalled_alert = alerts.list_alerts(store_path, "b_model")[0]
    assert stalled_alert["kind"] == "source_stalled" and stalled_alert["owner"] == env.ids["eng.b"]
    assert (await env.client.post("/api/v1/monitor/collect")).json()["collected"] == 1
    assert len(read) == 1 and runs.recorded(store_path) == {"dg-b"}


def _dagster_asset(jobs: list[str], run_job: str, run_id: str) -> dict:
    return {
        "jobs": jobs,
        "last_materialized_at": "2026-10-02T00:00:00+00:00",
        "last_materialized_run_id": run_id,
        "last_run": {"run_id": run_id, "job": run_job, "status": "SUCCESS", "started_at": None, "ended_at": None},
        "schedule": {"name": f"{run_job}_schedule", "job": run_job, "cron": "0 2 * * *", "running": True},
    }


@pytest.mark.asyncio
async def test_orchestration_status_only_shows_own_jobs(env, monkeypatch):
    _deploy(env, "a_model", "eng.a", "run-a-dep")
    assets = {
        "gold/a_model": _dagster_asset(["a_model_job"], "a_model_job", "dg-a"),
        "gold/b_model": _dagster_asset(["b_model_job"], "b_model_job", "dg-b"),
        # Asset dùng chung: lần chạy, lịch và lần materialize gần nhất là của B.
        "bronze/shared": _dagster_asset(["a_model_job", "b_model_job"], "b_model_job", "dg-b"),
    }
    monkeypatch.setattr(dagster, "fetch_status", lambda url: assets)

    async def status() -> dict:
        body = (await env.client.get("/api/v1/orchestration/status")).json()
        assert body["available"] is True
        return body["assets"]

    await _login(env, "eng.a")
    seen = await status()
    assert set(seen) == {"gold/a_model", "bronze/shared"}
    assert seen["bronze/shared"] == {
        "jobs": ["a_model_job"],
        "last_materialized_at": None,
        "last_materialized_run_id": None,
        "last_run": None,
        "schedule": None,
    }
    assert "dg-b" not in str(seen) and "b_model" not in str(seen)

    await _login(env, "rev.one")
    assert await status() == {}

    await _login(env, "eng.b")
    seen = await status()
    assert set(seen) == {"gold/b_model", "bronze/shared"}
    assert seen["bronze/shared"]["last_run"]["run_id"] == "dg-b" and seen["bronze/shared"]["jobs"] == ["b_model_job"]

    await _login(env, "admin", "Admin@123")
    assert await status() == assets


def _strip_created_by(env, run_id: str) -> None:
    """Biến run đã lưu thành dữ liệu cũ: bản lưu thiếu `created_by`."""
    with sqlite3.connect(env.config.store_path) as conn:
        conn.execute("UPDATE run_reports SET report = json_remove(report, '$.created_by') WHERE run_id = ?", (run_id,))


def _stored_created_by(env, run_id: str) -> tuple[int, str | None]:
    """(có khóa `created_by` không, giá trị) trong JSON đã lưu."""
    with sqlite3.connect(env.config.store_path) as conn:
        return conn.execute(
            "SELECT json_type(report, '$.created_by') IS NOT NULL, json_extract(report, '$.created_by')"
            " FROM run_reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()


@pytest.mark.asyncio
async def test_legacy_run_without_created_by_is_readable_only_by_admin_and_demo_pair(env):
    save_run_report(env.config.store_path, _fixture("ready_for_review.json").model_copy(update={"run_id": "run-old"}))
    _strip_created_by(env, "run-old")

    for username, password in (("admin", "Admin@123"), ("engineer", "Engineer@123"), ("reviewer", "Reviewer@123")):
        await _login(env, username, password)
        assert "run-old" in await _run_ids(env), username
        detail = await env.client.get("/api/v1/runs/run-old")
        assert detail.status_code == 200, detail.text
        assert detail.json()["created_by"] is None, "đọc ra không gán chủ nào cho run cũ"

    for username in ("eng.a", "rev.one"):
        await _login(env, username)
        assert "run-old" not in await _run_ids(env)
        await _blocked_reads(env, "run-old")
    await _blocked_reviews(env, "run-old")  # rev.one: Reviewer chưa liên kết
    assert _stored_created_by(env, "run-old") == (0, None)


@pytest.mark.asyncio
async def test_reviewer_invited_by_minh_sees_minh_runs_but_never_legacy_runs(env):
    """Run cũ không phải run của minh: Reviewer minh mời thấy và duyệt run thật của minh, còn run cũ (kể cả run hệ
    thống sinh từ run cũ, cảnh báo, pipeline cũ) chỉ Admin và cặp demo thấy. an duyệt run cũ không ghi thêm chủ."""
    from src.run_report.access import is_legacy, model_visible

    store_path = env.config.store_path
    run_a = get_run_report(store_path, "run-a")
    files = get_run_files(store_path, "run-a", run_a.version)
    minh_run = _with_model(run_a, "minh_model", run_id="run-minh", created_by="minh")
    old = _with_model(run_a, "old_model", run_id="run-old")
    old_sys = old.model_copy(
        update={"run_id": "run-old-sys", "created_by": "system", "trigger": "regression", "parent_run_id": "run-old"}
    )
    for report in (minh_run, old, old_sys):
        save_run_report(store_path, report)
        save_run_files(store_path, report.run_id, report.version, files)
    _strip_created_by(env, "run-old")
    assert is_legacy(store_path, "run-old") and is_legacy(store_path, "run-old-sys")
    assert not is_legacy(store_path, "run-minh")
    old_alert = alerts.raise_alert(store_path, "old_model", "needs_human", "Run cũ cần sửa", run_id="run-old-sys")
    owner_alert = alerts.raise_alert(store_path, "old_model", "source_stalled", "Nguồn cũ ngừng", owner=None)
    _deploy(env, "old_model", "eng.a", "run-old-dep")
    _strip_created_by(env, "run-old-dep")  # pipeline cũ đang deploy, không rõ chủ

    await _login(env, "engineer", "Engineer@123")
    token = await _invite(env)
    await _login(env, "rev.one")
    assert (await env.client.post("/api/v1/pipeline/invites/accept", json={"token": token})).status_code == 200
    rev_one = {"id": env.ids["rev.one"], "role": "Reviewer"}

    seen = await _run_ids(env)
    assert {READY, "run-minh"} <= seen, "run thật của minh"
    assert not seen & {"run-old", "run-old-sys", "run-old-dep", "c4b8f0d3-1e2a-4f5b-b7c9-6a0e3d2f1b50"}
    for run_id in ("run-old", "run-old-sys"):
        await _blocked_reads(env, run_id)
        await _blocked_reviews(env, run_id)
    assert (await env.client.get("/api/v1/runs/run-minh/compare", params={"base": "run-old"})).status_code == 404
    assert (await env.client.get("/api/v1/monitor/alerts")).json() == []
    for alert in (old_alert, owner_alert):
        assert (await env.client.post(f"/api/v1/monitor/alerts/{alert['id']}/resolve")).status_code == 404
    assert (await env.client.get("/api/v1/monitor/pipelines/old_model")).status_code == 404
    assert (await env.client.post("/api/v1/pipelines/old_model/optimize")).status_code == 404
    assert not model_visible(rev_one, store_path, "old_model")
    approved = await env.client.post("/api/v1/runs/run-minh/approve", json={"files_hash": env.files_hash})
    assert approved.status_code == 200, approved.text

    await _login(env, "reviewer", "Reviewer@123")
    assert {"run-old", "run-old-sys", "run-minh"} <= await _run_ids(env)
    alert_ids = {alert["id"] for alert in (await env.client.get("/api/v1/monitor/alerts")).json()}
    assert {old_alert["id"], owner_alert["id"]} <= alert_ids
    approved = await env.client.post("/api/v1/runs/run-old/approve", json={"files_hash": env.files_hash})
    assert approved.status_code == 200, approved.text
    assert approved.json()["created_by"] is None
    assert _stored_created_by(env, "run-old")[1] is None, "lưu lại run cũ không ghi thêm minh"
    assert is_legacy(store_path, "run-old")

    await _login(env, "rev.one")
    await _blocked_reads(env, "run-old")


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


async def _link(env, engineer: str, reviewer: str = "rev.one", password: str = PASSWORD) -> None:
    await _login(env, engineer, password)
    token = await _invite(env)
    await _login(env, reviewer)
    assert (await env.client.post("/api/v1/pipeline/invites/accept", json={"token": token})).status_code == 200


def _approved(env, run_id: str, model: str) -> RunReport:
    """Run của A đã được duyệt, chờ deploy, tạo `model`."""
    from src.run_report.models import Approval

    report = _with_model(get_run_report(env.config.store_path, "run-a"), model, run_id=run_id, status="APPROVED")
    approval = Approval(
        decision="approved",
        reviewer=env.ids["rev.one"],
        files_hash=report.files_hash,
        version=report.version,
        at=report.created_at,
        comment="",
    )
    report = RunReport.model_validate({**report.model_dump(), "approval": approval.model_dump()})
    save_run_report(env.config.store_path, report)
    files = get_run_files(env.config.store_path, "run-a", report.version)
    save_run_files(env.config.store_path, run_id, report.version, files)
    return report


@pytest.mark.asyncio
async def test_deploy_refuses_model_name_owned_by_another_user(env):
    from src.run_report.access import claim_model, model_owner

    store_path = env.config.store_path
    taken = _approved(env, "run-a-taken", "b_model")
    own = _approved(env, "run-a-own", "a_model")
    assert model_owner(store_path, "b_model") == env.ids["eng.b"] and model_owner(store_path, "a_model") is None
    assert claim_model(store_path, own) is None
    assert model_owner(store_path, "a_model") == env.ids["eng.a"]
    # Bản sửa do hệ thống tạo trên pipeline của B vẫn là của B, không bị coi là trùng tên.
    assert claim_model(store_path, get_run_report(store_path, "run-sys")) is None
    await _link(env, "eng.a")

    response = await env.client.post(f"/api/v1/runs/{taken.run_id}/deploy")

    assert response.status_code == 409
    assert response.json()["detail"] == "Tên pipeline b_model đã có người dùng khác dùng, hãy đổi tên model."
    assert get_run_report(store_path, taken.run_id).deployment is None


@pytest.mark.asyncio
async def test_model_name_is_claimed_before_github_and_only_one_owner_wins(env, monkeypatch):
    """Deploy giữ tên model trước khi mở pull request; deploy lỗi vẫn giữ tên; chủ khác tới sau nhận 409 mà không gọi
    GitHub."""
    from src.codegen.github import GithubError
    from src.run_report.access import claim_model, model_owner

    store_path = env.config.store_path
    env.config.deploy_mode = "pr"  # test này kiểm luồng pull request; mặc định giờ là direct
    opened = []

    def failing_github(report, files, github):
        opened.append((report.run_id, store.get_model_owner(store_path, "fresh_model")))
        raise GithubError(502, "GitHub đang lỗi")

    monkeypatch.setattr("src.codegen.deploy.open_pull_request", failing_github)
    mine = _approved(env, "run-a-fresh", "fresh_model")
    await _link(env, "eng.a")

    failed = await env.client.post(f"/api/v1/runs/{mine.run_id}/deploy")
    assert failed.status_code == 502
    assert opened == [(mine.run_id, env.ids["eng.a"])], "tên đã được giữ trước khi gọi GitHub"
    assert model_owner(store_path, "fresh_model") == env.ids["eng.a"]

    theirs = _with_model(get_run_report(store_path, "run-b"), "fresh_model", run_id="run-b-fresh")
    save_run_report(store_path, theirs)
    assert claim_model(store_path, theirs) == "fresh_model"
    assert claim_model(store_path, mine) is None, "chủ đã giữ tên deploy lại được"

    # B duyệt bằng Reviewer đã liên kết với B: 409, không mở pull request nào.
    approved_b = _approved(env, "run-b-approved", "fresh_model").model_copy(update={"created_by": env.ids["eng.b"]})
    save_run_report(store_path, approved_b)
    await _link(env, "eng.b")
    response = await env.client.post(f"/api/v1/runs/{approved_b.run_id}/deploy")
    assert response.status_code == 409, response.text
    assert "fresh_model" in response.json()["detail"]
    assert len(opened) == 1 and model_owner(store_path, "fresh_model") == env.ids["eng.a"]


def test_concurrent_model_claims_by_two_owners_have_one_winner(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from src.run_report import access

    store_path = env.config.store_path
    race_a = _with_model(get_run_report(store_path, "run-a"), "race_model", run_id="run-a-race")
    race_b = _with_model(get_run_report(store_path, "run-b"), "race_model", run_id="run-b-race")
    for report in (race_a, race_b):
        save_run_report(store_path, report)
    # Cả hai cùng qua bước đọc (chưa ai giữ tên, chưa ai deploy) rồi cùng ghi: chỉ khóa chính quyết định ai thắng.
    monkeypatch.setattr(access, "model_owners", lambda *args: [])
    barrier = Barrier(2)

    def claim(report: RunReport) -> str | None:
        barrier.wait()
        return access.claim_model(store_path, report)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [race_a, race_b]))

    assert results.count(None) == 1 and results.count("race_model") == 1
    winner = env.ids["eng.a"] if results[0] is None else env.ids["eng.b"]
    assert store.get_model_owner(store_path, "race_model") == winner
    with store.database(store_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_owners WHERE model_name = 'race_model'").fetchone()[0] == 1


def _legacy_approved(env, run_id: str, model: str) -> RunReport:
    """Run cũ đã duyệt, chờ deploy, bản lưu thiếu `created_by`."""
    _approved(env, run_id, model)
    _strip_created_by(env, run_id)
    return get_run_report(env.config.store_path, run_id)


def _fake_pull_request(opened: list[str], hold: Callable[[], None] = lambda: None):
    """Thay open_pull_request: ghi run đã mở pull request; lần mở đầu tiên gọi `hold` (đang chờ GitHub)."""
    from datetime import UTC, datetime

    from src.run_report.models import Deployment

    def open_pull_request(report, files, github):
        opened.append(report.run_id)
        if len(opened) == 1:
            hold()
        return Deployment(
            repo="org/pipelines",
            branch=f"atomesus/{report.run_id}",
            pr_number=len(opened),
            pr_url=f"https://github.com/org/pipelines/pull/{len(opened)}",
            head_sha="abc123",
            opened_at=datetime.now(UTC),
        )

    return open_pull_request


@pytest.mark.asyncio
async def test_legacy_run_without_created_by_deploys_and_keeps_deployment(env, monkeypatch):
    from src.run_report.access import claim_model, model_owner, model_visible

    store_path = env.config.store_path
    opened = []
    monkeypatch.setattr("src.codegen.deploy.open_pull_request", _fake_pull_request(opened))
    legacy = _legacy_approved(env, "run-old-approved", "old_model")
    assert legacy.created_by is None
    await _login(env, "reviewer", "Reviewer@123")

    response = await env.client.post(f"/api/v1/runs/{legacy.run_id}/deploy")

    assert response.status_code == 200, response.text
    assert response.json()["deployment"]["pr_number"] == 1 and opened == [legacy.run_id]
    stored = get_run_report(store_path, legacy.run_id)
    assert stored.deployment is not None and stored.deployment.pr_number == 1
    assert stored.created_by is None and _stored_created_by(env, legacy.run_id)[1] is None
    # Tên model giữ cho LEGACY_OWNER chỉ để chặn trùng tên; không cấp quyền xem pipeline cũ cho Reviewer minh mời.
    assert model_owner(store_path, "old_model") == LEGACY_OWNER == store.get_model_owner(store_path, "old_model")
    other = _with_model(get_run_report(store_path, "run-b"), "old_model", run_id="run-b-old")
    save_run_report(store_path, other)
    assert claim_model(store_path, other) == "old_model", "chủ khác không lấy được tên của pipeline cũ"
    await _link(env, "engineer", password="Engineer@123")
    assert model_visible({"id": "minh", "role": "Engineer"}, store_path, "old_model")
    assert not model_visible({"id": env.ids["rev.one"], "role": "Reviewer"}, store_path, "old_model")


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_first", [True, False], ids=["legacy-first", "owner-first"])
async def test_concurrent_deploys_of_legacy_and_owned_run_open_one_pull_request(env, monkeypatch, legacy_first):
    """Run cũ đang chờ GitHub thì run của chủ khác cùng tên model bị 409 (và ngược lại): chỉ một pull request."""
    import asyncio
    import threading

    from httpx import ASGITransport, AsyncClient

    from src.run_report.access import model_owner

    store_path = env.config.store_path
    legacy = _legacy_approved(env, "run-old-shared", "shared_model")
    owned = _approved(env, "run-a-shared", "shared_model")
    await _link(env, "eng.a")  # env.client: rev.one, đã liên kết với A
    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        entered.set()
        assert release.wait(10), "request thứ hai không xong"

    opened = []
    monkeypatch.setattr("src.codegen.deploy.open_pull_request", _fake_pull_request(opened, hold))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as demo:
        login = await demo.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        assert login.status_code == 200
        pairs = [(demo, legacy), (env.client, owned)]
        (first_client, first), (second_client, second) = pairs if legacy_first else pairs[::-1]
        pending = asyncio.create_task(first_client.post(f"/api/v1/runs/{first.run_id}/deploy"))
        assert await asyncio.to_thread(entered.wait, 10), "request đầu chưa tới bước mở pull request"
        try:
            loser = await second_client.post(f"/api/v1/runs/{second.run_id}/deploy")
        finally:
            release.set()
        winner = await pending

    assert winner.status_code == 200, winner.text
    assert loser.status_code == 409, loser.text
    assert loser.json()["detail"] == "Tên pipeline shared_model đã có người dùng khác dùng, hãy đổi tên model."
    assert opened == [first.run_id], "chỉ một pull request được mở"
    assert get_run_report(store_path, first.run_id).deployment is not None
    assert get_run_report(store_path, second.run_id).deployment is None
    assert model_owner(store_path, "shared_model") == (LEGACY_OWNER if legacy_first else env.ids["eng.a"])


@pytest.mark.asyncio
async def test_batch_review_reports_each_item_for_mixed_visibility(env):
    await _login(env, "eng.a")
    items = [{"run_id": "run-a", "files_hash": env.files_hash}, {"run_id": "run-b", "files_hash": env.files_hash}]

    mixed = await env.client.post("/api/v1/runs/batch-review", json={"decision": "approve", "items": items})

    assert mixed.status_code == 200, mixed.text
    assert mixed.json()["results"] == [
        {"run_id": "run-a", "ok": False, "status": None, "message": "Chỉ Reviewer được duyệt hoặc từ chối run."},
        {"run_id": "run-b", "ok": False, "status": None, "message": "Không tìm thấy run run-b"},
    ]
    own_only = await env.client.post("/api/v1/runs/batch-review", json={"decision": "approve", "items": items[:1]})
    assert own_only.status_code == 403
    for run_id in ("run-a", "run-b"):
        assert get_run_report(env.config.store_path, run_id).status == "READY_FOR_REVIEW"


CONNECTION_B = "87654321-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DOMAIN_B, SOURCE_B = "u_87654321", "c_87654321"


def _private_source(env, monkeypatch) -> list[str]:
    """Kết nối Postgres riêng của B đã chọn bảng; trả danh sách connection_ref đã bị kết nối tới để khám phá."""
    owner = env.ids["eng.b"]
    metadata = {
        "id": CONNECTION_B,
        "owner_id": owner,
        "name": "Kho riêng B",
        "host": "db-rieng-b.example",
        "port": 5432,
        "database": "shop",
        "user": "b_user",
        "sslmode": "require",
        "created_at": 1,
    }
    store.save_connection(env.config.store_path, metadata, "mat-khau-ma-hoa")
    store.save_connection_selection(env.config.store_path, CONNECTION_B, owner, "b_private", ["orders"])
    discovered = []

    def discover(decl, config):
        discovered.append(decl.connection_ref)
        columns = [{"name": "id", "type": "integer"}]
        orders = {"name": "orders", "kind": "table", "columns": columns, "primary_key": ["id"], "row_estimate": 1}
        return {"objects": [orders], "foreign_keys": []}

    monkeypatch.setattr(connectors, "discover", discover)
    return discovered


async def _sees_private_source(env) -> bool:
    """Catalog, danh sách nguồn, khám phá bảng và /policy phải cùng thấy hoặc cùng không thấy nguồn riêng của B."""
    catalog = await env.client.get("/api/v1/catalog")
    sources = await env.client.get("/api/v1/catalog/sources")
    objects = await env.client.get(f"/api/v1/catalog/sources/{SOURCE_B}/objects")
    policy = await env.client.get("/api/v1/policy")
    seen = {
        DOMAIN_B in catalog.json()["domains"],
        SOURCE_B in {source["ref"] for source in sources.json()},
        objects.status_code == 200,
        DOMAIN_B in {domain["name"] for domain in policy.json()["user_domains"]},
    }
    assert len(seen) == 1, seen
    if seen == {False}:
        assert objects.status_code == 404
        for response in (catalog, sources, policy):
            assert "b_private" not in response.text and CONNECTION_B not in response.text
            assert "eng.b@example.com" not in response.text
    return seen.pop()


@pytest.mark.asyncio
async def test_reviewer_sees_private_sources_only_of_linked_engineers(env, monkeypatch):
    discovered = _private_source(env, monkeypatch)
    conn_ref = f"conn:{CONNECTION_B}"

    for username in ("rev.one", "eng.a"):
        await _login(env, username)
        assert not await _sees_private_source(env), username
    assert conn_ref not in discovered, "không kết nối tới Postgres riêng của B cho người không được thấy"

    await _link(env, "eng.b")
    assert await _sees_private_source(env)
    assert conn_ref in discovered

    await _login(env, "eng.b")
    assert (await env.client.delete(f"/api/v1/pipeline/reviewers/{env.ids['rev.one']}")).status_code == 200
    await _login(env, "rev.one")
    discovered.clear()
    assert not await _sees_private_source(env)
    assert conn_ref not in discovered

    for username, password in (("eng.b", PASSWORD), ("admin", "Admin@123")):
        await _login(env, username, password)
        assert await _sees_private_source(env), username


def test_request_changes_agent_gets_only_the_run_owners_domains(env, monkeypatch):
    """Reviewer yêu cầu sửa run của A: agent không nhận domain riêng của B dù Reviewer liên kết với cả hai."""
    _private_source(env, monkeypatch)
    monkeypatch.setattr("src.agents.llm.openai_llm", lambda: lambda *_: pytest.fail("Không được gọi LLM thật"))

    def domains(run_id: str | None) -> set[str]:
        return set(request_change_deps(env.config, run_id).policy.domains.domains)

    assert DOMAIN_B not in domains("run-a")
    assert DOMAIN_B in domains("run-b") and DOMAIN_B in domains("run-sys")
    assert DOMAIN_B not in domains("khong-co-run")
    assert DOMAIN_B in domains(None), "vòng sửa nội bộ (P16 → P4) giữ chính sách hệ thống đầy đủ"


def _measure(env, model: str, run_id: str) -> None:
    """Một lần chạy theo lịch đã thu số đo của `model` (bảng pipeline_runs của monitor)."""
    row = {column: None for column in runs.COLUMNS}
    row.update(
        run_id=run_id,
        model=model,
        status="SUCCESS",
        started_at="2026-10-01T02:00:00+00:00",
        source_records='{"orders": 1234}',
        collected_at="2026-10-01T02:05:00+00:00",
    )
    with runs._database(env.config.store_path) as conn:
        conn.execute(f"INSERT INTO pipeline_runs VALUES ({', '.join('?' for _ in runs.COLUMNS)})", list(row.values()))


async def _model_data(env, model: str) -> tuple[int, int]:
    """(status lịch sử chạy, status đối soát bảng gold) của `model` với người đang đăng nhập."""
    series = await env.client.get(f"/api/v1/monitor/pipelines/{model}")
    reconcile = await env.client.post(f"/api/v1/catalog/datasets/sales/{model}/reconcile")
    return series.status_code, reconcile.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("a_first", [False, True], ids=["b-old-a-current", "a-old-b-current"])
async def test_history_and_gold_table_of_model_owned_by_others_stay_hidden(env, a_first):
    """Tên model là không gian tên chung: A không nhận lịch sử chạy, số đo hay bảng gold của B, kể cả khi dữ liệu cũ
    có A deploy trùng tên (trước hay sau B). Tên lẫn hai chủ thì chỉ Admin thấy."""
    store_path = env.config.store_path
    full_spec = get_run_report(store_path, "run-b").spec  # đủ Spec để thành bản đang chạy (live_pipelines)
    save_run_report(store_path, get_run_report(store_path, "run-b-dep").model_copy(update={"spec": full_spec}))
    _measure(env, "b_model", "dg-b")

    await _login(env, "eng.a")
    assert await _model_data(env, "b_model") == (404, 404)
    await _login(env, "eng.b")
    assert (await env.client.get("/api/v1/monitor/pipelines/b_model")).json()["runs"][0]["run_id"] == "dg-b"
    assert (await env.client.post("/api/v1/catalog/datasets/sales/b_model/reconcile")).json()["run_id"] == "run-b-dep"

    report = _deploy(env, "b_model", "eng.a", "run-a-dep", days_later=-1 if a_first else 1)
    save_run_report(store_path, report.model_copy(update={"spec": full_spec}))
    for username in ("eng.a", "eng.b"):
        await _login(env, username)
        assert await _model_data(env, "b_model") == (404, 404), username
        assert (await env.client.get("/api/v1/monitor/pipelines")).json() == []
    await _login(env, "admin", "Admin@123")
    assert (await env.client.get("/api/v1/monitor/pipelines/b_model")).status_code == 200


@pytest.mark.asyncio
async def test_owner_cannot_review_own_system_runs_after_becoming_reviewer(env):
    """Run hệ thống tạo (`system`) thuộc chủ pipeline gốc: B đổi sang vai Reviewer vẫn không tự duyệt được."""
    await _login(env, "admin", "Admin@123")
    changed = await env.client.patch(f"/api/v1/pipeline/users/{env.ids['eng.b']}/role", json={"role": "Reviewer"})
    assert changed.status_code == 200, changed.text

    await _login(env, "eng.b")
    client, review = env.client, {"files_hash": env.files_hash, "comment": "ok"}
    assert (await client.get("/api/v1/runs/run-sys")).status_code == 200
    for action in ("approve", "reject"):
        response = await client.post(f"/api/v1/runs/run-sys/{action}", json=review)
        assert response.status_code == 403 and "tự duyệt" in response.json()["detail"], action
    changes = await client.post("/api/v1/runs/run-sys/request-changes", json={"feedback": "Sửa cột"})
    assert changes.status_code == 403
    batch = await client.post(
        "/api/v1/runs/batch-review",
        json={"decision": "approve", "items": [{"run_id": "run-sys", "files_hash": env.files_hash}]},
    )
    assert batch.json()["results"] == [
        {"run_id": "run-sys", "ok": False, "status": None, "message": "Không được tự duyệt run của mình."}
    ]
    assert get_run_report(env.config.store_path, "run-sys").status == "READY_FOR_REVIEW"


def _system_chain(env, root: str, length: int, prefix: str) -> str:
    """`length` run hệ thống nối nhau từ `root` (run sửa của run sửa...), không ghi chủ; trả run cuối, chờ duyệt."""
    store_path = env.config.store_path
    run_a = get_run_report(store_path, "run-a")
    parent = root
    for index in range(length):
        report = run_a.model_copy(
            update={
                "run_id": f"{prefix}-{index}",
                "created_by": "system",
                "trigger": "regression",
                "parent_run_id": parent,
            }
        )
        save_run_report(store_path, report)
        parent = report.run_id
    save_run_files(store_path, parent, run_a.version, get_run_files(store_path, "run-a", run_a.version))
    return parent


@pytest.mark.asyncio
async def test_long_system_chain_still_belongs_to_root_owner(env):
    """Hơn 100 run sửa nối nhau, chưa ghi chủ: vẫn lần tới run gốc của A, không thành run cũ cho cặp demo thấy."""
    from src.run_report.access import is_legacy, run_owner, visible_run_ids

    store_path = env.config.store_path
    leaf = _system_chain(env, "run-a", 120, "run-chain")
    assert run_owner(store_path, leaf) == env.ids["eng.a"] and not is_legacy(store_path, leaf)
    assert leaf in visible_run_ids({"id": env.ids["eng.a"], "role": "Engineer"}, store_path)

    for username, password in (("engineer", "Engineer@123"), ("reviewer", "Reviewer@123"), ("rev.one", PASSWORD)):
        await _login(env, username, password)
        assert leaf not in await _run_ids(env), username
        await _blocked_reads(env, leaf)
        await _blocked_reviews(env, leaf)  # an và rev.one chưa liên kết với A
    assert get_run_report(store_path, leaf).status == "READY_FOR_REVIEW"

    await _login(env, "eng.a")
    assert leaf in await _run_ids(env)
    assert (await env.client.get(f"/api/v1/runs/{leaf}")).status_code == 200
    await _link(env, "eng.a")
    assert leaf in await _run_ids(env)
    approved = await env.client.post(f"/api/v1/runs/{leaf}/approve", json={"files_hash": env.files_hash})
    assert approved.status_code == 200, approved.text


@pytest.mark.asyncio
async def test_system_run_without_reachable_root_is_admin_only_and_never_reviewable(env):
    """Run hệ thống mất run gốc (run cha không còn, thiếu parent_run_id, trỏ vòng) không phải run cũ: cặp demo không
    thấy, chỉ Admin thấy; không ai duyệt hay deploy được, kể cả giữ tên model."""
    from src.run_report.access import UNKNOWN, claim_model, is_legacy, run_owner, visible_run_ids

    store_path = env.config.store_path
    run_a = get_run_report(store_path, "run-a")
    files = get_run_files(store_path, "run-a", run_a.version)
    parents = {"run-orphan": "run-gone", "run-rootless": None, "run-loop-1": "run-loop-2", "run-loop-2": "run-loop-1"}
    for run_id, parent in parents.items():
        report = _with_model(
            run_a, "lost_model", run_id=run_id, created_by="system", trigger="regression", parent_run_id=parent
        )
        save_run_report(store_path, report)
        save_run_files(store_path, run_id, report.version, files)
    lost = {*parents, _system_chain(env, "run-gone-root", 120, "run-lost")}
    for run_id in lost:
        report = get_run_report(store_path, run_id)
        assert run_owner(store_path, run_id) == UNKNOWN and not is_legacy(store_path, run_id), run_id
        model = report.spec["transform"]["model_name"]
        assert claim_model(store_path, report) == model and store.get_model_owner(store_path, model) is None
    owner = day2.pipeline_owner(store_path, get_run_report(store_path, "run-orphan"))
    alert = alerts.raise_alert(store_path, "lost_model", "source_stalled", "Nguồn mất chủ", owner=owner)
    assert alert["owner"] == UNKNOWN

    for username, password in (
        ("engineer", "Engineer@123"),
        ("reviewer", "Reviewer@123"),
        ("eng.a", PASSWORD),
        ("rev.one", PASSWORD),
    ):
        await _login(env, username, password)
        assert not await _run_ids(env) & lost, username
        for run_id in lost:
            await _blocked_reads(env, run_id)
            await _blocked_reviews(env, run_id)
        assert alert["id"] not in {item["id"] for item in (await env.client.get("/api/v1/monitor/alerts")).json()}
        assert (await env.client.post(f"/api/v1/monitor/alerts/{alert['id']}/resolve")).status_code == 404

    await _login(env, "admin", "Admin@123")
    assert visible_run_ids({"id": "admin", "role": "Admin"}, store_path) is None  # danh sách không lọc
    assert alert["id"] in {item["id"] for item in (await env.client.get("/api/v1/monitor/alerts")).json()}
    for run_id in lost:
        assert (await env.client.get(f"/api/v1/runs/{run_id}")).status_code == 200
        approve = await env.client.post(f"/api/v1/runs/{run_id}/approve", json={"files_hash": env.files_hash})
        assert approve.status_code == 403
        assert (await env.client.post(f"/api/v1/runs/{run_id}/deploy")).status_code == 403
        assert get_run_report(store_path, run_id).status == "READY_FOR_REVIEW"


def test_day2_fix_records_its_owner_at_creation(env, monkeypatch):
    """Run sửa Day 2 ghi sẵn chủ pipeline lúc tạo: đọc thẳng, không phụ thuộc chuỗi run gốc còn hay không."""
    from src.run_report.access import UNKNOWN, run_owner, visible_run_ids

    store_path = env.config.store_path

    def start_regression(parent, evidence, deps, path):
        update = {"run_id": "run-fix", "created_by": "system", "trigger": "regression", "parent_run_id": parent.run_id}
        started = parent.model_copy(update=update)
        save_run_report(path, started)
        return started, lambda: started

    monkeypatch.setattr(day2, "start_regression", start_regression)
    submitted = []
    started = day2.launcher(None, store_path, submitted.append)(get_run_report(store_path, "run-b-dep"), {})
    assert started.run_id == "run-fix" and len(submitted) == 1
    assert store.get_run_owner(store_path, "run-fix") == env.ids["eng.b"]

    with sqlite3.connect(store_path) as conn:
        conn.execute("DELETE FROM run_reports WHERE run_id = 'run-b-dep'")
    assert run_owner(store_path, "run-fix") == env.ids["eng.b"]
    assert run_owner(store_path, "run-sys") == UNKNOWN, "run sửa cũ không ghi chủ, mất run gốc thì đóng hẳn"
    visible = visible_run_ids({"id": env.ids["eng.b"], "role": "Engineer"}, store_path)
    assert "run-fix" in visible and "run-sys" not in visible
