"""Node `test-run` (P9–P12, tất định): chạy bộ file đã render trong sandbox và gom bằng chứng (SPEC §14.2)."""

from src.agents.nodes.common import AgentDeps
from src.agents.state import AgentState
from src.spec.models import Spec


def sandbox_run_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        result = deps.sandbox(Spec.model_validate(state["spec"]), state["generated_files"], deps.policy, deps.settings)
        return {
            "status": result.status,
            "stage_reached": result.stage_reached,
            "gates": [*state.get("gates", []), *(gate.model_dump() for gate in result.gates)],
            "checks": [check.model_dump() for check in result.checks],
            "error_trace": [*state.get("error_trace", []), *result.error_trace],
            "duration_sec": result.duration_sec,
        }

    return node
