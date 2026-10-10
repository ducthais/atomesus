"""Local account/session auth shared by every API. Identity comes from the session, never from the client."""

import hashlib
import secrets
import time

from fastapi import Depends, HTTPException, Request

from src.config import get_settings
from src.pipeline import store
from src.pipeline.settings import PipelineSettings

USERS = {
    "minh": {"id": "minh", "name": "Minh", "role": "Engineer"},
    "an": {"id": "an", "name": "An", "role": "Reviewer"},
    "admin": {"id": "admin", "name": "Quản trị viên", "role": "Admin"},
}
ACCOUNTS = {"engineer": "minh", "reviewer": "an", "admin": "admin"}
COOKIE = "pipeline_session"
# Cookie phải gửi kèm mọi API dưới /api/v1, không chỉ /pipeline.
COOKIE_PATH = "/api/v1"
SESSION_SECONDS = 28800
PASSWORD_ITERATIONS = 600_000
PASSWORD_SCHEME = "pbkdf2_sha256"


def settings() -> PipelineSettings:
    return PipelineSettings()


def demo_only():
    if get_settings().app_env == "production":
        raise HTTPException(403, "Đăng nhập bằng tài khoản local chỉ dành cho môi trường phát triển.")


def session_digest(request: Request) -> str:
    return hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()


def hash_password(password: str) -> str:
    """Băm mật khẩu với salt ngẫu nhiên; không bao giờ lưu hoặc log mật khẩu gốc."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt, expected = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        rounds = int(iterations)
        if rounds != PASSWORD_ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds).hex()
    except (TypeError, ValueError):
        return False
    return secrets.compare_digest(actual, expected)


def current_user(request: Request, config: PipelineSettings = Depends(settings)) -> dict:
    demo_only()
    with store.database(config.store_path) as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND expires > ?", (session_digest(request), time.time())
        ).fetchone()
    user = USERS.get(row[0]) if row else None
    if row and user is None:
        user = store.get_user_by_id(config.store_path, row[0])
    if user is None:
        raise HTTPException(401, "Vui lòng đăng nhập.")
    return user
