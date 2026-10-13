# Data Catalog · frontend

Next.js App Router + TypeScript. Giao diện tổ chức theo nguyên tắc **data catalog**: dataset (nguồn → bronze → staging → gold) là thực thể trung tâm; mỗi dataset có owner, thuật ngữ nghiệp vụ, schema, lineage, kiểm tra chất lượng và các run đã tạo ra nó. Run/pipeline là *hoạt động* trên dataset.

Mặc định đọc **backend thật** ở `http://127.0.0.1:8000` (đổi bằng `PIPELINE_BACKEND_URL`). Đặt `CATALOG_MOCK=1` để chạy giao diện với dữ liệu mock trong `lib/catalog/mock.ts`, không cần backend (thao tác ghi khi đó chỉ báo "Mock").

## Chạy

```bash
cd web
npm ci
npm run dev        # http://localhost:3000
npm run typecheck
npm test
npm run test:e2e   # Playwright tự bật Next + backend fixture, không gọi LLM/GitHub thật
npm run build
```

Cần Postgres nguồn và backend đang chạy (xem README gốc, mục "Chạy hệ thống"). Đăng nhập bằng `engineer` / `Engineer@123`, `reviewer` / `Reviewer@123` hoặc `admin` / `Admin@123`; muốn đổi vai trò thì đăng xuất và đăng nhập lại. Tab **Đăng ký** gửi mã xác thực email trước khi tạo tài khoản Engineer; quyền Reviewer chỉ được cấp từ màn **Tài khoản** của Admin.

**Phiên kết nối nguồn:** bấm Kết nối thành công thì tên nguồn được nhớ trong cookie `catalog_connections` (chỉ tên nguồn, không có credential, hết hạn cùng phiên đăng nhập 8 giờ). Mở lại form Yêu cầu pipeline hay trang Quan hệ thì tự kết nối lại (danh sách bảng vẫn đọc mới từ nguồn). Nút Ngắt hoặc Đăng xuất thì quên.

**Phiên đăng nhập:** Server Action gửi username/password tới `POST /pipeline/session`. Đăng ký gửi tên, email, username và password tới `POST /pipeline/register`; backend gửi OTP 6 số và chỉ tạo tài khoản khi `POST /pipeline/register/verify` xác thực thành công. Backend lưu mật khẩu bằng PBKDF2 + salt, đặt cookie HttpOnly `pipeline_session` với `Path=/api/v1`; action lấy token và đặt lại cookie với `Path=/` để mọi lời gọi backend từ server (`lib/catalog/source.ts`) gửi kèm token này. SMTP cấu hình bằng `PIPELINE_SMTP_*`; development không có SMTP sẽ hiện mã ngay trên UI.

## Màn hình

| Route | Làm gì | API thật sẽ dùng |
|---|---|---|
| `/` Khám phá | Tìm dataset theo tên, mô tả, cột, tag, thuật ngữ; lọc facet (lớp, domain, nền tảng, owner, tag); số liệu tổng và hoạt động gần đây | `GET /catalog`, `GET /runs` |
| `/datasets/[id]` | Tab Tổng quan · Schema · Lineage · Chất lượng · Pipeline & run · Dữ liệu mẫu · Làm sạch (chỉ bảng nguồn Postgres, thay trang `/postgres` cũ) | `GET /catalog`, `GET /runs`, `POST /pipeline/runs` |
| `/domains`, `/domains/[id]` | Steward, nguồn được phép đọc, dataset theo lớp, lineage toàn domain, thuật ngữ | `GET /policy`, `GET /catalog` |
| `/relationships` Quan hệ | Chọn một nguồn và bấm **Kết nối** (`?source=<ref>`): server kết nối trực tiếp tới nguồn, đọc bảng, khóa chính, khóa ngoại (`pg_constraint`) rồi vẽ sơ đồ ER; đường liền = khóa ngoại N–1, đường đứt = phép join pipeline đã dùng. Có Làm mới / Ngắt. Tab Quan hệ của từng dataset vẫn đọc từ catalog | `GET /catalog/sources`, `GET /catalog/sources/{ref}/objects`, `GET /runs` (`transform.joins`) |
| `/glossary` | Thuật ngữ theo domain, bảng khai báo, dataset dùng thuật ngữ; cảnh báo trùng tên chưa có trong `conformed_dimensions` | `GET /policy` |
| `/runs` Hoạt động | Run lọc theo trạng thái, dataset liên quan, chi phí so với trần `budgets.yml` | `GET /runs`, `GET /policy` |
| `/runs/[id]` | Sửa/xác nhận kế hoạch; xem Staging (lý do và dòng rác mẫu), bằng chứng, code, log; duyệt / từ chối / yêu cầu sửa; hủy run đang chạy | `GET /runs/{id}`, `GET /runs/{id}/files`, `POST /agent/runs/{id}/confirm-plan`, `POST /runs/{id}/approve`, `/reject`, `/request-changes`, `/cancel` |
| `/runs/new` | Chọn kết nối khai báo trong `domains.yml`, bấm **Kết nối**: backend kết nối trực tiếp tới nguồn lúc đó và trả các bảng đang có (cột, khóa chính, số dòng). Có thể tạo một pipeline hoặc một mẻ nhiều pipeline dùng chung phạm vi nguồn; UI hiện chi phí giữ chỗ ước tính và backend chặn toàn mẻ nếu vượt số dư ngày. Bắt đầu từ một dataset (`?dataset=`) thì tự kết nối và chọn sẵn bảng nguồn của nó | `GET /catalog/sources`, `GET /catalog/sources/{ref}/objects`, `GET /agent/runs/batch/budget`, `POST /agent/runs` hoặc `POST /agent/runs/batch` |
| `/policy` | Nội dung `policy/*.yml`, chỉ đọc | `GET /policy` |

## Dữ liệu đến từ đâu

- **Connector** (`src/pipeline/connectors.py`): mỗi loại nguồn một hàm khám phá metadata (bảng, cột, khóa, số dòng ước tính), đăng ký trong `CONNECTORS`. Hiện có Postgres; thêm MongoDB = thêm một connector trả collection và thêm `mongodb` vào `SourceKind` (`src/policy/models.py`). Thông tin đăng nhập luôn ở phía server (`domains.yml` + `.env`), không đi qua trình duyệt.
- **Nguồn**: `GET /catalog` (`src/api/catalog.py`) trả domain → nguồn → bảng/cột thật (information_schema, tài khoản chỉ đọc), steward và thực thể từ `domains.yml`. Nguồn chưa hỗ trợ vẫn hiện với lý do.
- **Bronze / staging / gold**: suy ra từ Spec của các run đã chạy thử xong (`READY_FOR_REVIEW`, `APPROVED`, `DEPLOYED`): `bronze.<target.table>`, `staging.stg_<model>__<source>`, `gold.<model>`. Run dừng sớm chỉ gắn vào bảng nguồn.
- **Số dòng, kiểm tra, mẫu**: chỉ lấy từ RunReport mới nhất của dataset (`row_reconcile`, `staging_reconcile`, `dbt_test`, gate `post_run`).

## Cấu trúc

- `lib/catalog/types.ts`: mô hình hiển thị (Dataset, Column, Domain, GlossaryTerm, Snapshot…). Run dùng chung kiểu `Run` của `lib/studio.ts`.
- `lib/catalog/live.ts`: `buildSnapshot()` dựng catalog từ phản hồi backend (hàm thuần, có test).
- `lib/catalog/query.ts`: lớp `Catalog` truy vấn trên snapshot (tìm kiếm + facet, lineage, run ↔ dataset, xung đột thuật ngữ).
- `lib/catalog/source.ts`: phía server, gọi backend kèm phiên (`getCatalog`, `currentUser`) hoặc trả mock.
- `lib/catalog/mock.ts`: dữ liệu mock cho `CATALOG_MOCK=1` và test.
- `app/actions.ts`: Server Action đăng nhập, gửi yêu cầu cho agent, duyệt / từ chối, làm sạch.
- `app/_components/`: thành phần dùng chung (`ui.tsx`, `lineage.tsx`, `shell.tsx`, client: `run-client.tsx`, `clean-form.tsx`).

## Vận hành trên Dagster

`make dagster` (hoặc `python scripts/dagster_local.py`) bật Dagster UI + daemon ở http://127.0.0.1:3070 cho mọi pipeline đã duyệt trong `data/dagster_local/` (workspace tự sinh, lịch sử run giữ ở `data/dagster_home`, warehouse DuckDB chung ở `data/warehouse/`). Trang dataset (lớp bronze/staging/gold) có panel **Vận hành · Dagster**: lần chạy cuối (thành công / thất bại, thời lượng, link run), dữ liệu cập nhật lúc nào, lịch chạy (bật / tắt) và lần chạy kế tiếp, đọc qua `GET /orchestration/status` (backend gọi GraphQL của Dagster). Lịch mặc định ở trạng thái tắt; bật trong Dagster UI (Automation).

## Hỏi đáp về run

Tab **Hỏi đáp** trên trang run gửi `POST /chat` kèm `run_id` và hiện lịch sử từ `GET /runs/{id}/messages`. Backend trả lời tất định từ RunReport (vì sao dừng, số dòng mỗi nguồn, file sinh ra, trạng thái duyệt), không gọi LLM và không đổi trạng thái run. Trần câu hỏi mỗi run là `per_run.max_chat_calls` trong `budgets.yml` (trả qua `GET /policy`); hết trần thì ô nhập khóa. Run đang chạy thì chưa hỏi được.

## Chưa có

- Thuật ngữ gắn theo cột, mô tả dataset do steward viết: backend chưa lưu.
- Tạo run mới từ chat (`POST /chat` không có `run_id`): giao diện dùng form Yêu cầu pipeline.
- Theo dõi tiến độ trực tiếp bằng SSE (giao diện hiện đang polling RunReport).

Monaco tải mã từ CDN; trong lúc tải (hoặc khi mạng chặn CDN) code vẫn hiện dạng văn bản có số dòng.
