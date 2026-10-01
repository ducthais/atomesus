# RunReport Fixtures (Mock Data cho Sprint 0 - Role D)

Thư mục này chứa các `RunReport` mẫu tuân thủ hợp đồng dữ liệu chuẩn tại `SPEC_1.md §8.2`. 
API `GET /api/v1/runs` **không** đọc trực tiếp các file này mà đọc sổ bằng chứng SQLite (`src/run_report/store.py`). Các file dùng làm dữ liệu test và có thể nạp vào SQLite để demo UI bằng `python scripts/seed_run_reports.py`.

---

### Danh sách các trạng thái mẫu

| File | Trạng thái (`status`) | Mục đích thử nghiệm cho UI / Reviewer |
|---|---|---|
| `ready_for_review.json` | `READY_FOR_REVIEW` | Pipeline đã chạy thử xong trong sandbox, đang chờ reviewer duyệt. Có kèm `reject_samples` (mẫu dòng rác bị loại) để hiển thị trên màn hình Staging. |
| `deployed.json` | `DEPLOYED` | Pipeline đã được phê duyệt hợp lệ (có `approval` đầy đủ thông tin: `reviewer`, `files_hash`, `version`, `at`) và đã triển khai vào Dagster. |
| `blocked_semantic.json` | `BLOCKED_SEMANTIC_CONFLICT` | Pipeline bị cổng Semantic chặn lại an toàn do xung đột từ ngữ kinh doanh (không cho phép sinh code/chạy thử). |

---

### Bất biến nghiệp vụ bắt buộc (Business Invariants)
1. **Chống tự duyệt**: `approval.reviewer != created_by`.
2. **Hiệu lực chữ ký**: `approval.files_hash == files_hash` và `approval.version == version`.
3. **Trạng thái DEPLOYED**: Bắt buộc phải có `approval.decision == 'approved'`.
