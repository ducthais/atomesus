import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/agent/status")
    assert response.status_code == 200
    assert response.json()["graph"] == ["planner", "codegen", "budget", "render", "test_run"]


@pytest.mark.asyncio
async def test_policy_requires_session_and_returns_files_and_limits(client, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.main import app
    from src.pipeline import auth
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        assert (await client.get("/api/v1/policy")).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        body = (await client.get("/api/v1/policy")).json()
        assert set(body["files"]) == {"domains.yml", "budgets.yml", "objectives.yml"}
        assert "max_llm_calls: 6" in body["files"]["budgets.yml"]
        assert body["limits"] == {
            "max_llm_calls": 6,
            "max_spec_attempts": 2,
            "max_sql_attempts": 2,
            "max_chat_calls": 20,
            "max_usd_per_day": 2.0,
        }
    finally:
        app.dependency_overrides.pop(auth.settings, None)


FK = {
    "name": "orders_customer_id_fkey",
    "table": "orders",
    "columns": ["customer_id"],
    "ref_schema": "sales_raw",
    "ref_table": "customers",
    "ref_columns": ["customer_id"],
}


@pytest.fixture
def catalog_api(tmp_path, monkeypatch):
    """Đăng nhập được, Postgres giả ở biên connector (không cần DB thật)."""
    from types import SimpleNamespace

    from src.main import app
    from src.pipeline import auth, connectors
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    monkeypatch.setattr(
        connectors, "postgres_columns", lambda _c, schema: {"orders": [{"name": "order_id", "type": "integer"}]}
    )
    monkeypatch.setattr(connectors, "postgres_keys", lambda _c, schema: ({"orders": ["order_id"]}, [FK]))
    monkeypatch.setattr(connectors, "postgres_row_estimates", lambda _c, schema: {"orders": 30})
    yield connectors
    app.dependency_overrides.pop(auth.settings, None)


def _down(_config, schema):
    from src.pipeline import postgres

    raise postgres.SourceUnavailableError("Không đọc được Postgres.")


@pytest.mark.asyncio
async def test_catalog_lists_declared_sources_with_real_columns_and_entities(client, catalog_api, monkeypatch):
    assert (await client.get("/api/v1/catalog")).status_code == 401
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    sales = (await client.get("/api/v1/catalog")).json()["domains"]["sales"]
    assert sales["steward"] == "an@company.vn"
    assert sales["sources"]["sales_pg"]["available"] is True
    assert sales["sources"]["sales_pg"]["tables"]["orders"] == [{"name": "order_id", "type": "integer"}]
    assert sales["sources"]["sales_pg"]["primary_keys"] == {"orders": ["order_id"]}
    assert sales["sources"]["sales_pg"]["foreign_keys"] == [FK]
    landing = sales["sources"]["landing_sales"]
    assert landing["available"] is False and landing["reason"] == "Chưa có connector cho nguồn filesystem"
    assert sales["entities"]["order"]["tables"] == ["sales_pg.orders"]

    monkeypatch.setattr(catalog_api, "postgres_columns", _down)
    pg = (await client.get("/api/v1/catalog")).json()["domains"]["sales"]["sources"]["sales_pg"]
    assert pg["available"] is False and pg["reason"] == "Không đọc được Postgres."


@pytest.mark.asyncio
async def test_source_discovery_connects_on_request(client, catalog_api, monkeypatch):
    assert (await client.get("/api/v1/catalog/sources")).status_code == 401
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    sources = {s["ref"]: s for s in (await client.get("/api/v1/catalog/sources")).json()}
    assert sources["sales_pg"]["supported"] is True
    assert sources["sales_pg"]["kind"] == "postgres" and sources["sales_pg"]["location"] == "sales_raw"
    assert sources["sales_api"]["supported"] is True and sources["landing_sales"]["supported"] is False

    found = (await client.get("/api/v1/catalog/sources/sales_pg/objects")).json()
    assert found["domain"] == "sales" and found["foreign_keys"] == [FK] and found["discovered_at"]
    assert found["objects"] == [
        {
            "name": "orders",
            "kind": "table",
            "columns": [{"name": "order_id", "type": "integer"}],
            "primary_key": ["order_id"],
            "row_estimate": 30,
        }
    ]
    missing = await client.get("/api/v1/catalog/sources/khong_co/objects")
    assert missing.status_code == 404
    unsupported = await client.get("/api/v1/catalog/sources/landing_sales/objects")
    assert unsupported.status_code == 422 and "Chưa có connector" in unsupported.json()["detail"]
    from fastapi.testclient import TestClient

    from infra.mock_api.app import app as mock_api
    from src.pipeline import rest_api

    monkeypatch.setattr(rest_api, "client", lambda url: TestClient(mock_api, base_url=url))
    api = (await client.get("/api/v1/catalog/sources/sales_api/objects")).json()
    assert api["kind"] == "rest_api" and [o["name"] for o in api["objects"]] == ["payments"]
    monkeypatch.setattr(rest_api, "client", lambda url: TestClient(mock_api, base_url="http://x/khong-co"))
    api_down = await client.get("/api/v1/catalog/sources/sales_api/objects")
    assert api_down.status_code == 503 and "mock_api" in api_down.json()["detail"]
    monkeypatch.setattr(catalog_api, "postgres_columns", _down)
    down = await client.get("/api/v1/catalog/sources/sales_pg/objects")
    assert down.status_code == 503 and down.json()["detail"] == "Không đọc được Postgres."


@pytest.mark.parametrize(
    ("schema", "visibility", "expected"),
    [
        ("pagila", (False, False), "chưa có trong Postgres nguồn. Chạy `python scripts/load_pagila.py`"),
        (
            "pagila",
            (True, False),
            "chưa được cấp quyền đọc schema 'pagila'. Chạy `python scripts/load_pagila.py --replace`",
        ),
        ("sales_raw", (False, False), "Schema 'sales_raw' chưa có trong Postgres nguồn. Kiểm tra dữ liệu seed"),
    ],
)
def test_empty_postgres_schema_explains_how_to_fix(monkeypatch, schema, visibility, expected):
    from src.pipeline import connectors, postgres
    from src.policy.models import SourceDecl

    monkeypatch.setattr(connectors, "postgres_columns", lambda _c, _s: {})
    monkeypatch.setattr(connectors, "schema_visibility", lambda _c, _s: visibility)
    decl = SourceDecl.model_validate({"kind": "postgres", "connection_ref": "sandbox_pg", "schema": schema})
    with pytest.raises(
        postgres.SourceUnavailableError, match=expected.replace("(", r"\(").replace(")", r"\)").replace(".", r"\.")
    ):
        connectors.discover(decl, None)
