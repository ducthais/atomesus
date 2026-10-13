# ADR-0001: Một luồng Engineer → Reviewer cho mọi người dùng và nền production

| | |
|---|---|
| Trạng thái | **Đề xuất**, chờ nhóm duyệt |
| Ngày | 2026-10-13 (bản rút gọn: bỏ hai chế độ Cá nhân / Đội, giữ hai vai theo yêu cầu BTC) |
| Người đề xuất | ducthais |
| Code tham chiếu | Viết khi `main` = production = `859a790`; mục 9 cập nhật ở `dc2c6a0` |
| Sửa SPEC | §0.1, §2, §3, §4 (FR-01, FR-17, FR-18, FR-19), §8.4, §9.5, §12, §14.4, §15, §16, §17.1, §19 (bất biến 2 và 8) |

## 1. Bối cảnh

SPEC hiện viết cho **một đội data trong một tổ chức**: nguồn do steward khai báo trong git, có hai vai Engineer và
Reviewer, deploy bằng pull request rồi người có quyền merge bấm merge trên GitHub. Mục tiêu mới: production dùng được
cho **bất kỳ ai có dữ liệu**, từ người làm một mình tới một đội data, **trên cùng một luồng, không chia chế độ**.

### 1.0 Ràng buộc cứng: yêu cầu của BTC

> **Cơ bản:** web deploy, 2 vai trò (Engineer/Reviewer), NL → sinh dbt model + DAG → chạy thử sandbox → HITL review
> diff & duyệt.
> **Nâng cao:** multi-agent (agent optimizer đo & rewrite query chậm), eval đo tính đúng output pipeline + cải thiện
> thời gian/chi phí chạy so với baseline, tự sinh test cho pipeline.

Mọi quyết định bên dưới phải giữ nguyên các mục này. Cụ thể: **hai vai Engineer và Reviewer là bắt buộc**, và
**người duyệt phải đọc diff** trước khi duyệt. Phần rút gọn chỉ áp vào những thứ BTC không yêu cầu: workspace, hồ sơ
quản trị, merge trên GitHub.

| Yêu cầu BTC | Hiện trạng trên `main` |
|---|---|
| Web deploy | Có: https://c4-app-045.id.vn (VM Azure B2s) |
| 2 vai Engineer / Reviewer | Có: `src/pipeline/auth.py`, Engineer không tự duyệt (FR-01) |
| NL → dbt model + DAG | Có: P0–P12 sinh dlt + dbt staging/gold + Dagster asset (`templates/`) |
| Chạy thử sandbox | Có: dlt + dbt + DuckDB mỗi run một sandbox, có hàng đợi (`RUN-QUEUE`) |
| HITL review diff & duyệt | Có: trang Duyệt, chữ ký gắn `files_hash` |
| Agent optimizer đo & rewrite | Có: P13 + UC-3 (`src/agents/optimizer.py`), đo nhiều lần, chỉ nhận khi nhanh hơn ≥ 10% |
| Eval đúng output | Có: `eval/run_eval.py`, golden 6 ca + 15 ca `eval/cases` |
| Eval cải thiện thời gian/chi phí so với baseline | **Còn thiếu:** `eval/results/latest_optimize.md` 3/3 ca đúng hành vi nhưng cả 3 đều không cải thiện (bảng mẫu quá nhỏ). Cần một ca dữ liệu đủ lớn cho thấy rõ trước/sau |
| Tự sinh test | Có: `dbt_schema.yml.j2` sinh `unique`/`not_null` cho khóa và test của Spec cho bảng gold |

### 1.1 Những chỗ chặn người dùng (code hiện tại)

| # | Chặn | Bằng chứng |
|---|---|---|
| 1 | Không đưa được dữ liệu của mình vào | Nguồn cố định trong `policy/domains.yml`, steward sửa qua PR (SPEC §2). **Đã gỡ một phần** bằng kết nối Postgres (mục 9) |
| 2 | Làm một mình thì không tự duyệt được | Engineer không được duyệt run của mình (SPEC §2, FR-01) |
| 3 | Phải merge trên GitHub | Bất biến 8; `src/codegen/github.py` không có thao tác merge; run giữ `APPROVED` tới khi có người merge |
| 4 | Không lấy được kết quả | Bảng đầy đủ nằm trong `data/warehouse/warehouse.duckdb` trên VM; app chỉ hiện tối đa 20 dòng mẫu; Dagster UI bị khóa bằng basic auth (`deploy/Caddyfile:22`) |

### 1.2 Những chỗ chưa đạt mức production

| # | Hiện trạng | Hậu quả khi có nhiều người dùng |
|---|---|---|
| 5 | Tài khoản cứng `minh` / `an` / `admin` (`src/pipeline/auth.py:13`). Tài khoản local bị chặn khi `APP_ENV=production` nên VM đang chạy `staging` | Chưa có người dùng thật |
| 6 | Mọi pipeline ghi vào **một file DuckDB chung** (`scripts/dagster_local.py:5`, `src/deployer/install.py:106`) | Hai pipeline chạy cùng lúc sẽ khóa nhau; không có ranh giới dữ liệu giữa người dùng |
| 7 | Dagster lưu bằng SQLite, **mỗi pipeline là một code location** (`src/deployer/install.py:44`) | VM 4 GB hết RAM sau vài chục pipeline; SQLite không chịu nhiều run song song |
| 8 | Ngân sách là một trần chung (`per_day.max_usd`, `src/agents/budget.py:110`) | Một người tiêu hết ngân sách LLM của mọi người |
| 9 | Vector store (FR-18, chưa làm) định lưu mọi Spec chung một chỗ | Few-shot lấy Spec của người này làm ví dụ cho người khác, lộ tên bảng, cột, logic nghiệp vụ |

## 2. Quyết định

### D1. Một luồng cho mọi người, giữ hai vai Engineer và Reviewer

Mọi người dùng đi cùng một luồng: **kết nối dữ liệu → mô tả (Engineer) → hệ thống sinh và chạy thử → Reviewer đọc
diff và duyệt → deploy → xem và tải kết quả**. Không có workspace, không có hồ sơ quản trị riêng.

- Ba vai như hiện nay: **Engineer** (kết nối nguồn, tạo và sửa run), **Reviewer** (đọc diff, duyệt hoặc từ chối),
  **Admin** (vận hành nền tảng). Engineer không duyệt run của chính mình (FR-01 giữ nguyên).
- Người dùng mới đăng ký thành Engineer, rồi **mời Reviewer bằng link**. Người nhận link có tài khoản Reviewer, chỉ
  thấy run được gửi cho mình duyệt. Đây là cách người làm một mình có người thứ hai duyệt mà không cần dựng "đội".
- Cả hệ thống dùng chung P0–P17, sáu cổng, sandbox và sổ bằng chứng như hiện nay.

### D2. Tài nguyên thuộc về người tạo

Kết nối, domain, run, pipeline, kết quả và mẫu few-shot thuộc về **người tạo** (`owner_id`, đã làm cho kết nối ở mục
9). Mọi truy vấn của backend lọc theo người dùng lấy từ phiên đăng nhập, không bao giờ lấy từ client.

Reviewer thấy run vì được Engineer mời (D1), không cần thêm cơ chế chia sẻ. Không dựng mô hình workspace / tổ chức
cho tới khi thật sự cần.

### D3. Đăng nhập

- Đăng ký bằng email + mật khẩu (giới hạn số lần thử), thêm Google khi kịp. Bỏ bảng `USERS` cứng.
- Session vẫn là cookie HttpOnly, Secure.

### D4. Nguồn do người dùng khai báo

Sổ kết nối theo người dùng, thay cho việc ánh xạ `connection_ref` trong cấu hình backend. Đã có Postgres (mục 9).
Thêm dần: file tải lên (CSV, XLSX), Google Sheets, MySQL.

Ràng buộc giữ nguyên như đã làm: secret mã hóa, chỉ giải mã khi chạy sandbox hoặc production; agent, LLM và trình
duyệt không thấy secret; chống SSRF lúc lưu và lúc chạy; luôn kết nối chỉ đọc. File tải lên có giới hạn dung lượng
và kiểm định dạng thật.

### D5. Reviewer duyệt bằng diff (HITL), giữ nguyên như hiện nay

- **Không có nút bỏ qua cổng.** Chỉ duyệt được khi cả sáu cổng và năm phép kiểm của `post_run` đều pass.
- Màn duyệt giữ **diff dbt model + DAG + test sinh ra** làm phần chính (yêu cầu BTC). Thêm một đoạn tóm tắt bằng ngôn
  ngữ thường ở đầu: "Pipeline đọc 3 bảng, ra 1 bảng 12.480 dòng, chạy mỗi ngày lúc 6:00, tốn ước tính 0,01 USD/ngày",
  kèm vài dòng kết quả mẫu và số đo optimizer trước/sau.
- Chữ ký duyệt gắn với `files_hash` và `version` (FR-16, bất biến 6). Đổi file thì chữ ký mất hiệu lực.
- Run do hệ thống tạo (`regression`, Day 2) vẫn phải chờ Reviewer duyệt (FR-25 giữ nguyên).

### D6. Deploy thẳng, GitHub là tùy chọn

Bấm **Deploy** sau khi Reviewer duyệt thì:

1. P16 đóng gói 4 + N file đã duyệt thành một bundle có manifest (sha256 từng file, `files_hash`, `run_id`, `version`,
   chữ ký duyệt). Dùng lại định dạng của `src/deployer/manifest.py`.
2. Deployer **kiểm hash khớp chữ ký**, cài vào Dagster, chạy thử một lần (mức D2 của SPEC §9.5). Không xanh thì giữ
   bản cũ và báo `DEPLOY_FAILED`.
3. Run chuyển `DEPLOYED` khi deployer báo xanh.

Mở PR vào repo GitHub thành **tùy chọn của hệ thống** (bật khi có cấu hình repo), dùng để lưu vết code, không còn là
điều kiện để deploy. Rollback: giữ vài bundle gần nhất, bấm "Quay lại bản trước" thì cài lại bundle cũ.

### D7. Dagster ẩn khỏi người dùng

Dagster vẫn là động cơ chạy theo lịch. Người dùng **không vào Dagster UI**; app làm mọi việc qua GraphQL (đã đọc ở
`src/orchestration/dagster.py`, `src/monitor/runs.py`, nay thêm ghi): chạy ngay, tạm dừng / bật lịch, xem lịch sử
chạy và lỗi viết bằng câu dễ hiểu. Dagster UI chỉ dành cho quản trị.

Đổi cách triển khai cho đỡ tốn tài nguyên: lưu trạng thái bằng Postgres (`dagster-postgres`) thay SQLite, gộp pipeline
vào ít code location hơn (không phải mỗi pipeline một tiến trình), giới hạn số run đồng thời bằng
`QueuedRunCoordinator`.

### D8. Kho dữ liệu: Postgres, mỗi người dùng một schema

- DuckDB **chỉ còn dùng cho sandbox** (mỗi run một file, xóa sau khi xong; bất biến 1 giữ nguyên).
- Kho production là Postgres. Mỗi người dùng một schema riêng (`u_<id>`), thêm `PostgresWarehouse` vào
  `WarehouseAdapter` (SPEC §12), dbt dùng `dbt-postgres`.
- Người dùng muốn ghi ra kho của họ (BigQuery, Postgres riêng) thì làm sau.

### D9. Lấy kết quả ra

- Xem bảng kết quả trong app: phân trang, lọc, sắp xếp; giới hạn số dòng và thời gian mỗi truy vấn.
- Tải CSV hoặc XLSX.
- Sau: chuỗi kết nối chỉ đọc cho BI (Power BI, Looker Studio, Metabase), đẩy kết quả ra Google Sheets.

### D10. Hạn mức theo người dùng

`policy/budgets.yml` thành mặc định chung; cổng `budget` cộng dồn tiền LLM, số pipeline, số run đồng thời **theo người
dùng** thay vì toàn hệ thống. Mẫu few-shot (FR-18) lưu bằng `pgvector`, chỉ tìm trong dữ liệu của chính người đó, cộng
bộ mẫu chung do nhóm soạn.

### D11. Hạ tầng đích (sau đợt demo)

| Thành phần | Đích |
|---|---|
| Web, API | Azure Container Apps |
| CSDL backend, kho, Dagster storage, vector | Azure Database for PostgreSQL (khác database) |
| File, bundle | Azure Blob Storage |
| Secret | Azure Key Vault + managed identity |
| Giám sát | Azure Monitor / Application Insights |

## 3. Bất biến (SPEC §19) sau khi đổi

| # | Bất biến hiện tại | Sau ADR |
|---|---|---|
| 1 | Sandbox mới cho mỗi run, xóa sau khi xong | Giữ nguyên |
| 2 | Connection string tới prod không tồn tại trong môi trường agent | Giữ; thêm: secret kết nối của người dùng chỉ được giải mã khi chạy sandbox hoặc production |
| 3 | Mọi file deploy truy về một `run_id` và một chữ ký | Giữ; chữ ký nằm trong manifest của bundle |
| 4 | Không node LLM nào thực thi shell | Giữ nguyên |
| 5 | Mọi phiên bản spec lưu trong `spec_history` | Giữ nguyên |
| 6 | `approval.hash == files_hash` để mở PR | Đổi thành: để **deploy** (và mở PR nếu bật) |
| 7 | `llm_calls <= max_llm_calls` | Giữ; thêm trần tiền LLM theo người dùng |
| 8 | Mọi thay đổi lên production qua PR do người có quyền merge; agent không có quyền merge | Đổi thành: mọi thay đổi lên production là một bundle **có chữ ký duyệt của Reviewer (khác Engineer tạo run), hash khớp**. Agent không duyệt hay deploy; deployer từ chối bundle sai hash. Merge PR trên GitHub không còn là điều kiện |
| 9 (mới) | | Mọi truy vấn dữ liệu, kết nối, run và mẫu few-shot lọc theo người dùng từ phiên đăng nhập; có test truy cập chéo phải bị chặn |
| 10 (mới) | | Kết nối do người dùng khai báo không được trỏ vào mạng nội bộ của nền tảng |

Quy tắc "Engineer không tự duyệt run của mình" (SPEC §2, FR-01) **giữ nguyên** (yêu cầu BTC).

## 4. Các phương án đã cân nhắc

| Phương án | Lý do không chọn |
|---|---|
| Giữ PR + merge tay cho mọi người | Người dùng thường không có GitHub; run kẹt ở `APPROVED` mãi |
| Hai chế độ Cá nhân / Đội với workspace, vai và hồ sơ quản trị riêng (bản đầu của ADR này) | Quá nhiều khái niệm cho người dùng và quá nhiều việc cho 1 tháng demo. Một luồng + mời Reviewer bằng link đủ cho cả hai nhóm |
| Cho người làm một mình tự duyệt (gộp Engineer và Reviewer) | Trái yêu cầu BTC (2 vai, HITL review). Mời Reviewer bằng link giải quyết được người làm một mình |
| Mỗi người dùng một instance Dagster | Tốn RAM và tiền |
| Giữ DuckDB làm kho production | Một tiến trình ghi mỗi file; app khó đọc khi pipeline đang chạy |
| Thay Dagster bằng scheduler tự viết | Mất lineage, backfill, hàng đợi, retry |

## 5. Hệ quả

**Được:** người dùng đi hết luồng trong app, không cần GitHub, Dagster hay dbt. Ít khái niệm, ít việc phải làm. Dữ
liệu và chi phí tách theo người dùng.

**Mất hoặc tốn thêm:**

- Người làm một mình vẫn cần một người thứ hai (Reviewer) mới deploy được. Đây là đánh đổi có chủ đích để giữ HITL.
- SPEC §0.1 ("deploy nghĩa là PR") và câu trả lời cho hội đồng phải viết lại theo D6.
- Nhận dữ liệu của người dùng thì phải tuân thủ Nghị định 13/2023/NĐ-CP: điều khoản sử dụng, xóa dữ liệu khi người
  dùng yêu cầu, nhật ký truy cập.

## 6. Thứ tự triển khai

| Bước | Nội dung | Quyết định |
|---|---|---|
| 0 | **Eval optimizer có ca cải thiện thật** (dữ liệu đủ lớn, báo trước/sau thời gian và chi phí) | BTC nâng cao |
| 1 | Deploy thẳng sau khi Reviewer duyệt, không cần merge GitHub; tóm tắt bằng ngôn ngữ thường trên màn duyệt diff | D5, D6 |
| 2 | Xem và tải kết quả (CSV/XLSX) | D9 |
| 3 | Đăng ký người dùng thật, Engineer mời Reviewer bằng link, mọi tài nguyên lọc theo `owner_id`, test bất biến 9 | D1, D2, D3 |
| 4 | Dagster lưu bằng Postgres, gộp code location, hàng đợi; kho Postgres theo schema người dùng | D7, D8 |
| 5 | Nguồn thêm: file tải lên, Google Sheets | D4 |
| 6 | Hạn mức theo người dùng, few-shot `pgvector` | D10 |

Dữ liệu hiện có (`sales`, `marketing`, `pagila`) giữ làm dữ liệu mẫu cho tài khoản demo.

### Ảnh hưởng tới việc đang chia trong `progress.md`

| Việc | Ảnh hưởng |
|---|---|
| #1 Deploy qua PR | Thành tùy chọn (D6). Chỉ cần chạy thử một lần, không còn chặn |
| #4 + #10 Chroma | Đổi sang `pgvector` qua `VectorStore`, lọc theo người dùng (D10). Không thêm service Chroma |
| #2 + #3 Optimizer | Không đổi. Phần đo phải chạy được trên `PostgresWarehouse` |
| #5 Tạo hàng loạt | Ngân sách cả mẻ tính theo người dùng |
| #7 Test E2E | Phủ một luồng: kết nối → Engineer tạo run → Reviewer duyệt diff → deploy → tải kết quả |
| #11 Cloud đầy đủ | Thay bằng D11, sau đợt demo |

## 7. Câu hỏi còn mở

1. ~~Ngân sách hạ tầng~~: đã chốt ở mục 8 (100 USD, demo 1 tháng).
2. Giới hạn mỗi người dùng: số pipeline, chu kỳ lịch nhỏ nhất, dung lượng?
3. Hội đồng đã nghe bản "deploy = PR" theo SPEC §0.1. Khi demo có bật PR (tùy chọn) để giữ câu chuyện đó không?

## 8. Phạm vi cho đợt demo (chốt 2026-10-13)

Ràng buộc: tín dụng Azure for Students **100 USD**, cần chạy demo trong **khoảng 1 tháng**.

**Hạ tầng: giữ nguyên 1 VM B2s** (`deploy/compose.yml`), không dựng thêm dịch vụ trả phí:

| Khoản | Ước tính / tháng |
|---|---|
| VM B2s (2 vCPU, 4 GB) | ~35 USD |
| Đĩa OS + IP tĩnh | ~8–12 USD |
| Blob Storage (`pipeline-sources`), băng thông | < 1 USD |
| **Cộng** | **~45–50 USD**, còn dư để phòng sự cố |

Tiền gọi OpenAI tính vào tài khoản OpenAI, không trừ vào tín dụng Azure. Cần xem trên Azure Portal (Cost
Management) số tín dụng đã dùng từ 2026-10-09 trước khi chốt.

Hệ quả:

- **D11 hoãn.** Postgres trong compose giữ vai CSDL backend và kho dữ liệu. Khóa mã hóa đặt trong `.env` trên VM.
- **D7 làm phần rẻ.** Dagster lưu bằng Postgres, giới hạn 1–2 run đồng thời (RAM 4 GB).
- **Ưu tiên:** đủ mục BTC trước (eval optimizer có ca cải thiện thật), rồi tính năng người dùng thấy được: kết nối
  Postgres (mục 9, đã xong), deploy thẳng sau khi Reviewer duyệt, xem và tải kết quả.

## 9. Người dùng kết nối Postgres của họ và chọn dữ liệu

### 9.1 Luồng người dùng

1. Vào **Nguồn dữ liệu** → **Thêm kết nối Postgres**. Nhập host, cổng, database, user, mật khẩu, chế độ SSL.
   Màn hình ghi rõ IP của máy chủ để người dùng mở firewall, và khuyên tạo user chỉ đọc.
2. Bấm **Kiểm tra kết nối**. Hệ thống kết nối thử ở chế độ chỉ đọc, báo lỗi dễ hiểu: sai mật khẩu, không tới được
   host, thiếu SSL, user không có quyền `SELECT`.
3. Chọn schema, rồi tick các bảng. Mỗi bảng hiện số cột, khóa chính và số dòng ước tính.
4. Bấm **Dùng các bảng này**. Hệ thống tạo một **domain của người dùng**, chỉ chứa đúng các bảng đã chọn. Domain này
   hiện trong ô chọn domain của trang tạo run và của trợ lý chat.
5. Từ đây đi đúng luồng hiện có: mô tả yêu cầu → kế hoạch → sandbox → duyệt → deploy. Pipeline đã deploy đọc nguồn
   bằng kết nối đã lưu.

### 9.2 Thiết kế

- **Sổ kết nối** (bảng mới trong CSDL của backend): chủ sở hữu, tên, host, cổng, database, user, mật khẩu **đã mã
  hóa** (Fernet, khóa `PIPELINE_CONNECTION_KEY` trong `.env`), `sslmode`. Thiếu khóa thì backend từ chối khởi động ở
  `staging` và `production`. API không bao giờ trả mật khẩu về client.
- **Chống SSRF:** phân giải host, chặn loopback, dải private, link-local (kể cả `169.254.169.254`) và tên service nội
  bộ (`postgres`, `backend`, `dagster`…). Kiểm cả lúc lưu lẫn lúc kết nối. Biến
  `PIPELINE_CONNECTION_ALLOW_PRIVATE=1` chỉ có hiệu lực ở `development` và `test`, để test trên máy.
- **Luôn chỉ đọc:** mọi kết nối tới nguồn của người dùng đặt `default_transaction_read_only=on`, `statement_timeout`
  và `connect_timeout`, giống `src/pipeline/postgres.py:21`.
- **Chính sách hiệu lực = `policy/*.yml` + domain của người dùng** qua `effective_policy()`. Domain của người dùng có
  tên `u_<slug>`, `connection_ref = conn:<id>`, có danh sách bảng được phép đọc, và mang `objectives` mặc định (khối
  `quality` và `regression`, SPEC §8.4).
- **`connection_ref` dạng `conn:<id>`** được phân giải ở sandbox (`src/codegen/sandbox.py`), khám phá schema và đối
  chiếu (`src/pipeline/postgres.py`), và Dagster production.
- **Quyền (theo code hiện tại):** Engineer thêm, sửa, xóa kết nối của mình; Reviewer chỉ xem tên kết nối và danh sách
  bảng (giữ theo D1). Xóa kết nối mà còn pipeline đã deploy dùng nó thì trả 409.

### 9.3 Task harness

| Task | Nội dung |
|---|---|
| `CONN-STORE` | Sổ kết nối, mã hóa, chống SSRF, API `POST/GET/DELETE /connections`, `POST /connections/test` |
| `CONN-BROWSE` | Liệt kê schema và bảng; lưu lựa chọn thành domain người dùng; `effective_policy()` |
| `CONN-RUNTIME` | Phân giải `conn:<id>` ở sandbox, đối chiếu, Day 2 và Dagster production |
| `CONN-POLICY` | Domain người dùng dùng được ở mọi chỗ đọc chính sách |
| `CONN-ALLOWLIST` | Agent và API tạo run chỉ dùng bảng đã chọn |
| `CONN-UI` | Trang **Nguồn dữ liệu**: form kết nối, kiểm tra, chọn bảng |
| `CONN-EGRESS` | IP mở firewall đọc lúc chạy, không gắn lúc build |

**Nghiệm thu cả cụm:** tạo một Postgres miễn phí bên ngoài (Supabase hoặc Neon), nạp vài bảng mẫu, rồi trên
https://c4-app-045.id.vn: thêm kết nối → chọn bảng → tạo run → `READY_FOR_REVIEW` → duyệt → deploy → Dagster chạy
theo lịch và đọc được nguồn đó. Thêm kết nối trỏ tới `localhost`, `10.x`, `postgres` hay `169.254.169.254` đều bị
chặn.

### 9.4 Kết quả (2026-10-13)

Cả 7 task đã làm xong qua harness và deploy production (`dc2c6a0`). Cùng ngày `RUN-QUEUE` giới hạn số sandbox chạy
đồng thời (`PIPELINE_MAX_PARALLEL_SANDBOX`, mặc định 1) cho VM 4 GB. Chưa chạy thử trọn luồng với một Postgres thật
bên ngoài trên production.

### 9.5 Chưa làm trong cụm này

- MySQL, Google Sheets, file tải lên (D4).
- Đường hầm SSH cho database không mở ra Internet.
