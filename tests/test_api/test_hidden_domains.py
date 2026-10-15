"""Domain `hidden: true` (domains.yml: sales) bị bỏ khỏi luồng thao tác mới, còn thao tác trên run có sẵn vẫn thấy đủ.

- Mới: catalog, chọn nguồn, tạo run (API, batch), chat tạo pipeline, planner và cổng semantic của run đó.
- Có sẵn: xác nhận, chạy tiếp, sửa qua chat, Day 2, drift, đối chiếu pipeline đã deploy (`include_hidden=True`).
"""

from types import SimpleNamespace

import pytest

from src.api.routes import for_new_runs
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.policy import Policy, load_policy, without_hidden
from src.run_report.access import policy_for
from tests.test_agents.test_graph import deps, tiered_llm


def _with_partition_rules(policy: Policy, refs: list[str]) -> Policy:
    raw = policy.model_dump()
    raw["budgets"]["rules"]["require_partition_filter_on"] = refs
    return Policy.model_validate(raw)


def test_without_hidden_drops_domain_objectives_and_its_partition_rules():
    policy = _with_partition_rules(load_policy(), ["sales_pg.orders", "pagila_pg.rental"])
    assert policy.domains.domains["sales"].hidden is True and "sales" in policy.objectives

    visible = without_hidden(policy)

    assert set(visible.domains.domains) == {"marketing", "rental", "bench"}
    assert "sales" not in visible.objectives
    assert visible.budgets.rules.require_partition_filter_on == ["pagila_pg.rental"]
    assert "sales" in policy.domains.domains, "không sửa chính sách gốc"


def test_without_hidden_returns_the_same_policy_when_nothing_is_hidden(sales_visible):
    policy = load_policy()
    assert without_hidden(policy) is policy


@pytest.mark.parametrize("user", [{"id": "admin", "role": "Admin"}, {"id": "minh", "role": "Engineer"}])
def test_policy_for_hides_by_default_and_keeps_hidden_for_existing_runs(tmp_path, user):
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    assert "sales" not in policy_for(user, config).domains.domains
    assert "sales" in policy_for(user, config, include_hidden=True).domains.domains


def test_for_new_runs_narrows_only_the_policy():
    existing = deps(tiered_llm)

    fresh = for_new_runs(existing)

    assert "sales" not in fresh.policy.domains.domains and "rental" in fresh.policy.domains.domains
    assert fresh.llm is existing.llm and fresh.catalog is existing.catalog
    assert "sales" in existing.policy.domains.domains, "deps của run có sẵn giữ nguyên"


@pytest.fixture
def engineer(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)  # đủ domain như run có sẵn
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    yield client
    app.dependency_overrides.pop(auth.settings, None)
    app.dependency_overrides.pop(routes.agent_deps, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/agent/runs", {"request": "Doanh thu theo ngày"}),
        ("/api/v1/agent/runs/batch", {"requests": [{"request": "Doanh thu theo ngày"}, {"request": "Số đơn"}]}),
        ("/api/v1/chat", {"message": "Doanh thu theo ngày"}),
    ],
)
async def test_new_run_batch_and_chat_reject_hidden_sources(engineer, path, body):
    await engineer.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    sources = [{"source_ref": "sales_pg", "tables": ["orders"]}]
    if "requests" in body:
        body = {"requests": [{**r, "sources": sources} for r in body["requests"]]}
    else:
        body = {**body, "sources": sources}

    response = await engineer.post(path, json=body)

    assert response.status_code == 422, response.text
    assert "'sales_pg' không có trong domains.yml" in response.text
