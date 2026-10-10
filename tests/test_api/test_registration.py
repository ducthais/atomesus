from types import SimpleNamespace

import pytest
import pytest_asyncio

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


@pytest_asyncio.fixture
async def registration(client, tmp_path, monkeypatch):
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
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
    requested = await client.post("/api/v1/pipeline/register", json=second)
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
