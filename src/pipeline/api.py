"""Local Sprint 1 API. Demo identity selection is disabled in production.

Mỗi run ghi một `RunReport` vào sổ bằng chứng; đọc lại qua `GET /api/v1/runs`.
"""

import hashlib
import secrets
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.config import get_settings
from src.pipeline import postgres, store
from src.pipeline.auth import (
    ACCOUNTS,
    COOKIE,
    COOKIE_PATH,
    SESSION_SECONDS,
    USERS,
    current_user,
    demo_only,
    session_digest,
    settings,
)
from src.pipeline.cleaning import clean_rows
from src.pipeline.models import LoginRequest, RunRequest
from src.pipeline.settings import PipelineSettings
from src.run_report import Check, Cost, GateResult, RunReport, Timing, save_run_report

router = APIRouter(prefix="/pipeline", tags=["Postgres pipeline"])
DEFAULT_PASSWORDS = {"Engineer@123", "Reviewer@123"}
DOMAIN = "sales"
SAMPLE_ROWS = 20
MISSING_COLUMNS = "Spec tham chiếu cột không tồn tại trong schema nguồn."


@router.get("/users", dependencies=[Depends(current_user)])
def users():
    return list(USERS.values())


@router.post("/session", dependencies=[Depends(demo_only)])
def login(body: LoginRequest, response: Response, config: PipelineSettings = Depends(settings)):
    username = body.username.strip().lower()
    user_id = ACCOUNTS.get(username)
    expected = {
        "engineer": config.engineer_password.get_secret_value(),
        "reviewer": config.reviewer_password.get_secret_value(),
    }.get(username, "")
    if get_settings().app_env == "staging" and expected in DEFAULT_PASSWORDS:
        # Bản demo đã deploy ra Internet: mật khẩu mặc định ai cũng biết, và ai đăng nhập được là tiêu được tiền LLM.
        raise HTTPException(503, "Môi trường staging phải đặt PIPELINE_ENGINEER_PASSWORD / PIPELINE_REVIEWER_PASSWORD.")
    if not user_id or not secrets.compare_digest(body.password, expected):
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng.")
    token = secrets.token_urlsafe(32)
    with store.database(config.store_path) as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + SESSION_SECONDS),
        )
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=SESSION_SECONDS, path=COOKIE_PATH)
    return USERS[user_id]


@router.get("/session")
def session(user: dict = Depends(current_user)):
    return user


@router.delete("/session")
def logout(request: Request, response: Response, config: PipelineSettings = Depends(settings)):
    with store.database(config.store_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (session_digest(request),))
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return {"ok": True}


@router.get("/source", dependencies=[Depends(current_user)])
def source(config: PipelineSettings = Depends(settings)):
    try:
        return {"table": "sales_raw.orders", "columns": postgres.source_schema(config), "max_rows": config.max_rows}
    except postgres.SourceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


def _staging_check(table: str, cleaned: dict) -> Check:
    """Đối soát bronze → staging của nguồn, kèm mẫu dòng sạch và dòng rác để màn Staging hiển thị."""
    read, rejected = cleaned["rows_read"], cleaned["rows_rejected"]
    return Check(
        name="staging_reconcile",
        source_id=table,
        result="pass" if read and not rejected else "fail",
        observed={
            "bronze": read,
            "staging": cleaned["rows_written"],
            "rejected": rejected,
            "reject_pct": round(rejected * 100 / read, 2) if read else 0.0,
            "reject_reasons": {"cast": rejected} if rejected else {},
            "reject_samples": cleaned["rejected"][:SAMPLE_ROWS],
            "output_schema": cleaned["output_schema"],
            "output_samples": cleaned["output"][:SAMPLE_ROWS],
        },
    )


@router.post("/runs", status_code=201, response_model=RunReport)
def create_run(
    body: RunRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> RunReport:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được chạy pipeline.")
    created_at = datetime.now(UTC)
    started = time.perf_counter()
    gates: list[GateResult] = []
    checks: list[Check] = []
    error_trace: list[dict] = []
    try:
        schema = postgres.source_schema(config)
        missing = sorted({col.source for col in body.columns} - {col["name"] for col in schema})
        if missing:
            gates.append(
                GateResult(
                    name="schema_real",
                    result="fail",
                    detail=MISSING_COLUMNS,
                    observed={"source_id": body.table, "missing_columns": missing},
                )
            )
            raise ValueError(MISSING_COLUMNS)
        gates.append(
            GateResult(name="schema_real", result="pass", detail="Mọi cột trong Spec có ở nguồn.", observed={})
        )
        rows = postgres.read_rows(config, [col.source for col in body.columns])
        checks.append(_staging_check(body.table, clean_rows(rows, body.columns)))
        status, stage = ("READY_FOR_REVIEW" if checks[0].result == "pass" else "NEEDS_HUMAN"), "report"
    except postgres.SourceUnavailableError as exc:
        status, stage = "INFRA_ERROR", "el_run"
        error_trace.append({"stage": stage, "source_id": body.table, "error": str(exc)})
    except ValueError as exc:
        status, stage = "NEEDS_HUMAN", "static"
        error_trace.append({"stage": stage, "error": str(exc)})
    except ImportError:
        status, stage = "INTERNAL_ERROR", "t_run"
        error_trace.append({"stage": stage, "error": "Backend cần cài requirements.txt."})
    except Exception:
        status, stage = "INTERNAL_ERROR", "t_run"
        error_trace.append({"stage": stage, "error": "Chạy sandbox thất bại. Kiểm tra cấu hình và tài nguyên backend."})
    report = RunReport(
        run_id=str(uuid4()),
        version=1,
        domain=DOMAIN,
        created_by=user["id"],
        trigger="user_request",
        created_at=created_at,
        user_request=body.title,
        status=status,
        stage_reached=stage,
        # Cột không khai báo date_formats thì không ghi trường này (Spec giữ nguyên như trước khi có tùy chọn này).
        spec=body.model_dump(by_alias=True, exclude_none=True),
        gates=gates,
        checks=checks,
        # Chạy DuckDB local, không quét warehouse nên chưa có chi phí để ghi.
        cost=Cost(estimated_bytes=0, actual_bytes=0, usd=0),
        timing=Timing(baseline_sec=round(time.perf_counter() - started, 3)),
        llm_calls=0,
        error_trace=error_trace,
    )
    save_run_report(config.store_path, report)
    return report
