from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.main import app
from src.pipeline import api, auth, store
from src.pipeline.settings import PipelineSettings


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

    response = await client.post(
        "/api/v1/pipeline/register",
        json={"name": "  Linh Nguyễn  ", "username": "Linh.Data", "password": "StrongPass9"},
    )

    assert response.status_code == 201, response.text
    user = response.json()
    assert user == {
        "id": user["id"],
        "username": "linh.data",
        "name": "Linh Nguyễn",
        "role": "Engineer",
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
    payload = {"name": "Linh Nguyễn", "username": "linh.data", "password": "StrongPass9"}
    created = (await client.post("/api/v1/pipeline/register", json=payload)).json()
    assert (await client.delete("/api/v1/pipeline/session")).status_code == 200

    response = await client.post(
        "/api/v1/pipeline/session", json={"username": "LINH.DATA", "password": "StrongPass9"}
    )

    assert response.status_code == 200
    assert response.json() == created
    assert (await client.get("/api/v1/pipeline/session")).status_code == 200


@pytest.mark.asyncio
async def test_registration_rejects_duplicate_reserved_weak_and_privileged_accounts(registration):
    client, _config = registration
    valid = {"name": "Linh Nguyễn", "username": "linh.data", "password": "StrongPass9"}
    assert (await client.post("/api/v1/pipeline/register", json=valid)).status_code == 201
    assert (
        await client.post("/api/v1/pipeline/register", json={**valid, "username": "LINH.DATA"})
    ).status_code == 409
    assert (
        await client.post("/api/v1/pipeline/register", json={**valid, "username": "engineer"})
    ).status_code == 409
    assert (
        await client.post("/api/v1/pipeline/register", json={**valid, "username": "new.user", "password": "weakpass"})
    ).status_code == 422
    assert (
        await client.post("/api/v1/pipeline/register", json={**valid, "username": "review.user", "role": "Reviewer"})
    ).status_code == 422
