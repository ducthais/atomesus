from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.pipeline import api, auth, store
from src.pipeline.settings import PipelineSettings


async def request_and_verify(client, payload):
    requested = await client.post("/api/v1/pipeline/register", json=payload)
    assert requested.status_code == 202, requested.text
    verification = requested.json()
    response = await client.post(
        "/api/v1/pipeline/register/verify",
        json={"email": payload["email"], "code": verification["development_code"]},
    )
    return response


@pytest_asyncio.fixture(params=[True], ids=["email-verification"])
async def registration(request, client, tmp_path, monkeypatch):
    config = PipelineSettings(
        _env_file=None, store_path=str(tmp_path / "runs.sqlite3"), registration_email_verification=request.param
    )
    app.dependency_overrides[auth.settings] = lambda: config
    environment = SimpleNamespace(app_env="test")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "get_settings", lambda: environment)
    try:
        yield client, config
    finally:
        app.dependency_overrides.pop(auth.settings, None)


@pytest.mark.asyncio
async def test_register_creates_engineer_hashes_password_and_starts_session(registration):
    client, config = registration

    payload = {
        "name": "  Linh Nguyễn  ",
        "email": "  Linh@example.com ",
        "username": "Linh.Data",
        "password": "StrongPass9",
    }
    requested = await client.post("/api/v1/pipeline/register", json=payload)
    assert requested.status_code == 202
    assert requested.json()["email"] == "linh@example.com"
    assert len(requested.json()["development_code"]) == 6
    assert store.get_user_by_username(config.store_path, "linh.data") is None

    response = await client.post(
        "/api/v1/pipeline/register/verify",
        json={"email": "LINH@example.com", "code": requested.json()["development_code"]},
    )

    assert response.status_code == 201, response.text
    user = response.json()
    assert user == {
        "id": user["id"],
        "username": "linh.data",
        "name": "Linh Nguyễn",
        "role": "Engineer",
        "email": "linh@example.com",
    }
    assert user["id"].startswith("user_")
    assert (await client.get("/api/v1/pipeline/session")).json() == user
    listed = (await client.get("/api/v1/pipeline/users")).json()
    assert any(item["id"] == user["id"] for item in listed)

    with store.database(config.store_path) as conn:
        encoded = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()[0]
    assert "StrongPass9" not in encoded
    assert encoded.startswith("pbkdf2_sha256$")


@pytest.mark.asyncio
async def test_registered_user_can_log_out_and_log_in_case_insensitively(registration):
    client, _config = registration
    payload = {
        "name": "Linh Nguyễn",
        "email": "linh@example.com",
        "username": "linh.data",
        "password": "StrongPass9",
    }
    created = (await request_and_verify(client, payload)).json()
    assert (await client.delete("/api/v1/pipeline/session")).status_code == 200

    response = await client.post("/api/v1/pipeline/session", json={"username": "LINH.DATA", "password": "StrongPass9"})

    assert response.status_code == 200
    assert response.json() == created
    assert (await client.get("/api/v1/pipeline/session")).status_code == 200


@pytest.mark.asyncio
async def test_registration_rejects_duplicate_reserved_weak_and_privileged_accounts(registration):
    client, _config = registration
    valid = {
        "name": "Linh Nguyễn",
        "email": "linh@example.com",
        "username": "linh.data",
        "password": "StrongPass9",
    }
    assert (await request_and_verify(client, valid)).status_code == 201
    assert (
        await client.post(
            "/api/v1/pipeline/register", json={**valid, "email": "other@example.com", "username": "LINH.DATA"}
        )
    ).status_code == 409
    assert (await client.post("/api/v1/pipeline/register", json={**valid, "username": "other.user"})).status_code == 409
    assert (
        await client.post(
            "/api/v1/pipeline/register", json={**valid, "email": "engineer@example.com", "username": "engineer"}
        )
    ).status_code == 409
    assert (
        await client.post(
            "/api/v1/pipeline/register", json={**valid, "email": "admin@example.com", "username": "admin"}
        )
    ).status_code == 409
    assert (
        await client.post(
            "/api/v1/pipeline/register",
            json={**valid, "email": "new@example.com", "username": "new.user", "password": "weakpass"},
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/pipeline/register",
            json={**valid, "email": "review@example.com", "username": "review.user", "role": "Reviewer"},
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_registration_rejects_wrong_expired_and_reused_codes(registration, monkeypatch):
    client, config = registration
    payload = {
        "name": "Linh Nguyễn",
        "email": "linh@example.com",
        "username": "linh.data",
        "password": "StrongPass9",
    }
    requested = await client.post("/api/v1/pipeline/register", json=payload)
    code = requested.json()["development_code"]

    wrong = await client.post("/api/v1/pipeline/register/verify", json={"email": payload["email"], "code": "000000"})
    assert wrong.status_code == 400
    verified = await client.post("/api/v1/pipeline/register/verify", json={"email": payload["email"], "code": code})
    assert verified.status_code == 201
    reused = await client.post("/api/v1/pipeline/register/verify", json={"email": payload["email"], "code": code})
    assert reused.status_code == 404

    second = {**payload, "email": "expired@example.com", "username": "expired.user"}
    # Lần đăng ký thứ hai đến từ IP khác: mỗi IP chỉ được 1 lần mỗi 10 phút.
    requested = await client.post("/api/v1/pipeline/register", json=second, headers={"X-Forwarded-For": "198.51.100.7"})
    with store.database(config.store_path) as conn:
        conn.execute("UPDATE pending_registrations SET expires = 0 WHERE email = ?", (second["email"],))
    expired = await client.post(
        "/api/v1/pipeline/register/verify",
        json={"email": second["email"], "code": requested.json()["development_code"]},
    )
    assert expired.status_code == 410


@pytest.mark.asyncio
async def test_registration_locks_after_five_wrong_codes(registration):
    client, _config = registration
    payload = {
        "name": "Linh Nguyễn",
        "email": "linh@example.com",
        "username": "linh.data",
        "password": "StrongPass9",
    }
    requested = await client.post("/api/v1/pipeline/register", json=payload)
    correct = requested.json()["development_code"]
    wrong = "000000" if correct != "000000" else "000001"
    for _ in range(5):
        assert (
            await client.post("/api/v1/pipeline/register/verify", json={"email": payload["email"], "code": wrong})
        ).status_code == 400
    locked = await client.post("/api/v1/pipeline/register/verify", json={"email": payload["email"], "code": correct})
    assert locked.status_code == 429


@pytest.mark.asyncio
async def test_only_admin_can_promote_registered_user_to_reviewer(registration):
    client, _config = registration
    payload = {
        "name": "Linh Nguyễn",
        "email": "linh@example.com",
        "username": "linh.data",
        "password": "StrongPass9",
    }
    created = (await request_and_verify(client, payload)).json()

    forbidden = await client.patch(f"/api/v1/pipeline/users/{created['id']}/role", json={"role": "Reviewer"})
    assert forbidden.status_code == 403

    await client.delete("/api/v1/pipeline/session")
    admin_login = await client.post("/api/v1/pipeline/session", json={"username": "admin", "password": "Admin@123"})
    assert admin_login.status_code == 200 and admin_login.json()["role"] == "Admin"
    listed = (await client.get("/api/v1/pipeline/users")).json()
    assert next(item for item in listed if item["id"] == "admin")["managed"] is False
    assert next(item for item in listed if item["id"] == created["id"])["managed"] is True

    promoted = await client.patch(f"/api/v1/pipeline/users/{created['id']}/role", json={"role": "Reviewer"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "Reviewer"
    assert (await client.patch("/api/v1/pipeline/users/an/role", json={"role": "Engineer"})).status_code == 409
    assert (await client.patch("/api/v1/pipeline/users/missing/role", json={"role": "Reviewer"})).status_code == 404
    assert (
        await client.patch(f"/api/v1/pipeline/users/{created['id']}/role", json={"role": "Admin"})
    ).status_code == 422

    await client.delete("/api/v1/pipeline/session")
    user_login = await client.post(
        "/api/v1/pipeline/session", json={"username": "linh.data", "password": "StrongPass9"}
    )
    assert user_login.status_code == 200 and user_login.json()["role"] == "Reviewer"


def payload_for(name):
    return {"name": "Người dùng", "email": f"{name}@example.com", "username": name, "password": "StrongPass9"}


@pytest.mark.asyncio
async def test_one_registration_per_ip_every_ten_minutes(registration):
    client, config = registration
    assert (await client.post("/api/v1/pipeline/register", json=payload_for("first.user"))).status_code == 202

    blocked = await client.post("/api/v1/pipeline/register", json=payload_for("second.user"))
    assert blocked.status_code == 429
    assert "10 phút" in blocked.json()["detail"]
    assert 0 < int(blocked.headers["Retry-After"]) <= api.REGISTRATION_IP_SECONDS
    assert store.get_pending_registration(config.store_path, "second.user@example.com") is None

    other_ip = await client.post(
        "/api/v1/pipeline/register", json=payload_for("second.user"), headers={"X-Forwarded-For": "203.0.113.9"}
    )
    assert other_ip.status_code == 202

    with store.database(config.store_path) as conn:
        conn.execute("UPDATE registration_ips SET at = at - ?", (api.REGISTRATION_IP_SECONDS + 1,))
    assert (await client.post("/api/v1/pipeline/register", json=payload_for("third.user"))).status_code == 202


@pytest.mark.asyncio
async def test_rejected_registration_does_not_use_the_ip_slot(registration, monkeypatch):
    client, _config = registration
    duplicate = {**payload_for("engineer"), "email": "x@example.com"}
    assert (await client.post("/api/v1/pipeline/register", json=duplicate)).status_code == 409

    def smtp_down(*_args):
        raise OSError("SMTP down")

    monkeypatch.setattr(api.email_delivery, "send_verification_code", smtp_down)
    assert (await client.post("/api/v1/pipeline/register", json=payload_for("first.user"))).status_code == 503
    monkeypatch.undo()
    assert (await client.post("/api/v1/pipeline/register", json=payload_for("first.user"))).status_code == 202


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("172.18.0.5", "203.0.113.9", "203.0.113.9"),  # web/Caddy trong mạng Docker chuyển tiếp IP thật
        ("172.18.0.5", "203.0.113.9, 172.18.0.2", "203.0.113.9"),
        ("8.8.8.8", "1.2.3.4", "8.8.8.8"),  # client Internet tự gửi header: bỏ qua
        ("172.18.0.5", "không-phải-ip", "172.18.0.5"),
        ("172.18.0.5", "", "172.18.0.5"),
    ],
)
def test_client_ip_trusts_forwarded_header_only_from_private_proxy(peer, forwarded, expected):
    request = SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": forwarded})
    assert api.client_ip(request) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("registration", [False], ids=["direct"], indirect=True)
async def test_direct_registration_creates_engineer_and_starts_session(registration):
    client, config = registration
    response = await client.post("/api/v1/pipeline/register", json=payload_for("linh.data"))
    assert response.status_code == 201, response.text
    user = response.json()
    assert user["role"] == "Engineer" and user["username"] == "linh.data" and user["email"] == "linh.data@example.com"
    assert "development_code" not in user
    assert (await client.get("/api/v1/pipeline/session")).json() == user
    assert store.get_pending_registration(config.store_path, "linh.data@example.com") is None

    await client.delete("/api/v1/pipeline/session")
    login = await client.post("/api/v1/pipeline/session", json={"username": "LINH.DATA", "password": "StrongPass9"})
    assert login.status_code == 200 and login.json()["id"] == user["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("registration", [False], ids=["direct"], indirect=True)
async def test_direct_registration_keeps_duplicate_and_ip_limits(registration):
    client, _config = registration
    assert (await client.post("/api/v1/pipeline/register", json=payload_for("linh.data"))).status_code == 201
    other_ip = {"X-Forwarded-For": "203.0.113.9"}
    duplicate = await client.post("/api/v1/pipeline/register", json=payload_for("linh.data"), headers=other_ip)
    assert duplicate.status_code == 409
    assert (await client.post("/api/v1/pipeline/register", json=payload_for("minh.data"))).status_code == 429
    assert (
        await client.post("/api/v1/pipeline/register", json=payload_for("minh.data"), headers=other_ip)
    ).status_code == 201


@pytest.mark.asyncio
async def test_production_blocks_built_in_account_with_default_password(tmp_path, monkeypatch):
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    environment = SimpleNamespace(app_env="production")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "get_settings", lambda: environment)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://test") as production_client:
            response = await production_client.post(
                "/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"}
            )

        assert response.status_code == 503
        assert "production" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(auth.settings, None)


@pytest.mark.asyncio
async def test_production_allows_changed_built_in_passwords_and_registered_accounts(tmp_path, monkeypatch):
    config = PipelineSettings(
        _env_file=None,
        store_path=str(tmp_path / "runs.sqlite3"),
        engineer_password="EngineerChanged9",
        admin_password="AdminChanged9",
    )
    app.dependency_overrides[auth.settings] = lambda: config
    environment = SimpleNamespace(app_env="production")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "get_settings", lambda: environment)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://test") as production_client:
            registered = await production_client.post(
                "/api/v1/pipeline/register", json=payload_for("production.user")
            )
            assert registered.status_code == 201, registered.text
            registered_user = registered.json()
            assert (await production_client.get("/api/v1/pipeline/session")).json() == registered_user

            await production_client.delete("/api/v1/pipeline/session")
            registered_login = await production_client.post(
                "/api/v1/pipeline/session",
                json={"username": "production.user", "password": "StrongPass9"},
            )
            assert registered_login.status_code == 200

            await production_client.delete("/api/v1/pipeline/session")
            engineer_login = await production_client.post(
                "/api/v1/pipeline/session",
                json={"username": "engineer", "password": "EngineerChanged9"},
            )
            assert engineer_login.status_code == 200
            assert (await production_client.get("/api/v1/pipeline/session")).json()["role"] == "Engineer"

            await production_client.delete("/api/v1/pipeline/session")
            admin_login = await production_client.post(
                "/api/v1/pipeline/session", json={"username": "admin", "password": "AdminChanged9"}
            )
            assert admin_login.status_code == 200
            promoted = await production_client.patch(
                f"/api/v1/pipeline/users/{registered_user['id']}/role", json={"role": "Reviewer"}
            )
            assert promoted.status_code == 200
            assert promoted.json()["role"] == "Reviewer"
    finally:
        app.dependency_overrides.pop(auth.settings, None)


@pytest.mark.asyncio
async def test_login_rate_limit_is_scoped_by_ip_and_username(registration):
    client, _config = registration
    wrong = {"username": "engineer", "password": "WrongPassword9"}

    for _ in range(api.MAX_LOGIN_ATTEMPTS - 1):
        assert (await client.post("/api/v1/pipeline/session", json=wrong)).status_code == 401

    blocked = await client.post("/api/v1/pipeline/session", json=wrong)
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers["Retry-After"]) <= api.LOGIN_WINDOW_SECONDS
    assert (
        await client.post(
            "/api/v1/pipeline/session",
            json={"username": "engineer", "password": "Engineer@123"},
        )
    ).status_code == 429

    # Username khác trên cùng IP và cùng username từ IP khác có hạn mức độc lập.
    assert (
        await client.post(
            "/api/v1/pipeline/session",
            json={"username": "reviewer", "password": "Reviewer@123"},
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/pipeline/session",
            json={"username": "engineer", "password": "Engineer@123"},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_successful_login_clears_failures_and_expired_lock_resets(registration):
    client, config = registration
    headers = {"X-Forwarded-For": "198.51.100.10"}
    wrong = {"username": "ENGINEER", "password": "WrongPassword9"}
    correct = {"username": "engineer", "password": "Engineer@123"}

    for _ in range(api.MAX_LOGIN_ATTEMPTS - 1):
        assert (await client.post("/api/v1/pipeline/session", json=wrong, headers=headers)).status_code == 401
    assert (await client.post("/api/v1/pipeline/session", json=correct, headers=headers)).status_code == 200

    # Sau lần đăng nhập đúng, bộ đếm bắt đầu lại từ đầu; username được chuẩn hóa không phân biệt hoa thường.
    for _ in range(api.MAX_LOGIN_ATTEMPTS - 1):
        assert (await client.post("/api/v1/pipeline/session", json=wrong, headers=headers)).status_code == 401
    assert (await client.post("/api/v1/pipeline/session", json=wrong, headers=headers)).status_code == 429

    with store.database(config.store_path) as conn:
        conn.execute("UPDATE login_failures SET first_at = first_at - ?", (api.LOGIN_WINDOW_SECONDS + 1,))
    assert (await client.post("/api/v1/pipeline/session", json=correct, headers=headers)).status_code == 200
