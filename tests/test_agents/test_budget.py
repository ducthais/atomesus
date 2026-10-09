"""Cổng budget (P8): ước tính byte / USD, chặn BLOCKED_BUDGET khi vượt trần run hoặc trần ngày."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from infra.mock_api.app import app as mock_api
from src.agents import budget
from src.pipeline import rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.run_report import RunReport, save_run_report
from src.spec.models import Spec
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, run, small_cost
from tests.test_codegen.test_rest_api_sandbox import SPEC as API_SPEC

FIXTURE = "fixtures/run_reports/ready_for_review.json"


def _estimate(size: int, usd: float) -> dict:
    return {"sources": {"orders": size // 2}, "model_bytes": size // 2, "bytes": size, "usd": usd, "estimated": True}


def test_within_limits_passes_and_reports_the_numbers():
    gate, errors = budget.gate(_estimate(16384, 1e-7), load_policy(), spent=0.5)
    assert not errors and gate.result == "pass"
    assert "16,384 byte" in gate.detail and "0.500000/2.0 USD" in gate.detail
    assert gate.observed["max_bytes_scanned"] == 5_000_000_000 and gate.observed["estimated"] is True


@pytest.mark.parametrize(
    ("size", "usd", "spent", "rule"),
    [
        (6_000_000_000, 0.01, 0.0, "per_run.max_bytes_scanned"),
        (1000, 0.06, 0.0, "per_run.max_usd"),
        (1000, 0.02, 1.99, "per_day.max_usd"),
    ],
)
def test_each_limit_blocks(size, usd, spent, rule):
    gate, errors = budget.gate(_estimate(size, usd), load_policy(), spent)
    assert gate.result == "fail" and len(errors) == 1 and rule in errors[0]


def test_over_budget_stops_before_render_and_records_the_cost():
    huge = lambda spec, policy, settings: {**small_cost(spec, policy, settings), "bytes": 6_000_000_000}  # noqa: E731
    state = run(FakeLLM(PLAN, SPEC), estimate_cost=huge)
    assert (state["status"], state["stage_reached"]) == ("BLOCKED_BUDGET", "budget")
    assert "generated_files" not in state, "chưa render, chưa chạy thử"
    assert state["cost"]["estimated_bytes"] == 6_000_000_000
    assert state["gates"][-1]["name"] == "budget" and "max_bytes_scanned" in state["error_trace"][-1]["error"]


def test_daily_spend_blocks_the_next_run():
    state = run(FakeLLM(PLAN, SPEC), spent_today=lambda settings: 2.0)  # đã dùng hết trần ngày
    assert state["status"] == "BLOCKED_BUDGET" and "per_day.max_usd" in state["error_trace"][-1]["error"]


def test_spent_today_uses_when_cost_is_recorded_not_run_creation(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    base = RunReport.model_validate_json(open(FIXTURE, encoding="utf-8").read())
    now = datetime.now(UTC)
    for run_id, at, usd in [
        ("a", now, 0.25),
        ("b", now - timedelta(seconds=1), 0.5),
        ("c", now - timedelta(days=1), 9.0),
    ]:
        cost = base.cost.model_copy(update={"usd": usd})
        save_run_report(store, base.model_copy(update={"run_id": run_id, "created_at": at, "cost": cost}))
    assert budget.spent_today(PipelineSettings(_env_file=None, store_path=store)) == 9.75


def test_estimate_reads_every_api_page(monkeypatch):
    monkeypatch.setattr(rest_api, "client", lambda url: TestClient(mock_api, base_url=url))
    spec = Spec.model_validate(API_SPEC)
    estimated = budget.estimate(spec, load_policy(), PipelineSettings(_env_file=None))
    payments = estimated["sources"]["payments"]
    assert 122 * 80 < payments < 122 * 200, "122 bản ghi JSON, mỗi bản khoảng một trăm byte"
    assert estimated["bytes"] == 2 * payments and estimated["usd"] == pytest.approx(2 * payments / 1e12 * 6.25)


def test_deps_default_to_the_real_estimator():
    agent = deps(FakeLLM())
    assert agent.estimate_cost is small_cost
    from src.agents.nodes.common import AgentDeps

    assert AgentDeps.__dataclass_fields__["estimate_cost"].default is budget.estimate
