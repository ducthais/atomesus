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
        budget_passes = sum(gate.get("name") == "budget" for gate in state.get("gates", [])) + 1
        if not errors and deps.reserve_daily:
            reserved, spent = deps.reserve_daily(
                deps.settings,
                state["run_id"],
                state.get("version", 1),
                f"scan:{budget_passes}",
                estimated["usd"],
                deps.policy.budgets.per_day.max_usd,
            )
            if not reserved:
                result, errors = budget.gate(estimated, deps.policy, spent)
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
