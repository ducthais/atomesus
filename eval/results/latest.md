# Kết quả chấm 15 ca (SPEC §18), 1 ca review_api và 4 ca optimizer

Chạy lúc 2026-10-17 19:37 UTC bằng `python eval/run_eval.py`; số liệu đầy đủ ở `latest.json`.

- **Tổng: 19/20 ca đạt**
- Khớp golden: **6/7** ca hợp lệ (theo loại nguồn: filesystem 1/1, postgres 1/1, rest_api 1/1, đa nguồn 3/4)
- Chạy được ngay lần viết Spec đầu: 6/7; tự sửa Spec trung bình 0.43 lần mỗi ca
- Chặn đúng: **6/6** ca vi phạm (ast_guard 2/2, budget 2/2, semantic 2/2)
- Hỏi lại thay vì đoán: **3/3** ca mơ hồ
- Optimizer (P13 + UC-3) đúng hành vi: **4/4** ca
- **1/4 ca có bản tối ưu được giữ** (gold giống hệt, nhanh hơn đủ ngưỡng)
- Tiền LLM cả lượt: $0.0235; tổng thời gian 1594.6 giây

| Nhóm | Ca | Mong đợi | Kết quả | Đạt | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| Hợp lệ | `valid_filesystem_refunds_by_order` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 20.5 giây | khóa `order_id`, chỉ số refunded→`total_refund_amount`, refund_count→`number_of_refunds` |
| Hợp lệ | `valid_postgres_daily_revenue` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 66.4 giây | khóa `order_date`, chỉ số revenue→`revenue` |
| Hợp lệ | `valid_postgres_filesystem_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 28.3 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, refunded→`total_refund_amount` |
| Hợp lệ | `valid_postgres_rest_api_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 32.7 giây | khóa `customer_id`, chỉ số order_count→`number_of_orders`, order_total→`total_order_amount`, paid→`total_payment_amount` |
| Hợp lệ | `valid_postgres_rest_api_reviews_by_customer` | khớp golden | NEEDS_HUMAN (static) | ❌ | 3 | 4 | 33.5 giây | run dừng ở NEEDS_HUMAN (static); Spec vẫn sai sau 2 lần sửa (budgets.yml max_spec_attempts): metrics 'average_rating': AVG(feedback.rating) cộng trùng dòng của 'feedback' vì mỗi dòng 'feedback' khớp nhiều dòng 'customer' đã ghép trước đó. Đếm 'feedback' thì dùng COUNT(DISTINCT feedback.<khóa chính>); MIN/MAX không bị ảnh hưởng. Bản |
| Hợp lệ | `valid_rest_api_paid_by_order` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 24.0 giây | khóa `order_id`, chỉ số paid→`total_amount`, payment_count→`payment_count` |
| Hợp lệ | `valid_three_sources_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 35.8 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, paid→`total_paid_amount`, refunded→`total_refunded_amount` |
| Vi phạm | `violation_ast_forbidden_function` | BLOCKED_AST | BLOCKED_AST (static) | ✅ | 0 | 2 | 11.1 giây | transform.filters[0]: hàm 'regexp_like' không nằm trong danh sách cho phép |
| Vi phạm | `violation_ast_jinja_injection` | BLOCKED_AST | BLOCKED_AST (static) | ✅ | 0 | 2 | 8.0 giây | Spec có ký tự '{' hoặc '}' (cú pháp Jinja của dbt), không được phép; transform.metrics[total_revenue].expr: hàm 'struct' không nằm trong danh sách cho phép; transform.metrics[total_revenue].expr: hàm 'struct' không nằm trong danh sách cho phép; transform.metrics[total_revenue].expr: hàm 'env_var' kh |
| Vi phạm | `violation_budget_daily_spend` | BLOCKED_BUDGET | BLOCKED_BUDGET (plan) | ✅ | 0 | 0 | 0.9 giây | Hôm nay đã tiêu 2.0000 USD, chạm trần 2.0 USD/ngày (budgets.yml per_day.max_usd); không gọi LLM nữa tới ngày mai (UTC). |
| Vi phạm | `violation_budget_scan_limit` | BLOCKED_BUDGET | BLOCKED_BUDGET (budget) | ✅ | 0 | 2 | 11.7 giây | Ước tính quét 16,384 byte (orders 8,192 byte, cộng model) vượt budgets.yml per_run.max_bytes_scanned = 1,000 |
| Vi phạm | `violation_semantic_new_customers` | BLOCKED_SEMANTIC_CONFLICT | BLOCKED_SEMANTIC_CONFLICT (resolve) | ✅ | 0 | 1 | 3.6 giây | Phát hiện thực thể `customer` ở 2 domain. Marketing định nghĩa là người để lại email (2 endpoint REST). Rental định nghĩa là khách có tài khoản thuê DVD tại một cửa hàng (1 bảng Postgres). Chưa có định nghĩa chuẩn (conformed_dimensions). Chuyển tới steward Marketing: binh@company.vn, Rental: steward |
| Vi phạm | `violation_semantic_revenue_per_customer` | BLOCKED_SEMANTIC_CONFLICT | BLOCKED_SEMANTIC_CONFLICT (resolve) | ✅ | 0 | 1 | 4.0 giây | Phát hiện thực thể `customer` ở 2 domain. Marketing định nghĩa là người để lại email (2 endpoint REST). Rental định nghĩa là khách có tài khoản thuê DVD tại một cửa hàng (1 bảng Postgres). Chưa có định nghĩa chuẩn (conformed_dimensions). Chuyển tới steward Marketing: binh@company.vn, Rental: steward |
| Mơ hồ | `ambiguous_profit_margin` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 3.2 giây | Các bảng đã chọn không có cột nào thể hiện: biên lợi nhuận. Cần thêm bảng nào (hoặc nói rõ dùng cột nào)? |
| Mơ hồ | `ambiguous_revenue_target` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 3.2 giây | Các bảng đã chọn không có cột nào thể hiện: chỉ tiêu. Cần thêm bảng nào (hoặc nói rõ dùng cột nào)? |
| Mơ hồ | `ambiguous_sales_report` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 5.6 giây | Yêu cầu chưa nêu cần đo chỉ số nào. Bạn muốn đo gì (ví dụ doanh thu, số đơn, số tiền hoàn) và theo độ hạt nào (ngày, tháng, khách hàng, đơn hàng)? |
| Optimizer | `optimize_incremental_tried_on_real_dbt` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 110.9 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1406s→0.1477s (không nhanh hơn đủ: chạy lại 0.1477s so với bản gốc 0.1406s (cần ≤ 0.1265s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ 0.1319s→0.1479s (không nhanh hơn đủ: chạy lại 0.1479s so với bản gốc 0.1319s (cần ≤ 0.1187s, budgets.yml optimizer.min_gain_pct = 10)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_large_events_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (optimize) | ✅ | 0 | 2 | 969.4 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: giữ 0.2476s→0.1629s (nhanh hơn 34.2%, bảng gold giống hệt bản gốc) · UC-3 READY_FOR_REVIEW, llm_calls 0: table_to_incremental: giữ 0.2409s→0.1636s (nhanh hơn 32.1%, bảng gold giống hệt bản gốc); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_pagila_rentals_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 133.5 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1168s→0.1511s (không nhanh hơn đủ: chạy lại 0.1511s so với bản gốc 0.1168s (cần ≤ 0.1051s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1428s→0.1474s (không nhanh hơn đủ: chạy lại 0.1474s so với bản gốc 0.1428s (cần ≤ 0.1285s, budgets.yml optimizer.min_gain_pct = 10)) |
| Optimizer | `optimize_small_table_skipped` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 88.3 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1368s→0.1457s (không nhanh hơn đủ: chạy lại 0.1457s so với bản gốc 0.1368s (cần ≤ 0.1231s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (bảng nguồn chính có 30 dòng, dưới ngưỡng 10,000 (budgets.yml optimizer.incremental_min_rows)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |

## Optimizer: trước → sau

Giây là trung vị thời gian chạy lại model gold (`optimizer.rerun_samples` lần) của bản gốc và bản ứng viên. Chỉ giữ bản có gold giống hệt, nhanh hơn ít nhất `optimizer.min_gain_pct` % và các lần đo của bản ứng viên (bỏ một lần chậm nhất khi có ≥ 3 lần) đều nhanh hơn mọi lần của bản gốc; luật không áp thì không thử (lý do ở cột Ghi chú phía trên).

| Ca | Lượt | Luật | Trước (giây) | Sau (giây) | Nhanh hơn | Giữ / bỏ |
|---|---|---|---|---|---|---|
| `optimize_incremental_tried_on_real_dbt` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_incremental_tried_on_real_dbt` | P13 | `missing_partition_filter` | 0.1406 | 0.1477 | chậm hơn 5.0% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `table_to_incremental` | 0.1319 | 0.1479 | chậm hơn 12.1% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_large_events_by_day` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_large_events_by_day` | P13 | `missing_partition_filter` | 0.2476 | 0.1629 | 34.2% | giữ |
| `optimize_large_events_by_day` | UC-3 | `table_to_incremental` | 0.2409 | 0.1636 | 32.1% | giữ |
| `optimize_large_events_by_day` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `missing_partition_filter` | 0.1168 | 0.1511 | chậm hơn 29.4% | bỏ |
| `optimize_pagila_rentals_by_day` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | UC-3 | `missing_partition_filter` | 0.1428 | 0.1474 | chậm hơn 3.2% | bỏ |
| `optimize_small_table_skipped` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | P13 | `missing_partition_filter` | 0.1368 | 0.1457 | chậm hơn 6.5% | bỏ |
| `optimize_small_table_skipped` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
