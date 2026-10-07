"""Trần thời gian mỗi phase (NFR-05): mọi node, không chỉ bước chạy thử, dừng INFRA_ERROR khi quá `phase_timeout_sec`."""

import time

import pytest

from src.agents.graph import build_graph
from src.agents.timeout import with_timeout
from src.policy.loader import load_policy
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps


def _policy(seconds: int):
    policy = load_policy()
    per_run = policy.budgets.per_run.model_copy(update={"phase_timeout_sec": seconds})
    return policy.model_copy(update={"budgets": policy.budgets.model_copy(update={"per_run": per_run})})


def test_fast_node_returns_its_update():
    assert with_timeout(lambda state: {"x": state["x"] + 1}, "plan", 1)({"x": 1}) == {"x": 2}


def test_slow_node_stops_with_infra_error_naming_the_phase():
    def slow(state):
        time.sleep(0.5)
        return {"status": "VALIDATING"}

    update = with_timeout(slow, "budget", 0.1)({"error_trace": []})

    assert update["status"] == "INFRA_ERROR" and update["stage_reached"] == "budget"
    assert "phase_timeout_sec" in update["error_trace"][-1]["error"]


def test_errors_inside_the_node_still_raise():
    def broken(state):
        raise ValueError("hỏng")

    with pytest.raises(ValueError, match="hỏng"):
        with_timeout(broken, "render", 1)({})


class SlowLLM(FakeLLM):
    def __call__(self, tier, system, user):
        if tier == "cheap":
            time.sleep(1.5)
        return super().__call__(tier, system, user)


def test_graph_stops_a_hanging_planner_with_the_policy_timeout():
    graph = build_graph(deps(SlowLLM(PLAN, SPEC), policy=_policy(1)))

    state = graph.invoke({"run_id": "r1", "version": 1, "user_request": "x", "llm_calls": 0, "error_trace": []})

    assert state["status"] == "INFRA_ERROR" and state["stage_reached"] == "plan"
    assert "1 giây" in state["error_trace"][-1]["error"]
