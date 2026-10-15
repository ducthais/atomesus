# Hướng dẫn Agent — Công việc Người 2: Production Hardening, Deploy 1-Chạm và review_api

## 1. Mục tiêu và vai trò

Bạn là **Người 2** trong kế hoạch sửa lỗi hệ thống. Nhiệm vụ của bạn gồm hai chặng:

- **Sprint 1 — công việc tương đối độc lập:** giới hạn thời gian retry của LLM; xử lý vòng đời checkpoint để tránh rò rỉ RAM; thay regex guard bằng SQL AST guard dùng `sqlglot`; mở rộng che giấu secret trong output.
- **Sprint 2 — sau Sync Gate 1:** triển khai luồng Deploy 1-chạm theo `ADR-0001 §D6`; tích hợp nguồn `review_api` (gắn với Pagila) vào Planner/Chat/UI; kiểm chứng chức năng export CSV/XLSX hiện có tương thích với đường dẫn DuckDB mới; bổ sung regression/unit tests.

Mục tiêu là sửa đúng phạm vi, không phá vỡ hành vi lịch sử, không triển khai lại tính năng export đã hoàn thành, và bàn giao bằng chứng kiểm thử rõ ràng.

> **Nguyên tắc bắt buộc:** Trước khi sửa, đọc mã nguồn và test hiện có. Không giả định nội dung file chỉ dựa vào số dòng trong báo cáo vì số dòng có thể đã thay đổi. Giữ thay đổi nhỏ, có kiểm thử, và không tự ý mở rộng phạm vi.

---

## 2. Phạm vi công việc

### Trong phạm vi

1. `src/agents/llm.py`
   - Điều chỉnh `ChatOpenAI.max_retries` để tổng thời gian retry không vượt trần của một phase.
2. `src/agents/service.py`
   - Xử lý vòng đời checkpoint trong `MemorySaver` để tránh giữ dữ liệu run đã hoàn tất trong RAM.
3. `src/codegen/guard.py`
   - Xây dựng AST guard thực sự bằng `sqlglot`, thay thế cơ chế regex tạm thời.
4. `src/codegen/sandbox.py`
   - Mở rộng `_safe_output` để che các chuỗi nhạy cảm ngoài biến có hậu tố `__CREDENTIALS`.
5. Planner, Chat Planner và UI liên quan đến nguồn dữ liệu
   - Chặn nguồn `sales` đối với **pipeline mới**, cả khi tạo trực tiếp lẫn qua Chat.
   - Cho phép nguồn `review_api` theo định danh và cấu hình đã được nhóm thống nhất.
   - Ẩn `sales` khỏi danh sách chọn nguồn mới và các danh mục Catalog/Glossary liên quan, nhưng vẫn giữ khả năng xem run lịch sử.
6. Luồng Deploy
   - Backend chỉ cho Reviewer hoặc Admin thực hiện Deploy.
   - Đóng gói bundle đã ký duyệt và chuyển trực tiếp cho deployer để kiểm tra chữ ký/hash, cài đặt vào Dagster, chạy xác nhận và cập nhật trạng thái `DEPLOYED`.
   - GitHub Pull Request chỉ là kênh GitOps tùy chọn chạy nền nếu cấu hình GitHub có mặt; không được chặn thao tác Deploy.
7. Export Gold
   - Chỉ kiểm chứng endpoint và UI export CSV/XLSX hiện có khi dùng đường dẫn DuckDB mới. Không viết lại chức năng export nếu không phát hiện lỗi cụ thể.

### Ngoài phạm vi / không tự ý thực hiện

- Không xử lý phần Auth, rate-limit đăng nhập, SQLite WAL hoặc HMAC timestamp thuộc Người 1, trừ khi cần phối hợp giao diện/hợp đồng.
- Không tự triển khai cô lập biến môi trường Docker, tách file DuckDB hoặc reload Dagster; đây là phạm vi chính của Người 3. Chỉ phối hợp và kiểm thử tương thích.
- Không xóa hoặc sửa dữ liệu run lịch sử của `sales`.
- Không chặn `sales` chỉ ở frontend. API, Planner và Chat Planner cũng phải từ chối việc tạo mới.
- Không loại bỏ dữ liệu `sales` khỏi benchmark hoặc làm hỏng các benchmark hiện có.
- Không triển khai lại export CSV/XLSX đã hoàn thành ở commit `5fe741c`.
- Không tự suy đoán cách kết nối `review_api` với Pagila. Chờ nhóm chốt contract ở Sync Gate 1.
- Không commit secret, credential, file `.env`, dữ liệu thật hoặc bundle chứa thông tin nhạy cảm.

---

## 3. Điều kiện và hợp đồng cần xác nhận trước khi code

### 3.1. Kiểm tra repository

1. Xác nhận branch hiện tại và working tree:
   - `git status --short`
   - `git branch --show-current`
2. Làm việc trên nhánh hardening mà nhóm chỉ định (ví dụ `fix/production-hardening-2`) hoặc nhánh task được giao.
3. Đọc các file và test liên quan trước khi sửa.
4. Tìm cách dự án đang cấu hình dependency, logger, Planner, nguồn dữ liệu và deployer; ưu tiên mẫu kiến trúc hiện có.
5. Không ghi đè thay đổi chưa commit của thành viên khác.

### 3.2. Các điểm phải chốt ở Sync Gate 1

Trước Sprint 2, nhận xác nhận của nhóm về:

- **Contract DuckDB:** hàm/logic chuẩn lấy đường dẫn `data/warehouse/<model_name>.duckdb` và cách các thành phần sử dụng nó.
- **Contract `review_api`:** `review_api` dùng chung database Pagila hay là nguồn/API riêng đọc dữ liệu từ Pagila; định danh nguồn chuẩn, schema và cấu hình cần thiết.
- **Contract bundle/deployer:** cấu trúc bundle, manifest, report, cách ký và xác minh hash/chữ ký, response/status của deployer.
- **Contract Dagster:** cách xác nhận pipeline đã được nạp/chạy sau deploy, phối hợp với Người 3.
- Quyền deploy: vai trò nào được phép; báo cáo yêu cầu Reviewer hoặc Admin.

Nếu một contract chưa được chốt, không tự tạo ra định dạng riêng. Hoàn thiện phần độc lập và ghi rõ blocker.

---

## 4. Sprint 1 — LLM timeout, checkpoint, SQL AST guard và che secret

### Task 1 — Khống chế tổng thời gian chờ LLM (#8)

**File chính:** `src/agents/llm.py`

Báo cáo nêu cấu hình hiện tại có `timeout=120` và để `max_retries=2` mặc định. Với ba lượt thử, tổng thời gian chờ có thể vượt giới hạn phase 300 giây.

#### Việc cần làm

1. Đọc cách tạo `ChatOpenAI`, các cấu hình timeout/retry và cách phase giới hạn thời gian.
2. Đặt `max_retries` tường minh, sao cho:
   \[
   timeout \times (max\_retries + 1) < 300 \text{ giây}
   \]
3. Nếu timeout thực tế có thể cộng thêm overhead hoặc thời gian backoff, hãy tính đến phần đó; không chỉ dựa vào phép nhân lý tưởng.
4. Giữ timeout/retry có thể cấu hình theo quy ước hiện có của dự án. Không tạo nhiều nguồn cấu hình mâu thuẫn.
5. Bảo đảm lỗi timeout/retry được truyền hoặc xử lý theo cơ chế hiện tại, không biến lỗi thành kết quả thành công giả.
6. Bổ sung/cập nhật unit test để chứng minh giá trị cấu hình và hành vi lỗi.

#### Tiêu chí hoàn thành

- `max_retries` không còn phụ thuộc mặc định ngoài ý muốn.
- Tổng ngân sách retry nhỏ hơn 300 giây theo công thức và cấu hình thực tế.
- Test xác nhận cấu hình; test không gọi LLM thật nếu có thể dùng mock.
- Không làm thay đổi không cần thiết prompt, model hoặc logic nghiệp vụ của agent.

### Task 2 — Ngăn rò rỉ RAM từ `MemorySaver` (N2)

**File chính:** `src/agents/service.py` (báo cáo chỉ vị trí quanh dòng 56 và 176; hãy tìm lại theo symbol).

Báo cáo ghi nhận biến toàn cục `DEFAULT_CHECKPOINTER = MemorySaver()` có thể giữ toàn bộ checkpoint của nhiều run (Spec, file sinh ra, evidence) đến khi backend restart.

#### Việc cần làm

1. Lần theo nơi khai báo `DEFAULT_CHECKPOINTER`, nơi tạo graph/agent và nơi gọi graph cho một run.
2. Xác định `thread_id`/key checkpoint được tạo ở đâu, phạm vi sống của thread, và các trạng thái kết thúc run (thành công, lỗi, timeout, hủy).
3. Chọn cách sửa phù hợp với API của thư viện đang dùng:
   - Ưu tiên không dùng checkpointer toàn cục cho các luồng chạy một lượt nếu checkpoint không cần tồn tại sau run; **hoặc**
   - Xóa checkpoint của đúng thread ngay sau khi run kết thúc, bảo đảm cleanup chạy cả khi có exception.
4. Không xóa checkpoint của thread khác hoặc thread đang chạy.
5. Nếu giao diện/API có chức năng tiếp tục một thread hội thoại lâu dài, không làm mất khả năng tiếp tục đó. Tách rõ run một lượt với conversation/thread có chủ đích tồn tại lâu hơn.
6. Tránh phụ thuộc vào private API không ổn định của thư viện nếu có giải pháp lifecycle được hỗ trợ.
7. Thêm test dùng checkpointer giả hoặc dữ liệu nhỏ để xác nhận dữ liệu của thread đã kết thúc được giải phóng/không còn truy xuất được.
8. Kiểm tra đường chạy lỗi: cleanup vẫn được gọi nếu agent ném exception.

#### Tiêu chí hoàn thành

- Run đã hoàn tất không để lại checkpoint không cần thiết trong `MemorySaver`.
- Không ảnh hưởng thread đang chạy hay thread cần được giữ lại.
- Cleanup được thực hiện ở cả success và failure.
- Test tái hiện được trường hợp trước đây gây giữ dữ liệu và chứng minh hành vi mới.

### Task 3 — Thay regex bằng SQL AST Guard thật

**File chính:** `src/codegen/guard.py`

Báo cáo đính chính rằng guard hiện tại mới dùng regex, chưa phải SQL AST guard. Dùng thư viện `sqlglot` để parse SQL và kiểm tra cấu trúc cú pháp thực sự.

#### Việc cần làm

1. Đọc contract hiện có của guard: hàm đầu vào/đầu ra, cách báo lỗi, dialect SQL, nơi gọi guard và policy hiện tại.
2. Kiểm tra `sqlglot` đã nằm trong dependency chưa. Nếu chưa, thêm dependency đúng file quản lý package của dự án và khóa phiên bản theo quy ước repository.
3. Parse SQL bằng `sqlglot` với dialect phù hợp. Không chỉ tìm từ khóa bằng regex.
4. Duy trì hoặc tăng cường policy bảo mật đang được code sử dụng. Tối thiểu phải xác định rõ:
   - Chấp nhận loại câu lệnh nào (ví dụ truy vấn đọc nếu đó là policy hiện có).
   - Từ chối câu lệnh hoặc biểu thức ngoài policy.
   - Xử lý nhiều statement trong một input.
   - Xử lý SQL sai cú pháp, dialect không hỗ trợ và trường hợp parse không tạo AST.
   - Xử lý các biểu thức/construct nguy hiểm theo policy của ứng dụng.
5. Không tự suy diễn rằng parse thành công đồng nghĩa SQL an toàn. Guard phải kiểm tra AST theo allowlist/policy.
6. Không làm yếu các kiểm soát khác như giới hạn quyền DuckDB, kiểm duyệt identifier hoặc placeholder ở tầng storage.
7. Giữ interface mà caller đang sử dụng nếu không có lý do bắt buộc phải đổi; nếu đổi, cập nhật toàn bộ caller và test cùng commit.
8. Viết test cho:
   - SQL hợp lệ được phép.
   - SQL sai cú pháp bị từ chối an toàn.
   - Statement bị cấm bị từ chối.
   - Nhiều statement, comment, chữ hoa/thường và khoảng trắng không thể lách policy.
   - Các trường hợp nested query/CTE nếu được policy cho phép.
   - Dialect được hỗ trợ.
9. Không dùng regex như cơ chế quyết định bảo mật chính; regex chỉ nên dùng cho kiểm tra phụ khi có lý do rõ ràng.

#### Tiêu chí hoàn thành

- AST guard dùng `sqlglot` thật.
- Có policy rõ ràng và test bao phủ cả allow/deny.
- Lỗi parse được xử lý theo hướng fail-closed: từ chối SQL không thể xác minh.
- Không làm hỏng luồng codegen hợp lệ đang được hỗ trợ.

### Task 4 — Mở rộng che secret trong `_safe_output` (N4)

**File chính:** `src/codegen/sandbox.py`, hàm `_safe_output` (báo cáo chỉ vị trí quanh dòng 278–289).

#### Việc cần làm

1. Đọc toàn bộ đường đi output từ sandbox tới log/API/UI để xác định dữ liệu nào có thể chứa secret.
2. Giữ khả năng che các biến hậu tố `__CREDENTIALS` hiện có.
3. Mở rộng che các dạng secret nhạy cảm khác theo dữ liệu thực tế dự án có thể xử lý, ví dụ API key, bearer token, password, access token, private key hoặc connection string có thông tin xác thực.
4. Đặc biệt kiểm tra secret nằm trong URL/DSN đầy đủ, header, JSON, traceback hoặc chuỗi thông báo lỗi; không chỉ che một biến có tên đặc biệt.
5. Ưu tiên thay giá trị nhạy cảm bằng placeholder ổn định như `[REDACTED]`. Không ghi secret gốc vào log debug khi phát hiện lỗi che.
6. Tránh che quá rộng khiến mọi output vô dụng; giữ lại thông tin không nhạy cảm cần thiết cho debug.
7. Viết test với secret giả (không dùng credential thật), gồm chuỗi độc lập, URL/DSN, JSON và các định dạng log có thể gặp.
8. Xác nhận output sau che không còn chứa giá trị secret gốc.

#### Tiêu chí hoàn thành

- Các dạng secret được hỗ trợ bởi test đều bị che.
- Các thông tin chẩn đoán không nhạy cảm vẫn đọc được.
- Không log lại secret trong chính quá trình redaction.

### Kiểm thử Sprint 1

- Thêm test vào cấu trúc test hiện có; không tạo framework kiểm thử mới nếu không cần.
- Chạy các test mục tiêu trước, sau đó chạy:
  ```bash
  python -m ruff check src tests
  python -m pytest tests -q
  ```
  Nếu dự án yêu cầu môi trường ảo hoặc lệnh Windows riêng, dùng đúng interpreter/venv của repository.
- Không báo test pass nếu test chưa thực sự chạy. Ghi rõ test nào chạy, test nào bị chặn và lý do.
- Commit các thay đổi theo từng nhóm logic độc lập, phù hợp nguyên tắc mỗi hạng mục một commit.

---

## 5. Sync Gate 1 — Bàn giao trước Sprint 2

Trước khi bắt đầu Sprint 2:

- [ ] Báo Người 1 biết thay đổi Agent/Codegen đã sẵn sàng tích hợp.
- [ ] Nhận contract đường dẫn DuckDB chuẩn từ Người 3.
- [ ] Nhận định danh, schema và cách kết nối chính thức của `review_api` gắn Pagila từ Người 3.
- [ ] Xác nhận cấu trúc bundle và contract deployer.
- [ ] Xác nhận cách Dagster báo trạng thái nạp/chạy pipeline sau deploy.
- [ ] Rebase/merge theo quy trình nhóm; giải quyết conflict, không ghi đè code của thành viên khác.

Nếu contract `review_api` chưa rõ, tiếp tục công việc Deploy hoặc test độc lập, nhưng không tự phỏng đoán cấu hình nguồn.

---

## 6. Sprint 2 — Deploy 1-chạm, `review_api`, Planner/UI và export

### Task 5 — Triển khai Deploy 1-chạm theo ADR-0001 §D6 (#5)

**Các vị trí được báo cáo nêu:** `src/run_report/router.py` quanh `open_pull_request` và `src/codegen/deploy.py` quanh xử lý thiếu cấu hình GitHub. Tìm lại theo symbol và đọc ADR-0001 §D6 trước khi sửa.

#### Hành vi mục tiêu

Khi Reviewer hoặc Admin bấm Deploy:

1. Backend xác minh danh tính và quyền deploy; Engineer hoặc vai trò không được phép phải bị từ chối ở backend.
2. Backend lấy đúng run/bundle đã được ký duyệt, bao gồm các thành phần theo contract hiện có: `files`, `manifest.json`, `run_report.json`.
3. Backend gửi bundle cho service deployer.
4. Deployer xác minh chữ ký/hash trước khi cài đặt; không tin dữ liệu chỉ vì request đến từ backend.
5. Deployer cài trực tiếp vào Dagster và thực hiện bước chạy/xác nhận theo ADR và contract của dự án.
6. Khi kết quả xác nhận thành công, cập nhật trạng thái run sang `DEPLOYED`.
7. Nếu `PIPELINE_GITHUB_REPO` và `PIPELINE_GITHUB_TOKEN` có cấu hình, việc mở Pull Request GitHub chạy nền như kênh lưu vết GitOps tùy chọn.
8. Thiếu cấu hình GitHub không được làm Deploy thất bại; không bắt người dùng chờ PR merge.
9. Nếu xác minh bundle, cài đặt hoặc bước xác nhận thất bại, không đánh dấu `DEPLOYED`. Trả lỗi phù hợp và giữ trạng thái phản ánh đúng kết quả thực tế.

#### Việc cần làm

- Đọc `ADR-0001 §D6`, router, deploy service và contract API deployer.
- Xác định trạng thái run hiện có, chuyển trạng thái hợp lệ và cách lưu audit/report.
- Bỏ sự phụ thuộc bắt buộc vào `open_pull_request` trong luồng đồng bộ.
- Đảm bảo GitHub PR là best-effort/background task và lỗi PR được log an toàn, không rollback một deploy đã thành công.
- Đảm bảo deployer vẫn là nơi xác minh chữ ký/hash và thực hiện cài đặt.
- Bảo đảm quyền được kiểm tra ở backend, không chỉ ẩn nút Deploy ở frontend.
- Thêm test cho quyền, thiếu cấu hình GitHub, deploy thành công, bundle không hợp lệ, deployer lỗi và trạng thái sau mỗi trường hợp.
- Dùng mock cho deployer/GitHub trong unit test; dành xác minh thực tế cho E2E trên VM.

#### Tiêu chí hoàn thành

- Deploy thành công không cần GitHub token/repository và không cần merge PR.
- Reviewer/Admin có thể deploy; vai trò không đủ quyền không thể gọi API để deploy.
- Bundle được xác minh trước khi cài đặt.
- Chỉ cập nhật `DEPLOYED` sau khi các bước bắt buộc thành công.
- PR GitHub, nếu có, không chặn luồng Deploy và không làm lộ secret.
- Không bỏ qua ADR-0001 §D6 hoặc contract deployer hiện hữu.

### Task 6 — Tích hợp `review_api` gắn Pagila vào Planner, Chat và UI

#### Quy tắc sản phẩm bắt buộc

1. `sales` không được dùng cho **thao tác/pipeline mới**.
2. Không thể lách quy tắc bằng cách gọi API trực tiếp hoặc yêu cầu Chat Planner tạo pipeline.
3. Run lịch sử dùng `sales` vẫn phải mở được, xem được và giữ nguyên dữ liệu.
4. Benchmark/bench không bị ảnh hưởng.
5. `review_api` phải có thể được chọn để tạo pipeline mới theo contract đã chốt ở Sync Gate 1.

#### Việc cần làm

1. Lần theo các luồng tạo kế hoạch:
   - Planner thông thường.
   - Planner được khởi tạo từ Chat.
   - API tạo mới/submit plan.
   - UI chọn nguồn dữ liệu mới.
   - Catalog/Glossary nếu chúng cung cấp nguồn cho thao tác mới.
2. Tìm mọi nơi `sales` được liệt kê hoặc được chấp nhận; phân biệt:
   - allowlist/validation cho thao tác mới;
   - danh sách hiển thị để tạo mới;
   - dữ liệu và màn hình đọc lịch sử.
3. Chặn `sales` tại backend validation/Planner, không chỉ ẩn khỏi giao diện.
4. Ẩn `sales` khỏi danh sách chọn nguồn mới và các danh mục Catalog/Glossary liên quan đến thao tác mới.
5. Không xóa metadata hoặc dữ liệu lịch sử cần để render run cũ. Trang chi tiết run cũ phải có thể hiển thị nguồn `sales` đã được lưu.
6. Thêm `review_api` vào cấu hình/registry nguồn theo contract đã thống nhất:
   - dùng đúng source ID chuẩn;
   - dùng đúng schema/connection adapter;
   - không hard-code credential;
   - không tự tạo cấu hình Pagila riêng nếu Người 3 chưa chốt.
7. Đảm bảo Planner thường và Chat Planner đều:
   - từ chối yêu cầu tạo mới với `sales`;
   - lập kế hoạch thành công với `review_api` khi đầu vào hợp lệ.
8. Nếu semantic scan/catalog được dùng để cung cấp lựa chọn nguồn, phối hợp Người 3 để nguồn mới xuất hiện và `sales` không được đưa lại vào lựa chọn mới. Không tự sửa phần semantic scan thuộc Người 3 nếu chưa phối hợp.
9. Thêm regression test cho cả đường API và Chat Planner; thêm test đảm bảo run lịch sử `sales` vẫn đọc được.

#### Tiêu chí hoàn thành

- `sales` bị từ chối ở backend cho mọi đường tạo mới đã xác định.
- UI không đưa `sales` vào danh sách chọn mới.
- Chat không thể tạo kế hoạch mới sử dụng `sales`.
- `review_api` được nhận diện và lập kế hoạch thành công ở Planner thường lẫn Chat Planner.
- Run lịch sử `sales` vẫn xem được; không có migration xóa dữ liệu lịch sử.
- Bench không bị thay đổi ngoài dự kiến.

### Task 7 — Kiểm chứng export CSV/XLSX từ Gold, không triển khai lại (#6)

Tính năng export đã hoàn thành ở commit `5fe741c`. Công việc của bạn là **kiểm chứng hồi quy**, đặc biệt sau khi Người 3 tách DuckDB thành file riêng `data/warehouse/<model_name>.duckdb`.

#### Điểm cần kiểm tra

- Endpoint: `GET /api/v1/datasets/{id}/export?format=csv`.
- UI: nút Tải CSV trong `web/app/_components/run-result.tsx`.
- Kiểm tra thêm định dạng XLSX theo API/implementation hiện có.
- Dataset/bảng Gold được đọc từ đúng file DuckDB của pipeline/model tương ứng, không đọc nhầm file warehouse dùng chung hoặc file của pipeline khác.
- Kiểm tra dữ liệu xuất, header/cột, số dòng và định dạng response/download.
- Kiểm tra dataset/run không tồn tại, format không hỗ trợ, bảng Gold rỗng hoặc lỗi truy cập DB theo quy ước API hiện có.
- Nếu phát hiện lỗi do contract đường dẫn mới, phối hợp Người 3 và sửa phần thuộc phạm vi của mình; không viết lại export từ đầu.

#### Tiêu chí hoàn thành

- CSV và XLSX tải được từ bảng Gold với đường dẫn DuckDB mới.
- Dữ liệu tải xuống khớp dữ liệu trong bảng Gold.
- Không đọc nhầm dữ liệu giữa hai pipeline/model.
- Các lỗi đầu vào trả về response phù hợp; không lộ path/secret nội bộ.
- Có test tự động phù hợp; có kết quả kiểm tra E2E nếu môi trường VM sẵn sàng.

---

## 7. Bộ kiểm thử bắt buộc

Tên test dưới đây là mục tiêu hành vi, không phải tên file bắt buộc. Đặt test theo convention của repository.

### Agent và Codegen

- [ ] `ChatOpenAI` có `max_retries` tường minh và tổng ngân sách retry nhỏ hơn 300 giây.
- [ ] Timeout/exception của LLM không bị biến thành kết quả thành công giả.
- [ ] Checkpoint của run một lượt được dọn sau thành công.
- [ ] Checkpoint được dọn khi run ném exception.
- [ ] Cleanup không xóa thread khác hoặc thread hội thoại cần giữ lại.
- [ ] SQL hợp lệ theo policy được chấp nhận.
- [ ] SQL sai cú pháp/không parse được bị từ chối.
- [ ] Statement hoặc AST construct ngoài allowlist bị từ chối.
- [ ] Multi-statement/biến thể khoảng trắng/comment không lách guard.
- [ ] `_safe_output` che credential suffix hiện có và các dạng secret được hỗ trợ.
- [ ] Output sau redaction không còn giá trị secret gốc.

### Deploy 1-chạm

- [ ] Reviewer deploy được.
- [ ] Admin deploy được.
- [ ] Vai trò không đủ quyền bị từ chối kể cả gọi API trực tiếp.
- [ ] Thiếu `PIPELINE_GITHUB_REPO`/`PIPELINE_GITHUB_TOKEN` vẫn deploy được.
- [ ] GitHub PR chạy nền khi cấu hình có mặt; lỗi PR không chặn deploy.
- [ ] Bundle có chữ ký/hash sai không được cài đặt.
- [ ] Deployer thất bại thì run không bị đánh dấu `DEPLOYED`.
- [ ] Chỉ đánh dấu `DEPLOYED` sau xác nhận thành công.

### Planner và nguồn dữ liệu

- [ ] Planner thường từ chối `sales` khi tạo mới.
- [ ] Chat Planner từ chối `sales` khi tạo mới.
- [ ] API trực tiếp không thể vượt qua chặn `sales`.
- [ ] `review_api` được Planner thường chấp nhận khi cấu hình hợp lệ.
- [ ] `review_api` được Chat Planner chấp nhận khi cấu hình hợp lệ.
- [ ] UI chọn nguồn mới không hiển thị `sales`.
- [ ] Catalog/Glossary không đưa `sales` trở lại lựa chọn thao tác mới.
- [ ] Run lịch sử `sales` vẫn hiển thị chi tiết bình thường.
- [ ] Bench hiện có vẫn chạy và kết quả không bị phá vỡ.

### Export và tương thích DuckDB

- [ ] Export CSV thành công từ bảng Gold.
- [ ] Export XLSX thành công từ bảng Gold.
- [ ] Export đọc đúng `data/warehouse/<model_name>.duckdb`.
- [ ] Hai pipeline/model không đọc nhầm kho của nhau.
- [ ] Dữ liệu/header/số dòng xuất khớp bảng Gold.
- [ ] Trường hợp dataset không tồn tại/format sai được xử lý đúng.

---

## 8. Quy trình chạy kiểm thử và nghiệm thu

### 8.1. Kiểm thử cục bộ

1. Chạy test mục tiêu sau từng task.
2. Chạy lint:
   ```bash
   python -m ruff check src tests
   ```
3. Chạy toàn bộ unit test:
   ```bash
   python -m pytest tests -q
   ```
4. Nếu repository dùng `.venv` hoặc Windows interpreter riêng, thay `python` bằng interpreter đúng của dự án. Báo cáo gốc dùng:
   ```powershell
   .venv\Scripts\python.exe -m ruff check src tests
   .venv\Scripts\python.exe -m pytest tests -q
   ```
5. Kiểm tra diff và trạng thái Git:
   ```bash
   git diff --check
   git status --short
   git diff --stat
   ```
6. Không xóa, reset hoặc sửa file không thuộc phạm vi để làm test pass.

### 8.2. E2E trên VM — phối hợp Người 2 + Người 3

- [ ] Đăng nhập bằng tài khoản Reviewer.
- [ ] Deploy một pipeline đã được duyệt.
- [ ] Xác nhận deployer cài bundle trực tiếp và run chuyển sang `DEPLOYED` mà không chờ merge PR.
- [ ] Xác nhận Dagster reload theo cơ chế Người 3 triển khai; không tự khởi động lại toàn bộ tiến trình hoặc giết job đang chạy.
- [ ] Xác nhận pipeline ghi vào file DuckDB riêng đúng model.
- [ ] Tải CSV và XLSX từ bảng Gold.
- [ ] Xác nhận dữ liệu export đúng với kho DuckDB mới.

### 8.3. E2E nguồn `sales` và `review_api` — phối hợp cả nhóm

- [ ] Thử chọn `sales` trên UI tạo mới: không xuất hiện/không được phép.
- [ ] Gọi API trực tiếp tạo mới với `sales`: bị từ chối.
- [ ] Dùng Chat Planner yêu cầu tạo pipeline với `sales`: bị từ chối.
- [ ] Mở run lịch sử dùng `sales`: dữ liệu vẫn hiển thị.
- [ ] Chạy bench hiện có: không bị ảnh hưởng.
- [ ] Tạo pipeline mới với `review_api` gắn Pagila.
- [ ] Chạy pipeline qua Sandbox Deploy lên Dagster.
- [ ] Xác nhận pipeline chạy và ghi thành công vào file DuckDB riêng.

---

## 9. Quy tắc an toàn và xử lý lỗi

- Không in/log secret, connection string đầy đủ hoặc token để phục vụ debug.
- Không dùng credential production trong unit test; dùng giá trị giả.
- Không đánh dấu Deploy thành công khi deployer chưa xác nhận.
- Không để lỗi GitHub PR làm thất bại một deployment đã cài đặt thành công.
- Không nuốt lỗi AST guard, checkpoint cleanup hoặc deployer theo cách khiến caller hiểu nhầm thành công.
- Với guard bảo mật, mặc định từ chối nếu không thể parse hoặc xác minh an toàn.
- Không làm thay đổi quyền xem run lịch sử khi chặn nguồn `sales` cho pipeline mới.
- Nếu gặp lỗi do contract của Người 3 chưa sẵn sàng, ghi rõ blocker, input cần bàn giao và test chưa thể chạy; không giả lập thành công.

---

## 10. Cách chia commit và báo cáo bàn giao

Ưu tiên commit riêng theo nhóm logic, ví dụ:

1. `fix(agents): bound LLM retry time`
2. `fix(agents): clean up completed run checkpoints`
3. `feat(codegen): enforce SQL AST guard with sqlglot`
4. `fix(codegen): redact sensitive sandbox output`
5. `feat(deploy): support direct one-click deployment`
6. `feat(planner): add review_api and block sales for new plans`
7. `test(export): verify Gold exports with per-model DuckDB`

Tên commit có thể theo convention thực tế của repository. Không gộp thay đổi của người khác vào commit của mình nếu không được yêu cầu.

Khi bàn giao, cung cấp:

- Tóm tắt thay đổi theo task.
- Danh sách file đã sửa.
- Contract/giả định đã dùng, đặc biệt đối với `review_api`, bundle và DuckDB.
- Lệnh kiểm thử đã chạy và kết quả thực tế.
- Các test còn thiếu hoặc E2E chưa chạy được, kèm lý do.
- Rủi ro còn lại và blocker cần Người 1/Người 3 xác nhận.
- Hash commit tương ứng.

## 11. Checklist nghiệm thu cuối

- [ ] LLM retry nằm trong giới hạn phase 300 giây.
- [ ] Checkpoint không bị giữ lại sau run một lượt.
- [ ] SQL guard sử dụng `sqlglot` AST, fail-closed và có test.
- [ ] `_safe_output` che các secret đã xác định và có test.
- [ ] Deploy trực tiếp không phụ thuộc merge PR GitHub.
- [ ] Quyền deploy được kiểm tra ở backend.
- [ ] `sales` bị chặn ở mọi luồng tạo mới nhưng lịch sử vẫn nguyên vẹn.
- [ ] `review_api` hoạt động ở Planner và Chat Planner theo contract đã chốt.
- [ ] CSV/XLSX export hoạt động với DuckDB riêng theo model.
- [ ] Ruff và toàn bộ pytest đã chạy; kết quả được báo cáo trung thực.
- [ ] E2E liên quan đã được chạy trên VM hoặc ghi rõ chưa thể chạy.
- [ ] Không có secret, dữ liệu lịch sử bị xóa hoặc thay đổi ngoài phạm vi.
