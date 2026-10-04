# Sprint 0-B + Sprint 1-B: Postgres → làm sạch → frontend

## Phần đã triển khai

- Postgres 16 riêng, 10 khách hàng và 30 đơn hàng giả lập; tổng tiền `465000.00`.
- Seed chạy lại không nhân đôi ID. Image chứa seed để chạy được cả khi Docker Desktop không mount được ổ ngoài.
- Tài khoản `pipeline_reader` chỉ có quyền đọc nguồn.
- API đọc schema thật, nhận cấu hình đổi tên/ép kiểu, chạy DuckDB mới cho mỗi lần thực thi.
- `TRY_CAST` tách dòng lỗi, giữ giá trị gốc và tên cột lỗi; NULL nguồn được giữ nguyên.
- Màn `/postgres`: đăng nhập tài khoản local, cấu hình cột, chạy pipeline, xem kết quả/lỗi và 100 run gần nhất từ backend.
- Session cookie HttpOnly, giới hạn 8 giờ; backend kiểm tra quyền Engineer/Reviewer. Đăng nhập demo bị khóa khi `APP_ENV=production`.
- Kết quả lưu trong SQLite tại `data/pipeline/runs.sqlite3`, tồn tại qua restart backend.

## Chạy local

Yêu cầu: Python 3.11, Node 24, Docker và Docker Compose. Chạy lệnh backend từ thư mục gốc repo.

### 1. Cài dependency riêng

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/setup_pipeline.py
```

Script điền mật khẩu ngẫu nhiên cho Postgres nguồn vào `.env` gốc (tạo từ `.env.example` nếu chưa có), không ghi đè giá trị đã có; còn file cũ `infra/postgres/.env` thì lấy lại mật khẩu từ đó. `.env` bị Git bỏ qua. Backend và `docker compose --env-file .env` cùng đọc file này; biến môi trường tiến trình có ưu tiên cao hơn.

Nếu tạo file thủ công từ `.env.example`, phải đặt mật khẩu reader trùng với phần mật khẩu trong `PIPELINE_DATABASE_URL` (URL-encode nếu có ký tự đặc biệt).

### 2. Dựng và seed Postgres

```bash
docker compose --env-file .env -p p045-b \
  -f infra/postgres/compose.yml up -d --build --wait
```

Nguồn lắng nghe ở `127.0.0.1:5433`. Bảng nguồn: `sales_raw.orders`.

Seed tự chạy khi volume mới. Khi volume đã tồn tại hoặc vừa sửa seed, dùng:

```bash
python scripts/seed_pipeline.py
```

Lệnh này cập nhật các bản ghi demo theo khóa chính và quyền reader; không xóa volume. Nếu đổi mật khẩu reader trong `.env`, chạy lại `up` để container nhận môi trường mới rồi chạy lệnh seed. Đổi mật khẩu admin trên volume cũ cần quản trị riêng; biến `POSTGRES_PASSWORD` chỉ khởi tạo mật khẩu lúc tạo database.

### 3. Backend

```bash
python -m uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

### 4. Frontend — terminal khác

```bash
cd web
npm ci
npm run dev
```

Mở <http://localhost:3000/postgres> hoặc nút **Postgres** ở thanh trên của Studio.

Frontend proxy `/api/v1/*` sang backend `http://127.0.0.1:8000`, nên cookie cùng origin và không cần thêm cấu hình CORS. Nếu backend ở địa chỉ khác, đặt `PIPELINE_BACKEND_URL` khi khởi động Next. Với `next build`/`next start`, đặt URL này khi build vì rewrites được ghi vào bản build.

## Demo nghiệm thu

1. Đăng nhập `engineer` / `Engineer@123` (**Minh · Engineer**); phải đọc được 5 cột từ Postgres.
2. Giữ mapping mặc định: `amount_text → amount / decimal(18,2)`, `ordered_at_text → ordered_at / date`.
3. Bấm **Chạy pipeline**: 30 dòng đọc, 30 hợp lệ, 0 bị tách; dòng đầu có `amount = "1000.00"`.
4. Đổi tên đầu ra `amount` thành `revenue_vnd`, chạy lại: kết quả dùng tên mới.
5. Đổi kiểu của `amount_text` thành `date`, chạy: 30 dòng lỗi được tách; trạng thái **Cần kiểm tra**.
6. Refresh trình duyệt: phiên đăng nhập và lịch sử vẫn còn. Chọn run để xem lại kết quả.
7. Đăng xuất, đăng nhập `reviewer` / `Reviewer@123` (**An · Reviewer**): xem được lịch sử; UI vô hiệu hóa nút chạy và API trả 403 nếu vẫn gửi yêu cầu.
8. Dừng Postgres, bấm **Đọc lại schema**: hiển thị lỗi kết nối; không thay thế bằng dữ liệu mẫu.

## API cho A/C/D tích hợp

Mọi API nằm dưới `/api/v1` và dùng chung phiên đăng nhập (`src/pipeline/auth.py`, cookie `Path=/api/v1`). Danh tính luôn lấy từ phiên, không nhận từ body.

| Method | Path | Kết quả |
|---|---|---|
| GET | `/pipeline/users` | Danh sách người dùng demo |
| POST | `/pipeline/session` | Body `{ "username": "engineer", "password": "…" }`; xác thực và tạo session cookie |
| GET / DELETE | `/pipeline/session` | Xem phiên / đăng xuất |
| GET | `/pipeline/source` | Schema thật và giới hạn số dòng |
| POST | `/pipeline/runs` | Chạy đồng bộ, ghi và trả `RunReport` |
| GET | `/runs?status=` | 100 `RunReport` gần nhất trong sổ bằng chứng, lọc theo status |
| GET | `/runs/{id}` | `RunReport` đầy đủ |

Body ví dụ ở `examples/pipeline/postgres_cleaning.json`. Mapping sử dụng `from`, `as`, `type`; đây là phần đổi tên/ép kiểu của staging, không thay thế Spec đầy đủ do C quản lý.

### Sổ bằng chứng

Chỉ có một sổ bằng chứng: bảng `run_reports` trong SQLite `PIPELINE_STORE_PATH` (mặc định `data/pipeline/runs.sqlite3`, cùng file với bảng `sessions`), quản lý bởi `src/run_report/store.py`. Mỗi bản ghi đúng hợp đồng `RunReport` trong `src/run_report/models.py`; ghi lại cùng `run_id` là cập nhật run đó.

Run làm sạch Postgres được ghi như sau:

| Tình huống | `status` | `stage_reached` | Bằng chứng |
|---|---|---|---|
| Làm sạch xong, không dòng lỗi | `READY_FOR_REVIEW` | `report` | gate `schema_real` pass, check `staging_reconcile` pass |
| Có dòng ép kiểu lỗi hoặc nguồn rỗng | `NEEDS_HUMAN` | `report` | check `staging_reconcile` fail |
| Spec tham chiếu cột không có ở nguồn | `NEEDS_HUMAN` | `static` | gate `schema_real` fail, `observed.missing_columns` |
| Không kết nối được Postgres | `INFRA_ERROR` | `el_run` | `error_trace` có `source_id` |
| Lỗi sandbox / thiếu dependency | `INTERNAL_ERROR` | `t_run` | `error_trace` |

`staging_reconcile.observed` chứa `bronze`, `staging`, `rejected`, `reject_pct`, `reject_reasons`, tối đa 20 `reject_samples` và 20 `output_samples` kèm `output_schema`; sổ bằng chứng không lưu toàn bộ dữ liệu đầu ra. `user_request` là tên run, `spec` là body gửi lên, `timing.baseline_sec` là thời gian chạy. `cost` bằng 0 vì DuckDB local không quét warehouse. `approval` luôn là `null`: bước làm sạch chưa phải bước phê duyệt.

Muốn có sẵn các run mẫu (`READY_FOR_REVIEW`, `DEPLOYED`, `BLOCKED_SEMANTIC_CONFLICT`) để demo UI: `python scripts/seed_run_reports.py`. Chạy lại không nhân đôi.

Khung chat, review và các kịch bản demo trên trang Studio hiện hữu vẫn thuộc task A/D. Màn `/postgres` là luồng gọi API thật cho phạm vi Sprint 1-B.

## Phạm vi sandbox

Sandbox ở sprint này là file DuckDB mới trong thư mục tạm mỗi lần chạy, xóa khi thành công hoặc có exception. External access của DuckDB bị tắt; SQL được dựng từ tên cột đã kiểm tra và danh sách kiểu cho phép. Không nhận SQL/Python tự do từ người dùng.

Giới hạn mặc định: 1.000 dòng, 2 MB giá trị đầu vào, DuckDB 128 MB và một thread; Postgres connect timeout 5 giây, statement timeout 5 giây. Vượt giới hạn dữ liệu gây lỗi rõ ràng, không báo thành công trên dữ liệu bị cắt.

Đây là cách ly dữ liệu và thao tác cho phép, không phải container chạy code không tin cậy. Chưa có dlt/dbt, dedupe, chuẩn hóa tiền tệ, scheduler, SSE, hủy run hay phê duyệt/deploy; các bước này cần ghép theo Spec ở sprint tiếp theo. NULL và chuỗi rỗng có hành vi khác nhau; lỗi cast bị tách, NULL giữ lại. Không tự suy luận luật nghiệp vụ như số tiền âm hoặc cột bắt buộc.

## Kiểm thử

```bash
ruff check src/pipeline src/run_report pipeline_tests scripts/setup_pipeline.py scripts/seed_pipeline.py scripts/seed_run_reports.py
pytest tests pipeline_tests -q --tb=short
```

Test tích hợp chỉ chạy khi có `PIPELINE_TEST_DATABASE_URL`. Với database demo vừa dựng, chạy bằng đoạn sau để không in mật khẩu:

```bash
python - <<'PY'
import os
import pytest
from src.pipeline.settings import PipelineSettings
os.environ['PIPELINE_TEST_DATABASE_URL'] = PipelineSettings().database_url.get_secret_value()
raise SystemExit(pytest.main(['tests', 'pipeline_tests', '-q', '--tb=short']))
PY
```

Frontend:

```bash
cd web
npm run typecheck
npm test
npm run build
```

Mọi thư viện (kể cả psycopg, DuckDB, dlt, dbt) nằm trong `requirements.txt`; `requirements/pipeline.txt` chỉ còn trỏ về file này để CI `pipeline.yml` và Dockerfile cũ không vỡ. Workflow `.github/workflows/pipeline.yml` dựng thêm Postgres thật để chạy test tích hợp.

Docker backend có dependency mới:

```bash
docker build -f docker/pipeline.Dockerfile -t p045-pipeline-backend .
```

Khi chạy image, cấp `PIPELINE_DATABASE_URL` trỏ tới host Postgres mà container truy cập được, và volume ghi được ở `/app/data`. Dockerfile gốc cài `requirements.txt` nên cũng đủ thư viện chạy pipeline.

## File chung và push

Hai điểm gắn vào code chung: thêm router trong `src/main.py`, thêm link `/postgres` trong `web/app/studio-page.tsx`. Dependency Python khai báo trong `requirements.txt`.

Trước commit, chỉ stage file task B và kiểm tra:

```bash
git diff --cached --name-only
```

Không stage `config/`, file `.env`, dữ liệu SQLite hoặc cache build. Khi merge, vẫn cần review hai điểm tích hợp trên nếu thành viên khác sửa cùng đoạn; không thể cam kết mọi file sẽ không conflict.
