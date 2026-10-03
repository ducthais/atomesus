"""Gộp theo kỳ thời gian (transform.time_grain) và ép timestamptz theo UTC.

Lỗi thật đã gặp: "doanh thu theo tháng" trên Pagila ra 16.974 dòng (gộp theo từng giây), và giờ bị đẩy +7 vì
DuckDB ép timestamptz → timestamp theo múi giờ của máy chạy.
"""

import json
from datetime import date
from decimal import Decimal

import pytest

from src.agents.validate import requested_period, validate_spec
from src.codegen.render import render
from src.policy.loader import load_policy
from src.spec import SPEC_FIXTURES_DIR
from src.spec.models import Spec

SNAPSHOT = {
    "payment": {
        "source_ref": "sales_pg",
        "table": "payment",
        "kind": "postgres",
        "columns": {"payment_id": "integer", "amount": "numeric", "payment_date": "timestamp with time zone"},
        "sample_rows": [],
    }
}


def spec(time_grain=None, date_type="timestamp") -> dict:
    return {
        "spec_version": "1.2",
        "domain": "sales",
        "sources": [
            {
                "id": "payment",
                "kind": "postgres",
                "source_ref": "sales_pg",
                "table": "payment",
                "primary_key": ["payment_id"],
                "cursor_field": None,
                "write_disposition": "replace",
                "target": {"dataset": "bronze", "table": "raw_payment"},
                "staging": {
                    "columns": [
                        {"from": "payment_id", "as": "payment_id", "type": "integer"},
                        {"from": "amount", "as": "amount_vnd", "type": "decimal(18,2)"},
                        {"from": "payment_date", "as": "payment_date", "type": date_type},
                    ],
                    "reject_if": [],
                },
            }
        ],
        "transform": {
            "model_name": "fct_revenue_by_period",
            "from": "payment",
            "joins": [],
            "materialization": "table",
            "grain": ["payment_date"],
            "time_grain": time_grain,
            "metrics": [
                {"name": "revenue", "expr": "SUM(amount_vnd)"},
                {"name": "payment_count", "expr": "COUNT(payment_id)"},
            ],
            "filters": [],
            "tests": [{"column": "payment_date", "test": "unique"}],
        },
        "schedule": {"cron": "0 2 * * *"},
        "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
    }


@pytest.mark.parametrize(
    ("request_text", "unit"),
    [
        ("Doanh thu theo tháng", "month"),
        ("Tính doanh thu và số đơn hàng theo từng ngày đặt hàng", "day"),
        ("số đơn mỗi tuần", "week"),
        ("doanh thu từng quý", "quarter"),
        ("Revenue per month", "month"),
        ("monthly revenue", "month"),
        ("Tổng doanh thu, chạy hằng ngày", None),  # lịch chạy, không phải kỳ gộp
        ("Tính tổng doanh thu", None),
    ],
)
def test_requested_period(request_text, unit):
    assert requested_period(request_text) == unit


@pytest.fixture(scope="module")
def policy():
    return load_policy()


def gate(result):
    return next((g for g in result.gates if g.name == "time_grain"), None)


def test_timestamp_grain_without_bucket_goes_back_to_codegen(policy):
    result = validate_spec(spec(), SNAPSHOT, policy, "Doanh thu theo tháng")
    assert gate(result).result == "fail" and not result.blocked
    assert '"column": "payment_date", "unit": "month"' in result.errors[0]


def test_matching_bucket_passes(policy):
    result = validate_spec(spec({"column": "payment_date", "unit": "month"}), SNAPSHOT, policy, "Doanh thu theo tháng")
    assert result.errors == [] and gate(result).result == "pass"


def test_wrong_unit_is_rejected(policy):
    result = validate_spec(spec({"column": "payment_date", "unit": "day"}), SNAPSHOT, policy, "Doanh thu theo tháng")
    assert "đổi thành 'month'" in result.errors[0]


def test_date_column_already_is_daily(policy):
    result = validate_spec(spec(date_type="date"), SNAPSHOT, policy, "Doanh thu theo ngày")
    assert result.errors == [] and gate(result).result == "pass"


def test_no_period_in_request_skips_gate(policy):
    result = validate_spec(spec(), SNAPSHOT, policy, "Tính tổng doanh thu")
    assert result.errors == [] and gate(result) is None


def test_time_grain_must_be_a_time_column_of_grain():
    bad = spec({"column": "payment_id", "unit": "month"})
    with pytest.raises(ValueError, match="phải là một phần tử của grain"):
        Spec.model_validate(bad)
    bad["transform"]["grain"] = ["payment_id"]
    with pytest.raises(ValueError, match="kiểu date hoặc timestamp"):
        Spec.model_validate(bad)


def test_gold_sql_truncates_the_bucketed_grain(policy):
    files = render(Spec.model_validate(spec({"column": "payment_date", "unit": "month"})), policy)
    gold = files["dbt_project/models/gold/fct_revenue_by_period.sql"]
    assert "cast(date_trunc('month', payment_date) as date) as \"payment_date\"" in gold


def test_fixture_specs_still_valid():
    for path in SPEC_FIXTURES_DIR.glob("spec_*.json"):
        Spec.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ---------- chạy dbt thật ----------


def _gold(tmp_path_factory, policy, timezone):
    pytest.importorskip("dbt.cli.main")
    duckdb = pytest.importorskip("duckdb")
    from src.codegen import sandbox
    from tests.test_codegen.test_staging_dbt import _Settings

    model = Spec.model_validate(spec({"column": "payment_date", "unit": "month"}))
    root = tmp_path_factory.mktemp("sandbox")
    sandbox.prepare_project(root, render(model, policy))
    path = root / "warehouse.duckdb"
    with duckdb.connect(str(path)) as db:
        # Máy chạy ở giờ Việt Nam: không ép UTC thì 31/01 18:00 UTC thành 01/02 01:00 và rơi sang tháng 2.
        db.execute("set TimeZone='Asia/Ho_Chi_Minh'")
        db.execute("create schema bronze")
        db.execute(
            "create table bronze.raw_payment (payment_id bigint, amount decimal(5,2), payment_date timestamptz,"
            " _dlt_id varchar)"
        )
        db.executemany(
            "insert into bronze.raw_payment values (?, ?, cast(? as timestamptz), ?)",
            [
                (1, Decimal("2.99"), "2017-01-24 18:21:56+00", "a"),
                (2, Decimal("4.01"), "2017-01-31 18:00:00+00", "b"),
                (3, Decimal("10.00"), "2017-02-15 09:00:00+00", "c"),
            ],
        )
    settings = _Settings()
    settings.warehouse_timezone = timezone
    env = sandbox._env(root, model, policy, settings)
    results, error = sandbox._t_run(root, env, timeout=300)
    assert error is None, error
    with duckdb.connect(str(path), read_only=True) as db:
        return db.execute('select * from gold."fct_revenue_by_period" order by payment_date').fetchall()


@pytest.fixture(scope="module")
def gold(tmp_path_factory, policy):
    return _gold(tmp_path_factory, policy, "UTC")


def test_dbt_groups_by_month_in_utc(gold):
    assert gold == [(date(2017, 1, 1), Decimal("7.00"), 2), (date(2017, 2, 1), Decimal("10.00"), 1)]


def test_wrong_warehouse_timezone_reproduces_the_shift(tmp_path_factory, policy):
    # Cấu hình cũ (múi giờ máy, UTC+7): giao dịch 31/01 18:00 UTC rơi sang tháng 2. Đối chiếu độc lập bắt lỗi này.
    shifted = _gold(tmp_path_factory, policy, "Asia/Ho_Chi_Minh")
    assert shifted == [(date(2017, 1, 1), Decimal("2.99"), 1), (date(2017, 2, 1), Decimal("14.01"), 2)]
