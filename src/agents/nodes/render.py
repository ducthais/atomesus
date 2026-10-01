"""Node `render` (P6, tất định): Spec → 4 + N file bằng templates/ (SPEC §13). Lỗi ở đây là bug template."""

from src.agents.nodes.common import AgentDeps, StopError
from src.agents.state import AgentState
from src.codegen.render import RenderError, file_hashes, render
from src.spec.models import Spec


def render_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        try:
            files = render(Spec.model_validate(state["spec"]), deps.policy)
        except RenderError as exc:
            return StopError("INTERNAL_ERROR", "render", str(exc)).update(state)
        hashes, files_hash = file_hashes(files)
        return {"generated_files": files, "files": hashes, "files_hash": files_hash, "stage_reached": "render"}

    return node
