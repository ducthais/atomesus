# Kết quả chấm (latest_optimize)

Chạy lúc 2026-10-13 16:02 UTC bằng `python eval/run_eval.py`; số liệu đầy đủ ở `latest_optimize.json`.

- **Tổng: 4/4 ca đạt**
- Optimizer (P13 + UC-3) đúng hành vi: **4/4** ca
- **1/4 ca có bản tối ưu được giữ** (gold giống hệt, nhanh hơn đủ ngưỡng)
- Tiền LLM cả lượt: $0.0051; tổng thời gian 1313.1 giây

| Nhóm | Ca | Mong đợi | Kết quả | Đạt | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| Optimizer | `optimize_incremental_tried_on_real_dbt` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 124.0 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1155s→0.1255s (không nhanh hơn đủ: chạy lại 0.1255s so với bản gốc 0.1155s (cần ≤ 0.104s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ 0.116s→0.1216s (không nhanh hơn đủ: chạy lại 0.1216s so với bản gốc 0.116s (cần ≤ 0.1044s, budgets.yml optimizer.min_gain_pct = 10)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_large_events_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (optimize) | ✅ | 0 | 2 | 998.6 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: giữ 0.2258s→0.1435s (nhanh hơn 36.4%, bảng gold giống hệt bản gốc) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ 0.2159s→0.1512s (chênh lệch nằm trong nhiễu đo: các lần chạy lại của bản ứng viên [0.2543, 0.1512, 0.2523, 0.1345, 0.1376] chồng lên bản gốc [0.2612, 0.2159, 0.2202, 0.2122, 0.2152] (mọi lần của bản ứng viên phải nhanh hơn mọi lần của bản gốc)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_pagila_rentals_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 1 | 3 | 142.5 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1168s→0.1319s (không nhanh hơn đủ: chạy lại 0.1319s so với bản gốc 0.1168s (cần ≤ 0.1051s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.122s→0.1245s (không nhanh hơn đủ: chạy lại 0.1245s so với bản gốc 0.122s (cần ≤ 0.1098s, budgets.yml optimizer.min_gain_pct = 10)) |
| Optimizer | `optimize_small_table_skipped` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 48.0 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ (đã có điều kiện trên `order_date`) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (bảng nguồn chính có 30 dòng, dưới ngưỡng 10,000 (budgets.yml optimizer.incremental_min_rows)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |

## Optimizer: trước → sau

Giây là trung vị thời gian chạy lại model gold (`optimizer.rerun_samples` lần) của bản gốc và bản ứng viên. Chỉ giữ bản có gold giống hệt, nhanh hơn ít nhất `optimizer.min_gain_pct` % và hai dải đo không chồng nhau; luật không áp thì không thử (lý do ở cột Ghi chú phía trên).

| Ca | Lượt | Luật | Trước (giây) | Sau (giây) | Nhanh hơn | Giữ / bỏ |
|---|---|---|---|---|---|---|
| `optimize_incremental_tried_on_real_dbt` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_incremental_tried_on_real_dbt` | P13 | `missing_partition_filter` | 0.1155 | 0.1255 | chậm hơn 8.7% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `table_to_incremental` | 0.116 | 0.1216 | chậm hơn 4.8% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_large_events_by_day` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_large_events_by_day` | P13 | `missing_partition_filter` | 0.2258 | 0.1435 | 36.4% | giữ |
| `optimize_large_events_by_day` | UC-3 | `table_to_incremental` | 0.2159 | 0.1512 | 30.0% | bỏ |
| `optimize_large_events_by_day` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `missing_partition_filter` | 0.1168 | 0.1319 | chậm hơn 12.9% | bỏ |
| `optimize_pagila_rentals_by_day` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | UC-3 | `missing_partition_filter` | 0.122 | 0.1245 | chậm hơn 2.0% | bỏ |
| `optimize_small_table_skipped` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | P13 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_small_table_skipped` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
