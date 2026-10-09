# Architecture Diagram — Atomesus (P-045)

> Tài liệu tổng hợp kiến trúc và sơ đồ hệ thống của dự án **Atomesus (P-045)**.
> Chi tiết toàn diện xem tại: [system_architecture_analysis.md](system_architecture_analysis.md)

---

## 1. High-Level Architecture

```mermaid
graph TB
    subgraph UI_Layer["Tầng Giao Diện (Frontend - Next.js 16)"]
        UI_Studio["Pipeline Studio (Catalog, ERD, Mapping Editor)"]
        UI_Run["Run Monitor & Diff Viewer (Monaco Editor)"]
        UI_Review["Reviewer Approval Console"]
    end

    subgraph Backend_Layer["Tầng Điều Khiển (FastAPI Backend)"]
        API_Gateway["API Routes (/agent, /runs, /catalog, /policy)"]
        Auth_RBAC["Session & RBAC (Engineer vs Reviewer)"]
        Evidence_Store[("Evidence Store (SQLite)")]
        Policy_Engine["Policy Loader (domains, budgets, objectives)"]
    end

    subgraph Agent_Core["Tầng Lõi AI Agent (LangGraph)"]
        Planner["Planner (P1: gpt-4o-mini)"]
        CodeGen["Codegen & Spec (P3-P4: gpt-4o)"]
        Validator{"Cổng Kiểm Tra Spec (Schema, Real Columns, AST)"}
        Renderer["Renderer (P6: Jinja2 Templates)"]
    end

    subgraph Sandbox_Layer["Tầng Sandbox (DuckDB Data Plane)"]
        DuckDB_Sandbox[("DuckDB Tạm Thời")]
        DLT_Runner["dlt Engine (Postgres -> DuckDB Bronze)"]
        DBT_Runner["dbt-duckdb (Bronze -> Staging -> Gold)"]
        Reconcile_Engine["Independent Reconciliation Engine (Direct SQL)"]
    end

    subgraph External_Storage["Hệ Thống Dữ Liệu & Vận Hành"]
        Postgres_Source[("PostgreSQL Nguồn (Port 5433)")]
        Dagster_Orch["Dagster Orchestrator (Port 3070)"]
    end

    UI_Studio --> API_Gateway
    UI_Run --> API_Gateway
    UI_Review --> API_Gateway

    API_Gateway --> Auth_RBAC
    API_Gateway --> Evidence_Store
    API_Gateway --> Agent_Core
    Policy_Engine -.-> Agent_Core

    Planner --> CodeGen
    CodeGen --> Validator
    Validator -- Hợp lệ --> Renderer
    Validator -- Lỗi --> CodeGen

    Renderer --> Sandbox_Layer
    DLT_Runner --> Postgres_Source
    DLT_Runner --> DuckDB_Sandbox
    DBT_Runner --> DuckDB_Sandbox
    Reconcile_Engine --> Postgres_Source
    Reconcile_Engine <--> DuckDB_Sandbox

    API_Gateway --> Dagster_Orch
```

---

## 2. End-to-End Execution Flow

```mermaid
sequenceDiagram
    autonumber
    actor Eng as Engineer
    participant Web as Next.js Studio
    participant API as FastAPI Backend
    participant Agent as LangGraph Agent
    participant Sand as DuckDB Sandbox
    participant PG as PostgreSQL Nguồn
    actor Rev as Reviewer
    participant Dag as Dagster

    Eng->>Web: Nhập yêu cầu nghiệp vụ & chọn bảng nguồn
    Web->>API: POST /api/v1/agent/drafts
    API->>Agent: Lập kế hoạch (P1) -> Sinh Spec (P3-P4)
    Agent-->>Web: Trả về trạng thái DRAFT (tab Mapping)
    Eng->>Web: Xác nhận/chỉnh sửa mapping -> Bấm "Chạy thử"
    Web->>API: POST /api/v1/agent/runs/{id}/continue
    API->>Agent: Render mã nguồn từ Template -> Chạy Sandbox
    Agent->>Sand: dlt tải dữ liệu -> dbt build & test
    Sand->>PG: Chạy SQL đối chiếu độc lập trên Postgres nguồn
    Sand-->>API: Kết quả khớp 100% -> Trạng thái READY_FOR_REVIEW
    Rev->>Web: Đánh giá diff, bảng kết quả và đối chiếu
    Rev->>API: POST /api/v1/runs/{id}/approve (Kèm files_hash)
    API->>Dag: Triển khai file asset vào Dagster & kích hoạt Schedule
```

---

## 3. Independent Reconciliation (`metric_reconcile`)

```mermaid
flowchart LR
    Spec["Spec đã duyệt"] --> Path1["Đường 1: dlt -> DuckDB Bronze -> dbt Staging -> dbt Gold"]
    Spec --> Path2["Đường 2: Reference SQL dịch độc lập -> Chạy trực tiếp trên Postgres nguồn"]
    Path1 --> Comp{"So khớp từng dòng<br/>(Row-by-Row Diff)"}
    Path2 --> Comp
    Comp -->|Khớp 100%| Pass["READY_FOR_REVIEW"]
    Comp -->|Lệch| Fail["NEEDS_HUMAN (Chặn lại, tự chẩn đoán lỗi)"]
```
