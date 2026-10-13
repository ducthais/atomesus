from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import SecretStr

from src.pipeline import connections, connectors, postgres, store
from tests.test_api.test_connections import PAYLOAD, dns_rows, use_session
from tests.test_api.test_connections import registry as registry

FOREIGN_KEY = {
    "name": "orders_account_id_fkey",
    "table": "orders",
    "columns": ["account_id"],
    "ref_schema": "sales",
    "ref_table": "accounts",
    "ref_columns": ["id"],
}


class FakePostgres:
    def __init__(self):
        self.schemas = {"sales": True, "empty_schema": True, "no_usage": False}
        self.columns = {
            "accounts": [{"name": "id", "type": "bigint"}],
            "empty": [],
            "orders": [{"name": "id", "type": "bigint"}, {"name": "account_id", "type": "bigint"}],
        }
        self.tables = {"sales": {"accounts": True, "empty": True, "orders": True, "secret": False}}
        self.estimates = [("accounts", 20), ("empty", 0), ("orders", -1), ("secret", -1)]
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        result = MagicMock()
        if query == connections.SCHEMAS_SQL:
            assert "has_schema_privilege(n.oid, 'USAGE')" in query
            assert "has_table_privilege(c.oid, 'SELECT')" in query
            assert "n.nspname !~ '^pg_'" in query and "n.nspname <> 'information_schema'" in query
            assert "c.relkind IN ('r', 'p') AND NOT c.relispartition" in query
            result.fetchall.return_value = [
                (schema, sum(self.tables.get(schema, {}).values()))
                for schema, usable in sorted(self.schemas.items())
                if usable and not schema.startswith("pg_") and schema != "information_schema"
            ]
        elif query == connections.TABLES_SQL:
            assert "c.relkind IN ('r', 'p') AND NOT c.relispartition" in query
            result.fetchall.return_value = sorted(self.tables.get(params[0], {}).items())
        elif query == connectors.COLUMNS_SQL:
            result.fetchall.return_value = [
                (table, col["name"], col["type"])
                for table, columns in self.columns.items()
                for col in columns
                if self.tables.get(params[0], {}).get(table)
            ]
        elif query == connectors.KEYS_SQL:
            result.fetchall.return_value = [
                ("p", "accounts", ["id"], None, None, [], "accounts_pkey"),
                ("p", "orders", ["id"], None, None, [], "orders_pkey"),
                ("f", "orders", ["account_id"], "sales", "accounts", ["id"], FOREIGN_KEY["name"]),
                ("f", "orders", ["account_id"], "sales", "accounts", ["id"], "partition_fkey"),
                ("f", "secret", ["account_id"], "sales", "accounts", ["id"], "secret_fkey"),
            ]
        elif isinstance(query, str) and "reltuples" in query:
            result.fetchall.return_value = self.estimates
        elif isinstance(query, str) and "COALESCE(has_schema_privilege" in query:
            schema = params[0]
            result.fetchone.return_value = (schema in self.schemas, self.schemas.get(schema, False))
        elif isinstance(query, psycopg.sql.Composed):
            assert query.as_string() == 'SELECT count(*) FROM (SELECT 1 FROM "sales"."orders" LIMIT %s) capped'
            assert params == (connectors.ROW_COUNT_CAP + 1,)
            result.fetchone.return_value = (connectors.ROW_COUNT_CAP + 1,)
        else:
            raise AssertionError(f"Truy vấn ngoài metadata: {query!r}")
        return result


@pytest_asyncio.fixture
async def browse(registry):
    response = await registry.client.post("/api/v1/connections", json=PAYLOAD)
    assert response.status_code == 201, response.text
    registry.id = response.json()["id"]
    registry.source = FakePostgres()
    registry.pg.__enter__.return_value = registry.source
    registry.dns.reset_mock()
    registry.connect.reset_mock()
    registry.pg.reset_mock()
    return registry


@pytest.mark.asyncio
async def test_schemas_list_usable_user_schemas_and_readable_table_counts(browse):
    browse.source.schemas.update({"pg_catalog": True, "pg_toast": True, "information_schema": True, "pgx": True})
    response = await browse.client.get(f"/api/v1/connections/{browse.id}/schemas")
    assert response.status_code == 200, response.text
    assert response.json() == [
        {"name": "empty_schema", "table_count": 0},
        {"name": "pgx", "table_count": 0},
        {"name": "sales", "table_count": 3},
    ]
    browse.dns.assert_called_once_with(PAYLOAD["host"], 5432, type=connections.socket.SOCK_STREAM)
    assert browse.connect.call_args.kwargs == {
        "host": PAYLOAD["host"],
        "hostaddr": "8.8.8.8",
        "port": PAYLOAD["port"],
        "dbname": PAYLOAD["database"],
        "user": PAYLOAD["user"],
        "password": PAYLOAD["password"],
        "sslmode": PAYLOAD["sslmode"],
        "connect_timeout": 5,
        "options": connections.READ_ONLY_OPTIONS,
    }
    browse.pg.__exit__.assert_called_once()
    assert PAYLOAD["password"] not in response.text


@pytest.mark.asyncio
async def test_tables_reuse_catalog_columns_keys_and_capped_row_estimates(browse):
    response = await browse.client.get(f"/api/v1/connections/{browse.id}/tables", params={"schema": "sales"})
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "name": "accounts",
            "columns": browse.source.columns["accounts"],
            "primary_key": ["id"],
            "foreign_keys": [],
            "row_estimate": 20,
        },
        {"name": "empty", "columns": [], "primary_key": [], "foreign_keys": [], "row_estimate": 0},
        {
            "name": "orders",
            "columns": browse.source.columns["orders"],
            "primary_key": ["id"],
            "foreign_keys": [FOREIGN_KEY],
            "row_estimate": connectors.ROW_COUNT_CAP + 1,
        },
    ]
    assert sum(isinstance(query, psycopg.sql.Composed) for query, _ in browse.source.queries) == 1
    browse.pg.__exit__.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema,reason", [("missing", "không tồn tại"), ("no_usage", "USAGE")])
async def test_tables_and_selection_explain_missing_or_unusable_schema(browse, schema, reason):
    response = await browse.client.get(f"/api/v1/connections/{browse.id}/tables", params={"schema": schema})
    assert response.status_code == 422 and reason in response.json()["detail"]
    response = await browse.client.put(
        f"/api/v1/connections/{browse.id}/selection", json={"schema": schema, "tables": ["orders"]}
    )
    assert response.status_code == 422 and reason in response.json()["detail"]
    assert not any(query == connectors.COLUMNS_SQL for query, _ in browse.source.queries)
    assert store.get_connection_selection(browse.config.store_path, browse.id, "minh") is None


@pytest.mark.asyncio
async def test_empty_schema_returns_no_tables(browse):
    browse.source.estimates = []
    response = await browse.client.get(f"/api/v1/connections/{browse.id}/tables", params={"schema": "empty_schema"})
    assert response.status_code == 200 and response.json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [None, "", " ", "sales\x00", "x" * 129])
async def test_tables_reject_invalid_schema_with_a_clear_reason(browse, schema):
    response = await browse.client.get(
        f"/api/v1/connections/{browse.id}/tables", params={} if schema is None else {"schema": schema}
    )
    assert response.status_code == 422 and "schema" in response.json()["detail"]
    assert browse.source.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", ["pg_catalog", "information_schema"])
async def test_tables_and_selection_reject_system_schemas(browse, schema):
    browse.source.schemas[schema] = True
    response = await browse.client.get(f"/api/v1/connections/{browse.id}/tables", params={"schema": schema})
    assert response.status_code == 422 and "hệ thống" in response.json()["detail"]
    response = await browse.client.put(
        f"/api/v1/connections/{browse.id}/selection", json={"schema": schema, "tables": ["orders"]}
    )
    assert response.status_code == 422 and "hệ thống" in response.json()["detail"]


@pytest.mark.asyncio
async def test_selection_is_persisted_replaced_and_deleted_with_connection(browse):
    url = f"/api/v1/connections/{browse.id}/selection"
    assert (await browse.client.get(url)).json() is None
    browse.source.schemas["archive"] = True
    browse.source.tables["archive"] = {"orders": True}
    for selection in (
        {"schema": "sales", "tables": ["orders", "accounts"]},
        {"schema": "sales", "tables": ["empty"]},
        {"schema": "archive", "tables": ["orders"]},
    ):
        response = await browse.client.put(url, json=selection)
        assert response.status_code == 200 and response.json() == selection
        assert (await browse.client.get(url)).json() == selection
        assert store.get_connection_selection(browse.config.store_path, browse.id, "minh") == selection
    with store.database(browse.config.store_path) as conn:
        assert conn.execute("SELECT count(*) FROM connection_selections").fetchone()[0] == 1
    browse.connect.reset_mock()
    browse.dns.reset_mock()
    assert (await browse.client.get(url)).json() == {"schema": "archive", "tables": ["orders"]}
    browse.connect.assert_not_called()
    browse.dns.assert_not_called()
    assert (await browse.client.delete(f"/api/v1/connections/{browse.id}")).status_code == 204
    with store.database(browse.config.store_path) as conn:
        assert conn.execute("SELECT count(*) FROM connection_selections").fetchone()[0] == 0
    assert (await browse.client.get(url)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("table,reason", [("missing", "không tồn tại"), ("secret", "SELECT")])
async def test_selection_rejects_missing_or_unreadable_table_without_changing_saved_selection(browse, table, reason):
    url = f"/api/v1/connections/{browse.id}/selection"
    selection = {"schema": "sales", "tables": ["accounts"]}
    assert (await browse.client.put(url, json=selection)).status_code == 200
    response = await browse.client.put(url, json={"schema": "sales", "tables": ["orders", table]})
    assert response.status_code == 422 and reason in response.json()["detail"]
    assert (await browse.client.get(url)).json() == selection


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"schema": "sales", "tables": []},
        {"schema": "sales", "tables": ["orders", "orders"]},
        {"schema": "sales", "tables": [""]},
        {"schema": "sales", "tables": [" "]},
        {"schema": "sales", "tables": ["orders\x00"]},
        {"schema": "", "tables": ["orders"]},
        {"schema": "sales\x00", "tables": ["orders"]},
        {"schema": "sales", "tables": "orders"},
        {"schema": "sales", "tables": ["orders"], "owner_id": "user_other"},
        {"schema": "sales", "tables": ["orders"], "password": PAYLOAD["password"]},
    ],
)
async def test_selection_rejects_invalid_body_without_echoing_input_or_connecting(browse, body):
    response = await browse.client.put(f"/api/v1/connections/{browse.id}/selection", json=body)
    assert response.status_code == 422 and "Lựa chọn không hợp lệ" in response.json()["detail"]
    assert PAYLOAD["password"] not in response.text
    browse.connect.assert_not_called()
    browse.dns.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,status", [("user_other", 404), ("an", 403), (None, 401)])
async def test_browse_and_selection_are_owner_only_and_require_engineer_session(browse, identity, status):
    if identity is None:
        browse.client.cookies.clear()
    else:
        use_session(browse.client, identity)
    for connection_id in (browse.id, "missing"):
        prefix = f"/api/v1/connections/{connection_id}"
        for suffix in ("/schemas", "/tables?schema=sales", "/selection"):
            response = await browse.client.get(prefix + suffix)
            assert response.status_code == status, response.text
        response = await browse.client.put(prefix + "/selection", json={"schema": "sales", "tables": ["orders"]})
        assert response.status_code == status, response.text
    browse.connect.assert_not_called()
    browse.dns.assert_not_called()


@pytest.mark.asyncio
async def test_owner_gets_404_for_missing_connection(browse):
    prefix = "/api/v1/connections/missing"
    for suffix in ("/schemas", "/tables?schema=sales", "/selection"):
        assert (await browse.client.get(prefix + suffix)).status_code == 404
    response = await browse.client.put(prefix + "/selection", json={"schema": "sales", "tables": ["orders"]})
    assert response.status_code == 404
    browse.connect.assert_not_called()
    browse.dns.assert_not_called()


@pytest.mark.asyncio
async def test_every_saved_connection_open_rechecks_dns_and_rejects_rebinding(browse, caplog):
    assert (await browse.client.get(f"/api/v1/connections/{browse.id}/schemas")).status_code == 200
    browse.dns.return_value = dns_rows("10.0.0.5")
    browse.connect.reset_mock()
    for suffix in ("/schemas", "/tables?schema=sales"):
        response = await browse.client.get(f"/api/v1/connections/{browse.id}" + suffix)
        assert response.status_code == 422 and "private" in response.json()["detail"]
        assert PAYLOAD["password"] not in response.text
    response = await browse.client.put(
        f"/api/v1/connections/{browse.id}/selection", json={"schema": "sales", "tables": ["orders"]}
    )
    assert response.status_code == 422
    assert browse.dns.call_count == 4
    browse.connect.assert_not_called()
    assert PAYLOAD["password"] not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("during_query", [False, True])
async def test_saved_connection_errors_are_safe_and_close_open_connection(browse, caplog, during_query):
    with store.database(browse.config.store_path) as conn:
        conn.execute("UPDATE connections SET host = 'ssl.example.com' WHERE id = ?", (browse.id,))
    error = psycopg.OperationalError(f'connection to server at "ssl.example.com" failed: timeout {PAYLOAD["password"]}')
    if during_query:
        browse.source.execute = MagicMock(side_effect=error)
    else:
        browse.connect.side_effect = error
    response = await browse.client.get(f"/api/v1/connections/{browse.id}/schemas")
    assert response.status_code == 422 and "Không kết nối được" in response.json()["detail"]
    assert "SSL" not in response.json()["detail"]
    assert PAYLOAD["password"] not in response.text and PAYLOAD["password"] not in caplog.text
    assert browse.connect.call_args.kwargs["host"] == "ssl.example.com"
    if during_query:
        browse.pg.__exit__.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "invalid", "different"])
async def test_saved_connection_refuses_missing_invalid_or_wrong_key_safely(browse, key, caplog):
    browse.config.connection_key = SecretStr(Fernet.generate_key().decode() if key == "different" else key)
    response = await browse.client.get(f"/api/v1/connections/{browse.id}/schemas")
    assert response.status_code == 503 and "PIPELINE_CONNECTION_KEY" in response.json()["detail"]
    assert PAYLOAD["password"] not in response.text and PAYLOAD["password"] not in caplog.text
    browse.connect.assert_not_called()


def test_decryption_and_opening_can_be_reused_without_user_permission_logic(browse):
    metadata, encrypted = store.get_connection(browse.config.store_path, browse.id, "minh")
    with connections.open_connection(browse.config, metadata, encrypted) as conn:
        assert conn is browse.source
    assert "password" not in metadata and "password_encrypted" not in metadata
    assert browse.connect.call_args.kwargs["password"] == PAYLOAD["password"]


def test_context_manager_checks_owner_before_dns_or_decryption(browse):
    with pytest.raises(HTTPException) as caught, connections.saved_connection(browse.config, browse.id, "user_other"):
        pytest.fail("Không được mở kết nối của người khác")
    assert caught.value.status_code == 404
    browse.connect.assert_not_called()
    browse.dns.assert_not_called()


def test_store_selection_write_rechecks_owner_and_existence(browse):
    assert not store.save_connection_selection(browse.config.store_path, browse.id, "user_other", "sales", ["orders"])
    assert not store.save_connection_selection(browse.config.store_path, "missing", "minh", "sales", ["orders"])
    assert store.get_connection_selection(browse.config.store_path, browse.id, "minh") is None


def test_legacy_catalog_helpers_keep_connection_lifecycle_and_results(monkeypatch):
    source = FakePostgres()
    source.estimates = source.estimates[:-1]
    opened, closed = [], []

    @contextmanager
    def connection(config):
        opened.append(config)
        try:
            yield source
        finally:
            closed.append(config)

    monkeypatch.setattr(postgres, "connection", connection)
    config = SimpleNamespace()
    assert connectors.postgres_columns(config, "sales") == {
        "accounts": source.columns["accounts"],
        "orders": source.columns["orders"],
    }
    primary, foreign = connectors.postgres_keys(config, "sales")
    assert primary == {"accounts": ["id"], "orders": ["id"]}
    assert foreign == [FOREIGN_KEY, {**FOREIGN_KEY, "name": "secret_fkey", "table": "secret"}]
    assert connectors.postgres_row_estimates(config, "sales") == {
        "accounts": 20,
        "empty": 0,
        "orders": connectors.ROW_COUNT_CAP + 1,
    }
    assert connectors.schema_visibility(config, "sales") == (True, True)
    assert connectors.schema_visibility(config, "no_usage") == (True, False)
    assert connectors.schema_visibility(config, "missing") == (False, False)
    assert opened == closed == [config] * 6
