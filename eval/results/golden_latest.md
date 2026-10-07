# Kết quả chấm golden (ca hợp lệ)

Chạy lúc 2026-10-07 16:41 UTC bằng `python eval/run_eval.py`; số liệu đầy đủ ở `golden_latest.json`.

- **Khớp golden: 5/6 ca**; khớp ngay lần viết Spec đầu: 4/6
- Theo loại nguồn: filesystem 1/1, postgres 1/1, rest_api 1/1, đa nguồn 2/3
- Tiền LLM cả lượt: $0.0158

| Ca | Nguồn | Trạng thái | Khớp | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|
| `valid_filesystem_refunds_by_order` | filesystem | READY_FOR_REVIEW | ✅ | 0 | 2 | 22.1 giây | khóa `order_id`, chỉ số refunded→`total_refund_amount`, refund_count→`refund_count` |
| `valid_postgres_daily_revenue` | postgres | READY_FOR_REVIEW | ✅ | 0 | 2 | 21.6 giây | khóa `order_date`, chỉ số revenue→`total_revenue` |
| `valid_postgres_filesystem_by_customer` | postgres + filesystem | NEEDS_HUMAN | ❌ | 3 | 4 | 36.1 giây | run dừng ở NEEDS_HUMAN (static); Spec vẫn sai sau 2 lần sửa (budgets.yml max_spec_attempts): <gốc>: Value error, unique_key/partition_by/tests dùng cột không có ở gold: ['customers.customer_id'] |
| `valid_postgres_rest_api_by_customer` | postgres + rest_api | READY_FOR_REVIEW | ✅ | 0 | 2 | 34.3 giây | khóa `customer_id`, chỉ số order_count→`number_of_orders`, order_total→`total_order_amount`, paid→`total_payment_amount` |
| `valid_rest_api_paid_by_order` | rest_api | READY_FOR_REVIEW | ✅ | 0 | 2 | 27.1 giây | khóa `order_id`, chỉ số paid→`total_amount`, payment_count→`payment_count` |
| `valid_three_sources_by_customer` | postgres + rest_api + filesystem | READY_FOR_REVIEW | ✅ | 2 | 4 | 67.5 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, paid→`total_paid_amount`, refunded→`total_refunded_amount` |
