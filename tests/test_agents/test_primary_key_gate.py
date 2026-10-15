"""Cổng `schema_real`: primary_key của Spec phải đúng khóa thật của nguồn, dedupe phải giữ đủ khóa đó.

Tái hiện từ eval 2026-10-15 (ca valid_postgres_rest_api_reviews_by_customer): snapshot Postgres chưa có khóa chính,
LLM đặt primary_key và dedupe của `pagila.rental` là `customer_id`, staging còn mỗi khách một lượt thuê nên
rental_count = 1 cho cả 599 khách, mà mọi test dbt vẫn đạt.
"""

import copy

import pytest

from src.agents.validate import validate_spec
from src.policy.loader import load_policy
from tests.test_agents.test_spec_gates import ORDERS, _spec


def _snapshot(kind="postgres", primary_key=("order_id",)):
    item = {
        "source_ref": "sales_pg",
        "kind": kind,
        "table": "orders",
        "columns": {"order_id": "integer", "customer_id": "integer", "amount": "numeric"},
        "sample_rows": [],
    }
    if primary_key is not None:
        item["primary_key"] = list(primary_key)
    return {"orders": item}


def _orders(primary_key, dedupe_keys):
    source = copy.deepcopy(ORDERS)
    source["primary_key"] = primary_key
    source["staging"]["dedupe"] = {"keys": dedupe_keys, "keep": "first", "order_by": dedupe_keys[0]}
    return _spec([source], [], [("orders", "COUNT(order_id)")], ["customer_id"])


def _schema_real(result):
    return next(g for g in result.gates if g.name == "schema_real")


def test_key_and_dedupe_on_a_non_unique_column_are_rejected():
    result = validate_spec(_orders(["customer_id"], ["customer_id"]), _snapshot(), load_policy())
    assert _schema_real(result).result == "fail"
    assert len(result.errors) == 2
    assert "primary_key: ['customer_id'] khác khóa chính thật của nguồn ['order_id']" in result.errors[0]
    assert "staging.dedupe.keys: ['customer_id'] thiếu cột khóa chính ['order_id']" in result.errors[1]


def test_right_key_passes_and_dedupe_may_add_columns():
    assert not validate_spec(_orders(["order_id"], ["order_id"]), _snapshot(), load_policy()).errors
    assert not validate_spec(_orders(["order_id"], ["order_id", "customer_id"]), _snapshot(), load_policy()).errors


def test_composite_key_compares_as_a_set():
    snapshot = _snapshot(primary_key=("customer_id", "order_id"))
    both = _orders(["order_id", "customer_id"], ["order_id", "customer_id"])
    assert not validate_spec(both, snapshot, load_policy()).errors
    assert validate_spec(_orders(["order_id"], ["order_id"]), snapshot, load_policy()).errors


@pytest.mark.parametrize(
    "snapshot",
    [_snapshot(primary_key=None), _snapshot(primary_key=()), _snapshot(kind="filesystem", primary_key=("order_id",))],
    ids=["no-key-in-snapshot", "table-without-primary-key", "csv-key-is-only-a-suggestion"],
)
def test_sources_without_a_real_key_are_not_forced(snapshot):
    result = validate_spec(_orders(["customer_id"], ["customer_id"]), snapshot, load_policy())
    assert not any("khóa chính" in error for error in result.errors)
