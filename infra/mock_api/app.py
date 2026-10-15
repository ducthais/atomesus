"""Mock REST API làm nguồn `rest_api` cho demo và test (SPEC §12.1, §18): chỉ đọc, dữ liệu cố định.

Mỗi domain một ứng dụng con với mô tả OpenAPI riêng, khớp `openapi` trong policy/domains.yml:
- `/sales/openapi.json`: `GET /sales/payments`, thanh toán cho 30 đơn của `sales_raw.orders` (Postgres).
- `/marketing/openapi.json`: `GET /marketing/leads`, `GET /marketing/contacts`.
- `/reviews/openapi.json`: `GET /reviews/feedback`, phản hồi khách gửi sau khi trả DVD ở Pagila (domain `rental`), khóa
  chung `rental_id`, `customer_id`, `film_id` với bảng `pagila.*`. Sinh từ seed Pagila bằng scripts/build_review_data.py
  (4.764 bản ghi: 47 gửi trùng `feedback_id`, 24 `rating = "N/A"`, 14 trỏ tới lượt thuê không có).

Phân trang `page` / `page_size`, bản ghi nằm ở `data`; trang quá cuối trả `data: []` (dlt dừng ở đó).
Khóa chính khai báo bằng `x-primary-key` trong schema bản ghi (OpenAPI không có khái niệm khóa).

Dữ liệu bẩn có chủ đích ở `payments` (122 bản ghi), giữ dưới trần `objectives.yml: sales.quality.max_reject_pct` (1%):
- 2 bản ghi lặp lại nguyên `payment_id` (dedupe / merge phải gộp),
- 2 khoản trả bằng USD (staging đổi về VND theo `currency.rates`),
- 1 khoản có `amount = "N/A"` (ép kiểu thất bại → bảng rejected).

Chạy: `make mock-api` (cổng 8100, khớp PIPELINE_MOCK_API_URL mặc định). `POST /sales/_admin/grow` (ẩn khỏi OpenAPI)
thêm thanh toán cho đơn mới, dùng bởi scripts/grow_data.py để làm dữ liệu to dần.
"""

import gzip
import json
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field

PAGE_SIZE = 20
ORDERS = 30
# Không đặt ở data/: .gitignore và .dockerignore bỏ mọi thư mục tên data/.
FEEDBACK_FILE = Path(__file__).resolve().parent / "fixtures" / "rental_feedback.json.gz"


class Payment(BaseModel):
    model_config = ConfigDict(json_schema_extra={"x-primary-key": ["payment_id"]})
    payment_id: int
    order_id: int
    amount: str  # chuỗi như nguồn thật hay trả; có giá trị bẩn
    currency: str
    paid_at: str = Field(json_schema_extra={"format": "date-time"})  # ISO 8601, UTC
    method: str


class Lead(BaseModel):
    model_config = ConfigDict(json_schema_extra={"x-primary-key": ["lead_id"]})
    lead_id: int
    email: str
    campaign: str
    created_at: str = Field(json_schema_extra={"format": "date-time"})


class Contact(BaseModel):
    model_config = ConfigDict(json_schema_extra={"x-primary-key": ["contact_id"]})
    contact_id: int
    lead_id: int
    full_name: str
    phone: str


class Feedback(BaseModel):
    model_config = ConfigDict(json_schema_extra={"x-primary-key": ["feedback_id"]})
    feedback_id: str
    rental_id: int  # → pagila.rental.rental_id
    customer_id: int  # → pagila.customer.customer_id (khách của lượt thuê đó)
    film_id: int  # → pagila.film.film_id
    rating: str  # "1".."5" như nguồn thật hay trả; có giá trị bẩn "N/A"
    would_recommend: bool
    comment: str
    submitted_at: str = Field(json_schema_extra={"format": "date-time"})  # ISO 8601, UTC, sau return_date


class FeedbackPage(BaseModel):
    data: list[Feedback]
    page: int
    page_size: int
    total: int


class PaymentPage(BaseModel):
    data: list[Payment]
    page: int
    page_size: int
    total: int


class LeadPage(BaseModel):
    data: list[Lead]
    page: int
    page_size: int
    total: int


class ContactPage(BaseModel):
    data: list[Contact]
    page: int
    page_size: int
    total: int


def _payments() -> list[dict]:
    """Mỗi đơn i (tiền i*1000 VND) trả góp bốn lần: 10%, 20%, 30%, 40%."""
    rows = []
    for order in range(1, ORDERS + 1):
        for part, share, hour in ((1, 100, 9), (2, 200, 11), (3, 300, 14), (4, 400, 16)):
            payment_id = (order - 1) * 4 + part
            rows.append(
                {
                    "payment_id": payment_id,
                    "order_id": order,
                    "amount": str(order * share),
                    "currency": "VND",
                    "paid_at": f"2026-01-{min(order + part - 1, 31):02d}T{hour:02d}:00:00Z",
                    "method": "card" if payment_id % 3 else "bank_transfer",
                }
            )
    rows[6] |= {"amount": "1.50", "currency": "USD"}  # payment 7
    rows[18] |= {"amount": "2.00", "currency": "USD"}  # payment 19
    rows[41]["amount"] = "N/A"  # payment 42
    duplicates = [dict(rows[10]), dict(rows[33])]  # gửi lại payment 11 và 34
    return rows[:25] + [duplicates[0]] + rows[25:50] + [duplicates[1]] + rows[50:]


PAYMENTS = _payments()
LEADS = [
    {
        "lead_id": i,
        "email": f"lead{i}@example.com",
        "campaign": ("tet_2026", "summer_sale")[i % 2],
        "created_at": f"2026-02-{i:02d}T08:00:00Z",
    }
    for i in range(1, 16)
]
CONTACTS = [
    {"contact_id": i, "lead_id": i * 2, "full_name": f"Contact {i}", "phone": f"09{i:08d}"} for i in range(1, 8)
]
with gzip.open(FEEDBACK_FILE, "rt", encoding="utf-8") as _file:
    FEEDBACK = json.load(_file)

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _page(rows: list[dict], page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    return {"data": rows[start : start + page_size], "page": page, "page_size": page_size, "total": len(rows)}


sales = FastAPI(title="Mock API — sales", version="1.0")
marketing = FastAPI(title="Mock API — marketing", version="1.0")
reviews = FastAPI(title="Mock API — reviews", version="1.0")


@sales.get("/payments", response_model=PaymentPage)
def payments(page: Page = 1, page_size: PageSize = PAGE_SIZE) -> dict:
    return _page(PAYMENTS, page, page_size)


@sales.post("/_admin/grow", include_in_schema=False)
def grow(first_order: Annotated[int, Query(ge=1)], orders: Annotated[int, Query(ge=1, le=1_000_000)]) -> dict:
    """Thêm 4 khoản thanh toán cho mỗi đơn mới (scripts/grow_data.py). Chỉ trong bộ nhớ; ẩn khỏi OpenAPI."""
    next_id = max(p["payment_id"] for p in PAYMENTS) + 1
    added = []
    for order in range(first_order, first_order + orders):
        for part, share in ((1, 100), (2, 200), (3, 300), (4, 400)):
            added.append(
                {
                    "payment_id": next_id + len(added),
                    "order_id": order,
                    "amount": str(order % 1000 * share),
                    "currency": "VND",
                    "paid_at": f"2026-03-{1 + order % 28:02d}T{8 + part:02d}:00:00Z",
                    "method": "card" if part % 2 else "bank_transfer",
                }
            )
    PAYMENTS.extend(added)
    return {"payments": len(added), "total": len(PAYMENTS)}


@marketing.get("/leads", response_model=LeadPage)
def leads(page: Page = 1, page_size: PageSize = PAGE_SIZE) -> dict:
    return _page(LEADS, page, page_size)


@marketing.get("/contacts", response_model=ContactPage)
def contacts(page: Page = 1, page_size: PageSize = PAGE_SIZE) -> dict:
    return _page(CONTACTS, page, page_size)


@reviews.get("/feedback", response_model=FeedbackPage)
def feedback(page: Page = 1, page_size: PageSize = PAGE_SIZE) -> dict:
    return _page(FEEDBACK, page, page_size)


app = FastAPI(title="Mock API", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/sales", sales)
app.mount("/marketing", marketing)
app.mount("/reviews", reviews)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
