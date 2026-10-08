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
| repair    | §11.2    | tất định  | chạy thử không đạt → bảng định tuyến: vòng spec / SQL / dừng |

Trạng thái kết thúc (mọi trạng thái ngoài DRAFT/GENERATING/VALIDATING) đi thẳng tới END.
Cổng người P2 (xác nhận kế hoạch) chia graph làm hai lượt: `stop_after_plan` dừng sau planner và cổng semantic,
Engineer bấm xác nhận thì chạy tiếp với `entry="codegen"` (src/agents/service.py). Chưa có optimizer (P13).

Chạy thử không đạt thì `repair` tra bảng định tuyến (src/agents/router.py): vòng spec quay về codegen rồi đi lại
budget → … → test_run; vòng SQL quay về codegen (chế độ sql_fix) rồi render thẳng (P6). Trần mỗi vòng ở budgets.yml.

Mọi node chạy dưới trần `budgets.yml: per_run.phase_timeout_sec` (NFR-05, src/agents/timeout.py).
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from src.agents.nodes.arbitrate import arbitrate_node
from src.agents.nodes.budget import budget_node
from src.agents.nodes.codegen import codegen
from src.agents.nodes.common import RUNNING, AgentDeps
from src.agents.nodes.planner import planner
from src.agents.nodes.render import render_node
from src.agents.nodes.repair import repair_node
from src.agents.nodes.sandbox_run import sandbox_run_node
from src.agents.nodes.semantic import semantic_node
from src.agents.state import AgentState
from src.agents.timeout import timed


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
        if stop_after_spec:
            return END
        return "render" if state.get("repair") == "sql_fix" else "budget"  # vòng SQL: render lại ngay (P6)

    return route


def build_graph(
    deps: AgentDeps,
    stop_after_spec: bool = False,
    checkpointer: BaseCheckpointSaver | None = None,
    stop_after_plan: bool = False,
    entry: str = "planner",
):
    """`stop_after_spec`: dừng khi Spec đã hợp lệ (bản nháp để Engineer xem và sửa mapping trước khi chạy).
    `stop_after_plan`: dừng khi kế hoạch đã xong, chờ Engineer xác nhận (P2).
    `entry`: "planner" cho run mới, "codegen" khi kế hoạch đã được xác nhận.
    `checkpointer`: Checkpointer của LangGraph (MemorySaver, SqliteSaver...) lưu trạng thái agent (phi trạng thái).
    """
    graph = StateGraph(AgentState)
    nodes = {
        "planner": planner(deps),
        "semantic": semantic_node(deps),
        "codegen": codegen(deps),
        "budget": budget_node(deps),
        "arbitrate": arbitrate_node(deps),
        "render": render_node(deps),
        "test_run": sandbox_run_node(deps),
        "repair": repair_node(deps),
    }
    for name, node in nodes.items():
        graph.add_node(name, timed(deps, name, node))
    graph.set_entry_point(entry)
    graph.add_conditional_edges("planner", _then("semantic"), ["semantic", END])
    # Cổng semantic chạy trước khi dừng chờ xác nhận (P2): từ hai nghĩa bị chặn trước khi người dùng phải xem kế hoạch.
    graph.add_conditional_edges("semantic", _then(END if stop_after_plan else "codegen"), ["codegen", END])
    graph.add_conditional_edges("codegen", _after_codegen(stop_after_spec), ["codegen", "budget", "render", END])
    graph.add_conditional_edges("budget", _then("arbitrate"), ["arbitrate", END])
    graph.add_conditional_edges("arbitrate", _then("render"), ["render", END])
    graph.add_conditional_edges("render", _then("test_run"), ["test_run", END])
    graph.add_conditional_edges(
        "test_run", lambda state: "repair" if state.get("status") == "NEEDS_HUMAN" else END, ["repair", END]
    )
    graph.add_conditional_edges("repair", _then("codegen"), ["codegen", END])
    saver = checkpointer if checkpointer is not None else getattr(deps, "checkpointer", None)
    return graph.compile(checkpointer=saver)
