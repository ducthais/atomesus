# ADR-0001: Hai chế độ sử dụng và nền production nhiều người dùng

| | |
|---|---|
| Trạng thái | **Đề xuất**, chờ nhóm duyệt |
| Ngày | 2026-10-13 |
| Người đề xuất | ducthais |
| Code tham chiếu | Viết khi `main` = production = `859a790`; mục 9 cập nhật ở `dc2c6a0` |
| Sửa SPEC | §0.1, §2, §3, §4 (FR-01, FR-17, FR-18, FR-19), §8.4, §9.5, §12, §14.4, §15, §16, §17.1, §19 (bất biến 2 và 8) |

## 1. Bối cảnh

SPEC hiện viết cho **một đội data trong một tổ chức**: nguồn do steward khai báo trong git, có hai vai Engineer và
Reviewer, deploy bằng pull request rồi người có quyền merge bấm merge trên GitHub. Mục tiêu mới là production phục vụ
**hai nhóm người dùng trên cùng một hệ thống**:

- **Người dùng thường:** có dữ liệu (file, Google Sheets, một database) và muốn có pipeline chạy định kỳ ra bảng
  sạch. Họ không biết GitHub, Dagster hay dbt, và thường làm việc một mình.
- **Đội data:** cần quy trình review hai người, GitOps, kho dữ liệu và có thể cả Dagster của riêng họ.

### 1.1 Những chỗ chặn người dùng thường (code hiện tại)

| # | Chặn | Bằng chứng |
|---|---|---|
| 1 | Không đưa được dữ liệu của mình vào | Nguồn cố định trong `policy/domains.yml`, steward sửa qua PR (SPEC §2). `connection_ref` ánh xạ sang URL trong cấu hình backend (`src/codegen/sandbox.py:60`) |
| 2 | Làm một mình thì không tự duyệt được | Engineer không được duyệt run của mình (SPEC §2, FR-01) |
| 3 | Phải merge trên GitHub | Bất biến 8; `src/codegen/github.py` không có thao tác merge; run giữ `APPROVED` tới khi có người merge |
| 4 | Không lấy được kết quả | Bảng đầy đủ nằm trong `data/warehouse/warehouse.duckdb` trên VM; app chỉ hiện tối đa 20 dòng mẫu; Dagster UI bị khóa bằng basic auth (`deploy/Caddyfile:22`) |

### 1.2 Những chỗ chưa đạt mức production (kể cả cho đội data)

| # | Hiện trạng | Hậu quả khi có nhiều người dùng |
|---|---|---|
| 5 | Tài khoản cứng `minh` / `an` / `admin` (`src/pipeline/auth.py:13`). Tài khoản local bị chặn khi `APP_ENV=production` nên VM đang chạy `staging` | Chưa có người dùng thật và chưa đăng nhập được ở chế độ production |
| 6 | Mọi pipeline ghi vào **một file DuckDB chung** (`scripts/dagster_local.py:5`, `src/deployer/install.py:106`) | DuckDB chỉ cho một tiến trình ghi tại một thời điểm, nên hai pipeline chạy cùng lúc sẽ khóa nhau. Không có ranh giới dữ liệu giữa người dùng |
| 7 | Dagster lưu run, lịch và event log bằng SQLite trong `data/dagster_home`, và **mỗi pipeline là một code location** (`src/deployer/install.py:44`) | Mỗi code location là một tiến trình Python riêng, nên VM B2s 4 GB sẽ hết RAM sau vài chục pipeline. SQLite không chịu được nhiều run song song |
| 8 | Sổ bằng chứng là SQLite (`database_url` mặc định `sqlite:///./data/app.db`) | Không mở rộng ngang được, khó sao lưu nóng |
| 9 | Ngân sách là một trần chung cho cả hệ thống (`per_day.max_usd`, `src/agents/budget.py:110`) | Một người tiêu hết ngân sách LLM của mọi người |
| 10 | Vector store (FR-18, chưa làm) định lưu mọi Spec chung một chỗ | Few-shot lấy Spec của người này làm ví dụ cho người khác, tức là lộ tên bảng, cột và logic nghiệp vụ giữa các khách hàng |

## 2. Quyết định

### D1. Workspace là đơn vị cô lập

Thêm khái niệm **workspace**. Mọi tài nguyên đều thuộc đúng một workspace: thành viên, kết nối nguồn, chính sách,
pipeline, run, sổ bằng chứng, kho dữ liệu, ngân sách và mẫu few-shot. Mọi truy vấn của backend đều lọc theo
`workspace_id` lấy từ phiên đăng nhập, không bao giờ lấy từ client (giống nguyên tắc ở SPEC §15).

Một người dùng có thể thuộc nhiều workspace. Lúc đăng ký, mỗi người được tạo sẵn một workspace Cá nhân.

### D2. Hai hồ sơ quản trị (governance profile) trên cùng một bộ máy

Không làm hai sản phẩm. Cả hai dùng chung P0–P17, sáu cổng, sandbox và sổ bằng chứng. Chỉ khác ở **ai được làm gì**
và **đường lên production**:

| | **Cá nhân** (`personal`) | **Đội** (`team`) |
|---|---|---|
| Thành viên | 1 chủ sở hữu, có thể mời người xem | Owner, Engineer, Reviewer, Viewer |
| Nguồn | Khai báo trong app (D4) | Khai báo trong app, hoặc `domains.yml` qua git (GitOps) |
| Thực thể và định nghĩa nghiệp vụ | Tùy chọn. Hệ thống gợi ý từ schema, người dùng xác nhận | Steward sở hữu, bắt buộc như SPEC §8.4 |
| Duyệt (P15) | Chủ sở hữu tự xác nhận (D5) | Engineer ≠ Reviewer, bắt buộc |
| Lên production (P16) | Bản phát hành ký số, không có GitHub (D6) | Bản phát hành ký số, thêm tùy chọn PR vào repo của đội |
| Dagster UI | Không thấy. Mọi thao tác nằm trong app (D7) | Không thấy UI chung; muốn có UI thì dùng data plane riêng (D9) |
| Kho dữ liệu | Kho do nền tảng quản lý (D8) | Kho của nền tảng, hoặc kho của đội (BigQuery, Postgres…) |

Workspace Cá nhân nâng lên Đội được (thêm thành viên, bật review hai người). Chiều ngược lại thì không, để không ai
gỡ được quy trình review hai người của một đội.

### D3. Người dùng thật và đăng nhập

- Đăng nhập bằng OIDC (Google, Microsoft Entra ID) và email + mật khẩu (có xác minh email, đặt lại mật khẩu, giới
  hạn số lần thử). Bỏ bảng `USERS` cứng.
- Vai theo workspace: **Owner** (quản lý thành viên, kết nối, ngân sách), **Engineer**, **Reviewer**, **Viewer**
  (xem kết quả, tải dữ liệu). Trong workspace Cá nhân, Owner có quyền của cả Engineer và Reviewer.
- Session vẫn là cookie HttpOnly, Secure. Đổi vai hay rời workspace thì session cũ mất quyền ngay.
- Token API cá nhân, có phạm vi và hạn dùng, cho đội data muốn gọi API từ CI của họ.

### D4. Nguồn do người dùng khai báo

Thêm **sổ kết nối** (connection registry) theo workspace, thay cho việc ánh xạ `connection_ref` trong cấu hình
backend:

| Loại | Cách khai báo | Adapter |
|---|---|---|
| File tải lên (CSV, XLSX, JSON, Parquet) | Kéo thả trên web, lưu vào Blob Storage theo workspace | `filesystem` (đã có) |
| Google Sheets | OAuth, chỉ xin quyền đọc | `SourceAdapter` mới |
| Postgres, MySQL, SQL Server | Chuỗi kết nối, khuyến nghị dùng user chỉ đọc | `postgres` (đã có), thêm adapter mới |
| REST API | URL gốc + OpenAPI + khóa API | `rest_api` (đã có) |
| Thư mục định kỳ (SFTP, Blob container) | Đường dẫn + thông tin đăng nhập | `filesystem` |

Ràng buộc:

- Secret của kết nối được mã hóa (envelope encryption, khóa chủ nằm trong Azure Key Vault), chỉ giải mã trong tiến
  trình chạy sandbox hoặc chạy production của **đúng workspace đó**. Agent, LLM và trình duyệt không bao giờ thấy
  secret (giữ tinh thần bất biến 2).
- **Chống SSRF:** kết nối do người dùng nhập không được trỏ vào mạng nội bộ của nền tảng (dải IP private,
  link-local, metadata endpoint `169.254.169.254`, tên service nội bộ của compose). Kiểm cả lúc khai báo lẫn lúc chạy,
  vì DNS có thể đổi.
- File tải lên có giới hạn dung lượng theo gói, chỉ nhận các định dạng trong danh sách, và kiểm định dạng thật chứ
  không tin đuôi file.
- `domains.yml` vẫn dùng được cho workspace Đội theo kiểu GitOps: đồng bộ một chiều từ repo vào sổ kết nối và chính
  sách của workspace đó. App hiển thị ở chế độ chỉ đọc như FR-19.

### D5. Duyệt trong workspace Cá nhân

Chủ sở hữu tự xác nhận ở P15. Để bù cho việc không có người thứ hai, P15 trong workspace Cá nhân có các chốt sau:

- **Không có nút bỏ qua cổng.** Chỉ duyệt được khi cả sáu cổng và năm phép kiểm của `post_run` đều pass. Hiện nay
  điều này cũng đúng, nên chỉ cần giữ nguyên.
- Màn duyệt viết bằng ngôn ngữ thường: "Pipeline đọc 3 bảng, ra 1 bảng 12.480 dòng, chạy mỗi ngày lúc 6:00, tốn ước
  tính 0,01 USD/ngày", kèm vài dòng kết quả mẫu. Diff code chỉ hiện khi bấm "Xem chi tiết kỹ thuật".
- Chữ ký duyệt vẫn gắn với `files_hash` và `version` (FR-16, bất biến 6). Đổi file thì chữ ký mất hiệu lực.
- Run do hệ thống tạo (`regression`, Day 2) vẫn phải qua bước xác nhận của chủ sở hữu (FR-25 giữ nguyên).

### D6. Lên production bằng bản phát hành ký số, GitHub là tùy chọn

Thay "pull request + người merge" bằng **bản phát hành** (release) do control plane quản lý:

1. P16 đóng gói 4 + N file đã duyệt thành một bundle **bất biến**, có manifest (sha256 từng file, `files_hash`,
   `run_id`, `version`, chữ ký duyệt). Manifest dùng lại định dạng của `src/deployer/manifest.py`.
2. Bundle được ký bằng khóa của control plane (khóa nằm trong Key Vault, backend chỉ được gọi API ký) và lưu vào Blob
   Storage theo workspace.
3. Deployer của data plane (D7, D9) kéo bundle, **kiểm chữ ký và hash**, cài vào code location của workspace, chạy
   thử một lần (mức D2 của SPEC §9.5), không xanh thì giữ bản cũ và báo `DEPLOY_FAILED`.
4. Run chuyển `DEPLOYED` khi deployer báo xanh. Callback dùng lại `src/api/webhooks.py`.

Workspace Đội có thể bật thêm **GitOps**: sau bước 2, hệ thống mở PR vào repo của đội như hiện nay. Đội chọn một
trong hai cách:

- **Merge tay:** giữ đúng luồng hiện tại. Deployer chỉ cài bundle khi PR đã merge **và** chữ ký khớp.
- **Tự merge khi CI xanh:** workflow trong repo của đội tự merge PR do bot mở, sau khi `pipeline-ci` kiểm hash khớp
  chữ ký. Token của backend vẫn chỉ có quyền mở PR.

Rollback: mỗi pipeline giữ N bản phát hành gần nhất. Người dùng bấm "Quay lại bản trước" thì deployer cài lại bundle
cũ, không phải sinh lại code.

### D7. Dagster: chạy ở mức production và ẩn khỏi người dùng thường

Dagster vẫn là động cơ chạy theo lịch (asset model, lineage, `dagster-dbt`, `dagster-dlt` vẫn đáng giữ). Phải đổi
cách triển khai:

| Hạng mục | Hiện tại | Production |
|---|---|---|
| Lưu trữ của Dagster | SQLite trong `data/dagster_home` | Postgres (`dagster-postgres`: run, event log, schedule storage) |
| Tiến trình | webserver + daemon trên cùng container với code | webserver, daemon và code server tách riêng |
| Code location | Mỗi **pipeline** một location (một tiến trình Python) | Mỗi **workspace** một location (gRPC server), nạp mọi pipeline của workspace đó. Đội lớn có thể tách thêm |
| Chạy run | Trong tiến trình code server | Run launcher tạo **container riêng cho mỗi run** (`DockerRunLauncher`, sau này `K8sRunLauncher`), chỉ mang secret của workspace đó, có giới hạn CPU, RAM và thời gian |
| Hàng đợi | Không có | `QueuedRunCoordinator`, giới hạn số run đồng thời toàn hệ thống và **theo workspace** (tag `workspace`) |
| Nạp lại khi deploy | Khởi động lại Dagster khi `workspace.yaml` đổi | Chỉ reload code location của workspace vừa deploy, các workspace khác không bị gián đoạn |

**Người dùng không vào Dagster UI.** App làm mọi việc qua GraphQL của Dagster. Backend đã đọc GraphQL ở
`src/orchestration/dagster.py` và `src/monitor/runs.py`, nay thêm thao tác ghi, lọc theo workspace:

- Chạy ngay, tạm dừng hoặc bật lại lịch, đổi giờ chạy (đổi lịch là một version mới và phải duyệt lại, như hiện nay
  P5 ghi `spec_history`).
- Lịch sử chạy, thời gian, số dòng, lỗi viết lại bằng câu dễ hiểu, kèm log kỹ thuật khi bấm "Chi tiết".
- Chạy bù (backfill) cho một khoảng ngày.

Dagster OSS không phân quyền theo người dùng, nên Dagster UI chung **chỉ dành cho quản trị nền tảng**, đặt sau VPN
hoặc SSO của quản trị viên chứ không chỉ basic auth. Đội data muốn có Dagster UI thì dùng data plane riêng (D9).

### D8. Kho dữ liệu riêng cho từng workspace

- DuckDB **chỉ còn dùng cho sandbox** (mỗi run một file, xóa sau khi xong; bất biến 1 giữ nguyên).
- Kho production mặc định là **Postgres do nền tảng quản lý** (Azure Database for PostgreSQL Flexible Server). Mỗi
  workspace có một database riêng, gồm schema `bronze`, `staging` và `gold`, cùng ba role: role ghi (chỉ container
  chạy run của workspace đó có), role đọc cho app, và role đọc cho BI do người dùng tạo. Thêm `PostgresWarehouse`
  vào `WarehouseAdapter` (SPEC §12). dbt dùng `dbt-postgres`.
- Workspace Đội có thể dùng kho của mình (BigQuery qua adapter đã có, Postgres, sau này Snowflake). Nền tảng chỉ giữ
  credential ghi ở data plane, không ở control plane.
- Có hạn mức dung lượng theo gói và chính sách giữ dữ liệu (ví dụ bronze giữ 90 ngày).

### D9. Data plane: nền tảng quản lý, hoặc đội tự host

Tách hệ thống thành hai phần:

- **Control plane:** web, API, agent, sổ bằng chứng, sổ kết nối, ký bản phát hành. Không giữ credential ghi vào kho
  production.
- **Data plane:** deployer, Dagster, container chạy run, kho dữ liệu.

Mặc định mọi workspace dùng data plane do nền tảng quản lý. Workspace Đội có thể chạy **data plane riêng** trong hạ
tầng của họ: một container "agent" (deployer hiện tại mở rộng) chủ động gọi ra control plane để lấy bundle đã ký, cài
vào Dagster của đội, rồi báo trạng thái về. Cách này giống chế độ hybrid của Dagster+ và Airbyte. Dữ liệu và secret
của đội không bao giờ rời hạ tầng của họ, còn control plane không cần mở cổng vào mạng của đội.

### D10. Lấy kết quả ra

- Xem bảng gold trong app: phân trang, lọc, sắp xếp. Đọc bằng role chỉ đọc (D8), có giới hạn số dòng và thời gian
  mỗi truy vấn.
- Tải CSV hoặc XLSX. Bảng lớn thì xuất nền và gửi link tải có hạn dùng.
- Kết nối BI (Power BI, Looker Studio, Metabase): app cấp role chỉ đọc gắn với schema `gold` của workspace, hiện chuỗi
  kết nối, có thể thu hồi.
- Tùy chọn đẩy kết quả ra Google Sheets theo lịch (reverse ETL đơn giản). Phần này làm sau cùng.

### D11. Hạn mức và chi phí theo workspace

`policy/budgets.yml` thành **mặc định của nền tảng**. Mỗi workspace có hạn mức riêng theo gói, Owner hạ xuống được
nhưng không vượt trần của gói:

- Tiền LLM theo ngày và theo tháng (cổng `budget` cộng dồn theo `workspace_id` thay vì toàn hệ thống).
- Số pipeline đang chạy, chu kỳ lịch nhỏ nhất (ví dụ gói miễn phí: tối đa mỗi giờ một lần), số run đồng thời.
- Dung lượng file tải lên và dung lượng kho.
- Mẫu few-shot (FR-18) lưu theo workspace và chỉ tìm trong workspace đó. Mẫu dùng chung toàn nền tảng chỉ lấy từ bộ
  mẫu do nhóm phát triển soạn, không bao giờ lấy từ dữ liệu khách hàng.

### D12. Hạ tầng production đích

| Thành phần | Đích | Ghi chú |
|---|---|---|
| Web (Next.js), API (FastAPI) | Azure Container Apps, nhiều bản sao | API stateless; SSE cần sticky session hoặc chuyển sang pub/sub |
| CSDL control plane | Azure Database for PostgreSQL | Thay SQLite. Dagster storage dùng chung server, khác database |
| Kho workspace | Postgres Flexible Server, một database mỗi workspace | Workspace lớn thì tách server |
| File và bundle | Azure Blob Storage, container theo workspace | Đã có `pipeline-sources` (PR #11) |
| Secret | Azure Key Vault + managed identity | Bỏ secret trong `.env` trên VM |
| Vector store | `pgvector` trên CSDL control plane, lọc bắt buộc theo `workspace_id` | Một service ít hơn so với Chroma hoặc Qdrant; vẫn đi qua `VectorStore` (SPEC §14.4) |
| Dagster | VM riêng hoặc AKS: webserver, daemon, code server mỗi workspace, run launcher | Bắt đầu bằng Docker run launcher trên VM; lên AKS khi số workspace tăng |
| Giám sát | Azure Monitor / Application Insights, cảnh báo về email hoặc Slack | Theo dõi cả nền tảng lẫn Day 2 của từng workspace |
| Sao lưu | Backup tự động của Postgres; Blob có soft delete | Có kế hoạch khôi phục đã chạy thử |

## 3. Bất biến (SPEC §19) sau khi đổi

| # | Bất biến hiện tại | Sau ADR |
|---|---|---|
| 1 | Sandbox mới cho mỗi run, xóa sau khi xong | Giữ nguyên |
| 2 | Connection string tới prod không tồn tại trong môi trường agent | Giữ, mở rộng: credential của kho production và kết nối nguồn chỉ được giải mã trong container chạy run của đúng workspace; control plane không giữ credential ghi kho production |
| 3 | Mọi file deploy truy về một `run_id` và một chữ ký | Giữ; chữ ký nằm trong manifest của bundle |
| 4 | Không node LLM nào thực thi shell | Giữ nguyên |
| 5 | Mọi phiên bản spec lưu trong `spec_history` | Giữ nguyên |
| 6 | `approval.hash == files_hash` để mở PR | Đổi thành: để **tạo bản phát hành** (và mở PR nếu bật GitOps) |
| 7 | `llm_calls <= max_llm_calls` | Giữ; thêm trần tiền LLM theo workspace |
| 8 | Mọi thay đổi lên production qua PR do người có quyền merge; agent không có quyền merge | Đổi thành: mọi thay đổi lên production là một **bản phát hành bất biến, ký số, có chữ ký duyệt của người có quyền theo hồ sơ của workspace**. Agent và API tạo run không có quyền ký hay cài lên production; deployer từ chối bundle sai chữ ký hoặc sai hash |
| 9 (mới) | | Mọi truy vấn dữ liệu, file, kết nối, run và mẫu few-shot đều lọc theo `workspace_id` lấy từ phiên đăng nhập; có test truy cập chéo workspace phải bị chặn |
| 10 (mới) | | Kết nối do người dùng khai báo không được trỏ vào mạng nội bộ của nền tảng |

Quy tắc "Engineer không tự duyệt run của mình" (SPEC §2, FR-01) thành quy tắc của **hồ sơ Đội**, không tắt được trong
workspace Đội.

## 4. Các phương án đã cân nhắc

| Phương án | Lý do không chọn |
|---|---|
| Giữ PR + merge tay cho mọi người | Người dùng thường không có GitHub; run kẹt ở `APPROVED` mãi |
| Tự merge PR cho mọi người, GitHub vẫn là đường bắt buộc | Người dùng thường vẫn phụ thuộc một repo GitHub họ không biết và không sở hữu. Nền tảng phải giữ một repo chứa code của mọi khách hàng |
| Làm hai sản phẩm riêng | Hai bộ máy thẩm định, hai lần sửa lỗi; mất điểm mạnh "cùng sáu cổng cho mọi pipeline" |
| Mỗi workspace một instance Dagster riêng | Tốn RAM và tiền khi có nhiều workspace nhỏ. Chỉ dùng cho data plane tự host (D9) |
| Giữ DuckDB làm kho production, mỗi workspace một file | Vẫn chỉ một tiến trình ghi mỗi file; BI và app khó đọc đồng thời khi pipeline đang chạy; khó sao lưu nóng |
| Thay Dagster bằng scheduler tự viết | Mất lineage, backfill, hàng đợi và retry mà Dagster đã có; tự viết lại tốn hơn nhiều so với cấu hình Dagster cho đúng |

## 5. Hệ quả

**Được:**

- Người dùng thường đi được hết luồng trong app: tải dữ liệu, mô tả, xác nhận, xem kết quả, tải về. Không cần
  GitHub, Dagster hay dbt.
- Đội data giữ được quy trình review hai người và GitOps, còn có thêm data plane riêng.
- Dữ liệu và chi phí cô lập theo workspace. Đây là điều kiện tối thiểu để cho người ngoài nhóm dùng.
- Pipeline chạy song song được, không còn khóa file DuckDB chung.

**Mất hoặc tốn thêm:**

- Khối lượng lớn: mô hình workspace, đăng nhập thật, sổ kết nối, adapter kho Postgres, đổi cách triển khai Dagster,
  ký bản phát hành.
- Chi phí hạ tầng cao hơn một VM B2s. Cần tính lại với tín dụng Azure for Students và chốt gói miễn phí giới hạn tới
  đâu.
- SPEC §0.1 ("deploy nghĩa là PR") và câu trả lời chuẩn bị cho hội đồng phải viết lại theo D6.
- Nhận dữ liệu của người dùng thì phải tuân thủ Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân: điều khoản sử
  dụng, nơi lưu dữ liệu, xóa dữ liệu khi người dùng yêu cầu, nhật ký truy cập.

## 6. Thứ tự triển khai

Mỗi bước là một hoặc nhiều task harness. Bước sau dựa trên bước trước.

| Bước | Nội dung | Quyết định |
|---|---|---|
| 1. Nền | Mô hình workspace + `workspace_id` khắp backend; người dùng thật, OIDC; CSDL control plane chuyển sang Postgres; test bất biến 9 | D1, D3, D12 |
| 2. Data plane | Dagster dùng Postgres storage, mỗi workspace một code location, run launcher, hàng đợi; kho Postgres mỗi workspace + `PostgresWarehouse` | D7, D8 |
| 3. Phát hành | Bundle ký số, deployer kiểm chữ ký, rollback; GitOps (PR) chuyển thành tùy chọn của workspace Đội | D6 |
| 4. Tự phục vụ | Sổ kết nối, file tải lên, Google Sheets, chống SSRF; hồ sơ Cá nhân và màn duyệt bằng ngôn ngữ thường | D2, D4, D5 |
| 5. Kết quả | Xem, tải, kết nối BI; điều khiển lịch chạy trong app | D7, D10 |
| 6. Hạn mức | Ngân sách và hạn mức theo workspace và gói; few-shot theo workspace (`pgvector`) | D11 |
| 7. Data plane riêng | Agent tự host cho workspace Đội | D9 |

Dữ liệu hiện có (`sales`, `marketing`, `pagila`) chuyển thành một workspace Đội tên `demo`. Pipeline đã deploy được
phát hành lại thành bundle ở bước 3.

### Ảnh hưởng tới việc đang chia trong `progress.md`

| Việc | Ảnh hưởng |
|---|---|
| #1 Deploy qua PR | Vẫn làm. Đây là luồng GitOps của workspace Đội, và kiểm được deployer trước khi đổi sang bundle |
| #4 + #10 Chroma | Đổi sang `pgvector` qua `VectorStore`, lọc theo `workspace_id` (D11). Không thêm service Chroma vào compose |
| #2 + #3 Optimizer | Không đổi. Phần đo phải chạy được trên `PostgresWarehouse` |
| #5 Tạo hàng loạt | Ngân sách cả mẻ tính theo workspace |
| #7 Test E2E | Phải phủ cả hai hồ sơ: Cá nhân (tải file → kết quả) và Đội (hai người, PR) |
| #11 Cloud đầy đủ | Thay bằng D12 |

## 7. Câu hỏi còn mở

1. ~~Ngân sách hạ tầng~~: đã chốt ở mục 8 (100 USD, demo 1 tháng).
2. Gói miễn phí giới hạn những gì: số pipeline, chu kỳ lịch nhỏ nhất, dung lượng?
3. Đăng nhập: chỉ Google và Microsoft, hay cần thêm email + mật khẩu ngay từ đầu?
4. Workspace Cá nhân có cho mời người khác cùng xem kết quả không, hay mời người là phải nâng lên Đội?
5. Có cần bản tiếng Anh của giao diện cho người dùng ngoài Việt Nam không?
6. Hội đồng đã nghe bản "deploy = PR" theo SPEC §0.1. Có cần giữ GitOps làm mặc định cho workspace Đội khi demo
   không?

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

Hệ quả cho các quyết định ở mục 2:

- **D12 hoãn.** Không dùng Container Apps, Postgres Flexible hay Key Vault. Postgres trong compose giữ vai CSDL
  control plane và kho dữ liệu. Khóa mã hóa đặt trong `.env` trên VM.
- **D7 làm phần rẻ.** Dagster chuyển sang lưu trạng thái bằng Postgres và giới hạn 1–2 run đồng thời (RAM 4 GB).
  Run launcher dùng container riêng là tùy chọn.
- **D9, D10 (BI), D11 (gói) để sau đợt demo.**
- **Ưu tiên tính năng người dùng thấy được:** kết nối Postgres của người dùng (mục 9), xem và tải kết quả, duyệt
  bằng ngôn ngữ thường.

## 9. Việc tiếp theo: người dùng kết nối Postgres của họ và chọn dữ liệu

### 9.1 Luồng người dùng

1. Vào **Nguồn dữ liệu** → **Thêm kết nối Postgres**. Nhập host, cổng, database, user, mật khẩu, chế độ SSL.
   Màn hình ghi rõ IP của máy chủ (`4.145.113.187`) để người dùng mở firewall, và khuyên tạo user chỉ đọc.
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
- **Chính sách hiệu lực = `policy/*.yml` + domain của người dùng.** Một hàm `effective_policy()` thay cho 8 chỗ gọi
  `load_policy()`. Domain của người dùng có tên `u_<slug>`, `connection_ref = conn:<id>`, có thêm danh sách bảng được
  phép đọc, và mang `objectives` mặc định (khối `quality` và `regression`, SPEC §8.4). File YAML không đổi.
- **`connection_ref` dạng `conn:<id>`** được phân giải ở mọi nơi đang dùng URL Postgres cố định:
  - `_connection_url` (`src/codegen/sandbox.py:60`): sandbox chạy dlt
  - `postgres.connection` (`src/pipeline/postgres.py:13`): khám phá schema, đối chiếu độc lập, đếm nguồn cho Day 2
  - `credentials_env` (`src/codegen/sandbox.py`): Dagster production. Container Dagster cần khóa giải mã
- **Quyền:** Engineer thêm, sửa, xóa kết nối của mình. Reviewer chỉ xem tên kết nối và danh sách bảng (để duyệt),
  không xem host hay user. Xóa kết nối mà còn pipeline đã deploy dùng nó thì trả 409.

### 9.3 Chia task harness

| Task | Nội dung | Scope chính | UI |
|---|---|---|---|
| `CONN-STORE` | Sổ kết nối, mã hóa, chống SSRF, API `POST/GET/DELETE /connections`, `POST /connections/test` | `src/pipeline/connections.py` (mới), `src/pipeline/store.py`, `src/pipeline/settings.py`, `src/main.py`, `requirements.txt`, test | Không |
| `CONN-BROWSE` | Liệt kê schema và bảng của một kết nối; lưu lựa chọn thành domain người dùng; `effective_policy()` thay 8 chỗ gọi `load_policy()` | `src/pipeline/connectors.py`, `src/policy/`, các file gọi `load_policy`, test | Không |
| `CONN-RUNTIME` | Phân giải `conn:<id>` ở sandbox, đối chiếu, Day 2 và Dagster production; khóa giải mã cho container Dagster | `src/codegen/sandbox.py`, `src/pipeline/postgres.py`, `deploy/compose.yml`, test | Không |
| `CONN-UI` | Trang **Nguồn dữ liệu**: form kết nối, kiểm tra, chọn bảng; domain người dùng hiện trong trang tạo run và chat | `web/app/connections/`, `web/lib/`, `web/app/runs/new/` | Có |

Thứ tự: `CONN-STORE` → `CONN-BROWSE` → `CONN-RUNTIME` và `CONN-UI` làm song song.

**Nghiệm thu cả cụm:** tạo một Postgres miễn phí bên ngoài (Supabase hoặc Neon), nạp vài bảng mẫu, rồi trên
https://c4-app-045.id.vn: thêm kết nối → chọn bảng → tạo run → `READY_FOR_REVIEW` → duyệt → deploy → Dagster chạy
theo lịch và đọc được nguồn đó. Thêm kết nối trỏ tới `localhost`, `10.x`, `postgres` hay `169.254.169.254` đều bị
chặn.

### 9.4 Kết quả (2026-10-13)

Đã làm xong qua harness và deploy production (`dc2c6a0`): `CONN-STORE`, `CONN-BROWSE`, `CONN-RUNTIME`,
`CONN-POLICY`, `CONN-ALLOWLIST`, `CONN-UI`, `CONN-EGRESS`. Khác thiết kế ở 9.3: tách thêm `CONN-ALLOWLIST` (agent và
API tạo run chỉ dùng bảng đã chọn) và `CONN-EGRESS` (IP mở firewall đọc lúc chạy, không gắn lúc build). Cùng ngày
`RUN-QUEUE` giới hạn số sandbox chạy đồng thời (`PIPELINE_MAX_PARALLEL_SANDBOX`, mặc định 1) cho VM 4 GB. Chưa chạy
thử trọn luồng với một Postgres thật bên ngoài trên production.

### 9.5 Chưa làm trong cụm này

- Workspace đầy đủ (D1). Kết nối và domain tạm thuộc về **người tạo**; làm workspace thì chuyển sang thuộc workspace.
- MySQL, SQL Server, Google Sheets, file tải lên (D4).
- Đường hầm SSH hoặc private link cho database không mở ra Internet.
