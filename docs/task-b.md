# Sprint 0-B + Sprint 1-B: Postgres → làm sạch → frontend

## Phần đã triển khai

- Postgres 16 riêng, 10 khách hàng và 30 đơn hàng giả lập; tổng tiền `465000.00`.
- Seed chạy lại không nhân đôi ID. Image chứa seed để chạy được cả khi Docker Desktop không mount được ổ ngoài.
- Tài khoản `pipeline_reader` chỉ có quyền đọc nguồn.
- API đọc schema thật, nhận cấu hình đổi tên/ép kiểu, chạy DuckDB mới cho mỗi lần thực thi.
- `TRY_CAST` tách dòng lỗi, giữ giá trị gốc và tên cột lỗi; NULL nguồn được giữ nguyên.
- Màn `/postgres`: chọn người dùng demo, cấu hình cột, chạy pipeline, xem kết quả/lỗi và 100 run gần nhất từ backend.
- Session cookie HttpOnly, giới hạn 8 giờ; backend kiểm tra quyền Engineer/Reviewer. Đăng nhập demo bị khóa khi `APP_ENV=production`.
- Kết quả lưu trong SQLite tại `data/pipeline/runs.sqlite3`, tồn tại qua restart backend.

## Chạy local

Yêu cầu: Python 3.11, Node 24, Docker và Docker Compose. Chạy lệnh backend từ thư mục gốc repo.

### 1. Cài dependency riêng

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements/pipeline.txt
python scripts/setup_pipeline.py
```

Script tạo `infra/postgres/.env` với mật khẩu ngẫu nhiên, không ghi đè file đã có. File này bị Git bỏ qua. Backend đọc `.env` gốc rồi `infra/postgres/.env`; biến môi trường tiến trình có ưu tiên cao hơn cả hai.

Nếu tạo file thủ công từ `.env.example`, phải đặt mật khẩu reader trùng với phần mật khẩu trong `PIPELINE_DATABASE_URL` (URL-encode nếu có ký tự đặc biệt).

### 2. Dựng và seed Postgres

```bash
docker compose --env-file infra/postgres/.env -p p045-b \
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

Frontend proxy `/api/v1/pipeline/*` sang backend `http://127.0.0.1:8000`, nên cookie cùng origin và không cần thêm cấu hình CORS. Nếu backend ở địa chỉ khác, đặt `PIPELINE_BACKEND_URL` khi khởi động Next. Với `next build`/`next start`, đặt URL này khi build vì rewrites được ghi vào bản build.

## Demo nghiệm thu

1. Chọn **Minh · Engineer**; phải đọc được 5 cột từ Postgres.
2. Giữ mapping mặc định: `amount_text → amount / decimal(18,2)`, `ordered_at_text → ordered_at / date`.
3. Bấm **Chạy pipeline**: 30 dòng đọc, 30 hợp lệ, 0 bị tách; dòng đầu có `amount = "1000.00"`.
4. Đổi tên đầu ra `amount` thành `revenue_vnd`, chạy lại: kết quả dùng tên mới.
5. Đổi kiểu của `amount_text` thành `date`, chạy: 30 dòng lỗi được tách; trạng thái **Cần kiểm tra**.
6. Refresh trình duyệt: phiên đăng nhập và lịch sử vẫn còn. Chọn run để xem lại kết quả.
7. Đăng xuất, chọn **An · Reviewer**: xem được lịch sử; UI vô hiệu hóa nút chạy và API trả 403 nếu vẫn gửi yêu cầu.
8. Dừng Postgres, bấm **Đọc lại schema**: hiển thị lỗi kết nối; không thay thế bằng dữ liệu mẫu.

## API cho A/C/D tích hợp

Prefix: `/api/v1/pipeline`.

| Method | Path | Kết quả |
|---|---|---|
| GET | `/users` | Danh sách người dùng demo |
| POST | `/session` | Body `{ "user_id": "minh" }`; tạo session cookie |
| GET / DELETE | `/session` | Xem phiên / đăng xuất |
| GET | `/source` | Schema thật và giới hạn số dòng |
| POST | `/runs` | Chạy đồng bộ, trả kết quả thực thi đã lưu |
| GET | `/runs` | Tóm tắt 100 run gần nhất |
| GET | `/runs/{id}` | Kết quả đầy đủ, gồm output và rejected |

Body ví dụ ở `examples/pipeline/postgres_cleaning.json`. Mapping sử dụng `from`, `as`, `type`; đây là phần đổi tên/ép kiểu của staging, không thay thế Spec đầy đủ do C quản lý.

Kết quả thực thi gồm `run_id`, `created_by`, `version`, `spec_hash`, thời gian, số dòng, schema đầu ra, dữ liệu đầu ra và rejected. `created_by` lấy từ session backend, không nhận từ body. Mỗi lần bấm chạy tạo run mới, version 1. `approval` luôn là `null`: bước làm sạch chưa phải bước phê duyệt.

Đây là **kết quả thực thi task B**, chưa phải hợp đồng `src/run_report/models.py` của toàn bộ control plane. D có thể dùng số liệu này cho `checks[].observed`, `error_trace` và thời gian trong RunReport. `SUCCEEDED` chỉ có nghĩa đọc/làm sạch thành công; không có nghĩa toàn bộ cổng kiểm tra đã đạt hoặc run đã được duyệt.

`src/run_report/` và API mẫu `/api/v1/runs` của D vẫn giữ nguyên. Khung chat, review và các kịch bản demo trên trang Studio hiện hữu vẫn thuộc task A/D. Màn `/postgres` là luồng gọi API thật cho phạm vi Sprint 1-B.

## Phạm vi sandbox

Sandbox ở sprint này là file DuckDB mới trong thư mục tạm mỗi lần chạy, xóa khi thành công hoặc có exception. External access của DuckDB bị tắt; SQL được dựng từ tên cột đã kiểm tra và danh sách kiểu cho phép. Không nhận SQL/Python tự do từ người dùng.

Giới hạn mặc định: 1.000 dòng, 2 MB giá trị đầu vào, DuckDB 128 MB và một thread; Postgres connect timeout 5 giây, statement timeout 5 giây. Vượt giới hạn dữ liệu gây lỗi rõ ràng, không báo thành công trên dữ liệu bị cắt.

Đây là cách ly dữ liệu và thao tác cho phép, không phải container chạy code không tin cậy. Chưa có dlt/dbt, dedupe, chuẩn hóa tiền tệ, scheduler, SSE, hủy run hay phê duyệt/deploy; các bước này cần ghép theo Spec ở sprint tiếp theo. NULL và chuỗi rỗng có hành vi khác nhau; lỗi cast bị tách, NULL giữ lại. Không tự suy luận luật nghiệp vụ như số tiền âm hoặc cột bắt buộc.

## Kiểm thử

```bash
ruff check src/pipeline pipeline_tests scripts/setup_pipeline.py scripts/seed_pipeline.py
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
node --experimental-strip-types --test tests/postgres-api.test.ts
npm run build
```

Workflow `.github/workflows/pipeline.yml` cài dependency riêng và dựng Postgres thật. CI cũ vẫn chỉ cài `requirements.txt`: module pipeline không import psycopg/DuckDB lúc khởi động app, nên không làm hỏng test hiện có.

Docker backend có dependency mới:

```bash
docker build -f docker/pipeline.Dockerfile -t p045-pipeline-backend .
```

Khi chạy image, cấp `PIPELINE_DATABASE_URL` trỏ tới host Postgres mà container truy cập được, và volume ghi được ở `/app/data`. Dockerfile gốc vẫn dùng dependency gốc; muốn chạy pipeline cần dùng Dockerfile mới hoặc cài `requirements/pipeline.txt` trong image tích hợp.

## File chung và push

Hai điểm gắn vào code chung: thêm router trong `src/main.py`, thêm link `/postgres` trong `web/app/studio-page.tsx`. Dependency mới chỉ nằm ở `requirements/pipeline.txt`; không sửa `requirements.txt`, `web/package.json` hoặc lockfile.

Trước commit, chỉ stage file task B và kiểm tra:

```bash
git diff --cached --name-only
git diff --cached -- requirements.txt
```

Lệnh thứ hai phải rỗng. Không stage `config/`, file `.env`, dữ liệu SQLite hoặc cache build. Khi merge, vẫn cần review hai điểm tích hợp trên nếu thành viên khác sửa cùng đoạn; không thể cam kết mọi file sẽ không conflict.
