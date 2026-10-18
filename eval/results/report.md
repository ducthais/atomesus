# Evaluation Report

> Báo cáo đánh giá chất lượng sản phẩm theo tiêu chí BTC và SPEC §18. Mọi số đều kèm mẫu số; không làm tròn thành phần
> trăm khi mẫu số nhỏ (SPEC §18.3). Chiến lược kiểm thử đầy đủ: [docs/testing.md](../../docs/testing.md).

- Bản demo: **https://c4-app-045.id.vn** (Azure VM B2s)
- Commit đo: `main` sau `75a368b`, cộng hai bản sửa của đợt này (mục 2.2), cùng commit với báo cáo này
- Ngày đo: 2026-10-17 → 2026-10-18 (giờ UTC trong bảng)
- Lượt benchmark chính thức: lượt 5, 19:37 UTC 17/10 — bảng từng ca ở [`latest.md`](latest.md), số liệu thô [`latest.json`](latest.json)
- Chạy lại trên production: 06:03 UTC 18/10, trong container `backend` của bản `1072104` — **18/19** (bỏ ca 3 triệu
  dòng), mục 2.7 và [`production.md`](production.md)

---

## 1. Metrics

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Response accuracy: khớp golden output (ca hợp lệ) | >80% | **6/7** ca | ✅ |
| Response accuracy: chặn đúng ca vi phạm chính sách | >80% | **6/6** ca | ✅ |
| Response accuracy: hỏi lại khi yêu cầu mơ hồ | >80% | **3/3** ca | ✅ |
| Optimizer đúng hành vi (P13 + UC-3) | >80% | **4/4** ca | ✅ |
| Response accuracy: tổng 20 ca | >80% | **19/20** ca (lượt chính thức); 19/20 ở lượt 3 với seed cũ | ✅ |
| Cải thiện thời gian so với bản gốc (ca 3 triệu dòng) | giữ bản nhanh hơn ≥ 10%, gold giống hệt | **nhanh hơn 34,2%** (P13), **32,1%** (UC-3) | ✅ |
| Chi phí LLM mỗi lượt 20 ca | — | **$0,0235** (~0,1 cent mỗi run) | — |
| Response latency | <3s | Không áp dụng cho run sinh pipeline: một run hợp lệ mất 20–66 giây (LLM + dlt + dbt + đối chiếu + optimizer); chặn chính sách hoặc hỏi lại mất 0,9–12 giây | — |
| Test backend | xanh | **1.629 passed, 2 skipped** | ✅ |
| Test coverage (`src/`) | >60% | **93%** (9.277 câu lệnh, 637 câu chưa chạy tới) | ✅ |
| Test web | xanh | **104/104** unit, **4/4** E2E Playwright | ✅ |
| Test tích hợp Postgres thật | xanh | **28/28** | ✅ |
| User satisfaction | >4/5 | Chưa thu (mục 3) | ⏳ |

## 2. Test Results

### 2.1. Benchmark 20 ca (`python eval/run_eval.py`)

Agent thật (OpenAI, `temperature=0`) trên nguồn thật: Postgres `sales_raw` (seed bẩn có chủ đích) và `pagila`, mock
REST API, thư mục CSV, bộ sự kiện 3 triệu dòng sinh lúc chạy. Mỗi ca: lập kế hoạch → tự xác nhận (P2) → viết Spec → chạy
thử trong sandbox → chấm.

| Nhóm | Số ca | Đạt khi | Lượt chính thức |
|---|---|---|---|
| Hợp lệ | 7 | tới `READY_FOR_REVIEW` và bảng gold khớp golden từng dòng (sai số ≤ 0,01%) | **6/7** (filesystem 1/1, postgres 1/1, rest_api 1/1, đa nguồn 3/4) |
| Vi phạm | 6 | dừng đúng mã `BLOCKED_*` | **6/6** (ast_guard 2/2, budget 2/2, semantic 2/2) |
| Mơ hồ | 3 | dừng `NEEDS_HUMAN` ở bước lập kế hoạch kèm câu hỏi | **3/3** |
| Optimizer | 4 | P13 và UC-3 đo khi luật áp, bỏ kèm lý do khi không; UC-3 `llm_calls = 0`; chỉ giữ bản vừa đúng vừa nhanh hơn | **4/4** |

- Chạy được ngay lần viết Spec đầu: 6/7 ca hợp lệ; tự sửa Spec trung bình 0,43 lần mỗi ca.
- Tiền LLM cả lượt: $0,0235; tổng thời gian 1.594,6 giây (ca 3 triệu dòng chiếm 969 giây).
- Ca `budget`: dữ liệu seed không đủ lớn để vượt trần thật (5 GB / 0,05 USD mỗi run), nên một ca hạ
  `per_run.max_bytes_scanned` xuống 1.000 byte, ca kia đặt chi phí đã dùng trong ngày bằng trần `per_day.max_usd`; chỉ áp
  cho riêng ca đó (khai báo trong `eval/cases/*.json`).

**Ca không đạt ở lượt chính thức:** `valid_postgres_rest_api_reviews_by_customer` (`pagila_pg` + `review_api`: số lượt
thuê, số phản hồi, điểm trung bình mỗi khách). Cổng `join_fanout` phát hiện đúng `AVG(feedback.rating)` bị nhân dòng khi
ghép `customer → rental → feedback`, nhưng LLM không viết lại thành gộp trước bảng phản hồi trong 2 vòng sửa
(`max_spec_attempts`). Ca này trượt ở cả 5 lượt hôm nay: 3 lượt dừng ở cổng; 2 lượt qua cổng nhưng lệch golden (một lượt
dùng INNER JOIN làm mất lượt thuê không có phản hồi, một lượt gold thiếu 1 khách) — là điểm yếu ổn định của agent, xem mục 5.

### 2.2. Năm lượt chạy và hai lỗi được sửa

| Lượt (UTC) | Trạng thái code / dữ liệu | Hợp lệ | Vi phạm | Mơ hồ | Optimizer | Tổng |
|---|---|---|---|---|---|---|
| 1 · 16:51 | code `main`; Postgres máy dev còn seed sạch cũ | — | — | — | — | dừng ở ca cuối: bộ chấm lỗi khi pipeline gốc không tới bước duyệt |
| 2 · 17:49 | + sửa bộ chấm | 5/7 | 4/6 | 3/3 | 3/4 | 15/20 |
| 3 · 18:28 | + **sửa planner** (khái niệm thời gian) | 6/7 | 6/6 | 3/3 | 4/4 | 19/20 |
| 4 · 19:05 | nạp lại seed bẩn hiện tại của repo | 2/7 | 6/6 | 3/3 | 2/4 | 13/20 |
| 5 · 19:37 | + **sửa định dạng ngày `%Y/%m/%d`** | **6/7** | **6/6** | **3/3** | **4/4** | **19/20** |

| Lỗi | Nguyên nhân | Cách sửa (có test) |
|---|---|---|
| Bộ chấm dừng giữa lượt (lượt 1) | Ca optimizer chạy UC-3 trên run không có Spec khi pipeline gốc dừng sớm | `eval/run_eval.py`: pipeline gốc không tới `READY_FOR_REVIEW` thì chấm không đạt kèm lý do, không chạy UC-3 |
| 2 ca AST + 1 ca optimizer hỏi lại yêu cầu rõ (lượt 1–2) | Planner có luật: khái niệm không gắn cột thì hỏi lại. Bảng `orders` lưu ngày dạng chuỗi; LLM đặt đúng `grain = ordered_at_text` nhưng ghi khái niệm "theo ngày" là không có cột | `planner._grain_covers_time`: khái niệm kỳ thời gian ("theo ngày", "từng tháng") được coi là có cột khi grain gộp theo một cột ngày giờ có thật; khái niệm khác (biên lợi nhuận, chỉ tiêu) vẫn phải hỏi. 3 test mới |
| **Hồi quy sản phẩm:** mọi pipeline trên `sales_raw.orders` dừng `NEEDS_HUMAN` (lượt 4) | Từ khi LLM không còn được xem giá trị nguồn (chỉ xem profile cột), định dạng ngày trong Spec chỉ lấy từ `DATE_FORMATS`, mà danh sách này thiếu `%Y/%m/%d`; 4/30 đơn ghi `2026/01/05` bị tách ở staging (13% > trần 1%) | Thêm `%Y/%m/%d` vào `DATE_FORMATS` (`src/agents/nodes/codegen.py`); test profile gửi LLM và bước tự bổ sung định dạng. Ảnh hưởng cả file / Postgres của người dùng có ngày kiểu này — đã deploy 18/10 (`1072104`) |

Lượt 2–3 chạy trên Postgres máy dev còn seed sạch từ trước ngày 06/10 nên không lộ lỗi định dạng ngày; từ lượt 4 dùng
đúng seed của repo (`infra/postgres/init/001_seed.sql`). Bài học: benchmark phải chạy trên đúng dữ liệu của repo — đã
ghi vào mục cách chạy ở `docs/testing.md`.

### 2.3. Optimizer: trước → sau (lượt chính thức)

Giây là trung vị thời gian chạy lại model gold (5 lần) của bản gốc và bản ứng viên. Chỉ giữ bản có gold giống hệt, nhanh
hơn ít nhất 10% (`budgets.yml optimizer.min_gain_pct`) và các lần đo của bản ứng viên (bỏ một lần chậm nhất) đều nhanh
hơn mọi lần của bản gốc.

| Ca | Lượt | Luật | Trước (giây) | Sau (giây) | Nhanh hơn | Giữ / bỏ |
|---|---|---|---|---|---|---|
| `optimize_large_events_by_day` (3 triệu dòng) | P13 | `missing_partition_filter` | 0,2476 | 0,1629 | **34,2%** | **giữ** |
| `optimize_large_events_by_day` (3 triệu dòng) | UC-3 | `table_to_incremental` | 0,2409 | 0,1636 | **32,1%** | **giữ** |
| `optimize_incremental_tried_on_real_dbt` (30 dòng) | P13 | `missing_partition_filter` | 0,1406 | 0,1477 | chậm hơn 5,0% | bỏ |
| `optimize_incremental_tried_on_real_dbt` (30 dòng) | UC-3 | `table_to_incremental` | 0,1319 | 0,1479 | chậm hơn 12,1% | bỏ |
| `optimize_pagila_rentals_by_day` (16.044 dòng) | P13 | `missing_partition_filter` | 0,1168 | 0,1511 | chậm hơn 29,4% | bỏ |
| `optimize_pagila_rentals_by_day` (16.044 dòng) | UC-3 | `missing_partition_filter` | 0,1428 | 0,1474 | chậm hơn 3,2% | bỏ |
| `optimize_small_table_skipped` (30 dòng) | P13 | `missing_partition_filter` | 0,1368 | 0,1457 | chậm hơn 6,5% | bỏ |
| `optimize_small_table_skipped` (30 dòng) | UC-3 | `table_to_incremental` | — | — | — | không thử: 30 dòng dưới ngưỡng 10.000 |

- Trên bảng nhỏ, bản incremental **không** nhanh hơn (overhead dbt lớn hơn phần tiết kiệm) và bị bỏ — đúng hành vi.
- Ca 3 triệu dòng giữ bản tối ưu ở cả P13 và UC-3 trong cả 4 lượt chấm xong (2–5); mức cải thiện 23,0–43,2% tùy lượt (máy dev).
- Mọi run UC-3 có `llm_calls = 0`.
- Lượt 2 có một lần UC-3 giữ `table_to_incremental` trên bảng 30 dòng (nhanh hơn 27,9%); các lượt sau đo cùng ca chậm hơn
  và bỏ. Luật chống nhiễu hiện tại vẫn có thể giữ nhầm khi máy đang nhàn rỗi — ghi ở mục 5.

### 2.4. Cổng ngân sách với số đo thật trên Azure (thay `dry_run` của BigQuery)

Nhóm deploy trên Azure nên không dùng BigQuery. Synapse serverless SQL tính tiền theo dữ liệu đọc từ Storage (5 USD/TB,
`policy/budgets.yml: pricing.usd_per_tb`), Parquet đọc theo cột. Cổng budget (`src/agents/azure_storage.py`) lấy kích
thước nén của đúng các cột pipeline đọc từ **footer** của từng file Parquet trên Blob Storage, không tải dữ liệu.

| Bảng (số cột Spec đọc) | Ước tính cũ (`pg_relation_size`) | Đo từ metadata Parquet | Byte thực sự tải về |
|---|---|---|---|
| `sales_raw.orders` (5 cột) | 8.192 byte | 629 byte | 575 byte (footer) |
| `pagila.rental` (4 cột) | 2.457.600 byte | 389.807 byte | 890 byte (footer) |

Production đã đặt `PIPELINE_AZURE_STORAGE_*` và chép 30 bảng lên container `pipeline-sources` (12/10). Benchmark chạy
không bật Azure nên cổng budget dùng ước tính kiểu DuckDB.

### 2.5. Tám bất biến (SPEC §19, chỉnh theo ADR-0001)

`tests/test_invariants.py` — **8/8 xanh**:

| # | Luật | Test |
|---|---|---|
| 1 | Sandbox mới cho mỗi run, xóa sau khi xong (kể cả khi lỗi) | ✅ |
| 2 | Không có credential production trong môi trường chạy code sinh ra | ✅ |
| 3 | File deploy truy về `run_id` và chữ ký; không ai tự duyệt run của mình | ✅ |
| 4 | Không node LLM nào chạy lệnh shell | ✅ |
| 5 | Mọi phiên bản Spec nằm trong `spec_history` | ✅ |
| 6 | Chữ ký phải khớp `files_hash`, sai thì 409 và không deploy | ✅ |
| 7 | `llm_calls ≤ budgets.per_run.max_llm_calls`, không ghi cứng | ✅ |
| 8 | App không có quyền merge; client GitHub chỉ có thao tác tối thiểu | ✅ (bản 10/10 còn `xfail`) |

### 2.6. Unit, integration, E2E

```
$ python -m pytest tests -q --cov=src
TOTAL                              9277    637    93%
1629 passed, 2 skipped in 778.84s (0:12:58)

$ python -m pytest pipeline_tests            # Postgres nguồn thật, PIPELINE_TEST_DATABASE_URL
28 passed

$ cd web && npm run typecheck && npm test && npm run test:e2e
tsc: không lỗi · 104 pass · 4 passed (Playwright: đăng nhập → tạo mẻ → duyệt → Deploy; màn duyệt 1440px / 390px)
```

Đợt này sửa hai bộ test đã cũ so với code (không nằm trong gate nên trước đây không ai thấy):

- E2E: nút "Hiện mật khẩu" mới làm `getByLabel('Mật khẩu')` khớp hai phần tử → chọn đúng nhãn (`exact: true`).
- `pipeline_tests`: API làm sạch cũ cố ý từ chối run mới trên domain `sales` đã ẩn (đã có test riêng ở
  `tests/test_api/test_hidden_domains.py`) → fixture bỏ chặn để kiểm cơ chế làm sạch; production chặn mật khẩu mặc định
  bằng 503 (không phải 403 như test cũ).

Gate `scripts/harness.py verify-main` (pytest trong Docker, web, chụp UI) xanh trên bản production `1072104`.

### 2.7. Chạy lại benchmark trên production (18/10)

Sau khi deploy `1072104`, chạy lại `eval/run_eval.py` **ngay trong container `backend` của production**: cùng image,
Postgres nguồn, mock REST API, Azure Storage và key OpenAI của bản đang phục vụ người dùng; sổ bằng chứng riêng
(`data/eval/prod-bench.sqlite3`), không lẫn run của người dùng. Bỏ ca 3 triệu dòng để không quá tải VM 4 GB (RAM còn
~1,7 GB trống trong lúc chạy).

| Nơi chạy | Hợp lệ | Vi phạm | Mơ hồ | Optimizer | Tổng | Tiền LLM | Thời gian |
|---|---|---|---|---|---|---|---|
| Máy dev, lượt 5 (cùng 19 ca) | 6/7 | 6/6 | 3/3 | 3/3 | 18/19 | — | — |
| **Production** `1072104` | **6/7** | **6/6** | **3/3** | **3/3** | **18/19** | $0,0211 | 592 giây |

- Cùng một ca trượt (`valid_postgres_rest_api_reviews_by_customer`) nhưng khác cách trượt: trên production vòng sửa SQL tự
  xử lý lỗi ép kiểu `'N/A'`, rồi Spec dùng INNER JOIN giữa `rental` và `feedback` làm mất lượt thuê không có phản hồi;
  đối chiếu độc lập vẫn khớp (pipeline đúng Spec) nên run **tới bước duyệt với số lệch golden** — đúng giới hạn "không
  kiểm Spec đúng ý", Reviewer phải bắt. Mọi ca hợp lệ khác chạy được ngay lần viết Spec đầu.
- Sáu ca hợp lệ khớp golden từng dòng → Postgres nguồn trên production đúng seed của repo (không lặp lại sự cố seed cũ ở
  mục 2.2).
- Các cổng gắn với hạ tầng production chạy đúng: trần chi phí theo ngày, đo byte quét bằng metadata Parquet trên Azure,
  xung đột ngữ nghĩa giữa domain `marketing` và `rental`.
- Ba ca optimizer bỏ bản ứng viên vì model gold chỉ ~0,1 giây (không nhanh hơn đủ 10%) — đúng hành vi; ca chứng minh
  tối ưu thật là ca 3 triệu dòng ở mục 2.3.

Bảng từng ca: [`production.md`](production.md), số liệu thô [`production.json`](production.json).

## 3. User Feedback

Chưa tổ chức buổi lấy ý kiến người dùng; điền sau buổi demo.

| User | Feedback | Rating |
|------|----------|--------|
| | | |

## 4. Demo Results

- Bản demo: https://c4-app-045.id.vn (production `1072104`, smoke test 9/9 xanh 18/10; benchmark chạy trên production
  18/19, mục 2.7)
- Luồng đã chạy thật trên production 17/10: Reviewer duyệt → Deploy → deployer cài lên Dagster; PR lưu vết
  [ducthais/atomesus-pipelines#2](https://github.com/ducthais/atomesus-pipelines/pull/2) qua CI và được GitHub Actions tự merge.
- Video demo và pitch deck: nhóm bổ sung.

## 5. Action Items

- [x] **Deploy hai bản sửa** (định dạng ngày `%Y/%m/%d`, khái niệm thời gian của planner) lên production: `1072104`,
      18/10, smoke 9/9 xanh, benchmark trên production 18/19.
- [ ] Ca review: hướng dẫn / luật tất định để Spec gộp trước bảng "nhiều" khi tính AVG qua chuỗi ghép 1–n (cổng
      `join_fanout` đã bắt được, còn thiếu bước tự viết lại), và cảnh báo khi INNER JOIN làm mất dòng của bảng đang đếm.
- [ ] Đối chiếu độc lập chỉ chứng minh pipeline đúng Spec; thêm kiểm tra ý định (ví dụ độ hạt của gold khớp khái niệm
      "theo từng khách hàng" trong yêu cầu) — lượt 2 có một ca gộp theo ngày × khách thay vì theo khách.
- [ ] Optimizer: tăng số mẫu hoặc yêu cầu khoảng tin cậy để không giữ nhầm bản "nhanh hơn" trên bảng rất nhỏ.
- [ ] Đưa `pipeline_tests` và E2E Playwright vào gate `verify-main` để không cũ đi lần nữa.
- [ ] Chạy benchmark định kỳ (nhiều lượt) để theo dõi độ ổn định của agent theo thời gian.
- [ ] Thu ý kiến người dùng sau buổi demo.
