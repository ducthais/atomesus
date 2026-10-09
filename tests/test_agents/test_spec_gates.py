"""Cổng `currency` và `join_fanout`: hai lỗi làm sai số liệu gold mà mọi kiểm tra sau chạy thử đều cho qua.

Tái hiện từ hai run thật với nguồn API (2026-10-06): 81048089 cộng 1,50 USD như 1,50 VND; b8d45355 cộng tổng tiền
đơn hàng 4 lần vì mỗi đơn có 4 khoản thanh toán.
"""

import copy

import pytest

from src.agents.validate import _join_fanout, _multiplied, validate_spec
from src.policy.loader import load_policy
from src.spec.models import Spec


def _source(sid, kind, columns, key, **extra):
    base = {
        "id": sid,
        "kind": kind,
        "source_ref": "sales_api" if kind == "rest_api" else "sales_pg",
        "primary_key": [key],
        "cursor_field": None,
        "write_disposition": "replace",
        "target": {"dataset": "bronze", "table": f"raw_{sid}"},
        "staging": {
            "columns": [{"from": c, "as": c, "type": t} for c, t in columns],
            "dedupe": {"keys": [key], "keep": "first", "order_by": key},
            "reject_if": [],
        },
    }
    if kind == "rest_api":
        base |= {"endpoint": f"/{sid}", "records_path": "data", "pagination": "page"}
    else:
        base["table"] = sid
    return base | extra


CUSTOMERS = _source("customers", "postgres", [("customer_id", "integer"), ("customer_name", "varchar")], "customer_id")
ORDERS = _source(
    "orders", "postgres", [("order_id", "integer"), ("customer_id", "integer"), ("amount", "decimal(18,2)")], "order_id"
)
PAYMENTS = _source(
    "payments",
    "rest_api",
    [("payment_id", "integer"), ("order_id", "integer"), ("amount", "decimal(18,2)")],
    "payment_id",
)


def _spec(sources, joins, metrics, grain):
    return {
        "spec_version": "1.2",
        "domain": "sales",
        "sources": copy.deepcopy(sources),
        "transform": {
            "model_name": "fct_test",
            "from": sources[0]["id"],
            "joins": joins,
            "materialization": "table",
            "unique_key": [],
            "grain": grain,
            "metrics": [{"name": name, "expr": expr} for name, expr in metrics],
            "filters": [],
            "tests": [],
        },
        "schedule": {"cron": "0 2 * * *"},
        "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
    }


CUSTOMER_JOINS = [
    {"source": "orders", "type": "inner", "on": [["customers.customer_id", "orders.customer_id"]]},
    {"source": "payments", "type": "left", "on": [["orders.order_id", "payments.order_id"]]},
]


def _fanout(metrics, sources=(CUSTOMERS, ORDERS, PAYMENTS), joins=CUSTOMER_JOINS, grain=("customers.customer_name",)):
    return _join_fanout(Spec.model_validate(_spec(list(sources), joins, metrics, list(grain))))


def test_one_to_many_join_multiplies_every_table_joined_before():
    spec = Spec.model_validate(
        _spec([CUSTOMERS, ORDERS, PAYMENTS], CUSTOMER_JOINS, [("x", "COUNT(*)")], ["customers.customer_name"])
    )
    assert set(_multiplied(spec)) == {"customers", "orders"}, "payments không bị lặp: mỗi khoản khớp đúng một đơn"


def test_sum_on_the_one_side_passes_once_the_many_side_is_preaggregated():
    # run b8d45355: SUM(orders.amount) cộng 4 lần; giờ payments được gộp trước theo order_id.
    spec = Spec.model_validate(
        _spec(
            [CUSTOMERS, ORDERS, PAYMENTS],
            CUSTOMER_JOINS,
            [("total", "SUM(orders.amount)")],
            ["customers.customer_name"],
        )
    )
    assert _join_fanout(spec) == [] and set(_multiplied(spec)) == {"customers"}


def test_sum_is_rejected_when_the_many_side_cannot_be_preaggregated():
    # Gộp theo một cột của payments: mỗi đơn vẫn lặp theo từng phương thức, không gộp trước được.
    payments = copy.deepcopy(PAYMENTS)
    payments["staging"]["columns"].append({"from": "method", "as": "method", "type": "varchar"})
    errors = _fanout(
        [("total_order", "SUM(orders.amount)"), ("total_paid", "SUM(payments.amount)")],
        (CUSTOMERS, ORDERS, payments),
        grain=("payments.method",),
    )
    assert (
        len(errors) == 1
        and "total_order" in errors[0]
        and "không được bỏ chỉ số người dùng yêu cầu" in errors[0].lower()
    )


@pytest.mark.parametrize(
    "expr",
    ["SUM(payments.amount)", "COUNT(DISTINCT orders.order_id)", "MAX(orders.amount)", "COUNT(payments.payment_id)"],
)
def test_safe_aggregates_pass(expr):
    assert _fanout([("m", expr)]) == []


def test_count_star_after_fanout_is_rejected():
    (error,) = _fanout([("n", "COUNT(*)")])
    assert "COUNT(DISTINCT" in error


def test_many_to_one_join_only_multiplies_the_lookup_side():
    joins = [{"source": "customers", "type": "left", "on": [["orders.customer_id", "customers.customer_id"]]}]
    assert _fanout([("revenue", "SUM(orders.amount)")], (ORDERS, CUSTOMERS), joins, ("customers.customer_name",)) == []
    assert _fanout([("n", "COUNT(customers.customer_id)")], (ORDERS, CUSTOMERS), joins, ("orders.order_id",))


def test_single_source_has_no_fanout():
    assert _fanout([("n", "COUNT(*)"), ("s", "SUM(amount)")], (PAYMENTS,), [], ("order_id",)) == []


SNAPSHOT_ROWS = [
    {"payment_id": "1", "order_id": "1", "amount": "100", "currency": "VND", "paid_at": "2026-01-01T09:00:00Z"},
    {"payment_id": "7", "order_id": "2", "amount": "1.50", "currency": "USD", "paid_at": "2026-01-02T14:00:00Z"},
]


def _api_snapshot(rows=SNAPSHOT_ROWS):
    columns = {"payment_id": "integer", "order_id": "integer", "amount": "text", "currency": "text"}
    columns["paid_at"] = "timestamp with time zone"
    return {
        "payments": {
            "source_ref": "sales_api",
            "kind": "rest_api",
            "table": "payments",
            "endpoint": "/payments",
            "records_path": "data",
            "pagination": "page",
            "columns": columns,
            "sample_rows": rows,
        }
    }


def _payments_spec(currency=None):
    source = copy.deepcopy(PAYMENTS)
    if currency:
        source["staging"]["columns"][2]["currency"] = currency
    return _spec([source], [], [("total", "SUM(amount)")], ["order_id"])


def test_money_column_must_convert_when_the_source_has_a_currency_code():
    result = validate_spec(_payments_spec(), _api_snapshot(), load_policy())
    gate = next(g for g in result.gates if g.name == "currency")
    assert gate.result == "fail" and "'currency'" in result.errors[0] and "['USD', 'VND']" in result.errors[0]
    assert '"to": "VND"' in result.errors[0]

    fixed = validate_spec(_payments_spec({"code_column": "currency", "to": "VND"}), _api_snapshot(), load_policy())
    assert not fixed.errors and next(g for g in fixed.gates if g.name == "currency").result == "pass"


def test_no_currency_column_means_nothing_to_convert():
    rows = [{k: v for k, v in row.items() if k != "currency"} for row in SNAPSHOT_ROWS]
    snapshot = _api_snapshot(rows)
    snapshot["payments"]["columns"].pop("currency")
    assert not validate_spec(_payments_spec(), snapshot, load_policy()).errors


def test_date_formats_dropped_when_the_raw_column_is_already_a_timestamp():
    # run fbb052d9: dlt đổ chuỗi ISO thành timestamp ở bronze; date_formats khiến 121/122 dòng bị tách.
    from src.agents.nodes.codegen import _add_key_columns

    raw = _payments_spec({"code_column": "currency", "to": "VND"})
    columns = raw["sources"][0]["staging"]["columns"]
    columns.append({"from": "paid_at", "as": "paid_date", "type": "date", "date_formats": ["%Y-%m-%dT%H:%M:%SZ"]})
    columns.append({"from": "amount", "as": "amount_text", "type": "varchar", "date_formats": None})
    fixed = _add_key_columns(raw, _api_snapshot())
    paid = next(c for c in fixed["sources"][0]["staging"]["columns"] if c["as"] == "paid_date")
    assert paid["date_formats"] is None and paid["type"] == "date"

    text = _api_snapshot()
    text["payments"]["columns"]["paid_at"] = "text"  # cột chuỗi thật thì giữ nguyên định dạng
    columns[-2]["date_formats"] = ["%Y-%m-%dT%H:%M:%SZ"]
    kept = _add_key_columns(raw, text)
    assert next(c for c in kept["sources"][0]["staging"]["columns"] if c["as"] == "paid_date")["date_formats"]


def test_time_grain_column_follows_the_grain_spelling():
    # run e308aa64: grain ['payments.paid_date'] nhưng time_grain.column 'paid_date', LLM lặp lỗi 3 lần.
    from src.agents.nodes.codegen import _add_key_columns

    raw = _payments_spec({"code_column": "currency", "to": "VND"})
    raw["transform"] |= {"grain": ["payments.paid_at"], "time_grain": {"column": "paid_at", "unit": "day"}}
    assert _add_key_columns(raw, _api_snapshot())["transform"]["time_grain"]["column"] == "payments.paid_at"


def test_currency_is_moved_to_the_source_that_has_a_currency_code():
    # run c6a290d8: LLM gắn currency cho orders (không có cột mã tiền), bị báo thì bỏ luôn ở payments.
    from src.agents.nodes.codegen import _add_key_columns

    orders, payments = copy.deepcopy(ORDERS), copy.deepcopy(PAYMENTS)
    orders["staging"]["columns"][2]["currency"] = {"code_column": "currency", "to": "VND"}
    raw = _spec(
        [orders, payments],
        [{"source": "payments", "type": "left", "on": [["orders.order_id", "payments.order_id"]]}],
        [("t", "SUM(payments.amount)")],
        ["orders.order_id"],
    )
    snapshot = {
        **_api_snapshot(),
        "orders": {
            "source_ref": "sales_pg",
            "kind": "postgres",
            "table": "orders",
            "columns": {"order_id": "integer", "customer_id": "integer", "amount": "text"},
            "sample_rows": [],
        },
    }
    fixed = _add_key_columns(raw, snapshot, load_policy())
    by_id = {s["id"]: s["staging"]["columns"][2] for s in fixed["sources"]}
    assert by_id["orders"]["currency"] is None
    assert by_id["payments"]["currency"] == {"code_column": "currency", "to": "VND"}
    assert not validate_spec(fixed, snapshot, load_policy()).errors


def test_api_join_key_takes_the_type_of_the_other_side():
    # eval 2026-10-08: orders.order_id integer, refunds.order_id (CSV) varchar; LLM lặp lỗi lệch kiểu 2 lần.
    from src.agents.nodes.codegen import _add_key_columns

    payments = copy.deepcopy(PAYMENTS)
    payments["staging"]["columns"][1]["type"] = "varchar"
    raw = _spec(
        [ORDERS, payments],
        [{"source": "payments", "type": "left", "on": [["orders.order_id", "payments.order_id"]]}],
        [("t", "SUM(orders.amount)")],
        ["orders.order_id"],
    )
    fixed = _add_key_columns(raw, _api_snapshot())
    assert fixed["sources"][1]["staging"]["columns"][1]["type"] == "integer"
    assert fixed["sources"][0]["staging"]["columns"][0]["type"] == "integer", "phía Postgres giữ nguyên"


def test_gold_key_columns_lose_the_source_prefix():
    # eval 2026-10-08: unique_key / tests dùng 'customers.customer_id', bị báo 2 lần vẫn lặp lại.
    from src.agents.nodes.codegen import _add_key_columns

    raw = _spec([ORDERS], [], [("revenue", "SUM(orders.amount)")], ["orders.customer_id"])
    raw["transform"] |= {
        "materialization": "incremental",
        "unique_key": ["orders.customer_id"],
        "partition_by": "orders.customer_id",
        "tests": [
            {"column": "orders.customer_id", "test": "unique"},
            {"column": "khong_co.cot_la", "test": "not_null"},
        ],
    }
    transform = _add_key_columns(raw, {})["transform"]
    assert transform["unique_key"] == ["customer_id"] and transform["partition_by"] == "customer_id"
    assert [t["column"] for t in transform["tests"]] == ["customer_id", "khong_co.cot_la"], "cột lạ để cổng báo lỗi"


@pytest.mark.parametrize("extra", ["customer_id", "order_id", "orders.order_id"])
def test_unique_key_of_an_aggregate_is_its_grain(extra):
    # run a7d0d9ca: "doanh thu và số đơn hàng theo ngày", grain ['orders.ordered_at'] nhưng LLM đặt unique_key
    # ['ordered_at', 'order_id'] (cột không có ở gold) và lặp lỗi qua cả 3 lần viết Spec. Mỗi dòng gold là một tổ hợp
    # grain nên khóa duy nhất chỉ có thể là các cột của grain.
    from src.agents.nodes.codegen import _add_key_columns

    orders = _source(
        "orders", "postgres", [("order_id", "integer"), ("ordered_at", "date"), ("amount", "decimal(18,2)")], "order_id"
    )
    raw = _spec(
        [orders],
        [],
        [("total_revenue", "SUM(orders.amount)"), ("order_count", "COUNT(DISTINCT orders.order_id)")],
        ["orders.ordered_at"],
    )
    raw["transform"]["unique_key"] = ["ordered_at", extra]
    fixed = _add_key_columns(raw, {})
    assert fixed["transform"]["unique_key"] == ["ordered_at"]
    assert Spec.model_validate(fixed)


def test_unique_key_left_empty_stays_empty():
    from src.agents.nodes.codegen import _add_key_columns

    raw = _spec([ORDERS], [], [("total", "SUM(orders.amount)")], ["orders.customer_id"])
    assert _add_key_columns(raw, {})["transform"]["unique_key"] == []
