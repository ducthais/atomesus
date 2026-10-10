"""Phát hiện schema nguồn đổi so với lúc pipeline được duyệt (src/run_report/drift.py) + GET /catalog/drift."""

from types import SimpleNamespace

import pytest

from src.run_report.drift import detect, live_pipelines
from src.run_report.models import GateResult
from src.run_report.store import save_run_report
from tests.test_run_report.test_compare import report, spec, with_deployment

LIVE = {"orders": {"order_id": "integer", "amount_text": "text", "status": "text"}}


def with_baseline(r, columns):
    r.gates.append(
        GateResult(name="schema_real", result="pass", detail="ok", observed={"sources": 1, "columns": columns})
    )
    return r


def test_live_pipelines_keep_latest_approval_per_model():
    older, newer = report("a", 0, "DEPLOYED"), report("b", 10, "DEPLOYED")
    other = report("c", 5, "DEPLOYED", spec_=spec(model="fct_other"))
    assert sorted(r.run_id for r in live_pipelines([older, newer, other, report("d", 20)])) == ["b", "c"]


def test_newer_approvals_awaiting_deploy_do_not_replace_live_pipeline():
    deployed = report("dep", 0, "DEPLOYED")
    signed = report("signed", 10, "APPROVED")
    opened = with_deployment(report("opened", 20, "APPROVED"), 25)
    assert [r.run_id for r in live_pipelines([deployed, signed, opened])] == ["dep"]
    assert live_pipelines([signed, opened]) == []


def test_unchanged_schema_is_ok():
    result = detect(report("a", 0, "DEPLOYED"), lambda ref: LIVE)
    assert result["status"] == "ok" and result["sources"][0]["missing_columns"] == []
    assert result["has_type_baseline"] is False


def test_removed_or_renamed_column_is_breaking():
    live = {"orders": {"order_id": "integer", "amount": "numeric"}}  # amount_text đã đổi tên
    result = detect(report("a", 0, "DEPLOYED"), lambda ref: live)
    assert result["status"] == "breaking" and result["sources"][0]["missing_columns"] == ["amount_text"]


def test_missing_table_is_breaking():
    result = detect(report("a", 0, "DEPLOYED"), lambda ref: {})
    assert result["status"] == "breaking" and result["sources"][0]["missing_table"] is True


def test_type_change_is_warning_only_with_baseline_and_ignores_type_parameters():
    r = with_baseline(report("a", 0, "DEPLOYED"), {"orders": {"order_id": "integer", "amount_text": "text"}})
    changed = {"orders": {"order_id": "bigint", "amount_text": "text"}}
    result = detect(r, lambda ref: changed)
    assert result["status"] == "warning" and result["sources"][0]["type_changes"] == [
        {"column": "order_id", "before": "integer", "after": "bigint"}
    ]
    same = with_baseline(
        report("b", 0, "DEPLOYED"), {"orders": {"order_id": "numeric", "amount_text": "character varying"}}
    )
    assert (
        detect(same, lambda ref: {"orders": {"order_id": "numeric(5,2)", "amount_text": "character varying(45)"}})[
            "status"
        ]
        == "ok"
    )


def test_unreachable_source_is_unknown_not_breaking():
    def down(ref):
        raise RuntimeError("Không đọc được Postgres.")

    result = detect(report("a", 0, "DEPLOYED"), down)
    assert result["status"] == "unknown" and result["sources"][0]["reason"] == "Không đọc được Postgres."


@pytest.mark.asyncio
async def test_drift_endpoint(client, tmp_path, monkeypatch):
    from src.main import app
    from src.pipeline import auth, connectors
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    monkeypatch.setattr(
        connectors, "postgres_columns", lambda _c, schema: {"orders": [{"name": "order_id", "type": "integer"}]}
    )
    monkeypatch.setattr(connectors, "postgres_keys", lambda _c, schema: ({}, []))
    monkeypatch.setattr(connectors, "postgres_row_estimates", lambda _c, schema: {})
    save_run_report(config.store_path, report("dep", 0, "DEPLOYED"))
    save_run_report(config.store_path, report("draft", 5))  # chưa duyệt: không kiểm tra
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        body = (await client.get("/api/v1/catalog/drift")).json()
        assert [p["run_id"] for p in body["pipelines"]] == ["dep"]
        assert body["pipelines"][0]["status"] == "breaking"
        assert body["pipelines"][0]["sources"][0]["missing_columns"] == ["amount_text"]
    finally:
        app.dependency_overrides.pop(auth.settings, None)
