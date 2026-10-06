"""Node `semantic` (P3, tất định): thực thể mơ hồ giữa các domain → BLOCKED_SEMANTIC_CONFLICT, trước khi sinh Spec."""

from src.agents.nodes.common import AgentDeps, StopError
from src.agents.semantic import entity_scan
from src.agents.state import AgentState


def semantic_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        gate, conflict = entity_scan(state["user_request"], state.get("source_scope"), deps.policy)
        update = {"gates": [*state.get("gates", []), gate.model_dump()], "stage_reached": "resolve"}
        if conflict:
            return StopError("BLOCKED_SEMANTIC_CONFLICT", "resolve", conflict).update(state, **update)
        return update

    return node
