# Sơ đồ kiến trúc — Atomesus (P-045)

> Cập nhật 2026-10-18 theo code trên `main` (bản production `1072104`, https://c4-app-045.id.vn).
> Sơ đồ viết bằng Mermaid, GitHub hiển thị trực tiếp. Mô tả chữ từng thành phần ở [ARCHITECTURE.md](../ARCHITECTURE.md);
> quyết định thiết kế ở [ADR-0001](adr/0001-mot-luong-va-nen-production.md); đặc tả gốc ở [SPEC.md](../SPEC.md).

Mục lục

1. [Ngữ cảnh hệ thống](#1-ngữ-cảnh-hệ-thống)
2. [Các thành phần bên trong](#2-các-thành-phần-bên-trong)
3. [Triển khai production](#3-triển-khai-production)
4. [Agent LangGraph](#4-agent-langgraph)
5. [Luồng end-to-end: yêu cầu → production](#5-luồng-end-to-end-yêu-cầu--production)
6. [Sandbox và đối chiếu độc lập](#6-sandbox-và-đối-chiếu-độc-lập)
7. [Máy trạng thái của run](#7-máy-trạng-thái-của-run)
8. [Deploy: hộp thư release, deployer và GitOps](#8-deploy-hộp-thư-release-deployer-và-gitops)
9. [Ranh giới bảo mật](#9-ranh-giới-bảo-mật)
10. [Bảng tra module](#10-bảng-tra-module)

---

## 1. Ngữ cảnh hệ thống

Ai dùng hệ thống, hệ thống nói chuyện với bên ngoài nào.

```mermaid
flowchart LR
    Eng(["Engineer<br/>mô tả nhu cầu dữ liệu"])
    Rev(["Reviewer<br/>xem bằng chứng, duyệt, Deploy"])
    Adm(["Admin<br/>quản lý tài khoản, xem chính sách"])

    subgraph Atomesus["Atomesus — c4-app-045.id.vn"]
        Sys["Pipeline Studio<br/>web + API + agent + sandbox<br/>+ Dagster production"]
    end

    LLM[["OpenAI API<br/>gpt-4o-mini / gpt-4o"]]
    Src[("Nguồn dữ liệu<br/>Postgres hệ thống, Postgres của người dùng,<br/>REST API, file CSV/XLSX tải lên")]
    GH[["GitHub<br/>repo ducthais/atomesus-pipelines<br/>(PR lưu vết, CI, tự merge)"]]
    Blob[["Azure Blob Storage<br/>metadata Parquet để đo byte quét"]]

    Eng -->|"yêu cầu bằng lời, chọn bảng,<br/>xác nhận kế hoạch, sửa mapping"| Sys
    Rev -->|"duyệt diff + bằng chứng, Deploy"| Sys
    Adm -->|"tài khoản, chính sách"| Sys
    Sys -->|"lập kế hoạch, viết Spec<br/>(không gửi giá trị dữ liệu mẫu)"| LLM
    Sys -->|"đọc chỉ đọc"| Src
    Sys -->|"mở PR chứa bộ file đã duyệt"| GH
    Sys -->|"đọc kích thước"| Blob
```

Nguyên tắc xuyên suốt: **LLM chỉ viết Spec (JSON có kiểu), không viết code**. Code pipeline sinh tất định từ template;
chạy thử trên dữ liệu thật trong sandbox; người (Reviewer khác Engineer) duyệt bộ file có hash trước khi lên production.

---

## 2. Các thành phần bên trong

```mermaid
flowchart TB
    subgraph Web["Web — Next.js 16 / React 19 (web/)"]
        Landing["Landing page /"]
        Catalog["Data Catalog<br/>/explore /domains /relationships /glossary /datasets"]
        Studio["Pipeline Studio<br/>/runs/new · /runs/[id] (kế hoạch, mapping, diff, bằng chứng, chat)"]
        Review["Duyệt<br/>/runs/review (hàng loạt) · tóm tắt màn duyệt · Deploy"]
        Ops["Vận hành<br/>/monitor · /results/[model] (xem, tải CSV/XLSX)"]
        Account["Tài khoản<br/>/connections · /reviewers · /invite · /admin/users · /policy"]
    end

    subgraph API["Backend — FastAPI (src/), prefix /api/v1"]
        Auth["pipeline/auth.py<br/>phiên cookie, vai, hạn mức"]
        AgentAPI["api/routes.py<br/>tạo run, xác nhận kế hoạch, hàng loạt"]
        RunsAPI["run_report/router.py<br/>duyệt, từ chối, yêu cầu sửa, Deploy"]
        CatalogAPI["api/catalog.py · policy.py<br/>results.py · monitor.py"]
        ConnAPI["pipeline/connections.py · uploads.py<br/>Postgres người dùng, file tải lên"]
        Chat["chat/<br/>hỏi đáp, sửa pipeline bằng chat"]
        Hooks["api/webhooks.py<br/>webhook GitHub, callback deployer"]
        Queue["Hàng đợi chạy thử<br/>(giới hạn sandbox song song)"]
    end

    subgraph Agent["Agent — LangGraph (src/agents/)"]
        Graph["graph.py<br/>planner → semantic → codegen → budget → arbitrate<br/>→ render → test_run → repair / optimize"]
        Policy["policy/<br/>domains.yml · budgets.yml · objectives.yml"]
    end

    subgraph Codegen["Sinh code và chạy thử (src/codegen/)"]
        Render["render.py + templates/<br/>Spec → 4 + N file"]
        Guard["guard.py<br/>AST guard (sqlglot)"]
        Sandbox["sandbox.py<br/>dlt + dbt-duckdb trên dữ liệu thật"]
        Reference["reference.py<br/>SQL đối chiếu độc lập"]
        Deploy["deploy.py<br/>bundle release · PR GitHub"]
    end

    Store[("Sổ bằng chứng SQLite<br/>RunReport, file theo version,<br/>phiên, người dùng, kết nối mã hóa")]

    subgraph Prod["Production data plane"]
        Inbox[/"Hộp thư release<br/>data/releases/inbox"/]
        Deployer["deployer/<br/>kiểm manifest + sha256, cài, chạy thử"]
        Dagster["Dagster webserver + daemon<br/>code location hợp nhất (src/dagster_runtime)"]
        Gold[("DuckDB<br/>&lt;model&gt;.duckdb mỗi pipeline")]
    end

    Web -->|"REST /api/v1 (rewrite qua Next)"| API
    AgentAPI --> Queue --> Graph
    Graph --> Policy
    Graph --> Render --> Guard
    Graph --> Sandbox --> Reference
    API --> Store
    Graph --> Store
    RunsAPI --> Deploy --> Inbox --> Deployer --> Dagster --> Gold
    Deployer -->|"callback ký HMAC"| Hooks
    CatalogAPI --> Gold
```

---

## 3. Triển khai production

Một VM **Azure B2s** (2 vCPU, 4 GB RAM) chạy toàn bộ bằng `deploy/compose.yml`. Tên miền ở Tenten, HTTPS do Caddy tự
cấp (Let's Encrypt). Deploy bằng `scripts/harness.py deploy --yes`: chỉ commit đã có gate xanh trên `origin/main`, sau
đó smoke test, đỏ thì quay về bản trước.

```mermaid
flowchart TB
    User(["Trình duyệt"]) -->|"HTTPS 443"| Caddy
    GHW(["GitHub webhook"]) -->|"HTTPS /api/v1/webhooks/github"| Caddy

    subgraph VM["Azure VM B2s · Ubuntu · Docker Compose (deploy/compose.yml)"]
        Caddy["caddy:2<br/>HSTS, X-Frame-Options, nosniff<br/>c4-app-045.id.vn → web<br/>dagster.c4-app-045.id.vn → dagster (basic auth)"]
        WebC["web<br/>Next.js :3000<br/>rewrite /api/v1 → backend"]
        BackC["backend<br/>FastAPI :8000<br/>đọc ~/atomesus/.env (OpenAI, GitHub, khóa kết nối)"]
        DagC["dagster<br/>:3070 webserver + daemon<br/>KHÔNG đọc .env, chỉ biến được liệt kê"]
        DepC["deployer<br/>scripts/deployer.py<br/>chỉ PIPELINE_DEPLOYER_SECRET"]
        PgC[("postgres<br/>sales_raw + pagila<br/>user pipeline_reader chỉ SELECT")]
        MockC["mock-api :8100<br/>REST sales_api, mkt_api, review_api"]

        Data[("~/atomesus/data<br/>runs.sqlite3 · releases/ · dagster_home/ · warehouse/")]
        Vol[("volume dagster_code<br/>(deployer ghi, dagster đọc)")]
    end

    Caddy --> WebC --> BackC
    Caddy --> DagC
    BackC --> PgC
    BackC --> MockC
    BackC --> Data
    BackC -->|"ghi bundle"| Data
    DepC -->|"đọc inbox, chuyển done/rejected"| Data
    DepC -->|"cài code location"| Vol
    DagC --> Vol
    DagC -->|"chạy pipeline theo lịch"| PgC
    DagC --> MockC
    DepC -->|"GraphQL: chạy thử job, reload location"| DagC
    DepC -->|"callback HMAC"| BackC

    BackC -->|"HTTPS"| OpenAI[["OpenAI API"]]
    BackC -->|"HTTPS: tạo nhánh + PR"| GitHub[["github.com/ducthais/atomesus-pipelines"]]
    BackC -->|"Postgres người dùng<br/>(IP egress cố định của VM)"| UserPG[("Postgres của người dùng")]
```

| Service | Ảnh | Vai trò | Bí mật được thấy |
|---|---|---|---|
| `caddy` | `caddy:2` | TLS, header bảo mật, basic auth cho Dagster | hash mật khẩu Dagster |
| `web` | `atomesus-web` | Next.js, render server, rewrite `/api/v1` | không |
| `backend` | `atomesus-app` | API, agent, sandbox, sổ bằng chứng | toàn bộ `.env` (OpenAI, GitHub, khóa Fernet kết nối) |
| `dagster` | `atomesus-app` | chạy pipeline đã deploy theo lịch | chỉ URL Postgres `pipeline_reader` và biến liệt kê |
| `deployer` | `atomesus-deployer` | cài bundle đã duyệt vào Dagster | chỉ secret ký callback |
| `postgres` | `atomesus-postgres` | nguồn mẫu `sales_raw`, `pagila` | mật khẩu admin (không lộ ra service khác) |
| `mock-api` | `atomesus-app` | nguồn REST mẫu | không |

---

## 4. Agent LangGraph

`src/agents/graph.py`. Node LLM chỉ có `planner` (model rẻ) và `codegen` (model mạnh); các node còn lại tất định. Mọi
node chạy dưới trần thời gian `budgets.yml: per_run.phase_timeout_sec`.

```mermaid
flowchart TD
    Start(["Run mới"]) --> planner
    planner["planner · P1 · LLM rẻ<br/>nguồn, join, độ hạt, chỉ số, lịch"] --> semantic
    semantic{"semantic · P3<br/>từ hai nghĩa ở hai domain?"}
    semantic -- "có" --> BSC(["BLOCKED_SEMANTIC_CONFLICT"])
    semantic -- "không" --> P2{{"Cổng người P2<br/>Engineer xác nhận / sửa kế hoạch"}}
    P2 -- "xác nhận (entry=codegen)" --> codegen
    codegen["codegen · P3–P4 · LLM mạnh<br/>schema thật + profile (không giá trị mẫu) → Spec"]
    codegen -- "cổng Spec lỗi, còn lượt" --> codegen
    codegen -- "vượt max_spec_attempts" --> NH1(["NEEDS_HUMAN"])
    codegen -- "AST guard" --> BAST(["BLOCKED_AST"])
    codegen -- "Spec hợp lệ" --> budget
    budget{"budget · P8<br/>byte quét, USD run / ngày / người"}
    budget -- "vượt trần" --> BB(["BLOCKED_BUDGET"])
    budget --> arbitrate["arbitrate · P5<br/>lịch rẻ nhất vẫn đủ tươi (objectives.yml)"]
    arbitrate --> render["render · P6<br/>Spec → 4 + N file từ templates/"]
    render --> test_run["test_run · P9–P12<br/>dlt + dbt trong sandbox, đối chiếu, RunReport"]
    test_run -- "đạt" --> optimize["optimize · P13<br/>thử luật tối ưu, giữ bản vừa đúng vừa nhanh hơn"]
    optimize --> RFR(["READY_FOR_REVIEW"])
    test_run -- "không đạt" --> repair{"repair · §11.2<br/>bảng định tuyến (router.py)"}
    repair -- "vòng spec" --> codegen
    repair -- "vòng SQL (sql_fix)" --> codegen
    repair -- "hết lượt / lỗi hạ tầng" --> NH2(["NEEDS_HUMAN / INFRA_ERROR"])
```

**Các cổng kiểm Spec** (`GateName` trong `src/run_report/models.py`): `spec_schema`, `schema_real`, `currency`,
`join_fanout`, `time_grain`, `filter_values`, `semantic`, `ast_guard`, `budget`, `arbitrate`, `post_run`.

**Trần mỗi run** (`policy/budgets.yml`): 6 lần gọi LLM, 2 vòng sửa Spec, 2 vòng sửa SQL, 0,05 USD, 5 GB quét; mỗi ngày
2 USD toàn hệ thống; mỗi người 0,30 USD và 20 run/ngày.

**UC-3 Tối ưu thủ công** (`src/agents/manual_optimize.py`): chạy lại optimizer trên pipeline có sẵn, `llm_calls = 0`;
không có bản tốt hơn thì kết thúc `NO_IMPROVEMENT`.

---

## 5. Luồng end-to-end: yêu cầu → production

```mermaid
sequenceDiagram
    autonumber
    actor Eng as Engineer
    participant Web as Web (Next.js)
    participant API as Backend (FastAPI)
    participant Ag as Agent (LangGraph)
    participant LLM as OpenAI
    participant SB as Sandbox (dlt + dbt-duckdb)
    participant Src as Nguồn (Postgres / REST / file)
    actor Rev as Reviewer
    participant Dep as Deployer
    participant Dag as Dagster production
    participant GH as GitHub (repo pipeline)

    Eng->>Web: Chọn bảng, nhập yêu cầu bằng lời
    Web->>API: POST /api/v1/agent/runs
    API->>Ag: planner (P1) + semantic (P3)
    Ag->>LLM: metadata schema (không giá trị dữ liệu)
    Ag-->>Web: Kế hoạch, trạng thái DRAFT (stage confirm)
    Eng->>Web: Xác nhận / sửa kế hoạch (P2)
    Web->>API: confirm → PLAN_CONFIRMED
    API->>Ag: codegen → cổng Spec → budget → arbitrate → render
    Ag->>LLM: schema thật + profile cột
    Ag->>SB: chạy 4 + N file trong thư mục tạm
    SB->>Src: dlt đọc bằng tài khoản chỉ đọc
    SB->>Src: SQL đối chiếu độc lập
    SB-->>Ag: checks: row / hash / staging / dbt_test / metric / idempotent
    Ag->>SB: optimize (P13): đo, giữ bản nhanh hơn nếu kết quả giống hệt
    Ag-->>API: RunReport READY_FOR_REVIEW + files_hash
    Rev->>Web: Xem tóm tắt, diff, bảng gold, bằng chứng
    Rev->>API: approve (files_hash) → APPROVED
    Rev->>API: Deploy
    API->>API: kiểm quyền, chữ ký, giữ tên model
    API->>Dep: ghi bundle bất biến vào hộp thư release
    API--)GH: (chạy nền) mở PR chứa đúng bộ file
    Dep->>Dep: kiểm manifest + sha256 từng file
    Dep->>Dag: cài code location, reload, chạy thử 1 lần
    Dag-->>Dep: SUCCESS / lỗi
    Dep->>API: callback HMAC → DEPLOYED (hoặc DEPLOY_FAILED, giữ bản cũ)
    GH->>GH: CI pipeline-ci kiểm manifest + dbt parse → job auto-merge
    Dag->>Src: chạy theo lịch, ghi <model>.duckdb
```

---

## 6. Sandbox và đối chiếu độc lập

Bằng chứng trước khi duyệt không dựa vào chính pipeline: kết quả gold được tính lại bằng một đường khác.

```mermaid
flowchart LR
    Spec["Spec đã kiểm"] --> R["render<br/>4 + N file"]
    R --> P1
    Spec --> Ref["reference.py<br/>dịch Spec thành SQL độc lập"]

    subgraph P1["Đường pipeline (sandbox mới mỗi run)"]
        direction LR
        dlt["dlt<br/>nguồn → bronze"] --> stg["dbt staging<br/>ép kiểu, luật loại dòng,<br/>khử trùng, tách rejected"] --> gold["dbt gold<br/>gộp theo độ hạt"]
    end

    subgraph P2["Đường đối chiếu"]
        Ref --> Run2["chạy trực tiếp trên nguồn<br/>(Postgres) hoặc tính lại từ bronze<br/>(REST API, file)"]
    end

    gold --> Cmp{"so từng dòng"}
    Run2 --> Cmp
    Cmp -->|"khớp"| OK["metric_reconcile = pass"]
    Cmp -->|"lệch"| Bad["repair → vòng spec / SQL<br/>hoặc NEEDS_HUMAN"]

    dlt -.-> C1["row_reconcile<br/>hash_reconcile<br/>idempotent (chạy lại không đổi)"]
    stg -.-> C2["staging_reconcile<br/>lý do + dòng bị loại mẫu"]
    gold -.-> C3["dbt_test<br/>not_null, unique, not_negative"]
```

Sandbox là thư mục tạm + `warehouse.duckdb` riêng mỗi run, xóa khi xong; tiến trình dlt/dbt không nhận API key LLM hay
credential production (bất biến 1 và 2, `tests/test_invariants.py`).

---

## 7. Máy trạng thái của run

`RunStatus` trong `src/run_report/models.py`.

```mermaid
stateDiagram-v2
    [*] --> GENERATING: tạo run
    GENERATING --> DRAFT: kế hoạch xong (chờ P2) / Spec xong (chế độ sửa mapping)
    DRAFT --> PLAN_CONFIRMED: Engineer xác nhận
    PLAN_CONFIRMED --> GENERATING
    DRAFT --> GENERATING: chạy thử với mapping
    GENERATING --> VALIDATING: Spec qua cổng, đang chạy thử
    VALIDATING --> READY_FOR_REVIEW: chạy thử + đối chiếu đạt
    GENERATING --> BLOCKED_SEMANTIC_CONFLICT
    GENERATING --> BLOCKED_BUDGET
    GENERATING --> BLOCKED_AST
    VALIDATING --> NEEDS_HUMAN: hết lượt sửa
    GENERATING --> NEEDS_HUMAN: mơ hồ, cần hỏi lại
    GENERATING --> INFRA_ERROR: nguồn / LLM không gọi được
    GENERATING --> CANCELLED: Engineer hủy
    READY_FOR_REVIEW --> APPROVED: Reviewer duyệt (files_hash)
    READY_FOR_REVIEW --> REJECTED: Reviewer từ chối
    READY_FOR_REVIEW --> GENERATING: yêu cầu sửa (version mới)
    APPROVED --> DEPLOYED: deployer chạy thử SUCCESS
    APPROVED --> DEPLOY_FAILED: manifest sai / chạy thử lỗi (giữ bản cũ)
    VALIDATING --> NO_IMPROVEMENT: tối ưu thủ công không có bản tốt hơn
    DEPLOYED --> [*]
```

Engineer tạo run không được tự duyệt run đó (bất biến 3, ADR-0001 §3). Duyệt chỉ ghi chữ ký gắn với `files_hash` và
`version`; đổi file nào là chữ ký mất hiệu lực.

---

## 8. Deploy: hộp thư release, deployer và GitOps

Chế độ mặc định `PIPELINE_DEPLOY_MODE=direct` (ADR-0001 D6).

```mermaid
flowchart LR
    subgraph Backend
        D["POST /runs/{id}/deploy<br/>Reviewer hoặc Admin"] --> K{"APPROVED?<br/>chữ ký khớp files_hash + version?<br/>tên model còn trống?"}
        K -- "không" --> E409(["409, không ghi gì"])
        K -- "có" --> W["write_bundle<br/>files + manifest.json + run_report.json<br/>ghi tạm rồi rename (nguyên tử)"]
        K -- "có, nếu có PIPELINE_GITHUB_*" --> PR["mở PR chạy nền<br/>(lỗi chỉ ghi log)"]
    end

    W --> Inbox[/"data/releases/inbox/&lt;run_id&gt;-v&lt;version&gt;"/]

    subgraph Deployer["deployer (mỗi 60 giây)"]
        Inbox --> V{"manifest + sha256<br/>từng file khớp?"}
        V -- "không" --> Rej["rejected/ → DEPLOY_FAILED"]
        V -- "có" --> I["cài vào dagster_code<br/>(giữ bản trước)"] --> T["reload location,<br/>chạy thử job 1 lần"]
        T -- "SUCCESS" --> Done["done/ → callback DEPLOYED<br/>lịch chạy tự bật"]
        T -- "lỗi / quá hạn" --> RB["khôi phục bản trước<br/>→ DEPLOY_FAILED"]
    end

    PR --> GH["Repo pipeline<br/>nhánh pipeline/&lt;model&gt;/&lt;run&gt;-v&lt;n&gt;"]
    GH --> CI{"pipeline-ci<br/>check_pr.py: chỉ đổi pipelines/&lt;model&gt;/,<br/>manifest khớp chữ ký · dbt parse"}
    CI -- "xanh" --> AM["job auto-merge<br/>squash, --match-head-commit"] --> Main["main của repo pipeline<br/>= lịch sử bản đã deploy"]
    CI -- "đỏ" --> Hold["PR nằm lại"]
```

Chế độ `pr` (tùy chọn) bỏ hộp thư: deployer kéo `main` của repo pipeline bằng deploy key chỉ đọc và chỉ cài sau khi PR
được merge.

---

## 9. Ranh giới bảo mật

```mermaid
flowchart TB
    subgraph Trust0["Internet"]
        U["Người dùng"]
    end
    subgraph Trust1["Biên: Caddy"]
        TLS["TLS + HSTS + header<br/>basic auth cho Dagster"]
    end
    subgraph Trust2["Ứng dụng"]
        Sess["Cookie phiên HttpOnly, Secure, SameSite<br/>giới hạn đăng nhập sai (429)"]
        RBAC["Vai Engineer / Reviewer / Admin<br/>mỗi người chỉ thấy run của mình<br/>Engineer ≠ Reviewer của cùng run"]
        Quota["Hạn mức mỗi người: USD/ngày, run/ngày,<br/>dung lượng file, số dòng tải xuống"]
    end
    subgraph Trust3["Agent và sandbox"]
        NoCode["LLM chỉ trả Spec JSON<br/>không có quyền shell (bất biến 4)"]
        AST["AST guard: chỉ hàm trong danh sách cho phép,<br/>cấm Jinja, ;, DDL/DML"]
        Iso["Sandbox mới mỗi run<br/>không có key LLM / credential production"]
    end
    subgraph Trust4["Production data plane"]
        Sig["Chỉ cài bundle có chữ ký Reviewer + sha256 khớp"]
        RO["Nguồn đọc bằng tài khoản chỉ SELECT<br/>Postgres người dùng: mật khẩu mã hóa Fernet,<br/>chặn địa chỉ mạng nội bộ"]
        NoEnv["dagster / deployer không đọc .env"]
    end
    U --> TLS --> Sess --> RBAC --> Quota --> NoCode --> AST --> Iso --> Sig --> RO
    Sig --> NoEnv
```

---

## 10. Bảng tra module

| Thư mục | Nội dung chính |
|---|---|
| `src/agents/` | Graph LangGraph, node (`nodes/`), prompt, router sửa lỗi, optimizer P13, tối ưu thủ công, ngân sách, semantic |
| `src/spec/` | Hợp đồng Spec (Pydantic + JSON Schema), các cổng kiểm |
| `src/codegen/` | Render template, AST guard, sandbox, SQL đối chiếu, bundle release, client GitHub |
| `src/run_report/` | Hợp đồng `RunReport`, sổ bằng chứng SQLite, quyền xem, duyệt / từ chối / Deploy |
| `src/pipeline/` | Phiên đăng nhập, người dùng, kết nối Postgres người dùng, file tải lên, nguồn REST |
| `src/api/` | Router catalog, chính sách, kết quả, theo dõi, webhook GitHub / callback deployer |
| `src/deployer/` | Service deployer: hộp thư release, kiểm manifest, cài + chạy thử + rollback |
| `src/dagster_runtime/` | Code location hợp nhất nạp mọi pipeline đã cài, cấp credential theo nguồn |
| `src/monitor/` | Số đo mỗi lần chạy theo lịch, cảnh báo Day 2 (chậm, nguồn ngừng cấp) |
| `src/chat/` | Chat trên run: hỏi đáp, sửa pipeline bằng lời |
| `src/policy/` | Đọc `policy/*.yml`, chính sách hiệu lực theo người dùng |
| `templates/` | Jinja (`[[ ]]`) sinh dlt, dbt staging/gold, test, asset Dagster |
| `web/` | Next.js: catalog, studio, duyệt, theo dõi, tài khoản |
| `deploy/` | `compose.yml`, Caddyfile, script dựng VM, bản mẫu repo pipeline |
| `eval/` | Bộ chấm 20 ca, golden, dữ liệu bench 3 triệu dòng |
