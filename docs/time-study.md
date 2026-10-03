# Đo thời gian: làm tay so với dùng agent

Mục tiêu: cùng **một yêu cầu**, đo thời gian thật của hai cách làm, tính cả phần việc của người (chỉnh mapping, review).
Phía agent được hệ thống **tự ghi** (khung "Thời gian & công sức" ở tab Tổng quan của run). Phía làm tay **phải bấm giờ
thật**: không ước lượng, không lấy số từ nơi khác.

## Yêu cầu dùng để đo

> Tính doanh thu và số đơn hàng theo từng ngày (nguồn `pagila_pg`, bảng `payment`).

Kết quả đúng để đối chiếu (tính thẳng trên Postgres): 50 ngày, tổng 227.768,87, 32.093 giao dịch.

## Phía agent (tự ghi)

1. Engineer: **Yêu cầu pipeline** → chọn `pagila_pg.payment` → nhập yêu cầu, **để tích** "Xem & chỉnh mapping" → Gửi.
2. Tab **Mapping**: xem, chỉnh nếu cần → **Chạy thử với mapping này**.
3. Reviewer (đăng nhập An): mở run → xem Kết quả / Bằng chứng → **Phê duyệt**.
4. Mở tab **Tổng quan** của run: khung "Thời gian & công sức" ghi Tổng / Máy / Người và từng chặng, cùng số dòng code agent đã sinh.

Thời gian của người là thời gian thực giữa hai mốc (gồm cả lúc đọc, cân nhắc). Muốn số sạch thì Reviewer bắt đầu xem
ngay khi run chuyển sang "Chờ duyệt".

## Phía làm tay (bấm giờ)

Một engineer **không dùng agent** làm ra cùng sản phẩm mà agent làm ra, với cùng độ kỹ:

| # | Việc | Sản phẩm tương đương của agent |
|---|---|---|
| 1 | Tìm bảng, đọc schema và dữ liệu mẫu của `pagila.payment` | Kế hoạch + Spec |
| 2 | Viết script tải dữ liệu Postgres → bronze (dlt) | `ingestion/*_pipeline.py` |
| 3 | Viết model staging: ép kiểu, luật loại dòng, khử trùng | `models/staging/*.sql` |
| 4 | Viết model gold gộp theo ngày (UTC) | `models/gold/*.sql` |
| 5 | Viết test dbt (not_null, unique, not_negative) | `models/gold/*.yml` |
| 6 | Viết asset Dagster + lịch chạy 02:00 | `infra/dagster/generated/*_asset.py` |
| 7 | Chạy thử, sửa lỗi tới khi kết quả **khớp truy vấn kiểm tra trên Postgres** (50 ngày, tổng 227.768,87) | Bước chạy thử + đối chiếu độc lập |
| 8 | Một người khác review code và kết quả rồi đồng ý | Reviewer duyệt |

Quy tắc: bấm giờ từ lúc nhận yêu cầu tới lúc bước 8 xong; được dùng tài liệu như ngày làm việc bình thường; dừng đồng
hồ khi nghỉ. Ghi cả các lần sửa lỗi ở bước 7, vì đây là phần agent làm thay.

## Bảng kết quả

| Ngày đo | Người làm | Làm tay (tổng) | Agent: tổng | Agent: máy | Agent: người (mapping + review) | Run |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |

Ghi chú khi trình bày: nói rõ số liệu làm tay đo với **ai** (kinh nghiệm dbt/dlt bao nhiêu) và số lần đo; một lần đo
là minh họa, không phải thống kê.
