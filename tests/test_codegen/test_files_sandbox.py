"""Nguồn filesystem (CSV) chạy thật: connector → snapshot của agent → dlt + dbt trong sandbox (P9–P12).

infra/landing/sales/refunds/ có 2 file xuất theo tháng, bẩn có chủ đích nhưng làm sạch được hết:
- tháng 1 ghi ngày YYYY-MM-DD, tháng 2 ghi DD/MM/YYYY,
- 3 khoản hoàn bằng USD (0.50, 1.00, 0.80), số tiền có hoặc không có đuôi ".00",
- khoản hoàn 7 xuất lặp ở cả hai file.
Kết quả đúng: 12 khoản cho 12 đơn; 20 700 VND + 2.30 USD × 25 400 = 79 120 VND.
"""

import os
from decimal import Decimal

import pytest

from src.agents import resolve
from src.agents.validate import validate_spec
from src.pipeline import connectors, files
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.policy.models import SourceDecl
from src.spec.models import Spec

pytest.importorskip("dbt.cli.main")

EXPECTED_TOTAL_VND = Decimal("79120.00")

SPEC = {
    "spec_version": "1.2",
    "domain": "sales",
    "sources": [
        {
            "id": "refunds",
            "kind": "filesystem",
            "source_ref": "landing_sales",
            "path_pattern": "refunds/*.csv",
            "format": "csv",
            "primary_key": ["refund_id"],
            "cursor_field": None,
            "write_disposition": "replace",
            "target": {"dataset": "bronze", "table": "raw_refunds"},
            "staging": {
                "columns": [
                    {"from": "refund_id", "as": "refund_id", "type": "integer"},
                    {"from": "order_id", "as": "order_id", "type": "integer"},
                    {
                        "from": "amount",
                        "as": "amount_vnd",
                        "type": "decimal(18,2)",
                        "currency": {"code_column": "currency", "to": "VND"},
                    },
                    {
                        "from": "refunded_at",
                        "as": "refunded_at",
                        "type": "date",
                        "date_formats": ["%Y-%m-%d", "%d/%m/%Y"],
                    },
                ],
                "dedupe": {"keys": ["refund_id"], "keep": "first", "order_by": "refund_id"},
                "reject_if": [],
            },
        }
    ],
    "transform": {
        "model_name": "fct_order_refunds",
        "from": "refunds",
        "joins": [],
        "materialization": "table",
        "grain": ["order_id"],
        "metrics": [
            {"name": "refunded", "expr": "SUM(amount_vnd)"},
            {"name": "refund_count", "expr": "COUNT(refund_id)"},
        ],
        "filters": [],
        "tests": [{"column": "order_id", "test": "unique"}],
    },
    "schedule": {"cron": "0 * * * *"},
    "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
}


def _decl():
    return load_policy().domains.domains["sales"].sources["landing_sales"]


def test_folders_become_objects_with_a_suggested_key():
    found = connectors.discover(_decl(), PipelineSettings(_env_file=None))
    (refunds,) = found["objects"]
    assert (refunds["name"], refunds["kind"], refunds["primary_key"]) == ("refunds", "folder", ["refund_id"])
    assert {c["type"] for c in refunds["columns"]} == {"text"}, "CSV không có kiểu: staging ép kiểu"
    rows = list(files.records(_decl(), "refunds"))
    assert len(rows) == 13 and rows[7]["refunded_at"] == "21/01/2026", "dòng đầu của file tháng 2 là bản xuất lặp"


def test_root_must_stay_inside_the_repo():
    outside = SourceDecl(kind="filesystem", connection_ref="x", root="../../")
    with pytest.raises(files.FilesUnavailableError):
        files.root(outside)
    assert files.suggested_key("refunds", ["order_id", "refund_id"]) == ["refund_id"]
    assert files.suggested_key("events", ["event_time"]) == []


def test_snapshot_feeds_the_agent_and_validates(monkeypatch):
    monkeypatch.setattr(resolve, "_tables", lambda settings, schema, decl=None: {})
    monkeypatch.setattr(resolve, "_relations", lambda settings, schema, decl=None: [])
    policy, settings = load_policy(), PipelineSettings(_env_file=None, mock_api_url="http://127.0.0.1:9")
    assert resolve.catalog(policy, settings)["sales"]["sources"]["landing_sales"]["available"] is True
    plan = {"domain": "sales", "sources": [{"id": "refunds", "source_ref": "landing_sales", "object": "refunds"}]}
    snap = resolve.snapshot(plan, policy, settings)["refunds"]
    assert (snap["path_pattern"], snap["format"], snap["primary_key"]) == ("refunds/*.csv", "csv", ["refund_id"])
    assert not validate_spec(SPEC, {"refunds": snap}, policy).errors


def test_sandbox_loads_csv_into_bronze_and_cleans_it():
    from src.codegen.render import render
    from src.codegen.sandbox import run_in_sandbox

    policy = load_policy()
    database_url = os.environ.get("PIPELINE_TEST_DATABASE_URL", "")
    settings = PipelineSettings(_env_file=None, database_url=database_url)
    spec = Spec.model_validate(SPEC)
    result = run_in_sandbox(spec, render(spec, policy), policy, settings)
    assert result.status == "READY_FOR_REVIEW", result.error_trace
    checks = {c.name: c for c in result.checks}
    assert checks["row_reconcile"].observed == {"source": 13, "bronze": 13, "source_records": 13}
    assert checks["hash_reconcile"].result == checks["idempotent"].result == "pass"
    staged = checks["staging_reconcile"].observed
    assert (staged["bronze"], staged["staging"], staged["duplicates"], staged["rejected"]) == (13, 12, 1, 0)
    gold = result.gates[0].observed
    total = next(c for c in gold["gold_profile"] if c["name"] == "refunded")
    assert gold["gold_rows"] == 12 and Decimal(total["sum"]) == EXPECTED_TOTAL_VND
    if database_url:  # đối chiếu độc lập: đọc lại file CSV, tính trên Postgres
        assert checks["metric_reconcile"].result == "pass", checks["metric_reconcile"].observed
    else:
        assert "Postgres" in gold["reconcile_skipped"]
