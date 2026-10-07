# Kịch bản demo MVP Atomesus (khoảng 20 phút)

**Thông điệp chính:** người làm dữ liệu mô tả nhu cầu bằng lời, agent sinh pipeline ELT thật (dlt → dbt → Dagster) trên dữ liệu thật, kèm bằng chứng đo được; con người duyệt rồi mới chạy định kỳ. Mọi thứ được tổ chức theo nguyên tắc data catalog: dataset là trung tâm, pipeline là hoạt động trên dataset.

Các run và con số trong kịch bản là dữ liệu thật trong hệ thống tại thời điểm viết; `scripts/demo_check.py` kiểm tra chúng còn đúng.

---

## Chuẩn bị (30 phút trước)

1. Mở **Docker Desktop**, rồi 5 terminal tại `D:\Repo\P-045`:
   ```bash
   docker compose --env-file .env -p p045-b -f infra/postgres/compose.yml up -d --wait
   .venv\Scripts\uvicorn.exe src.main:app --port 8000
   .venv\Scripts\uvicorn.exe infra.mock_api.app:app --port 8100    # nguồn REST API (sales_api, marketing_api)
   .venv\Scripts\python.exe scripts\dagster_local.py
   cd web && npm run dev
   ```
2. Chạy kiểm tra, phải ra **"Sẵn sàng demo."**:
   ```bash
   .venv\Scripts\python.exe scripts\demo_check.py
   ```
3. Mở sẵn 2 tab trình duyệt:
   - http://localhost:3000, đăng nhập `engineer` / `Engineer@123` (**Minh · Engineer**)
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
| Nhập: **"Tổng doanh thu và số đơn hàng của từng khách hàng, kèm tên khách hàng"** → **Gửi cho agent** | Trang run mở ngay, agent lập kế hoạch (vài giây) rồi **dừng chờ xác nhận**. Agent chạy nền, rời trang cũng không sao. |
| Trang run mở ở tab **Kế hoạch** | Agent định đọc bảng nào, ghép thế nào, gộp theo gì, tính chỉ số gì. Chưa xác nhận thì chưa viết Spec, chưa đọc dữ liệu nguồn. Xác nhận chỉ qua nút, chat không xác nhận thay. |
| Để nguyên ô **Xem & chỉnh mapping trước khi chạy thử** đã tích → **Xác nhận kế hoạch** | Khung **Agent đang chạy** hiện từng bước (khoảng 10 giây): đọc schema + 20 dòng mẫu thật, viết Spec, rồi **dừng lại ở bản nháp**. |
| Trang run mở ở tab **Mapping** | Engineer thấy agent định lấy cột nào, đặt tên gì, ép kiểu gì, loại dòng nào. Phần "Bảng kết quả sẽ tính" bên dưới cho biết gộp theo gì, tính chỉ số gì. Chưa có code nào chạy. |
| Bấm **Chạy thử với mapping này** | Bây giờ mới sinh code dlt/dbt/Dagster **từ template** (không phải LLM viết tự do) rồi chạy thật trong sandbox: dlt tải dữ liệu thật, dbt build, khoảng 15–20 giây; trang hiện tiến độ và tự chuyển khi xong. |
| Trang tự chuyển sang tab **Kết quả** | Diễn giải bằng lời: đọc bảng nào, làm sạch gì, mỗi dòng kết quả là gì; bên dưới là **dữ liệu kết quả thật**. |

**Nếu bước này lỗi hoặc quá lâu:** nói "LLM bên ngoài đôi khi chậm" rồi mở run có sẵn **`ce884a4f`** (cùng yêu cầu, đã chạy xong) từ trang **Hoạt động**.

---

## 4b. Ba loại nguồn trong một pipeline, sửa kế hoạch trước khi chạy (3 phút)

Postgres, REST API và file CSV được ghép trong cùng một pipeline. Engineer sửa kế hoạch ngay trên thẻ xác nhận.

| Thao tác | Nói / chỉ vào |
|---|---|
| **Yêu cầu pipeline** → tích `sales_pg` (**orders**, **customers**), `sales_api` (**payments**), `landing_sales` (**refunds**) | Ba loại nguồn: Postgres đọc trực tiếp, API đọc theo OpenAPI, CSV đọc từ thư mục landing. |
| Nhập: **"Theo từng khách hàng: tổng tiền đơn hàng, tổng đã thanh toán và tổng đã hoàn"** → bỏ tích xem mapping → **Gửi cho agent** | Kế hoạch có 4 bảng và 3 phép ghép: payments và refunds ghép LEFT theo `order_id`. |
| Trên thẻ kế hoạch: **Gộp theo** thêm `customers.customer_name`, đổi **Bảng kết quả** thành `fct_customer_money_flow`, rồi **Xác nhận kế hoạch** | Engineer sửa trực tiếp; hệ thống kiểm lại kế hoạch đã sửa bằng chính các luật của planner. Sổ bằng chứng ghi sự kiện "Engineer sửa kế hoạch" riêng. |
| Khoảng 1 phút sau → tab **Bằng chứng** | **18/18 kiểm tra đạt**: số dòng và tập khóa nguồn ↔ bronze cho cả 4 bảng, nạp lần hai không đổi dữ liệu, dbt test, và **đối chiếu độc lập**. Với API và CSV, dữ liệu nguồn được đưa thẳng vào câu SQL đối chiếu. |
| Tab **Tổng quan** → ô **Gọi LLM** | Tiền LLM thật của run (khoảng $0,006), tính theo bảng giá trong `policy/budgets.yml`. |

Run mẫu đã chạy sẵn: **`0dbc25b0`**. Nếu gửi lịch `0 3 * * *` trên thẻ, P5 sẽ đổi sang hằng giờ vì SLA độ tươi của domain Sales; lần đổi đó được ghi trong lịch sử Spec.

**Hủy run:** ở một run đang chờ xác nhận (ví dụ **`a1cb5645`**), bấm **Hủy run**. Bằng chứng đã có vẫn giữ nguyên, và run không xác nhận được nữa.

---

## ★ Điểm nhấn: đối chiếu độc lập với nguồn (2 phút)

Đây là phần làm Atomesus khác các công cụ "AI viết SQL": kết quả của pipeline được **tính lại bằng một đường độc lập và so từng dòng**, nên các lỗi thực thi mà test thông thường cho qua (múi giờ, ép kiểu, mất dòng) bị bắt trước khi duyệt.

**Giới hạn, nói ngay từ đầu:** đối chiếu kiểm tra **pipeline thực hiện đúng Spec**. Spec có đúng nhu cầu nghiệp vụ hay không thì **Engineer xác nhận** (tab Mapping, trước khi chạy thử) và Reviewer xem lại trước khi duyệt. Không nói "hệ thống đảm bảo đúng business logic".

| Thao tác | Nói / chỉ vào |
|---|---|
| Ở run vừa tạo (hoặc **`98506251`**, doanh thu theo tháng), tab **Kết quả**: khung xanh trên cùng | "Sau khi chạy thử, hệ thống dịch Spec thành **một câu SQL chạy thẳng trên Postgres nguồn**, không đi qua dlt, DuckDB, dbt hay template, rồi so **từng dòng**. 6/6 dòng khớp tới từng xu: pipeline đã làm đúng những gì Spec mô tả. Còn Spec có đúng ý người dùng không thì Engineer đã xác nhận ở tab Mapping." |
| Mở **Truy vấn đối chiếu trên Postgres** | "Câu SQL này ai cũng tự chạy lại được trên nguồn để kiểm tra." |
| Khám phá → **`gold.fct_total_revenue`** (pipeline đang chạy trên Dagster) → **Đối chiếu lại** | Khung đỏ: **tổng vẫn khớp 227.768,87**, nhưng 16.871 dòng lệch. Hệ thống **tự chẩn đoán: mọi thời điểm lệch đúng +7 giờ**, tức lỗi múi giờ. Hai cột cạnh nhau: nguồn 18:21 ngày 24/01, pipeline 01:21 ngày 25/01. |
| Câu chốt | "Lỗi này có thật, nằm trong pipeline của chính chúng tôi. Mọi dbt test đều đạt, tổng doanh thu cũng khớp. Chỉ có phép đối chiếu từng dòng bắt được nó. Bây giờ mỗi lần chạy thử đều đối chiếu như vậy, và lệch thì run bị chặn, không tới được tay Reviewer." |

**Nếu được hỏi "vậy là kết quả chắc chắn đúng?":** không hứa vậy. Đối chiếu chỉ kiểm tra pipeline **thực hiện đúng Spec**; nếu Spec hiểu sai yêu cầu (ví dụ lọc nhầm trạng thái đơn), hai đường tính sẽ cùng sai và vẫn khớp nhau. Phần đó do Engineer xác nhận Spec, cộng một số cổng hỗ trợ như "Gộp đúng kỳ yêu cầu" và cảnh báo ở tab Kết quả (bước 5), và Reviewer xem lại trước khi duyệt.

### Bắt lỗi trước khi duyệt: test đạt nhưng đối chiếu lệch (3 phút, tùy chọn)

Tái hiện đúng lỗi múi giờ đã gặp, lần này **ở bước chạy thử, trước khi tới Reviewer**.

| Thao tác | Nói / chỉ vào |
|---|---|
| Trước khi demo: thêm dòng `PIPELINE_WAREHOUSE_TIMEZONE=Asia/Ho_Chi_Minh` vào `.env` (backend đọc lại mỗi request, không cần khởi động lại) | "Giả sử warehouse được cấu hình theo giờ máy, như lúc đầu dự án." |
| Gửi yêu cầu **"Tính doanh thu và số đơn hàng theo từng ngày"** trên `pagila_pg.payment` (bỏ tích xem mapping) | Run chạy khoảng 30 giây. |
| Run dừng ở **Cần kỹ sư** → tab **Bằng chứng** | dbt test **5/5 đạt**, đối soát số dòng **32.093 → 32.093 đạt**, làm sạch **đạt**. Chỉ **Đối chiếu độc lập** không đạt. Test thông thường không bắt được lỗi này. |
| Tab **Kết quả** | **Tổng vẫn khớp 227.768,87** nhưng 81 giá trị lệch, 7 ngày thiếu, 6 ngày thừa. Chẩn đoán: chạy thử dùng múi giờ `Asia/Ho_Chi_Minh` trong khi nguồn dùng UTC. Run không tới tay Reviewer. |
| Engineer sửa: xóa dòng vừa thêm trong `.env` (về UTC) → **Sửa & chạy lại** → Gửi | Run mới khớp **50/50 dòng**, chuyển sang Chờ duyệt. |
| Tab **So sánh** của run mới | So với run bị chặn: "Cấu hình chạy thử · múi giờ warehouse: Asia/Ho_Chi_Minh → UTC", số dòng 49 → 50. |

Run mẫu đã chạy sẵn: bị chặn **`1072d9dc`**, đã sửa **`56329f23`**. **Nhớ xóa dòng trong `.env`** sau phần này.

### Giảm việc lặp lại: thời gian làm tay so với agent (1 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Mở run đã đi hết luồng (gửi → chỉnh mapping → chạy thử → duyệt), tab **Tổng quan** → khung **Thời gian & công sức** | Hệ thống tự ghi: máy chạy bao lâu, người chỉnh mapping và duyệt bao lâu, agent sinh bao nhiêu dòng code (ví dụ run **`dee0dc49`**: máy 27 giây, engineer chỉnh mapping 35 giây, 187 dòng code trong 5 file). |
| So với số làm tay | Đọc bảng trong `docs/time-study.md`: cùng yêu cầu, một engineer làm tay có bấm giờ. **Chỉ nói số đã đo thật.** |

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
| Đăng xuất rồi đăng nhập `reviewer` / `Reviewer@123` (**An · Reviewer**) | Người tạo không được tự duyệt. |
| Mở run vừa tạo ở bước 4 (hoặc **`ce884a4f`**) → **Phê duyệt** | Duyệt đúng bộ file đã xem (chữ ký `files_hash`); hệ thống ghi thành project Dagster. |
| Chờ 1–2 phút (khoảng 20 giây cho mỗi pipeline đã duyệt), sang tab **Dagster** → **Jobs** → `fct_customer_orders_job` | Dagster tự nạp pipeline mới. |
| **Materialize all** → mở run | Dagster chạy dlt rồi dbt, log từng bước; khoảng 40–60 giây. |
| **Automation** | Lịch 02:00 hằng ngày, bật bằng công tắc. |
| Quay lại Atomesus → dataset gold của pipeline → panel **Vận hành · Dagster** | Catalog đọc trạng thái chạy thật từ Dagster. |

**Lưu ý:** trong 1–2 phút Dagster khởi động lại, UI Dagster tạm không vào được, đó là bình thường. Đừng bấm Phê duyệt khi đang có run Dagster chạy dở.

### Yêu cầu sửa và duyệt nhiều run (2 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| (Reviewer) mở run **`0dbc25b0`** → tab **Tổng quan** | Run này đã ở **v2**: Reviewer từng bấm **Yêu cầu sửa** kèm nhận xét; agent viết lại Spec từ nhận xét đó, chạy thử lại, và bản v1 vẫn nằm trong sổ bằng chứng. Tiền LLM của v1 vẫn được tính vào run. |
| Trang **Hoạt động** → **Duyệt nhiều run** | Mọi run đang chờ duyệt, kèm số kiểm tra đạt và chi phí. Chỉ tích được run của người khác **và** đã đạt mọi kiểm tra; run không chọn được ghi rõ lý do. |
| Tích 2 run → **Duyệt** (hoặc nhập ghi chú → **Từ chối**) | Mỗi run vẫn được backend kiểm lại đúng bộ file (`files_hash`) như khi duyệt từng run; kết quả hiện riêng cho từng run. |

**Tránh khi demo:** nhận xét "Yêu cầu sửa" kiểu **thêm chỉ số mới** (ví dụ "thêm số đơn hàng"). Hiện agent giữ đúng kế hoạch cũ nên bản mới chưa có chỉ số đó (đã thử với `0dbc25b0`). Nên dùng nhận xét sửa trong phạm vi kế hoạch, ví dụ đổi tên cột.

---

## 7b. Theo dõi pipeline đang chạy · Day 2 (2 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Menu **Theo dõi** | Mọi pipeline đã deploy: lần chạy gần nhất trên Dagster, thời gian so với mốc của chính nó, số dòng gold, test fail, số bản ghi mới ở nguồn. |
| Bấm **Thu số đo** | Đọc các lần chạy theo lịch vừa xong từ Dagster, dbt và nguồn. Chỉ đọc, không thay đổi gì. |
| (Engineer) bấm **Kiểm tra suy giảm** | Chạy các luật phát hiện: pipeline chậm hơn mốc, test fail, nguồn ngừng cấp dữ liệu. Mỗi cảnh báo kèm bằng chứng và gửi tới owner của domain. |
| Cảnh báo → **Đã xử lý** | Đóng cảnh báo khi đã xem. |

---

## Đo chất lượng agent: số đã chạy thật (1 phút)

| Thao tác | Nói / chỉ vào |
|---|---|
| Mở `eval/results/golden_latest.md` | 6 ca có kết quả đúng tính tay từ dữ liệu seed (`eval/golden/`). `python eval/run_eval.py` chạy thật từng ca bằng LLM thật, rồi so bảng gold với kết quả đúng **từng dòng**. Tên cột do LLM đặt nên ghép cột theo giá trị. |
| Đọc số | **Khớp 5/6 ca**, khớp ngay lần viết Spec đầu 4/6. Theo loại nguồn: Postgres 1/1, API 1/1, CSV 1/1, đa nguồn 2/3. Tiền LLM cả lượt khoảng $0,016. |
| Ca không đạt | Postgres + CSV: agent đặt khóa `customers.customer_id` (có tên bảng), cổng Spec chặn, sau 2 lần sửa vẫn sai nên dừng ở **Cần kỹ sư**. Run sai dừng lại chứ không tới tay Reviewer. |

**Luôn nói kèm mẫu số:** 6 ca là ít. Ca vi phạm chính sách và ca yêu cầu mơ hồ (`eval/cases/`) đang được viết thêm.

---

## 8. Kết (1 phút)

**Nói:**
- Đã có: catalog trên dữ liệu thật; nguồn Postgres, REST API và CSV; agent sinh pipeline kèm sổ bằng chứng (mọi phép đo của lần chạy thử); Engineer xác nhận và sửa kế hoạch; Reviewer duyệt, từ chối hoặc yêu cầu sửa, từng run hoặc nhiều run; Dagster chạy định kỳ; màn theo dõi phát hiện suy giảm.
- Điểm khác biệt: kết quả được tính lại bằng một đường độc lập trên nguồn và so từng dòng, nên lỗi thực thi bị chặn trước khi duyệt; hệ thống đã bắt được lỗi thật mà test không thấy. Chất lượng agent đo bằng ca có kết quả đúng (hiện 5/6). Spec có đúng nhu cầu hay không vẫn do Engineer xác nhận và Reviewer duyệt.
- Tiếp theo: nút Tối ưu, ca eval vi phạm / mơ hồ, deploy production qua pull request (hiện duyệt xong ghi thẳng ra Dagster local), backfill theo partition, connector MongoDB.

---

## Câu hỏi mentor có thể hỏi

| Câu hỏi | Trả lời ngắn |
|---|---|
| AI có bịa số không? | LLM không tạo ra con số: nó chỉ viết kế hoạch và Spec, code sinh từ template, số liệu là phép đo khi chạy thật. Mỗi lần chạy thử, kết quả còn được tính lại bằng SQL chạy thẳng trên Postgres nguồn và so từng dòng; lệch thì run bị chặn. Điều này kiểm tra pipeline **làm đúng Spec**; Spec có đúng nhu cầu không do Engineer xác nhận và Reviewer duyệt. |
| Vậy hệ thống có đảm bảo đúng business logic không? | Không hứa điều đó. Hệ thống kiểm tra được thực thi (pipeline ↔ Spec) và chặn một số lỗi hiểu ý phổ biến (gộp sai kỳ, bảng rỗng), nhưng nghiệp vụ đúng hay sai là quyết định của Engineer khi xác nhận Spec và của Reviewer khi duyệt. |
| Đối chiếu có bắt được lỗi thật không? | Có: nó bắt lỗi múi giờ +7 giờ trong pipeline của chính chúng tôi, khi tổng vẫn khớp và mọi dbt test đều đạt (mở `gold.fct_total_revenue` → Đối chiếu lại). |
| AI hiểu sai thì sao? | Cảnh báo dễ hiểu ở tab Kết quả, Reviewer bắt lỗi trước khi duyệt; sửa và chạy lại tạo run mới, lịch sử giữ nguyên. |
| Bảo mật? | Tài khoản Postgres chỉ đọc, mọi giao dịch read-only; credential ở server; agent không chạm production. |
| Vì sao dlt, dbt, Dagster? | Công cụ mã nguồn mở phổ biến; code sinh ra đọc được và chạy được ngoài hệ thống. |
| Agent đúng được bao nhiêu? | Trên 6 ca có kết quả đúng tính tay: khớp từng dòng 5/6 (đa nguồn 2/3). Số đo bằng `eval/run_eval.py`, chạy lại được. Ca sai bị cổng chặn ở **Cần kỹ sư**, không tới Reviewer. |
| Chi phí mỗi pipeline? | Mỗi run ghi tiền LLM thật (thường dưới $0,01) và chi phí quét dữ liệu ước tính; trần chi phí và số lần gọi LLM đọc từ `policy/budgets.yml`. |
| Dữ liệu lớn thì sao? | Sandbox giới hạn thời gian mỗi phase (300 giây); production dự kiến dùng BigQuery thay DuckDB. |

---

## Sự cố thường gặp

| Hiện tượng | Xử lý |
|---|---|
| Trang báo "Jest worker encountered…" | Tắt terminal `npm run dev` rồi chạy lại. |
| Run có nguồn `sales_api` lỗi `INFRA_ERROR` | Mock API chưa chạy: `.venv\Scripts\uvicorn.exe infra.mock_api.app:app --port 8100`. |
| Bấm Kết nối báo lỗi | Kiểm tra Docker Desktop / container Postgres; chạy lại `demo_check.py`. |
| Kết nối `pagila_pg` báo "Schema 'pagila' chưa có" | Postgres tạo từ volume cũ: chạy `python scripts/load_pagila.py` rồi Kết nối lại. |
| Tạo pipeline lỗi `INFRA_ERROR` | Hết hạn hoặc thiếu `OPENAI_API_KEY`, hoặc mạng; dùng run dự phòng `ce884a4f`. |
| Dagster không thấy pipeline vừa duyệt | Đợi 1–2 phút; nếu vẫn không thấy, kiểm tra terminal `dagster_local.py` có dòng "workspace.yaml đổi". |
| Màn hình đăng nhập hiện ra giữa chừng | Phiên 8 giờ đã hết; đăng nhập lại. |
