"""Graph sinh pipeline: planner → codegen → render → test_run (SPEC §9, §14.2).

| Node      | Phase    | Loại      | Kết quả                                                  |
|-----------|----------|-----------|----------------------------------------------------------|
| planner   | P1       | LLM rẻ    | kế hoạch: nguồn, join, độ hạt, chỉ số, lịch chạy          |
| codegen   | P3, P4   | LLM mạnh  | schema thật + 20 dòng mẫu → Spec đã kiểm tra (vòng spec) |
| render    | P6       | tất định  | 4 + N file từ templates/                                 |
| test_run  | P9–P12   | tất định  | chạy dlt + dbt trong sandbox, đối soát, RunReport        |

Trạng thái kết thúc (mọi trạng thái ngoài DRAFT/GENERATING/VALIDATING) đi thẳng tới END.
Sprint 1 chưa có cổng người xác nhận kế hoạch (P2) và chưa có optimizer (P13).
"""

from langgraph.graph import END, StateGraph

from src.agents.nodes.codegen import codegen
from src.agents.nodes.common import RUNNING, AgentDeps
from src.agents.nodes.planner import planner
from src.agents.nodes.render import render_node
from src.agents.nodes.sandbox_run import sandbox_run_node
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
        return END if stop_after_spec else "render"

    return route


def build_graph(deps: AgentDeps, stop_after_spec: bool = False):
    """`stop_after_spec`: dừng khi Spec đã hợp lệ (bản nháp để Engineer xem và sửa mapping trước khi chạy)."""
    graph = StateGraph(AgentState)
    graph.add_node("planner", planner(deps))
    graph.add_node("codegen", codegen(deps))
    graph.add_node("render", render_node(deps))
    graph.add_node("test_run", sandbox_run_node(deps))
    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", _then("codegen"), ["codegen", END])
    graph.add_conditional_edges("codegen", _after_codegen(stop_after_spec), ["codegen", "render", END])
    graph.add_conditional_edges("render", _then("test_run"), ["test_run", END])
    graph.add_edge("test_run", END)
    return graph.compile()
