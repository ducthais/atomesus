"""Local Sprint 1 API. Demo identity selection is disabled in production."""

import hashlib
import json
import secrets
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.config import get_settings
from src.pipeline import postgres, store
from src.pipeline.cleaning import clean_rows
from src.pipeline.models import LoginRequest, RunRequest
from src.pipeline.settings import PipelineSettings

router = APIRouter(prefix="/pipeline", tags=["Postgres pipeline"])
USERS = {
    "minh": {"id": "minh", "name": "Minh", "role": "Engineer"},
    "an": {"id": "an", "name": "An", "role": "Reviewer"},
}
COOKIE = "pipeline_session"


def settings() -> PipelineSettings:
    return PipelineSettings()


def demo_only():
    if get_settings().app_env == "production":
        raise HTTPException(403, "Đăng nhập chọn người dùng chỉ dành cho demo local.")


def current_user(request: Request, config: PipelineSettings = Depends(settings)) -> dict:
    demo_only()
    digest = hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
    with store.database(config.store_path) as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND expires > ?", (digest, time.time())
        ).fetchone()
    if not row or row[0] not in USERS:
        raise HTTPException(401, "Vui lòng chọn người dùng để đăng nhập.")
    return USERS[row[0]]


@router.get("/users", dependencies=[Depends(demo_only)])
def users():
    return list(USERS.values())


@router.post("/session", dependencies=[Depends(demo_only)])
def login(body: LoginRequest, response: Response, config: PipelineSettings = Depends(settings)):
    token = secrets.token_urlsafe(32)
    with store.database(config.store_path) as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (hashlib.sha256(token.encode()).hexdigest(), body.user_id, time.time() + 28800),
        )
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=28800, path="/api/v1/pipeline")
    return USERS[body.user_id]


@router.get("/session")
def session(user: dict = Depends(current_user)):
    return user


@router.delete("/session")
def logout(request: Request, response: Response, config: PipelineSettings = Depends(settings)):
    digest = hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
    with store.database(config.store_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (digest,))
    response.delete_cookie(COOKIE, path="/api/v1/pipeline")
    return {"ok": True}


@router.get("/source", dependencies=[Depends(current_user)])
def source(config: PipelineSettings = Depends(settings)):
    try:
        return {"table": "sales_raw.orders", "columns": postgres.source_schema(config), "max_rows": config.max_rows}
    except postgres.SourceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/runs", dependencies=[Depends(current_user)])
def list_runs(config: PipelineSettings = Depends(settings)):
    return [
        {key: value for key, value in report.items() if key not in {"output", "rejected", "spec"}}
        for report in store.reports(config.store_path)
    ]


@router.get("/runs/{run_id}", dependencies=[Depends(current_user)])
def get_run(run_id: str, config: PipelineSettings = Depends(settings)):
    report = store.report_by_id(config.store_path, run_id)
    if report is None:
        raise HTTPException(404, "Không tìm thấy run.")
    return report


@router.post("/runs", status_code=201)
def create_run(body: RunRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)):
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được chạy pipeline.")
    spec = body.model_dump(by_alias=True)
    started = time.perf_counter()
    report = {
        "run_id": str(uuid4()),
        "title": body.title,
        "version": 1,
        "created_by": user["id"],
        "started_at": datetime.now(UTC).isoformat(),
        "status": "FAILED",
        "spec": spec,
        "spec_hash": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
        "approval": None,
        "rows_read": 0,
        "rows_written": 0,
        "rows_rejected": 0,
        "output_schema": [],
        "output": [],
        "rejected": [],
        "error": None,
    }
    try:
        schema = postgres.source_schema(config)
        available = {col["name"] for col in schema}
        if any(col.source not in available for col in body.columns):
            raise ValueError("Spec tham chiếu cột không tồn tại trong schema nguồn.")
        rows = postgres.read_rows(config, [col.source for col in body.columns])
        report.update(clean_rows(rows, body.columns))
        report["status"] = "NEEDS_HUMAN" if report["rows_rejected"] or not rows else "SUCCEEDED"
    except (postgres.SourceUnavailableError, ValueError) as exc:
        report["error"] = str(exc)
    except ImportError:
        report["error"] = "Backend cần cài requirements/pipeline.txt."
    except Exception:
        report["error"] = "Chạy sandbox thất bại. Kiểm tra cấu hình và tài nguyên backend."
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    store.save_report(config.store_path, report)
    return report
