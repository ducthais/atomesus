"""Local Sprint 1 API. Built-in accounts require changed passwords outside development/test.

Mỗi run ghi một `RunReport` vào sổ bằng chứng; đọc lại qua `GET /api/v1/runs`.
"""

import hashlib
import ipaddress
import math
import secrets
import smtplib
import sqlite3
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.pipeline import email as email_delivery
from src.pipeline import postgres, store
from src.pipeline.auth import (
    ACCOUNTS,
    COOKIE,
    COOKIE_PATH,
    DEMO_ENGINEER,
    DEMO_REVIEWER,
    SESSION_SECONDS,
    USERS,
    current_user,
    hash_password,
    session_digest,
    settings,
    verify_password,
)
from src.pipeline.cleaning import clean_rows
from src.pipeline.models import LoginRequest, RegisterRequest, RoleUpdateRequest, RunRequest, VerifyRegistrationRequest
from src.pipeline.settings import PipelineSettings
from src.policy.loader import PolicyError, load_policy
from src.policy.models import Budgets
from src.run_report import Check, Cost, GateResult, RunReport, Timing, save_run_report
from src.run_report import quota as run_quota

router = APIRouter(prefix="/pipeline", tags=["Postgres pipeline"])
DEFAULT_PASSWORDS = {"Engineer@123", "Reviewer@123", "Admin@123"}
DOMAIN = "sales"
SAMPLE_ROWS = 20
MISSING_COLUMNS = "Spec tham chiếu cột không tồn tại trong schema nguồn."
VERIFICATION_SECONDS = 600
MAX_VERIFICATION_ATTEMPTS = 5
REGISTRATION_IP_SECONDS = 600  # mỗi IP đăng ký được 1 tài khoản trong cửa sổ này
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
INVITE_SECONDS = 7 * 24 * 3600
MAX_ACTIVE_INVITES = 20  # lời mời còn hạn chưa dùng của mỗi Engineer
INVITE_PATH = "/invite/"
INVITE_INVALID = "Link mời không hợp lệ."
INVITE_GONE = "Link mời đã hết hạn hoặc đã được dùng. Hãy xin người mời gửi link mới."


class InviteRegisterRequest(RegisterRequest):
    """Đăng ký; kèm `invite_token` (link mời của Engineer) thì tài khoản tạo ra là Reviewer đã liên kết."""

    invite_token: str | None = Field(default=None, min_length=16, max_length=128)


class AcceptInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=128)


def _invite_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _check_invite(state: str) -> None:
    """Token sai: 404; hết hạn / đã dùng: 410. Không thông báo nào nói ai đã mời."""
    if state == "missing":
        raise HTTPException(404, INVITE_INVALID)
    if state != "ok":
        raise HTTPException(410, INVITE_GONE)


@router.get("/users", dependencies=[Depends(current_user)])
def users(config: PipelineSettings = Depends(settings)):
    built_in = [{**USERS[user_id], "username": username, "managed": False} for username, user_id in ACCOUNTS.items()]
    registered = [{**user, "managed": True} for user in store.list_users(config.store_path)]
    return [*built_in, *registered]


@router.patch("/users/{user_id}/role")
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


@router.post("/session")
def login(body: LoginRequest, request: Request, response: Response, config: PipelineSettings = Depends(settings)):
    username = body.username.strip().lower()
    ip = client_ip(request)
    wait = store.login_retry_after(config.store_path, ip, username, MAX_LOGIN_ATTEMPTS, LOGIN_WINDOW_SECONDS)
    if wait > 0:
        raise HTTPException(
            429,
            "Bạn đã nhập sai quá nhiều lần. Vui lòng thử lại sau.",
            headers={"Retry-After": str(math.ceil(wait))},
        )
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
    environment = get_settings().app_env
    if registered is None and environment in ("staging", "production") and expected in DEFAULT_PASSWORDS:
        # Mật khẩu mặc định của tài khoản cài sẵn là công khai; không cho dùng ở môi trường triển khai thật.
        raise HTTPException(
            503,
            f"Môi trường {environment} phải đặt "
            "PIPELINE_ENGINEER_PASSWORD / PIPELINE_REVIEWER_PASSWORD / PIPELINE_ADMIN_PASSWORD.",
        )
    if user is None or not valid:
        wait = store.record_login_failure(config.store_path, ip, username, MAX_LOGIN_ATTEMPTS, LOGIN_WINDOW_SECONDS)
        if wait > 0:
            raise HTTPException(
                429,
                "Bạn đã nhập sai quá nhiều lần. Vui lòng thử lại sau.",
                headers={"Retry-After": str(math.ceil(wait))},
            )
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng.")
    store.clear_login_failures(config.store_path, ip, username)
    _start_session(response, user["id"], config)
    return user


def _verification_hash(email: str, code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{email}:{code}".encode()).hexdigest()
    return f"{salt}${digest}"


def client_ip(request: Request) -> str:
    """IP của người dùng gửi request.

    Bản deploy đi Caddy → web → backend: Caddy thay mọi X-Forwarded-For client tự gửi bằng IP thật, web chuyển tiếp
    nó. Chỉ tin header khi kết nối tới từ địa chỉ nội bộ (proxy); kết nối trực tiếp từ Internet dùng IP kết nối.
    """
    peer = request.client.host if request.client else ""
    try:
        trusted = ipaddress.ip_address(peer).is_private
    except ValueError:
        trusted = False
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if trusted and forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer or "unknown"


@router.post("/register", status_code=202)
def register(
    body: InviteRegisterRequest, request: Request, response: Response, config: PipelineSettings = Depends(settings)
):
    """Đăng ký tài khoản Engineer; kèm `invite_token` thì tạo Reviewer liên kết với Engineer đã mời. Mỗi IP một
    lần mỗi REGISTRATION_IP_SECONDS.

    `registration_email_verification` tắt: tạo tài khoản, đăng nhập ngay và trả 201. Bật: gửi mã email, trả 202,
    tài khoản chỉ được tạo ở /register/verify (lời mời được dùng lúc đó).
    """
    if body.username in ACCOUNTS or body.username in USERS:
        raise HTTPException(409, "Tên đăng nhập này đã được sử dụng.")
    if store.email_or_username_exists(config.store_path, body.email, body.username):
        raise HTTPException(409, "Email hoặc tên đăng nhập này đã được sử dụng.")
    invite_hash = _invite_hash(body.invite_token) if body.invite_token else None
    if invite_hash:
        # Kiểm trước khi giữ lượt của IP: link sai không làm mất lượt đăng ký.
        _check_invite(store.invite_state(config.store_path, invite_hash, time.time()))
    ip = client_ip(request)
    wait = store.claim_registration_slot(config.store_path, ip, REGISTRATION_IP_SECONDS)
    if wait > 0:
        raise HTTPException(
            429,
            f"Mỗi địa chỉ IP chỉ được đăng ký 1 tài khoản trong {REGISTRATION_IP_SECONDS // 60} phút. "
            f"Vui lòng thử lại sau {math.ceil(wait / 60)} phút.",
            headers={"Retry-After": str(math.ceil(wait))},
        )

    if not config.registration_email_verification:
        try:
            user = _create_account(
                config,
                invite_hash,
                username=body.username,
                name=body.name,
                password_hash=hash_password(body.password),
                email=body.email,
            )
        except sqlite3.IntegrityError as exc:  # request đồng thời vừa lấy cùng email/username
            store.release_registration_slot(config.store_path, ip)
            raise HTTPException(409, "Email hoặc tên đăng nhập này đã được sử dụng.") from exc
        except HTTPException:  # lời mời vừa bị dùng / hết hạn giữa lúc kiểm và lúc tạo
            store.release_registration_slot(config.store_path, ip)
            raise
        _start_session(response, user["id"], config)
        response.status_code = 201
        return user

    code = f"{secrets.randbelow(1_000_000):06d}"
    store.save_pending_registration(
        config.store_path,
        email=body.email,
        username=body.username,
        name=body.name,
        password_hash=hash_password(body.password),
        verification_hash=_verification_hash(body.email, code),
        expires=time.time() + VERIFICATION_SECONDS,
        invite_hash=invite_hash,
    )
    try:
        sent = email_delivery.send_verification_code(config, body.email, code)
    except (OSError, smtplib.SMTPException) as exc:
        store.delete_pending_registration(config.store_path, body.email)
        store.release_registration_slot(config.store_path, ip)
        raise HTTPException(503, "Không gửi được email xác thực. Vui lòng thử lại sau.") from exc

    environment = get_settings().app_env
    if not sent and environment not in ("development", "test"):
        store.delete_pending_registration(config.store_path, body.email)
        store.release_registration_slot(config.store_path, ip)
        raise HTTPException(503, "Máy chủ chưa được cấu hình SMTP để gửi email xác thực.")
    result: dict[str, str | int] = {"email": body.email, "expires_in": VERIFICATION_SECONDS}
    if not sent and environment in ("development", "test"):
        result["development_code"] = code
    return result


@router.post("/register/verify", status_code=201)
def verify_registration(
    body: VerifyRegistrationRequest, response: Response, config: PipelineSettings = Depends(settings)
):
    """Xác thực OTP một lần, tạo Engineer (hoặc Reviewer nếu đăng ký qua link mời) rồi đăng nhập ngay."""
    pending = store.get_pending_registration(config.store_path, body.email)
    if pending is None:
        raise HTTPException(404, "Không tìm thấy yêu cầu đăng ký. Vui lòng gửi lại mã.")
    email, username, name, password_hash, encoded, expires, attempts, invite_hash = pending
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
        user = _create_account(
            config, invite_hash, username=username, name=name, password_hash=password_hash, email=email
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Email hoặc tên đăng nhập này đã được sử dụng.") from exc
    except HTTPException:  # lời mời hết hạn / đã dùng trong lúc chờ mã: bỏ yêu cầu, không tạo tài khoản nào
        store.delete_pending_registration(config.store_path, email)
        raise
    store.delete_pending_registration(config.store_path, email)
    _start_session(response, user["id"], config)
    return user


def _create_account(
    config: PipelineSettings, invite_hash: str | None, *, username: str, name: str, password_hash: str, email: str
) -> dict:
    """Engineer khi không có lời mời; có thì Reviewer + liên kết + dùng lời mời trong cùng một giao dịch."""
    if invite_hash is None:
        return store.create_user(
            config.store_path,
            user_id=f"user_{uuid4().hex}",
            username=username,
            name=name,
            role="Engineer",
            password_hash=password_hash,
            created_at=time.time(),
            email=email,
        )
    state, user = store.create_invited_reviewer(
        config.store_path,
        invite_hash,
        time.time(),
        user_id=f"user_{uuid4().hex}",
        username=username,
        name=name,
        password_hash=password_hash,
        email=email,
    )
    if user is None:
        _check_invite(state)
        raise HTTPException(404, INVITE_INVALID)
    return user


def _engineer_only(user: dict) -> None:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được mời và quản lý Reviewer của mình.")


@router.post("/invites", status_code=201)
def create_invite(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)):
    """Engineer tạo link mời Reviewer (ADR-0001 D1). Token chỉ trả một lần ở đây; máy chủ chỉ lưu hash."""
    _engineer_only(user)
    token = secrets.token_urlsafe(32)
    now = time.time()
    if not store.create_invite(
        config.store_path, user["id"], _invite_hash(token), now, now + INVITE_SECONDS, MAX_ACTIVE_INVITES
    ):
        raise HTTPException(
            429, f"Bạn đang có {MAX_ACTIVE_INVITES} link mời chưa dùng. Đợi link cũ hết hạn hoặc được dùng."
        )
    return {
        "token": token,
        "path": f"{INVITE_PATH}{token}",
        "expires_at": datetime.fromtimestamp(now + INVITE_SECONDS, UTC).isoformat(),
    }


@router.post("/invites/accept")
def accept_invite(
    body: AcceptInviteRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
):
    """Reviewer đã đăng nhập chấp nhận link mời: từ giờ thấy và duyệt được run của Engineer đã mời."""
    if user["role"] == "Engineer":
        raise HTTPException(
            400,
            "Tài khoản Engineer không nhận lời mời duyệt được. Hãy đăng nhập bằng tài khoản Reviewer, "
            "hoặc đăng ký tài khoản mới từ link này.",
        )
    if user["role"] != "Reviewer":
        raise HTTPException(400, "Chỉ tài khoản Reviewer nhận được lời mời duyệt.")
    _check_invite(store.accept_invite(config.store_path, _invite_hash(body.token), user["id"], time.time()))
    return {"ok": True}


@router.get("/reviewers")
def list_reviewers(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)):
    """Reviewer đang duyệt run của Engineer. Cặp demo minh ↔ an luôn có và không gỡ được."""
    _engineer_only(user)
    linked = [{**item, "demo": False} for item in store.list_linked_reviewers(config.store_path, user["id"])]
    for item in linked:
        if item["id"] in USERS:  # Reviewer hệ thống không có dòng trong bảng users
            item.update(username=None, name=USERS[item["id"]]["name"])
    if user["id"] == DEMO_ENGINEER:
        demo = USERS[DEMO_REVIEWER]
        linked = [
            {"id": demo["id"], "username": None, "name": demo["name"], "linked_at": None, "demo": True},
            *(item for item in linked if item["id"] != DEMO_REVIEWER),
        ]
    return linked


@router.delete("/reviewers/{reviewer_id}")
def remove_reviewer(reviewer_id: str, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)):
    """Gỡ liên kết: Reviewer mất quyền xem và duyệt run của Engineer ngay lập tức."""
    _engineer_only(user)
    if user["id"] == DEMO_ENGINEER and reviewer_id == DEMO_REVIEWER:
        raise HTTPException(409, "Không thể gỡ Reviewer của cặp tài khoản demo.")
    if not store.remove_reviewer_link(config.store_path, user["id"], reviewer_id):
        raise HTTPException(404, "Reviewer này không có trong danh sách của bạn.")
    return {"ok": True}


@router.get("/session")
def session(user: dict = Depends(current_user)):
    return user


@router.get("/deploy-config", dependencies=[Depends(current_user)])
def deploy_config(config: PipelineSettings = Depends(settings)) -> dict:
    """Cách nút Deploy đưa bản đã duyệt lên production, để web hiện đúng các bước (ADR D6). Không lộ token hay repo.

    `mode`: direct = deployer cài bundle, chạy thử trên Dagster rồi mới báo DEPLOYED / DEPLOY_FAILED; pr = mở pull
    request, cài sau khi merge. `gitops`: chế độ direct có mở thêm pull request chạy nền để lưu vết (PIPELINE_GITHUB_*).
    """
    from src.codegen.github import GithubClient  # import muộn như run_report.router: tránh vòng import lúc nạp app

    return {"mode": config.deploy_mode, "gitops": GithubClient.from_settings()._configured}


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


def _budgets() -> Budgets:
    """Trần trong budgets.yml, đọc lại mỗi request (NFR-04)."""
    try:
        return load_policy().budgets
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc


def _require_new_domain() -> None:
    """The legacy cleaner is still an API that creates a new sales run, so honor `hidden` here too."""
    try:
        domain = load_policy().domains.domains.get(DOMAIN)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    if domain is None or domain.hidden:
        raise HTTPException(422, "Nguồn sales đã ngừng nhận thao tác mới; lịch sử run vẫn có thể xem.")


@router.get("/quota")
def quota(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Hạn mức hôm nay của chính người dùng (ADR-0001 D10): tiền và số run đã dùng / trần; Admin không có trần riêng.
    Chỉ có số của người đang đăng nhập, không lộ mức dùng của người khác."""
    return run_quota.usage(config.store_path, user, _budgets())


@router.post("/runs", status_code=201, response_model=RunReport)
def create_run(
    body: RunRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> RunReport:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được chạy pipeline.")
    _require_new_domain()
    # Hết lượt run trong ngày (budgets.yml per_user.max_runs_per_day) → 429 trước khi đọc nguồn.
    [run_id] = run_quota.claim_runs(config.store_path, user, _budgets())
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
        run_id=run_id,
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
