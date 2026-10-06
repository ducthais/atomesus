"""Ghép một-nhiều: bảng phía "nhiều" được gộp trước theo khóa ghép, dbt chạy thật cho số không cộng trùng."""

from decimal import Decimal

import pytest

from src.codegen import preaggregate
from src.codegen.reference import reference_sql
from src.codegen.render import render
from src.policy.loader import load_policy
from src.spec.models import Spec


def _source(sid, columns, key):
    return {
        "id": sid,
        "kind": "postgres",
        "source_ref": "sales_pg",
        "table": sid,
        "primary_key": [key],
        "cursor_field": None,
        "write_disposition": "replace",
        "target": {"dataset": "bronze", "table": f"raw_{sid}"},
        "staging": {"columns": [{"from": c, "as": c, "type": t} for c, t in columns], "reject_if": []},
    }


def spec(metrics, filters=()):
    return Spec.model_validate(
        {
            "spec_version": "1.2",
            "domain": "sales",
            "sources": [
                _source(
                    "orders",
                    [("order_id", "integer"), ("customer_id", "integer"), ("amount", "decimal(18,2)")],
                    "order_id",
                ),
                _source(
                    "payments",
                    [
                        ("payment_id", "integer"),
                        ("order_id", "integer"),
                        ("amount", "decimal(18,2)"),
                        ("method", "varchar"),
                    ],
                    "payment_id",
                ),
            ],
            "transform": {
                "model_name": "fct_customer_payments",
                "from": "orders",
                "joins": [{"source": "payments", "type": "left", "on": [["orders.order_id", "payments.order_id"]]}],
                "materialization": "table",
                "grain": ["orders.customer_id"],
                "metrics": [{"name": n, "expr": e} for n, e in metrics],
                "filters": list(filters),
                "tests": [],
            },
            "schedule": {"cron": "0 * * * *"},
            "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
        }
    )


METRICS = [
    ("total_order", "SUM(orders.amount)"),
    ("order_count", "COUNT(DISTINCT orders.order_id)"),
    ("total_paid", "SUM(payments.amount)"),
    ("payment_count", "COUNT(payments.payment_id)"),
    ("avg_payment", "AVG(payments.amount)"),
    ("max_payment", "MAX(payments.amount)"),
]


def test_plan_rewrites_only_the_many_side():
    gold = preaggregate.plan(spec(METRICS, ["payments.method <> 'voided'"]))
    assert set(gold.preaggregated) == {"payments"}
    assert gold.metrics["total_order"] == "SUM(orders.amount)", "phía một giữ nguyên"
    assert gold.metrics["total_paid"] == "SUM(payments.__m1)"
    assert gold.metrics["payment_count"] == "COALESCE(SUM(payments.__m2), 0)"
    assert gold.filters == [] and gold.preaggregated["payments"].filters == ["payments.method <> 'voided'"]


@pytest.mark.parametrize(
    ("metrics", "filters"),
    [
        ([("n", "COUNT(*)")], []),  # đếm dòng sau ghép: không đổi nghĩa lặng lẽ
        ([("n", "COUNT(DISTINCT payments.method)")], []),  # distinct trên cột không phải khóa
        ([("x", "SUM(orders.amount + payments.amount)")], []),  # biểu thức lẫn hai bảng
        ([("x", "SUM(payments.amount)")], ["payments.amount > orders.amount"]),  # bộ lọc lẫn hai bảng
    ],
)
def test_cases_that_cannot_be_preaggregated(metrics, filters):
    assert preaggregate.plan(spec(metrics, filters)).preaggregated == {}


def test_reference_sql_uses_the_same_preaggregation():
    model = spec(METRICS)
    sql = reference_sql(
        model,
        load_policy(),
        lambda schema: {
            "orders": {"order_id": "integer", "customer_id": "integer", "amount": "numeric"},
            "payments": {"payment_id": "integer", "order_id": "integer", "amount": "numeric", "method": "text"},
        },
    )
    assert "LEFT JOIN (SELECT payments.order_id, SUM(payments.amount) AS __m1" in sql
    assert 'FROM "stg_payments" AS payments GROUP BY payments.order_id) AS "payments"' in sql


def test_dbt_totals_do_not_double_count(tmp_path):
    pytest.importorskip("dbt.cli.main")
    duckdb = pytest.importorskip("duckdb")
    from src.codegen import sandbox
    from tests.test_codegen.test_staging_dbt import _Settings

    policy, model = load_policy(), spec(METRICS)
    sandbox.prepare_project(tmp_path, render(model, policy))
    path = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("create schema bronze")
        db.execute(
            "create table bronze.raw_orders (order_id bigint, customer_id bigint, amount varchar, _dlt_id varchar)"
        )
        db.execute(
            "create table bronze.raw_payments (payment_id bigint, order_id bigint, amount varchar, method varchar,"
            " _dlt_id varchar)"
        )
        db.executemany(
            "insert into bronze.raw_orders values (?, ?, ?, ?)",
            [(1, 1, "100", "o1"), (2, 1, "200", "o2"), (3, 2, "50", "o3")],
        )
        # Đơn 1 trả hai lần, đơn 2 một lần, đơn 3 chưa trả: không gộp trước thì khách 1 bị cộng đơn 1 hai lần.
        db.executemany(
            "insert into bronze.raw_payments values (?, ?, ?, ?, ?)",
            [(10, 1, "30", "card", "p1"), (11, 1, "70", "cash", "p2"), (12, 2, "200", "card", "p3")],
        )
    env = sandbox._env(tmp_path, model, policy, _Settings())
    _results, error = sandbox._t_run(tmp_path, env, timeout=300)
    assert error is None, error
    with duckdb.connect(str(path), read_only=True) as db:
        rows = db.execute('select * from gold."fct_customer_payments" order by customer_id').fetchall()
    assert rows == [
        (1, Decimal("300.00"), 2, Decimal("300.00"), 3, Decimal("100.00"), Decimal("200.00")),
        (2, Decimal("50.00"), 1, None, 0, None, None),
    ]
