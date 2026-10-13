# Evaluation Report

> Báo cáo đánh giá chất lượng sản phẩm theo tiêu chí BTC và SPEC §18. Mọi số đều kèm mẫu số; không làm tròn thành phần
> trăm khi mẫu số nhỏ (SPEC §18.3).

- Bản demo: **https://c4-app-045.id.vn** (deploy trên Azure)
- Nhánh / commit đo: `refactor/project-restructure` @ `ceb0666` cộng các thay đổi của lượt này
- Ngày đo: 2026-10-10

---

## 1. Metrics

Số liệu của lượt chấm cuối (09:37 UTC, sau ba bản sửa ở mục 2.1). Lượt đầu cùng ngày, trước khi sửa, đạt 10/15.

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Response accuracy: khớp golden output (ca hợp lệ) | >80% | **6/6** ca | ✅ |
| Response accuracy: chặn đúng ca vi phạm chính sách | >80% | **6/6** ca | ✅ |
| Response accuracy: hỏi lại khi yêu cầu mơ hồ | >80% | **3/3** ca | ✅ |
| Response accuracy: tổng 15 ca | >80% | **15/15** ca (lượt cuối); 14/15 ở ba lượt trước đó, xem mục 2.1 | ✅ |
| Response latency | <3s | Không áp dụng cho run: một run sinh pipeline mất 22–35 giây (LLM + dlt + dbt); chặn chính sách hoặc hỏi lại mất 0,2–9 giây | — |
| User satisfaction | >4/5 | Chưa thu (xem mục 3) | ⏳ |
| Test coverage (`src/`) | >60% | **89%** (5.252 dòng, 575 dòng chưa chạy tới) | ✅ |

## 2. Test Results

### 2.1. Đánh giá 15 ca (SPEC §18.2)

Chạy `python eval/run_eval.py` bằng LLM thật (OpenAI) và nguồn thật (Postgres, mock REST API, file CSV). Bảng đầy đủ
từng ca của lượt cuối: [`latest.md`](latest.md); số liệu thô: [`latest.json`](latest.json).

| Nhóm | Số ca | Đạt khi | Lượt cuối |
|---|---|---|---|
| Hợp lệ | 6 | tới READY_FOR_REVIEW và bảng gold khớp golden từng dòng | **6/6** (postgres 1/1, rest_api 1/1, filesystem 1/1, đa nguồn 3/3) |
| Vi phạm | 6 | dừng đúng mã `BLOCKED_*` | **6/6** (semantic 2/2, budget 2/2, ast_guard 2/2) |
| Mơ hồ | 3 | dừng NEEDS_HUMAN ở bước lập kế hoạch kèm câu hỏi | **3/3** |

- Chạy được ngay lần viết Spec đầu: 6/6 ca hợp lệ (0 lần tự sửa Spec).
- Tiền LLM cả lượt 15 ca: $0,0155; tổng thời gian 236,1 giây.
- Ca `budget`: dữ liệu seed không có bảng đủ lớn để vượt trần thật (5 GB / 0,05 USD mỗi run), nên một ca hạ
  `per_run.max_bytes_scanned` xuống 1.000 byte, ca kia đặt chi phí đã dùng trong ngày bằng trần `per_day.max_usd`. Cả
  hai chỉ áp cho riêng ca đó (khai báo trong `eval/cases/*.json`).

**Năm lượt chấm trong ngày và các bản sửa**

| Lượt (UTC) | Thay đổi trước lượt | Hợp lệ | Vi phạm | Mơ hồ | Tổng |
|---|---|---|---|---|---|
| 1 · 08:33 | (code ban đầu) | 3/6 | 6/6 | 1/3 | 10/15 |
| 2 | Sửa định dạng ngày; prompt planner hỏi lại khi không nêu chỉ số / so với dữ liệu không có | 6/6 | 5/6 | 3/3 | 14/15 |
| 3 · 09:23 | Xung đột thực thể được chặn trước câu hỏi làm rõ | 6/6 | 6/6 | 2/3 | 14/15 |
| 4 · 09:30 | Kế hoạch không có khái niệm nào nằm trong yêu cầu thì hỏi lại (tất định) | 5/6 | 6/6 | 3/3 | 14/15 |
| 5 · 09:37 | Cổng `ast_guard` không chặn nhầm `CAST(x AS decimal(18,2))` | **6/6** | **6/6** | **3/3** | **15/15** |

Các ca trượt ở lượt 2–4 đến từ việc LLM trả lời khác nhau giữa các lần gọi, mỗi lần làm lộ một chỗ code chưa chặt; mỗi
chỗ được sửa tất định (có test) thay vì sửa prompt cho riêng ca đó. Các bản sửa:

| Lỗi | Nguyên nhân | Cách sửa |
|---|---|---|
| 3 ca có nguồn file hỏng (lượt 1) | Cột `refunded_at` trộn `2026-01-05` với `21/01/2026`. Profile dòng mẫu chỉ ghi định dạng khớp **mọi** giá trị nên trả `[]`; LLM chép `[]` (Spec bị từ chối) hoặc chỉ chọn một định dạng (46% dòng bị tách) | `codegen.covering_formats`: chọn ít định dạng nhất phủ mọi giá trị mẫu (ngày trước tháng khi hòa); `_fix_date_formats` bổ sung định dạng LLM còn thiếu, `[]` → đủ định dạng |
| 2 ca mơ hồ bị đoán (lượt 1) | Prompt planner dặn "đừng hỏi khi có thể chọn mặc định"; LLM tự chọn doanh thu cho "báo cáo về bán hàng", bỏ qua "chỉ tiêu" | Prompt: chỉ mặc định phần kỹ thuật, không tự chọn chỉ số; thứ đem ra so sánh là một khái niệm. Thêm luật tất định: không khái niệm nào của kế hoạch có trong câu yêu cầu → hỏi lại |
| 1 ca semantic thành câu hỏi (lượt 2) | Planner hỏi lại nên cổng semantic (chạy sau) không được chạy | Khi planner định hỏi, quét thực thể trước; xung đột giữa domain thắng câu hỏi (người dùng trả lời không gỡ được xung đột định nghĩa) |
| 1 ca hợp lệ bị chặn AST (lượt 4) | Cổng coi `decimal(` trong `CAST(... AS decimal(18,2))` là lời gọi hàm không được phép | Bỏ qua tham số kiểu chỉ gồm chữ số đứng ngay sau `AS`; biến thể khác (`decimal(...)` không có AS, tham số không phải số) vẫn bị chặn, có test |

### 2.2. Cổng ngân sách với số đo thật trên Azure (thay `dry_run` của BigQuery)

Nhóm deploy trên Azure nên không dùng BigQuery. Azure không có `dry_run`; Synapse serverless SQL tính tiền theo dữ liệu
đọc từ Storage (5 USD / TB, `policy/budgets.yml: pricing.usd_per_tb`), và Parquet được đọc theo cột. Cổng budget
(`src/agents/azure_storage.py`) lấy kích thước nén của đúng các cột pipeline đọc từ **footer** của từng file Parquet
trên Blob Storage, không tải dữ liệu.

Kiểm chứng trên Azurite (giả lập Blob Storage chính thức của Microsoft, chạy bằng docker) sau khi chép 30 bảng nguồn
bằng `scripts/azure_mirror.py`:

| Bảng (số cột Spec đọc) | Ước tính cũ (`pg_relation_size`) | Đo từ metadata Parquet | Byte thực sự tải về |
|---|---|---|---|
| `sales_raw.orders` (5 cột) | 8.192 byte | 629 byte | 575 byte (footer) |
| `pagila.rental` (4 cột) | 2.457.600 byte | 389.807 byte | 890 byte (footer) |

- Ước tính cũ đếm cả trang dữ liệu Postgres và mọi cột; số đo mới chỉ gồm các cột câu truy vấn đọc, đúng cách Synapse
  serverless tính tiền.
- Chưa đo trên tài khoản Azure Storage thật của nhóm: cần đặt `PIPELINE_AZURE_STORAGE_CONNECTION_STRING` và
  `PIPELINE_AZURE_STORAGE_CONTAINER` trên máy chủ rồi chạy `scripts/azure_mirror.py`. Code không đổi giữa Azurite và Azure.
- Lượt chấm 15 ca ở mục 2.1 chạy với cấu hình mặc định (không bật Azure), nên cổng budget dùng ước tính kiểu DuckDB.
- Chưa đo được "sai số giữa ước tính và chi phí thật" (SPEC §18.3) vì pipeline chạy trên DuckDB, không chạy query trên
  Synapse.

### 2.3. Tám luật cứng (SPEC §19)

`tests/test_invariants.py`, không phụ thuộc deploy:

| # | Luật | Test |
|---|---|---|
| 1 | Sandbox mới cho mỗi run, xóa sau khi xong (kể cả khi lỗi) | ✅ |
| 2 | Không có credential production trong môi trường chạy code sinh ra | ✅ |
| 3 | File deploy truy về `run_id` và chữ ký; **không ai tự duyệt run của mình** (API trả 403, hợp đồng RunReport cũng chặn) | ✅ |
| 4 | Không node LLM nào chạy lệnh shell (quét AST code agent và chat) | ✅ |
| 5 | Mọi phiên bản Spec nằm trong `spec_history` | ✅ |
| 6 | Chữ ký phải khớp `files_hash`, sai thì 409 và không deploy | ✅ |
| 7 | `llm_calls ≤ budgets.per_run.max_llm_calls`, trần đọc từ budgets.yml, không ghi cứng | ✅ |
| 8 | Mọi thay đổi lên production qua pull request | ⏳ `xfail`: duyệt xong vẫn ghi thẳng ra Dagster local (giai đoạn Deploy) |

### 2.4. Unit và integration test

```
$ python -m pytest tests --cov=src
TOTAL                              5252    575    89%
460 passed, 1 xfailed, 5 warnings in 336.96s (0:05:36)
```

`xfailed` là bất biến 8 (pull request), đánh dấu chủ động ở mục 2.3. Gồm test của agent, cổng, sandbox (dlt + dbt
thật trên DuckDB), API, chat, monitor Day 2, chấm eval, cổng budget trên Azure (`tests/test_agents/test_azure_storage.py`)
và các bản sửa ở mục 2.1 (`tests/test_agents/test_graph.py`, `tests/test_codegen/test_render.py`).

### 2.5. Optimizer P13 và tối ưu thủ công UC-3 (SPEC §9.3, §3.3, FR-14)

Chạy `python eval/run_eval.py --group optimize` lúc 2026-10-13, dbt + DuckDB + Postgres thật. Chi tiết từng luật:
[`latest_optimize.md`](latest_optimize.md).

| Ca | P13 lúc tạo pipeline | UC-3 (tối ưu thủ công) | Đạt |
|---|---|---|---|
| `optimize_pagila_rentals_by_day` (16.044 dòng) | `missing_partition_filter` đo thật, bỏ: 0,279s so với 0,215s | NO_IMPROVEMENT, `llm_calls = 0`; đo lại 0,165s so với 0,162s, bỏ | ✅ |
| `optimize_incremental_tried_on_real_dbt` (bản deploy là `table`) | `missing_partition_filter` bỏ (chậm hơn) | `table_to_incremental` chạy thật trên dbt, bỏ: 0,233s so với 0,203s; `llm_calls = 0` | ✅ |
| `optimize_small_table_skipped` (30 dòng, bản deploy là `table`) | `missing_partition_filter`: trung vị nhanh hơn 27% nhưng **dải đo chồng nhau → bỏ vì nhiễu** | NO_IMPROVEMENT, không chạy thử thêm; lý do "dưới ngưỡng 10,000" | ✅ |

- **Đúng hành vi 3/3 ca**; mọi run UC-3 có `llm_calls = 0` (LLM bị thay bằng hàm ném lỗi, nên không thể gọi).
- Trên dữ liệu seed (≤ 16 nghìn dòng), bản incremental **không nhanh hơn đo được** so với bản gốc: chưa ca thật nào giữ
  bản ứng viên. Trường hợp giữ được kiểm bằng test với sandbox giả (`tests/test_agents/test_optimizer.py`).
- Lượt đầu (cùng ngày, máy đang chạy song song cả bộ test) giữ nhầm một bản 30 dòng "nhanh hơn 26,9%" mà lượt sau đo
  chậm hơn 41%. Từ đó optimizer chỉ giữ bản ứng viên khi trung vị nhanh hơn `min_gain_pct` **và** mọi lần chạy lại của
  nó nhanh hơn mọi lần của bản gốc; `rerun_samples` tăng từ 3 lên 5.
- Ghi nhận: model LLM viết gần như luôn là `incremental` thiếu `partition_by`, nên luật hay áp nhất là
  `missing_partition_filter`; `table_to_incremental` áp cho pipeline deploy dạng `table`.

## 3. User Feedback

Chưa tổ chức buổi lấy ý kiến người dùng; điền sau buổi demo.

| User | Feedback | Rating |
|------|----------|--------|
| | | |

## 4. Demo Results

- Bản demo: https://c4-app-045.id.vn
- Ngày demo: chưa tổ chức
- Kịch bản: `docs/demo.md`

## 5. Action Items

- [ ] Chạy `python eval/run_eval.py` thêm vài lượt để đo độ ổn định (lượt 2–4 mỗi lượt trượt một ca do LLM trả lời khác nhau)
- [ ] Bật đo byte trên Azure Storage thật của nhóm trên máy chủ, rồi chạy lại eval với Azure bật
- [ ] Bất biến 8: duyệt xong mở pull request thay vì ghi thẳng Dagster local
- [ ] Optimizer: đo trên bảng nguồn đủ lớn (vài triệu dòng, ví dụ sau `scripts/grow_data`) để có ca thật giữ bản ứng viên
- [ ] Thu ý kiến người dùng sau buổi demo
