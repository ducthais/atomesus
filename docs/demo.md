# Kịch bản demo Atomesus (khoảng 15 phút)

**Thông điệp chính:** người làm dữ liệu mô tả nhu cầu bằng lời, agent sinh pipeline ELT thật (dlt → dbt → Dagster) trên dữ liệu thật, kèm bằng chứng đo được; con người duyệt rồi mới chạy định kỳ. Mọi thứ được tổ chức theo nguyên tắc data catalog: dataset là trung tâm, pipeline là hoạt động trên dataset.

Các run và con số trong kịch bản là dữ liệu thật trong hệ thống tại thời điểm viết; `scripts/demo_check.py` kiểm tra chúng còn đúng.

---

## Chuẩn bị (30 phút trước)

1. Mở **Docker Desktop**, rồi 4 terminal tại `D:\Repo\P-045`:
   ```bash
   docker compose --env-file infra/postgres/.env -p p045-b -f infra/postgres/compose.yml up -d --wait
   .venv\Scripts\uvicorn.exe src.main:app --port 8000
   .venv\Scripts\python.exe scripts\dagster_local.py
   cd web && npm run dev
   ```
2. Chạy kiểm tra, phải ra **"Sẵn sàng demo."**:
   ```bash
   .venv\Scripts\python.exe scripts\demo_check.py
   ```
3. Mở sẵn 2 tab trình duyệt:
   - http://localhost:3000, đăng nhập **Minh · Engineer**
   - http://127.0.0.1:3070 (Dagster), bấm **Skip** ở popup "Join the Dagster community"
4. **Tập trước bước 4** (tạo pipeline) một lần để chắc LLM trả lời ổn định hôm đó.
5. Phóng to trình duyệt 110–125% để mentor nhìn rõ; tắt thông báo hệ thống.
6. **Giữ nguyên** pipeline `gold.fct_total_revenue` đang chạy trên Dagster (bản cũ, trước khi sửa lỗi múi giờ). Đừng duyệt bản mới của nó trước buổi demo: đây là ca lệch thật cho phần điểm nhấn.

---

## 1. Mở đầu: bài toán (1 phút)

**Nói:** "Một yêu cầu dữ liệu như 'doanh thu theo khách hàng' thường mất vài ngày: tìm bảng, viết code tải dữ liệu, làm sạch, viết SQL, test, lên lịch. Atomesus để agent làm phần đó trong khoảng 1 phút, nhưng mọi con số đều được đo thật và phải có người duyệt trước khi chạy định kỳ."

---

## 2. Khám phá catalog (2 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Trang **Khám phá** | Mọi dataset từ nguồn → bronze → staging → gold, kèm owner, số dòng đo được, kết quả kiểm tra chất lượng. |
| Gõ `amount` vào ô tìm kiếm, Enter | Tìm được cả theo **tên cột**, không chỉ tên bảng. |
| Mở `gold.fct_daily_sales` | Một bảng gold: mô tả, thuật ngữ nghiệp vụ, lịch chạy. |
| Tab **Lineage** | Dữ liệu chảy từ `sales_raw.orders` → `bronze.raw_orders` → staging → gold. |
| Tab **Chất lượng** | Đối soát số dòng nguồn ↔ bronze, tỷ lệ dòng lỗi ở staging, dbt test. |
| Panel **Vận hành · Dagster** (tab Tổng quan) | Lần chạy cuối thành công trên Dagster, thời lượng, lịch 02:00 hằng ngày. |

---

## 3. Nguồn dữ liệu thật và quan hệ giữa các bảng (2 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Menu **Quan hệ** → `pagila_pg` → **Kết nối** | Hệ thống kết nối **trực tiếp** tới Postgres lúc bấm, đọc bảng, khóa chính, khóa ngoại. Pagila là bộ dữ liệu public: 15 bảng, 21 khóa ngoại. |
| Cuộn chuột để phóng to, kéo để di chuyển | Sơ đồ ER: đường liền là khóa ngoại (N → 1), ví dụ `payment → rental → inventory → film`. |
| Chỉ dòng "Đã kết nối · giờ" | Kết nối được nhớ trong phiên; lần sau không cần bấm lại. |
| (nếu được hỏi) | Hệ thống chỉ dùng tài khoản **chỉ đọc**; mật khẩu nằm ở server, không đi qua trình duyệt. |

---

## 4. Tạo pipeline bằng lời: phần chính (4 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Bấm **Yêu cầu pipeline** | |
| Ở `sales_pg`: bấm **Kết nối** (nếu chưa hiện bảng), rồi tích **customers** và **orders**. `pagila_pg` tự hiện bảng vì đã kết nối ở bước 3 | Agent chỉ được dùng đúng các bảng mình chọn; nguồn đã kết nối được nhớ trong phiên. |
| Nhập: **"Tổng doanh thu và số đơn hàng của từng khách hàng, kèm tên khách hàng"**, để nguyên ô **Xem & chỉnh mapping trước khi chạy thử** đã tích → **Gửi cho agent** | Trang run mở ngay, khung **Agent đang chạy** hiện từng bước (khoảng 15 giây): agent lập kế hoạch, đọc schema + 20 dòng mẫu thật, viết Spec, rồi **dừng lại ở bản nháp**. Agent chạy nền, rời trang cũng không sao. |
| Trang run mở ở tab **Mapping** | Engineer thấy agent định lấy cột nào, đặt tên gì, ép kiểu gì, loại dòng nào. Phần "Bảng kết quả sẽ tính" bên dưới cho biết gộp theo gì, tính chỉ số gì. Chưa có code nào chạy. |
| Bấm **Chạy thử với mapping này** | Bây giờ mới sinh code dlt/dbt/Dagster **từ template** (không phải LLM viết tự do) rồi chạy thật trong sandbox: dlt tải dữ liệu thật, dbt build, khoảng 15–20 giây; trang hiện tiến độ và tự chuyển khi xong. |
| Trang tự chuyển sang tab **Kết quả** | Diễn giải bằng lời: đọc bảng nào, làm sạch gì, mỗi dòng kết quả là gì; bên dưới là **dữ liệu kết quả thật**. |

**Nếu bước này lỗi hoặc quá lâu:** nói "LLM bên ngoài đôi khi chậm" rồi mở run có sẵn **`ce884a4f`** (cùng yêu cầu, đã chạy xong) từ trang **Hoạt động**.

---

## ★ Điểm nhấn: con số có bằng chứng (2 phút)

Đây là phần làm Atomesus khác các công cụ "AI viết SQL": hệ thống **tự chứng minh con số đúng** bằng một đường tính độc lập, và **bắt được lỗi mà mọi test đều cho qua**.

| Thao tác | Nói / chỉ vào |
|---|---|
| Ở run vừa tạo (hoặc **`98506251`**, doanh thu theo tháng), tab **Kết quả**: khung xanh trên cùng | "Mentor hỏi AI có bịa số không? Đây: sau khi chạy thử, hệ thống dịch Spec thành **một câu SQL chạy thẳng trên Postgres nguồn**, không đi qua dlt, DuckDB, dbt hay template, rồi so **từng dòng**. 6/6 dòng khớp tới từng xu." |
| Mở **Truy vấn đối chiếu trên Postgres** | "Câu SQL này ai cũng tự chạy lại được trên nguồn để kiểm tra." |
| Khám phá → **`gold.fct_total_revenue`** (pipeline đang chạy trên Dagster) → **Đối chiếu lại** | Khung đỏ: **tổng vẫn khớp 227.768,87**, nhưng 16.871 dòng lệch. Hệ thống **tự chẩn đoán: mọi thời điểm lệch đúng +7 giờ**, tức lỗi múi giờ. Hai cột cạnh nhau: nguồn 18:21 ngày 24/01, pipeline 01:21 ngày 25/01. |
| Câu chốt | "Lỗi này có thật, nằm trong pipeline của chính chúng tôi. Mọi dbt test đều đạt, tổng doanh thu cũng khớp. Chỉ có phép đối chiếu từng dòng bắt được nó. Bây giờ mỗi lần chạy thử đều đối chiếu như vậy, và lệch thì run bị chặn, không tới được tay Reviewer." |

**Nói thật nếu được hỏi:** đối chiếu chứng minh pipeline **tính đúng như Spec**. Spec có hiểu đúng ý người dùng không thì do cổng "Gộp đúng kỳ yêu cầu" và người duyệt lo (bước 5).

---

## 5. Bằng chứng, và khi AI hiểu chưa đúng ý (2 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Tab **Bằng chứng** | Mọi con số là phép đo của lần chạy: nguồn → bronze khớp, số dòng bị loại, dbt test, các cổng kiểm tra (Spec hợp lệ, cột có thật, biểu thức an toàn). |
| Tab **Code** | Các file sinh ra (dlt, dbt, Dagster), chỉ đọc. |
| Hoạt động → mở run **`bc434ba6`** ("Tính tổng doanh thu", Pagila) | Ví dụ AI hiểu **chưa đúng ý**: gộp theo thời điểm chính xác đến giây → 16.974 dòng. Đây là run cũ, trước khi có cổng **Gộp đúng kỳ yêu cầu**. |
| Mở run **`0bf1b8f4`** ("Tính doanh thu và số đơn hàng theo từng tháng") → tab **Bằng chứng** | Giờ yêu cầu nói "theo ngày / tuần / tháng / quý / năm" thì cổng **Gộp đúng kỳ yêu cầu** buộc Spec gộp đúng kỳ đó. Sai thì agent bị trả lại và tự sửa. Kết quả 6 dòng khớp từng con số với truy vấn trực tiếp Postgres (tháng 1: 4.824,43 USD / 1.157 đơn). |
| Mở run **`187c89e8`** ("Doanh thu thuê phim theo từng ngày") → tab **Bằng chứng**, phần lịch sử Spec | Cùng kiểu sai đó được **chặn từ bản nháp**: agent để `payment_date` kiểu `timestamp`, tức "theo ngày" thành "theo từng giây". Engineer đổi kiểu thành `date` và đặt tên `rental_day` ngay ở tab Mapping. Đổi tên thì phần gộp, khóa và test tự cập nhật theo. Kết quả là 49 dòng, mỗi ngày một dòng. Sổ bằng chứng giữ cả Spec gốc của agent lẫn dòng "Minh chỉnh mapping trước khi chạy thử". |
| Bấm **Sửa & chạy lại** trên một run đã duyệt, gửi yêu cầu sửa → tab **So sánh** của run mới | So với bản đang chạy trên Dagster: Spec đổi gì (mapping, bộ lọc, chế độ ghi), kết quả đổi bao nhiêu dòng / tổng chỉ số, diff từng file code, và pipeline khác nào dùng chung bảng bronze. Reviewer thấy ngay tác động trước khi duyệt. |

---

## 6. Quản trị (1 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Hoạt động → lọc **Dừng / bị chặn** → run **`c4b8f0d3`** | Bị chặn ở **cổng ngữ nghĩa**: "Thực thể customer chưa có định nghĩa chuẩn giữa sales và marketing". |
| Menu **Thuật ngữ** | "Khách hàng" ở Sales là tài khoản đã ký hợp đồng, ở Marketing là người để lại email → hệ thống cảnh báo trùng tên. |
| Menu **Chính sách** | Trần chi phí, số lần gọi LLM, ngưỡng tỷ lệ dòng lỗi đều đọc từ file YAML, sửa qua pull request. |

### Schema nguồn đổi (tùy chọn, 1 phút)

Trang **Hoạt động** có khung **Schema nguồn của pipeline đang chạy**. Mỗi lần mở trang, hệ thống kết nối nguồn và so với các cột pipeline đã duyệt đang đọc. Để minh họa, tạm đổi tên một cột ở Postgres demo rồi **đổi lại ngay** sau khi xem:

```bash
docker exec p045-b-postgres-1 psql -U <POSTGRES_USER> -d pipeline_demo -c "ALTER TABLE pagila.payment RENAME COLUMN amount TO amount_paid;"
# xem trang Hoạt động, dataset gold.fct_total_revenue và nguồn pagila.payment: đều hiện "Sẽ lỗi"
docker exec p045-b-postgres-1 psql -U <POSTGRES_USER> -d pipeline_demo -c "ALTER TABLE pagila.payment RENAME COLUMN amount_paid TO amount;"
```

| Nói |
|---|
| Nguồn đổi tên hoặc xóa cột thì pipeline đêm nay sẽ lỗi; hệ thống báo **trước**, chỉ đúng cột và đúng pipeline bị ảnh hưởng. Đổi kiểu cột thì báo **cảnh báo** (với pipeline duyệt sau khi có tính năng này, vì lúc duyệt hệ thống lưu kiểu từng cột làm mốc). |

**Nhớ đổi tên cột lại** trước khi làm tiếp bước 7.

---

## 7. Duyệt và chạy trên Dagster (3 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Góc trên phải: đổi sang **An · Reviewer** | Người tạo không được tự duyệt. |
| Mở run vừa tạo ở bước 4 (hoặc **`ce884a4f`**) → **Phê duyệt** | Duyệt đúng bộ file đã xem (chữ ký `files_hash`); hệ thống ghi thành project Dagster. |
| Chờ 1–2 phút (khoảng 20 giây cho mỗi pipeline đã duyệt), sang tab **Dagster** → **Jobs** → `fct_customer_orders_job` | Dagster tự nạp pipeline mới. |
| **Materialize all** → mở run | Dagster chạy dlt rồi dbt, log từng bước; khoảng 40–60 giây. |
| **Automation** | Lịch 02:00 hằng ngày, bật bằng công tắc. |
| Quay lại Atomesus → dataset gold của pipeline → panel **Vận hành · Dagster** | Catalog đọc trạng thái chạy thật từ Dagster. |

**Lưu ý:** trong 1–2 phút Dagster khởi động lại, UI Dagster tạm không vào được, đó là bình thường. Đừng bấm Phê duyệt khi đang có run Dagster chạy dở.

---

## 8. Kết (1 phút)

**Nói:**
- Đã có: catalog trên dữ liệu thật, kết nối nguồn theo yêu cầu, agent sinh pipeline có bằng chứng, người duyệt, Dagster chạy định kỳ và theo dõi.
- Điểm khác biệt: mỗi con số được tính lại bằng một đường độc lập trên nguồn và so từng dòng; hệ thống đã bắt được lỗi thật mà test không thấy.
- Tiếp theo: backfill theo partition, connector MongoDB, đối chiếu định kỳ sau mỗi lần Dagster chạy, deploy production qua pull request.

---

## Câu hỏi mentor có thể hỏi

| Câu hỏi | Trả lời ngắn |
|---|---|
| AI có bịa số không? | Không, và có bằng chứng: mỗi lần chạy thử, kết quả được tính lại bằng SQL chạy thẳng trên Postgres nguồn (không qua dlt / DuckDB / dbt) và so từng dòng; lệch thì run bị chặn. LLM chỉ viết kế hoạch và Spec, code sinh từ template. |
| Đối chiếu có bắt được lỗi thật không? | Có: nó bắt lỗi múi giờ +7 giờ trong pipeline của chính chúng tôi, khi tổng vẫn khớp và mọi dbt test đều đạt (mở `gold.fct_total_revenue` → Đối chiếu lại). |
| AI hiểu sai thì sao? | Cảnh báo dễ hiểu ở tab Kết quả, Reviewer bắt lỗi trước khi duyệt; sửa và chạy lại tạo run mới, lịch sử giữ nguyên. |
| Bảo mật? | Tài khoản Postgres chỉ đọc, mọi giao dịch read-only; credential ở server; agent không chạm production. |
| Vì sao dlt, dbt, Dagster? | Công cụ mã nguồn mở phổ biến; code sinh ra đọc được và chạy được ngoài hệ thống. |
| Dữ liệu lớn thì sao? | Sandbox giới hạn thời gian mỗi phase (300 giây); production dự kiến dùng BigQuery thay DuckDB. |

---

## Sự cố thường gặp

| Hiện tượng | Xử lý |
|---|---|
| Trang báo "Jest worker encountered…" | Tắt terminal `npm run dev` rồi chạy lại. |
| Bấm Kết nối báo lỗi | Kiểm tra Docker Desktop / container Postgres; chạy lại `demo_check.py`. |
| Tạo pipeline lỗi `INFRA_ERROR` | Hết hạn hoặc thiếu `OPENAI_API_KEY`, hoặc mạng; dùng run dự phòng `ce884a4f`. |
| Dagster không thấy pipeline vừa duyệt | Đợi 1–2 phút; nếu vẫn không thấy, kiểm tra terminal `dagster_local.py` có dòng "workspace.yaml đổi". |
| Màn hình đăng nhập hiện ra giữa chừng | Phiên 8 giờ đã hết; chọn lại người dùng. |
