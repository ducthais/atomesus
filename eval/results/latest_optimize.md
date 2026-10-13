# Kết quả chấm (latest_optimize)

Chạy lúc 2026-10-13 04:53 UTC bằng `python eval/run_eval.py`; số liệu đầy đủ ở `latest_optimize.json`.

- **Tổng: 3/3 ca đạt**
- Optimizer (P13 + UC-3) đúng hành vi: **3/3** ca
- Tiền LLM cả lượt: $0.0032; tổng thời gian 736.1 giây

| Nhóm | Ca | Mong đợi | Kết quả | Đạt | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| Optimizer | `optimize_incremental_tried_on_real_dbt` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 229.2 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1683s→0.2377s (không nhanh hơn đủ: chạy lại 0.2377s so với bản gốc 0.1683s (cần ≤ 0.1515s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ 0.2031s→0.2333s (không nhanh hơn đủ: chạy lại 0.2333s so với bản gốc 0.2031s (cần ≤ 0.1828s, budgets.yml optimizer.min_gain_pct = 10)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
| Optimizer | `optimize_pagila_rentals_by_day` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 292.0 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.2148s→0.279s (không nhanh hơn đủ: chạy lại 0.279s so với bản gốc 0.2148s (cần ≤ 0.1933s, budgets.yml optimizer.min_gain_pct = 10)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.1622s→0.1654s (không nhanh hơn đủ: chạy lại 0.1654s so với bản gốc 0.1622s (cần ≤ 0.146s, budgets.yml optimizer.min_gain_pct = 10)) |
| Optimizer | `optimize_small_table_skipped` | đúng hành vi optimizer | READY_FOR_REVIEW (t_validate) | ✅ | 0 | 2 | 214.9 giây | P13: table_to_incremental: bỏ (model đang là `incremental`, luật chỉ áp cho `table`); missing_partition_filter: bỏ 0.2462s→0.1788s (chênh lệch nằm trong nhiễu đo: các lần chạy lại của bản ứng viên [0.4991, 0.3619, 0.1788, 0.1718, 0.1506] chồng lên bản gốc [0.1948, 0.1735, 0.3148, 0.2611, 0.2462] (mọi lần của bản ứng viên phải nhanh hơn mọi lần của bản gốc)) · UC-3 NO_IMPROVEMENT, llm_calls 0: table_to_incremental: bỏ (bảng nguồn chính có 30 dòng, dưới ngưỡng 10,000 (budgets.yml optimizer.incremental_min_rows)); missing_partition_filter: bỏ (chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)) |
