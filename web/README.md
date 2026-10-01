# Pipeline Studio · frontend

Next.js App Router + TypeScript cho Engineer và Reviewer. Mọi dữ liệu đọc từ backend FastAPI qua proxy `/api/v1/*` (cùng origin, cookie phiên HttpOnly); không còn dữ liệu mẫu hay sự kiện mô phỏng trong trình duyệt.

## Chạy

Cần backend ở `http://127.0.0.1:8000` (xem `docs/task-b.md`, `docs/task-c.md`). Backend ở địa chỉ khác thì đặt `PIPELINE_BACKEND_URL` khi khởi động Next.

```bash
cd web
npm ci
npm run dev        # http://localhost:3000
npm run typecheck
npm test
npm run build
```

## Màn hình và API

| Màn | Làm gì | API |
|---|---|---|
| Đăng nhập | Chọn người dùng demo; bị khóa khi backend `APP_ENV=production` | `GET /pipeline/users`, `POST/GET/DELETE /pipeline/session` |
| Runs | 100 run gần nhất trong sổ bằng chứng, lọc, tìm; chi phí hôm nay so với trần `budgets.yml` | `GET /runs`, `GET /policy` |
| Tạo yêu cầu | Engineer gửi yêu cầu bằng lời; agent lập kế hoạch → viết Spec → sinh code → chạy thử (~30 giây, chạy đồng bộ) | `POST /agent/runs` |
| Chi tiết run | Cây file và code sinh ra (chỉ đọc), bảng bronze/staging/gold với số dòng đo thật, cổng + phép đối soát, log từ `spec_history`/`error_trace`, kế hoạch và Spec | `GET /runs/{id}`, `GET /runs/{id}/files` |
| Run dừng sớm | Lý do, bằng chứng, luật áp dụng đọc từ RunReport; sửa yêu cầu hoặc chạy lại (tạo run mới) | `POST /agent/runs` |
| Chính sách | Nội dung thật của `policy/*.yml`, chỉ đọc | `GET /policy` |
| Trợ lý (chat) | Chưa mở run: Engineer gửi yêu cầu, agent tạo run mới. Đang mở run: hỏi về run đó, trả lời tất định từ RunReport (trần `max_chat_calls`); lịch sử chat lưu cùng run | `POST /chat`, `GET /runs/{id}/messages` |

`lib/studio.ts` chuyển `RunReport` thành mô hình hiển thị (`toStudioRun`), không thêm số liệu nào không có trong report. `next.config.ts` nâng `experimental.proxyTimeout` lên 300 giây vì `POST /agent/runs` chạy lâu hơn mặc định 30 giây của proxy.

## Chưa có ở backend (nút khóa, ghi rõ trên giao diện)

- Yêu cầu sửa, hủy run (duyệt / từ chối và ghi Dagster local đã có: `POST /runs/{id}/approve`, `/reject`).
- Theo dõi tiến độ trực tiếp (SSE): trong lúc chờ chỉ hiện trạng thái "Agent đang chạy", không giả lập phase.
- Chat phân loại ý định bằng LLM, trả lời câu hỏi làm rõ (`clarify_answer`), sửa qua chat (`revise`) và bước Engineer xác nhận kế hoạch (P2) — Sprint 2.

Monaco tải mã từ CDN; trong lúc tải (hoặc khi mạng chặn CDN) code vẫn hiện dạng văn bản có số dòng.