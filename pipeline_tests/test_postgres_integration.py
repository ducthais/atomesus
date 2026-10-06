"""Run against the dedicated seeded database via PIPELINE_TEST_DATABASE_URL."""

import os
from decimal import Decimal

import pytest

from src.pipeline import postgres
from src.pipeline.cleaning import clean_rows
from src.pipeline.models import ColumnRule
from src.pipeline.settings import PipelineSettings


@pytest.fixture
def config():
    url = os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PIPELINE_TEST_DATABASE_URL to the dedicated seeded test database")
    return PipelineSettings(_env_file=None, database_url=url)


def test_seed_schema_counts_and_golden_output(config):
    schema = postgres.source_schema(config)
    assert [column["name"] for column in schema] == [
        "order_id",
        "customer_id",
        "amount_text",
        "ordered_at_text",
        "status",
    ]
    rows = postgres.read_rows(config, [column["name"] for column in schema])
    assert len(rows) == 30
    rules = [
        ColumnRule.model_validate({"from": "order_id", "as": "id", "type": "integer"}),
        ColumnRule.model_validate({"from": "amount_text", "as": "amount", "type": "decimal(18,2)"}),
        ColumnRule.model_validate({"from": "ordered_at_text", "as": "ordered_at", "type": "date"}),
    ]
    report = clean_rows(rows, rules)
    # Seed bẩn có chủ đích: TRY_CAST không đọc được ngày DD/MM/YYYY (đơn chia hết cho 3) nên 10 đơn bị tách.
    # Khoảng trắng / đuôi ".00" ở số tiền và ngày YYYY/MM/DD thì ép kiểu vẫn được.
    assert (report["rows_read"], report["rows_written"], report["rows_rejected"]) == (30, 20, 10)
    assert {r["values"]["order_id"] for r in report["rejected"]} == {str(i) for i in range(3, 31, 3)}
    assert sum(Decimal(row["amount"]) for row in report["output"]) == Decimal("300000.00")
    assert report["output"][0] == {"id": "1", "amount": "1000.00", "ordered_at": "2026-01-01"}
    assert {"id": "4", "amount": "4000.00", "ordered_at": "2026-01-04"} in report["output"]
    assert clean_rows(rows, rules)["output"] == report["output"]


def test_source_is_read_only_even_if_transaction_readonly_is_disabled(config):
    import psycopg

    with pytest.raises(postgres.SourceUnavailableError):
        with postgres.connection(config) as conn:
            conn.execute("DELETE FROM sales_raw.orders WHERE false")
    with psycopg.connect(config.database_url.get_secret_value()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SET TRANSACTION READ WRITE")
            conn.execute("DELETE FROM sales_raw.orders WHERE false")


def test_row_limit_fails_instead_of_silent_truncation(config):
    config.max_rows = 10
    with pytest.raises(ValueError, match="10"):
        postgres.read_rows(config, ["order_id"])
