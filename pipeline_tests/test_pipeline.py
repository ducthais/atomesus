"""Actual DuckDB staging and API contracts, with a stub only at the source boundary."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.pipeline import api, postgres, store
from src.pipeline.cleaning import clean_rows
from src.pipeline.models import ColumnRule, RunRequest
from src.pipeline.settings import PipelineSettings

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
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[api.settings] = lambda: config
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(app_env="test"))
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
    return response


def test_api_login_execute_history_and_persistence(client):
    browser, config = client
    assert browser.get("/api/v1/pipeline/runs").status_code == 401
    login(browser)
    assert browser.get("/api/v1/pipeline/source").status_code == 200
    response = browser.post("/api/v1/pipeline/runs", json=BODY)
    assert response.status_code == 201
    report = response.json()
    assert report["status"] == "SUCCEEDED"
    assert report["created_by"] == "minh" and report["approval"] is None
    assert report["rows_read"] == report["rows_written"] == 1
    assert browser.get(f"/api/v1/pipeline/runs/{report['run_id']}").json() == report
    history = browser.get("/api/v1/pipeline/runs").json()
    assert history[0]["run_id"] == report["run_id"] and "output" not in history[0]
    assert store.report_by_id(config.store_path, report["run_id"]) == report
    assert browser.get("/api/v1/pipeline/runs/missing").status_code == 404
    browser.delete("/api/v1/pipeline/session")
    assert browser.get("/api/v1/pipeline/session").status_code == 401


def test_backend_enforces_role_and_rejects_spoofed_creator(client):
    browser, _ = client
    login(browser, "an")
    assert browser.get("/api/v1/pipeline/runs").status_code == 200
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
    assert report["status"] == "FAILED" and report["error"] == "Database offline"
    assert report["output"] == []
    assert browser.get("/api/v1/pipeline/runs").json()[0]["status"] == "FAILED"


def test_expired_session_and_production_login(client, monkeypatch):
    browser, config = client
    login(browser)
    with store.database(config.store_path) as conn:
        conn.execute("UPDATE sessions SET expires = 0")
    assert browser.get("/api/v1/pipeline/runs").status_code == 401
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(app_env="production"))
    assert browser.post("/api/v1/pipeline/session", json={"user_id": "minh"}).status_code == 403


def test_invalid_source_column_and_empty_source_need_attention(client, monkeypatch):
    browser, _ = client
    login(browser)
    body = {**BODY, "columns": [{"from": "missing", "as": "missing", "type": "varchar"}]}
    assert browser.post("/api/v1/pipeline/runs", json=body).json()["status"] == "FAILED"
    monkeypatch.setattr(postgres, "read_rows", lambda settings, columns: [])
    assert browser.post("/api/v1/pipeline/runs", json=BODY).json()["status"] == "NEEDS_HUMAN"


def test_database_error_does_not_expose_credentials(monkeypatch):
    import psycopg

    def fail(*args, **kwargs):
        raise psycopg.OperationalError("secret-password")

    monkeypatch.setattr(psycopg, "connect", fail)
    config = PipelineSettings(_env_file=None, database_url="postgresql://user:secret-password@localhost/test")
    with pytest.raises(postgres.SourceUnavailableError) as error:
        postgres.source_schema(config)
    assert "secret-password" not in str(error.value)
