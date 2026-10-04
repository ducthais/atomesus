# Sprint 1-C: Lõi AI — yêu cầu → kế hoạch → Spec → sinh code → chạy thử

## Luồng

`POST /api/v1/agent/runs` với `{"request": "<yêu cầu bằng lời>"}` (Engineer, cần đăng nhập) chạy đồng bộ graph 4 node rồi ghi `RunReport` và nội dung file sinh ra vào sổ bằng chứng.

| Node | Phase | Loại | Làm gì | Code |
|---|---|---|---|---|
| `planner` | P1 | LLM rẻ | Yêu cầu + danh mục nguồn đọc được (bảng, cột thật) → kế hoạch JSON | `src/agents/nodes/planner.py`, `prompts/planner.md` |
| `codegen` | P3 + P4 | LLM mạnh | Đọc schema thật + 20 dòng mẫu từng nguồn, sinh Spec, kiểm tra ngay | `src/agents/nodes/codegen.py`, `prompts/codegen.md`, `src/agents/validate.py` |
| `render` | P6 | Tất định | Spec → 4 + N file bằng `templates/` | `src/codegen/render.py` |
| `test_run` | P9–P12 | Tất định | Chạy dlt → bronze, `dbt build`, đối soát trong sandbox DuckDB | `src/codegen/sandbox.py` |

Graph: `src/agents/graph.py`. Mọi trạng thái kết thúc đi thẳng tới END. Chưa có P2 (người xác nhận kế hoạch, Sprint 2) và P13 (optimizer, Sprint 4).

### Kiểm tra trước khi render (`src/agents/validate.py`)

| Cổng | Kiểm tra | Sai thì |
|---|---|---|
| `spec_schema` | `Spec.model_validate` + `policy_violations` | quay lại `codegen` kèm lỗi cụ thể |
| `schema_real` | mọi cột Spec dùng có trong schema thật; cột chuỗi → ngày phải có `date_formats`; khóa join cùng kiểu | quay lại `codegen` |
| `ast_guard` (tạm) | biểu thức không có `{ }`, `;`, comment, từ khóa ghi/sửa, hàm ngoài danh sách cho phép (`src/codegen/guard.py`) | `BLOCKED_AST`, dừng |

Vòng spec tối đa `budgets.yml: per_run.max_spec_attempts` (2) lần sửa; tổng số lần gọi LLM tối đa `max_llm_calls` (6). Chạm trần → `NEEDS_HUMAN` kèm `error_trace`.

## Template (`templates/`)

Delimiter của template là `[[ ]]`, `[% %]`, `[# #]` để `{{ ref() }}`, `{% if is_incremental() %}` của dbt đi thẳng ra file sinh, không phải escape.

| Template | File sinh ra |
|---|---|
| `dlt_pipeline.py.j2` + `sources/_postgres.j2`, `_rest_api.j2`, `_filesystem.j2` | `ingestion/{model}_pipeline.py` — một resource dlt mỗi nguồn, đích DuckDB `bronze` |
| `dbt_staging.sql.j2` | `dbt_project/models/staging/stg_{model}__{id}.sql` — mỗi nguồn một file |
| `dbt_model.sql.j2` | `dbt_project/models/gold/{model}.sql` |
| `dbt_schema.yml.j2` | `dbt_project/models/gold/{model}.yml` — nguồn bronze, test staging và gold |
| `dagster_asset.py.j2` | `infra/dagster/generated/{model}_asset.py` — `@dlt_assets`, `@dbt_assets`, job, `ScheduleDefinition` |

Khung dbt dùng chung ở `dbt_project/` (profile DuckDB theo `WAREHOUSE_PATH`, test `not_negative`, tên schema `staging`/`gold`).

Staging: mỗi dòng bronze đi đúng một đường — staging sạch, loại vì trùng (`dedupe`, đếm), hoặc `stg_{model}__{id}__rejected` kèm `_reject_reason` (`cast:`, `date_format:`, `unknown_currency:`, `reject_if:`) và `_raw_values` (JSON giá trị thô). Tỷ giá đọc từ `policy/domains.yml`.

Đã kiểm chứng: render đúng cú pháp cho cả 2 Spec mẫu; dbt chạy thật trên bronze bẩn có chủ đích (`tests/test_codegen/test_staging_dbt.py`); dlt + dbt chạy thật trên Postgres seed; file Dagster nạp được bằng Dagster thật với đúng lineage và lịch (`tests/test_codegen/test_dagster_asset.py`). Macro `rest_api` và `filesystem` mới kiểm cú pháp — chạy thật khi có mock API và thư mục file (Sprint 2/4).

## Sandbox (`src/codegen/sandbox.py`)

Thư mục tạm + file DuckDB riêng mỗi run, xóa khi xong. Tiến trình con (dlt, dbt) chỉ nhận biến môi trường cần thiết, không nhận khóa LLM. Thử kết nối từng nguồn trước khi chạy dlt; không kết nối được → `INFRA_ERROR` kèm `source_id`. Mỗi phase giới hạn `budgets.yml: per_run.phase_timeout_sec`.

Bằng chứng ghi vào RunReport: `row_reconcile` và `staging_reconcile` từng nguồn (ngưỡng rác `objectives.yml: quality.max_reject_pct`), `dbt_test`, gate `post_run` (20 dòng gold mẫu; gold rỗng khi staging có dữ liệu → không đạt). Nội dung đủ các file: `GET /api/v1/runs/{id}/files`.

## Chạy local

```bash
python -m pip install -r requirements.txt              # gồm cả dlt, dbt-duckdb (ghim dbt-core<1.11)
python -m pip install -r requirements/dagster.txt      # tùy chọn: chỉ để nạp/kiểm file Dagster
```

Cần Postgres của B đang chạy (`docs/task-b.md`) và `OPENAI_API_KEY` trong `.env`. `MODEL_NAME` là model rẻ (mặc định `gpt-4o-mini`); `MODEL_NAME_STRONG` là model sinh Spec, để trống thì dùng `MODEL_NAME`.

```bash
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
# đăng nhập Engineer rồi gửi yêu cầu (body UTF-8)
curl -c jar -X POST localhost:8000/api/v1/pipeline/session -H 'Content-Type: application/json' -d '{"username":"engineer","password":"Engineer@123"}'
curl -b jar -X POST localhost:8000/api/v1/agent/runs -H 'Content-Type: application/json' --data-binary @request.json
```

## Kiểm thử

```bash
pytest tests pipeline_tests -q                 # test dbt/Dagster tự bỏ qua nếu chưa cài
PIPELINE_TEST_DATABASE_URL=... pytest pipeline_tests/test_sandbox_integration.py
```

## Giới hạn hiện tại

- Chạy đồng bộ (~25–30 giây/run), chưa có SSE và hủy run.
- Chỉ chạy thử được nguồn `postgres`; `rest_api`/`filesystem` dừng ở `NEEDS_HUMAN` với lý do rõ.
- Lỗi sau khi render (dbt lỗi, test fail, gold rỗng) dừng ở `NEEDS_HUMAN`; vòng tự sửa SQL/Spec sau chạy thử là Sprint 3.
- `ast_guard` là bản tạm bằng regex; bản sqlglot thuộc Sprint 2-B.
- Với `gpt-4o-mini` cho cả hai bậc, LLM đôi khi viết lọc sai nghĩa (ví dụ `status <> 'paid'`); cổng `post_run` chặn trường hợp làm gold rỗng, đối soát tổng chỉ số (`metric_reconcile`) ở Sprint 3.
