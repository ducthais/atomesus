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
    assert report["rows_read"] == report["rows_written"] == 30
    assert report["rows_rejected"] == 0
    assert sum(Decimal(row["amount"]) for row in report["output"]) == Decimal("465000.00")
    assert report["output"][0] == {"id": "1", "amount": "1000.00", "ordered_at": "2026-01-01"}
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
