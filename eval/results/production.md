# Benchmark trên production

Chạy lúc 2026-10-18 06:03 UTC **ngay trong container `backend` của production** (https://c4-app-045.id.vn, Azure VM
B2s, commit `1072104`): cùng image, Postgres nguồn, mock REST API, Azure Storage và key OpenAI của bản đang phục vụ người
dùng. Lệnh: `python eval/run_eval.py --only <19 ca> --store data/eval/prod-bench.sqlite3` (sổ bằng chứng riêng, không lẫn
run của người dùng). Số liệu thô: [`production.json`](production.json).

Bỏ ca `optimize_large_events_by_day` (sinh 3 triệu dòng): ca này cần nhiều RAM, chạy trên VM 4 GB đang phục vụ người dùng
có thể làm treo site. Kết quả ca đó trên máy dev: [`latest.md`](latest.md) (nhanh hơn 34,2% với gold giống hệt).

So với lượt chính thức trên máy dev (cùng commit, cùng 19 ca): **18/19 = 18/19**, cùng một ca trượt
(`valid_postgres_rest_api_reviews_by_customer`) nhưng **khác cách trượt**. Máy dev: cổng `join_fanout` bắt `AVG` bị nhân
dòng, agent sửa 2 lần không được thì dừng `NEEDS_HUMAN`. Production: vòng sửa SQL tự xử lý lỗi ép kiểu `'N/A'` của cột
`rating`, nhưng Spec ghép `rental` và `feedback` bằng INNER JOIN (thêm điều kiện `feedback.rental_id = rental.rental_id`)
nên mất lượt thuê không có phản hồi và phản hồi trỏ tới lượt thuê không tồn tại; đối chiếu độc lập vẫn khớp (pipeline
đúng Spec) nên run tới `READY_FOR_REVIEW` với cả ba chỉ số lệch golden. Đây là giới hạn đã nêu: đối chiếu không kiểm Spec
đúng ý định, Reviewer phải bắt (tab Kết quả ghi "Ghép bảng (inner join)"). Sáu ca hợp lệ khớp golden từng dòng nên dữ liệu
nguồn trên production đúng seed của repo. Ba ca optimizer "0/3 bản được giữ" là hành vi đúng: model gold chỉ chạy ~0,1
giây, bản ứng viên không nhanh hơn đủ `optimizer.min_gain_pct` = 10% nên bị bỏ.

- **Tổng: 18/19 ca đạt**
- Khớp golden: **6/7** ca hợp lệ (theo loại nguồn: filesystem 1/1, postgres 1/1, rest_api 1/1, đa nguồn 3/4)
- Chạy được ngay lần viết Spec đầu: 6/7; tự sửa Spec trung bình 0.0 lần mỗi ca
- Chặn đúng: **6/6** ca vi phạm (ast_guard 2/2, budget 2/2, semantic 2/2)
- Hỏi lại thay vì đoán: **3/3** ca mơ hồ
- Optimizer (P13 + UC-3) đúng hành vi: **3/3** ca
- **0/3 ca có bản tối ưu được giữ** (gold giống hệt, nhanh hơn đủ ngưỡng)
- Tiền LLM cả lượt: $0.0211; tổng thời gian 592.1 giây

| Nhóm | Ca | Mong đợi | Kết quả | Đạt | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| Hợp lệ | `valid_filesystem_refunds_by_order` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 17.6 giây | khóa `order_id`, chỉ số refunded→`total_refund_amount`, refund_count→`number_of_refunds` |
| Hợp lệ | `valid_postgres_daily_revenue` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 63.6 giây | khóa `order_date`, chỉ số revenue→`revenue` |
| Hợp lệ | `valid_postgres_filesystem_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 24.8 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, refunded→`total_refund_amount` |
| Hợp lệ | `valid_postgres_rest_api_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 23.4 giây | khóa `customer_id`, chỉ số order_count→`number_of_orders`, order_total→`total_order_amount`, paid→`total_payment_amount` |
| Hợp lệ | `valid_postgres_rest_api_reviews_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ❌ | 0 | 3 | 57.6 giây | không cột nào khớp chỉ số 'rental_count' (số lượt thuê); không cột nào khớp chỉ số 'review_count' (số phản hồi hợp lệ); không cột nào khớp chỉ số 'avg_rating' (điểm trung bình (1–5)) |
| Hợp lệ | `valid_rest_api_paid_by_order` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 16.6 giây | khóa `order_id`, chỉ số paid→`total_amount`, payment_count→`payment_count` |
| Hợp lệ | `valid_three_sources_by_customer` | khớp golden | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 26.8 giây | khóa `customer_id`, chỉ số order_total→`total_order_amount`, paid→`total_paid_amount`, refunded→`total_refunded_amount` |
| Vi phạm | `violation_ast_forbidden_function` | BLOCKED_AST | BLOCKED_AST (static) | ✅ | 0 | 2 | 7.3 giây | transform.filters[0]: hàm 'regexp_like' không nằm trong danh sách cho phép |
| Vi phạm | `violation_ast_jinja_injection` | BLOCKED_AST | BLOCKED_AST (static) | ✅ | 0 | 2 | 7.4 giây | Spec có ký tự '{' hoặc '}' (cú pháp Jinja của dbt), không được phép; transform.metrics[revenue].expr: hàm 'struct' không nằm trong danh sách cho phép; transform.metrics[revenue].expr: hàm 'struct' không nằm trong danh sách cho phép; transform.metrics[revenue].expr: hàm 'env_var' không nằm trong danh |
| Vi phạm | `violation_budget_daily_spend` | BLOCKED_BUDGET | BLOCKED_BUDGET (plan) | ✅ | 0 | 0 | 0.1 giây | Hôm nay đã tiêu 2.0000 USD, chạm trần 2.0 USD/ngày (budgets.yml per_day.max_usd); không gọi LLM nữa tới ngày mai (UTC). |
| Vi phạm | `violation_budget_scan_limit` | BLOCKED_BUDGET | BLOCKED_BUDGET (budget) | ✅ | 0 | 2 | 7.6 giây | Metadata Parquet trên Azure quét 1,146 byte (orders 573 byte, cộng model) vượt budgets.yml per_run.max_bytes_scanned = 1,000 |
| Vi phạm | `violation_semantic_new_customers` | BLOCKED_SEMANTIC_CONFLICT | BLOCKED_SEMANTIC_CONFLICT (resolve) | ✅ | 0 | 1 | 3.6 giây | Phát hiện thực thể `customer` ở 2 domain. Marketing định nghĩa là người để lại email (2 endpoint REST). Rental định nghĩa là khách có tài khoản thuê DVD tại một cửa hàng (1 bảng Postgres). Chưa có định nghĩa chuẩn (conformed_dimensions). Chuyển tới steward Marketing: binh@company.vn, Rental: steward |
| Vi phạm | `violation_semantic_revenue_per_customer` | BLOCKED_SEMANTIC_CONFLICT | BLOCKED_SEMANTIC_CONFLICT (resolve) | ✅ | 0 | 1 | 3.7 giây | Phát hiện thực thể `customer` ở 2 domain. Marketing định nghĩa là người để lại email (2 endpoint REST). Rental định nghĩa là khách có tài khoản thuê DVD tại một cửa hàng (1 bảng Postgres). Chưa có định nghĩa chuẩn (conformed_dimensions). Chuyển tới steward Marketing: binh@company.vn, Rental: steward |
| Mơ hồ | `ambiguous_profit_margin` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 2.4 giây | Các bảng đã chọn không có cột nào thể hiện: biên lợi nhuận. Cần thêm bảng nào (hoặc nói rõ dùng cột nào)? |
| Mơ hồ | `ambiguous_revenue_target` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 2.3 giây | Các bảng đã chọn không có cột nào thể hiện: chỉ tiêu. Cần thêm bảng nào (hoặc nói rõ dùng cột nào)? |
| Mơ hồ | `ambiguous_sales_report` | NEEDS_HUMAN | NEEDS_HUMAN (plan) | ✅ | 0 | 1 | 4.5 giây | Yêu cầu chưa nêu cần đo chỉ số nào. Bạn muốn đo gì (ví dụ doanh thu, số đơn, số tiền hoàn) và theo độ hạt nào (ngày, tháng, khách hàng, đơn hàng)? |
| Optimizer | `optimize_incremental_tried_on_real_dbt` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 109.9 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1138s→0.1191s (không nhanh hơn đủ: chạy lại 0.1191s so với bản gốc 0.1138s (cần ≤ 0.1024s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ 0.1044s→0.1155s (không nhanh hơn đủ: chạy lại 0.1155s so với bản gốc 0.1044s (cần ≤ 0.09396s, budgets.yml optimizer.min_gain_pct = 10)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_pagila_rentals_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 123.7 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1172s→0.1173s (không nhanh hơn đủ: chạy lại 0.1173s so với bản gốc 0.1172s (cần ≤ 0.1055s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1154s→0.1163s (không nhanh hơn đủ: chạy lại 0.1163s so với bản gốc 0.1154s (cần ≤ 0.1039s, budgets.yml optimizer.min_gain_pct = 10)) |
| Optimizer | `optimize_small_table_skipped` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 89.2 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1141s→0.1149s (không nhanh hơn đủ: chạy lại 0.1149s so với bản gốc 0.1141s (cần ≤ 0.1027s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (bảng nguồn chính có 30 dòng, dưới ngưỡng 10,000 (budgets.yml optimizer.incremental_min_rows)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |

## Optimizer: trước → sau

Giây là trung vị thời gian chạy lại model gold (`optimizer.rerun_samples` lần) của bản gốc và bản ứng viên. Chỉ giữ bản có gold giống hệt, nhanh hơn ít nhất `optimizer.min_gain_pct` % và các lần đo của bản ứng viên (bỏ một lần chậm nhất khi có ≥ 3 lần) đều nhanh hơn mọi lần của bản gốc; luật không áp thì không thử (lý do ở cột Ghi chú phía trên).

| Ca | Lượt | Luật | Trước (giây) | Sau (giây) | Nhanh hơn | Giữ / bỏ |
|---|---|---|---|---|---|---|
| `optimize_incremental_tried_on_real_dbt` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_incremental_tried_on_real_dbt` | P13 | `missing_partition_filter` | 0.1138 | 0.1191 | chậm hơn 4.7% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `table_to_incremental` | 0.1044 | 0.1155 | chậm hơn 10.6% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `missing_partition_filter` | 0.1172 | 0.1173 | chậm hơn 0.1% | bỏ |
| `optimize_pagila_rentals_by_day` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | UC-3 | `missing_partition_filter` | 0.1154 | 0.1163 | chậm hơn 0.8% | bỏ |
| `optimize_small_table_skipped` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | P13 | `missing_partition_filter` | 0.1141 | 0.1149 | chậm hơn 0.7% | bỏ |
| `optimize_small_table_skipped` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
