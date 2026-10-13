"""Nguồn conn:<id> đi qua cùng kiểm SSRF, giải mã và tài khoản chỉ đọc ở mọi phase."""

import sys
import traceback
from threading import BoundedSemaphore, Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, quote, unquote, urlsplit

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine

from src.agents import budget, resolve
from src.codegen import el_checks, reference, sandbox
from src.pipeline import connections, connectors, files, postgres, store
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.policy.models import SourceDecl
from tests.test_api.test_connections import dns_rows
from tests.test_codegen.test_reference import SALES_COLUMNS, sales_spec

PASSWORD = "secret@:/?#9"


def with_sources(policy, sources):
    domain = policy.domains.domains["sales"].model_copy(update={"sources": sources})
    domains = policy.domains.model_copy(update={"domains": {"sales": domain}})
    return policy.model_copy(update={"domains": domains})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    config = PipelineSettings(
        _env_file=None,
        store_path=str(tmp_path / "runs.sqlite3"),
        database_url="postgresql://platform:platform-secret@postgres/platform",
        connection_key=Fernet.generate_key().decode(),
        connection_allow_private=False,
    )
    metadata = {
        "id": "customer-db",
        "owner_id": "another-engineer",
        "name": "Postgres người dùng",
        "host": "db.example.com",
        "port": 5433,
        "database": "user analytics",
        "user": "reader@: /?#",
        "sslmode": "verify-full",
        "created_at": 1,
    }
    encrypted = Fernet(config.connection_key.get_secret_value().encode()).encrypt(PASSWORD.encode()).decode()
    store.save_connection(config.store_path, metadata, encrypted)
    monkeypatch.setattr(connections, "get_settings", lambda: SimpleNamespace(app_env="production"))
    dns = MagicMock(return_value=dns_rows("8.8.8.8"))
    monkeypatch.setattr(connections.socket, "getaddrinfo", dns)
    conn = MagicMock()

    def execute(query, params=None):
        result = MagicMock()
        text = query if isinstance(query, str) else query.as_string()
        if query == connectors.COLUMNS_SQL:
            result.fetchall.return_value = [
                (table, name, kind) for table, cols in SALES_COLUMNS.items() for name, kind in cols.items()
            ]
        elif query == connectors.KEYS_SQL:
            result.fetchall.return_value = [("p", "orders", ["order_id"], None, None, [], "orders_pkey")]
        elif "reltuples" in text:
            result.fetchall.return_value = [("orders", 2)]
        elif "COALESCE(has_schema_privilege" in text:
            result.fetchone.return_value = (True, True)
        elif "information_schema.columns" in text:
            result.fetchall.return_value = list(SALES_COLUMNS["orders"].items())
        elif query == budget.TABLE_BYTES_SQL:
            result.fetchone.return_value = (123,)
        elif " AS reference LIMIT " in text:
            result.description = [SimpleNamespace(name="ordered_at"), SimpleNamespace(name="revenue")]
            result.fetchall.return_value = [("2026-01-01", 100)]
        elif "count(*)" in text:
            result.fetchone.return_value = (2,)
        elif text.startswith('SELECT "order_id" FROM'):
            result.fetchall.return_value = [(1,), (2,)]
        else:
            result.description = [SimpleNamespace(name=name) for name in SALES_COLUMNS["orders"]]
            result.fetchall.return_value = [(1, "100", "2026-01-01", "paid"), (2, "0", "2026-01-01", "paid")]
        return result

    conn.execute.side_effect = execute
    context = MagicMock()
    context.__enter__.return_value = conn
    connect = MagicMock(return_value=context)
    monkeypatch.setattr(psycopg, "connect", connect)
    opened = MagicMock(wraps=connections.open_connection)
    monkeypatch.setattr(connections, "open_connection", opened)
    decl = SourceDecl(kind="postgres", connection_ref=f"conn:{metadata['id']}", schema="customer_sales")
    policy = with_sources(load_policy(), {"sales_pg": decl})
    return SimpleNamespace(
        config=config,
        metadata=metadata,
        encrypted=encrypted,
        decl=decl,
        policy=policy,
        spec=sales_spec(),
        conn=conn,
        context=context,
        connect=connect,
        opened=opened,
        dns=dns,
    )


@pytest.mark.parametrize(
    "operation", ["budget", "snapshot", "catalog", "connector", "checks", "el_run", "schema", "rows"]
)
def test_every_postgres_reader_uses_saved_connection(runtime, monkeypatch, tmp_path, operation):
    r = runtime
    if operation == "budget":
        assert budget._source_bytes(r.spec, r.policy, r.config) == {"orders": 123}
    elif operation == "snapshot":
        plan = {"domain": "sales", "sources": [{"id": "orders", "source_ref": "sales_pg", "object": "orders"}]}
        found = resolve.snapshot(plan, r.policy, r.config)["orders"]
        assert found["columns"] == SALES_COLUMNS["orders"] and len(found["sample_rows"]) == 2
    elif operation == "catalog":
        assert resolve.catalog(r.policy, r.config)["sales"]["sources"]["sales_pg"]["objects"]["orders"]
    elif operation == "connector":
        assert connectors.discover(r.decl, r.config)["objects"][0]["row_estimate"] == 2
        assert connectors.schema_visibility(r.config, r.decl.schema_, r.decl) == (True, True)
        assert "không có bảng" in connectors._empty_reason(r.config, r.decl.schema_, r.decl)
    elif operation == "checks":
        side = el_checks.read_source(r.spec.sources[0], r.decl, r.config)
        assert side.records == 2 and side.keys == {("1",), ("2",)}
        assert el_checks.row_reconcile(r.spec.sources[0], side, side).result == "pass"
        assert el_checks.hash_reconcile(r.spec.sources[0], side, side).result == "pass"
    elif operation == "el_run":
        run = MagicMock()
        monkeypatch.setattr(sandbox, "_run", run)
        sandbox._el_run(tmp_path, r.spec, {}, 10, r.policy, r.config)
        run.assert_called_once()
    elif operation == "schema":
        assert postgres.source_schema(r.config, r.decl)[0] == {"name": "order_id", "type": "integer"}
    else:
        assert len(postgres.read_rows(r.config, list(SALES_COLUMNS["orders"]), r.decl)) == 2
    assert r.opened.call_count >= 1
    for call in r.opened.call_args_list:
        assert call.args == (r.config, r.metadata, r.encrypted)
    for call in r.connect.call_args_list:
        assert call.args == ()
        assert call.kwargs == {
            "host": r.metadata["host"],
            "hostaddr": "8.8.8.8",
            "port": 5433,
            "dbname": r.metadata["database"],
            "user": r.metadata["user"],
            "password": PASSWORD,
            "sslmode": "verify-full",
            "connect_timeout": 5,
            "options": connections.READ_ONLY_OPTIONS,
        }
    assert r.context.__exit__.call_count == r.connect.call_count


def test_internal_store_lookup_does_not_change_owner_scoped_lookup(runtime):
    r = runtime
    assert store.get_connection(r.config.store_path, r.metadata["id"], "wrong-owner") is None
    assert store.get_connection_internal(r.config.store_path, r.metadata["id"]) == (r.metadata, r.encrypted)
    assert store.get_connection_internal(r.config.store_path, "missing") is None


def test_legacy_refs_keep_platform_connection_and_url(runtime):
    r = runtime
    for decl in (None, SourceDecl(kind="postgres", connection_ref="sandbox_pg", schema="sales_raw")):
        with postgres.connection(r.config, decl) as conn:
            assert conn is r.conn
        assert r.connect.call_args.args == (r.config.database_url.get_secret_value(),)
        assert r.connect.call_args.kwargs["options"] == connections.READ_ONLY_OPTIONS
    r.opened.assert_not_called()
    assert sandbox._connection_url("sandbox_pg", r.config) == (
        "postgresql+psycopg://platform:platform-secret@postgres/platform?connect_timeout=10"
    )


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("missing", "Không tìm thấy"),
        ("wrong_key", "giải mã"),
        ("invalid_key", "giải mã"),
        ("empty_key", "giải mã"),
        ("ssrf", "private"),
        ("refused", "Không kết nối được"),
        ("auth", "mật khẩu"),
        ("query", "Không kết nối được"),
    ],
)
def test_runtime_errors_are_source_unavailable_and_p9_identifies_source(runtime, monkeypatch, failure, reason, caplog):
    r = runtime
    if failure == "missing":
        store.delete_connection(r.config.store_path, r.metadata["id"], r.metadata["owner_id"])
    elif failure.endswith("key"):
        key = (
            Fernet.generate_key().decode()
            if failure == "wrong_key"
            else ("invalid" if failure == "invalid_key" else "")
        )
        r.config.connection_key = SecretStr(key)
    elif failure == "ssrf":
        r.dns.return_value = dns_rows("10.0.0.5")
    elif failure in ("refused", "auth"):
        error = psycopg.errors.InvalidPassword(PASSWORD) if failure == "auth" else psycopg.OperationalError(PASSWORD)
        r.connect.side_effect = error
    else:
        r.conn.execute.side_effect = psycopg.OperationalError(PASSWORD)
    with pytest.raises(postgres.SourceUnavailableError, match=reason) as caught:
        with postgres.connection(r.config, r.decl) as conn:
            conn.execute("SELECT 1")
    assert PASSWORD not in "".join(traceback.format_exception(caught.value))
    monkeypatch.setattr(sandbox, "prepare_project", lambda *_: None)
    run = MagicMock()
    monkeypatch.setattr(sandbox, "_run", run)
    if failure == "query":
        # P9 chỉ mở kết nối; lỗi khi đọc ở P10 vẫn dùng SourceUnavailableError ở trên.
        return
    result = sandbox.run_in_sandbox(r.spec, {}, r.policy, r.config)
    assert (result.status, result.stage_reached) == ("INFRA_ERROR", "el_run")
    assert result.error_trace == [{"stage": "el_run", "error": str(caught.value), "source_id": "orders"}]
    assert PASSWORD not in str(result.error_trace) and PASSWORD not in caplog.text
    run.assert_not_called()


@pytest.mark.parametrize(
    "host,address", [("db.example.com", "8.8.8.8"), ("2606:4700:4700::1111", "2606:4700:4700::1111")]
)
def test_source_and_dagster_env_quote_credentials_and_pin_checked_ip(runtime, host, address):
    r = runtime
    with store.database(r.config.store_path) as conn:
        conn.execute("UPDATE connections SET host = ? WHERE id = ?", (host, r.metadata["id"]))
    name = "SOURCES__SALES_PG__CREDENTIALS"
    env = sandbox._source_env("sales_pg", r.decl, r.config)
    assert sandbox.credentials_env(r.policy, r.config) == env
    url = env[name]
    parsed = urlsplit(url)
    assert parsed.scheme == "postgresql+psycopg" and parsed.hostname == host and parsed.port == 5433
    assert parsed.password == quote(PASSWORD, safe="") and unquote(parsed.username) == r.metadata["user"]
    assert unquote(parsed.path[1:]) == r.metadata["database"]
    assert parse_qs(parsed.query) == {
        "sslmode": ["verify-full"],
        "hostaddr": [address],
        "connect_timeout": ["5"],
        "options": [connections.READ_ONLY_OPTIONS],
    }
    engine = create_engine(url)
    args, parameters = engine.dialect.create_connect_args(engine.url)
    assert args == []
    assert parameters["user"] == r.metadata["user"] and parameters["password"] == PASSWORD
    assert parameters["dbname"] == r.metadata["database"] and parameters["hostaddr"] == address
    assert parameters["sslmode"] == "verify-full" and parameters["connect_timeout"] == "5"
    assert parameters["options"] == connections.READ_ONLY_OPTIONS
    engine.dispose()
    r.connect.assert_not_called()


def test_env_rechecks_ssrf_and_skips_unavailable_credentials(runtime):
    r = runtime
    sandbox._source_env("sales_pg", r.decl, r.config)
    r.dns.return_value = dns_rows("127.0.0.1")
    with pytest.raises(postgres.SourceUnavailableError, match="loopback"):
        sandbox._connection_url(r.decl.connection_ref, r.config)
    assert sandbox.credentials_env(r.policy, r.config) == {}
    r.connect.assert_not_called()


@pytest.mark.parametrize("phase", ["env", "el_run"])
def test_failure_identifies_second_source_in_spec(runtime, monkeypatch, tmp_path, phase):
    r = runtime
    second = r.spec.sources[0].model_copy(update={"id": "second", "source_ref": "missing_pg"})
    spec = r.spec.model_copy(update={"sources": [*r.spec.sources, second]})
    missing = SourceDecl(kind="postgres", connection_ref="conn:missing", schema="sales")
    policy = with_sources(r.policy, {"sales_pg": r.decl, "missing_pg": missing})
    run = MagicMock()
    monkeypatch.setattr(sandbox, "_run", run)
    with pytest.raises(sandbox._StopError) as caught:
        if phase == "env":
            sandbox._env(tmp_path, spec, policy, r.config)
        else:
            sandbox._el_run(tmp_path, spec, {}, 10, policy, r.config)
    assert (caught.value.status, caught.value.stage) == ("INFRA_ERROR", "el_run")
    assert caught.value.trace["source_id"] == "second"
    assert "Không tìm thấy" in caught.value.trace["error"]
    run.assert_not_called()


def test_dlt_trace_redacts_raw_and_quoted_password(runtime, monkeypatch, tmp_path):
    r = runtime
    env = sandbox._source_env("sales_pg", r.decl, r.config)
    url = next(iter(env.values()))
    process = MagicMock(returncode=1)
    process.communicate.return_value = ("", f"Connection refused {PASSWORD} {quote(PASSWORD, safe='')} {url}")
    monkeypatch.setattr(sandbox.subprocess, "Popen", MagicMock(return_value=process))
    result = sandbox.run_in_sandbox(r.spec, {}, r.policy, r.config)
    assert result.status == "INFRA_ERROR"
    assert PASSWORD not in str(result.error_trace) and quote(PASSWORD, safe="") not in str(result.error_trace)
    with pytest.raises(sandbox._StopError) as caught:
        sandbox._run([sys.executable, "ingestion.py"], tmp_path, env, 10, "el_validate")
    assert PASSWORD not in str(caught.value) and url not in str(caught.value)


@pytest.mark.parametrize("second_ref", [None, "conn:customer-db", "conn:other-db", "sandbox_pg"])
def test_metric_reconcile_routes_one_database_and_skips_multiple(runtime, second_ref):
    r = runtime
    spec, policy = r.spec, r.policy
    if second_ref:
        second = r.spec.sources[0].model_copy(update={"id": "second", "source_ref": "other_pg"})
        spec = r.spec.model_copy(update={"sources": [*r.spec.sources, second]})
        policy = with_sources(
            r.policy,
            {
                "sales_pg": r.decl,
                "other_pg": SourceDecl(kind="postgres", connection_ref=second_ref, schema="another_schema"),
            },
        )
    if second_ref in ("conn:other-db", "sandbox_pg"):
        with pytest.raises(reference.UnsupportedError) as caught:
            reference.reconcile(spec, [], policy, r.config)
        assert str(caught.value) == "các nguồn Postgres nằm ở nhiều database, chưa đối chiếu độc lập được"
        r.connect.assert_not_called()
        return
    matched, observed = reference.reconcile(spec, [{"ordered_at": "2026-01-01", "revenue": 100}], policy, r.config)
    assert matched and observed["engine"] == "postgres"
    assert '"customer_sales"."orders"' in observed["sql"]
    if second_ref:
        assert '"another_schema"."orders"' in observed["sql"]
    assert all(call.args[1]["id"] == r.metadata["id"] for call in r.opened.call_args_list)
    assert r.connect.call_args.kwargs["dbname"] == r.metadata["database"]


def test_multiple_databases_skip_metric_check_without_failing_run(runtime, monkeypatch):
    import duckdb

    from src.run_report.models import Check

    r = runtime
    store.save_connection(r.config.store_path, {**r.metadata, "id": "other-db"}, r.encrypted)
    second = r.spec.sources[0].model_copy(update={"id": "second", "source_ref": "other_pg"})
    spec = r.spec.model_copy(update={"sources": [*r.spec.sources, second]})
    policy = with_sources(
        r.policy,
        {
            "sales_pg": r.decl,
            "other_pg": SourceDecl(kind="postgres", connection_ref="conn:other-db", schema="sales"),
        },
    )
    monkeypatch.setattr(sandbox, "prepare_project", lambda *_: None)
    monkeypatch.setattr(sandbox, "_el_run", lambda *_: None)
    monkeypatch.setattr(sandbox, "_el_validate", lambda *_: [])
    monkeypatch.setattr(sandbox, "_t_run", lambda *_: ([{"unique_id": "test.gold", "status": "pass"}], None))
    monkeypatch.setattr(
        sandbox,
        "_staging_reconcile",
        lambda *_: [
            Check(name="staging_reconcile", source_id="orders", result="pass", observed={"staging": 1}),
        ],
    )
    monkeypatch.setattr(sandbox, "_gold_preview", lambda *_: {"gold_rows": 1})
    monkeypatch.setattr(reference, "read_gold", lambda *_: [])
    monkeypatch.setattr(duckdb, "connect", MagicMock())
    result = sandbox.run_in_sandbox(spec, {}, policy, r.config)
    assert result.status == "READY_FOR_REVIEW" and result.error_trace == []
    assert not any(check.name == "metric_reconcile" for check in result.checks)
    assert "các nguồn Postgres nằm ở nhiều database" in result.gates[0].observed["reconcile_skipped"]


def test_metric_reconcile_without_postgres_uses_platform_as_calculator(runtime, monkeypatch):
    r = runtime
    source = r.spec.sources[0].model_copy(
        update={"kind": "filesystem", "path_pattern": "orders/*.csv", "format": "csv"}
    )
    spec = r.spec.model_copy(update={"sources": [source]})
    decl = SourceDecl(kind="filesystem", connection_ref="landing", root="infra/landing/sales")
    policy = with_sources(r.policy, {"sales_pg": decl})
    monkeypatch.setattr(
        files,
        "objects",
        lambda _: {
            "orders": {"columns": [{"name": name, "type": kind} for name, kind in SALES_COLUMNS["orders"].items()]},
        },
    )
    monkeypatch.setattr(
        files,
        "records",
        lambda *_: iter(
            [
                {
                    "order_id": 1,
                    "amount_text": "100",
                    "ordered_at_text": "2026-01-01",
                    "status": "paid",
                }
            ]
        ),
    )
    query, rows = reference.source_rows(spec, policy, r.config)
    assert "VALUES" in query and rows[0]["revenue"] == 100
    assert all(call.args == (r.config.database_url.get_secret_value(),) for call in r.connect.call_args_list)
    r.opened.assert_not_called()
    r.config.database_url = SecretStr("")
    with pytest.raises(reference.UnsupportedError, match="cần Postgres để tính lại"):
        reference.source_rows(spec, policy, r.config)


@pytest.mark.parametrize("kind", ["filesystem", "rest_api"])
def test_metric_reconcile_inlines_other_sources_on_saved_postgres(runtime, monkeypatch, kind):
    from src.pipeline import rest_api

    r = runtime
    second = r.spec.sources[0].model_copy(
        update={
            "id": "inline",
            "source_ref": "inline_source",
            "kind": kind,
            "path_pattern": "orders/*.csv",
            "format": "csv",
            "endpoint": "/orders",
        }
    )
    spec = r.spec.model_copy(update={"sources": [*r.spec.sources, second]})
    rows = [{"order_id": 1, "amount_text": "100", "ordered_at_text": "2026-01-01", "status": "paid"}]
    described = {"columns": [{"name": name, "type": kind} for name, kind in SALES_COLUMNS["orders"].items()]}
    if kind == "filesystem":
        decl = SourceDecl(kind=kind, connection_ref="file", root="infra/landing/sales")
        monkeypatch.setattr(files, "objects", lambda _: {"orders": described})
        monkeypatch.setattr(files, "records", lambda *_: iter(rows))
    else:
        decl = SourceDecl(kind=kind, connection_ref="api", openapi="infra/mock_api/sales.openapi.json")
        monkeypatch.setattr(rest_api, "endpoints", lambda *_: {"orders": described})
        monkeypatch.setattr(rest_api, "records", lambda *_, **kwargs: iter(rows))
    policy = with_sources(r.policy, {"sales_pg": r.decl, "inline_source": decl})
    query, result = reference.source_rows(spec, policy, r.config)
    assert "VALUES" in query and '"customer_sales"."orders"' in query and result[0]["revenue"] == 100
    assert all(call.kwargs["dbname"] == r.metadata["database"] for call in r.connect.call_args_list)


def test_reference_sql_error_never_exposes_saved_password(runtime, caplog):
    r = runtime
    execute = r.conn.execute.side_effect

    def failed_reference(query, params=None):
        if isinstance(query, str) and " AS reference LIMIT " in query:
            raise psycopg.errors.SyntaxError(PASSWORD)
        return execute(query, params)

    r.conn.execute.side_effect = failed_reference
    with pytest.raises(reference.UnsupportedError, match="Kiểm tra SQL") as caught:
        reference.source_rows(r.spec, r.policy, r.config)
    assert PASSWORD not in "".join(traceback.format_exception(caught.value))
    assert PASSWORD not in caplog.text
    r.conn.rollback.assert_called_once()


def test_hung_dns_lookups_do_not_block_following_lookup(runtime, monkeypatch):
    release = Event()
    finished = [Event() for _ in range(4)]
    started = [Event() for _ in range(4)]
    slots = BoundedSemaphore(8)
    monkeypatch.setattr(connections, "DNS_SLOTS", slots)
    monkeypatch.setattr(connections, "DNS_TIMEOUT", 0.1)

    def lookup(host, port, **kwargs):
        if host.startswith("hung"):
            i = int(host[4])
            started[i].set()
            try:
                release.wait(10)
            finally:
                finished[i].set()
        return dns_rows("8.8.8.8")

    runtime.dns.side_effect = lookup
    try:
        for i in range(4):
            with pytest.raises(HTTPException, match="Không phân giải được host"):
                connections.resolve_host(f"hung{i}.example.com", 5432, runtime.config)
            assert started[i].wait(5)
        assert not any(event.is_set() for event in finished)
        monkeypatch.setattr(connections, "DNS_TIMEOUT", 5)
        assert connections.resolve_host("next.example.com", 5432, runtime.config) == "8.8.8.8"
    finally:
        release.set()
        assert all(event.wait(5) for event in finished)


def test_dns_capacity_rejects_without_waiting_and_recovers(runtime, monkeypatch):
    started, release = Event(), Event()
    monkeypatch.setattr(connections, "DNS_SLOTS", BoundedSemaphore(1))
    monkeypatch.setattr(connections, "DNS_TIMEOUT", 5)
    errors = []

    def lookup(*args, **kwargs):
        started.set()
        release.wait(10)
        return dns_rows("8.8.8.8")

    def resolve_first():
        try:
            connections.resolve_host("hung.example.com", 5432, runtime.config)
        except Exception as exc:
            errors.append(exc)

    runtime.dns.side_effect = lookup
    first = Thread(target=resolve_first, daemon=True)
    first.start()
    try:
        assert started.wait(5)
        with pytest.raises(HTTPException) as caught:
            connections.resolve_host("next.example.com", 5432, runtime.config)
        assert caught.value.status_code == 422 and "Hệ thống đang bận" in caught.value.detail
        runtime.dns.assert_called_once()
    finally:
        release.set()
        first.join(5)
    assert not first.is_alive() and not errors
    assert connections.resolve_host("next.example.com", 5432, runtime.config) == "8.8.8.8"
