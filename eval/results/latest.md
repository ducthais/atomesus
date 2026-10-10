# Kết quả chấm 15 ca (SPEC §18)

Chạy lúc 2026-10-10 09:37 UTC bằng `python eval/run_eval.py`; số liệu đầy đủ ở `latest.json`.

- **Tổng: 15/15 ca đạt**
- Khớp golden: **6/6** ca hợp lệ (theo loại nguồn: filesystem 1/1, postgres 1/1, rest_api 1/1, đa nguồn 3/3)
- Chạy được ngay lần viết Spec đầu: 6/6; tự sửa Spec trung bình 0.0 lần mỗi ca
- Chặn đúng: **6/6** ca vi phạm (ast_guard 2/2, budget 2/2, semantic 2/2)
- Hỏi lại thay vì đoán: **3/3** ca mơ hồ
- Tiền LLM cả lượt: $0.0155; tổng thời gian 236.1 giây

| Nhóm | Ca | Mong đợi | Kết quả | Đạt | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| Hợp lệ | `valid_filesystem_refunds_by_order` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 24.8 giây | khóa `order_id`, chỉ số refunded→`total_refund_amount`, refund_count→`refund_count` |
| Hợp lệ | `valid_postgres_daily_revenue` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 23.3 giây | khóa `order_date`, chỉ số revenue→`total_revenue` |
| Hợp lệ | `valid_postgres_filesystem_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 33.1 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, refunded→`total_refund_amount` |
| Hợp lệ | `valid_postgres_rest_api_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 34.1 giây | khóa `customer_id`, chỉ số order_count→`order_count`, order_total→`total_order_amount`, paid→`total_payment_amount` |
| Hợp lệ | `valid_rest_api_paid_by_order` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 23.5 giây | khóa `order_id`, chỉ số paid→`total_amount`, payment_count→`payment_count` |
| Hợp lệ | `valid_three_sources_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 3 | 61.6 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, paid→`total_paid_amount`, refunded→`total_refunded_amount` |
| Vi phạm | `violation_ast_forbidden_function` | BLOCKED_AST | BLOCKED_AST (static) | ✅ | 0 | 2 | 7.6 giây | transform.filters[0]: hàm 'regexp_matches' không nằm trong danh sách cho phép |
| Vi phạm | `violation_ast_jinja_injection` | BLOCKED_AST | BLOCKED_AST (static) | ✅ | 0 | 2 | 7.3 giây | Spec có ký tự '{' hoặc '}' (cú pháp Jinja của dbt), không được phép; transform.metrics[revenue].expr: hàm 'env_var' không nằm trong danh sách cho phép |
| Vi phạm | `violation_budget_daily_spend` | BLOCKED_BUDGET | BLOCKED_BUDGET (plan) | ✅ | 0 | 0 | 0.2 giây | Hôm nay đã tiêu 2.0000 USD, chạm trần 2.0 USD/ngày (budgets.yml per_day.max_usd); không gọi LLM nữa tới ngày mai (UTC). |
| Vi phạm | `violation_budget_scan_limit` | BLOCKED_BUDGET | BLOCKED_BUDGET (budget) | ✅ | 0 | 2 | 7.3 giây | Ước tính quét 16,384 byte (orders 8,192 byte, cộng model) vượt budgets.yml per_run.max_bytes_scanned = 1,000 |
| Vi phạm | `violation_semantic_new_customers` | BLOCKED_SEMANTIC_CONFLICT | BLOCKED_SEMANTIC_CONFLICT (resolve) | ✅ | 0 | 1 | 2.6 giây | Phát hiện thực thể `customer` ở 3 domain. Sales định nghĩa là tài khoản đã ký hợp đồng (2 bảng Postgres). Marketing định nghĩa là người để lại email (2 endpoint REST). Rental định nghĩa là khách có tài khoản thuê DVD tại một cửa hàng (1 bảng Postgres). Chưa có định nghĩa chuẩn (conformed_dimensions) |
| Vi phạm | `violation_semantic_revenue_per_customer` | BLOCKED_SEMANTIC_CONFLICT | BLOCKED_SEMANTIC_CONFLICT (resolve) | ✅ | 0 | 1 | 2.5 giây | Phát hiện thực thể `customer` ở 3 domain. Sales định nghĩa là tài khoản đã ký hợp đồng (2 bảng Postgres). Marketing định nghĩa là người để lại email (2 endpoint REST). Rental định nghĩa là khách có tài khoản thuê DVD tại một cửa hàng (1 bảng Postgres). Chưa có định nghĩa chuẩn (conformed_dimensions) |
| Mơ hồ | `ambiguous_profit_margin` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 1.9 giây | Các bảng đã chọn không có cột nào thể hiện: biên lợi nhuận. Cần thêm bảng nào (hoặc nói rõ dùng cột nào)? |
| Mơ hồ | `ambiguous_revenue_target` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 2.0 giây | Các bảng đã chọn không có cột nào thể hiện: chỉ tiêu. Cần thêm bảng nào (hoặc nói rõ dùng cột nào)? |
| Mơ hồ | `ambiguous_sales_report` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 4.3 giây | Yêu cầu chưa nêu cần đo chỉ số nào. Bạn muốn đo gì (ví dụ doanh thu, số đơn, số tiền hoàn) và theo độ hạt nào (ngày, tháng, khách hàng, đơn hàng)? |
