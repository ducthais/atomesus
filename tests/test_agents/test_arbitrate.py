"""Bước P5 arbitrate: lịch chạy phải giữ dữ liệu tươi theo objectives.yml, rẻ nhất trong các lịch đạt."""

import pytest

from src.agents.arbitrate import CronError, arbitrate, cadence
from src.policy.loader import load_policy
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, run


@pytest.mark.parametrize(
    ("cron", "gap", "per_day"),
    [
        ("*/15 * * * *", 15, 96.0),
        ("0 * * * *", 60, 24.0),
        ("0 2 * * *", 1440, 1.0),
        ("30 8,17 * * *", 900, 2.0),  # 17:30 → 8:30 hôm sau
        ("0 9 * * 1-5", 4320, 0.7143),  # thứ Sáu 9:00 → thứ Hai 9:00
        ("5/20 * * * *", 20, 72.0),  # :05 :25 :45, rồi :05 giờ sau
    ],
)
def test_cadence(cron, gap, per_day):
    assert cadence(cron) == {"max_gap_minutes": gap, "runs_per_day": per_day}


def test_invalid_cron_is_reported():
    with pytest.raises(CronError):
        cadence("0 25 * * *")


def _objectives(domain="sales", **hard):
    objectives = load_policy().objectives[domain]
    return objectives.model_copy(update={"hard": objectives.hard.model_copy(update=hard)}) if hard else objectives


def test_feasible_spec_schedule_is_kept_even_if_a_cheaper_one_exists():
    gate, cron, reason = arbitrate("*/15 * * * *", 1e-6, _objectives())  # sales: tươi trong 60 phút
    assert (cron, reason, gate.result) == ("*/15 * * * *", None, "pass")
    assert "giữ lịch của Spec" in gate.detail and gate.observed["chosen"] == "*/15 * * * *"


def test_too_stale_schedule_switches_to_the_cheapest_feasible():
    gate, cron, _ = arbitrate("0 2 * * *", 1e-6, _objectives())
    assert cron == "0 * * * *", "hằng giờ là lịch thưa nhất (rẻ nhất) còn giữ được 60 phút"
    assert "1440 phút > 60 phút" in gate.detail and "cheapest_feasible" in gate.detail
    infeasible = {row["cron"] for row in gate.observed["proposals"] if not row["feasible"]}
    assert infeasible == {"0 2 * * *", "0 */6 * * *"}


def test_no_feasible_schedule_escalates_with_the_tradeoff_table():
    gate, cron, reason = arbitrate("0 * * * *", 1e-6, _objectives(freshness_minutes=5))
    assert cron is None and gate.result == "fail" and "*/15 * * * *" in reason
    assert len(gate.observed["proposals"]) == 5 and not any(row["feasible"] for row in gate.observed["proposals"])


def test_soft_cost_only_warns():
    gate, cron, _ = arbitrate("*/15 * * * *", 0.02, _objectives())  # 96 lần × 0.02 = 1.92 USD/ngày > 1.0
    assert cron == "*/15 * * * *" and gate.result == "pass" and "soft.cost_usd_per_day" in gate.detail


def test_graph_renders_the_chosen_schedule():
    state = run(FakeLLM(PLAN, {**SPEC, "schedule": {"cron": "0 2 * * *"}}))
    assert state["status"] == "READY_FOR_REVIEW", state.get("error_trace")
    assert state["spec"]["schedule"]["cron"] == "0 * * * *"
    dagster = next(text for path, text in state["generated_files"].items() if path.startswith("infra/dagster/"))
    assert "0 * * * *" in dagster and "0 2 * * *" not in dagster


def test_graph_stops_when_nothing_is_fresh_enough():
    policy = load_policy()
    strict = policy.objectives["sales"].model_copy(
        update={"hard": policy.objectives["sales"].hard.model_copy(update={"freshness_minutes": 5})}
    )
    policy = policy.model_copy(update={"objectives": {**policy.objectives, "sales": strict}})
    state = run(FakeLLM(PLAN, SPEC), policy=policy)
    assert (state["status"], state["stage_reached"]) == ("NEEDS_HUMAN", "arbitrate")
    assert "generated_files" not in state
