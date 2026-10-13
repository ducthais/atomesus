from unittest.mock import MagicMock

import pytest

from src.agents import resolve
from src.pipeline import connectors, postgres
from src.pipeline.settings import PipelineSettings
from src.policy import load_policy
from src.policy.models import SourceDecl


@pytest.fixture
def selected_source(request):
    tables = request.param
    decl = SourceDecl(
        kind="postgres", connection_ref="sandbox_pg" if tables is None else "conn:test-db", schema="shop", tables=tables
    )
    policy = load_policy()
    domain = policy.domains.domains["sales"].model_copy(update={"sources": {"shop_pg": decl}, "entities": {}})
    policy = policy.model_copy(update={"domains": policy.domains.model_copy(update={"domains": {"shop": domain}})})
    return policy, decl, PipelineSettings(_env_file=None)


@pytest.mark.parametrize("selected_source", [["orders", "customers"], [], None], indirect=True)
def test_planner_catalog_keeps_only_selected_tables_and_both_relation_ends(selected_source, monkeypatch):
    policy, decl, settings = selected_source
    tables = decl.tables
    columns = MagicMock(return_value={name: [{"name": "id"}] for name in ["orders", "customers", "private"]})
    foreign = [
        {"table": left, "columns": ["id"], "ref_schema": schema, "ref_table": right, "ref_columns": ["id"]}
        for left, schema, right in [
            ("orders", "shop", "customers"),
            ("orders", "shop", "private"),
            ("private", "shop", "customers"),
            ("orders", "other_schema", "customers"),
        ]
    ]
    keys = MagicMock(return_value=({}, foreign))
    monkeypatch.setattr(connectors, "postgres_columns", columns)
    monkeypatch.setattr(connectors, "postgres_keys", keys)
    source = resolve.catalog(policy, settings)["shop"]["sources"]["shop_pg"]
    assert set(source["objects"]) == (set(tables) if tables is not None else {"orders", "customers", "private"})
    assert source["relations"] == (
        ["orders.id -> customers.id", "orders.id -> private.id", "private.id -> customers.id"]
        if tables is None
        else (["orders.id -> customers.id"] if tables else [])
    )
    columns.assert_called_once_with(settings, "shop", decl)
    keys.assert_called_once_with(settings, "shop", decl)


@pytest.mark.parametrize("selected_source", [["orders", "customers"], []], indirect=True)
def test_snapshot_rejects_unselected_table_before_opening_connection(selected_source, monkeypatch):
    policy, decl, settings = selected_source
    connect = MagicMock()
    monkeypatch.setattr(postgres, "connection", connect)
    plan = {"domain": "shop", "sources": [{"id": "private", "source_ref": "shop_pg", "object": "private"}]}
    with pytest.raises(resolve.ResolveError) as caught:
        resolve.snapshot(plan, policy, settings)
    assert str(caught.value) == "bảng private không nằm trong các bảng đã chọn cho nguồn shop_pg"
    connect.assert_not_called()
