import hashlib
import socket
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import SecretStr

from src import main
from src.main import app
from src.pipeline import auth, connections, store
from src.pipeline.settings import PipelineSettings
from src.run_report import RunReport, save_run_report

PAYLOAD = {
    "name": "Kho dữ liệu",
    "host": "db.example.com",
    "port": 5432,
    "database": "analytics",
    "user": "reader",
    "password": "Secret-source-password9",
    "sslmode": "verify-full",
}


def dns_rows(*addresses):
    return [
        (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 5432)) for ip in addresses
    ]


def use_session(client, user_id):
    client.cookies.set(auth.COOKIE, f"session-{user_id}", path=auth.COOKIE_PATH)


@pytest_asyncio.fixture
async def registry(client, tmp_path, monkeypatch):
    config = PipelineSettings(
        _env_file=None,
        store_path=str(tmp_path / "runs.sqlite3"),
        connection_key=Fernet.generate_key().decode(),
        connection_allow_private=False,
    )
    environment = SimpleNamespace(app_env="staging", app_name="Atomesus")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(connections, "get_settings", lambda: environment)
    app.dependency_overrides[auth.settings] = lambda: config
    store.create_user(
        config.store_path,
        user_id="user_other",
        username="other.engineer",
        name="Engineer khác",
        role="Engineer",
        password_hash="unused",
        created_at=time.time(),
    )
    with store.database(config.store_path) as conn:
        for user_id in ("minh", "an", "user_other", "admin"):
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (hashlib.sha256(f"session-{user_id}".encode()).hexdigest(), user_id, time.time() + 60),
            )
    use_session(client, "minh")
    dns = MagicMock(return_value=dns_rows("8.8.8.8"))
    monkeypatch.setattr(connections.socket, "getaddrinfo", dns)
    pg = MagicMock()
    pg.__enter__.return_value.execute.return_value.fetchone.return_value = (True,)
    connect = MagicMock(return_value=pg)
    monkeypatch.setattr(connections.psycopg, "connect", connect)
    try:
        yield SimpleNamespace(client=client, config=config, environment=environment, dns=dns, connect=connect, pg=pg)
    finally:
        app.dependency_overrides.pop(auth.settings, None)


@pytest.mark.asyncio
async def test_probe_is_read_only_pins_dns_and_does_not_save(registry):
    response = await registry.client.post("/api/v1/connections/test", json=PAYLOAD)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert store.list_connections(registry.config.store_path, "minh") == []
    registry.dns.assert_called_once_with(PAYLOAD["host"], 5432, type=socket.SOCK_STREAM)
    kwargs = registry.connect.call_args.kwargs
    assert kwargs == {
        "host": PAYLOAD["host"],
        "hostaddr": "8.8.8.8",
        "port": 5432,
        "dbname": PAYLOAD["database"],
        "user": PAYLOAD["user"],
        "password": PAYLOAD["password"],
        "sslmode": "verify-full",
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000",
    }
    query = registry.pg.__enter__.return_value.execute.call_args.args[0]
    assert "has_table_privilege(c.oid, 'SELECT')" in query
    assert "has_schema_privilege(n.oid, 'USAGE')" in query
    registry.pg.__exit__.assert_called_once()
    assert PAYLOAD["password"] not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["8.8.8.8", "2606:4700:4700::1111"])
async def test_public_ip_and_request_defaults_are_supported(registry, host):
    payload = {key: value for key, value in PAYLOAD.items() if key not in ("name", "port", "sslmode")}
    response = await registry.client.post("/api/v1/connections", json={**payload, "host": host})
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Postgres"
    assert response.json()["port"] == 5432 and response.json()["sslmode"] == "require"
    assert registry.connect.call_args.kwargs["hostaddr"] == host
    registry.dns.assert_not_called()


@pytest.mark.asyncio
async def test_save_encrypts_password_and_scopes_list_and_delete_to_session(registry):
    client, config = registry.client, registry.config
    response = await client.post("/api/v1/connections", json=PAYLOAD)
    assert response.status_code == 201, response.text
    saved = response.json()
    registry.connect.assert_called_once()
    assert saved["owner_id"] == "minh"
    assert "password" not in saved and "password_encrypted" not in saved
    with store.database(config.store_path) as conn:
        encrypted = conn.execute("SELECT password_encrypted FROM connections WHERE id = ?", (saved["id"],)).fetchone()[
            0
        ]
    assert isinstance(encrypted, str) and PAYLOAD["password"] not in encrypted
    assert (
        Fernet(config.connection_key.get_secret_value().encode()).decrypt(encrypted.encode()).decode()
        == PAYLOAD["password"]
    )
    assert (await client.get("/api/v1/connections")).json() == [saved]

    use_session(client, "user_other")
    assert (await client.get("/api/v1/connections")).json() == []
    assert (await client.delete(f"/api/v1/connections/{saved['id']}")).status_code == 404
    other = await client.post("/api/v1/connections", json={**PAYLOAD, "name": "Kết nối khác"})
    assert other.status_code == 201 and other.json()["owner_id"] == "user_other"
    assert (await client.get("/api/v1/connections")).json() == [other.json()]

    use_session(client, "minh")
    assert (await client.get("/api/v1/connections")).json() == [saved]
    assert (await client.delete(f"/api/v1/connections/{saved['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/connections/{saved['id']}")).status_code == 404
    assert (await client.get("/api/v1/connections")).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["DEPLOYED", "APPROVED"])
async def test_delete_preserves_connections_used_by_deployed_pipelines(registry, status):
    saved = (await registry.client.post("/api/v1/connections", json=PAYLOAD)).json()
    report = RunReport.model_validate_json(Path("fixtures/run_reports/deployed.json").read_text(encoding="utf-8"))
    report = report.model_copy(
        update={
            "status": status,
            "spec": {**report.spec, "sources": [{"connection_ref": f"conn:{saved['id']}"}]},
        }
    )
    save_run_report(registry.config.store_path, report)
    use_session(registry.client, "user_other")
    assert (await registry.client.delete(f"/api/v1/connections/{saved['id']}")).status_code == 404
    use_session(registry.client, "minh")
    response = await registry.client.delete(f"/api/v1/connections/{saved['id']}")
    assert response.status_code == (409 if status == "DEPLOYED" else 204), response.text
    assert bool(store.list_connections(registry.config.store_path, "minh")) == (status == "DEPLOYED")


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["an", "admin"])
async def test_only_engineer_can_mutate_or_probe(registry, user_id):
    use_session(registry.client, user_id)
    for endpoint in ("/api/v1/connections", "/api/v1/connections/test"):
        assert (await registry.client.post(endpoint, json=PAYLOAD)).status_code == 403
    assert (await registry.client.delete("/api/v1/connections/missing")).status_code == 403
    registry.connect.assert_not_called()
    registry.dns.assert_not_called()


@pytest.mark.asyncio
async def test_reviewer_listing_never_exposes_host_or_user(registry):
    saved = (await registry.client.post("/api/v1/connections", json=PAYLOAD)).json()
    with store.database(registry.config.store_path) as conn:
        conn.execute("UPDATE connections SET owner_id = 'an' WHERE id = ?", (saved["id"],))
    use_session(registry.client, "an")
    assert (await registry.client.get("/api/v1/connections")).json() == [{"id": saved["id"], "name": saved["name"]}]


@pytest.mark.asyncio
async def test_all_endpoints_require_a_session(registry):
    registry.client.cookies.clear()
    assert (await registry.client.get("/api/v1/connections")).status_code == 401
    assert (await registry.client.post("/api/v1/connections", json=PAYLOAD)).status_code == 401
    assert (await registry.client.post("/api/v1/connections/test", json=PAYLOAD)).status_code == 401
    assert (await registry.client.delete("/api/v1/connections/missing")).status_code == 401
    registry.connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_private", [False, True])
@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST.",
        "a.localhost",
        "127.0.0.1",
        "10.0.0.5",
        "169.254.169.254",
        "postgres",
        "Postgres.",
        "backend",
        "dagster",
        "deployer",
        "mock-api",
        "web",
        "caddy",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "224.0.0.1",
        "100.64.0.1",
    ],
)
async def test_staging_blocks_internal_hosts_even_when_override_is_set(registry, host, allow_private):
    registry.config.connection_allow_private = allow_private
    for endpoint in ("/api/v1/connections/test", "/api/v1/connections"):
        response = await registry.client.post(endpoint, json={**PAYLOAD, "host": host})
        assert response.status_code == 422, response.text
    registry.connect.assert_not_called()
    assert store.list_connections(registry.config.store_path, "minh") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [("10.0.0.5",), ("8.8.8.8", "::1"), ("8.8.8.8", "192.0.2.1")])
async def test_dns_rejects_all_answers_if_any_address_is_forbidden(registry, addresses):
    registry.dns.return_value = dns_rows(*addresses)
    for endpoint in ("/api/v1/connections/test", "/api/v1/connections"):
        assert (await registry.client.post(endpoint, json=PAYLOAD)).status_code == 422
    registry.connect.assert_not_called()


@pytest.mark.asyncio
async def test_dns_is_checked_again_when_saving_after_a_successful_probe(registry):
    assert (await registry.client.post("/api/v1/connections/test", json=PAYLOAD)).status_code == 200
    registry.dns.return_value = dns_rows("169.254.169.254")
    assert (await registry.client.post("/api/v1/connections", json=PAYLOAD)).status_code == 422
    registry.connect.assert_called_once()
    assert store.list_connections(registry.config.store_path, "minh") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", ["development", "test", "staging", "production"])
async def test_private_override_is_limited_to_local_environments(registry, app_env):
    registry.environment.app_env = app_env
    registry.config.connection_allow_private = True
    if app_env in ("development", "test"):
        assert connections.resolve_host("127.0.0.1", 5432, registry.config) == "127.0.0.1"
        registry.dns.return_value = dns_rows("10.0.0.5")
        assert connections.resolve_host("postgres", 5432, registry.config) == "10.0.0.5"
    else:
        with pytest.raises(HTTPException) as caught:
            connections.resolve_host("127.0.0.1", 5432, registry.config)
        assert caught.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (psycopg.errors.InvalidPassword(f"password authentication failed {PAYLOAD['password']}"), "mật khẩu"),
        (psycopg.errors.InvalidPassword('password authentication failed for user "ssl_reader"'), "mật khẩu"),
        (psycopg.OperationalError('password authentication failed for user "ssl_reader"'), "mật khẩu"),
        (psycopg.OperationalError(f"connection timed out {PAYLOAD['password']}"), "host"),
        (psycopg.OperationalError('connection to server at "ssl.example.com" failed: timeout expired'), "host"),
        (psycopg.OperationalError(f"server requires SSL {PAYLOAD['password']}"), "SSL"),
        (psycopg.OperationalError("server does not support SSL, but SSL was required"), "SSL"),
        (psycopg.OperationalError("SSL error: certificate verify failed"), "SSL"),
        (psycopg.OperationalError("no pg_hba.conf entry for host, no encryption"), "SSL"),
        (psycopg.errors.InsufficientPrivilege(f"permission denied {PAYLOAD['password']}"), "SELECT"),
        (psycopg.errors.InsufficientPrivilege("permission denied for table ssl_events"), "SELECT"),
        (psycopg.errors.InvalidCatalogName("database does not exist"), "Database"),
        (psycopg.errors.InvalidCatalogName('database "ssl_analytics" does not exist'), "Database"),
    ],
)
async def test_postgres_errors_are_safe_explained_and_never_saved(registry, error, reason):
    registry.connect.side_effect = error
    for endpoint in ("/api/v1/connections/test", "/api/v1/connections"):
        response = await registry.client.post(endpoint, json=PAYLOAD)
        assert response.status_code == 422
        assert reason in response.json()["detail"]
        assert PAYLOAD["password"] not in response.text
    assert store.list_connections(registry.config.store_path, "minh") == []


@pytest.mark.asyncio
async def test_no_select_privilege_fails_without_saving_and_closes_connection(registry):
    registry.pg.__enter__.return_value.execute.return_value.fetchone.return_value = (False,)
    for endpoint in ("/api/v1/connections/test", "/api/v1/connections"):
        response = await registry.client.post(endpoint, json=PAYLOAD)
        assert response.status_code == 422 and "SELECT" in response.json()["detail"]
    assert registry.pg.__exit__.call_count == 2
    assert store.list_connections(registry.config.store_path, "minh") == []


@pytest.mark.asyncio
async def test_permission_query_error_is_safe_and_closes_connection(registry):
    registry.pg.__enter__.return_value.execute.side_effect = psycopg.errors.InsufficientPrivilege(PAYLOAD["password"])
    response = await registry.client.post("/api/v1/connections", json=PAYLOAD)
    assert response.status_code == 422 and "SELECT" in response.json()["detail"]
    assert PAYLOAD["password"] not in response.text
    registry.pg.__exit__.assert_called_once()
    assert store.list_connections(registry.config.store_path, "minh") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [[], None])
async def test_unresolvable_dns_has_a_safe_reason(registry, answers):
    if answers is None:
        registry.dns.side_effect = socket.gaierror(f"DNS failed {PAYLOAD['password']}")
    else:
        registry.dns.return_value = answers
    response = await registry.client.post("/api/v1/connections/test", json=PAYLOAD)
    assert response.status_code == 422 and "DNS" in response.json()["detail"]
    assert PAYLOAD["password"] not in response.text
    registry.connect.assert_not_called()


@pytest.mark.asyncio
async def test_dns_timeout_returns_without_waiting_for_getaddrinfo(registry, monkeypatch):
    finished = Event()
    started = Event()

    def slow_dns(*args, **kwargs):
        started.set()
        finished.wait(10)
        return dns_rows("8.8.8.8")

    registry.dns.side_effect = slow_dns
    monkeypatch.setattr(connections, "DNS_TIMEOUT", 0.1)
    try:
        response = await registry.client.post("/api/v1/connections/test", json=PAYLOAD)
        assert started.wait(5) and not finished.is_set()
        assert response.status_code == 422 and "Không phân giải được host" in response.json()["detail"]
        registry.connect.assert_not_called()
    finally:
        finished.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"owner_id": "user_other"},
        {"port": 0},
        {"port": 65536},
        {"sslmode": "invalid"},
        {"host": "/tmp/postgres"},
        {"host": "http://db.example.com"},
        {"host": "db.example.com,127.0.0.1"},
        {"host": "fe80::1%eth0"},
        {"database": " "},
        {"user": "a\x00b"},
        {"password": ""},
        {"password": [PAYLOAD["password"]]},
        {"name": " "},
    ],
)
async def test_invalid_payload_never_echoes_secrets_or_connects(registry, invalid):
    for endpoint in ("/api/v1/connections/test", "/api/v1/connections"):
        response = await registry.client.post(endpoint, json={**PAYLOAD, **invalid})
        assert response.status_code == 422, response.text
        assert PAYLOAD["password"] not in response.text
    registry.connect.assert_not_called()
    registry.dns.assert_not_called()


@pytest.mark.asyncio
async def test_missing_key_in_development_allows_probe_but_refuses_storage(registry):
    registry.environment.app_env = "development"
    registry.config.connection_key = SecretStr("")
    assert (await registry.client.post("/api/v1/connections/test", json=PAYLOAD)).status_code == 200
    response = await registry.client.post("/api/v1/connections", json=PAYLOAD)
    assert response.status_code == 503 and "PIPELINE_CONNECTION_KEY" in response.json()["detail"]
    assert store.list_connections(registry.config.store_path, "minh") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", ["staging", "production"])
@pytest.mark.parametrize("key", ["", "invalid-key"])
async def test_startup_refuses_missing_or_invalid_key_before_touching_storage(tmp_path, monkeypatch, app_env, key):
    path = tmp_path / "not-created.sqlite3"
    config = PipelineSettings(_env_file=None, store_path=str(path), connection_key=key)
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(app_env=app_env, app_name="Atomesus"))
    monkeypatch.setattr(main, "PipelineSettings", lambda: config)
    with pytest.raises(RuntimeError, match="PIPELINE_CONNECTION_KEY"):
        async with main.lifespan(app):
            pytest.fail("Backend không được khởi động khi thiếu khóa hợp lệ")
    assert not path.exists()


@pytest.mark.asyncio
async def test_startup_accepts_valid_key(registry, monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: registry.environment)
    monkeypatch.setattr(main, "PipelineSettings", lambda: registry.config)
    async with main.lifespan(app):
        pass
