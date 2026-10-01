"""Actual DuckDB staging and API contracts, with a stub only at the source boundary."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.pipeline import api, auth, postgres, store
from src.pipeline.cleaning import clean_rows
from src.pipeline.models import ColumnRule, RunRequest
from src.pipeline.settings import PipelineSettings
from src.run_report import RunReport, get_run_report
from src.run_report import router as run_reports_router

COLUMNS = [
    {"from": "order_id", "as": "id", "type": "integer"},
    {"from": "amount_text", "as": "amount", "type": "decimal(18,2)"},
    {"from": "ordered_at_text", "as": "ordered_at", "type": "date"},
]
BODY = {"title": "Clean orders", "table": "sales_raw.orders", "columns": COLUMNS}
ROWS = [{"order_id": "1", "amount_text": "1234.50", "ordered_at_text": "2026-01-01"}]


def rules():
    return [ColumnRule.model_validate(column) for column in COLUMNS]


def test_cleaning_uses_actual_duckdb_and_preserves_precision():
    result = clean_rows(ROWS, rules())
    assert result["output"] == [{"id": "1", "amount": "1234.50", "ordered_at": "2026-01-01"}]
    assert result["output_schema"] == [
        {"name": "id", "type": "integer"},
        {"name": "amount", "type": "decimal(18,2)"},
        {"name": "ordered_at", "type": "date"},
    ]
    assert clean_rows(ROWS, rules()) == result


def test_rejected_rows_are_accounted_for_and_nulls_are_preserved():
    rows = ROWS + [
        {"order_id": "2", "amount_text": "bad", "ordered_at_text": "2026-02-31"},
        {"order_id": "3", "amount_text": None, "ordered_at_text": None},
    ]
    result = clean_rows(rows, rules())
    assert result["rows_read"] == result["rows_written"] + result["rows_rejected"] == 3
    assert result["rejected"][0]["columns"] == ["amount", "ordered_at"]
    assert result["rejected"][0]["values"]["amount_text"] == "bad"
    assert result["output"][1] == {"id": "3", "amount": None, "ordered_at": None}
    assert rows[1]["amount_text"] == "bad"


def test_empty_input_and_sql_like_values():
    assert clean_rows([], rules())["rows_written"] == 0
    value = "'); DROP TABLE bronze; --"
    assert clean_rows(
        [{"label": value}], [ColumnRule.model_validate({"from": "label", "as": "label", "type": "varchar"})]
    )["output"] == [{"label": value}]


def test_sandbox_deleted_on_success_and_failure(tmp_path, monkeypatch):
    import tempfile

    import src.pipeline.cleaning as cleaning

    locations = []

    def directory(**kwargs):
        result = tempfile.TemporaryDirectory(dir=tmp_path, **kwargs)
        locations.append(Path(result.name))
        return result

    monkeypatch.setattr(cleaning, "TemporaryDirectory", directory)
    clean_rows(ROWS, rules())
    with pytest.raises(KeyError):
        clean_rows([{}], rules())
    assert len(locations) == 2
    assert all(not path.exists() for path in locations)


@pytest.mark.parametrize(
    "change",
    [
        {"table": "secret.users"},
        {"title": "  "},
        {"columns": []},
        {"created_by": "an"},
        {"columns": [COLUMNS[0], COLUMNS[0]]},
        {"columns": [{"from": "order_id", "as": "id;drop", "type": "integer"}]},
        {"columns": [{"from": "order_id", "as": "id", "type": "integer);DROP TABLE x"}]},
    ],
)
def test_spec_validation(change):
    with pytest.raises(ValidationError):
        RunRequest.model_validate({**BODY, **change})


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.include_router(run_reports_router, prefix="/api/v1")
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    monkeypatch.setattr(
        postgres, "source_schema", lambda settings: [{"name": column["from"], "type": "text"} for column in COLUMNS]
    )
    monkeypatch.setattr(postgres, "read_rows", lambda settings, columns: ROWS)
    with TestClient(app) as browser:
        yield browser, config


def login(browser, user="minh"):
    response = browser.post("/api/v1/pipeline/session", json={"user_id": user})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Path=/api/v1;" in response.headers["set-cookie"]
    return response


def staging(report):
    return next(check for check in report["checks"] if check["name"] == "staging_reconcile")


def test_api_login_execute_history_and_persistence(client):
    browser, config = client
    assert browser.get("/api/v1/runs").status_code == 401
    login(browser)
    assert browser.get("/api/v1/pipeline/source").status_code == 200
    response = browser.post("/api/v1/pipeline/runs", json=BODY)
    assert response.status_code == 201
    report = response.json()
    RunReport.model_validate(report)
    assert report["status"] == "READY_FOR_REVIEW" and report["stage_reached"] == "report"
    assert report["created_by"] == "minh" and report["approval"] is None
    assert report["user_request"] == BODY["title"] and report["spec"] == BODY
    assert report["gates"][0]["name"] == "schema_real" and report["gates"][0]["result"] == "pass"
    observed = staging(report)["observed"]
    assert staging(report)["source_id"] == "sales_raw.orders" and staging(report)["result"] == "pass"
    assert observed["bronze"] == observed["staging"] == 1 and observed["rejected"] == 0
    assert observed["output_samples"] == [{"id": "1", "amount": "1234.50", "ordered_at": "2026-01-01"}]
    assert browser.get(f"/api/v1/runs/{report['run_id']}").json() == report
    history = browser.get("/api/v1/runs").json()
    assert [run["run_id"] for run in history] == [report["run_id"]]
    assert get_run_report(config.store_path, report["run_id"]).model_dump(mode="json") == report
    assert browser.get("/api/v1/runs/missing").status_code == 404
    browser.delete("/api/v1/pipeline/session")
    assert browser.get("/api/v1/pipeline/session").status_code == 401
    assert browser.get("/api/v1/runs").status_code == 401


def test_rejected_rows_need_human_with_samples(client, monkeypatch):
    browser, _ = client
    login(browser)
    monkeypatch.setattr(postgres, "read_rows", lambda settings, columns: ROWS + [{**ROWS[0], "amount_text": "bad"}])
    report = browser.post("/api/v1/pipeline/runs", json=BODY).json()
    assert report["status"] == "NEEDS_HUMAN" and staging(report)["result"] == "fail"
    observed = staging(report)["observed"]
    assert observed["rejected"] == 1 and observed["reject_pct"] == 50.0
    assert observed["reject_reasons"] == {"cast": 1}
    assert observed["reject_samples"][0]["values"]["amount_text"] == "bad"
    assert browser.get("/api/v1/runs", params={"status": "NEEDS_HUMAN"}).json()[0]["run_id"] == report["run_id"]
    assert browser.get("/api/v1/runs", params={"status": "READY_FOR_REVIEW"}).json() == []


def test_backend_enforces_role_and_rejects_spoofed_creator(client):
    browser, _ = client
    login(browser, "an")
    assert browser.get("/api/v1/runs").status_code == 200
    assert browser.post("/api/v1/pipeline/runs", json=BODY).status_code == 403
    login(browser)
    assert browser.post("/api/v1/pipeline/runs", json={**BODY, "created_by": "an"}).status_code == 422


def test_failed_source_saved_without_fabricated_success(client, monkeypatch):
    browser, _ = client
    login(browser)

    def unavailable(config):
        raise postgres.SourceUnavailableError("Database offline")

    monkeypatch.setattr(postgres, "source_schema", unavailable)
    assert browser.get("/api/v1/pipeline/source").status_code == 503
    report = browser.post("/api/v1/pipeline/runs", json=BODY).json()
    assert report["status"] == "INFRA_ERROR" and report["stage_reached"] == "el_run"
    assert report["error_trace"] == [{"stage": "el_run", "source_id": "sales_raw.orders", "error": "Database offline"}]
    assert report["checks"] == []
    assert browser.get("/api/v1/runs").json()[0]["status"] == "INFRA_ERROR"


def test_expired_session_and_production_login(client, monkeypatch):
    browser, config = client
    login(browser)
    with store.database(config.store_path) as conn:
        conn.execute("UPDATE sessions SET expires = 0")
    assert browser.get("/api/v1/runs").status_code == 401
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="production"))
    assert browser.post("/api/v1/pipeline/session", json={"user_id": "minh"}).status_code == 403


def test_invalid_source_column_and_empty_source_need_attention(client, monkeypatch):
    browser, _ = client
    login(browser)
    body = {**BODY, "columns": [{"from": "missing", "as": "missing", "type": "varchar"}]}
    report = browser.post("/api/v1/pipeline/runs", json=body).json()
    assert report["status"] == "NEEDS_HUMAN" and report["stage_reached"] == "static"
    assert report["gates"][0]["result"] == "fail"
    assert report["gates"][0]["observed"]["missing_columns"] == ["missing"]
    monkeypatch.setattr(postgres, "read_rows", lambda settings, columns: [])
    report = browser.post("/api/v1/pipeline/runs", json=BODY).json()
    assert report["status"] == "NEEDS_HUMAN" and staging(report)["observed"]["bronze"] == 0


def test_database_error_does_not_expose_credentials(monkeypatch):
    import psycopg

    def fail(*args, **kwargs):
        raise psycopg.OperationalError("secret-password")

    monkeypatch.setattr(psycopg, "connect", fail)
    config = PipelineSettings(_env_file=None, database_url="postgresql://user:secret-password@localhost/test")
    with pytest.raises(postgres.SourceUnavailableError) as error:
        postgres.source_schema(config)
    assert "secret-password" not in str(error.value)
