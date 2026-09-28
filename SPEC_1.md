# SPEC.md — Control Plane cho việc sinh pipeline ELT

Đặc tả **hệ thống đích**. Đọc `CLAUDE.md` trước để nắm bảy luật cứng.

## 0. Ba tài liệu và quy tắc ưu tiên

| Tài liệu | Trả lời câu hỏi | Không chứa |
|---|---|---|
| `SPEC.md` (file này) | Hệ thống đích trông như thế nào, hành vi ra sao | Lộ trình, cắt giảm, số ngày |
| `SCOPE.md` | MVP làm gì, bỏ gì, nghiệm thu ra sao | Chi tiết thiết kế |
| `UI-SPEC.md` | Giao diện thể hiện hệ thống ra sao | Quyết định phạm vi |

**Khi mâu thuẫn:** `SCOPE.md` thắng về *phạm vi* (làm hay không, ở mức nào). `SPEC.md` thắng về *hành vi* (đã làm thì phải chạy thế nào). `UI-SPEC.md` không bao giờ quyết định phạm vi backend — bỏ một ô hiển thị không có nghĩa là bỏ phép kiểm tra.

### 0.1 Tuyên bố governance

Trong MVP, **deploy nghĩa là ghi vào code location của Dagster ở môi trường sandbox**. Thao tác phê duyệt trên web là *bản ghi quyết định*, không thay thế quy trình review và merge lên production. Luồng pull request và merge nằm trong thiết kế đích (mục 9.5, mức D2), không nằm trong MVP.

Nêu câu này trong báo cáo và khi bảo vệ, trước khi hội đồng hỏi.

---

## 1. Mục tiêu và định vị

### 1.1 Câu định vị

> **Control plane cho vòng đời pipeline: sinh ra đúng, và giữ cho đúng.**

Sản phẩm là lớp điều khiển đứng giữa yêu cầu dữ liệu và kho dữ liệu, hoạt động ở hai giai đoạn:

- **Day 1 — tạo mới.** Nhận yêu cầu ngôn ngữ tự nhiên, sinh pipeline, buộc mọi đề xuất đi kèm bằng chứng đo được, đối chiếu bộ luật do tổ chức khai báo, rồi mới cho phép thực thi.
- **Day 2 — vận hành.** Theo dõi các pipeline đã deploy, phát hiện suy giảm về thời gian, chi phí, độ tươi, tự đề xuất bản sửa **đã được kiểm chứng trên sandbox**, và chỉ gõ cửa kỹ sư khi có việc cần quyết định.

Bằng chứng ghi lúc tạo mới chính là đường cơ sở để so sánh lúc vận hành. Hai giai đoạn dùng chung một bộ máy, không phải hai tính năng rời.

### 1.2 Ba nỗi đau trong đề bài

| # | Nỗi đau | Biểu hiện thực tế | Hệ thống giải quyết ở đâu |
|---|---|---|---|
| A | Viết pipeline **lặp đi lặp lại** | Pipeline thứ 40 có cấu trúc y hệt pipeline thứ 39, nhưng vẫn phải viết tay; kết quả không đồng đều — cái có test, cái không, cái đặt tên sai quy ước | UC-1, UC-2 |
| B | **Query chậm** cần tối ưu | Pipeline chạy 4 giây lúc bảng có 2 triệu dòng, 40 giây khi bảng lên 50 triệu dòng. Chậm dần sau khi deploy, không phải lúc mới viết | UC-3, UC-4 |
| C | Phải **ngồi theo dõi** | Mỗi sáng mở dashboard xem job nào chậm, job nào tốn tiền, bảng nào thiếu dữ liệu, rồi tự điều tra, tự sửa, tự kiểm | UC-4 |

**Về nỗi đau A:** giá trị chính không nằm ở tốc độ mà ở **tính nhất quán**. Agent sinh mọi pipeline từ cùng một bộ template và cho chúng đi qua cùng một bộ thẩm định, nên chất lượng đồng đều hơn thay vì chỉ nhanh hơn.

**Về nỗi đau C:** kỹ sư không muốn theo dõi; họ muốn **được báo khi có việc, kèm sẵn phương án đã kiểm chứng**. Một cảnh báo "job chậm 3 lần liên tiếp" vẫn bắt kỹ sư tự điều tra. Hệ thống này đi xa hơn: tự đề xuất bản sửa, tự chạy thử, tự chứng minh bản sửa vừa đúng vừa nhanh hơn, rồi mới trình lên.

### 1.3 Vì sao không phải "AI viết SQL"

Ba vấn đề mà công cụ sinh code thuần không giải quyết được:

1. **Guardrail phải cưỡng chế, không phải lời dặn.** Agent chạy tự động cần chốt chặn ở tầng hạ tầng (IAM, `maximum_bytes_billed`, `require_partition_filter`), không phải câu "đừng làm hỏng prod" trong prompt.
2. **Ranh giới nghiệp vụ không suy ra được từ schema.** Khi có 17 bảng tên `customer` ở các domain khác nhau, agent thông minh hơn không giúp được. Nó phải đọc định nghĩa do con người viết, và **biết từ chối** khi chưa có định nghĩa.
3. **Xung đột mục tiêu cần trọng tài tất định.** Streaming nhanh nhưng đắt, batch rẻ nhưng chậm. Quyết định phải dựa trên hàm mục tiêu con người khai báo trước, không phải hai LLM thương lượng.
4. **Pipeline xuống cấp sau khi deploy.** Công cụ sinh code dừng lại khi code được sinh ra. Nhưng phần lớn thời gian của kỹ sư nằm ở giai đoạn sau đó.

### 1.4 Hệ quả kiến trúc

**LLM chỉ được quyền đề xuất. Code tất định mới được quyền quyết định. Con người sở hữu bộ luật.**

Bộ sinh code là bộ phận thay thế được. Kho chính sách và sổ bằng chứng là lõi. Thiết kế module phải phản ánh điều này: đổi model LLM không được đụng tới `control_plane/`.

---

## 2. Actor và ranh giới quyền

| Actor | Làm được | Không làm được |
|---|---|---|
| Người yêu cầu | Gửi yêu cầu, xác nhận kế hoạch ở P2, xem kết quả | Duyệt code, deploy |
| Data engineer | Chạy agent, sửa spec, chạy lại, đọc bằng chứng | Duyệt chính run của mình |
| Reviewer | Đọc diff và bằng chứng, duyệt / từ chối / yêu cầu sửa, kích hoạt deploy | Sửa code trực tiếp trong app |
| Data steward | Sở hữu `policy/*.yml`, xử lý phiếu xung đột thực thể | — |
| Agent | Sinh, chạy thử, tự sửa **trong sandbox** | Chạm prod, deploy khi chưa được duyệt, chạy lệnh shell |

Quy tắc "engineer không tự duyệt run của mình" là ràng buộc bắt buộc, không phải tùy chọn cấu hình.

---

## 3. Use case

### 3.1 UC-1 — Tạo pipeline mới từ yêu cầu nghiệp vụ

**Tiền điều kiện:** nguồn đã khai báo trong `policy/domains.yml`; người dùng có vai trò Engineer.

**Luồng chính:** người dùng mô tả nhu cầu bằng ngôn ngữ tự nhiên → hệ thống lập kế hoạch (P1) → người dùng xác nhận (P2) → hệ thống phân giải schema và kiểm ngữ nghĩa (P3) → sinh spec và render (P4–P6) → kiểm tĩnh và ngân sách (P7–P8) → chạy thử và thẩm định trên sandbox (P9–P12) → tối ưu (P13) → xuất bằng chứng (P14) → Reviewer quyết định (P15) → deploy (P16) → lưu mẫu (P17).

**Luồng thay thế:** xung đột ngữ nghĩa ở P3 → dừng, phiếu chuyển steward. Vượt ngân sách ở P8 → quay lại P4 kèm ràng buộc byte. Thẩm định fail → vòng spec hoặc vòng SQL theo mục 11.3. Chạm trần → `NEEDS_HUMAN`.

**Hậu điều kiện:** pipeline tồn tại trong code location; mọi quyết định và số đo đã ghi vào sổ bằng chứng.

### 3.2 UC-2 — Onboard hàng loạt bảng

Người dùng chọn N bảng của một hệ thống nguồn mới; hệ thống sinh N pipeline ingestion và staging theo cùng quy ước, thẩm định từng cái, và trình kết quả theo mẻ để duyệt.

**Giá trị:** nhất quán, không chỉ nhanh. Cả N pipeline sinh từ cùng template, cùng quy ước đặt tên, cùng bộ test, cùng đi qua 6 cổng. Đây là câu trả lời trực tiếp cho nỗi đau A.

**Khác UC-1:** fan-out sau P3, mỗi nhánh chạy P4–P14 độc lập, gom lại ở P15. Ngân sách tính trên tổng mẻ, không trên từng nhánh.

### 3.3 UC-3 — Tối ưu theo yêu cầu

Người dùng **chủ động** trỏ vào một model đã tồn tại; hệ thống đo baseline, áp luật tối ưu, chạy thử bản ứng viên trên sandbox, và chỉ đề xuất khi bản ứng viên vừa **đúng** vừa **nhanh hơn**.

**Khác UC-1:** `trigger = "manual_optimize"`, vào thẳng P4 với spec đã có, bỏ qua P0–P3.

### 3.4 UC-4 — Tự phát hiện suy giảm và đề xuất sửa

Đây là use case Day 2, giải quyết nỗi đau B và C.

**Tiền điều kiện:** pipeline đã deploy qua UC-1 hoặc UC-2, có đường cơ sở ghi trong sổ bằng chứng.

**Luồng chính:** `monitor` thu số đo sau mỗi lần pipeline chạy → một luật phát hiện ở mục 9.6 vượt ngưỡng → hệ thống tự tạo run mới với `trigger = "regression"`, mang theo spec đang chạy và bằng chứng suy giảm → vào P4 → đi qua P5–P14 như bình thường → nếu bản sửa vừa đúng vừa tốt hơn thì vào hàng đợi review với nhãn "bản sửa cho pipeline đang suy giảm" → kỹ sư duyệt → deploy.

**Luồng thay thế:** không tìm được bản sửa nào tốt hơn → không làm phiền kỹ sư, chỉ ghi vào sổ bằng chứng và gắn cờ pipeline để kỹ sư xem khi rảnh. Suy giảm về độ tươi do nguồn ngừng cấp dữ liệu → không phải lỗi pipeline, chuyển thành cảnh báo hạ tầng, không tạo run sửa.

**Hậu điều kiện:** kỹ sư không phải mở dashboard. Họ chỉ nhận những đề xuất đã có bằng chứng.

**Ràng buộc:** UC-4 **không bao giờ tự deploy**. Tự phát hiện và tự đề xuất, nhưng quyết định vẫn thuộc về người. Đây là điểm phân biệt với hệ thống tự sửa hoàn toàn, và giữ nguyên ràng buộc HITL của đề bài.

### 3.5 Ranh giới

Hệ thống không thay thế data engineer mà đổi vai họ từ người viết thành người duyệt. Nó không xử lý logic nghiệp vụ cần tri thức ngoài schema (hoa hồng nhiều tầng, đối soát kế toán). Nó không chạm prod. Nó không thay thế data catalog hay quy trình governance của tổ chức.

---

## 4. Yêu cầu chức năng

| FR | Yêu cầu | Phase | Nghiệm thu |
|---|---|---|---|
| FR-01 | Xác thực và phân biệt hai vai trò Engineer / Reviewer | — | Reviewer không thấy nút tạo; Engineer không duyệt được run của mình |
| FR-02 | Nhận yêu cầu ngôn ngữ tự nhiên kèm chọn domain | P0–P1 | Yêu cầu tạo ra `run_id` và bản ghi run |
| FR-03 | Lập kế hoạch và cho người xác nhận trước khi sinh code | P1–P2 | Không sinh spec khi chưa có `PLAN_CONFIRMED` |
| FR-04 | Phân giải schema thật từ nguồn, không để LLM tự nghĩ tên cột | P3 | `schema_snapshot` có nguồn gốc `information_schema` |
| FR-05 | Phát hiện và chặn xung đột định nghĩa thực thể giữa các domain | P3 | Ca vi phạm trả `BLOCKED_SEMANTIC_CONFLICT` kèm cả hai định nghĩa |
| FR-06 | Sinh đặc tả pipeline dưới dạng JSON spec, không sinh code tự do | P4 | Spec khớp JSON Schema; code do template render |
| FR-07 | Phân xử giữa nhiều phương án theo hàm mục tiêu khai báo | P5 | Lý do chọn ghi trong report; không phương án khả thi thì escalate |
| FR-08 | Render bộ file pipeline đầy đủ: ingestion, model, schema test, orchestration | P6 | 4 file, đúng cú pháp 100% |
| FR-09 | Chặn lệnh nguy hiểm và mẫu SQL xấu bằng phân tích AST | P7 | `DROP`/`DELETE`/cross-db bị chặn với `BLOCKED_AST` |
| FR-10 | Ước lượng chi phí trước khi chạy và chặn khi vượt ngân sách | P8 | `dry_run` cho byte; vượt trần trả `BLOCKED_BUDGET` |
| FR-11 | Thực thi pipeline trên sandbox cô lập | P9–P11 | Sandbox mới mỗi run, xóa sau khi xong |
| FR-12 | Thẩm định tính đúng của ingestion và transform | P10, P12 | 4 phép kiểm tra ở mục 10.1 đều chạy |
| FR-13 | Tự sửa khi lỗi, trong trần đã khai báo | P4/P6 | Ba vòng lặp ở mục 11.3, trần đọc từ `budgets.yml` |
| FR-14 | Đo và đề xuất tối ưu, chỉ nhận khi vừa đúng vừa nhanh hơn | P13 | Bản ứng viên kém hơn thì bị loại, ghi lý do |
| FR-15 | Xuất bằng chứng có cấu trúc cho mọi run | P14 | `RunReport` đủ `gates`, `checks`, `cost`, `timing` |
| FR-16 | Review có ba nhánh quyết định, phê duyệt gắn với `files_hash` | P15 | File đổi làm phê duyệt mất hiệu lực |
| FR-17 | Deploy lên môi trường sandbox sau khi được duyệt | P16 | Mức D1/D2 ở mục 9.5 |
| FR-18 | Lưu mẫu đã duyệt để tái sử dụng làm few-shot | P17 | Chỉ lưu khi deploy thành công |
| FR-19 | Hiển thị chính sách hiện hành ở chế độ chỉ đọc | — | 3 file YAML hiển thị đúng nội dung trong git |
| FR-20 | Ghi sổ bằng chứng và truy vết được mọi quyết định | Mọi phase | Mỗi file deploy truy về một `run_id` và một chữ ký |
| FR-21 | Thu số đo của mọi lần chạy pipeline đã deploy | Day 2 | Sổ bằng chứng có chuỗi thời gian theo từng pipeline |
| FR-22 | Phát hiện suy giảm theo luật khai báo, không dùng LLM | Day 2 | 4 luật ở mục 9.6, ngưỡng đọc từ `objectives.yml` |
| FR-23 | Tự tạo run sửa khi phát hiện suy giảm | Day 2 → P4 | Run mới có `trigger = "regression"` và bằng chứng suy giảm đính kèm |
| FR-24 | Không làm phiền kỹ sư khi không có bản sửa tốt hơn | P13–P14 | Run không vào hàng đợi review, chỉ gắn cờ |
| FR-25 | Không tự deploy bản sửa từ Day 2 | P15 | Mọi run `regression` bắt buộc qua cổng người |

---

## 5. Yêu cầu phi chức năng

| NFR | Yêu cầu | Cách kiểm |
|---|---|---|
| NFR-01 | Agent không có credential prod trong môi trường | Test bất biến, không phải lời dặn trong prompt |
| NFR-02 | Không node LLM nào thực thi lệnh shell | Rà mã nguồn, test bất biến |
| NFR-03 | Mọi cổng là hàm thuần, có unit test | Độ phủ test của `control_plane/gates/` |
| NFR-04 | Mọi trần và ngân sách đọc từ `policy/*.yml`, không hardcode | Đổi giá trị trong YAML thì hành vi đổi theo, không cần build lại |
| NFR-05 | Một run không treo vô hạn | Trần gọi LLM và timeout mỗi phase |
| NFR-06 | Đổi warehouse chỉ là đổi một adapter | `WarehouseAdapter` protocol, mục 14 |
| NFR-07 | Đổi model LLM không đụng `control_plane/` | Ranh giới module |
| NFR-08 | Mọi con số hiển thị truy được về một phép đo trong `RunReport` | Rà UI |
| NFR-09 | Tái hiện được quá trình khi cần giải trình | `spec_history` lưu mọi phiên bản |
| NFR-10 | Giao diện truy cập được bằng bàn phím, màu luôn kèm chữ trạng thái | `UI-SPEC.md` |

---

## 6. Tech stack

| Lớp | Công cụ | Ghi chú |
|---|---|---|
| Agent | LangGraph | 4 node: planner → codegen → test-run → optimizer |
| LLM | Claude (API) | Model rẻ cho phân loại, model mạnh cho sinh spec |
| Ingestion (E-L) | dlt | Bổ sung so với đề bài gốc; đề bài không có công cụ cho E-L |
| Transform (T) | dbt-core + dbt-duckdb | dbt-bigquery cho demo cổng chi phí |
| Orchestration | Dagster (`dagster-dbt`, `dagster-dlt`) | Nhẹ hơn Airflow, asset model hợp với lineage |
| Sandbox | DuckDB | Môi trường phát triển hằng ngày |
| Cloud warehouse | BigQuery | Chỉ để chứng minh `dry_run` và cổng chi phí |
| Phân tích SQL | sqlglot | Phân tích **tĩnh** (AST). Query plan lấy từ `EXPLAIN ANALYZE` / `dry_run` |
| Vector DB | Qdrant (hoặc pgvector) | Lưu spec **đã duyệt** làm few-shot |
| Backend | FastAPI | SSE streaming |
| Frontend | Next.js + Monaco (`@monaco-editor/react` DiffEditor) | |
| Nguồn | Postgres container + FastAPI mock API | |
| Đóng gói | docker-compose | Cloud Run cho FastAPI + Next.js ở bản deploy |

**Lưu ý thuật ngữ:** đề bài gốc viết "sqlglot phân tích query plan". sqlglot không đọc query plan, nó parse SQL thành AST. Query plan thật đến từ `EXPLAIN ANALYZE` (DuckDB) và `dry_run` (BigQuery). Ghi đúng điều này trong báo cáo.

---

## 7. Cấu trúc repo

```
.
├── CLAUDE.md
├── SPEC.md
├── Makefile
├── policy/
│   ├── domains.yml
│   ├── budgets.yml
│   └── objectives.yml
├── templates/
│   ├── dlt_pipeline.py.j2
│   ├── dbt_model.sql.j2
│   ├── dbt_schema.yml.j2
│   └── dagster_asset.py.j2
├── control_plane/
│   ├── models.py            # pydantic: Spec, RunReport, GateResult, ExecResult
│   ├── render.py            # spec -> {path: content}
│   ├── execute.py           # chạy dlt + dbt trên sandbox
│   ├── evidence.py          # sổ bằng chứng
│   ├── sandbox.py           # tạo/huỷ sandbox cô lập
│   ├── policy.py            # nạp và xác thực 3 file YAML
│   ├── arbiter.py           # trọng tài theo objectives.yml
│   ├── entity_scan.py       # dò trùng thực thể
│   ├── monitor.py           # Day 2: thu số đo sau mỗi lần pipeline chạy
│   ├── detectors.py         # Day 2: 4 luật phát hiện suy giảm
│   ├── adapters/
│   │   ├── base.py          # WarehouseAdapter protocol
│   │   ├── duckdb_adapter.py
│   │   └── bigquery_adapter.py
│   └── gates/
│       ├── spec_schema.py
│       ├── schema_real.py
│       ├── semantic.py
│       ├── ast_guard.py
│       ├── budget.py
│       └── post_run.py
├── agent/
│   ├── graph.py             # LangGraph, máy trạng thái
│   ├── state.py
│   ├── router.py            # bảng định tuyến lỗi (tất định)
│   ├── nodes/
│   │   ├── planner.py
│   │   ├── codegen.py
│   │   ├── sql_fix.py
│   │   └── optimizer.py
│   ├── prompts/
│   └── memory.py            # vector DB, spec đã duyệt
├── api/
│   └── main.py
├── web/
├── infra/
│   ├── docker-compose.yml
│   ├── seeds/               # SQL seed dữ liệu bẩn có chủ đích
│   ├── mock_api/            # FastAPI giả lập nguồn REST
│   └── dagster/
├── dbt_project/
├── fixtures/                # spec_01..05.json viết tay
└── eval/
    ├── cases/               # 15 ca
    └── run_eval.py
```

---

## 8. Hợp đồng dữ liệu

Bốn cấu trúc dưới đây là ranh giới giữa các module. **Sửa chúng phải báo cả nhóm trong ngày.**

### 8.1 Spec (JSON)

```json
{
  "spec_version": "1.0",
  "domain": "sales",
  "source": {
    "kind": "postgres",
    "connection_ref": "sandbox_pg",
    "table": "orders",
    "primary_key": ["order_id"],
    "cursor_field": "updated_at",
    "write_disposition": "merge"
  },
  "target": { "dataset": "bronze", "table": "raw_orders" },
  "transform": {
    "model_name": "fct_daily_revenue",
    "materialization": "incremental",
    "unique_key": ["order_date", "channel"],
    "grain": ["order_date", "channel"],
    "metrics": [
      { "name": "revenue", "expr": "SUM(amount)" },
      { "name": "order_count", "expr": "COUNT(DISTINCT order_id)" }
    ],
    "filters": ["status <> 'cancelled'"],
    "partition_by": "order_date",
    "tests": [
      { "column": "order_date", "test": "not_null" },
      { "column": "revenue", "test": "not_negative" }
    ]
  },
  "schedule": { "cron": "0 * * * *" },
  "estimates": { "bytes_scanned": 0, "usd": 0.0, "latency_sec": 0 }
}
```

Ràng buộc:
- `source.kind` ∈ `{postgres, rest_api, filesystem}`
- `source.write_disposition` ∈ `{append, replace, merge}`; `merge` bắt buộc có `primary_key`
- `transform.materialization` ∈ `{view, table, incremental}`; `incremental` bắt buộc có `unique_key`
- `metrics[].expr` chỉ được chứa hàm tổng hợp và tên cột; qua cổng `ast`
- Viết JSON Schema tương ứng tại `control_plane/schemas/spec.schema.json`

### 8.2 RunReport (JSON)

```json
{
  "run_id": "uuid",
  "trigger": "user_request",
  "parent_run_id": null,
  "regression": null,
  "created_at": "iso8601",
  "user_request": "…",
  "status": "READY_FOR_REVIEW",
  "stage_reached": "measure",
  "plan": { "…": "…" },
  "spec": { "…": "…" },
  "spec_history": [],
  "files": { "models/fct_daily_revenue.sql": "sha256:…" },
  "files_hash": "sha256:…",
  "gates": [
    { "name": "budget", "result": "pass",
      "detail": "1.2 GB ước tính, trần 5 GB",
      "observed": { "bytes": 1234567890 } }
  ],
  "checks": [
    { "name": "row_reconcile", "result": "pass",
      "observed": { "source": 12480, "bronze": 12480 } },
    { "name": "idempotent", "result": "pass",
      "observed": { "run1": 12480, "run2": 12480 } },
    { "name": "dbt_test", "result": "pass",
      "observed": { "passed": 6, "failed": 0 } },
    { "name": "metric_reconcile", "result": "pass",
      "observed": { "gold_sum": 8123456.0, "bronze_sum": 8123456.0 } }
  ],
  "cost": { "estimated_bytes": 0, "actual_bytes": 0, "usd": 0.0 },
  "timing": { "baseline_sec": 4.2, "optimized_sec": 0.9 },
  "optimizer": [
    { "rule": "table_to_incremental", "applied": true,
      "before_sec": 4.2, "after_sec": 0.9 }
  ],
  "attempts": { "spec": 1, "sql": 2, "el": 0 },
  "llm_calls": 5,
  "error_trace": [],
  "approval": null
}
```

### 8.3 Sự kiện SSE

```json
{ "run_id": "…", "stage": "el_run", "status": "running",
  "message": "Đang chạy dlt…", "payload": {} }
```

Tập `stage` cố định, **bằng đúng id phase ở mục 9**, không có tên riêng:
`intake, plan, confirm, resolve, propose, arbitrate, render, static, budget, el_run, el_validate, t_run, t_validate, optimize, report, review, deploy, persist`

Ánh xạ `P0→intake`, `P1→plan`, … `P17→persist`. Bất kỳ tên nào ngoài 18 giá trị này là lỗi hợp đồng.

`status` ∈ `{running, pass, fail, escalate, blocked}`

### 8.4 Ba file chính sách

**`policy/domains.yml`**

```yaml
domains:
  sales:
    datasets: [sales_raw]
    steward: an@company.vn
    entities:
      customer:
        canonical: false
        definition: "tài khoản đã ký hợp đồng"
        tables: [sales_raw.customers, sales_raw.accounts]
      order:
        canonical: true
        definition: "đơn hàng đã xác nhận"
        tables: [sales_raw.orders]
  marketing:
    datasets: [mkt_raw]
    steward: binh@company.vn
    entities:
      customer:
        canonical: false
        definition: "người để lại email"
        tables: [mkt_raw.leads, mkt_raw.contacts]

conformed_dimensions: []   # rỗng nghĩa là chưa có thực thể nào được công nhận dùng chung
```

**`policy/budgets.yml`**

```yaml
pricing:
  usd_per_tb: 6.25          # đơn giá BigQuery on-demand; DuckDB quy đổi ước tính theo số này
per_run:
  max_bytes_scanned: 5_000_000_000
  max_usd: 0.05
  max_llm_calls: 6          # trần toàn cục 1 run; router VÀ UI đều đọc từ đây
per_day:
  max_usd: 2.00
rules:
  - require_partition_filter_on: [sales_raw.orders]
  - forbid_select_star_in: [gold]
  - max_tables_joined: 6
```

**`policy/objectives.yml`**

```yaml
sales:
  hard:
    freshness_minutes: 60
  soft:
    cost_usd_per_day: 1.0
  prefer: cheapest_feasible
  degrade_order: [freshness, completeness]
  regression:                     # Day 2 — ngưỡng phát hiện suy giảm
    window_runs: 7                # so trung vị 7 lần chạy gần nhất
    runtime_increase_pct: 50      # chậm hơn đường cơ sở 50% thì báo
    cost_increase_pct: 40
    test_fail_rate_pct: 5
    cooldown_hours: 24            # không tạo run sửa trùng cho cùng pipeline trong 24h
marketing:
  hard:
    freshness_minutes: 1440
  soft:
    cost_usd_per_day: 0.5
  prefer: cheapest_feasible
  degrade_order: [freshness, completeness]
```

---

## 9. Workflow runtime — 18 phase P0–P17

**P0–P17 là bộ định danh duy nhất của dự án.** Dùng ở `SPEC.md`, `SCOPE.md`, `UI-SPEC.md`, trong mã nguồn, trong SSE `stage`, trong log. Không có hệ đánh số thứ hai.

Mỗi phase ghi state xuống sổ bằng chứng trước khi sang phase sau.

| P | id | Loại | Mô tả |
|---|---|---|---|
| P0 | `intake` | Tất định | Sinh `run_id`, tạo sandbox mới, ghi bản ghi run |
| P1 | `plan` | LLM | Sinh kế hoạch: nguồn, bảng, độ hạt, chỉ số, tần suất. Tối đa 1 câu hỏi làm rõ |
| P2 | `confirm` | **Người** | Cổng HITL 1: người dùng xác nhận kế hoạch |
| P3 | `resolve` | Tất định | Schema thật từ `information_schema`, 20 dòng mẫu, lineage từ dbt manifest, `entity_scan` → cổng `semantic` |
| P4 | `propose` | LLM | Sinh JSON spec. Nhiều phương án khả dĩ thì sinh cả hai kèm số đo |
| P5 | `arbitrate` | Tất định | Đối chiếu phương án với `objectives.yml` |
| P6 | `render` | Tất định | Jinja render 4 file. Lỗi ở đây là bug template → `INTERNAL_ERROR` |
| P7 | `static` | Tất định | `compile()` + `sqlglot.parse` + cổng `spec_schema`, `schema_real`, `ast_guard` |
| P8 | `budget` | Tất định | `dry_run` lấy byte, quy USD, cổng `budget` |
| P9 | `el_run` | Tất định | Chạy dlt đổ vào bronze trên sandbox |
| P10 | `el_validate` | Tất định | 3 phép: `row_reconcile`, `hash_reconcile`, `idempotent` |
| P11 | `t_run` | Tất định | `dbt run` |
| P12 | `t_validate` | Tất định | `dbt test` + `metric_reconcile` |
| P13 | `optimize` | Hỗn hợp | Đo baseline, áp luật, chạy lại, đo. **Không bao giờ làm fail run** |
| P14 | `report` | Tất định | Gom RunReport, tính `files_hash`, lấy chi phí thật |
| P15 | `review` | **Người** | Cổng HITL 2: duyệt / từ chối / yêu cầu sửa |
| P16 | `deploy` | Tất định | Chỉ chạy khi `approval.hash == files_hash`. Mức D1/D2 xem mục 9.5 |
| P17 | `persist` | Tất định | Lưu (yêu cầu, spec) vào vector DB làm few-shot |

### 9.0 Nhóm phase cho hiển thị

UI không hiện 18 chấm. Gom thành 6 nhóm, bung ra mới thấy phase con. Nhóm là **khái niệm hiển thị**, không phải khái niệm runtime.

| Nhóm | Phase | Nhãn UI |
|---|---|---|
| G1 | P0–P2 | Tiếp nhận |
| G2 | P3 | Phân giải |
| G3 | P4–P6 | Sinh |
| G4 | P7–P8 | Kiểm |
| G5 | P9–P13 | Chạy |
| G6 | P14–P17 | Quyết định |

### 9.1 Chi tiết `resolve` — dò trùng thực thể

```python
def entity_scan(request: str, catalog: Catalog, domains: DomainRegistry) -> EntityConflict | None
```

Thuật toán:
1. Trích danh từ thực thể từ yêu cầu (LLM hoặc từ khoá, MVP dùng từ khoá + danh sách trong `domains.yml`)
2. Tìm mọi bảng khớp thực thể đó trên toàn catalog
3. Nếu các bảng khớp nằm ở **nhiều hơn một domain** và thực thể chưa nằm trong `conformed_dimensions` → trả về `EntityConflict`
4. Chặng dừng ngay với `status = BLOCKED_SEMANTIC_CONFLICT`, tạo phiếu chuyển steward

Chặn ở đây, **trước** khi tốn bất kỳ lần gọi LLM nào cho việc sinh code.

Thông điệp trả về phải nêu rõ cả hai định nghĩa, ví dụ:

> Phát hiện thực thể `customer` ở 2 domain. Sales định nghĩa là tài khoản đã ký hợp đồng (2 bảng). Marketing định nghĩa là người để lại email (2 bảng). Chưa có định nghĩa chuẩn. Chuyển tới steward domain Sales: an@company.vn

### 9.2 Chi tiết `arbitrate`

```python
def arbitrate(proposals: list[Proposal], objectives: DomainObjectives) -> ArbitrationResult
```

- Lọc các phương án thoả **toàn bộ** ràng buộc `hard`
- Nếu rỗng → `ArbitrationResult(status="escalate", tradeoff_table=[...])`
- Nếu có → chọn theo `prefer` (MVP chỉ cần `cheapest_feasible`)
- Ghi lý do chọn vào report

### 9.3 Chi tiết `optimize`

Hai luật cố định:

1. **`table_to_incremental`**: nếu `materialization == "table"` và bảng nguồn có cột thời gian và ước tính số dòng > ngưỡng → đề xuất `incremental` với `unique_key` từ `grain`
2. **`missing_partition_filter`**: nếu SQL không có điều kiện trên cột `partition_by` → cảnh báo và đề xuất thêm

Quy trình: đo baseline bằng `EXPLAIN ANALYZE` → render bản tối ưu → chạy lại **P11 và P12** trên bản đó → đo lại. Nếu bản tối ưu sai hoặc chậm hơn, **vứt đi, giữ bản gốc, ghi lý do**.

### 9.4 Chi tiết `el_validate` và `t_validate`

Bốn phép kiểm tra ở mục 10.1 là **bắt buộc ở backend**, không phụ thuộc vào việc UI có hiện ô tương ứng hay không. `UI-SPEC.md` chỉ quyết định cách trình bày.

### 9.5 Chi tiết `deploy` — hai mức D1 và D2

| Mức | Hành vi | Tiêu chí nghiệm thu |
|---|---|---|
| **D1** | Ghi 4 file vào code location của Dagster. Người dùng nạp lại Dagster thủ công và thấy asset xuất hiện | File tồn tại đúng đường dẫn; asset hiện trong đồ thị; `status = DEPLOYED` |
| **D2** | D1 cộng: gọi API nạp lại, trigger một lần chạy, chờ xanh trong 120s; không xanh thì xóa file, nạp lại, `DEPLOY_FAILED` | Run xanh, **hoặc** file đã bị xóa và trạng thái đúng là `DEPLOY_FAILED` |

MVP dừng ở **D1**. D2 là thiết kế đích. Cả hai mức đều yêu cầu `approval.hash == files_hash`.

Luồng pull request và merge lên production nằm ngoài cả D1 và D2 — xem mục 0.1.

---

### 9.6 Day 2 — vòng vận hành

Day 2 **không thêm phase mới**. Nó dùng lại nguyên P4–P16, chỉ khác ở chỗ ai kích hoạt run.

#### 9.6.1 Ba loại kích hoạt

| `trigger` | Ai tạo | Vào từ phase | Mang theo |
|---|---|---|---|
| `user_request` | Người dùng | P0 | Yêu cầu ngôn ngữ tự nhiên |
| `manual_optimize` | Người dùng trỏ vào model | P4 | Spec đang chạy |
| `regression` | `monitor` tự tạo | P4 | Spec đang chạy + bằng chứng suy giảm + `parent_run_id` |

Run `regression` bỏ qua P0–P3 vì spec đã được duyệt và schema đã được phân giải từ lần tạo mới. Riêng P3 chạy lại nếu schema nguồn đã đổi so với `schema_snapshot` cũ.

#### 9.6.2 Monitor thu gì

`monitor` không quan sát dữ liệu, nó quan sát **các lần chạy**. Cả ba nguồn đều có sẵn, không phải dựng thêm hạ tầng:

| Nguồn | Cho biết |
|---|---|
| Dagster run events | Thời gian chạy, thành công hay thất bại, thời điểm |
| dbt `run_results.json` | Thời gian từng model, test nào fail |
| `adapter.job_cost()` | Byte quét và chi phí mỗi lần chạy |

Sổ bằng chứng chuyển từ "mỗi run một dòng" thành **chuỗi thời gian theo từng pipeline**. Dòng đầu tiên của chuỗi là số đo lúc P14 của run tạo mới — đó là đường cơ sở.

#### 9.6.3 Bốn luật phát hiện

Tất cả tất định, không dùng LLM, ngưỡng đọc từ `objectives.yml: <domain>.regression`.

| Luật | Điều kiện kích hoạt | Hành động |
|---|---|---|
| `runtime_regression` | Trung vị thời gian `window_runs` lần gần nhất > đường cơ sở × (1 + `runtime_increase_pct`) | Tạo run `regression` |
| `cost_regression` | Trung vị chi phí vượt đường cơ sở × (1 + `cost_increase_pct`) | Tạo run `regression` |
| `test_degradation` | Tỷ lệ test fail > `test_fail_rate_pct` | Tạo run `regression` |
| `freshness_breach` | Vi phạm `hard.freshness_minutes` | Xem 9.6.4 |

`cooldown_hours` ngăn tạo run sửa trùng lặp cho cùng một pipeline.

#### 9.6.4 Khi nào không tạo run sửa

Không phải suy giảm nào cũng là lỗi của pipeline:

- **Độ tươi vi phạm vì nguồn ngừng cấp dữ liệu** — kiểm tra số dòng mới ở nguồn trước. Nguồn trống thì đây là sự cố hạ tầng, chuyển thành cảnh báo, không tạo run.
- **P13 không tìm được bản nào tốt hơn** — ghi vào sổ bằng chứng, gắn cờ pipeline, **không** đưa vào hàng đợi review. Kỹ sư chỉ bị làm phiền khi có đề xuất đáng xem.

#### 9.6.5 Hiển thị trong hàng đợi review

Run `regression` hiện trong cùng hàng đợi với run tạo mới, nhưng nhãn khác. Không phải "pipeline mới chờ duyệt" mà là:

> Bản sửa cho `fct_daily_revenue` · đang chậm hơn 62% so với lúc deploy · bản đề xuất đã thử trên sandbox, nhanh hơn 38%, kết quả khớp 100%

Ba con số: mức suy giảm, mức cải thiện, độ khớp. Đủ để kỹ sư quyết định mà không phải mở thêm màn nào.

#### 9.6.6 Ràng buộc

Day 2 **không bao giờ tự deploy**. Mọi run `regression` bắt buộc qua P15. Hệ thống tự phát hiện, tự đề xuất, tự kiểm chứng — nhưng không tự quyết.

---

## 10. Sáu cổng

Mọi cổng là hàm thuần, không gọi LLM, có unit test. **Sáu cổng và bốn phép kiểm tra đều bắt buộc trong MVP.** Việc UI rút gọn số ô hiển thị không làm giảm số phép kiểm tra chạy ở backend.

| Cổng | Chữ ký | Kiểm tra | Nguồn luật |
|---|---|---|---|
| `spec_schema` | `(spec) -> GateResult` | Khớp JSON Schema | `schemas/spec.schema.json` |
| `schema_real` | `(spec, catalog) -> GateResult` | PK tồn tại và unique; cursor tồn tại và là timestamp/số tăng | `information_schema` |
| `semantic` | `(request, catalog, domains) -> GateResult` | Thực thể mơ hồ chưa có định nghĩa chuẩn | `domains.yml` |
| `ast_guard` | `(sql_files) -> GateResult` | Chặn DROP/DELETE/TRUNCATE/ALTER, cross-database ref, SELECT * ở gold, join quá `max_tables_joined`, tích Descartes | sqlglot + `budgets.yml` |
| `budget` | `(sql, adapter, budgets) -> GateResult` | `dry_run` bytes vượt `max_bytes_scanned` hoặc `max_usd`; thiếu partition filter trên bảng bắt buộc | `budgets.yml` |
| `post_run` | `(exec_result, adapter) -> list[GateResult]` | 4 phép kiểm tra sau chạy | Dữ liệu thật |

### 10.1 Bốn phép kiểm tra của `post_run`

| Phép | Cách làm | Pass khi |
|---|---|---|
| `row_reconcile` | `COUNT(*)` nguồn trong khoảng đã lấy vs bronze | Bằng nhau |
| `hash_reconcile` | Hash các cột PK ở hai đầu | Trùng khớp |
| `idempotent` | Chạy pipeline lần hai | Số dòng không đổi |
| `metric_reconcile` | Tổng chỉ số ở gold vs tính trực tiếp từ bronze | Chênh lệch < 0.01% |

`idempotent` **không được bỏ qua** trong bất kỳ trường hợp nào.

---

## 11. Máy trạng thái và định tuyến lỗi

### 11.1 Trạng thái

Luồng chính:
`DRAFT → PLAN_CONFIRMED → GENERATING → VALIDATING → READY_FOR_REVIEW → APPROVED → DEPLOYED`

Kết thúc sớm:
`NEEDS_HUMAN, BLOCKED_SEMANTIC_CONFLICT, BLOCKED_BUDGET, BLOCKED_AST, REJECTED, INFRA_ERROR, INTERNAL_ERROR, DEPLOY_FAILED`

Chỉ `READY_FOR_REVIEW` mới được duyệt. Chỉ `APPROVED` mới được deploy. Không có đường tắt.

### 11.2 Bảng định tuyến

Cài trong `agent/router.py` dưới dạng bảng dữ liệu, không phải chuỗi `if`.

| Lỗi tại | Loại lỗi | Đi đâu | Trần |
|---|---|---|---|
| P3 | `semantic` escalate | Dừng, `BLOCKED_SEMANTIC_CONFLICT` | 0 |
| P5 | Không phương án nào khả thi | Dừng, chờ người | 0 |
| P6 | Render lỗi | Dừng, `INTERNAL_ERROR` | 0 |
| P7 | `spec_schema` fail | P4 | 2 |
| P7 | `schema_real` fail | P4 | 2 |
| P7 | SQL sai cú pháp | `sql_fix` → P6 | 3 |
| P7 | `ast_guard` fail | Dừng, `BLOCKED_AST` | 0 |
| P8 | `budget` fail | P4, kèm số byte cụ thể phải rút xuống | 2 |
| P9 | Không kết nối được nguồn | Dừng, `INFRA_ERROR` | 0 |
| P9 | Sai kiểu dữ liệu / sai cột | P4 | 2 |
| P10 | `row_reconcile` hoặc `hash_reconcile` lệch | P4 | 2 |
| P10 | `idempotent` fail | P4, ép xem lại `primary_key` | 2 |
| P11 | dbt run lỗi | `sql_fix` → P6 | 3 |
| P12 | dbt test hoặc `metric_reconcile` fail | `sql_fix` → P6 | 2 |
| P13 | Bản tối ưu sai/chậm hơn | Bỏ qua, đi tiếp P14 | 0 |
| P16 | D2: Dagster không xanh trong 120s | Rollback, `DEPLOY_FAILED` | 0 |

### 11.3 Ba vòng lặp

Cả hệ thống chỉ có ba đường quay lui. Mọi lỗi khác dừng hẳn.

| Vòng | Đường đi | Trần | Bộ đếm |
|---|---|---|---|
| Vòng spec | P7/P8/P9/P10 → P4 | 2 | `attempts.spec` |
| Vòng SQL | P7/P11/P12 → `sql_fix` → P6 | 3 | `attempts.sql` |
| Vòng người | P15 → P4 | không tính | đặt lại `attempts.spec = 0` |

### 11.4 Trần toàn cục

Trần gọi LLM **đọc từ `budgets.yml: per_run.max_llm_calls`**, không hardcode ở bất kỳ đâu. MVP đặt 6. Router và UI cùng đọc một nguồn, nên không thể lệch nhau.

Chạm trần → `NEEDS_HUMAN`, hiển thị toàn bộ `error_trace` lên UI. Một run thất bại có trace rõ ràng vẫn là sản phẩm tốt; một run treo vô hạn thì không.

---

## 12. Adapter warehouse

Trừu tượng để đổi DuckDB ↔ BigQuery chỉ là đổi một lớp.

```python
class WarehouseAdapter(Protocol):
    def dry_run(self, sql: str) -> DryRunResult:      # bytes_estimate, usd_estimate
    def execute(self, sql: str, max_bytes: int | None) -> QueryResult
    def explain(self, sql: str) -> PlanInfo           # wall_time, operators
    def get_schema(self, table: str) -> TableSchema
    def job_cost(self, run_id: str) -> CostInfo       # chi phí thật sau khi chạy
```

**DuckDB:** `dry_run` ước tính byte từ thống kê bảng (số dòng × kích thước dòng trung bình). `usd` quy đổi từ byte theo đơn giá BigQuery on-demand khai báo trong `budgets.yml: pricing.usd_per_tb`, và `DryRunResult.estimated = True` để UI gắn nhãn "ước tính". `explain` dùng `EXPLAIN ANALYZE`. `job_cost` trả cùng giá trị ước tính đó, cũng kèm `estimated = True`.

Lý do quy đổi thay vì trả 0: dải bằng chứng phải luôn hiện chi phí bằng tiền (`UI-SPEC.md` mục 8, nguyên tắc 10). Một ô trống ở mọi run trừ run BigQuery duy nhất sẽ làm nguyên tắc đó vô nghĩa. Nhãn "ước tính" giữ cho con số không bị hiểu nhầm là chi phí thật.

**BigQuery:** `dry_run` dùng `QueryJobConfig(dry_run=True)` → `total_bytes_processed`, quy ra USD theo đơn giá. `execute` đặt `maximum_bytes_billed`. Mọi job gắn label `run_id`. `job_cost` truy vấn `INFORMATION_SCHEMA.JOBS_BY_PROJECT` lọc theo label.

Phần lớn phát triển chạy trên DuckDB. BigQuery chỉ dùng cho cảnh demo số 2 và 4.

---

## 13. Template Jinja

Bốn template, mỗi cái nhận `spec` làm biến đầu vào.

| Template | Xuất ra | Nội dung chính |
|---|---|---|
| `dlt_pipeline.py.j2` | `ingestion/{table}_pipeline.py` | `@dlt.resource` với `primary_key`, `write_disposition`, `dlt.sources.incremental(cursor_field)`; `dlt.pipeline(destination=…)` |
| `dbt_model.sql.j2` | `dbt_project/models/gold/{model_name}.sql` | `{{ config(materialized=…, unique_key=…, partition_by=…) }}`, SELECT với `grain` + `metrics` + `filters`, khối `{% if is_incremental() %}` |
| `dbt_schema.yml.j2` | `dbt_project/models/gold/{model_name}.yml` | Khai báo cột và `tests` từ spec |
| `dagster_asset.py.j2` | `infra/dagster/generated/{model_name}_asset.py` | `@dlt_assets` cho E-L, `@dbt_assets` cho T, `ScheduleDefinition` từ `schedule.cron` |

**Nguyên tắc:** template phải render đúng cú pháp 100%. Nếu render ra file lỗi cú pháp, đó là bug template, không phải lỗi agent → `INTERNAL_ERROR`.

Cách làm đúng: viết tay một pipeline hoàn chỉnh chạy được **trước**, rồi thay các chỗ khác biệt bằng biến Jinja. Không viết template từ đầu.

---

## 14. Agent — LangGraph

### 14.1 State

```python
class AgentState(TypedDict):
    run_id: str
    trigger: str                   # user_request | manual_optimize | regression
    parent_run_id: str | None      # run tạo mới mà run này đang sửa
    regression: dict | None        # bằng chứng suy giảm, chỉ có khi trigger = regression
    user_request: str
    plan: dict | None
    schema_snapshot: dict          # từ resolve, KHÔNG do LLM sinh
    proposals: list[dict]
    spec: dict | None
    spec_history: list[dict]
    generated_files: dict[str, str]
    files_hash: str | None
    run_report: dict | None
    attempts: dict[str, int]
    llm_calls: int
    error_trace: list[dict]
    status: str
```

### 14.2 Bốn node LLM

| Node | Đầu vào | Đầu ra | Model |
|---|---|---|---|
| `planner` | yêu cầu + danh sách bảng (tên + mô tả ngắn) | kế hoạch JSON | rẻ |
| `codegen` | kế hoạch đã duyệt + schema thật + few-shot từ vector DB | 1–2 spec JSON | mạnh |
| `sql_fix` | Biểu thức SQL hiện tại + thông báo lỗi + schema thật | Biểu thức đã sửa | mạnh |
| `optimizer` | không dùng LLM ở MVP | — | — |

### 14.3 Quy tắc prompt

- Luôn kèm schema thật lấy từ `resolve`. **Không bao giờ để LLM tự nghĩ tên cột.**
- Yêu cầu trả về JSON thuần, không markdown fence. Nếu vẫn có fence thì strip trước khi parse.
- Few-shot lấy 3 spec đã duyệt gần nhất từ vector DB, chọn theo tương đồng với yêu cầu.
- Khi quay lại `propose` sau khi fail cổng, đưa **thông báo lỗi cụ thể** vào prompt, không đưa lời khuyên chung chung.

---

## 15. API

| Method | Path | Mô tả |
|---|---|---|
| `POST` | `/runs` | Tạo run mới từ yêu cầu, trả `run_id` |
| `GET` | `/runs/{id}/events` | SSE stream theo hợp đồng 6.3 |
| `GET` | `/runs/{id}` | RunReport đầy đủ |
| `POST` | `/runs/{id}/confirm-plan` | Cổng HITL 1 |
| `POST` | `/runs/{id}/approve` | Body: `{files_hash, reviewer}`. Từ chối nếu hash không khớp |
| `POST` | `/runs/{id}/reject` | Body: `{reason}` |
| `POST` | `/runs/{id}/request-changes` | Body: `{feedback}` → quay về `propose` |
| `GET` | `/policy` | Trả 3 file chính sách hiện hành |
| `GET` | `/runs` | Lịch sử, lọc theo status |

---

## 16. Frontend

Chi tiết màn hình, mức độ sâu và thứ tự dựng nằm ở `UI-SPEC.md`. Bảng dưới chỉ nêu màn mà hệ thống đích cần có.

| Màn | Nội dung |
|---|---|
| Gửi yêu cầu | Ô nhập, chọn domain, nút gạt vai trò Engineer/Reviewer |
| Theo dõi tiến trình | 6 nhóm phase (mục 9.0), bung ra thấy P0–P17, cập nhật realtime qua SSE, hiện ba bộ đếm vòng lặp |
| Review | Monaco DiffEditor (4 file) bên trái; bảng bằng chứng bên phải: gates, checks, cost, timing; 3 nút Duyệt / Từ chối / Yêu cầu sửa |
| Chính sách | Hiển thị 3 file YAML ở chế độ chỉ đọc |
| Lịch sử | Bảng run, ai duyệt, lúc nào, chi phí bao nhiêu |

Màn Review là màn quan trọng nhất. Bảng bằng chứng phải hiện con số cụ thể, ví dụ "Nguồn 12.480 dòng, bronze 12.480 dòng, khớp", "Chạy lần 2 không phát sinh dòng mới", "4,2 giây → 0,9 giây", "0,012 USD". Những con số này làm sản phẩm trông chín hơn bất kỳ hiệu ứng giao diện nào.

---

## 17. Yêu cầu dữ liệu và môi trường

### 17.1 Môi trường

| Thành phần | Vai trò | Ràng buộc |
|---|---|---|
| Hệ thống nguồn | Nơi agent đọc dữ liệu ra | Chỉ đọc; không có credential ghi |
| Sandbox | Nơi agent chạy thử | Mới cho mỗi run, xóa sau khi xong |
| Code location | Nơi deploy ghi file sau khi được duyệt | Tách khỏi prod |
| Prod | Ngoài tầm với của agent | Credential không tồn tại trong môi trường agent |

### 17.2 Yêu cầu với dữ liệu kiểm thử

Dữ liệu dùng để kiểm thử hệ thống phải chứa lỗi **có chủ đích**, và mỗi lỗi phải ánh xạ tới đúng một phép kiểm tra sẽ bắt được nó. Dữ liệu sạch làm tầng thẩm định không chứng minh được gì.

| Loại lỗi gieo vào | Phép bắt được | Phase |
|---|---|---|
| Khóa trùng giữa hai kênh | `hash_reconcile`, dbt test `unique` | P10, P12 |
| Khóa chính chọn sai gây nhân đôi | `idempotent` | P10 |
| Giá trị âm và NULL ở cột số | dbt test `not_negative`, `not_null` | P12 |
| Định dạng ngày lẫn lộn | `metric_reconcile` lệch | P12 |
| Thiếu dữ liệu một số ngày | Kiểm tra freshness | P12 |
| Thực thể cùng tên ở nhiều domain | `entity_scan` | P3 |
| Bảng đủ lớn để vượt trần byte | `dry_run` | P8 |

Hai dòng cuối là dữ liệu tồn tại **chỉ để kiểm chứng cổng ngữ nghĩa và cổng ngân sách**. Thiếu chúng thì hai cổng quan trọng nhất không có ca kiểm thử.

Bộ seed cụ thể cho MVP nằm ở `SCOPE.md`.

---

## 18. Đo lường

### 18.1 Nguyên tắc

Tính đúng của pipeline đo bằng **so kết quả từng dòng** với golden output, không so chuỗi SQL — hai câu SQL khác nhau vẫn có thể cùng kết quả.

Tính đúng của hệ thống điều khiển đo bằng **tỷ lệ chặn đúng** trên tập ca cố tình vi phạm chính sách. Đây là chỉ số phản ánh đúng thứ sản phẩm hứa hẹn, nên tập eval bắt buộc có ca vi phạm cho từng loại cổng.

### 18.2 Cấu trúc tập eval

`eval/cases/` chứa các ca dạng `{request, expected_outcome, golden_result?}`, chia ba nhóm:

- **Hợp lệ** — có `golden_result` viết tay, dùng đo tính đúng
- **Vi phạm** — mong đợi một mã `BLOCKED_*` cụ thể, dùng đo tỷ lệ chặn đúng, phủ cả ba cổng `semantic`, `budget`, `ast_guard`
- **Mơ hồ** — mong đợi agent hỏi lại ở P1 thay vì đoán

### 18.3 Nhóm chỉ số

| Nhóm | Chỉ số |
|---|---|
| Tính đúng | Tỷ lệ chạy được lần đầu; sau tự sửa; tỷ lệ khớp golden output |
| Kiểm soát | Tỷ lệ chặn đúng; số lần tự sửa trung bình |
| Chi phí | Sai số giữa ước tính `dry_run` và chi phí thật |
| Hiệu năng | Thời gian chạy trước và sau khi optimizer can thiệp |
| Baseline | Thời gian từ yêu cầu tới pipeline chạy được, so với người viết tay |

Số lượng ca, giá trị mục tiêu và cách phát biểu kết quả nằm ở `SCOPE.md`.

---

## 19. Bất biến hệ thống

Viết thành test trong `tests/test_invariants.py`:

1. Sandbox là file DuckDB / dataset BigQuery **mới cho mỗi run**, xoá sau khi xong
2. Connection string tới prod **không tồn tại** trong môi trường agent
3. Mọi file deploy truy vết được về một `run_id` và một chữ ký phê duyệt
4. Không node LLM nào thực thi lệnh shell
5. Mọi phiên bản spec đều được lưu trong `spec_history`
6. `approval.hash == files_hash` là điều kiện bắt buộc của `deploy`
7. `llm_calls <= budgets.per_run.max_llm_calls` với mọi run. Không hardcode con số ở bất kỳ đâu

---

