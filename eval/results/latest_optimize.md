# Kết quả chấm (latest_optimize)

Chạy lúc 2026-10-13 16:50 UTC bằng `python eval/run_eval.py`; số liệu đầy đủ ở `latest_optimize.json`.

- **Tổng: 4/4 ca đạt**
- Optimizer (P13 + UC-3) đúng hành vi: **4/4** ca
- **1/4 ca có bản tối ưu được giữ** (gold giống hệt, nhanh hơn đủ ngưỡng)
- Tiền LLM cả lượt: $0.0044; tổng thời gian 1385.6 giây

| Nhóm | Ca | Mong đợi | Kết quả | Đạt | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| Optimizer | `optimize_incremental_tried_on_real_dbt` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 124.5 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1178s→0.1298s (không nhanh hơn đủ: chạy lại 0.1298s so với bản gốc 0.1178s (cần ≤ 0.106s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ 0.1054s→0.1213s (không nhanh hơn đủ: chạy lại 0.1213s so với bản gốc 0.1054s (cần ≤ 0.09486s, budgets.yml optimizer.min_gain_pct = 10)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_large_events_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (optimize) | ✅ | 0 | 2 | 1020.9 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: giữ 0.2348s→0.2103s (nhanh hơn 10.4%, bảng gold giống hệt bản gốc) · UC-3 READY_FOR_REVIEW, llm_calls 0: table_to_incremental: giữ 0.2155s→0.1405s (nhanh hơn 34.8%, bảng gold giống hệt bản gốc); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_pagila_rentals_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 140.8 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1201s→0.1264s (không nhanh hơn đủ: chạy lại 0.1264s so với bản gốc 0.1201s (cần ≤ 0.1081s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1285s→0.1459s (không nhanh hơn đủ: chạy lại 0.1459s so với bản gốc 0.1285s (cần ≤ 0.1157s, budgets.yml optimizer.min_gain_pct = 10)) |
| Optimizer | `optimize_small_table_skipped` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 99.4 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1147s→0.12s (không nhanh hơn đủ: chạy lại 0.12s so với bản gốc 0.1147s (cần ≤ 0.1032s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (bảng nguồn chính có 30 dòng, dưới ngưỡng 10,000 (budgets.yml optimizer.incremental_min_rows)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |

## Optimizer: trước → sau

Giây là trung vị thời gian chạy lại model gold (`optimizer.rerun_samples` lần) của bản gốc và bản ứng viên. Chỉ giữ bản có gold giống hệt, nhanh hơn ít nhất `optimizer.min_gain_pct` % và các lần đo của bản ứng viên (bỏ một lần chậm nhất khi có ≥ 3 lần) đều nhanh hơn mọi lần của bản gốc; luật không áp thì không thử (lý do ở cột Ghi chú phía trên).

| Ca | Lượt | Luật | Trước (giây) | Sau (giây) | Nhanh hơn | Giữ / bỏ |
|---|---|---|---|---|---|---|
| `optimize_incremental_tried_on_real_dbt` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_incremental_tried_on_real_dbt` | P13 | `missing_partition_filter` | 0.1178 | 0.1298 | chậm hơn 10.2% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `table_to_incremental` | 0.1054 | 0.1213 | chậm hơn 15.1% | bỏ |
| `optimize_incremental_tried_on_real_dbt` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_large_events_by_day` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_large_events_by_day` | P13 | `missing_partition_filter` | 0.2348 | 0.2103 | 10.4% | giữ |
| `optimize_large_events_by_day` | UC-3 | `table_to_incremental` | 0.2155 | 0.1405 | 34.8% | giữ |
| `optimize_large_events_by_day` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | P13 | `missing_partition_filter` | 0.1201 | 0.1264 | chậm hơn 5.2% | bỏ |
| `optimize_pagila_rentals_by_day` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_pagila_rentals_by_day` | UC-3 | `missing_partition_filter` | 0.1285 | 0.1459 | chậm hơn 13.5% | bỏ |
| `optimize_small_table_skipped` | P13 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | P13 | `missing_partition_filter` | 0.1147 | 0.12 | chậm hơn 4.6% | bỏ |
| `optimize_small_table_skipped` | UC-3 | `table_to_incremental` | — | — | — | không thử |
| `optimize_small_table_skipped` | UC-3 | `missing_partition_filter` | — | — | — | không thử |
