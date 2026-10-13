import sqlite3
from types import SimpleNamespace

import pytest

from src.pipeline import store
from src.policy import PolicyError, effective_policy, load_policy
from src.policy import effective as effective_module
from src.policy.effective import read_user_selections
from tests.test_policy.test_loader import _edit
from tests.test_policy.test_loader import policy_dir as policy_dir

CONNECTION_A = "12345678-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CONNECTION_B = "87654321-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DOMAIN_A, SOURCE_A = "u_12345678", "c_12345678"
DOMAIN_B = "u_87654321"


@pytest.fixture
def selected_store(tmp_path):
    config = SimpleNamespace(store_path=str(tmp_path / "runs.sqlite3"))
    store.create_user(
        config.store_path,
        user_id="minh",
        username="engineer-a",
        name="Engineer A",
        role="Engineer",
        password_hash="không đọc",
        created_at=1,
        email="engineer-a@example.vn",
    )
    for connection_id, owner in [(CONNECTION_A, "minh"), (CONNECTION_B, "engineer-b"), ("unselected", "minh")]:
        store.save_connection(
            config.store_path,
            {
                "id": connection_id,
                "owner_id": owner,
                "name": f"Shop {owner}",
                "host": "host-khong-duoc-doc.example",
                "port": 5432,
                "database": "shop",
                "user": "user-khong-duoc-doc",
                "sslmode": "require",
                "created_at": 1,
            },
            "mat-khau-ma-hoa-khong-duoc-doc",
        )
    store.save_connection_selection(config.store_path, CONNECTION_A, "minh", "shop", ["orders", "customers"])
    store.save_connection_selection(config.store_path, CONNECTION_B, "engineer-b", "archive", ["orders"])
    return config


def test_effective_policy_scopes_users_and_applies_defaults(selected_store):
    policy = effective_policy(selected_store, owner_id="minh")
    domain = policy.domains.domains[DOMAIN_A]
    source = domain.sources[SOURCE_A]
    assert domain.label == "Shop minh" and domain.owner_id == "minh"
    assert domain.steward == "engineer-a@example.vn"
    assert domain.datasets == ["shop"] and domain.entities == {}
    assert source.kind == "postgres" and source.connection_ref == f"conn:{CONNECTION_A}"
    assert source.schema_ == "shop" and source.tables == ["orders", "customers"]
    assert domain.currency.base == "VND" and domain.currency.rates == {"VND": 1, "USD": 25400}
    assert policy.objectives[DOMAIN_A] == policy.objectives["_user_default"]
    assert policy.objectives[DOMAIN_A].hard.freshness_minutes == 1440
    assert policy.objectives[DOMAIN_A].quality.max_reject_pct == 2.0
    assert DOMAIN_B not in policy.domains.domains
    assert "u_unselect" not in policy.domains.domains
    assert policy.domains.domains["sales"].owner_id is None
    assert policy.domains.domains["sales"].sources["sales_pg"].tables is None
    other = effective_policy(selected_store, owner_id="engineer-b")
    assert DOMAIN_A not in other.domains.domains
    assert other.domains.domains[DOMAIN_B].steward == "engineer-b@users.local"
    assert {DOMAIN_A, DOMAIN_B} <= effective_policy(selected_store).domains.domains.keys()
    assert set(effective_policy(selected_store, owner_id="unknown").domains.domains) == set(
        load_policy().domains.domains
    )


def test_selection_changes_and_connection_deletion_are_immediate(selected_store):
    store.save_connection_selection(selected_store.store_path, CONNECTION_A, "minh", "new_schema", ["new_table"])
    source = effective_policy(selected_store, "minh").domains.domains[DOMAIN_A].sources[SOURCE_A]
    assert source.schema_ == "new_schema" and source.tables == ["new_table"]
    assert store.delete_connection(selected_store.store_path, CONNECTION_A, "minh")
    assert DOMAIN_A not in effective_policy(selected_store).domains.domains


def test_store_is_read_only_and_never_reads_credentials(selected_store, monkeypatch):
    connect = sqlite3.connect
    opened = []

    def metadata_only(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro") and kwargs["uri"] is True
        conn = connect(database_uri, **kwargs)
        opened.append(conn)

        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table in ("connections", "users"):
                allowed = {"connections": {"id", "owner_id", "name"}, "users": {"id", "email"}}
                assert column in allowed[table], f"Đọc ngoài metadata: {table}.{column}"
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(effective_module.sqlite3, "connect", metadata_only)
    assert DOMAIN_A in effective_policy(selected_store).domains.domains
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_missing_or_legacy_store_keeps_yaml_without_creating_tables(tmp_path):
    path = tmp_path / "missing.sqlite3"
    config = SimpleNamespace(store_path=str(path))
    assert effective_policy(config) == load_policy()
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sessions (token TEXT)")
    assert effective_policy(config) == load_policy()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == [("sessions",)]


def test_templates_and_optional_currency_defaults_load_with_old_yaml(policy_dir, selected_store, monkeypatch):
    _edit(policy_dir, "objectives", lambda raw: raw.update(_another_template=raw["sales"]))
    _edit(policy_dir, "domains", lambda raw: raw.pop("user_defaults"))
    monkeypatch.setattr(effective_module, "load_policy", lambda: load_policy(policy_dir))
    assert effective_policy(selected_store).domains.domains[DOMAIN_A].currency.base == "VND"
    _edit(
        policy_dir, "domains", lambda raw: raw.update(user_defaults={"currency": {"base": "USD", "rates": {"USD": 1}}})
    )
    assert effective_policy(selected_store).domains.domains[DOMAIN_A].currency.base == "USD"
    _edit(policy_dir, "objectives", lambda raw: raw["_user_default"]["hard"].update(freshness_minutes=123))
    assert effective_policy(selected_store).objectives[DOMAIN_A].hard.freshness_minutes == 123


def test_missing_user_template_is_a_clear_policy_error(policy_dir, selected_store, monkeypatch):
    _edit(policy_dir, "objectives", lambda raw: raw.pop("_user_default"))
    monkeypatch.setattr(effective_module, "load_policy", lambda: load_policy(policy_dir))
    with pytest.raises(PolicyError, match="thiếu mẫu _user_default"):
        effective_policy(selected_store)


def test_invalid_saved_selection_is_a_clear_policy_error(selected_store):
    with store.database(selected_store.store_path) as conn:
        conn.execute(
            "UPDATE connection_selections SET tables_json = ? WHERE connection_id = ?", ("invalid", CONNECTION_A)
        )
    with pytest.raises(PolicyError, match="Không đọc được lựa chọn"):
        effective_policy(selected_store)


def test_short_id_collision_does_not_overwrite_a_domain(selected_store):
    with store.database(selected_store.store_path) as conn:
        conn.execute("UPDATE connections SET id = ? WHERE id = 'unselected'", ("12345678-collision",))
    store.save_connection_selection(selected_store.store_path, "12345678-collision", "minh", "shop", ["orders"])
    with pytest.raises(PolicyError, match="trùng tên"):
        effective_policy(selected_store)


def test_corrupt_store_is_not_silently_ignored(tmp_path):
    path = tmp_path / "bad.sqlite3"
    path.write_text("không phải SQLite", encoding="utf-8")
    with pytest.raises(PolicyError, match="Không đọc được lựa chọn"):
        read_user_selections(str(path))


def test_selections_without_users_table_use_local_steward(selected_store):
    with sqlite3.connect(selected_store.store_path) as conn:
        conn.execute("DROP TABLE users")
    assert effective_policy(selected_store).domains.domains[DOMAIN_A].steward == "minh@users.local"
