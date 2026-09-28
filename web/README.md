# VSF Pipeline — giao diện

Next.js + TypeScript + Monaco. Bố cục theo Microsoft Fabric: rail icon trái, header workspace có thanh lệnh,
canvas task flow ở giữa, pane chi tiết bên phải, panel kéo được phía dưới. Chức năng và chat với agent nằm trong
menu **Agent** ở góc trên phải (kiểu menu Genie của Databricks).

## Chạy

```sh
make seed          # ở thư mục gốc, một lần
make api           # FastAPI ở cổng 8000 (không có ANTHROPIC_API_KEY thì dùng LLM giả lập tất định)
make web           # Next.js ở cổng 3000, chuyển /api/* sang CP_API_URL (mặc định http://127.0.0.1:8000)
```

Kiểm tra: `npm run typecheck`, `npm test`, `npm run build`.

## Chế độ demo (không cần backend)

Giao diện phát lại các run thật đã ghi trong `lib/demo/recording.json`: tạo pipeline, xác nhận kế hoạch, P7 fail rồi
`sql_fix` sửa, cảnh vượt ngân sách P8 → P4, chặn thực thể `customer`, duyệt, deploy D1. Số đo là số thật của lần ghi;
agent trả lời theo kịch bản đã ghi. Trạng thái chỉ nằm trong bộ nhớ trang, tải lại là về ban đầu.

| Cách chạy | Lệnh | Cần |
|---|---|---|
| Một file HTML | mở `dist/vsf-demo.html` (bấm đúp) | chỉ trình duyệt |
| Dev server | `npm ci && npm run dev:demo` (Windows PowerShell: `$env:NEXT_PUBLIC_DEMO=1; npm run dev`) | Node ≥ 20.9 |

Build lại file HTML sau khi sửa giao diện: `npm run build:demo-html` (xuất tĩnh Next.js ở chế độ demo rồi nhúng mọi JS, CSS,
font vào một file). Ghi lại dữ liệu demo từ backend thật: `make api` rồi `.venv/bin/python scripts/record_demo.py` ở thư mục gốc.

Gõ yêu cầu có "đơn hàng/doanh thu" → luồng chuẩn; "khách hàng" → chặn ngữ nghĩa; "web_events/lượt truy cập" → vượt ngân sách
rồi tự rút gọn; câu khác → agent hỏi lại một câu làm rõ. Tab File dùng Monaco từ CDN nên cần mạng; không có mạng thì hiện code thường.

## Màn hình

| Vùng | Nội dung | Nguồn dữ liệu |
|---|---|---|
| Canvas | 6 nhóm G1–G6, bung thấy P0–P17; 3 vòng lặp (spec, SQL, người) vẽ nét đứt, sáng lên khi đã chạy | SSE `/runs/{id}/events` |
| Pane phải | Bấm thẻ nhóm: từng phase làm gì, ai làm (LLM / tất định / người). Không chọn: bằng chứng, bộ đếm, cổng, màn chặn | `GET /runs/{id}`, `/explain` |
| Panel dưới | Mục (danh sách run), File (Monaco diff theo version), Kiểm tra, Log, Spec, Kết quả gold; thanh review P15/P16 | `GET /runs`, `/files` |
| Menu Agent | Tạo pipeline, Theo dõi run, Vì sao bị chặn, Giải thích code, Góp ý cho agent; UC-2/3/4 hiện "Sắp có" | các endpoint POST |
| Chính sách | 3 file YAML chỉ đọc, trần đọc từ `/policy` | `GET /policy` |

Không dùng `localStorage`. Trần LLM và số lượt sửa đọc từ `/policy` và `caps` trong RunReport, không hardcode.
Vai trò Engineer (minh) / Reviewer (an) là người dùng demo, chưa có xác thực thật.
Monaco tải từ CDN jsdelivr; nếu CDN bị chặn, tab File hiện code thuần có số dòng.
