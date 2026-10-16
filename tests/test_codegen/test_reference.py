"""Đối chiếu độc lập: Spec → SQL Postgres tính lại gold, và so từng dòng với gold của pipeline.

Lỗi thật đã gặp: gold lệch +7 giờ do DuckDB ép timestamptz theo múi giờ máy chạy; tổng vẫn khớp, chỉ so từng dòng
mới thấy. Test ở đây không cần Postgres: kiểm SQL sinh ra và phép so.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.codegen.reference import Inline, UnsupportedError, compare, is_match, reference_sql
from src.policy.loader import load_policy
from src.spec.models import Spec
from tests.test_codegen.test_preaggregate import spec as joined_spec
from tests.test_codegen.test_time_grain import spec as pagila_spec

PAGILA_COLUMNS = {
    "payment": {"payment_id": "integer", "amount": "numeric(5,2)", "payment_date": "timestamp with time zone"}
}


@pytest.fixture(scope="module")
def policy():
    return load_policy()


def sales_spec(**transform) -> Spec:
    return Spec.model_validate(
        {
            "spec_version": "1.2",
            "domain": "sales",
            "sources": [
                {
                    "id": "orders",
                    "kind": "postgres",
                    "source_ref": "sales_pg",
                    "table": "orders",
                    "primary_key": ["order_id"],
                    "cursor_field": None,
                    "write_disposition": "replace",
                    "target": {"dataset": "bronze", "table": "raw_orders"},
                    "staging": {
                        "columns": [
                            {"from": "order_id", "as": "order_id", "type": "integer"},
                            {"from": "amount_text", "as": "amount_vnd", "type": "decimal(18,2)"},
                            {
                                "from": "ordered_at_text",
                                "as": "ordered_at",
                                "type": "date",
                                "date_formats": ["%Y-%m-%d", "%d/%m/%Y"],
                            },
                            {"from": "status", "as": "status", "type": "varchar"},
                        ],
                        "dedupe": {"keys": ["order_id"], "keep": "latest", "order_by": "order_id"},
                        "reject_if": ["amount_vnd < 0"],
                    },
                }
            ],
            "transform": {
                "model_name": "fct_daily_sales",
                "from": "orders",
                "joins": [],
                "materialization": "table",
                "grain": ["orders.ordered_at"],
                "metrics": [{"name": "revenue", "expr": "SUM(orders.amount_vnd)"}],
                "filters": ["orders.status = 'paid'"],
                **transform,
            },
            "schedule": {"cron": "0 2 * * *"},
            "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
        }
    )


SALES_COLUMNS = {"orders": {"order_id": "integer", "amount_text": "text", "ordered_at_text": "text", "status": "text"}}


@pytest.mark.parametrize("inline_ids", [(), ("orders", "payments"), ("payments",)], ids=["postgres", "inline", "mixed"])
def test_all_staging_ctes_are_materialized(policy, inline_ids):
    spec = joined_spec([("total_paid", "SUM(payments.amount)")])
    columns = {
        "orders": {"order_id": "integer", "customer_id": "integer", "amount": "numeric"},
        "payments": {"payment_id": "integer", "order_id": "integer", "amount": "numeric", "method": "text"},
    }
    inline = {sid: Inline(columns[sid], [{name: 1 for name in columns[sid]}]) for sid in inline_ids}
    query = reference_sql(spec, policy, lambda schema: columns, inline)
    for source in spec.sources:
        assert f'"stg_{source.id}" AS MATERIALIZED (' in query
    assert query.count("AS MATERIALIZED (") == len(spec.sources)


def test_timestamptz_is_read_in_utc_and_bucketed(policy):
    spec = Spec.model_validate(pagila_spec({"column": "payment_date", "unit": "month"}))
    query = reference_sql(spec, policy, lambda schema: PAGILA_COLUMNS)
    assert "AT TIME ZONE 'UTC'" in query
    assert "CAST(date_trunc('month', payment_date) AS date) AS \"payment_date\"" in query
    assert 'FROM "sales"."payment" AS src' in query or '"payment" AS src' in query
    assert 'SUM(amount_vnd) AS "revenue"' in query and "GROUP BY 1" in query


def test_text_columns_are_parsed_safely_like_try_cast(policy):
    query = reference_sql(sales_spec(), policy, lambda schema: SALES_COLUMNS)
    assert "~ '^\\s*[+-]?(\\d+(\\.\\d*)?|\\.\\d+)([eE][+-]?\\d+)?\\s*$'" in query  # số dạng chuỗi: chỉ ép khi hợp lệ
    assert (
        "make_timestamp" in query and "BETWEEN 1 AND 12" in query
    )  # ngày dạng chuỗi: 2026/13/45 thành NULL, không lỗi
    assert query.count("regexp_match") >= 2  # mỗi định dạng một lần thử
    assert '(_raw_1 IS NOT NULL AND "amount_vnd" IS NULL)' in query  # ép kiểu lỗi → loại như staging
    assert "(amount_vnd < 0) IS TRUE" in query
    assert "PARTITION BY _key_0 ORDER BY _order DESC NULLS LAST" in query  # keep: latest
    assert "AND (orders.status = 'paid')" in query


def test_unsupported_cases_say_why(policy):
    with pytest.raises(UnsupportedError, match="median"):
        reference_sql(
            sales_spec(metrics=[{"name": "m", "expr": "MEDIAN(orders.amount_vnd)"}]), policy, lambda s: SALES_COLUMNS
        )
    with pytest.raises(UnsupportedError, match="không còn ở nguồn"):
        reference_sql(sales_spec(), policy, lambda schema: {"orders": {"order_id": "integer"}})
    with pytest.raises(UnsupportedError, match="không còn ở nguồn"):
        reference_sql(sales_spec(), policy, lambda schema: {})


def test_identical_rows_match_to_the_cent(policy):
    spec = sales_spec()
    gold = [
        {"ordered_at": date(2026, 1, 1), "revenue": Decimal("1000.00")},
        {"ordered_at": date(2026, 1, 2), "revenue": 2000.0},
    ]
    source = [
        {"ordered_at": date(2026, 1, 2), "revenue": Decimal("2000.004")},
        {"ordered_at": date(2026, 1, 1), "revenue": Decimal("1000")},
    ]
    observed = compare(spec, gold, source)
    assert is_match(observed) and observed["matched"] == 2
    assert observed["totals"]["revenue"] == {"gold": "3000.00", "source": "3000.004"}


def test_shifted_timestamps_are_caught_even_when_totals_match(policy):
    spec = Spec.model_validate(pagila_spec())
    utc = [{"payment_date": datetime(2017, 1, 24, 18, 21), "revenue": Decimal("2.99"), "payment_count": 1}]
    shifted = [{"payment_date": datetime(2017, 1, 25, 1, 21), "revenue": Decimal("2.99"), "payment_count": 1}]
    observed = compare(spec, shifted, utc)
    assert not is_match(observed)
    assert observed["missing_count"] == 1 and observed["extra_count"] == 1
    assert observed["missing_in_gold"] == [{"payment_date": "2017-01-24 18:21:00"}]
    assert observed["totals"]["revenue"]["gold"] == observed["totals"]["revenue"]["source"]
    assert observed["shift_hours"] == 7  # tự chẩn đoán: mọi thời điểm lệch đúng +7 giờ


def test_value_difference_is_reported_with_both_numbers(policy):
    spec = sales_spec()
    observed = compare(
        spec,
        [{"ordered_at": date(2026, 1, 1), "revenue": Decimal("1000.00")}],
        [{"ordered_at": date(2026, 1, 1), "revenue": Decimal("1000.02")}],
    )
    assert observed["mismatch_count"] == 1 and observed["matched"] == 0
    assert observed["mismatched"][0] == {
        "key": {"ordered_at": "2026-01-01"},
        "metric": "revenue",
        "gold": "1000.00",
        "source": "1000.02",
    }


@pytest.mark.asyncio
async def test_reconcile_deployed_endpoint(client, tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    from src.codegen import reference
    from src.main import app
    from src.pipeline import auth
    from src.pipeline.settings import PipelineSettings
    from src.run_report.store import save_run_report
    from tests.test_run_report.test_compare import report

    warehouse = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(warehouse)) as db:
        db.execute("create schema gold")
        db.execute("create table gold.fct_daily_sales as select date '2026-01-01' as ordered_at, 1000.00 as revenue")
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"), warehouse_path=str(warehouse))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    seen = {}

    def fake_reconcile(spec, gold, policy, settings):
        seen["gold"] = gold
        seen["sales_hidden"] = policy.domains.domains["sales"].hidden
        return False, {"mismatch_count": 1}

    monkeypatch.setattr(reference, "reconcile", fake_reconcile)
    save_run_report(
        config.store_path, report("dep", 0, "DEPLOYED", spec_=sales_spec().model_dump(by_alias=True, mode="json"))
    )
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        body = (await client.post("/api/v1/catalog/datasets/sales/fct_daily_sales/reconcile")).json()
        assert body["available"] is True and body["matched"] is False and body["run_id"] == "dep"
        assert seen["gold"] == [{"ordered_at": date(2026, 1, 1), "revenue": Decimal("1000.00")}]
        assert seen["sales_hidden"] is True
        missing = await client.post("/api/v1/catalog/datasets/sales/fct_khac/reconcile")
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(auth.settings, None)
