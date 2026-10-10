import subprocess
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.main import app
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.run_report import FIXTURES_DIR, RunReport, list_run_reports, save_run_report

READY_RUN_ID = "3f6c1a2e-8b4d-4c7a-9e21-0d5b7a1c9f10"


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """Sổ bằng chứng SQLite tạm, nạp sẵn các RunReport mẫu trong fixtures/run_reports."""
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    for path in FIXTURES_DIR.glob("*.json"):
        save_run_report(config.store_path, RunReport.model_validate_json(path.read_text(encoding="utf-8")))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    yield config.store_path
    app.dependency_overrides.pop(auth.settings, None)


@pytest_asyncio.fixture
async def logged_in(client, store_path):
    response = await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
    assert response.status_code == 200
    return client


@pytest.mark.asyncio
async def test_runs_require_session(client, store_path):
    assert (await client.get("/api/v1/runs")).status_code == 401
    assert (await client.get(f"/api/v1/runs/{READY_RUN_ID}")).status_code == 401


@pytest.mark.asyncio
async def test_list_runs_returns_newest_first(logged_in):
    response = await logged_in.get("/api/v1/runs")

    assert response.status_code == 200
    created = [RunReport.model_validate(r).created_at for r in response.json()]
    assert len(created) == 3
    assert created == sorted(created, reverse=True)


@pytest.mark.asyncio
async def test_list_runs_filters_by_status(logged_in):
    response = await logged_in.get("/api/v1/runs", params={"status": "DEPLOYED"})

    assert response.status_code == 200
    runs = response.json()
    assert [r["status"] for r in runs] == ["DEPLOYED"]
    assert runs[0]["approval"]["reviewer"] == "an"


@pytest.mark.asyncio
async def test_list_runs_rejects_unknown_status(logged_in):
    response = await logged_in.get("/api/v1/runs", params={"status": "DONE"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_run_returns_full_report(logged_in):
    response = await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")

    assert response.status_code == 200
    report = response.json()
    assert report["run_id"] == READY_RUN_ID
    assert report["created_by"] == "minh"
    assert report["version"] == 3
    assert report["approval"] is None
    for field in ("gates", "checks", "cost", "timing"):
        assert field in report


@pytest.mark.asyncio
async def test_get_run_unknown_id_returns_404(logged_in):
    response = await logged_in.get("/api/v1/runs/does-not-exist")

    assert response.status_code == 404


@pytest.fixture
def generated_run(store_path, tmp_path):
    """Run chờ duyệt có bộ file sinh thật từ spec mẫu, như run do agent tạo."""
    from src.codegen.render import file_hashes, render
    from src.policy.loader import load_policy
    from src.run_report.store import get_run_report, save_run_files
    from src.spec import SPEC_FIXTURES_DIR, Spec

    spec = Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    files = render(spec, load_policy())
    hashes, files_hash = file_hashes(files)
    ready = get_run_report(store_path, READY_RUN_ID)
    report = ready.model_copy(
        update={"spec": spec.model_dump(by_alias=True, mode="json"), "files": hashes, "files_hash": files_hash}
    )
    save_run_report(store_path, report)
    save_run_files(store_path, READY_RUN_ID, report.version, files)
    app.dependency_overrides[auth.settings]().deploy_dir = str(tmp_path / "dagster_local")
    return report


@pytest.mark.asyncio
async def test_approve_deploys_approved_files_to_local_dagster(logged_in, generated_run, tmp_path):
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash, "comment": "ok"}
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "DEPLOYED"
    assert report["approval"]["reviewer"] == "an"
    assert report["approval"]["files_hash"] == generated_run.files_hash
    project = tmp_path / "dagster_local" / "fct_daily_orders"
    assert (project / "infra/dagster/generated/fct_daily_orders_asset.py").is_file()
    assert (project / "dbt_project/models/gold/fct_daily_orders.sql").is_file()
    assert (project / "dbt_project/dbt_project.yml").is_file()


@pytest.mark.asyncio
async def test_deploy_timeout_is_recorded_as_deploy_failed(logged_in, generated_run, monkeypatch):
    from src.codegen import deploy

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("dbt parse", 120)

    monkeypatch.setattr(deploy, "deploy_local", timeout)
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "DEPLOY_FAILED"


@pytest.mark.asyncio
async def test_engineer_cannot_approve(client, store_path, generated_run):
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    response = await client.post(f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_approve_rejects_stale_files_hash(logged_in, generated_run, tmp_path):
    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": "sha256:old"})

    assert response.status_code == 409
    assert (await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")).json()["status"] == "READY_FOR_REVIEW"
    assert not (tmp_path / "dagster_local").exists()


@pytest.mark.asyncio
async def test_reject_records_decision_without_deploy(logged_in, generated_run, tmp_path):
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/reject", json={"files_hash": generated_run.files_hash, "comment": "sai metric"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert response.json()["approval"]["decision"] == "rejected"
    assert not (tmp_path / "dagster_local").exists()
    second = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_batch_review_processes_each_run_and_reports_partial_failure(logged_in, generated_run):
    response = await logged_in.post(
        "/api/v1/runs/batch-review",
        json={
            "decision": "reject",
            "comment": "Sai định nghĩa metric",
            "items": [
                {"run_id": READY_RUN_ID, "files_hash": generated_run.files_hash},
                {"run_id": "missing-run", "files_hash": "sha256:missing"},
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["results"] == [
        {"run_id": READY_RUN_ID, "ok": True, "status": "REJECTED", "message": "Đã từ chối"},
        {"run_id": "missing-run", "ok": False, "status": None, "message": "Không tìm thấy run missing-run"},
    ]


@pytest.mark.asyncio
async def test_batch_review_validates_duplicates_and_role(client, store_path, generated_run, logged_in):
    item = {"run_id": READY_RUN_ID, "files_hash": generated_run.files_hash}
    duplicate = await logged_in.post(
        "/api/v1/runs/batch-review",
        json={"decision": "approve", "comment": "", "items": [item, item]},
    )
    assert duplicate.status_code == 422

    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    forbidden = await client.post(
        "/api/v1/runs/batch-review",
        json={"decision": "approve", "comment": "", "items": [item]},
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_can_request_changes_and_restart_from_spec(logged_in, generated_run):
    from src.run_report.router import request_change_deps
    from src.run_report.store import get_run_report
    from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps

    llm = FakeLLM(SPEC)
    store_path = app.dependency_overrides[auth.settings]().store_path
    save_run_report(store_path, generated_run.model_copy(update={"plan": PLAN}))
    app.dependency_overrides[request_change_deps] = lambda: deps(llm)
    try:
        response = await logged_in.post(
            f"/api/v1/runs/{READY_RUN_ID}/request-changes",
            json={
                "files_hash": generated_run.files_hash,
                "version": generated_run.version,
                "feedback": "  Giữ metric, sửa lịch chạy  ",
            },
        )
    finally:
        app.dependency_overrides.pop(request_change_deps, None)

    assert response.status_code == 200, response.text
    assert response.json()["version"] == generated_run.version + 1
    assert response.json()["status"] == "GENERATING"
    revised = get_run_report(store_path, READY_RUN_ID)
    assert revised.status == "READY_FOR_REVIEW", revised.error_trace
    assert revised.review_history[-1].decision == "changes_requested"
    assert revised.review_history[-1].comment == "Giữ metric, sửa lịch chạy"
    assert "Giữ metric, sửa lịch chạy" in llm.calls[0]["user"]["yêu_cầu_thay_đổi"]


@pytest.mark.asyncio
async def test_request_changes_rejects_blank_feedback(logged_in, generated_run):
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/request-changes",
        json={"files_hash": generated_run.files_hash, "version": generated_run.version, "feedback": "   "},
    )

    assert response.status_code == 422


def test_save_overwrites_same_run_and_survives_reopen(store_path):
    ready = next(r for r in list_run_reports(store_path) if r.run_id == READY_RUN_ID)
    save_run_report(store_path, ready.model_copy(update={"status": "NEEDS_HUMAN"}))

    runs = list_run_reports(store_path)
    assert len(runs) == 3
    assert [r.status for r in runs if r.run_id == READY_RUN_ID] == ["NEEDS_HUMAN"]
    assert [r.run_id for r in list_run_reports(store_path, "READY_FOR_REVIEW")] == []


def test_cancelled_run_cannot_be_overwritten_by_late_background_save(store_path):
    from src.run_report.store import get_run_report

    ready = next(r for r in list_run_reports(store_path) if r.run_id == READY_RUN_ID)
    running = ready.model_copy(update={"status": "GENERATING", "approval": None})
    save_run_report(store_path, running)
    cancelled = running.model_copy(update={"status": "CANCELLED"})
    save_run_report(store_path, cancelled)
    save_run_report(store_path, ready)  # job nền hoàn tất muộn

    assert get_run_report(store_path, READY_RUN_ID).status == "CANCELLED"


@pytest.mark.asyncio
async def test_owner_can_cancel_running_run_but_not_reviewable_run(client, store_path):
    from src.run_report.store import get_run_report

    ready = get_run_report(store_path, READY_RUN_ID)
    save_run_report(store_path, ready.model_copy(update={"status": "GENERATING", "approval": None}))
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})

    response = await client.post(f"/api/v1/runs/{READY_RUN_ID}/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["timeline"][-1]["event"] == "cancelled"
    assert (await client.post(f"/api/v1/runs/{READY_RUN_ID}/cancel")).status_code == 409
