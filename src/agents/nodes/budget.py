"""Node `budget` (P8, tất định): ước tính chi phí của Spec đã qua cổng tĩnh, vượt trần thì dừng BLOCKED_BUDGET."""

from src.agents import budget
from src.agents.nodes.common import AgentDeps, StopError
from src.agents.state import AgentState
from src.pipeline.postgres import SourceUnavailableError
from src.spec.models import Spec


def budget_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        budget_passes = sum(gate.get("name") == "budget" for gate in state.get("gates", [])) + 1
        try:
            result, errors, estimated = budget.check(
                Spec.model_validate(state["spec"]),
                deps.policy,
                deps.settings,
                estimate_cost=deps.estimate_cost,
                spent_fn=deps.spent_today,
                reserve=deps.reserve_daily,
                run_id=state.get("run_id", ""),
                version=state.get("version", 1),
                category=f"scan:{budget_passes}",
            )
        except SourceUnavailableError as exc:
            return StopError("INFRA_ERROR", "budget", str(exc)).update(state)
        previous = state.get("cost") or {"estimated_bytes": 0, "actual_bytes": 0, "usd": 0}
        update = {
            "gates": [*state.get("gates", []), result.model_dump()],
            # DuckDB không có chi phí thật sau khi chạy: job_cost trả lại chính số ước tính (SPEC §12).
            "cost": {
                "estimated_bytes": previous.get("estimated_bytes", 0) + estimated["bytes"],
                "actual_bytes": previous.get("actual_bytes", 0) + estimated["bytes"],
                "usd": previous.get("usd", 0) + estimated["usd"],
            },
            "stage_reached": "budget",
        }
        if errors:
            return StopError("BLOCKED_BUDGET", "budget", "; ".join(errors)).update(state, **update)
        return update

    return node
