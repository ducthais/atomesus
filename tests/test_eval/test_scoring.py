"""So khớp bảng gold với golden trong eval/run_eval.py: ghép cột theo giá trị vì tên cột do LLM đặt."""

import importlib.util
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)


def _case(case_id: str) -> dict:
    return json.loads((ROOT / "eval" / "golden" / f"{case_id}.json").read_text(encoding="utf-8"))


def _gold_from(case: dict, key: str, rename: dict[str, str], transform=lambda m, v: v) -> list[dict]:
    """Dựng bảng gold "như LLM viết" từ golden: tên cột khác, kiểu khác (Decimal, date)."""
    return [
        {key: row["key"][key], **{rename[m]: transform(m, v) for m, v in row["values"].items()}} for row in case["rows"]
    ]


def test_columns_are_matched_by_value_not_name():
    case = _case("valid_postgres_rest_api_by_customer")
    gold = _gold_from(
        case,
        "customer_name",
        {"order_count": "number_of_orders", "order_total": "total_order_amount", "paid": "total_payment_amount"},
        lambda m, v: Decimal(str(v)),
    )
    result = run_eval.match_case(case, gold)
    assert result["matched"] and result["key"] == {"golden": "customer_name", "gold": "customer_name"}
    assert result["measures"] == {
        "order_count": "number_of_orders",
        "order_total": "total_order_amount",
        "paid": "total_payment_amount",
    }


def test_dates_and_int_like_keys_are_normalized():
    case = _case("valid_postgres_daily_revenue")
    gold = [
        {"order_date": date.fromisoformat(r["key"]["day"]), "total_revenue": Decimal(r["values"]["revenue"])}
        for r in case["rows"]
    ]
    assert run_eval.match_case(case, gold)["matched"]
    assert run_eval.key_text(1.0) == "1" and run_eval.key_text(Decimal("12")) == "12"


def test_wrong_totals_are_reported():
    # Lỗi đã gặp ở run b8d45355: tổng tiền đơn bị nhân theo số khoản thanh toán.
    case = _case("valid_postgres_rest_api_by_customer")
    gold = _gold_from(case, "customer_id", {"order_count": "n", "order_total": "total", "paid": "paid"},
                      lambda m, v: Decimal(str(v)) * (4 if m == "order_total" else 1))  # fmt: skip
    result = run_eval.match_case(case, gold)
    assert not result["matched"] and result["measures"]["order_total"] is None
    assert any("order_total" in p for p in result["problems"])


def test_missing_rows_and_null_for_zero():
    case = _case("valid_postgres_filesystem_by_customer")
    gold = _gold_from(case, "customer_id", {"order_total": "orders", "refunded": "refunds"},
                      lambda m, v: None if m == "refunded" and v == "0.00" else Decimal(v))  # fmt: skip
    assert run_eval.match_case(case, gold)["matched"], "LEFT JOIN không có khoản hoàn: NULL chấp nhận như 0"
    assert not run_eval.match_case(case, gold[:-1])["matched"]


def test_summary_states_denominators_by_source_kind():
    results = [
        {"source_kinds": ["postgres"], "matched": True, "spec_attempts": 0, "llm_usd": 0.001},
        {"source_kinds": ["rest_api"], "matched": False, "spec_attempts": 1, "llm_usd": 0.002},
        {"source_kinds": ["postgres", "rest_api"], "matched": True, "spec_attempts": 1, "llm_usd": 0.003},
    ]
    assert run_eval.summarize(results) == {
        "matched": "2/3",
        "first_try": "1/3",
        "by_kind": {"postgres": "1/1", "rest_api": "0/1", "đa nguồn": "1/1"},
        "llm_usd": 0.006,
    }
