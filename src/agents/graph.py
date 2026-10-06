"""Graph sinh pipeline: planner → semantic → codegen → budget → arbitrate → render → test_run (SPEC §9, §14.2).

| Node      | Phase    | Loại      | Kết quả                                                  |
|-----------|----------|-----------|----------------------------------------------------------|
| planner   | P1       | LLM rẻ    | kế hoạch: nguồn, join, độ hạt, chỉ số, lịch chạy          |
| semantic  | P3       | tất định  | từ có hai nghĩa ở hai domain → BLOCKED_SEMANTIC_CONFLICT  |
| codegen   | P3, P4   | LLM mạnh  | schema thật + 20 dòng mẫu → Spec đã kiểm tra (vòng spec) |
| budget    | P8       | tất định  | ước tính byte, USD; vượt trần run / ngày → BLOCKED_BUDGET |
| arbitrate | P5       | tất định  | lịch chạy thỏa độ tươi (objectives.yml), rẻ nhất; cần chi phí của P8 |
| render    | P6       | tất định  | 4 + N file từ templates/                                 |
| test_run  | P9–P12   | tất định  | chạy dlt + dbt trong sandbox, đối soát, RunReport        |

Trạng thái kết thúc (mọi trạng thái ngoài DRAFT/GENERATING/VALIDATING) đi thẳng tới END.
Sprint 1 chưa có cổng người xác nhận kế hoạch (P2) và chưa có optimizer (P13).
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from src.agents.nodes.arbitrate import arbitrate_node
from src.agents.nodes.budget import budget_node
from src.agents.nodes.codegen import codegen
from src.agents.nodes.common import RUNNING, AgentDeps
from src.agents.nodes.planner import planner
from src.agents.nodes.render import render_node
from src.agents.nodes.sandbox_run import sandbox_run_node
from src.agents.nodes.semantic import semantic_node
from src.agents.state import AgentState


def _then(next_node: str):
    def route(state: AgentState) -> str:
        return next_node if state.get("status") in RUNNING else END

    return route


def _after_codegen(stop_after_spec: bool):
    def route(state: AgentState) -> str:
        if state.get("status") not in RUNNING:
            return END
        if state.get("feedback"):
            return "codegen"  # còn lỗi → vòng spec
        return END if stop_after_spec else "budget"

    return route


def build_graph(deps: AgentDeps, stop_after_spec: bool = False, checkpointer: BaseCheckpointSaver | None = None):
    """`stop_after_spec`: dừng khi Spec đã hợp lệ (bản nháp để Engineer xem và sửa mapping trước khi chạy).
    `checkpointer`: Checkpointer của LangGraph (MemorySaver, SqliteSaver...) lưu trạng thái agent (phi trạng thái).
    """
    graph = StateGraph(AgentState)
    graph.add_node("planner", planner(deps))
    graph.add_node("semantic", semantic_node(deps))
    graph.add_node("codegen", codegen(deps))
    graph.add_node("budget", budget_node(deps))
    graph.add_node("arbitrate", arbitrate_node(deps))
    graph.add_node("render", render_node(deps))
    graph.add_node("test_run", sandbox_run_node(deps))
    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", _then("semantic"), ["semantic", END])
    graph.add_conditional_edges("semantic", _then("codegen"), ["codegen", END])
    graph.add_conditional_edges("codegen", _after_codegen(stop_after_spec), ["codegen", "budget", END])
    graph.add_conditional_edges("budget", _then("arbitrate"), ["arbitrate", END])
    graph.add_conditional_edges("arbitrate", _then("render"), ["render", END])
    graph.add_conditional_edges("render", _then("test_run"), ["test_run", END])
    graph.add_edge("test_run", END)
    saver = checkpointer if checkpointer is not None else getattr(deps, "checkpointer", None)
    return graph.compile(checkpointer=saver)
