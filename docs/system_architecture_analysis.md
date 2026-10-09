# Phân Tích Luồng Hoạt Động & Sơ Đồ Hệ Thống Atomesus (P-045)

> **Tài liệu phân tích kiến trúc hệ sinh thái ELT Agent — Dự án VinUni AI20K (Đội P-045)**  
> *Phiên bản: 1.0 — Cập nhật: 2026-10-03*

---

## 1. Giới thiệu tổng quan

**Atomesus** là nền tảng AI-Native ELT Pipeline Platform giải quyết bài toán tự động hoá xây dựng đường ống dữ liệu (Data Pipeline) từ yêu cầu nghiệp vụ bằng ngôn ngữ tự nhiên. 

Khác biệt với các công cụ "AI sinh SQL tự do", Atomesus hoạt động theo các nguyên tắc kỹ thuật cốt lõi:
1. **Agent ràng buộc (Constrained AI Agent)**: LLM chỉ chịu trách nhiệm phân tích schema, lập kế hoạch và sinh đặc tả kỹ thuật (Spec JSON). Mã nguồn thực thi (dlt, dbt, Dagster) được sinh hoàn toàn tất định qua Jinja2 template.
2. **Sandbox cô lập (Isolated Execution Sandbox)**: Mọi pipeline sinh ra được nạp dữ liệu thật và chạy thử ngay trong container/sandbox DuckDB riêng biệt, có cơ chế dọn dẹp tài nguyên tự động.
3. **Đối chiếu độc lập (Independent Reconciliation - `metric_reconcile`)**: Kết quả của pipeline (đường `dlt -> DuckDB -> dbt`) được so khớp từng dòng với một câu SQL Postgres thuần được dịch độc lập và chạy trực tiếp trên nguồn dữ liệu gốc. Mọi sai lệch (múi giờ, làm tròn, ép kiểu, mất dòng) đều bị phát hiện trước khi đến tay người duyệt.
4. **Phân quyền và ký duyệt bảo mật (Human-in-the-Loop & Cryptographic Review)**: Kỹ sư (Engineer) tạo và chỉnh sửa mapping; Người duyệt (Reviewer) xem xét diff mã nguồn, bảng kết quả và bằng chứng đối chiếu. Quyết định duyệt gắn liền với mã băm mật mã `files_hash` của phiên bản được xem.

---

## 2. Sơ đồ 1: Tổng quan kiến trúc hệ thống (High-Level Architecture)

Hệ thống được tổ chức thành 4 phân tầng ranh giới rõ ràng:

```mermaid
graph TB
    subgraph UI_Layer["Tầng Giao Diện (Frontend - Next.js 16 / Port 3000)"]
        UI_Studio["Pipeline Studio (Catalog, ERD, Mapping Editor)"]
        UI_Run["Run Monitor & Diff Viewer (Monaco Editor)"]
        UI_Review["Reviewer Approval Console"]
    end

    subgraph Backend_Layer["Tầng Điều Khiển & Nghiệp Vụ (FastAPI / Port 8000)"]
        API_Gateway["API Routes (/agent, /runs, /catalog, /policy)"]
        Auth_RBAC["Session & RBAC (Engineer vs. Reviewer)"]
        Evidence_Store[("Evidence Store / Sổ Bằng Chứng<br/>SQLite: data/pipeline/runs.sqlite3")]
        Policy_Engine["Policy Loader (domains.yml, budgets.yml, objectives.yml)"]
    end

    subgraph Agent_Core["Tầng Lõi AI Agent (LangGraph StateGraph)"]
        Planner["P1: Planner Node<br/>(LLM Rẻ: gpt-4o-mini)"]
        CodeGen["P3-P4: Codegen Node<br/>(LLM Mạnh: gpt-4o / gpt-4o-mini)"]
        Validator{"Cổng Kiểm Tra Spec<br/>(Schema, Real Columns, AST Guard)"}
        Renderer["P6: Deterministic Renderer<br/>(Jinja2 Engine)"]
    end

    subgraph Sandbox_Layer["Tầng Sandbox Thực Thi (Isolated Data Plane)"]
        DuckDB_Sandbox[("DuckDB Tạm Thời<br/>(warehouse.duckdb)")]
        DLT_Runner["dlt Engine (Postgres -> DuckDB Bronze)"]
        DBT_Runner["dbt-duckdb (Bronze -> Staging -> Gold)"]
        Reconcile_Engine["Independent Reconciliation Engine<br/>(Direct SQL on Postgres)"]
    end

    subgraph External_Storage["Tầng Dữ Liệu & Vận Hành Ngoài"]
        Postgres_Source[("PostgreSQL Nguồn (Port 5433)<br/>sales_raw, pagila")]
        Dagster_Orch["Dagster Orchestration (Port 3070)<br/>DbtAssets, DltAssets, Schedules"]
    end

    %% Tương tác
    UI_Studio -->|REST API & Cookie Auth| API_Gateway
    UI_Run -->|Poll trạng thái & files| API_Gateway
    UI_Review -->|Ký duyệt files_hash| API_Gateway

    API_Gateway --> Auth_RBAC
    API_Gateway --> Evidence_Store
    API_Gateway --> Agent_Core
    Policy_Engine -.-> Agent_Core

    Planner --> CodeGen
    CodeGen --> Validator
    Validator -- Hợp lệ --> Renderer
    Validator -- Lỗi --> CodeGen

    Renderer --> Sandbox_Layer
    DLT_Runner -->|Đọc bảng nguồn| Postgres_Source
    DLT_Runner -->|Tải vào| DuckDB_Sandbox
    DBT_Runner -->|Biến đổi dữ liệu| DuckDB_Sandbox
    Reconcile_Engine -->|Chạy SQL đối chiếu trực tiếp| Postgres_Source
    Reconcile_Engine <-->|So khớp từng dòng| DuckDB_Sandbox

    API_Gateway -->|Deploy sau khi Reviewer duyệt| Dagster_Orch
```

---

## 3. Sơ đồ 2: Luồng xử lý End-to-End từ Yêu cầu đến Triển khai

Quy trình tuần tự từ lúc tiếp nhận yêu cầu kinh doanh bằng ngôn ngữ tự nhiên đến khi triển khai lịch chạy định kỳ:

```mermaid
sequenceDiagram
    autonumber
    actor Eng as Kỹ sư dữ liệu (Engineer)
    participant Web as Next.js Web Studio
    participant API as FastAPI Backend
    participant Agent as LangGraph Agent
    participant Sand as DuckDB Sandbox
    participant PG as PostgreSQL Nguồn
    actor Rev as Người duyệt (Reviewer)
    participant Dag as Dagster Engine

    %% Bước 1: Khởi tạo yêu cầu
    Eng->>Web: Nhập prompt: "Doanh thu theo từng khách hàng" & Chọn nguồn (sales_pg)
    Web->>API: POST /api/v1/agent/drafts (Chế độ tạo bản nháp)
    API->>Agent: Kích hoạt Agent Background Job
    API-->>Web: Trả về RunReport trạng thái GENERATING

    %% Bước 2: Agent lập kế hoạch & viết Spec
    Note over Agent: Node Planner: Đọc metadata nguồn -> Lập kế hoạch JSON
    Note over Agent: Node Codegen: Đọc schema thật + 20 dòng mẫu -> Viết Spec
    Note over Agent: Kiểm tra Spec qua các cổng (Schema, Cột thật, AST Guard)
    Agent->>API: Lưu trạng thái DRAFT vào SQLite Store
    Web->>API: Poll trạng thái Run -> Nhận bản DRAFT
    Web->>Eng: Hiển thị giao diện Mapping (Cột, ép kiểu, bộ lọc, aggregate)

    %% Bước 3: Engineer xác nhận và chạy thử
    Eng->>Web: Xem xét hoặc điều chỉnh mapping -> Bấm "Chạy thử"
    Web->>API: POST /api/v1/agent/runs/{id}/continue
    API->>Agent: Xác thực lại Spec -> Render Template Jinja2 ra file mã nguồn
    Note over Agent: Sinh 4+N file (pipeline.py, stg.sql, fct.sql, schema.yml, asset.py)

    %% Bước 4: Chạy thử trong Sandbox
    Agent->>Sand: Tạo thư mục tạm & warehouse.duckdb riêng
    Sand->>PG: dlt đọc dữ liệu thật từ bảng nguồn
    PG-->>Sand: Bronze tables
    Note over Sand: dbt build: Bronze -> Staging (khử trùng, tách reject) -> Gold (tính toán)
    Note over Sand: Chạy dbt test (unique, not_null, not_negative)

    %% Bước 5: Đối chiếu độc lập
    Sand->>PG: Chạy 1 câu SQL độc lập (sinh từ Spec) trực tiếp trên Postgres
    PG-->>Sand: Kết quả tham chiếu (Reference Gold)
    Note over Sand: So khớp từng dòng giữa DuckDB Gold và Postgres Reference SQL

    Sand->>API: Ghi toàn bộ bằng chứng vào RunReport (status = READY_FOR_REVIEW)
    API-->>Web: RunReport kèm bảng kết quả thật & bằng chứng đối chiếu

    %% Bước 6: Reviewer duyệt
    Rev->>Web: Xem RunReport, Diff file sinh ra, Bằng chứng đối chiếu
    Rev->>API: POST /api/v1/runs/{id}/approve (Kèm files_hash đã xem)
    API->>API: Kiểm tra RBAC (Không được tự duyệt) & kiểm tra files_hash khớp tuyệt đối
    API->>Dag: Ghi asset code vào thư mục Dagster
    API-->>Web: Cập nhật status = DEPLOYED
    Dag->>Dag: Lên lịch chạy định kỳ (theo cron trong Spec)
```

---

## 4. Sơ đồ 3: Chi tiết LangGraph AI Agent & Cơ chế phòng vệ nhiều lớp (Guardrails)

Agent được xây dựng bằng StateGraph (LangGraph) với cơ chế lặp phản hồi có kiểm soát ngân sách:

```mermaid
flowchart TD
    Start([Bắt đầu yêu cầu]) --> PlannerNode["1. Planner Node (gpt-4o-mini)<br/>Đọc schema metadata & quan hệ PK/FK<br/>Sinh kế hoạch: Nguồn, Khóa Join, Độ hạt, Lịch chạy"]
    
    PlannerNode --> CheckBudget1{"Kiểm tra số lần gọi LLM<br/>(max_llm_calls <= 6)"}
    CheckBudget1 -- Vượt trần --> ErrBudget["Trạng thái: NEEDS_HUMAN<br/>(Lý do: Vượt ngân sách LLM)"]
    
    CheckBudget1 -- Hợp lệ --> CodegenNode["2. Codegen Node (gpt-4o / gpt-4o-mini)<br/>Đọc schema chi tiết + 20 dòng mẫu thật<br/>Sinh cấu trúc Spec (JSON)"]
    
    CodegenNode --> GateSchema{"Cổng 1: spec_schema<br/>Pydantic ModelValidate<br/>& Vi phạm policy/domains.yml?"}
    GateSchema -- Thất bại --> IncAttempt["Tăng số lần sửa spec_attempts<br/>(Tối đa 2 lần)"]
    
    IncAttempt --> CheckAttempts{"spec_attempts <= 2?"}
    CheckAttempts -- Có --> FeedbackPrompt["Tổng hợp lỗi cụ thể -> Feedback cho Codegen"]
    FeedbackPrompt --> CodegenNode
    CheckAttempts -- Không --> ErrSpec["Trạng thái: NEEDS_HUMAN<br/>(Lỗi Spec không tự sửa được)"]
    
    GateSchema -- Đạt --> GateRealCol{"Cổng 2: schema_real<br/>Tất cả cột có trong DB nguồn?<br/>Ép ngày có date_formats?<br/>Khóa Join cùng kiểu dữ liệu?"}
    GateRealCol -- Thất bại --> IncAttempt
    
    GateRealCol -- Đạt --> GateAST{"Cổng 3: ast_guard<br/>Biểu thức có chứa ký tự cấm { }, ;, comment,<br/>từ khóa ghi/sửa (DROP, INSERT...) hoặc hàm lạ?"}
    GateAST -- Vi phạm AST --> ErrAST["Trạng thái: BLOCKED_AST<br/>(Chặn ngay lập tức)"]
    
    GateAST -- An toàn --> ModeCheck{"Chế độ chạy?"}
    ModeCheck -- Draft Mode --> StopDraft["Trạng thái: DRAFT<br/>(Dừng lại chờ Engineer kiểm tra mapping)"]
    
    ModeCheck -- Direct / Continue --> RenderNode["3. Render Node (Tất định)<br/>Dùng Jinja2 template để sinh file:<br/>- dlt_pipeline.py<br/>- dbt staging & gold sql<br/>- dbt schema test.yml<br/>- dagster_asset.py"]
    
    RenderNode --> SandboxNode["4. Sandbox Run Node<br/>Chạy dlt, dbt và đối chiếu độc lập"]
    
    SandboxNode --> End([Kết thúc chu trình Agent])
```

---

## 5. Sơ đồ 4: Luồng Sandbox & Đối chiếu độc lập (Independent Reconciliation)

Kiến trúc kiểm chứng kết quả bằng 2 đường tính toán độc lập nhằm loại trừ mọi rủi ro về sai lệch thực thi (execution drifts):

```mermaid
flowchart TD
    SpecData["Spec (Đã được xác thực)"]

    subgraph Path1["Đường 1: Pipeline ELT Tiêu Chuẩn (Thực thi sandbox)"]
        DLT["dlt Ingestion<br/>Đọc Postgres -> Nạp DuckDB Bronze"]
        ReconcileBronze{"Đối soát Bronze:<br/>Số dòng nguồn == Số dòng Bronze?"}
        DBT_Stg["dbt Staging<br/>- Ép kiểu dữ liệu an toàn<br/>- Bắt lỗi -> Bảng Rejected<br/>- Khử trùng theo Unique Keys"]
        DBT_Gold["dbt Gold<br/>- Gộp theo thời gian/nhóm<br/>- Tính tổng / đếm chỉ số"]
        DBT_Test{"dbt Test Suite<br/>Unique, Not Null, Not Negative"}
        GoldOutput[("Kết quả Gold Pipeline<br/>(DuckDB)")]
    end

    subgraph Path2["Đường 2: Phép Tính Tham Chiếu Độc Lập (reference.py)"]
        SpecTranslator["Dịch Spec sang 1 câu Postgres SQL thuần<br/>(Không đi qua dlt, dbt, duckdb, template)"]
        DirectQuery["Chạy SQL trực tiếp trên PostgreSQL nguồn<br/>(Bằng quyền Read-Only)"]
        RefOutput[("Kết quả Tham Chiếu Chuẩn<br/>(Postgres Direct)")]
    end

    subgraph Comparator["Bộ So Khớp & Chẩn Đoán Lệch"]
        RowDiff{"So khớp từng dòng<br/>(Row-by-Row Diff)"}
        DiagEngine["Công cụ tự chẩn đoán lỗi:<br/>- Lệch múi giờ (+7h / UTC)<br/>- Lỗi ép kiểu số thập phân<br/>- Thừa / thiếu dòng do lọc sai"]
        VerdictPass["✅ PASS: 100% dòng khớp từng xu<br/>-> READY_FOR_REVIEW"]
        VerdictFail["❌ FAIL: Phát hiện lệch dữ liệu<br/>-> NEEDS_HUMAN (Chặn lại, không đưa tới Reviewer)"]
    end

    SpecData --> DLT
    DLT --> ReconcileBronze
    ReconcileBronze -- Đạt --> DBT_Stg
    DBT_Stg --> DBT_Gold
    DBT_Gold --> DBT_Test
    DBT_Test -- Đạt --> GoldOutput

    SpecData --> SpecTranslator
    SpecTranslator --> DirectQuery
    DirectQuery --> RefOutput

    GoldOutput --> RowDiff
    RefOutput --> RowDiff

    RowDiff -- Khớp hoàn toàn --> VerdictPass
    RowDiff -- Có sai số --> DiagEngine --> VerdictFail
```

---

## 6. Sơ đồ 5: Phân quyền & Vòng đời trạng thái Run (State Machine & RBAC)

Toàn bộ các trạng thái mà một `RunReport` có thể trải qua trong hệ thống:

```mermaid
stateDiagram-v2
    [*] --> GENERATING: Engineer gửi yêu cầu
    GENERATING --> DRAFT: Bản nháp hoàn tất
    
    DRAFT --> GENERATING: Engineer chỉnh sửa mapping & bấm "Chạy thử"
    
    GENERATING --> NEEDS_HUMAN: Cổng kiểm tra thất bại / Sandbox lỗi
    GENERATING --> BLOCKED_AST: Vi phạm cú pháp AST độc hại
    GENERATING --> READY_FOR_REVIEW: Sandbox đạt & Đối chiếu khớp 100%
    
    state READY_FOR_REVIEW {
        [*] --> WaitingReview
        note right of WaitingReview
            Bất biến an toàn:
            1. Reviewer != Engineer (Không tự duyệt bài của mình)
            2. files_hash của Reviewer phải trùng khớp với mã đã test
        end note
    }

    READY_FOR_REVIEW --> REJECTED: Reviewer từ chối (Kèm nhận xét)
    READY_FOR_REVIEW --> DEPLOYED: Reviewer chấp thuận
    
    DEPLOYED --> [*]: Mã nguồn ghi vào Dagster và kích hoạt lịch chạy
    REJECTED --> [*]
    NEEDS_HUMAN --> [*]
    BLOCKED_AST --> [*]
```

---

## 7. Bảng tra cứu module mã nguồn (Source Code Mapping)

| Thành phần | Đường dẫn tệp | Trách nhiệm kỹ thuật |
|---|---|---|
| **FastAPI Entrypoint** | [`src/main.py`](file:///home/cuong/P-045/src/main.py) | Khởi tạo ứng dụng, cấu hình CORS, tích hợp các sub-routers, xử lý lifespan và đánh dấu lỗi các run dở dang khi restart. |
| **Agent StateGraph** | [`src/agents/graph.py`](file:///home/cuong/P-045/src/agents/graph.py) | Cấu trúc LangGraph 4 node (`planner` $\rightarrow$ `codegen` $\rightarrow$ `render` $\rightarrow$ `test_run`) và các rẽ nhánh điều kiện. |
| **Agent Service** | [`src/agents/service.py`](file:///home/cuong/P-045/src/agents/service.py) | Điều phối chạy nền (background task), ghi nhận tiến độ timeline, cập nhật trạng thái `RunReport` vào SQLite. |
| **Spec Validator** | [`src/agents/validate.py`](file:///home/cuong/P-045/src/agents/validate.py) | Kiểm tra tính hợp lệ của Spec: Pydantic schema, đối chiếu cột thực tế, bảo vệ cú pháp AST. |
| **Template Renderer** | [`src/codegen/render.py`](file:///home/cuong/P-045/src/codegen/render.py) | Đọc `Spec` và nạp Jinja2 templates tại `templates/` để sinh mã nguồn dlt, dbt và Dagster. |
| **Sandbox Runner** | [`src/codegen/sandbox.py`](file:///home/cuong/P-045/src/codegen/sandbox.py) | Khởi tạo môi trường thư mục tạm + DuckDB, thực thi `subprocess` dlt và dbt với biến môi trường tối thiểu (không leak LLM key). |
| **Independent Checker** | [`src/codegen/reference.py`](file:///home/cuong/P-045/src/codegen/reference.py) | Dịch Spec thành 1 câu SQL PostgreSQL độc lập, chạy trên nguồn và so khớp từng dòng với kết quả Gold của DuckDB. |
| **Evidence Store** | [`src/run_report/store.py`](file:///home/cuong/P-045/src/run_report/store.py) | Lưu trữ phiên bản, báo cáo chạy `RunReport`, nội dung tệp mã nguồn và tính toán `files_hash`. |
| **Deployer** | [`src/codegen/deploy.py`](file:///home/cuong/P-045/src/codegen/deploy.py) | Ghi mã nguồn đã được Reviewer duyệt vào thư mục code location của Dagster để nạp lịch chạy chính thức. |
| **Frontend Studio** | [`web/`](file:///home/cuong/P-045/web/) | Next.js 16 UI: Catalog dữ liệu, sơ đồ ER quan hệ bảng, giao diện xem & chỉnh sửa Mapping, so sánh phiên bản (Diff), phòng duyệt của Reviewer. |
