# Kết quả chấm golden (ca hợp lệ)

Chạy lúc 2026-10-08 03:11 UTC bằng `python eval/run_eval.py`; số liệu đầy đủ ở `golden_latest.json`.

- **Khớp golden: 6/6 ca**; khớp ngay lần viết Spec đầu: 4/6
- Theo loại nguồn: filesystem 1/1, postgres 1/1, rest_api 1/1, đa nguồn 3/3
- Tiền LLM cả lượt: $0.0117

| Ca | Nguồn | Trạng thái | Khớp | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|
| `valid_filesystem_refunds_by_order` | filesystem | READY_FOR_REVIEW | ✅ | 0 | 2 | 24.0 giây | khóa `order_id`, chỉ số refunded→`total_refund_amount`, refund_count→`refund_count` |
| `valid_postgres_daily_revenue` | postgres | READY_FOR_REVIEW | ✅ | 1 | 3 | 29.4 giây | khóa `ordered_at`, chỉ số revenue→`total_revenue` |
| `valid_postgres_filesystem_by_customer` | postgres + filesystem | READY_FOR_REVIEW | ✅ | 1 | 3 | 49.1 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, refunded→`total_refund_amount` |
| `valid_postgres_rest_api_by_customer` | postgres + rest_api | READY_FOR_REVIEW | ✅ | 0 | 2 | 42.6 giây | khóa `customer_id`, chỉ số order_count→`order_count`, order_total→`total_order_amount`, paid→`total_payment_amount` |
| `valid_rest_api_paid_by_order` | rest_api | READY_FOR_REVIEW | ✅ | 0 | 2 | 35.2 giây | khóa `order_id`, chỉ số paid→`total_amount`, payment_count→`payment_count` |
| `valid_three_sources_by_customer` | postgres + rest_api + filesystem | READY_FOR_REVIEW | ✅ | 0 | 2 | 46.4 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, paid→`total_paid_amount`, refunded→`total_refunded_amount` |
