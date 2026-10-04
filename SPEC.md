# SPEC.md — Control Plane cho việc sinh pipeline ELT

Đặc tả **hệ thống đích**. Tám bất biến ở mục 19 là luật cứng, không có ngoại lệ.

## 0. Phạm vi tài liệu

File này là nguồn duy nhất cho hệ thống đích: trông như thế nào, hành vi ra sao, MVP dừng ở đâu. Không chứa lộ trình và số ngày.

Giao diện không bao giờ quyết định phạm vi backend — bỏ một ô hiển thị không có nghĩa là bỏ phép kiểm tra.

### 0.1 Tuyên bố governance

**Deploy nghĩa là đưa pipeline lên Dagster production qua pull request.** Sau khi Reviewer duyệt, hệ thống tự mở pull request vào repo pipeline, đính kèm 4 + N file và RunReport. CI chạy lại test; người có quyền merge bấm merge; CI/CD của tổ chức deploy lên Dagster production. Agent **không bao giờ** giữ credential production và **không có quyền merge**: nó chỉ có quyền mở pull request. Thao tác duyệt trên web là điều kiện để mở pull request, không thay thế quy trình review và merge của tổ chức.

Nêu câu này trong báo cáo và khi bảo vệ, trước khi hội đồng hỏi.

---

## 1. Mục tiêu và định vị

### 1.1 Câu định vị

> **Control plane cho vòng đời pipeline: sinh ra đúng, và giữ cho đúng.**

Sản phẩm là lớp điều khiển đứng giữa yêu cầu dữ liệu và kho dữ liệu, hoạt động ở hai giai đoạn:

- **Day 1 — tạo mới.** Nhận yêu cầu ngôn ngữ tự nhiên, sinh pipeline, buộc mọi đề xuất đi kèm bằng chứng đo được, đối chiếu bộ luật do tổ chức khai báo, rồi mới cho phép thực thi.
- **Day 2 — vận hành.** Theo dõi các pipeline đã deploy, phát hiện suy giảm về thời gian, chi phí, độ tươi, tự đề xuất bản sửa **đã được kiểm chứng trên sandbox tạm của run**, và chỉ gõ cửa kỹ sư khi có việc cần quyết định. Pipeline được theo dõi trực tiếp trên production.

Bằng chứng ghi lúc tạo mới chính là đường cơ sở để so sánh lúc vận hành. Hai giai đoạn dùng chung một bộ máy, không phải hai tính năng rời.

### 1.2 Ba nỗi đau trong đề bài

| # | Nỗi đau | Biểu hiện thực tế | Hệ thống giải quyết ở đâu |
|---|---|---|---|
| A | Viết pipeline **lặp đi lặp lại** | Pipeline thứ 40 có cấu trúc y hệt pipeline thứ 39, nhưng vẫn phải viết tay; kết quả không đồng đều — cái có test, cái không, cái đặt tên sai quy ước | UC-1, UC-2 |
| B | **Query chậm** cần tối ưu | Pipeline chạy 4 giây lúc bảng có 2 triệu dòng, 40 giây khi bảng lên 50 triệu dòng. Chậm dần sau khi deploy, không phải lúc mới viết | UC-3, UC-4 |
| C | Phải **ngồi theo dõi** | Mỗi sáng mở dashboard xem job nào chậm, job nào tốn tiền, bảng nào thiếu dữ liệu, rồi tự điều tra, tự sửa, tự kiểm | UC-4 |

**Về nỗi đau A:** giá trị chính không nằm ở tốc độ mà ở **tính nhất quán**. Agent sinh mọi pipeline từ cùng một bộ template và cho chúng đi qua cùng một bộ thẩm định, nên chất lượng đồng đều hơn thay vì chỉ nhanh hơn.

**Về nỗi đau C:** kỹ sư không muốn theo dõi; họ muốn **được báo khi có việc, kèm sẵn phương án đã kiểm chứng**. Một cảnh báo "job chậm 3 lần liên tiếp" vẫn bắt kỹ sư tự điều tra. Hệ thống này đi xa hơn: tự đề xuất bản sửa, tự chạy thử, tự chứng minh bản sửa vừa đúng vừa nhanh hơn, rồi mới trình lên.

### 1.3 Vì sao không phải "AI viết SQL"

Bốn vấn đề mà công cụ sinh code thuần không giải quyết được:

1. **Guardrail phải cưỡng chế, không phải lời dặn.** Agent chạy tự động cần chốt chặn ở tầng hạ tầng (IAM, `maximum_bytes_billed`, `require_partition_filter`), không phải câu "đừng làm hỏng prod" trong prompt.
2. **Ranh giới nghiệp vụ không suy ra được từ schema.** Khi có 17 bảng tên `customer` ở các domain khác nhau, agent thông minh hơn không giúp được. Nó phải đọc định nghĩa do con người viết, và **biết từ chối** khi chưa có định nghĩa.
3. **Xung đột mục tiêu cần trọng tài tất định.** Streaming nhanh nhưng đắt, batch rẻ nhưng chậm. Quyết định phải dựa trên hàm mục tiêu con người khai báo trước, không phải hai LLM thương lượng.
4. **Pipeline xuống cấp sau khi deploy.** Công cụ sinh code dừng lại khi code được sinh ra. Nhưng phần lớn thời gian của kỹ sư nằm ở giai đoạn sau đó.

### 1.4 Hệ quả kiến trúc

**LLM chỉ được quyền đề xuất. Code tất định mới được quyền quyết định. Con người sở hữu bộ luật.**

Bộ sinh code là bộ phận thay thế được. Kho chính sách và sổ bằng chứng là lõi. Thiết kế module phải phản ánh điều này: đổi model LLM không được đụng tới `control_plane/`.

---

## 2. Actor và ranh giới quyền

App có đúng **hai vai**:

| Vai | Làm được | Không làm được |
|---|---|---|
| **Engineer** | Gửi yêu cầu (chat hoặc wizard), trả lời câu hỏi làm rõ, xác nhận kế hoạch ở P2, sửa spec qua chat, chạy lại, hủy run của mình, đọc bằng chứng | Duyệt hoặc deploy **run của chính mình** |
| **Reviewer** | Đọc diff và bằng chứng, hỏi trợ lý chat, duyệt / từ chối / yêu cầu sửa, kích hoạt mở pull request | Tạo run, sửa spec, sửa code trực tiếp trong app |

Quy tắc "engineer không tự duyệt run của mình" là ràng buộc bắt buộc, không phải tùy chọn cấu hình.

Hai bên liên quan **không phải vai trong app**:
- **Data steward** sở hữu `policy/*.yml` và xử lý phiếu xung đột thực thể. Steward sửa chính sách qua git (pull request), không qua app; app chỉ hiển thị chính sách ở chế độ chỉ đọc (FR-19).
- **Agent** chỉ sinh, chạy thử và tự sửa **trong sandbox**. Agent không chạm prod, chỉ mở pull request sau khi được duyệt, không có quyền merge, không chạy lệnh shell (mục 19).
- **Người có quyền merge** (thường là Reviewer hoặc tech lead) merge pull request trên GitHub theo branch protection của repo. Đây là bước con người cuối cùng trước production.

---

## 3. Use case

### 3.1 UC-1 — Tạo pipeline mới từ yêu cầu nghiệp vụ

**Tiền điều kiện:** nguồn đã khai báo trong `policy/domains.yml`; người dùng có vai trò Engineer.

**Luồng chính:** người dùng mô tả nhu cầu bằng ngôn ngữ tự nhiên → hệ thống lập kế hoạch (P1) → người dùng xác nhận (P2) → hệ thống phân giải schema và kiểm ngữ nghĩa (P3) → sinh spec và render (P4–P6) → kiểm tĩnh và ngân sách (P7–P8) → chạy thử và thẩm định trên sandbox (P9–P12) → tối ưu (P13) → xuất bằng chứng (P14) → Reviewer quyết định (P15) → mở pull request, CI/CD đưa lên Dagster production sau khi merge (P16) → lưu mẫu (P17).

**Luồng thay thế:** xung đột ngữ nghĩa ở P3 → dừng, phiếu chuyển steward. Vượt ngân sách ở P8 → quay lại P4 kèm ràng buộc byte. Thẩm định fail → vòng spec hoặc vòng SQL theo mục 11.3. Chạm trần → `NEEDS_HUMAN`.

**Hậu điều kiện:** pipeline tồn tại trong code location; mọi quyết định và số đo đã ghi vào sổ bằng chứng.

### 3.2 UC-2 — Onboard hàng loạt bảng

Người dùng chọn N đối tượng (bảng, endpoint hoặc nhóm file) của một hệ thống nguồn mới; hệ thống sinh N pipeline ingestion và staging theo cùng quy ước, thẩm định từng cái, và trình kết quả theo mẻ để duyệt.

**Giá trị:** nhất quán, không chỉ nhanh. Cả N pipeline sinh từ cùng template, cùng quy ước đặt tên, cùng bộ test, cùng đi qua 6 cổng. Đây là câu trả lời trực tiếp cho nỗi đau A.

**Khác UC-1:** fan-out sau P3, mỗi nhánh chạy P4–P14 độc lập, gom lại ở P15. Mỗi nhánh vẫn chịu trần `per_run` như một run thường; **tổng** chi phí ước tính của cả mẻ không được vượt phần còn lại của `budgets.yml: per_day.max_usd`, vượt thì chặn cả mẻ với `BLOCKED_BUDGET` trước khi fan-out.

### 3.3 UC-3 — Tối ưu theo yêu cầu

Engineer **chủ động** trỏ vào một pipeline đã deploy; hệ thống đo baseline, áp luật tối ưu, chạy thử bản ứng viên trên sandbox, và chỉ đề xuất khi bản ứng viên vừa **đúng** vừa **nhanh hơn**.

**Khác UC-1:** `trigger = "manual_optimize"`. **Chỉ chạy optimizer**, không gọi LLM, không sinh spec mới:

`P0 intake` → `P6 render` (spec đang chạy) → `P9–P12` (dựng dữ liệu trên sandbox và đo baseline; phải pass) → `P13 optimize` → `P14 report` → `P15 review` → `P16 deploy` → `P17 persist`

Bỏ qua P1–P5 vì spec đã được duyệt. Bỏ qua P7–P8 cho spec đang chạy vì nó đã qua các cổng lúc tạo mới; **bản ứng viên** của optimizer vẫn phải qua `ast_guard` và `budget` (mục 9.3). Nếu schema của bất kỳ nguồn nào đã đổi so với `schema_snapshot` cũ thì dừng với `NEEDS_HUMAN` và gợi ý tạo run mới qua UC-1.

**Kết quả:** optimizer tìm được bản tốt hơn thì run vào hàng đợi review như bình thường. Không tìm được thì run kết thúc ở P14 với trạng thái `NO_IMPROVEMENT` (mục 11.1), báo cho Engineer "không có bản tốt hơn" kèm lý do từng luật bị loại. Không vào hàng đợi review, không có gì để deploy.

### 3.4 UC-4 — Tự phát hiện suy giảm và đề xuất sửa

Đây là use case Day 2, giải quyết nỗi đau B và C.

**Tiền điều kiện:** pipeline đã deploy qua UC-1 hoặc UC-2, có đường cơ sở ghi trong sổ bằng chứng.

**Luồng chính:** `monitor` thu số đo sau mỗi lần pipeline chạy → một luật phát hiện ở mục 9.6 vượt ngưỡng → hệ thống tự tạo run mới với `trigger = "regression"`, mang theo spec đang chạy và bằng chứng suy giảm → vào P4 → đi qua P5–P14 như bình thường → nếu bản sửa vừa đúng vừa tốt hơn thì vào hàng đợi review với nhãn "bản sửa cho pipeline đang suy giảm" → kỹ sư duyệt → deploy.

**Luồng thay thế:** không tìm được bản sửa nào tốt hơn → run kết thúc với `NO_IMPROVEMENT`, không làm phiền kỹ sư, chỉ ghi vào sổ bằng chứng và gắn cờ pipeline để kỹ sư xem khi rảnh. Suy giảm về độ tươi do nguồn ngừng cấp dữ liệu → không phải lỗi pipeline, chuyển thành cảnh báo hạ tầng, không tạo run sửa.

**Hậu điều kiện:** kỹ sư không phải mở dashboard. Họ chỉ nhận những đề xuất đã có bằng chứng.

**Ràng buộc:** UC-4 **không bao giờ tự deploy**. Tự phát hiện và tự đề xuất, nhưng quyết định vẫn thuộc về người. Đây là điểm phân biệt với hệ thống tự sửa hoàn toàn, và giữ nguyên ràng buộc HITL của đề bài.

### 3.5 Ranh giới

Hệ thống không thay thế data engineer mà đổi vai họ từ người viết thành người duyệt. Nó không xử lý logic nghiệp vụ cần tri thức ngoài schema (hoa hồng nhiều tầng, đối soát kế toán). Agent không trực tiếp chạm prod: pipeline lên production chỉ qua pull request và CI/CD của tổ chức. Nó không thay thế data catalog hay quy trình governance của tổ chức.

---

## 4. Yêu cầu chức năng

| FR | Yêu cầu | Phase | Nghiệm thu |
|---|---|---|---|
| FR-01 | Xác thực và phân biệt hai vai trò Engineer / Reviewer | — | Reviewer không thấy nút tạo; Engineer không duyệt được run của mình. Danh tính và vai trò lấy từ phiên đăng nhập ở backend, **không bao giờ** tin trường `user`/`reviewer` do client gửi lên (mục 15) |
| FR-02 | Nhận yêu cầu ngôn ngữ tự nhiên kèm chọn domain, qua trợ lý chat hoặc wizard | P0–P1 | Yêu cầu tạo ra `run_id` và bản ghi run |
| FR-03 | Lập kế hoạch và cho người xác nhận trước khi sinh code | P1–P2 | Không sinh spec khi chưa có `PLAN_CONFIRMED` |
| FR-04 | Phân giải schema thật từ nguồn, không để LLM tự nghĩ tên cột | P3 | `schema_snapshot.origin` ghi đúng nguồn gốc theo loại nguồn (mục 12.1): `information_schema`, `openapi` hoặc `file_sample` |
| FR-05 | Phát hiện và chặn xung đột định nghĩa thực thể giữa các domain | P3 | Ca vi phạm trả `BLOCKED_SEMANTIC_CONFLICT` kèm cả hai định nghĩa |
| FR-06 | Sinh đặc tả pipeline dưới dạng JSON spec, không sinh code tự do | P4 | Spec khớp JSON Schema; code do template render |
| FR-07 | Phân xử giữa nhiều phương án theo hàm mục tiêu khai báo | P5 | Lý do chọn ghi trong report; không phương án khả thi thì escalate |
| FR-08 | Render bộ file pipeline đầy đủ: ingestion, staging cho từng nguồn, model gold, schema test, orchestration | P6 | 4 + N file (N = số nguồn), đúng cú pháp 100% |
| FR-09 | Chặn lệnh nguy hiểm và mẫu SQL xấu bằng phân tích AST | P7 | `DROP`/`DELETE`/cross-db bị chặn với `BLOCKED_AST` |
| FR-10 | Ước lượng chi phí trước khi chạy và chặn khi vượt ngân sách | P8 | `dry_run` cho byte; vượt trần trả `BLOCKED_BUDGET` |
| FR-11 | Thực thi pipeline trên sandbox cô lập | P9–P11 | Sandbox mới mỗi run, xóa sau khi xong |
| FR-12 | Thẩm định tính đúng của ingestion, staging và transform | P10, P12 | 5 phép kiểm tra ở mục 10.1 đều chạy |
| FR-13 | Tự sửa khi lỗi, trong trần đã khai báo | P4/P6 | Ba vòng lặp ở mục 11.3, trần đọc từ `budgets.yml` |
| FR-14 | Đo và đề xuất tối ưu, chỉ nhận khi vừa đúng vừa nhanh hơn; Engineer chủ động yêu cầu tối ưu một pipeline đã deploy (UC-3) chỉ chạy optimizer | P13 | Bản ứng viên kém hơn thì bị loại, ghi lý do; run UC-3 có `llm_calls = 0` |
| FR-15 | Xuất bằng chứng có cấu trúc cho mọi run | P14 | `RunReport` đủ `gates`, `checks`, `cost`, `timing` |
| FR-16 | Review có ba nhánh quyết định, phê duyệt gắn với `files_hash` | P15 | File đổi làm phê duyệt mất hiệu lực |
| FR-17 | Đưa pipeline đã duyệt lên production qua pull request và CI/CD | P16 | Mức D1/D2 ở mục 9.5 |
| FR-18 | Lưu mẫu đã duyệt để tái sử dụng làm few-shot | P17 | Chỉ lưu khi deploy thành công |
| FR-19 | Hiển thị chính sách hiện hành ở chế độ chỉ đọc | — | 3 file YAML hiển thị đúng nội dung trong git |
| FR-20 | Ghi sổ bằng chứng và truy vết được mọi quyết định | Mọi phase | Mỗi file deploy truy về một `run_id` và một chữ ký |
| FR-21 | Thu số đo của mọi lần chạy pipeline đã deploy | Day 2 | Sổ bằng chứng có chuỗi thời gian theo từng pipeline |
| FR-22 | Phát hiện suy giảm theo luật khai báo, không dùng LLM | Day 2 | 4 luật ở mục 9.6, ngưỡng đọc từ `objectives.yml` |
| FR-23 | Tự tạo run sửa khi phát hiện suy giảm | Day 2 → P4 | Run mới có `trigger = "regression"` và bằng chứng suy giảm đính kèm |
| FR-24 | Không làm phiền kỹ sư khi không có bản sửa tốt hơn | P13–P14 | Run kết thúc với `NO_IMPROVEMENT`, không vào hàng đợi review, chỉ gắn cờ |
| FR-25 | Không tự deploy bản sửa từ Day 2 | P15 | Mọi run `regression` bắt buộc qua cổng người |
| FR-26 | Sinh một pipeline đọc nhiều nguồn khác loại, làm sạch từng nguồn ở staging rồi join ở gold | P1–P12 | Spec có ≥ 2 phần tử `sources` khác `kind` chạy qua đủ 6 cổng; checks ingestion và staging tách theo `source_id` |
| FR-27 | Trợ lý chat là cửa vào hội thoại của agent, chỉ hành động qua API đã có | Mọi phase | Tin nhắn được phân loại đúng 4 ý định ở mục 14.5; cổng P2 và P15 chỉ qua nút; câu trả lời `ask` truy về trường trong `RunReport` |
| FR-28 | Chuẩn hóa dữ liệu từng nguồn trước khi join: đổi tên, ép kiểu, chuẩn hóa ngày và tiền tệ, loại trùng, tách bản ghi rác | P6, P11–P12 | Dòng lỗi không làm hỏng model mà vào bảng `__rejected` kèm lý do; tỷ lệ rejected vượt `objectives.yml: quality.max_reject_pct` thì `staging_reconcile` fail |
| FR-29 | Engineer hủy được run đang chạy của mình | P0–P13 | Run về `CANCELLED`, không gọi thêm LLM, sandbox bị xóa, sổ bằng chứng ghi phase lúc hủy; không hủy được từ `READY_FOR_REVIEW` trở đi |

---

## 5. Yêu cầu phi chức năng

| NFR | Yêu cầu | Cách kiểm |
|---|---|---|
| NFR-01 | Agent không có credential prod trong môi trường | Test bất biến, không phải lời dặn trong prompt |
| NFR-02 | Không node LLM nào thực thi lệnh shell | Rà mã nguồn, test bất biến |
| NFR-03 | Mọi cổng là hàm thuần, có unit test | Độ phủ test của `control_plane/gates/` |
| NFR-04 | Mọi trần và ngân sách đọc từ `policy/*.yml`, không hardcode | Đổi giá trị trong YAML thì hành vi đổi theo, không cần build lại |
| NFR-05 | Một run không treo vô hạn | Trần gọi LLM và timeout mỗi phase, đọc từ `budgets.yml: per_run.phase_timeout_sec`; quá hạn → `INFRA_ERROR` ghi phase bị treo |
| NFR-06 | Đổi warehouse chỉ là đổi một adapter; thêm loại nguồn chỉ là thêm một `SourceAdapter`; đổi vector DB chỉ là đổi một `VectorStore` | `WarehouseAdapter`, `SourceAdapter` (mục 12) và `VectorStore` (mục 14.4) |
| NFR-07 | Đổi model LLM không đụng `control_plane/` | Ranh giới module |
| NFR-08 | Mọi con số hiển thị truy được về một phép đo trong `RunReport` | Rà UI |
| NFR-09 | Tái hiện được quá trình khi cần giải trình | `spec_history` lưu mọi phiên bản |
| NFR-10 | Giao diện truy cập được bằng bàn phím, màu luôn kèm chữ trạng thái | Rà các màn ở mục 16 |

---

## 6. Tech stack

| Lớp | Công cụ | Ghi chú |
|---|---|---|
| Agent | LangGraph | 4 node: planner → codegen → test-run → optimizer (mục 14.2) |
| LLM | OpenAI (API) | Model rẻ cho phân loại, model mạnh cho sinh spec. Claude chỉ là trợ lý lập trình cho nhóm, **không** nằm trong runtime |
| Ingestion (E-L) | dlt | Bổ sung so với đề bài gốc; đề bài không có công cụ cho E-L |
| Transform (T) | dbt-core + dbt-duckdb | dbt-bigquery cho demo cổng chi phí |
| Orchestration | Dagster (`dagster-dbt`, `dagster-dlt`) | Nhẹ hơn Airflow, asset model hợp với lineage |
| Sandbox | DuckDB | Môi trường phát triển hằng ngày |
| Cloud warehouse | BigQuery | Chỉ để chứng minh `dry_run` và cổng chi phí |
| Phân tích SQL | sqlglot | Phân tích **tĩnh** (AST). Query plan lấy từ `EXPLAIN ANALYZE` / `dry_run` |
| Vector DB | Chroma (MVP) → Qdrant (sản phẩm thật) | Lưu spec **đã duyệt** làm few-shot. Truy cập qua `VectorStore` protocol, đổi Chroma sang Qdrant chỉ là đổi một adapter |
| Backend | FastAPI | SSE streaming |
| Frontend | Next.js + Monaco (`@monaco-editor/react` DiffEditor) | |
| Nguồn | Postgres container + FastAPI mock API + thư mục file (CSV/Parquet/JSONL) | Đủ 3 loại `sources[].kind`, dùng riêng hoặc kết hợp trong một pipeline; mỗi loại một `SourceAdapter` (mục 12.1) |
| Đóng gói | docker-compose | Cloud Run cho FastAPI + Next.js ở bản deploy |

**Lưu ý thuật ngữ:** đề bài gốc viết "sqlglot phân tích query plan". sqlglot không đọc query plan, nó parse SQL thành AST. Query plan thật đến từ `EXPLAIN ANALYZE` (DuckDB) và `dry_run` (BigQuery). Ghi đúng điều này trong báo cáo.

---

## 7. Cấu trúc repo

```
.
├── SPEC.md
├── Makefile
├── policy/
│   ├── domains.yml
│   ├── budgets.yml
│   └── objectives.yml
├── templates/
│   ├── dlt_pipeline.py.j2     # 1 file ingestion, gọi macro cho từng nguồn
│   ├── sources/
│   │   ├── _postgres.j2       # macro resource cho postgres
│   │   ├── _rest_api.j2       # macro resource cho rest_api
│   │   └── _filesystem.j2     # macro resource cho filesystem
│   ├── dbt_staging.sql.j2     # 1 model staging cho mỗi nguồn
│   ├── dbt_model.sql.j2       # model gold
│   ├── dbt_schema.yml.j2
│   └── dagster_asset.py.j2
├── control_plane/
│   ├── models.py            # pydantic: Spec, RunReport, GateResult, ExecResult
│   ├── render.py            # spec -> {path: content}
│   ├── execute.py           # chạy dlt + dbt trên sandbox
│   ├── evidence.py          # sổ bằng chứng
│   ├── sandbox.py           # tạo/huỷ sandbox cô lập
│   ├── policy.py            # nạp và xác thực 3 file YAML
│   ├── arbiter.py           # trọng tài theo objectives.yml
│   ├── entity_scan.py       # dò trùng thực thể
│   ├── monitor.py           # Day 2: thu số đo sau mỗi lần pipeline chạy
│   ├── detectors.py         # Day 2: 4 luật phát hiện suy giảm
│   ├── adapters/
│   │   ├── base.py          # WarehouseAdapter protocol
│   │   ├── duckdb_adapter.py
│   │   └── bigquery_adapter.py
│   ├── sources/
│   │   ├── base.py          # SourceAdapter protocol
│   │   ├── postgres.py
│   │   ├── rest_api.py
│   │   └── filesystem.py
│   └── gates/
│       ├── spec_schema.py
│       ├── schema_real.py
│       ├── semantic.py
│       ├── ast_guard.py
│       ├── budget.py
│       └── post_run.py
├── agent/
│   ├── graph.py             # LangGraph, máy trạng thái
│   ├── state.py
│   ├── router.py            # bảng định tuyến lỗi (tất định)
│   ├── chat.py              # trợ lý chat: phân loại ý định, trả lời từ RunReport (mục 14.5)
│   ├── nodes/
│   │   ├── planner.py
│   │   ├── codegen.py       # sinh spec; chế độ sql_fix sửa biểu thức SQL
│   │   ├── test_run.py      # tất định: P6–P12
│   │   └── optimizer.py
│   ├── prompts/
│   └── memory/
│       ├── base.py          # VectorStore protocol
│       ├── chroma_store.py  # MVP
│       └── qdrant_store.py  # sản phẩm thật
├── api/
│   └── main.py
├── web/
├── infra/
│   ├── docker-compose.yml
│   ├── seeds/               # SQL seed dữ liệu bẩn có chủ đích (postgres)
│   ├── mock_api/            # FastAPI giả lập nguồn REST, có dữ liệu bẩn
│   ├── landing/             # file nguồn CSV/Parquet/JSONL, có dữ liệu bẩn
│   └── dagster/
├── dbt_project/
├── fixtures/                # spec_01..05.json viết tay
└── eval/
    ├── cases/               # 15 ca, mục 18.2
    └── run_eval.py
```

---

## 8. Hợp đồng dữ liệu

Bốn cấu trúc dưới đây là ranh giới giữa các module. **Sửa chúng phải báo cả nhóm trong ngày.**

### 8.1 Spec (JSON)

**Đa nguồn.** Một pipeline có thể đọc **nhiều nguồn** thuộc cả ba loại. Dữ liệu đi qua ba tầng:

| Tầng | Làm gì | Mỗi pipeline có |
|---|---|---|
| **bronze** | Bản sao thô của từng nguồn, không sửa gì (dlt) | 1 bảng cho mỗi nguồn |
| **staging** | Làm sạch và chuẩn hóa **từng nguồn**: đổi tên cột, ép kiểu, chuẩn hóa ngày và tiền tệ, loại trùng, tách bản ghi rác (dbt, `stg_*`) | 1 model cho mỗi nguồn + 1 bảng `__rejected` |
| **gold** | Join các bảng staging đã sạch và tính chỉ số (dbt) | 1 model |

Các nguồn **chỉ gặp nhau ở gold**, sau khi mỗi nguồn đã được làm sạch riêng. Pipeline một nguồn là trường hợp `sources` có một phần tử, vẫn đi qua staging.

```json
{
  "spec_version": "1.2",
  "domain": "sales",
  "sources": [
    {
      "id": "orders",
      "kind": "postgres",
      "source_ref": "sales_pg",
      "table": "orders",
      "primary_key": ["order_id"],
      "cursor_field": "updated_at",
      "write_disposition": "merge",
      "target": { "dataset": "bronze", "table": "raw_orders" },
      "staging": {
        "columns": [
          { "from": "order_id",   "as": "order_id",   "type": "bigint" },
          { "from": "created_at", "as": "order_date", "type": "date" },
          { "from": "channel",    "as": "channel",    "type": "varchar" },
          { "from": "status",     "as": "status",     "type": "varchar" },
          { "from": "amount",     "as": "amount_vnd", "type": "decimal(18,2)",
            "currency": { "code_column": "currency", "to": "VND" } }
        ],
        "dedupe": { "keys": ["order_id"], "keep": "latest", "order_by": "updated_at" },
        "reject_if": ["order_id IS NULL", "amount_vnd < 0"]
      }
    },
    {
      "id": "payments",
      "kind": "rest_api",
      "source_ref": "sales_api",
      "endpoint": "/payments",
      "records_path": "data",
      "pagination": "page",
      "primary_key": ["payment_id"],
      "cursor_field": "updated_at",
      "write_disposition": "merge",
      "target": { "dataset": "bronze", "table": "raw_payments" },
      "staging": {
        "columns": [
          { "from": "payment_id", "as": "payment_id", "type": "varchar" },
          { "from": "orderId",    "as": "order_id",   "type": "bigint" },
          { "from": "paid_at",    "as": "paid_date",  "type": "date",
            "date_formats": ["%Y-%m-%d", "%d/%m/%Y"] },
          { "from": "total",      "as": "amount_vnd", "type": "decimal(18,2)",
            "currency": { "code_column": "ccy", "to": "VND" } }
        ],
        "dedupe": { "keys": ["payment_id"], "keep": "latest", "order_by": "updated_at" },
        "reject_if": ["order_id IS NULL", "amount_vnd <= 0"]
      }
    }
  ],
  "transform": {
    "model_name": "fct_daily_revenue",
    "from": "orders",
    "joins": [
      { "source": "payments", "type": "left",
        "on": [["orders.order_id", "payments.order_id"]] }
    ],
    "materialization": "incremental",
    "unique_key": ["order_date", "channel"],
    "grain": ["orders.order_date", "orders.channel"],
    "metrics": [
      { "name": "revenue", "expr": "SUM(payments.amount_vnd)" },
      { "name": "order_count", "expr": "COUNT(DISTINCT orders.order_id)" }
    ],
    "filters": ["orders.status <> 'cancelled'"],
    "partition_by": "order_date",
    "tests": [
      { "column": "order_date", "test": "not_null" },
      { "column": "revenue", "test": "not_negative" }
    ]
  },
  "schedule": { "cron": "0 * * * *" },
  "estimates": { "bytes_scanned": 0, "usd": 0.0, "latency_sec": 0 }
}
```

Trường riêng theo loại nguồn (ngoài `id`, `kind`, `source_ref`, `primary_key`, `cursor_field`, `write_disposition`, `target`, `staging`):

| `kind` | Trường bắt buộc | Ví dụ |
|---|---|---|
| `postgres` | `table` | `"table": "orders"` |
| `rest_api` | `endpoint`, `records_path`, `pagination` ∈ `{page, offset, cursor, none}` | `"endpoint": "/payments", "records_path": "data", "pagination": "page"` |
| `filesystem` | `path_pattern`, `format` ∈ `{csv, parquet, jsonl}` | `"path_pattern": "refunds/*.csv", "format": "csv"` |

Ràng buộc:
- `1 ≤ len(sources) ≤ budgets.yml: rules.max_tables_joined`
- `sources[].id` duy nhất trong spec, là bí danh dùng trong `transform`; `sources[].target.table` duy nhất
- `sources[].kind` ∈ `{postgres, rest_api, filesystem}` và phải khớp `kind` của nguồn mà `source_ref` trỏ tới
- `source_ref` trỏ tới một nguồn trong `domains.yml: <spec.domain>.sources`. Đọc nguồn của domain khác bị cổng `semantic` chặn, trừ khi thực thể liên quan nằm trong `conformed_dimensions`
- `write_disposition` ∈ `{append, replace, merge}`; `merge` bắt buộc có `primary_key`
- `cursor_field` bắt buộc khi load tăng dần (`merge` hoặc `append`). Với `postgres` và `rest_api` là một cột/trường thời gian hoặc số tăng; với `filesystem` là `modification_date` (thời điểm sửa file). Nguồn không có cursor thì bắt buộc `write_disposition = replace`
Ràng buộc của khối `staging` (bắt buộc với **mọi** nguồn):
- `columns[].from` phải tồn tại trong `schema_snapshot` của nguồn; `columns[].as` duy nhất trong nguồn. Cột không khai báo thì không đi tiếp sang staging
- `columns[].type` ∈ `{bigint, integer, decimal(p,s), double, varchar, boolean, date, timestamp}`. Ép kiểu luôn dùng `TRY_CAST`: dòng ép kiểu thất bại bị **tách sang bảng rejected**, không làm lỗi cả model
- Cột thô dạng chuỗi ép sang `date`/`timestamp` bắt buộc có `date_formats`, thử lần lượt theo thứ tự khai báo; không khớp định dạng nào thì dòng bị tách sang rejected
- `currency.code_column` là cột mã tiền tệ ở nguồn; tỷ giá đọc từ `domains.yml: <domain>.currency.rates` (tỷ giá cố định khai báo, không tra cứu bên ngoài). Mã tiền tệ không có trong bảng tỷ giá thì dòng bị tách sang rejected
- `dedupe.keys` mặc định bằng `primary_key`; `keep` ∈ `{latest, first}` theo `order_by`. Số dòng bị loại vì trùng được đếm, không tính là rejected. Với `write_disposition = merge`, dlt đã gộp theo `primary_key` ngay ở bronze nên `dedupe` thường đếm 0; `dedupe` là nơi xử lý trùng chính khi nguồn load `append` hoặc `replace`
- `reject_if` là biểu thức điều kiện trên **cột đầu ra** của staging; qua cổng `ast_guard`. Dòng thỏa điều kiện bị tách sang rejected kèm lý do
- Mọi dòng bị tách đều ghi vào `stg_<model_name>__<id>__rejected` với cột `_reject_reason`, để đếm và để người xem lại

Ràng buộc của `transform`:
- `transform.from` là một `sources[].id`. Mọi nguồn khác xuất hiện **đúng một lần** trong `transform.joins` — không có nguồn thừa
- `transform` **chỉ** tham chiếu cột đầu ra của staging (`<id>.<as>`), không bao giờ tham chiếu cột thô ở bronze
- `joins[].type` ∈ `{inner, left}`; `joins[].on` bắt buộc khác rỗng (không có tích Descartes). Khóa join phải có **cùng `type` sau staging** ở cả hai nguồn (cổng `schema_real`); khác kiểu ở nguồn thô thì staging ép về cùng kiểu
- Khi có từ hai nguồn trở lên, cột trong `grain`, `metrics`, `filters` viết đủ dạng `<id>.<cột>`. Tên cột đầu ra là phần sau dấu chấm; `unique_key`, `partition_by`, `tests` dùng tên đầu ra
- `transform.materialization` ∈ `{view, table, incremental}`; `incremental` bắt buộc có `unique_key`
- `metrics[].expr` chỉ được chứa hàm tổng hợp và tên cột; qua cổng `ast`
- Viết JSON Schema tương ứng tại `control_plane/schemas/spec.schema.json`

### 8.2 RunReport (JSON)

```json
{
  "run_id": "uuid",
  "version": 3,
  "domain": "sales",
  "created_by": "minh",
  "trigger": "user_request",
  "parent_run_id": null,
  "regression": null,
  "created_at": "iso8601",
  "user_request": "…",
  "status": "READY_FOR_REVIEW",
  "stage_reached": "report",
  "plan": { "…": "…" },
  "spec": { "…": "…" },
  "spec_history": [],
  "files": { "models/fct_daily_revenue.sql": "sha256:…" },
  "files_hash": "sha256:…",
  "gates": [
    { "name": "budget", "result": "pass",
      "detail": "1.2 GB ước tính, trần 5 GB",
      "observed": { "bytes": 1234567890 } }
  ],
  "checks": [
    { "name": "row_reconcile", "source_id": "orders", "result": "pass",
      "observed": { "source": 12480, "bronze": 12480 } },
    { "name": "row_reconcile", "source_id": "payments", "result": "pass",
      "observed": { "source": 11902, "bronze": 11902 } },
    { "name": "idempotent", "source_id": "orders", "result": "pass",
      "observed": { "run1": 12480, "run2": 12480 } },
    { "name": "staging_reconcile", "source_id": "payments", "result": "pass",
      "observed": { "bronze": 11902, "staging": 11750, "duplicates": 120,
                    "rejected": 32, "reject_pct": 0.27,
                    "reject_reasons": { "date_format": 20, "unknown_currency": 12 },
                    "reject_samples": [
                      { "_reject_reason": "unknown_currency", "payment_id": "P-881", "ccy": "EUR" }
                    ] } },
    { "name": "dbt_test", "result": "pass",
      "observed": { "passed": 6, "failed": 0 } },
    { "name": "metric_reconcile", "result": "pass",
      "observed": { "gold_sum": 8123456.0, "staging_sum": 8123456.0 } }
  ],
  "cost": { "estimated_bytes": 0, "actual_bytes": 0, "usd": 0.0 },
  "timing": { "baseline_sec": 4.2, "optimized_sec": 0.9 },
  "optimizer": [
    { "rule": "table_to_incremental", "applied": true,
      "before_sec": 4.2, "after_sec": 0.9 }
  ],
  "attempts": { "spec": 1, "sql": 2 },
  "llm_calls": 5,
  "error_trace": [],
  "approval": {
    "decision": "approved",
    "reviewer": "an",
    "files_hash": "sha256:…",
    "version": 3,
    "at": "iso8601",
    "comment": "…"
  }
}
```

Về các trường định danh:
- **`version`**: một run có nhiều version. Mỗi lần quay lại P4 do con người (Yêu cầu sửa, `revise` trong chat, chạy lại sau khi hủy) tạo version mới cùng `run_id`, có `files_hash` mới và **xóa hiệu lực** mọi `approval` cũ. Vòng spec và vòng SQL tự động **không** tạo version mới
- **`created_by`**: Engineer tạo run, lấy từ phiên đăng nhập. Là căn cứ duy nhất của quy tắc cấm tự duyệt: `approval.reviewer != created_by`
- **`approval`**: `null` cho tới P15. `decision` ∈ `{approved, rejected, changes_requested}`. Đây là "chữ ký phê duyệt" ở bất biến 3, mục 19
- **`reject_samples`**: tối đa 5 dòng rejected mỗi lý do, chép vào report **trước khi xóa sandbox**, để màn Staging vẫn xem được sau khi run kết thúc

Nội dung đầy đủ của các file sinh ra được lưu trong sổ bằng chứng theo `(run_id, version)`, không chỉ hash, để màn Review hiện diff sau khi sandbox đã bị xóa.

Ba phép kiểm tra ingestion (`row_reconcile`, `hash_reconcile`, `idempotent`) và `staging_reconcile` có **một dòng cho mỗi nguồn**, phân biệt bằng `source_id`. `dbt_test` và `metric_reconcile` là của model, không có `source_id`.

`schema_snapshot` trong `AgentState` (mục 14.1) là một dict theo `sources[].id`; mỗi phần tử luôn có trường `origin` ∈ `{information_schema, openapi, file_sample}` để truy được schema lấy từ đâu (FR-04).

### 8.3 Sự kiện SSE

```json
{ "run_id": "…", "stage": "el_run", "status": "running",
  "message": "Đang chạy dlt…", "payload": {} }
```

Tập `stage` cố định, **bằng đúng id phase ở mục 9**, không có tên riêng:
`intake, plan, confirm, resolve, propose, arbitrate, render, static, budget, el_run, el_validate, t_run, t_validate, optimize, report, review, deploy, persist`

Ánh xạ `P0→intake`, `P1→plan`, … `P17→persist`. Bất kỳ tên nào ngoài 18 giá trị này là lỗi hợp đồng.

`status` ∈ `{running, pass, fail, escalate, blocked, cancelled}`

### 8.4 Ba file chính sách

**`policy/domains.yml`**

```yaml
domains:
  sales:
    datasets: [sales_raw]
    steward: an@company.vn
    currency:                         # dùng cho staging.columns[].currency
      base: VND
      rates: { VND: 1, USD: 25400 }   # tỷ giá cố định, steward cập nhật
    sources:                          # mọi nguồn domain này được phép đọc
      sales_pg:      { kind: postgres,   connection_ref: sandbox_pg,    schema: sales_raw }
      sales_api:     { kind: rest_api,   connection_ref: mock_api,      openapi: /openapi.json }
      landing_sales: { kind: filesystem, connection_ref: landing_sales, root: infra/landing/sales }
    entities:
      customer:
        canonical: false
        definition: "tài khoản đã ký hợp đồng"
        tables: [sales_pg.customers, sales_pg.accounts]
      order:
        canonical: true
        definition: "đơn hàng đã xác nhận"
        tables: [sales_pg.orders]
      payment:
        canonical: true
        definition: "khoản thanh toán đã ghi nhận cho một đơn hàng"
        tables: [sales_api.payments]
      refund:
        canonical: true
        definition: "khoản hoàn tiền đã duyệt"
        tables: [landing_sales.refunds]
  marketing:
    datasets: [mkt_raw]
    steward: binh@company.vn
    currency:
      base: VND
      rates: { VND: 1, USD: 25400 }
    sources:
      mkt_api: { kind: rest_api, connection_ref: mock_api, openapi: /openapi.json }
    entities:
      customer:
        canonical: false
        definition: "người để lại email"
        tables: [mkt_api.leads, mkt_api.contacts]

conformed_dimensions: []   # rỗng nghĩa là chưa có thực thể nào được công nhận dùng chung
```

Quy ước tham chiếu đối tượng nguồn: `<source_id>.<tên>`, trong đó `<tên>` là tên bảng (`postgres`), tên endpoint bỏ dấu `/` (`rest_api`), hoặc thư mục con chứa file (`filesystem`). `entity_scan` quét mọi đối tượng khai báo ở đây, bất kể loại nguồn. Spec tham chiếu nguồn bằng `source_ref` = `<source_id>`; thông tin kết nối (`connection_ref`) chỉ nằm ở file này.

**`policy/budgets.yml`**

```yaml
pricing:
  usd_per_tb: 6.25          # đơn giá BigQuery on-demand; DuckDB quy đổi ước tính theo số này
per_run:
  max_bytes_scanned: 5_000_000_000
  max_usd: 0.05
  max_llm_calls: 6          # trần toàn cục 1 run; router VÀ UI đều đọc từ đây
  max_chat_calls: 20        # trần câu hỏi `ask` trong chat cho 1 run (mục 14.5), tách khỏi max_llm_calls
  max_spec_attempts: 2      # vòng spec, mục 11.3
  max_sql_attempts: 2       # vòng SQL, mục 11.3
  phase_timeout_sec: 300    # một phase chạy quá thời gian này thì dừng (NFR-05)
per_day:
  max_usd: 2.00             # tổng chi phí mọi run trong ngày; cổng budget cộng dồn (mục 10)
rules:
  - require_partition_filter_on: [sales_pg.orders]
  - forbid_select_star_in: [gold]
  - max_tables_joined: 6
```

**`policy/objectives.yml`**

```yaml
sales:
  hard:
    freshness_minutes: 60
  soft:
    cost_usd_per_day: 1.0
  prefer: cheapest_feasible
  degrade_order: [freshness, completeness]
  quality:
    max_reject_pct: 1.0           # staging: tỷ lệ dòng bị tách sang rejected tối đa, theo từng nguồn
  regression:                     # Day 2 — ngưỡng phát hiện suy giảm
    window_runs: 7                # so trung vị 7 lần chạy gần nhất
    runtime_increase_pct: 50      # chậm hơn đường cơ sở 50% thì báo
    cost_increase_pct: 40
    test_fail_rate_pct: 5
    cooldown_hours: 24            # không tạo run sửa trùng cho cùng pipeline trong 24h
marketing:
  hard:
    freshness_minutes: 1440
  soft:
    cost_usd_per_day: 0.5
  prefer: cheapest_feasible
  degrade_order: [freshness, completeness]
  quality:
    max_reject_pct: 2.0
  regression:
    window_runs: 7
    runtime_increase_pct: 50
    cost_increase_pct: 40
    test_fail_rate_pct: 5
    cooldown_hours: 24
```

Mọi domain **bắt buộc** có khối `quality` và `regression`. `policy.py` từ chối nạp `objectives.yml` nếu một domain khai báo trong `domains.yml` thiếu một trong hai khối này.

---

## 9. Workflow runtime — 18 phase P0–P17

**P0–P17 là bộ định danh duy nhất của dự án.** Dùng ở `SPEC.md`, trên giao diện, trong mã nguồn, trong SSE `stage`, trong log. Không có hệ đánh số thứ hai.

Mỗi phase ghi state xuống sổ bằng chứng trước khi sang phase sau.

| P | id | Loại | Mô tả |
|---|---|---|---|
| P0 | `intake` | Tất định | Sinh `run_id`, tạo sandbox mới, ghi bản ghi run |
| P1 | `plan` | LLM | Sinh kế hoạch: các nguồn và đối tượng cần đọc, cách join giữa chúng, độ hạt, chỉ số, tần suất. Tối đa 1 câu hỏi làm rõ, hỏi trong khung chat (mục 14.5) |
| P2 | `confirm` | **Người** | Cổng HITL 1: Engineer xác nhận kế hoạch bằng nút bấm |
| P3 | `resolve` | Tất định | Với **từng nguồn** trong kế hoạch: schema thật qua `SourceAdapter.get_schema` (mục 12.1), 20 dòng mẫu; lineage từ dbt manifest; `entity_scan` → cổng `semantic` |
| P4 | `propose` | LLM | Sinh JSON spec. Nhiều phương án khả dĩ thì sinh cả hai kèm số đo |
| P5 | `arbitrate` | Tất định | Đối chiếu phương án với `objectives.yml` |
| P6 | `render` | Tất định | Jinja render 4 + N file: ingestion (một resource cho mỗi nguồn), N model staging, model gold, schema test, asset Dagster. Lỗi ở đây là bug template → `INTERNAL_ERROR` |
| P7 | `static` | Tất định | `compile()` + `sqlglot.parse` + cổng `spec_schema`, `schema_real`, `ast_guard` |
| P8 | `budget` | Tất định | `dry_run` lấy byte, quy USD, cổng `budget`. Tính trên **tổng** mọi nguồn cộng model |
| P9 | `el_run` | Tất định | Chạy dlt đổ **mọi nguồn** vào bronze trên sandbox, mỗi nguồn một bảng |
| P10 | `el_validate` | Tất định | 3 phép `row_reconcile`, `hash_reconcile`, `idempotent`, chạy **cho từng nguồn**; một nguồn fail là cả phase fail |
| P11 | `t_run` | Tất định | `dbt run`: các model staging trước, rồi model gold |
| P12 | `t_validate` | Tất định | `staging_reconcile` cho từng nguồn + `dbt test` + `metric_reconcile` |
| P13 | `optimize` | Hỗn hợp | Đo baseline, áp luật, chạy lại, đo. **Không bao giờ làm fail run** |
| P14 | `report` | Tất định | Gom RunReport, tính `files_hash`, lấy chi phí thật |
| P15 | `review` | **Người** | Cổng HITL 2: duyệt / từ chối / yêu cầu sửa |
| P16 | `deploy` | Tất định + **Người** | Chỉ mở pull request khi `approval.hash == files_hash`. CI chạy lại test, người có quyền merge, CI/CD deploy lên Dagster production. Mức D1/D2 xem mục 9.5 |
| P17 | `persist` | Tất định | Lưu (yêu cầu, spec) vào vector DB làm few-shot |

### 9.0 Nhóm phase cho hiển thị

UI không hiện 18 chấm. Gom thành 6 nhóm, bung ra mới thấy phase con. Nhóm là **khái niệm hiển thị**, không phải khái niệm runtime.

| Nhóm | Phase | Nhãn UI |
|---|---|---|
| G1 | P0–P2 | Tiếp nhận |
| G2 | P3 | Phân giải |
| G3 | P4–P6 | Sinh |
| G4 | P7–P8 | Kiểm |
| G5 | P9–P13 | Chạy |
| G6 | P14–P17 | Quyết định |

### 9.1 Chi tiết `resolve` — dò trùng thực thể

```python
def entity_scan(request: str, catalog: Catalog, domains: DomainRegistry) -> EntityConflict | None
```

Thuật toán:
1. Trích danh từ thực thể từ yêu cầu (LLM hoặc từ khoá, MVP dùng từ khoá + danh sách trong `domains.yml`)
2. Tìm mọi đối tượng nguồn khớp thực thể đó trên toàn catalog. Catalog là **mọi nguồn** khai báo trong `domains.yml`, gồm cả bảng Postgres, endpoint REST và thư mục file
3. Nếu các đối tượng khớp nằm ở **nhiều hơn một domain** và thực thể chưa nằm trong `conformed_dimensions` → trả về `EntityConflict`
4. Chặng dừng ngay với `status = BLOCKED_SEMANTIC_CONFLICT`, tạo phiếu chuyển steward

Chặn ở đây, **trước** khi tốn bất kỳ lần gọi LLM nào cho việc sinh code.

Thông điệp trả về phải nêu rõ cả hai định nghĩa, ví dụ:

> Phát hiện thực thể `customer` ở 2 domain. Sales định nghĩa là tài khoản đã ký hợp đồng (2 bảng Postgres). Marketing định nghĩa là người để lại email (2 endpoint REST). Chưa có định nghĩa chuẩn. Chuyển tới steward domain Sales: an@company.vn

### 9.2 Chi tiết `arbitrate`

```python
def arbitrate(proposals: list[Proposal], objectives: DomainObjectives) -> ArbitrationResult
```

- Lọc các phương án thoả **toàn bộ** ràng buộc `hard`
- Nếu rỗng → `ArbitrationResult(status="escalate", tradeoff_table=[...])`
- Nếu có → chọn theo `prefer` (MVP chỉ cần `cheapest_feasible`)
- Ghi lý do chọn vào report

### 9.3 Chi tiết `optimize`

Hai luật cố định:

1. **`table_to_incremental`**: nếu `materialization == "table"` và bảng nguồn có cột thời gian và ước tính số dòng > ngưỡng → đề xuất `incremental` với `unique_key` từ `grain`
2. **`missing_partition_filter`**: nếu SQL không có điều kiện trên cột `partition_by` → cảnh báo và đề xuất thêm

Quy trình: đo baseline bằng `EXPLAIN ANALYZE` → render bản tối ưu → cho bản tối ưu qua cổng `ast_guard` và `budget` → chạy lại **P11 và P12** trên bản đó → đo lại. Nếu bản tối ưu không qua cổng, sai hoặc chậm hơn, **vứt đi, giữ bản gốc, ghi lý do**.

Optimizer ở MVP là tất định, không gọi LLM. Với `trigger = "manual_optimize"` (UC-3), đây là bước duy nhất tạo ra thay đổi; nếu không luật nào cho bản tốt hơn thì run không có gì để duyệt.

### 9.4 Chi tiết `el_validate` và `t_validate`

Năm phép kiểm tra ở mục 10.1 là **bắt buộc ở backend**, không phụ thuộc vào việc UI có hiện ô tương ứng hay không. UI chỉ quyết định cách trình bày.

### 9.5 Chi tiết `deploy` — pull request, CI/CD, hai mức D1 và D2

Luồng chung của P16:

1. Kiểm tra `approval.files_hash == files_hash` và `approval.version == version`. Không khớp thì không làm gì
2. Tạo nhánh `pipeline/<model_name>/v<version>`, commit 4 + N file, mở pull request. Mô tả pull request gồm `run_id`, `files_hash`, chữ ký duyệt và link RunReport
3. CI chạy lại `dbt compile`, `dbt test` trên dữ liệu kiểm thử và kiểm tra `files_hash` của commit khớp với chữ ký
4. Người có quyền merge bấm merge (branch protection của repo). Sau khi merge, CI/CD của tổ chức deploy lên Dagster production
5. `status` giữ `APPROVED` trong lúc chờ merge; deploy xong chuyển `DEPLOYED`

| Mức | Hành vi | Tiêu chí nghiệm thu |
|---|---|---|
| **D1** | Mở pull request như trên. Sau khi merge, CI/CD ghi file vào code location của Dagster production và nạp lại | Pull request có đủ file và RunReport; sau merge asset hiện trong đồ thị Dagster production; `status = DEPLOYED` |
| **D2** | D1 cộng: sau deploy, CI/CD trigger một lần chạy, chờ xanh trong 120s; không xanh thì revert commit, deploy lại bản trước, `DEPLOY_FAILED` | Run xanh, **hoặc** commit đã bị revert, bản trước đang chạy và trạng thái đúng là `DEPLOY_FAILED` |

MVP dừng ở **D1**. D2 là thiết kế đích. Cả hai mức đều yêu cầu `approval.hash == files_hash`.

Quyền của agent với repo: token chỉ có quyền tạo nhánh và mở pull request, **không** có quyền merge, không đọc được secret của CI/CD.

---

### 9.6 Day 2 — vòng vận hành

Day 2 **không thêm phase mới**. Nó dùng lại nguyên P4–P16, chỉ khác ở chỗ ai kích hoạt run.

#### 9.6.1 Ba loại kích hoạt

| `trigger` | Ai tạo | Vào từ phase | Mang theo |
|---|---|---|---|
| `user_request` | Người dùng | P0 | Yêu cầu ngôn ngữ tự nhiên |
| `manual_optimize` | Engineer trỏ vào pipeline đã deploy | P0, rồi nhảy P6 → P9–P13 (chỉ optimizer, không LLM; mục 3.3) | Spec đang chạy + `parent_run_id` |
| `regression` | `monitor` tự tạo | P0, rồi nhảy P4 | Spec đang chạy + bằng chứng suy giảm + `parent_run_id` |

Mọi run, kể cả run tự động, đều qua **P0** để có `run_id` và sandbox mới (bất biến 1, mục 19). Run `regression` bỏ qua P1–P3 vì spec đã được duyệt và schema đã được phân giải từ lần tạo mới. Riêng P3 chạy lại nếu schema của **bất kỳ nguồn nào** đã đổi so với `schema_snapshot` cũ.

Trạng thái khởi đầu theo `trigger`: `user_request` bắt đầu ở `DRAFT`; `regression` bắt đầu ở `GENERATING` (đã có kế hoạch); `manual_optimize` bắt đầu ở `VALIDATING` (không sinh spec). `created_by` của run tự động là `system`, nên mọi Reviewer đều duyệt được.

#### 9.6.2 Monitor thu gì

`monitor` không quan sát dữ liệu, nó quan sát **các lần chạy**. Cả ba nguồn đều có sẵn, không phải dựng thêm hạ tầng:

| Nguồn | Cho biết |
|---|---|
| Dagster run events (production) | Thời gian chạy, thành công hay thất bại, thời điểm |
| dbt `run_results.json` | Thời gian từng model, test nào fail |
| `adapter.job_cost()` | Byte quét và chi phí mỗi lần chạy |

Sổ bằng chứng chuyển từ "mỗi run một dòng" thành **chuỗi thời gian theo từng pipeline**. Dòng đầu tiên của chuỗi là số đo lúc P14 của run tạo mới — đó là đường cơ sở.

#### 9.6.3 Bốn luật phát hiện

Tất cả tất định, không dùng LLM, ngưỡng đọc từ `objectives.yml: <domain>.regression`.

| Luật | Điều kiện kích hoạt | Hành động |
|---|---|---|
| `runtime_regression` | Trung vị thời gian `window_runs` lần gần nhất > đường cơ sở × (1 + `runtime_increase_pct`) | Tạo run `regression` |
| `cost_regression` | Trung vị chi phí vượt đường cơ sở × (1 + `cost_increase_pct`) | Tạo run `regression` |
| `test_degradation` | Tỷ lệ test fail > `test_fail_rate_pct` | Tạo run `regression` |
| `freshness_breach` | Vi phạm `hard.freshness_minutes` | Xem 9.6.4 |

`cooldown_hours` ngăn tạo run sửa trùng lặp cho cùng một pipeline.

#### 9.6.4 Khi nào không tạo run sửa

Không phải suy giảm nào cũng là lỗi của pipeline:

- **Độ tươi vi phạm vì nguồn ngừng cấp dữ liệu** — kiểm tra số bản ghi mới ở nguồn trước, bằng `SourceAdapter.count` với khoảng từ lần chạy cuối (mục 12.1), áp dụng cho cả ba loại nguồn và cho **từng nguồn** của pipeline. Có nguồn trống thì đây là sự cố hạ tầng, chuyển thành cảnh báo nêu rõ nguồn nào, không tạo run.
- **P13 không tìm được bản nào tốt hơn** — run kết thúc với `NO_IMPROVEMENT`, ghi vào sổ bằng chứng, gắn cờ pipeline, **không** đưa vào hàng đợi review. Kỹ sư chỉ bị làm phiền khi có đề xuất đáng xem.

#### 9.6.5 Hiển thị trong hàng đợi review

Run `regression` hiện trong cùng hàng đợi với run tạo mới, nhưng nhãn khác. Không phải "pipeline mới chờ duyệt" mà là:

> Bản sửa cho `fct_daily_revenue` · đang chậm hơn 62% so với lúc deploy · bản đề xuất đã thử trên sandbox, nhanh hơn 38%, kết quả khớp 100%

Ba con số: mức suy giảm, mức cải thiện, độ khớp. Đủ để kỹ sư quyết định mà không phải mở thêm màn nào.

#### 9.6.6 Ràng buộc

Day 2 **không bao giờ tự deploy**. Mọi run `regression` bắt buộc qua P15. Hệ thống tự phát hiện, tự đề xuất, tự kiểm chứng — nhưng không tự quyết.

---

## 10. Sáu cổng

Mọi cổng là hàm thuần, không gọi LLM, có unit test. **Sáu cổng và năm phép kiểm tra đều bắt buộc trong MVP.** Việc UI rút gọn số ô hiển thị không làm giảm số phép kiểm tra chạy ở backend.

| Cổng | Chữ ký | Kiểm tra | Nguồn luật |
|---|---|---|---|
| `spec_schema` | `(spec) -> GateResult` | Khớp JSON Schema | `schemas/spec.schema.json` |
| `schema_real` | `(spec, catalog) -> GateResult` | Với **từng nguồn**: cột PK tồn tại và không NULL trên dữ liệu mẫu; cursor tồn tại và là timestamp/số tăng. Tính unique của PK **không** kiểm ở đây (dữ liệu nguồn được phép trùng) mà do dlt `merge` / staging `dedupe` xử lý và dbt test `unique` chứng minh. Với **từng join**: khóa có cùng `type` **sau staging** ở cả hai nguồn | `schema_snapshot` (mục 12.1) |
| `semantic` | `(request, catalog, domains) -> GateResult` | Thực thể mơ hồ chưa có định nghĩa chuẩn | `domains.yml` |
| `ast_guard` | `(sql_files) -> GateResult` | Chặn DROP/DELETE/TRUNCATE/ALTER, cross-database ref, SELECT * ở gold, join quá `max_tables_joined`, tích Descartes | sqlglot + `budgets.yml` |
| `budget` | `(sql, adapter, budgets, spent_today) -> GateResult` | **Tổng** `dry_run` bytes của mọi nguồn và model vượt `per_run.max_bytes_scanned` hoặc `per_run.max_usd`; `spent_today` + ước tính vượt `per_day.max_usd`; thiếu partition filter trên bảng bắt buộc. `detail` liệt kê byte theo từng nguồn | `budgets.yml` + sổ bằng chứng (chi phí đã dùng trong ngày) |
| `post_run` | `(exec_result, adapter) -> list[GateResult]` | 5 phép kiểm tra sau chạy | Dữ liệu thật + `objectives.yml: quality` |

`schema_real` với staging: mỗi `staging.columns[].from` tồn tại ở nguồn; ép kiểu và parse ngày được **thử trên 20 dòng mẫu** của P3, dòng mẫu nào thất bại thì ghi vào `detail` để LLM sửa `staging` ngay ở vòng spec, trước khi chạy thật.

### 10.1 Năm phép kiểm tra của `post_run`

| Phép | Cách làm | Pass khi |
|---|---|---|
| `row_reconcile` | `SourceAdapter.count` trong khoảng đã lấy vs `COUNT(*)` ở bronze. Với `merge`, cả hai đầu đếm **số khóa `primary_key` phân biệt** (vì dlt đã gộp trùng ở bronze) | Bằng nhau |
| `hash_reconcile` | `SourceAdapter.key_hashes` vs hash các cột PK ở bronze, trên tập khóa phân biệt | Trùng khớp |
| `idempotent` | Chạy pipeline lần hai | Số dòng không đổi |
| `staging_reconcile` | Với từng nguồn: số dòng bronze = staging + trùng bị loại + rejected | Cân bằng đúng **và** tỷ lệ rejected ≤ `objectives.yml: <domain>.quality.max_reject_pct` |
| `metric_reconcile` | Tổng chỉ số ở gold vs tính lại bằng một câu SQL độc lập trên các bảng staging, join theo đúng `transform.joins` | Chênh lệch < 0.01% |

`idempotent` **không được bỏ qua** trong bất kỳ trường hợp nào.

`row_reconcile`, `hash_reconcile` và `idempotent` chạy **riêng cho từng nguồn** (so nguồn đó với bảng bronze của nó) và ghi một dòng check có `source_id`. Cách đếm và băm ở phía nguồn khác nhau theo loại nguồn, nhưng cổng `post_run` chỉ gọi qua `SourceAdapter`, không biết nguồn là gì:

| Loại nguồn | `count` | `key_hashes` |
|---|---|---|
| `postgres` | `COUNT(*)` có điều kiện theo `cursor_field` | Hash các cột PK bằng SQL |
| `rest_api` | Gọi lần lượt mọi trang theo `pagination`, đếm bản ghi | Hash PK của từng bản ghi đã tải về |
| `filesystem` | Đọc các file khớp `path_pattern` trong khoảng, đếm dòng | Hash PK của từng dòng đã đọc |

---

## 11. Máy trạng thái và định tuyến lỗi

### 11.1 Trạng thái

Luồng chính:
`DRAFT → PLAN_CONFIRMED → GENERATING → VALIDATING → READY_FOR_REVIEW → APPROVED → DEPLOYED`

Kết thúc sớm:
`NEEDS_HUMAN, BLOCKED_SEMANTIC_CONFLICT, BLOCKED_BUDGET, BLOCKED_AST, REJECTED, INFRA_ERROR, INTERNAL_ERROR, DEPLOY_FAILED, CANCELLED, NO_IMPROVEMENT`

**`NO_IMPROVEMENT`** chỉ dùng cho run có `trigger` ∈ `{manual_optimize, regression}` khi P13 không tìm được bản vừa đúng vừa tốt hơn. Run kết thúc ở P14, có đủ RunReport (baseline và lý do từng luật bị loại), **không** vào hàng đợi review. Với `regression`, pipeline gốc được gắn cờ để kỹ sư xem khi rảnh (FR-24).

Chỉ `READY_FOR_REVIEW` mới được duyệt. Chỉ `APPROVED` mới được mở pull request. Chỉ pull request đã merge mới được deploy lên production. Không có đường tắt.

**`CANCELLED` — hủy run.** Engineer sở hữu run được hủy khi run đang ở `DRAFT`, `PLAN_CONFIRMED`, `GENERATING` hoặc `VALIDATING`; không hủy được từ `READY_FOR_REVIEW` trở đi (lúc đó dùng Từ chối). Khi hủy:
- Graph dừng ngay sau phase đang chạy; không gọi thêm LLM, không bắt đầu phase mới
- Sandbox của run bị xóa như khi run kết thúc bình thường (bất biến 1, mục 19)
- Sổ bằng chứng ghi phase đang chạy lúc hủy, người hủy và thời điểm; mọi số đo đã có vẫn giữ nguyên
- SSE phát một sự kiện cuối với `status = cancelled`, rồi đóng stream
- Chạy lại sau khi hủy là tạo **version mới** của run, không mở lại run đã hủy

### 11.2 Bảng định tuyến

Cài trong `agent/router.py` dưới dạng bảng dữ liệu, không phải chuỗi `if`.

| Lỗi tại | Loại lỗi | Đi đâu | Trần |
|---|---|---|---|
| P3 | `semantic` escalate | Dừng, `BLOCKED_SEMANTIC_CONFLICT` | 0 |
| P5 | Không phương án nào khả thi | Dừng, chờ người | 0 |
| P6 | Render lỗi | Dừng, `INTERNAL_ERROR` | 0 |
| P7 | `spec_schema` fail | P4 | 2 |
| P7 | `schema_real` fail | P4 | 2 |
| P7 | SQL sai cú pháp | `codegen` (chế độ `sql_fix`) → P6 | 2 |
| P7 | `ast_guard` fail | Dừng, `BLOCKED_AST` | 0 |
| P8 | `budget` fail | P4, kèm số byte cụ thể phải rút xuống | 2 |
| P9 | Không kết nối được một nguồn | Dừng, `INFRA_ERROR`, ghi `source_id` bị lỗi | 0 |
| P9 | Sai kiểu dữ liệu / sai cột | P4 | 2 |
| P10 | `row_reconcile` hoặc `hash_reconcile` lệch ở một nguồn | P4, kèm `source_id` bị lệch | 2 |
| P10 | `idempotent` fail ở một nguồn | P4, ép xem lại `primary_key` của nguồn đó | 2 |
| P11 | dbt run lỗi | `codegen` (chế độ `sql_fix`) → P6 | 2 |
| P12 | `staging_reconcile` fail vì tỷ lệ rejected vượt ngưỡng | P4, kèm lý do bị tách nhiều nhất theo từng nguồn (ví dụ `date_format: 20 dòng`) để sửa khối `staging` | 2 |
| P12 | dbt test hoặc `metric_reconcile` fail | `codegen` (chế độ `sql_fix`) → P6 | 2 |
| P13 | Bản tối ưu sai/chậm hơn | Bỏ qua, đi tiếp P14 | 0 |
| P16 | CI fail hoặc pull request bị đóng không merge | P4, tạo version mới (vòng người), xóa hiệu lực chữ ký cũ | không tính |
| P16 | D2: Dagster production không xanh trong 120s | Revert commit, deploy lại bản trước, `DEPLOY_FAILED` | 0 |

### 11.3 Ba vòng lặp

Cả hệ thống chỉ có ba đường quay lui. Mọi lỗi khác dừng hẳn.

| Vòng | Đường đi | Trần | Bộ đếm |
|---|---|---|---|
| Vòng spec | P7/P8/P9/P10/P12 (`staging_reconcile`) → P4 | 2 (`budgets.yml: per_run.max_spec_attempts`) | `attempts.spec` |
| Vòng SQL | P7/P11/P12 → `codegen` (chế độ `sql_fix`) → P6 | 2 (`budgets.yml: per_run.max_sql_attempts`) | `attempts.sql` |
| Vòng người | P15 → P4, hoặc P16 (CI fail / không merge) → P4 | không tính | đặt lại `attempts.spec = 0` |

### 11.4 Trần toàn cục

Trần gọi LLM **đọc từ `budgets.yml: per_run.max_llm_calls`**, không hardcode ở bất kỳ đâu. MVP đặt 6. Router và UI cùng đọc một nguồn, nên không thể lệch nhau.

Chạm trần → `NEEDS_HUMAN`, hiển thị toàn bộ `error_trace` lên UI. Một run thất bại có trace rõ ràng vẫn là sản phẩm tốt; một run treo vô hạn thì không.

---

## 12. Adapter warehouse

Trừu tượng để đổi DuckDB ↔ BigQuery chỉ là đổi một lớp.

```python
class WarehouseAdapter(Protocol):
    def dry_run(self, sql: str) -> DryRunResult:      # bytes_estimate, usd_estimate
    def execute(self, sql: str, max_bytes: int | None) -> QueryResult
    def explain(self, sql: str) -> PlanInfo           # wall_time, operators
    def get_schema(self, table: str) -> TableSchema
    def job_cost(self, run_id: str) -> CostInfo       # chi phí thật sau khi chạy
```

**DuckDB:** `dry_run` ước tính byte từ thống kê bảng (số dòng × kích thước dòng trung bình). `usd` quy đổi từ byte theo đơn giá BigQuery on-demand khai báo trong `budgets.yml: pricing.usd_per_tb`, và `DryRunResult.estimated = True` để UI gắn nhãn "ước tính". `explain` dùng `EXPLAIN ANALYZE`. `job_cost` trả cùng giá trị ước tính đó, cũng kèm `estimated = True`.

Lý do quy đổi thay vì trả 0: bảng bằng chứng phải luôn hiện chi phí bằng tiền (mục 16). Một ô trống ở mọi run trừ run BigQuery duy nhất sẽ làm nguyên tắc đó vô nghĩa. Nhãn "ước tính" giữ cho con số không bị hiểu nhầm là chi phí thật.

**BigQuery:** `dry_run` dùng `QueryJobConfig(dry_run=True)` → `total_bytes_processed`, quy ra USD theo đơn giá. `execute` đặt `maximum_bytes_billed`. Mọi job gắn label `run_id`. `job_cost` truy vấn `INFORMATION_SCHEMA.JOBS_BY_PROJECT` lọc theo label.

Phần lớn phát triển chạy trên DuckDB. BigQuery chỉ dùng để demo cổng chi phí với `dry_run` thật.

### 12.1 Adapter nguồn

`WarehouseAdapter` là phía **đích**. Phía **nguồn** có protocol riêng, để P3, `schema_real`, `post_run` và `monitor` không phải biết nguồn là Postgres, API hay file.

```python
class SourceAdapter(Protocol):
    def get_schema(self, obj: str) -> TableSchema              # kèm origin
    def sample(self, obj: str, n: int = 20) -> list[dict]
    def count(self, obj: str, since: Any | None) -> int
    def key_hashes(self, obj: str, keys: list[str], since: Any | None) -> str
```

| Loại | `get_schema` lấy từ | `origin` |
|---|---|---|
| `postgres` | `information_schema.columns` và ràng buộc khóa | `information_schema` |
| `rest_api` | Mô tả OpenAPI của mock API (`/openapi.json`) | `openapi` |
| `filesystem` | Suy kiểu từ file mẫu (dòng đầu, tối đa 1.000 dòng) | `file_sample` |

Nguồn luôn **chỉ đọc**: adapter không có phương thức ghi.

---

## 13. Template Jinja

Năm template chính, mỗi cái nhận `spec` làm biến đầu vào. Mỗi pipeline render ra **4 + N file** (N = số nguồn): một file ingestion chứa một resource dlt cho **mỗi** phần tử `sources[]` (sinh bằng macro theo `kind`), N model staging, một model gold, một file schema test, một asset Dagster.

| Template | Xuất ra | Nội dung chính |
|---|---|---|
| `dlt_pipeline.py.j2` | `ingestion/{model_name}_pipeline.py` | Lặp `sources[]`, gọi macro theo `kind`; mỗi resource ghi vào `sources[].target.table`; một `dlt.pipeline(destination=…)` chạy tất cả |
| `sources/_postgres.j2` | (macro) | `sql_table` từ `dlt.sources.sql_database`, `primary_key`, `write_disposition`, `dlt.sources.incremental(cursor_field)` |
| `sources/_rest_api.j2` | (macro) | `rest_api_source` với `endpoint`, `records_path`, `pagination`; incremental theo `cursor_field` |
| `sources/_filesystem.j2` | (macro) | `filesystem(path_pattern)` nối với reader theo `format`; incremental theo `modification_date` |
| `dbt_staging.sql.j2` | `dbt_project/models/staging/stg_{model_name}__{id}.sql` (một file cho mỗi nguồn) | Đọc bảng bronze; `TRY_CAST` theo `columns[].type`; parse ngày theo `date_formats`; quy đổi tiền tệ theo `domains.yml: currency.rates`; loại trùng bằng `ROW_NUMBER()` theo `dedupe`; tách dòng lỗi và dòng thỏa `reject_if` sang `stg_{model_name}__{id}__rejected` kèm `_reject_reason` (dbt post-hook) |
| `dbt_model.sql.j2` | `dbt_project/models/gold/{model_name}.sql` | `{{ config(materialized=…, unique_key=…, partition_by=…) }}`, `FROM` model staging của `transform.from`, `JOIN` các model staging theo `transform.joins`, SELECT với `grain` + `metrics` + `filters`, khối `{% if is_incremental() %}` |
| `dbt_schema.yml.j2` | `dbt_project/models/gold/{model_name}.yml` | Khai báo cột và `tests` cho model gold; `unique` + `not_null` trên khóa `dedupe` của từng model staging |
| `dagster_asset.py.j2` | `infra/dagster/generated/{model_name}_asset.py` | `@dlt_assets` cho E-L, `@dbt_assets` cho T, `ScheduleDefinition` từ `schedule.cron` |

**Nguyên tắc:** template phải render đúng cú pháp 100%. Nếu render ra file lỗi cú pháp, đó là bug template, không phải lỗi agent → `INTERNAL_ERROR`.

Cách làm đúng: viết tay một pipeline hoàn chỉnh chạy được **trước**, rồi thay các chỗ khác biệt bằng biến Jinja. Không viết template từ đầu.

---

## 14. Agent — LangGraph

### 14.1 State

```python
class AgentState(TypedDict):
    run_id: str
    version: int                   # tăng khi con người yêu cầu sửa (mục 8.2)
    domain: str
    created_by: str                # từ phiên đăng nhập; "system" với run tự động
    trigger: str                   # user_request | manual_optimize | regression
    parent_run_id: str | None      # run tạo mới mà run này đang sửa
    regression: dict | None        # bằng chứng suy giảm, chỉ có khi trigger = regression
    user_request: str
    plan: dict | None
    schema_snapshot: dict          # từ resolve, theo sources[].id, KHÔNG do LLM sinh
    proposals: list[dict]
    spec: dict | None
    spec_history: list[dict]
    generated_files: dict[str, str]
    files_hash: str | None
    run_report: dict | None
    attempts: dict[str, int]
    llm_calls: int
    error_trace: list[dict]
    status: str
```

### 14.2 Bốn node

Graph có đúng 4 node: `planner → codegen → test-run → optimizer`. Các phase tất định (P0, P3, P5, P14–P17) là bước nối giữa các node, không phải node riêng.

| Node | Phase | Loại | Đầu vào | Đầu ra | Model |
|---|---|---|---|---|---|
| `planner` | P1 | LLM | yêu cầu (từ chat hoặc wizard) + danh sách đối tượng nguồn của domain (tên, loại nguồn, mô tả ngắn) | kế hoạch JSON: các nguồn cần đọc và cách join | rẻ |
| `codegen` | P4 | LLM | kế hoạch đã duyệt + schema thật + 20 dòng mẫu mỗi nguồn (để suy ra định dạng ngày, mã tiền tệ, cột cần ép kiểu) + few-shot từ vector DB | 1–2 spec JSON, gồm khối `staging` cho từng nguồn | mạnh |
| `codegen` (chế độ `sql_fix`) | vòng SQL | LLM | biểu thức SQL hiện tại + thông báo lỗi + schema thật | biểu thức đã sửa | mạnh |
| `test-run` | P6–P12 | Tất định | spec | file đã render + kết quả cổng và kiểm tra | — |
| `optimizer` | P13 | Tất định ở MVP | spec + số đo baseline | bản tối ưu hoặc lý do loại | — |

### 14.3 Quy tắc prompt

- Luôn kèm schema thật lấy từ `resolve`. **Không bao giờ để LLM tự nghĩ tên cột.**
- Yêu cầu trả về JSON thuần, không markdown fence. Nếu vẫn có fence thì strip trước khi parse.
- Few-shot lấy 3 spec đã duyệt gần nhất từ vector DB, chọn theo tương đồng với yêu cầu.
- Khi quay lại `propose` sau khi fail cổng, đưa **thông báo lỗi cụ thể** vào prompt, không đưa lời khuyên chung chung.

### 14.4 Vector DB

```python
class VectorStore(Protocol):
    def add(self, run_id: str, request: str, spec: dict, metadata: dict) -> None
    def search(self, request: str, k: int = 3, filters: dict | None = None) -> list[dict]
```

| Môi trường | Adapter | Ghi chú |
|---|---|---|
| MVP | `ChromaStore` | Chạy nhúng, lưu ra thư mục cục bộ; không cần thêm container |
| Sản phẩm thật | `QdrantStore` | Chạy như một service riêng; lọc theo `domain` và `sources[].kind` |

Chọn adapter bằng cấu hình (biến môi trường), không sửa code. `agent/` và `control_plane/` chỉ gọi `VectorStore`, không import trực tiếp thư viện Chroma hay Qdrant. Chỉ spec **đã deploy thành công** mới được `add` (FR-18).

### 14.5 Trợ lý chat — giao diện hội thoại của agent

Trợ lý chat là **cửa vào chính** để người dùng làm việc với agent: mô tả nhu cầu ELT từ nhiều nguồn bằng lời, trả lời câu hỏi làm rõ, xem kế hoạch, hỏi vì sao run bị chặn, và yêu cầu chỉnh sửa. Chat là lớp hội thoại **bọc quanh** graph ở mục 14.2, không phải node thứ năm và không có quyền nào ngoài những gì graph và API đã cho phép.

**Mỗi tin nhắn được phân loại** (model rẻ) vào một trong bốn ý định, rồi đi đúng một đường:

| Ý định | Ví dụ | Đi đâu | Tính vào |
|---|---|---|---|
| `new_request` | "Gộp đơn hàng Postgres với thanh toán từ API, tính doanh thu ngày" | Tạo run mới, vào P0 → P1 | `max_llm_calls` của run mới |
| `clarify_answer` | Trả lời câu hỏi làm rõ của P1 | Quay lại P1 của run đang mở | `max_llm_calls` của run |
| `revise` | "Đổi sang merge theo `order_id`, bỏ nguồn refunds" | Engineer: tạo version mới, vào P4 kèm nhận xét (vòng người, mục 11.3) | `max_llm_calls` của version mới |
| `ask` | "Vì sao run này bị chặn?", "Nguồn payments có bao nhiêu dòng?" | Trả lời, **không** đổi trạng thái run | `max_chat_calls` |

**Quy tắc cứng:**
- **Cổng người chỉ qua nút bấm.** Xác nhận kế hoạch (P2) và Duyệt / Từ chối (P15) hiện trong chat dưới dạng thẻ có nút; không bao giờ suy ra từ câu chữ như "ok", "được rồi".
- **Chỉ trả lời từ dữ liệu có thật.** Câu trả lời `ask` chỉ dùng `RunReport`, `error_trace`, `schema_snapshot` và 3 file chính sách; mỗi con số kèm tham chiếu tới trường nguồn (NFR-08). Không có dữ liệu thì nói không có, không đoán.
- **Không có quyền hành động ngoài API.** Chat không duyệt, không deploy, không sửa `policy/*.yml`, không chạy lệnh shell, không đọc dữ liệu nguồn ngoài 20 dòng mẫu của P3 (NFR-02).
- **Tôn trọng vai trò.** Reviewer gửi `new_request` hoặc `revise` thì bị từ chối kèm lý do (FR-01).
- Lịch sử hội thoại của một run được lưu cùng run, để truy vết ai yêu cầu gì (FR-20).

---

## 15. API

| Method | Path | Mô tả |
|---|---|---|
| `POST` | `/runs` | Tạo run mới từ yêu cầu, trả `run_id` |
| `GET` | `/runs/{id}/events` | SSE stream theo hợp đồng 8.3 |
| `GET` | `/runs/{id}` | RunReport đầy đủ |
| `POST` | `/runs/{id}/confirm-plan` | Cổng HITL 1 |
| `POST` | `/runs/{id}/approve` | Body: `{files_hash, version}`. Từ chối nếu hash hoặc version không khớp, hoặc người duyệt là `created_by` |
| `POST` | `/runs/{id}/deploy` | Chỉ Reviewer; chỉ khi `status = APPROVED` và `approval.files_hash == files_hash`. Mở pull request, trả link (P16) |
| `POST` | `/webhooks/github` | Nhận sự kiện CI xong, pull request merge hoặc đóng, deploy xong; cập nhật `status` (P16) |
| `GET` | `/runs/{id}/files` | Nội dung các file của một version (query `version`), dùng cho diff |
| `POST` | `/runs/{id}/reject` | Body: `{reason}` |
| `POST` | `/runs/{id}/request-changes` | Body: `{feedback}` → quay về `propose` |
| `POST` | `/pipelines/{model_name}/optimize` | UC-3: tạo run `manual_optimize` từ spec đang chạy, trả `run_id`. Chỉ Engineer |
| `POST` | `/runs/{id}/cancel` | Chỉ Engineer sở hữu run, chỉ khi run chưa tới `READY_FOR_REVIEW` (mục 11.1); sai điều kiện trả 409 |
| `GET` | `/policy` | Trả 3 file chính sách hiện hành |
| `GET` | `/runs` | Lịch sử, lọc theo status |
| `POST` | `/chat` | Body: `{message, domain, run_id?}`. Phân loại ý định (mục 14.5); trả `{intent, reply, run_id?}`. `new_request` tạo run mới và trả `run_id` |
| `GET` | `/runs/{id}/messages` | Lịch sử chat của run |

**Danh tính và phân quyền:**
- Mọi endpoint lấy người gọi và vai trò từ **phiên đăng nhập ở backend**. Body không có trường `user`/`reviewer`; nếu client gửi lên thì bỏ qua
- MVP dùng hai tài khoản local cố định (Engineer, Reviewer) với mật khẩu cấu hình qua biến môi trường. Backend xác thực username/password và tạo session HttpOnly; cơ chế này bị khóa ở production, nơi lớp xác thực cần được thay bằng SSO.
- Kiểm tra quyền (vai trò, `created_by`, trạng thái run) nằm ở **backend**. UI ẩn nút chỉ để tiện, không phải lớp bảo vệ
- Sai vai trò trả 403; đúng vai trò nhưng sai trạng thái run trả 409

---

## 16. Frontend

Bảng dưới nêu các màn mà hệ thống đích cần có.

| Màn | Nội dung |
|---|---|
| Đăng nhập | MVP: chọn một người dùng trong danh sách cố định (mục 15); hiện tên và vai trò đang đăng nhập ở góc trên |
| Trợ lý chat | Khung chat luôn mở bên phải; gửi yêu cầu, trả lời câu hỏi làm rõ, hiện thẻ kế hoạch có nút Xác nhận, hỏi về run, yêu cầu sửa (mục 14.5) |
| Gửi yêu cầu | Wizard thay thế cho chat: ô nhập, chọn domain, nút gạt vai trò Engineer/Reviewer |
| Theo dõi tiến trình | 6 nhóm phase (mục 9.0), bung ra thấy P0–P17, cập nhật realtime qua SSE, hiện ba bộ đếm: `attempts.spec`, `attempts.sql` và `llm_calls` so với trần trong `budgets.yml` |
| Review | Monaco DiffEditor (4 + N file) bên trái; bảng bằng chứng bên phải: gates, checks (tách theo từng nguồn), cost, timing; 3 nút Duyệt / Từ chối / Yêu cầu sửa |
| Staging | Với từng nguồn: số dòng bronze → trùng bị loại → rejected → staging; lý do bị tách theo nhóm; xem vài dòng rejected mẫu |
| Chính sách | Hiển thị 3 file YAML ở chế độ chỉ đọc |
| Lịch sử | Bảng run, ai duyệt, lúc nào, chi phí bao nhiêu |

Màn Review là màn quan trọng nhất. Bảng bằng chứng phải hiện con số cụ thể, ví dụ "Nguồn 12.480 dòng, bronze 12.480 dòng, khớp", "Chạy lần 2 không phát sinh dòng mới", "4,2 giây → 0,9 giây", "0,012 USD". Những con số này làm sản phẩm trông chín hơn bất kỳ hiệu ứng giao diện nào.

Nguyên tắc hiển thị bắt buộc:
- Bảng bằng chứng **luôn** hiện chi phí bằng tiền. Con số ước tính (`estimated = True`, mục 12) gắn nhãn "ước tính", không để ô trống.
- Mọi con số hiển thị truy được về một phép đo trong `RunReport` (NFR-08).
- Màu trạng thái luôn kèm chữ; mọi thao tác dùng được bằng bàn phím (NFR-10).

---

## 17. Yêu cầu dữ liệu và môi trường

### 17.1 Môi trường

| Thành phần | Vai trò | Ràng buộc |
|---|---|---|
| Hệ thống nguồn | Nơi agent đọc dữ liệu ra | Chỉ đọc; không có credential ghi |
| Sandbox | Nơi agent chạy thử | Mới cho mỗi run, xóa sau khi xong |
| Repo pipeline (GitHub) | Nơi P16 mở pull request | Agent chỉ có quyền mở pull request, không có quyền merge |
| CI/CD | Chạy lại test, deploy sau khi merge | Secret chỉ nằm ở CI/CD, không nằm trong môi trường agent |
| Prod (Dagster production) | Nơi pipeline chạy thật và Day 2 theo dõi | Chỉ CI/CD ghi được; credential không tồn tại trong môi trường agent |

### 17.2 Yêu cầu với dữ liệu kiểm thử

Dữ liệu dùng để kiểm thử hệ thống phải chứa lỗi **có chủ đích**, và mỗi lỗi phải ánh xạ tới đúng chỗ **xử lý** nó (staging) và đúng phép kiểm tra **chứng minh** nó đã được xử lý. Dữ liệu sạch làm tầng staging và tầng thẩm định không chứng minh được gì.

Lỗi phải được gieo ở **cả ba loại nguồn**, để mọi phép kiểm tra được chứng minh trên nguồn không phải SQL.

| Loại lỗi gieo vào | Gieo ở | Staging xử lý bằng | Phép chứng minh | Phase |
|---|---|---|---|---|
| Khóa trùng giữa hai kênh | Postgres `sales_pg.orders_events` (load `append`) | `dedupe` | `staging_reconcile` (đếm trùng), dbt test `unique` | P12 |
| Bản ghi lặp ở hai trang API liền nhau | REST `sales_api.payments` (load `merge`) | — (dlt `merge` theo `payment_id` đã gộp ở bronze) | `row_reconcile` (đếm khóa phân biệt), `idempotent` | P10 |
| Khóa chính chọn sai gây nhân đôi | REST `mkt_api.leads` | — (lỗi của spec, không phải của dữ liệu) | `idempotent` | P10 |
| Giá trị âm và NULL ở cột số | Postgres `sales_pg.orders`, file `landing_sales/refunds` | `reject_if` | `staging_reconcile`, dbt test `not_negative`, `not_null` | P12 |
| Định dạng ngày lẫn lộn | File `landing_sales/refunds` (`dd/mm/yyyy` lẫn ISO), REST `paid_at` | `date_formats` | `staging_reconcile`; `metric_reconcile` nếu parse sai | P12 |
| Ngày không đúng định dạng nào | File `landing_sales/refunds` (vài dòng `31-02-2026`) | Tách sang rejected, lý do `date_format` | `staging_reconcile` | P12 |
| Lẫn tiền tệ USD và VND | Postgres `sales_pg.orders`, REST `sales_api.payments` | `currency` | `metric_reconcile` | P12 |
| Mã tiền tệ lạ | REST `sales_api.payments` (vài dòng `EUR`) | Tách sang rejected, lý do `unknown_currency` | `staging_reconcile` | P12 |
| Khóa join lệch kiểu giữa hai nguồn | `order_id` là số ở `sales_pg.orders`, là chuỗi ở `sales_api.payments` | Ép kiểu `order_id` về `bigint` | `schema_real` (khóa join sau staging), `metric_reconcile` | P7, P12 |
| Khóa join không ép kiểu được | REST `sales_api.payments` (vài `orderId` dạng `"ORD-12"`) | Tách sang rejected, lý do `cast` | `staging_reconcile` | P12 |
| Tên cột khác nhau giữa các nguồn | `amount` ở Postgres, `total` ở REST | Đổi tên qua `columns[].as` | `schema_real`, `metric_reconcile` | P7, P12 |
| Tỷ lệ dòng rác quá cao | File `landing_sales/refunds_bad` (15% dòng lỗi) | Tách sang rejected | `staging_reconcile` **fail** vì vượt `max_reject_pct` | P12 |
| Thiếu dữ liệu một số ngày | File `landing_sales/refunds` (thiếu file của 2 ngày) | — | Luật `freshness_breach` và kiểm tra nguồn trống (mục 9.6.3–9.6.4) | Day 2 |
| Bản ghi mồ côi khi join | `sales_api.payments` có thanh toán trỏ tới `order_id` không tồn tại | — (join `left` giữ nguyên, không bịa dữ liệu) | `metric_reconcile` | P12 |
| Thực thể cùng tên ở nhiều domain | `customer`: Postgres (sales) và REST (marketing) | — | `entity_scan` | P3 |
| Bảng đủ lớn để vượt trần byte | Postgres `sales_pg.orders_history` | — | `dry_run` | P8 |

Hai dòng cuối là dữ liệu tồn tại **chỉ để kiểm chứng cổng ngữ nghĩa và cổng ngân sách**. Thiếu chúng thì hai cổng quan trọng nhất không có ca kiểm thử.

Seed nằm ở `infra/seeds/` (Postgres), `infra/mock_api/` (REST) và `infra/landing/` (file).

---

## 18. Đo lường

### 18.1 Nguyên tắc

Tính đúng của pipeline đo bằng **so kết quả từng dòng** với golden output, không so chuỗi SQL — hai câu SQL khác nhau vẫn có thể cùng kết quả.

Tính đúng của hệ thống điều khiển đo bằng **tỷ lệ chặn đúng** trên tập ca cố tình vi phạm chính sách. Đây là chỉ số phản ánh đúng thứ sản phẩm hứa hẹn, nên tập eval bắt buộc có ca vi phạm cho từng loại cổng.

### 18.2 Cấu trúc tập eval

`eval/cases/` chứa **15 ca** dạng `{request, expected_outcome, golden_result?}`, chia ba nhóm:

| Nhóm | Số ca | Mục đích | Phân bổ |
|---|---|---|---|
| **Hợp lệ** | 6 | Có `golden_result` viết tay, dùng đo tính đúng | 1 ca mỗi loại nguồn đơn (`postgres`, `rest_api`, `filesystem`) + 3 ca đa nguồn (Postgres + REST, Postgres + file, cả ba). Cả 3 ca đa nguồn đều đọc dữ liệu bẩn, để chứng minh staging chuẩn hóa đúng trước khi join |
| **Vi phạm** | 6 | Mong đợi một mã `BLOCKED_*` cụ thể, dùng đo tỷ lệ chặn đúng | 2 ca mỗi cổng: `semantic`, `budget`, `ast_guard` |
| **Mơ hồ** | 3 | Mong đợi agent hỏi lại ở P1 thay vì đoán | — |

### 18.3 Nhóm chỉ số

| Nhóm | Chỉ số |
|---|---|
| Tính đúng | Tỷ lệ chạy được lần đầu; sau tự sửa; tỷ lệ khớp golden output |
| Kiểm soát | Tỷ lệ chặn đúng; số lần tự sửa trung bình |
| Chi phí | Sai số giữa ước tính `dry_run` và chi phí thật |
| Hiệu năng | Thời gian chạy trước và sau khi optimizer can thiệp |
| Baseline | Thời gian từ yêu cầu tới pipeline chạy được, so với người viết tay |

Kết quả luôn phát biểu kèm mẫu số, ví dụ "chặn đúng 6/6 ca vi phạm", "khớp golden output 5/6 ca hợp lệ", và tách theo loại nguồn khi đo tính đúng. Không làm tròn thành phần trăm khi mẫu số nhỏ.

---

## 19. Bất biến hệ thống

Viết thành test trong `tests/test_invariants.py`:

1. Sandbox là file DuckDB / dataset BigQuery **mới cho mỗi run**, xoá sau khi xong
2. Connection string tới prod **không tồn tại** trong môi trường agent
3. Mọi file deploy truy vết được về một `run_id` và một chữ ký phê duyệt
4. Không node LLM nào thực thi lệnh shell
5. Mọi phiên bản spec đều được lưu trong `spec_history`
6. `approval.hash == files_hash` là điều kiện bắt buộc để mở pull request ở `deploy`
7. `llm_calls <= budgets.per_run.max_llm_calls` với mọi run. Không hardcode con số ở bất kỳ đâu
8. Mọi thay đổi lên production đi qua pull request do người có quyền merge. Agent không có quyền merge

---

