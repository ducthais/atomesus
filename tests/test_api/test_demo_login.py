"""Nút "Truy cập nhanh": vào thẳng tài khoản demo engineer / reviewer mà không cần biết mật khẩu.

VM production sinh mật khẩu ngẫu nhiên (deploy/setup-vm.sh) nên gõ mật khẩu mặc định luôn sai. Đăng nhập nhanh không
dùng mật khẩu: development luôn bật, nơi khác chỉ khi PIPELINE_DEMO_LOGIN=1; Admin không bao giờ có.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.pipeline import api, auth
from src.pipeline.settings import PipelineSettings

RANDOM = {"engineer_password": "k9Xr2-random", "reviewer_password": "Qv7m-random", "admin_password": "Za1-random"}


@pytest_asyncio.fixture
async def configure(client, tmp_path, monkeypatch):
    def apply(app_env: str, demo_login: bool = False) -> PipelineSettings:
        config = PipelineSettings(
            _env_file=None, store_path=str(tmp_path / "runs.sqlite3"), demo_login=demo_login, **RANDOM
        )
        environment = SimpleNamespace(app_env=app_env)
        monkeypatch.setattr(auth, "get_settings", lambda: environment)
        monkeypatch.setattr(api, "get_settings", lambda: environment)
        app.dependency_overrides[auth.settings] = lambda: config
        return config

    yield apply
    app.dependency_overrides.pop(auth.settings, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env,flag", [("development", False), ("production", True), ("staging", True)])
async def test_demo_login_opens_engineer_and_reviewer_sessions_without_a_password(configure, app_env, flag):
    configure(app_env, flag)
    # Staging / production đặt cookie phiên Secure: client phải đi HTTPS mới gửi lại cookie.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as secure:
        assert (await secure.get("/api/v1/pipeline/demo-login")).json() == {"enabled": True}
        for account, role, user_id in (("engineer", "Engineer", "minh"), ("reviewer", "Reviewer", "an")):
            response = await secure.post("/api/v1/pipeline/session/demo", json={"account": account})

            assert response.status_code == 200, response.text
            assert (response.json()["id"], response.json()["role"]) == (user_id, role)
            assert not any(value in response.text for value in RANDOM.values()), "không lộ mật khẩu thật"
            session = await secure.get("/api/v1/pipeline/session")
            assert session.status_code == 200 and session.json()["id"] == user_id


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", ["production", "staging", "test"])
async def test_demo_login_is_off_without_the_flag_outside_development(client, configure, app_env):
    configure(app_env)

    assert (await client.get("/api/v1/pipeline/demo-login")).json() == {"enabled": False}
    response = await client.post("/api/v1/pipeline/session/demo", json={"account": "engineer"})

    assert response.status_code == 404 and "PIPELINE_DEMO_LOGIN" in response.text
    assert (await client.get("/api/v1/pipeline/session")).status_code == 401


@pytest.mark.asyncio
async def test_admin_and_unknown_accounts_have_no_quick_login(client, configure):
    configure("production", demo_login=True)

    for body in ({"account": "admin"}, {"account": "minh"}, {"account": "engineer", "password": "x"}, {}):
        assert (await client.post("/api/v1/pipeline/session/demo", json=body)).status_code == 422, body
    assert (await client.get("/api/v1/pipeline/session")).status_code == 401


@pytest.mark.asyncio
async def test_password_login_still_uses_the_real_passwords(client, configure):
    # Đăng nhập thường không đổi: production với mật khẩu ngẫu nhiên thì mật khẩu mặc định vẫn bị từ chối.
    configure("production", demo_login=True)

    wrong = await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    right = await client.post(
        "/api/v1/pipeline/session", json={"username": "engineer", "password": RANDOM["engineer_password"]}
    )

    assert wrong.status_code == 401 and right.status_code == 200
