"""Demo session shared by every API. Identity always comes from the session, never from the client."""

import hashlib
import time

from fastapi import Depends, HTTPException, Request

from src.config import get_settings
from src.pipeline import store
from src.pipeline.settings import PipelineSettings

USERS = {
    "minh": {"id": "minh", "name": "Minh", "role": "Engineer"},
    "an": {"id": "an", "name": "An", "role": "Reviewer"},
}
ACCOUNTS = {"engineer": "minh", "reviewer": "an"}
COOKIE = "pipeline_session"
# Cookie phải gửi kèm mọi API dưới /api/v1, không chỉ /pipeline.
COOKIE_PATH = "/api/v1"
SESSION_SECONDS = 28800


def settings() -> PipelineSettings:
    return PipelineSettings()


def demo_only():
    if get_settings().app_env == "production":
        raise HTTPException(403, "Đăng nhập bằng tài khoản local chỉ dành cho môi trường phát triển.")


def session_digest(request: Request) -> str:
    return hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()


def current_user(request: Request, config: PipelineSettings = Depends(settings)) -> dict:
    demo_only()
    with store.database(config.store_path) as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND expires > ?", (session_digest(request), time.time())
        ).fetchone()
    if not row or row[0] not in USERS:
        raise HTTPException(401, "Vui lòng đăng nhập.")
    return USERS[row[0]]
