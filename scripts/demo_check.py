"""Kiểm tra trước buổi demo: dịch vụ, nguồn dữ liệu, và các run mà kịch bản docs/demo.md dùng tới.

    .venv/Scripts/python.exe scripts/demo_check.py

Chỉ đọc, không tạo run, không gọi LLM. Mỗi dòng in ✅ hoặc ❌ kèm cách sửa.
"""

import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

BACKEND, DAGSTER, WEB = "http://127.0.0.1:8000", "http://127.0.0.1:3070", "http://localhost:3000"
MOCK_API = "http://127.0.0.1:8100"
START = {
    "postgres": "docker compose --env-file .env -p p045-b -f infra/postgres/compose.yml up -d --wait",
    "backend": r".venv\Scripts\uvicorn.exe src.main:app --port 8000",
    "dagster": r".venv\Scripts\python.exe scripts\dagster_local.py",
    "mock_api": "make mock-api",
    "web": "cd web && npm run dev",
}
# Run mà kịch bản dùng: (8 ký tự đầu, trạng thái cần có, vai trò trong demo)
DEMO_RUNS = [
    ("966c9974", "DEPLOYED", "pipeline đã chạy trên Dagster (Sales, doanh thu theo ngày)"),
    ("ce884a4f", "READY_FOR_REVIEW", "run dự phòng để Reviewer duyệt (Sales, doanh thu theo khách hàng)"),
    ("bc434ba6", "READY_FOR_REVIEW", "ví dụ AI hiểu chưa đúng ý → hệ thống cảnh báo (Pagila)"),
    ("c4b8f0d3", "BLOCKED_SEMANTIC_CONFLICT", "ví dụ bị chặn ở cổng ngữ nghĩa"),
    ("0dbc25b0", "READY_FOR_REVIEW", "3 nguồn Postgres + API + CSV, kế hoạch đã sửa, Reviewer đã yêu cầu sửa (v2)"),
    ("a1cb5645", "CANCELLED", "run bị Engineer hủy ở bước xác nhận kế hoạch"),
]
failures = 0


def check(ok: bool, label: str, fix: str = "") -> bool:
    global failures
    print(f"{'✅' if ok else '❌'} {label}" + ("" if ok or not fix else f"\n     → {fix}"))
    failures += not ok
    return ok


def get(url: str, **kwargs) -> httpx.Response | None:
    try:
        return httpx.get(url, timeout=10, **kwargs)
    except httpx.HTTPError:
        return None


def main() -> int:
    print("== Dịch vụ ==")
    pg = subprocess.run(
        ["docker", "ps", "--filter", "name=p045-b-postgres-1", "--format", "{{.Status}}"],
        capture_output=True,
        text=True,
    )
    check(
        "healthy" in pg.stdout,
        f"Postgres nguồn ({pg.stdout.strip() or 'không chạy'})",
        "Mở Docker Desktop rồi: " + START["postgres"],
    )
    backend = get(f"{BACKEND}/health")
    if not check(bool(backend and backend.status_code == 200), "Backend :8000", START["backend"]):
        return 1
    dagster = get(f"{DAGSTER}/server_info")
    check(bool(dagster and dagster.status_code == 200), "Dagster :3070", START["dagster"])
    mock = get(f"{MOCK_API}/sales/openapi.json")
    check(
        bool(mock and mock.status_code == 200),
        "Mock REST API :8100 (nguồn sales_api, marketing_api)",
        START["mock_api"],
    )
    web = get(WEB)
    check(bool(web and web.status_code == 200), "Frontend :3000", START["web"])
    check(
        bool(os.environ.get("OPENAI_API_KEY") or "OPENAI_API_KEY=" in (ROOT / ".env").read_text(encoding="utf-8")),
        "Có OPENAI_API_KEY cho bước tạo pipeline trực tiếp",
        "Thêm OPENAI_API_KEY vào .env rồi khởi động lại backend",
    )

    print("\n== Đăng nhập và nguồn dữ liệu ==")
    session = httpx.Client(base_url=f"{BACKEND}/api/v1", timeout=30)
    login = session.post(
        "/pipeline/session",
        json={
            "username": "engineer",
            "password": os.environ.get("PIPELINE_ENGINEER_PASSWORD", "Engineer@123"),
        },
    )
    check(login.status_code == 200, "Đăng nhập Minh (Engineer)")
    for ref, schema in [("sales_pg", "sales_raw"), ("pagila_pg", "pagila")]:
        found = session.get(f"/catalog/sources/{ref}/objects")
        if found.status_code != 200:
            check(False, f"Kết nối {ref}", found.text[:200])
            continue
        objects, fks = found.json()["objects"], found.json()["foreign_keys"]
        fix = (
            "python scripts/load_pagila.py (nạp bộ Pagila kèm trong repo)" if ref == "pagila_pg" else START["postgres"]
        )
        check(len(objects) > 0, f"Kết nối {ref} ({schema}): {len(objects)} bảng, {len(fks)} khóa ngoại", fix)

    print("\n== Run dùng trong kịch bản ==")
    runs = {r["run_id"][:8]: r for r in session.get("/runs").json()}
    for short, status, role in DEMO_RUNS:
        run = runs.get(short)
        check(
            bool(run and run["status"] == status),
            f"{short} {status}: {role}",
            f"hiện là {run['status'] if run else 'không có'}; chọn run khác cùng loại trong trang Hoạt động",
        )

    monitored = session.get("/monitor/pipelines")
    count = len(monitored.json()) if monitored.status_code == 200 else 0
    check(
        count > 0,
        f"Màn Theo dõi có {count} pipeline đang chạy",
        "Duyệt ít nhất một run rồi bấm Thu thập ở trang Theo dõi",
    )

    print("\n== Dagster ==")
    if dagster and dagster.status_code == 200:
        query = "{ workspaceOrError { ... on Workspace { locationEntries { name loadStatus } } } }"
        entries = httpx.post(f"{DAGSTER}/graphql", json={"query": query}, timeout=10).json()["data"][
            "workspaceOrError"
        ]["locationEntries"]
        loaded = [e["name"] for e in entries if e["loadStatus"] == "LOADED"]
        check("fct_daily_sales" in loaded, f"Pipeline đã nạp: {', '.join(loaded) or 'không có'}")
        status = session.get("/orchestration/status").json()
        last = (status.get("assets", {}).get("gold/fct_daily_sales") or {}).get("last_run") or {}
        check(
            last.get("status") == "SUCCESS",
            f"gold/fct_daily_sales có lần chạy thành công trên Dagster ({last.get('status', 'chưa chạy')})",
            "Dagster UI → Jobs → fct_daily_sales_job → Materialize all",
        )

    print(f"\n{'Sẵn sàng demo.' if not failures else f'Còn {failures} mục cần sửa trước khi demo.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
