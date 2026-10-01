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
    response = await client.post("/api/v1/pipeline/session", json={"user_id": "an"})
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


def test_save_overwrites_same_run_and_survives_reopen(store_path):
    ready = next(r for r in list_run_reports(store_path) if r.run_id == READY_RUN_ID)
    save_run_report(store_path, ready.model_copy(update={"status": "NEEDS_HUMAN"}))

    runs = list_run_reports(store_path)
    assert len(runs) == 3
    assert [r.status for r in runs if r.run_id == READY_RUN_ID] == ["NEEDS_HUMAN"]
    assert [r.run_id for r in list_run_reports(store_path, "READY_FOR_REVIEW")] == []
