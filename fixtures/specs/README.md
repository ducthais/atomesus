# Spec mẫu (Sprint 0 - Role C)

Bản thiết kế pipeline dạng JSON theo SPEC §8.1 (bản `SPEC_1.md` ở commit `6c32c2f`), model ở `src/spec/models.py`, JSON Schema sinh tự động ở `src/spec/schemas/spec.schema.json`.

| File | Nguồn | Dùng để |
|---|---|---|
| `spec_01_pg_daily_orders.json` | `sales_pg.orders` (Postgres của B) | Spec chạy được ngay: dùng đúng 5 cột thật của `sales_raw.orders` (`order_id`, `customer_id`, `amount_text`, `ordered_at_text`, `status`). Bảng không có cột cursor nên `write_disposition = replace`. |
| `spec_02_sales_multi_source.json` | `sales_pg.orders` + `sales_api.payments` | Ví dụ đa nguồn đúng như §8.1: merge theo cursor, đổi tên, ép kiểu, đa định dạng ngày, quy đổi tiền tệ, join ở gold. Các cột `created_at`, `channel`, `currency`, `updated_at` **chưa có** trong seed của B. |

## Khối làm sạch (`sources[].staging`)

- `columns[]`: `from` (cột thô, cho phép camelCase như `orderId`) → `as` (tên đầu ra snake_case) với `type`; ép kiểu bằng `TRY_CAST`, dòng lỗi vào bảng rejected.
- `date_formats`: chỉ cho cột `date`/`timestamp`, thử lần lượt.
- `currency`: `code_column` ở nguồn, `to` phải là tiền gốc của domain; tỷ giá đọc từ `policy/domains.yml`.
- `dedupe`: `keys` (mặc định `primary_key`), `keep` ∈ `{latest, first}`, `order_by`.
- `reject_if`: điều kiện trên cột đầu ra; dòng thỏa bị tách sang rejected kèm lý do.

## Kiểm tra

```bash
.venv/Scripts/python.exe -m pytest tests/test_spec tests/test_policy -q
```

- `Spec.model_validate(...)`: ràng buộc suy ra được từ chính Spec (merge cần `primary_key`, load tăng dần cần `cursor_field`, mọi nguồn khác `from` join đúng một lần, `transform` chỉ dùng cột đầu ra staging, `unique_key`/`partition_by`/`tests` là cột của gold, …).
- `policy_violations(spec, load_policy())`: đối chiếu `policy/*.yml` (domain, `source_ref` thuộc domain, `kind` khớp, `rules.max_tables_joined`, tiền gốc).
- Chưa kiểm ở đây: cột có thật ở nguồn và kiểu khóa join sau staging (cổng `schema_real`), cú pháp SQL (cổng `ast_guard`).

Sửa `src/spec/models.py` xong thì chạy lại `.venv/Scripts/python.exe scripts/export_spec_schema.py`; test sẽ fail nếu JSON Schema lệch model.
