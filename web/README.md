# Pipeline Studio · frontend demo

Workspace Next.js App Router + TypeScript cho Engineer và Reviewer. UI dùng Public Sans, JetBrains Mono, Lucide và Monaco DiffEditor có sẵn trong repo. Bố cục editor ba cột lấy cảm hứng từ Databricks Genie Code: cây file, diff, bảng kết quả và trợ lý theo ngữ cảnh. Đây là giao diện riêng của Pipeline Studio, không dùng thương hiệu Databricks. Không kết nối LLM, warehouse, Dagster hoặc API production.

## Chạy và kiểm tra

```powershell
cd web
npm ci
npm run dev
npm run typecheck
npm test
npm run build
```

Mở `http://localhost:3000`. Màn đầu tiên là workspace của `RUN-024`: chọn file trong cây trái, xem diff ở giữa, mở Bảng dữ liệu/RunReport/Log/Kế hoạch ở dưới và hỏi trợ lý mô phỏng bên phải. Nút **Run** ở thanh trái mở danh sách run. Repo chưa có script lint; typecheck, test và build là các kiểm tra hiện có.

## Thử kịch bản

1. Ở vai trò Engineer, chọn **Tạo yêu cầu** và một ví dụ trong wizard. Kiểm tra kế hoạch rồi xác nhận.
2. **Doanh thu ngày** đi qua 15 stage, sáu check, đến **Chờ duyệt**. Đổi sang Reviewer để xem diff và phê duyệt. Nút xuất artifact chỉ mô phỏng ghi vào code location.
3. **Xung đột customer** mở màn chặn ngữ nghĩa; **Vượt ngân sách** có hành động rút gọn và chạy lại trên version mới.
4. **Sai tên cột** ghi lỗi SQL, tạo version mới rồi chạy lại. **SQL bị chặn** hiển thị `BLOCKED_AST` và error trace trong Log. **Sai doanh thu** làm `metric_reconcile` fail và khóa review.
5. Reviewer chọn **Yêu cầu sửa** kèm nhận xét để tạo version mới. **Hủy run** dừng timer; **Chạy lại** tạo version tiếp theo.

Danh sách run có tìm kiếm và bộ lọc. Chi tiết run có timeline, bằng chứng, đặc tả, bốn file diff chỉ đọc, RunReport checks và log. Trợ lý bên phải chỉ đưa câu trả lời mô phỏng và tham chiếu ngữ cảnh. Chính sách YAML chỉ đọc.

## Quyết định và giới hạn

- Mock service trong `lib/studio-api.ts` phát sự kiện dạng SSE, có hủy timer và `PipelineApi` để thay bằng API thật. State nghiệp vụ nằm trong `lib/studio.ts` và trang điều phối `app/studio-page.tsx`.
- Có 15 stage hiển thị. Tại thời điểm triển khai, `SPEC.md` và `UI-SPEC.md` không còn ở vị trí cũ hoặc trong repo; tên stage trong `lib/studio.ts` là nhãn tạm cần đối chiếu với SPEC gốc. Không dùng mapping P0–P17 cũ.
- Giới hạn LLM mặc định 8/run, retry spec 2, SQL 3 lấy từ cấu hình demo `LIMITS` và `budgets.yml`. RunReport giữ mọi check, kể cả `metric_reconcile` ngoài dải 5 ô.
- Mã nguồn và diff chỉ đọc. Sửa thông qua kế hoạch hoặc “Yêu cầu sửa”. Bằng chứng, hash, chi phí và thời gian đều minh họa; chi phí được gắn nhãn ước tính. Không coi các số đo này là benchmark thật.
- Vai trò và hai danh tính là mô phỏng, không phải xác thực. Approval buộc đúng version/hash và người duyệt khác người tạo. Version mới làm mất hiệu lực approval và bằng chứng cũ.
- “Xuất artifact” chỉ đổi trạng thái demo và đường dẫn code location; không khẳng định Dagster đã chạy. Các phiếu steward và nâng trần cũng chỉ là mô phỏng.
- Run được lưu trong `localStorage` dưới key `pipeline-studio-demo-v1`; không lưu secret. Monaco có thể tải runtime qua CDN lần đầu.
