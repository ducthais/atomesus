"""Chi phí gọi LLM: token nhà cung cấp báo × giá trong budgets.yml, ghi từng lần gọi vào RunReport."""

import json

import pytest

from src.agents.llm import LLMReply
from src.agents.nodes.common import llm_usage
from src.agents.service import confirm_plan, plan_agent, run_agent, start_request_changes
from src.policy.loader import load_policy
from tests.test_agents.test_graph import PLAN, SPEC, deps


class PricedLLM:
    """Trả PLAN / SPEC kèm số token như OpenAI báo."""

    def __init__(self, model="gpt-4o-mini"):
        self.model = model

    def __call__(self, tier, system, user):
        if tier == "cheap":
            return LLMReply(json.dumps(PLAN), self.model, input_tokens=2_000, output_tokens=500)
        return LLMReply(json.dumps(SPEC), self.model, input_tokens=10_000, output_tokens=1_500)


def test_usage_is_priced_from_budgets_yml():
    usage = llm_usage(LLMReply("{}", "gpt-4o-mini", 1_000_000, 1_000_000), "plan", "cheap", load_policy())
    assert usage == {
        "stage": "plan",
        "tier": "cheap",
        "model": "gpt-4o-mini",
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "usd": pytest.approx(0.75),
    }


@pytest.mark.parametrize(
    "reply",
    [LLMReply("{}", "model-chua-co-gia", 100, 100), LLMReply("{}", "gpt-4o-mini"), "{}"],
    ids=["unpriced-model", "no-token-count", "plain-string"],
)
def test_unknown_price_or_tokens_is_not_guessed(reply):
    assert llm_usage(reply, "plan", "cheap", load_policy())["usd"] is None


def test_run_report_records_every_call_and_the_total(tmp_path):
    report = run_agent("Doanh thu theo ngày", "minh", deps(PricedLLM()), str(tmp_path / "runs.sqlite3"))
    assert [(c.stage, c.tier, c.input_tokens, c.output_tokens) for c in report.llm_usage] == [
        ("plan", "cheap", 2_000, 500),
        ("propose", "strong", 10_000, 1_500),
    ]
    # (2 000 + 10 000) × 0.15 + (500 + 1 500) × 0.60 = 3 000 micro-USD
    assert report.cost.llm_usd == pytest.approx(0.003)
    assert report.llm_calls == len(report.llm_usage)


def test_cost_survives_the_plan_confirmation(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(PricedLLM())
    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)
    assert planned.cost.llm_usd == pytest.approx(0.0006) and len(planned.llm_usage) == 1
    done = confirm_plan(planned, "minh", agent, store)
    assert [c.stage for c in done.llm_usage] == ["plan", "propose"] and done.cost.llm_usd == pytest.approx(0.003)


def test_unpriced_calls_are_listed_but_not_added(tmp_path):
    report = run_agent("Doanh thu theo ngày", "minh", deps(PricedLLM("model-la")), str(tmp_path / "runs.sqlite3"))
    assert len(report.llm_usage) == 2 and all(c.usd is None for c in report.llm_usage) and report.cost.llm_usd == 0


def test_daily_spend_includes_llm(tmp_path):
    from src.agents import budget
    from src.pipeline.settings import PipelineSettings

    store = str(tmp_path / "runs.sqlite3")
    run_agent("Doanh thu theo ngày", "minh", deps(PricedLLM()), store)
    spent = budget.spent_today(PipelineSettings(_env_file=None, store_path=store))
    assert spent == pytest.approx(0.003 + 1e-8), "tiền LLM + chi phí quét ước tính của run"


def test_request_changes_keeps_the_money_already_spent(tmp_path):
    # Version mới được đặt lại trần gọi LLM, nhưng tiền LLM của version trước không được mất khỏi báo cáo.
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(PricedLLM())
    ready = confirm_plan(plan_agent("Doanh thu theo ngày", "minh", agent, store), "minh", agent, store)
    _started, job = start_request_changes(ready, "Đổi lịch chạy", "an", agent, store)
    revised = job()
    assert revised.version == 2 and revised.llm_calls == 1, "chỉ đếm lần gọi của version mới"
    assert [c.stage for c in revised.llm_usage] == ["plan", "propose", "propose"]
    assert revised.cost.llm_usd == pytest.approx(0.003 + 0.0024)
