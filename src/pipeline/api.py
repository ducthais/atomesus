"""Local Sprint 1 API. Demo identity selection is disabled in production.

Mỗi run ghi một `RunReport` vào sổ bằng chứng; đọc lại qua `GET /api/v1/runs`.
"""

import hashlib
import secrets
import smtplib
import sqlite3
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.config import get_settings
from src.pipeline import email as email_delivery
from src.pipeline import postgres, store
from src.pipeline.auth import (
    ACCOUNTS,
    COOKIE,
    COOKIE_PATH,
    SESSION_SECONDS,
    USERS,
    current_user,
    demo_only,
    hash_password,
    session_digest,
    settings,
    verify_password,
)
from src.pipeline.cleaning import clean_rows
from src.pipeline.models import LoginRequest, RegisterRequest, RoleUpdateRequest, RunRequest, VerifyRegistrationRequest
from src.pipeline.settings import PipelineSettings
from src.run_report import Check, Cost, GateResult, RunReport, Timing, save_run_report

router = APIRouter(prefix="/pipeline", tags=["Postgres pipeline"])
DEFAULT_PASSWORDS = {"Engineer@123", "Reviewer@123", "Admin@123"}
DOMAIN = "sales"
SAMPLE_ROWS = 20
MISSING_COLUMNS = "Spec tham chiếu cột không tồn tại trong schema nguồn."
VERIFICATION_SECONDS = 600
MAX_VERIFICATION_ATTEMPTS = 5


@router.get("/users", dependencies=[Depends(current_user)])
def users(config: PipelineSettings = Depends(settings)):
    built_in = [{**USERS[user_id], "username": username, "managed": False} for username, user_id in ACCOUNTS.items()]
    registered = [{**user, "managed": True} for user in store.list_users(config.store_path)]
    return [*built_in, *registered]


@router.patch("/users/{user_id}/role", dependencies=[Depends(demo_only)])
def update_role(
    user_id: str,
    body: RoleUpdateRequest,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
):
    """Admin cấp hoặc thu hồi quyền Reviewer của tài khoản đăng ký; tài khoản hệ thống là bất biến."""
    if user["role"] != "Admin":
        raise HTTPException(403, "Chỉ Admin được thay đổi vai trò tài khoản.")
    if user_id in USERS:
        raise HTTPException(409, "Không thể thay đổi vai trò của tài khoản hệ thống.")
    updated = store.update_user_role(config.store_path, user_id, body.role)
    if updated is None:
        raise HTTPException(404, f"Không tìm thấy tài khoản {user_id}")
    return {**updated, "managed": True}


def _start_session(response: Response, user_id: str, config: PipelineSettings) -> None:
    token = secrets.token_urlsafe(32)
    with store.database(config.store_path) as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + SESSION_SECONDS),
        )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=get_settings().app_env not in ("development", "test"),
        max_age=SESSION_SECONDS,
        path=COOKIE_PATH,
    )


@router.post("/session", dependencies=[Depends(demo_only)])
def login(body: LoginRequest, response: Response, config: PipelineSettings = Depends(settings)):
    username = body.username.strip().lower()
    registered = store.get_user_by_username(config.store_path, username)
    if registered is not None:
        user, encoded = registered
        valid = verify_password(body.password, encoded)
    else:
        user_id = ACCOUNTS.get(username)
        user = USERS.get(user_id or "")
        expected = {
            "engineer": config.engineer_password.get_secret_value(),
            "reviewer": config.reviewer_password.get_secret_value(),
            "admin": config.admin_password.get_secret_value(),
        }.get(username, "")
        valid = bool(user_id) and secrets.compare_digest(body.password, expected)
    if registered is None and get_settings().app_env == "staging" and expected in DEFAULT_PASSWORDS:
        # Bản demo đã deploy ra Internet: mật khẩu mặc định ai cũng biết, và ai đăng nhập được là tiêu được tiền LLM.
        raise HTTPException(
            503,
            "Môi trường staging phải đặt PIPELINE_ENGINEER_PASSWORD / PIPELINE_REVIEWER_PASSWORD / PIPELINE_ADMIN_PASSWORD.",
        )
    if user is None or not valid:
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng.")
    _start_session(response, user["id"], config)
    return user


def _verification_hash(email: str, code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{email}:{code}".encode()).hexdigest()
    return f"{salt}${digest}"


@router.post("/register", status_code=202, dependencies=[Depends(demo_only)])
def register(body: RegisterRequest, config: PipelineSettings = Depends(settings)):
    """Gửi mã email; chưa tạo tài khoản cho tới khi mã được xác thực."""
    if body.username in ACCOUNTS or body.username in USERS:
        raise HTTPException(409, "Tên đăng nhập này đã được sử dụng.")
    if store.email_or_username_exists(config.store_path, body.email, body.username):
        raise HTTPException(409, "Email hoặc tên đăng nhập này đã được sử dụng.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    store.save_pending_registration(
        config.store_path,
        email=body.email,
        username=body.username,
        name=body.name,
        password_hash=hash_password(body.password),
        verification_hash=_verification_hash(body.email, code),
        expires=time.time() + VERIFICATION_SECONDS,
    )
    try:
        sent = email_delivery.send_verification_code(config, body.email, code)
    except (OSError, smtplib.SMTPException) as exc:
        store.delete_pending_registration(config.store_path, body.email)
        raise HTTPException(503, "Không gửi được email xác thực. Vui lòng thử lại sau.") from exc

    environment = get_settings().app_env
    if not sent and environment not in ("development", "test"):
        store.delete_pending_registration(config.store_path, body.email)
        raise HTTPException(503, "Máy chủ chưa được cấu hình SMTP để gửi email xác thực.")
    result: dict[str, str | int] = {"email": body.email, "expires_in": VERIFICATION_SECONDS}
    if not sent and environment in ("development", "test"):
        result["development_code"] = code
    return result


@router.post("/register/verify", status_code=201, dependencies=[Depends(demo_only)])
def verify_registration(
    body: VerifyRegistrationRequest, response: Response, config: PipelineSettings = Depends(settings)
):
    """Xác thực OTP một lần, tạo Engineer rồi đăng nhập ngay."""
    pending = store.get_pending_registration(config.store_path, body.email)
    if pending is None:
        raise HTTPException(404, "Không tìm thấy yêu cầu đăng ký. Vui lòng gửi lại mã.")
    email, username, name, password_hash, encoded, expires, attempts = pending
    if expires < time.time():
        store.delete_pending_registration(config.store_path, email)
        raise HTTPException(410, "Mã xác thực đã hết hạn. Vui lòng gửi lại mã.")
    if attempts >= MAX_VERIFICATION_ATTEMPTS:
        store.delete_pending_registration(config.store_path, email)
        raise HTTPException(429, "Bạn đã nhập sai quá nhiều lần. Vui lòng gửi lại mã.")
    salt, _digest = encoded.split("$", 1)
    if not secrets.compare_digest(_verification_hash(email, body.code, salt), encoded):
        store.increment_verification_attempts(config.store_path, email)
        raise HTTPException(400, "Mã xác thực không đúng.")
    if username in ACCOUNTS or username in USERS or store.email_or_username_exists(config.store_path, email, username):
        store.delete_pending_registration(config.store_path, email)
        raise HTTPException(409, "Email hoặc tên đăng nhập này đã được sử dụng.")
    try:
        user = store.create_user(
            config.store_path,
            user_id=f"user_{uuid4().hex}",
            username=username,
            name=name,
            role="Engineer",
            password_hash=password_hash,
            created_at=time.time(),
            email=email,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Email hoặc tên đăng nhập này đã được sử dụng.") from exc
    store.delete_pending_registration(config.store_path, email)
    _start_session(response, user["id"], config)
    return user


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
