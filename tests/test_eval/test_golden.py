"""Kết quả đúng của 6 ca hợp lệ (eval/golden): sinh lại khớp file đã ghi, và khớp các số đã kiểm bằng pipeline thật."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("golden_build", ROOT / "eval" / "golden" / "build.py")
golden_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden_build)
CASES = {case["id"]: case for case in golden_build.build()}


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_committed_file_matches_a_fresh_build(case_id):
    saved = json.loads((ROOT / "eval" / "golden" / f"{case_id}.json").read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(CASES[case_id], ensure_ascii=False)), "chạy lại eval/golden/build.py"


def test_one_case_per_single_source_kind_and_three_multi_source():
    kinds = sorted(tuple(c["source_kinds"]) for c in CASES.values())
    assert [k for k in kinds if len(k) == 1] == [("filesystem",), ("postgres",), ("rest_api",)]
    assert len([k for k in kinds if len(k) > 1]) == 3


def _row(case_id, **key):
    return next(r["values"] for r in CASES[case_id]["rows"] if r["key"] == key)


def test_numbers_match_the_real_pipeline_runs():
    # Run thật 2aa4ed56 / 7f3d8419 (Postgres + API theo khách), 81048089 sửa lại (API theo đơn), test_files_sandbox (CSV).
    assert _row("valid_postgres_rest_api_by_customer", customer_id=1) == {
        "order_count": 3,
        "order_total": "33000.00",
        "paid": "30800.00",
    }
    assert _row("valid_postgres_rest_api_by_customer", customer_id=5)["paid"] == "94300.00"
    assert _row("valid_rest_api_paid_by_order", order_id=2) == {"paid": "39500.00", "payment_count": 4}
    assert _row("valid_rest_api_paid_by_order", order_id=11)["payment_count"] == 3, "khoản N/A bị tách"
    assert CASES["valid_filesystem_refunds_by_order"]["totals"] == {"refunded": "79120.00", "refund_count": 12}
    assert CASES["valid_postgres_daily_revenue"]["totals"] == {"revenue": "465000.00"}
    no_refund = [r["key"]["customer_id"] for r in CASES["valid_postgres_filesystem_by_customer"]["rows"]
                 if r["values"]["refunded"] == "0.00"]  # fmt: skip
    assert no_refund == [1, 3, 6]
