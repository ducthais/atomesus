"""Chạy dbt thật trên SQL sinh ra với bronze bẩn có chủ đích: mỗi dòng phải đi đúng một đường
(staging sạch / loại vì trùng / bảng rejected kèm lý do và giá trị thô)."""

import json
from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("dbt.cli.main")
duckdb = pytest.importorskip("duckdb")

from src.codegen import sandbox  # noqa: E402
from src.codegen.render import render  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402
from src.spec.models import Spec  # noqa: E402

SPEC = {
    "spec_version": "1.2",
    "domain": "sales",
    "sources": [
        {
            "id": "payments",
            "kind": "postgres",
            "source_ref": "sales_pg",
            "table": "payments",
            "primary_key": ["orderId"],
            "cursor_field": "updated_at",
            "write_disposition": "merge",
            "target": {"dataset": "bronze", "table": "raw_payments"},
            "staging": {
                "columns": [
                    {"from": "orderId", "as": "order_id", "type": "bigint"},
                    {"from": "paid_at", "as": "paid_date", "type": "date", "date_formats": ["%Y-%m-%d", "%d/%m/%Y"]},
                    {
                        "from": "total",
                        "as": "amount_vnd",
                        "type": "decimal(18,2)",
                        "currency": {"code_column": "ccy", "to": "VND"},
                    },
                ],
                "dedupe": {"keys": ["orderId"], "keep": "latest", "order_by": "updated_at"},
                "reject_if": ["order_id IS NULL", "amount_vnd < 0"],
            },
        }
    ],
    "transform": {
        "model_name": "fct_paid_daily",
        "from": "payments",
        "joins": [],
        "materialization": "table",
        "unique_key": ["paid_date"],
        "grain": ["paid_date"],
        "metrics": [
            {"name": "revenue", "expr": "SUM(amount_vnd)"},
            {"name": "order_count", "expr": "COUNT(DISTINCT order_id)"},
        ],
        "filters": [],
        "tests": [
            {"column": "paid_date", "test": "not_null"},
            {"column": "paid_date", "test": "unique"},
            {"column": "revenue", "test": "not_negative"},
        ],
    },
    "schedule": {"cron": "0 2 * * *"},
    "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
}
# (order_id, paid_at, total, ccy, updated_at): cột bronze đã được dlt chuẩn hóa (orderId → order_id)
BRONZE = [
    ("1", "2026-01-01", "100", "VND", 1),  # bị bản ghi mới hơn của cùng order_id thay thế
    ("1", "2026-01-01", "150", "VND", 2),  # giữ (keep: latest)
    ("2", "02/01/2026", "10", "usd", 1),  # định dạng ngày thứ hai, USD → VND theo tỷ giá policy
    ("3", "2026-01-03", "5", "EUR", 1),  # unknown_currency
    ("4", "2026-01-04", "abc", "VND", 1),  # cast
    ("5", "2026/13/45", "20", "VND", 1),  # date_format
    ("6", "2026-01-06", "-1", "VND", 1),  # reject_if amount_vnd < 0
    (None, "2026-01-07", "1", "VND", 1),  # reject_if order_id IS NULL
]


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    policy = load_policy()
    spec = Spec.model_validate(SPEC)
    root = tmp_path_factory.mktemp("sandbox")
    sandbox.prepare_project(root, render(spec, policy))
    path = root / "warehouse.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("create schema bronze")
        db.execute(
            "create table bronze.raw_payments (order_id varchar, paid_at varchar, total varchar, ccy varchar,"
            " updated_at integer, _dlt_id varchar)"
        )
        db.executemany(
            "insert into bronze.raw_payments values (?, ?, ?, ?, ?, ?)",
            [(*row, f"id{i}") for i, row in enumerate(BRONZE)],
        )
    env = sandbox._env(root, spec, policy, _Settings())
    results, test_error = sandbox._t_run(root, env, timeout=300)
    with duckdb.connect(str(path), read_only=True) as db:
        staged = sandbox._staging_reconcile(db, spec, max_reject_pct=100)
        yield {
            "spec": spec,
            "results": results,
            "test_error": test_error,
            "staged": staged,
            "staging": db.execute('select * from staging."stg_fct_paid_daily__payments" order by order_id').fetchall(),
            "columns": [c[0] for c in db.execute('select * from staging."stg_fct_paid_daily__payments"').description],
            "rejected": dict(
                db.execute(
                    'select _reject_reason, _raw_values from staging."stg_fct_paid_daily__payments__rejected"'
                ).fetchall()
            ),
            "gold": db.execute('select * from gold."fct_paid_daily" order by paid_date').fetchall(),
        }


class _Settings:
    """Không cần kết nối nguồn: bronze được dựng sẵn, chỉ chạy dbt."""

    warehouse_timezone = "UTC"

    class database_url:  # noqa: N801
        @staticmethod
        def get_secret_value():
            return "postgresql://unused@localhost/unused"


def test_dbt_build_passes(warehouse):
    assert warehouse["test_error"] is None
    assert {r["status"] for r in warehouse["results"]} <= {"success", "pass"}
    assert sum(r["unique_id"].startswith("test.") for r in warehouse["results"]) == 5


def test_staging_keeps_clean_deduplicated_rows_only(warehouse):
    assert warehouse["columns"] == ["order_id", "paid_date", "amount_vnd"]  # cột kỹ thuật đã bị xóa
    assert warehouse["staging"] == [
        (1, date(2026, 1, 1), Decimal("150.00")),
        (2, date(2026, 1, 2), Decimal("254000.00")),
    ]


def test_every_dirty_row_lands_in_rejected_with_reason_and_raw_values(warehouse):
    rejected = warehouse["rejected"]
    assert set(rejected) == {
        "unknown_currency:amount_vnd",
        "cast:amount_vnd",
        "date_format:paid_date",
        "reject_if:amount_vnd < 0",
        "reject_if:order_id IS NULL",
    }
    assert json.loads(rejected["unknown_currency:amount_vnd"]) == {
        "orderId": "3",
        "paid_at": "2026-01-03",
        "total": "5",
    }
    assert json.loads(rejected["date_format:paid_date"])["paid_at"] == "2026/13/45"


def test_staging_reconcile_accounts_for_every_bronze_row(warehouse):
    (check,) = warehouse["staged"]
    observed = check.observed
    assert check.result == "pass"
    assert (observed["bronze"], observed["staging"], observed["duplicates"], observed["rejected"]) == (8, 2, 1, 5)
    assert observed["bronze"] == observed["staging"] + observed["duplicates"] + observed["rejected"]
    assert observed["reject_pct"] == 62.5
    assert len(observed["reject_samples"]) == 5 and all("_reject_reason" in s for s in observed["reject_samples"])


def test_gold_aggregates_clean_staging(warehouse):
    assert [(row[0], row[1], row[2]) for row in warehouse["gold"]] == [
        (date(2026, 1, 1), Decimal("150.00"), 1),
        (date(2026, 1, 2), Decimal("254000.00"), 1),
    ]
