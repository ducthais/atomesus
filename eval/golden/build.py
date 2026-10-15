"""Sinh kết quả đúng (golden) cho 7 ca hợp lệ của bộ đánh giá (SPEC §18): tính thẳng từ dữ liệu gốc, không qua pipeline.

6 ca của SPEC §18.2 trên domain sales, thêm 1 ca domain rental (pagila_pg + review_api). Nguồn số liệu, đọc độc lập
với dlt / DuckDB / dbt / agent:
- Postgres `sales_raw.orders`, `customers`: công thức của infra/postgres/init/001_seed.sql (đơn i thuộc khách
  ((i - 1) % 10) + 1, tiền i * 1000 VND, ngày 2026-01-01 + (i - 1)); kiểu ghi bẩn không đổi giá trị thật;
- REST API `payments`: danh sách seed trong infra/mock_api/app.py (trước khi scripts/grow_data.py thêm);
- CSV `refunds`: các file seed trong infra/landing/sales/refunds/ (bỏ qua refunds_grow_*.csv);
- Postgres `pagila.customer`, `rental`: khối COPY của infra/postgres/init/003_pagila.sql.gz (scripts/build_review_data.py;
  mọi khách đều có lượt thuê nên số khách theo rental bằng bảng customer: 599);
- REST API `feedback`: file infra/mock_api/fixtures/rental_feedback.json.gz mà mock API trả.
Luật làm sạch giống staging: bản ghi trùng khóa giữ bản đầu, số tiền không đọc được thì bỏ dòng, tiền đổi về VND theo
policy/domains.yml. Ghép một-nhiều cộng theo từng bảng trước rồi mới ghép (không cộng trùng).

`key` là các cột khóa thay thế nhau: bảng gold khớp **một** trong số đó là đủ (gộp theo khách hàng thì nhận cả
`customer_id` lẫn `customer_name`, vì cả hai đều đúng ý yêu cầu "theo từng khách hàng").

Chạy: python eval/golden/build.py  → ghi eval/golden/<id>.json. tests/test_eval/test_golden.py kiểm tra file đã ghi
khớp với lần sinh lại và khớp các con số đã kiểm bằng pipeline thật.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infra.mock_api.app import FEEDBACK, _payments  # noqa: E402
from scripts.build_review_data import read_dump  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402

OUT = Path(__file__).resolve().parent
ORDERS, CUSTOMERS = 30, 10
SALES_PG = {"source_ref": "sales_pg", "tables": ["orders", "customers"]}
SALES_API = {"source_ref": "sales_api", "tables": ["payments"]}
LANDING = {"source_ref": "landing_sales", "tables": ["refunds"]}
PAGILA = {"source_ref": "pagila_pg", "tables": ["customer", "rental"]}
REVIEW_API = {"source_ref": "review_api", "tables": ["feedback"]}


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _to_vnd(amount: str | None, currency: str | None, rates: dict[str, float]) -> Decimal | None:
    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError):
        return None  # staging tách dòng ép kiểu lỗi
    rate = rates.get(str(currency).upper())
    return None if rate is None else value * Decimal(str(rate))


def orders() -> list[dict]:
    return [
        {
            "order_id": i,
            "customer_id": (i - 1) % CUSTOMERS + 1,
            "amount": Decimal(i * 1000),
            "day": date(2026, 1, 1) + timedelta(days=i - 1),
        }
        for i in range(1, ORDERS + 1)
    ]


def _dedupe(rows: list[dict], key: str) -> list[dict]:
    seen, kept = set(), []
    for row in rows:
        if row[key] not in seen:
            seen.add(row[key])
            kept.append(row)
    return kept


def payments(rates) -> list[dict]:
    kept = []
    for row in _dedupe(_payments(), "payment_id"):
        vnd = _to_vnd(row["amount"], row["currency"], rates)
        if vnd is not None:
            kept.append({"payment_id": row["payment_id"], "order_id": row["order_id"], "vnd": vnd})
    return kept


def refunds(rates) -> list[dict]:
    rows = []
    for path in sorted((ROOT / "infra" / "landing" / "sales" / "refunds").glob("refunds_2026_*.csv")):
        with path.open(encoding="utf-8", newline="") as handle:
            rows += list(csv.DictReader(handle))
    kept = []
    for row in _dedupe(rows, "refund_id"):
        vnd = _to_vnd(row["amount"], row["currency"], rates)
        if vnd is not None:
            kept.append({"refund_id": int(row["refund_id"]), "order_id": int(row["order_id"]), "vnd": vnd})
    return kept


def feedback() -> list[dict]:
    """Phản hồi sau staging: trùng `feedback_id` giữ bản đầu, `rating` không ép được số nguyên ("N/A") thì bỏ dòng."""
    return [
        {"customer_id": row["customer_id"], "rating": int(row["rating"])}
        for row in _dedupe(FEEDBACK, "feedback_id")
        if row["rating"].isdigit()
    ]


def _by(rows: list[dict], key, value) -> dict:
    out: dict = defaultdict(Decimal)
    for row in rows:
        out[key(row)] += value(row)
    return out


def _count(rows: list[dict], key) -> dict:
    out: dict = defaultdict(int)
    for row in rows:
        out[key(row)] += 1
    return out


def _case(case_id, kinds, request, sources, key, measures, rows, notes) -> dict:
    totals = {
        m: (_money(sum(Decimal(r["values"][m]) for r in rows if r["values"][m] is not None)) if "count" not in m
            else sum(r["values"][m] or 0 for r in rows))
        for m in measures
    }  # fmt: skip
    return {
        "id": case_id,
        "source_kinds": kinds,
        "request": request,
        "sources": sources,
        "key": key,
        "measures": measures,
        "rows": sorted(rows, key=lambda r: list(r["key"].values())),
        "totals": totals,
        "notes": notes,
    }


def build() -> list[dict]:
    rates = load_policy().domains.domains["sales"].currency.rates
    o, p, r = orders(), payments(rates), refunds(rates)
    customer_of = {x["order_id"]: x["customer_id"] for x in o}
    cases = []

    revenue = _by(o, lambda x: x["day"], lambda x: x["amount"])
    cases.append(_case(
        "valid_postgres_daily_revenue", ["postgres"], "Doanh thu theo ngày", [{"source_ref": "sales_pg", "tables": ["orders"]}],
        ["day"], {"revenue": "tổng tiền đơn (VND)"},
        [{"key": {"day": d.isoformat()}, "values": {"revenue": _money(v)}} for d, v in revenue.items()],
        "Ngày ghi ba kiểu (YYYY-MM-DD, DD/MM/YYYY, YYYY/MM/DD); đọc thiếu kiểu nào là thiếu ngày đó.",
    ))  # fmt: skip

    paid, paid_count = _by(p, lambda x: x["order_id"], lambda x: x["vnd"]), _count(p, lambda x: x["order_id"])
    cases.append(_case(
        "valid_rest_api_paid_by_order", ["rest_api"],
        "Tổng tiền đã thanh toán và số lần thanh toán của từng đơn hàng", [SALES_API],
        ["order_id"], {"paid": "tổng thanh toán đã đổi về VND", "payment_count": "số khoản thanh toán"},
        [{"key": {"order_id": k}, "values": {"paid": _money(paid[k]), "payment_count": paid_count[k]}} for k in paid],
        "2 bản ghi gửi lặp tính một lần; 2 khoản USD đổi theo tỷ giá 25400; khoản 42 có amount N/A bị tách.",
    ))  # fmt: skip

    refunded, refund_count = _by(r, lambda x: x["order_id"], lambda x: x["vnd"]), _count(r, lambda x: x["order_id"])
    cases.append(_case(
        "valid_filesystem_refunds_by_order", ["filesystem"], "Tổng tiền hoàn và số khoản hoàn của từng đơn hàng", [LANDING],
        ["order_id"], {"refunded": "tổng hoàn tiền đã đổi về VND", "refund_count": "số khoản hoàn"},
        [{"key": {"order_id": k}, "values": {"refunded": _money(refunded[k]), "refund_count": refund_count[k]}}
         for k in refunded],
        "Hai file theo tháng ghi ngày khác kiểu; khoản hoàn 7 xuất lặp ở cả hai file; 3 khoản USD.",
    ))  # fmt: skip

    order_total = _by(o, lambda x: x["customer_id"], lambda x: x["amount"])
    order_count = _count(o, lambda x: x["customer_id"])
    paid_by_customer = _by(p, lambda x: customer_of[x["order_id"]], lambda x: x["vnd"])
    refunded_by_customer = _by(r, lambda x: customer_of[x["order_id"]], lambda x: x["vnd"])
    customers = range(1, CUSTOMERS + 1)
    zero = Decimal(0)

    cases.append(_case(
        "valid_postgres_rest_api_by_customer", ["postgres", "rest_api"],
        "Với từng khách hàng: số đơn hàng, tổng tiền đơn hàng và tổng tiền đã thanh toán", [SALES_PG, SALES_API],
        ["customer_id", "customer_name"],
        {"order_count": "số đơn", "order_total": "tổng tiền đơn (VND)", "paid": "tổng đã thanh toán (VND)"},
        [{"key": {"customer_id": c, "customer_name": f"Demo customer {c}"}, "values": {"order_count": order_count[c], "order_total": _money(order_total[c]),
                                                "paid": _money(paid_by_customer.get(c, zero))}} for c in customers],
        "Ghép một-nhiều đơn → thanh toán: tổng tiền đơn không được cộng trùng theo số khoản thanh toán.",
    ))  # fmt: skip

    cases.append(_case(
        "valid_postgres_filesystem_by_customer", ["postgres", "filesystem"],
        "Theo từng khách hàng: tổng tiền đơn hàng và tổng tiền đã hoàn", [SALES_PG, LANDING],
        ["customer_id", "customer_name"], {"order_total": "tổng tiền đơn (VND)", "refunded": "tổng đã hoàn (VND)"},
        [{"key": {"customer_id": c, "customer_name": f"Demo customer {c}"}, "values": {"order_total": _money(order_total[c]),
                                                "refunded": _money(refunded_by_customer.get(c, zero))}}
         for c in customers],
        "Khách 1, 3, 6 không có khoản hoàn nào: refunded = 0 (LEFT JOIN, COALESCE) hoặc NULL đều chấp nhận.",
    ))  # fmt: skip

    cases.append(_case(
        "valid_three_sources_by_customer", ["postgres", "rest_api", "filesystem"],
        "Theo từng khách hàng: tổng tiền đơn hàng, tổng đã thanh toán và tổng đã hoàn", [SALES_PG, SALES_API, LANDING],
        ["customer_id", "customer_name"],
        {"order_total": "tổng tiền đơn (VND)", "paid": "tổng đã thanh toán (VND)", "refunded": "tổng đã hoàn (VND)"},
        [{"key": {"customer_id": c, "customer_name": f"Demo customer {c}"}, "values": {"order_total": _money(order_total[c]),
                                                "paid": _money(paid_by_customer.get(c, zero)),
                                                "refunded": _money(refunded_by_customer.get(c, zero))}}
         for c in customers],
        "Hai ghép một-nhiều cùng lúc (đơn → thanh toán, đơn → hoàn): mỗi bảng phía nhiều phải gộp trước.",
    ))  # fmt: skip

    cases.append(_review_case())
    return cases


def _review_case() -> dict:
    """Nguồn review_api (domain rental): ghép với pagila.rental qua khóa chung `customer_id`."""
    rentals, _films = read_dump()
    rented = _count(rentals, lambda x: x["customer_id"])
    reviews = feedback()
    review_count = _count(reviews, lambda x: x["customer_id"])
    rating_sum = _by(reviews, lambda x: x["customer_id"], lambda x: Decimal(x["rating"]))
    rows = [
        {"key": {"customer_id": c},
         "values": {"rental_count": rented[c], "review_count": review_count.get(c, 0),
                    "avg_rating": str((rating_sum[c] / review_count[c] if c in review_count else Decimal(0))
                                      .quantize(Decimal("0.0001")))}}
        for c in rented
    ]  # fmt: skip
    case = _case(
        "valid_postgres_rest_api_reviews_by_customer", ["postgres", "rest_api"],
        "Với từng khách hàng: số lượt thuê DVD, số phản hồi khách đã gửi và điểm đánh giá trung bình của các phản hồi đó",
        [PAGILA, REVIEW_API], ["customer_id"],
        {"rental_count": "số lượt thuê", "review_count": "số phản hồi hợp lệ", "avg_rating": "điểm trung bình (1–5)"},
        rows,
        "Phản hồi gộp theo customer_id của chính phản hồi (14 phản hồi trỏ tới rental_id không có vẫn tính); "
        "47 bản gửi trùng feedback_id tính một lần; 24 phản hồi rating N/A bị tách, không vào số phản hồi lẫn điểm. "
        "Lấy customer (một dòng mỗi khách) làm bảng chính, rental và feedback đều là phía nhiều nên phải gộp trước theo "
        "customer_id (ghép thẳng rental với feedback theo customer_id là nhiều-nhiều, nhân số lượt thuê lẫn số phản hồi). "
        "Khách 413 chỉ có một phản hồi N/A: review_count = 0, avg_rating NULL hoặc 0 đều chấp nhận (LEFT JOIN).",
    )  # fmt: skip
    overall = sum(r["rating"] for r in reviews) / Decimal(len(reviews))
    return {**case, "totals": {**case["totals"], "avg_rating": str(overall.quantize(Decimal("0.0001")))}}


def main() -> int:
    for case in build():
        (OUT / f"{case['id']}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{case['id']}: {len(case['rows'])} dòng, tổng {case['totals']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
