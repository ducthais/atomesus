from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.api import monitor, routes
from src.chat import api as chat_api
from src.main import app
from src.pipeline import auth, connectors, postgres, store
from src.pipeline.settings import PipelineSettings
from src.policy import POLICY_DIR, effective_policy
from src.run_report import RunReport
from src.run_report.router import request_change_deps
from tests.test_policy.test_effective import CONNECTION_A, DOMAIN_A, DOMAIN_B, SOURCE_A
from tests.test_policy.test_effective import selected_store as selected_store


@pytest.fixture
def user_domains(selected_store, monkeypatch):
    config = PipelineSettings(_env_file=None, store_path=selected_store.store_path)
    state = SimpleNamespace(config=config, user={"id": "minh", "role": "Engineer"}, discovered=[], calls=[])
    app.dependency_overrides[auth.settings] = lambda: state.config
    app.dependency_overrides[auth.current_user] = lambda: state.user
    monkeypatch.setattr(routes, "openai_llm", lambda: lambda *_: pytest.fail("Không được gọi LLM thật"))
    monkeypatch.setattr("src.agents.llm.openai_llm", routes.openai_llm)

    def discover(decl, config):
        assert config is state.config
        state.discovered.append(decl.connection_ref)
        objects = [
            {
                "name": name,
                "kind": "table",
                "columns": [{"name": "id", "type": "integer"}],
                "primary_key": ["id"],
                "row_estimate": 10,
            }
            for name in ["orders", "customers", "private_table"]
        ]
        foreign = [
            {
                "table": "orders",
                "columns": ["customer_id"],
                "ref_schema": decl.schema_,
                "ref_table": "customers",
                "ref_columns": ["id"],
            },
            {"table": "orders", "ref_schema": decl.schema_, "ref_table": "private_table"},
            {"table": "private_table", "ref_schema": decl.schema_, "ref_table": "orders"},
            {"table": "orders", "ref_schema": "private_schema", "ref_table": "customers"},
        ]
        return {"objects": objects, "foreign_keys": foreign}

    monkeypatch.setattr(connectors, "discover", discover)
    yield state
    app.dependency_overrides.pop(auth.settings, None)
    app.dependency_overrides.pop(auth.current_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity,role,visible", [("minh", "Engineer", True), ("engineer-b", "Engineer", False), ("an", "Reviewer", True)]
)
async def test_catalog_scopes_domains_and_all_table_metadata(client, user_domains, identity, role, visible):
    user_domains.user = {"id": identity, "role": role}
    response = await client.get("/api/v1/catalog")
    assert response.status_code == 200
    domains = response.json()["domains"]
    assert "sales" in domains
    assert (DOMAIN_A in domains) is visible
    assert (f"conn:{CONNECTION_A}" in user_domains.discovered) is visible
    if visible:
        domain = domains[DOMAIN_A]
        assert domain["label"] == "Shop minh"
        source = domain["sources"][SOURCE_A]
        assert source["available"] is True
        assert set(source["tables"]) == {"orders", "customers"}
        assert set(source["primary_keys"]) == {"orders", "customers"}
        assert len(source["foreign_keys"]) == 1
        assert source["foreign_keys"][0]["ref_table"] == "customers"
    sources = (await client.get("/api/v1/catalog/sources")).json()
    assert (SOURCE_A in {source["ref"] for source in sources}) is visible
    user_domains.discovered.clear()
    objects = await client.get(f"/api/v1/catalog/sources/{SOURCE_A}/objects")
    assert objects.status_code == (200 if visible else 404)
    if visible:
        assert {obj["name"] for obj in objects.json()["objects"]} == {"orders", "customers"}
        assert len(objects.json()["foreign_keys"]) == 1
    else:
        assert user_domains.discovered == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity,role,expected",
    [
        ("minh", "Engineer", {DOMAIN_A}),
        ("engineer-b", "Engineer", {DOMAIN_B}),
        # Reviewer demo chỉ liên kết với minh: không thấy domain của engineer-b (ADR-0001 D2).
        ("an", "Reviewer", {DOMAIN_A}),
        ("admin", "Admin", {DOMAIN_A, DOMAIN_B}),
    ],
)
async def test_policy_preserves_yaml_and_exposes_only_safe_user_metadata(
    client, user_domains, identity, role, expected
):
    user_domains.user = {"id": identity, "role": role}
    response = await client.get("/api/v1/policy")
    assert response.status_code == 200
    body = response.json()
    assert set(body["files"]) == {"domains.yml", "budgets.yml", "objectives.yml"}
    for filename, content in body["files"].items():
        assert content == (POLICY_DIR / filename).read_text(encoding="utf-8")
    assert {domain["name"] for domain in body["user_domains"]} == expected
    for domain in body["user_domains"]:
        assert set(domain) == {"name", "label", "schema", "tables"}
    assert "host-khong-duoc-doc" not in response.text
    assert "user-khong-duoc-doc" not in response.text
    assert "mat-khau-ma-hoa" not in response.text
    assert user_domains.discovered == []


def _report(created_by, request, sources):
    return RunReport(
        run_id="test-user-domain-run",
        version=1,
        domain=DOMAIN_A,
        created_by=created_by,
        trigger="user_request",
        source_scope=sources,
        created_at=datetime.now(UTC),
        user_request=request,
        status="DRAFT",
        stage_reached="confirm",
        cost={"estimated_bytes": 0, "actual_bytes": 0, "usd": 0},
        llm_calls=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path,message_key", [("/api/v1/agent/runs", "request"), ("/api/v1/chat", "message")])
@pytest.mark.parametrize("identity,status", [("minh", 201), ("engineer-b", 422)])
async def test_creating_run_or_chat_rejects_another_owners_source(
    client, user_domains, monkeypatch, path, message_key, identity, status
):
    def plan(request, created_by, deps, store_path, sources=None, parent_run_id=None, run_id=None):
        user_domains.calls.append((created_by, sources, set(deps.policy.domains.domains)))
        return _report(created_by, request, sources)

    monkeypatch.setattr(chat_api, "plan_agent", plan)
    monkeypatch.setattr(routes, "start_agent", lambda *args: (plan(*args), lambda: None))
    user_domains.user = {"id": identity, "role": "Engineer"}
    sources = [{"source_ref": SOURCE_A, "tables": ["orders"]}]
    response = await client.post(path, json={message_key: "Đọc đơn hàng", "sources": sources})
    assert response.status_code == (200 if path.endswith("chat") and status == 201 else status), response.text
    if status == 422:
        assert "không có" in response.json()["detail"]
        assert user_domains.calls == []
    else:
        assert user_domains.calls == [("minh", sources, {"sales", "marketing", "rental", "bench", DOMAIN_A})]
    assert user_domains.discovered == []


@pytest.mark.asyncio
async def test_chat_without_selection_uses_only_the_engineers_policy(client, user_domains, monkeypatch):
    def plan(request, created_by, deps, store_path, run_id=None):
        assert DOMAIN_A in deps.policy.domains.domains and DOMAIN_B not in deps.policy.domains.domains
        return _report(created_by, request, None)

    monkeypatch.setattr(chat_api, "plan_agent", plan)
    assert (await client.post("/api/v1/chat", json={"message": "Đọc đơn hàng"})).status_code == 200
    invalid = await client.post(
        "/api/v1/chat", json={"message": "?", "run_id": "existing", "sources": [{"source_ref": SOURCE_A}]}
    )
    assert invalid.status_code == 422 and "pipeline mới" in invalid.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,message_key", [("/api/v1/agent/runs", "request"), ("/api/v1/chat", "message")])
async def test_creating_run_or_chat_rejects_tables_outside_saved_selection(
    client, user_domains, monkeypatch, path, message_key
):
    def unexpected(*args, **kwargs):
        pytest.fail("Không được lập kế hoạch với bảng ngoài lựa chọn")

    monkeypatch.setattr(routes, "start_agent", unexpected)
    monkeypatch.setattr(chat_api, "plan_agent", unexpected)
    response = await client.post(
        path,
        json={
            message_key: "Đọc bảng riêng",
            "sources": [{"source_ref": SOURCE_A, "tables": ["orders", "private_table"]}],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == f"bảng private_table không nằm trong các bảng đã chọn cho nguồn {SOURCE_A}"
    assert user_domains.discovered == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path,message_key", [("/api/v1/agent/runs", "request"), ("/api/v1/chat", "message")])
@pytest.mark.parametrize("source_ref,tables", [(SOURCE_A, []), ("sales_pg", ["other_table"])])
async def test_run_and_chat_keep_empty_choice_and_yaml_table_behavior(
    client, user_domains, monkeypatch, path, message_key, source_ref, tables
):
    expected_sources = [{"source_ref": source_ref, "tables": tables}]

    def plan(request, created_by, deps, store_path, sources=None, parent_run_id=None, run_id=None):
        assert sources == expected_sources
        user_domains.calls.append(sources)
        return _report(created_by, request, sources)

    monkeypatch.setattr(routes, "start_agent", lambda *args: (plan(*args), lambda: None))
    monkeypatch.setattr(chat_api, "plan_agent", plan)
    response = await client.post(path, json={message_key: "Đọc đơn hàng", "sources": expected_sources})
    assert response.status_code == (200 if path.endswith("chat") else 201), response.text
    assert user_domains.calls == [expected_sources]


@pytest.mark.asyncio
async def test_selected_catalog_keeps_connection_error_handling(client, user_domains, monkeypatch):
    def down(decl, config):
        raise postgres.SourceUnavailableError("Không đọc được Postgres.")

    monkeypatch.setattr(connectors, "discover", down)
    source = (await client.get("/api/v1/catalog")).json()["domains"][DOMAIN_A]["sources"][SOURCE_A]
    assert source["available"] is False and source["reason"] == "Không đọc được Postgres."
    assert (await client.get(f"/api/v1/catalog/sources/{SOURCE_A}/objects")).status_code == 503


@pytest.mark.asyncio
async def test_monitor_day2_and_reviewer_rework_use_all_user_domains(client, user_domains, monkeypatch):
    policies = []

    def check(config, policy, launch, models=None):
        policies.append(policy)
        return []

    monkeypatch.setattr(monitor.day2, "check", check)
    monkeypatch.setattr(monitor.day2, "overview", lambda config, policy: check(config, policy, None))
    monkeypatch.setattr(monitor.runs, "collect", lambda config, policy, models=None: check(config, policy, None))
    assert (await client.post("/api/v1/monitor/check")).status_code == 200
    assert (await client.get("/api/v1/monitor/pipelines")).status_code == 200
    assert (await client.post("/api/v1/monitor/collect")).status_code == 200
    policies.append(request_change_deps(user_domains.config).policy)
    assert len(policies) == 4
    assert all({DOMAIN_A, DOMAIN_B} <= policy.domains.domains.keys() for policy in policies)


@pytest.mark.asyncio
async def test_deleted_connection_disappears_from_catalog_and_policy(client, user_domains):
    assert store.delete_connection(user_domains.config.store_path, CONNECTION_A, "minh")
    assert DOMAIN_A not in (await client.get("/api/v1/catalog")).json()["domains"]
    assert (await client.get("/api/v1/policy")).json()["user_domains"] == []


def test_azure_mirror_skips_saved_connections(selected_store):
    from scripts.azure_mirror import tables

    policy = effective_policy(selected_store)
    assert tables(policy, None) == [("sales_pg", "sales_raw"), ("pagila_pg", "pagila")]
    assert tables(policy, {f"{SOURCE_A}.orders"}) == []
