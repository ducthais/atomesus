"""Node `budget` (P8, tất định): ước tính chi phí của Spec đã qua cổng tĩnh, vượt trần thì dừng BLOCKED_BUDGET."""

from src.agents import budget
from src.agents.nodes.common import AgentDeps, StopError
from src.agents.state import AgentState
from src.pipeline.postgres import SourceUnavailableError
from src.spec.models import Spec


def budget_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        try:
            estimated = deps.estimate_cost(Spec.model_validate(state["spec"]), deps.policy, deps.settings)
            spent = deps.spent_today(deps.settings)
        except SourceUnavailableError as exc:
            return StopError("INFRA_ERROR", "budget", str(exc)).update(state)
        result, errors = budget.gate(estimated, deps.policy, spent)
        update = {
            "gates": [*state.get("gates", []), result.model_dump()],
            # DuckDB không có chi phí thật sau khi chạy: job_cost trả lại chính số ước tính (SPEC §12).
            "cost": {
                "estimated_bytes": estimated["bytes"],
                "actual_bytes": estimated["bytes"],
                "usd": estimated["usd"],
            },
            "stage_reached": "budget",
        }
        if errors:
            return StopError("BLOCKED_BUDGET", "budget", "; ".join(errors)).update(state, **update)
        return update

    return node
