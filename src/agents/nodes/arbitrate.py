"""Node `arbitrate` (P5, tất định): chọn lịch chạy thỏa objectives.yml; không lịch nào đạt thì dừng chờ người.

Chạy sau cổng budget (P8) vì cần chi phí ước tính một lần chạy để so chi phí mỗi ngày giữa các phương án.
"""

from src.agents.arbitrate import CronError, arbitrate
from src.agents.nodes.common import AgentDeps, StopError
from src.agents.state import AgentState


def arbitrate_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        spec = state["spec"]
        try:
            gate, cron, reason = arbitrate(
                spec["schedule"]["cron"],
                (state.get("cost") or {}).get("usd", 0.0),
                deps.policy.objectives[spec["domain"]],
            )
        except CronError as exc:
            return StopError("NEEDS_HUMAN", "arbitrate", str(exc)).update(state)
        update: dict = {"gates": [*state.get("gates", []), gate.model_dump()], "stage_reached": "arbitrate"}
        if cron is None:
            return StopError("NEEDS_HUMAN", "arbitrate", reason).update(state, **update)
        if cron != spec["schedule"]["cron"]:
            update["spec"] = {**spec, "schedule": {**spec["schedule"], "cron": cron}}
        return update

    return node
