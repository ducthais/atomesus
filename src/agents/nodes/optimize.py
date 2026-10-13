"""Node `optimize` (P13, tất định, SPEC §9.3): sau khi chạy thử đạt, thử các luật tối ưu và giữ bản tốt hơn nếu có.

Logic nằm ở src/agents/optimizer.py; node chỉ nối vào graph. Không bao giờ làm hỏng run: không luật nào áp, bản ứng
viên bị loại, hay chính optimizer lỗi thì run giữ nguyên bản gốc (vẫn READY_FOR_REVIEW) và ghi lý do vào `optimizer`.
"""

from src.agents import optimizer
from src.agents.nodes.common import AgentDeps
from src.agents.state import AgentState
from src.spec.models import Spec


def optimize_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        if state.get("status") != "READY_FOR_REVIEW":
            return {}
        spec = Spec.model_validate(state["spec"])
        samples = deps.policy.budgets.optimizer.rerun_samples

        def baseline():  # chỉ đo lại bản gốc (thêm một lần chạy thử) khi có luật áp
            return deps.sandbox(spec, state["generated_files"], deps.policy, deps.settings, rerun_model=samples)

        try:
            outcome = optimizer.optimize(
                state["spec"],
                baseline,
                deps,
                rows=optimizer.main_rows(state.get("checks", []), spec),
                run_id=state.get("run_id", ""),
                version=state.get("version", 1),
            )
        except Exception as exc:  # P13 không bao giờ làm hỏng run (SPEC §9 bảng phase)
            reason = f"optimizer lỗi, giữ bản gốc: {type(exc).__name__}: {str(exc)[:300]}"
            return {"optimizer": [{"rule": rule, "applied": False, "reason": reason} for rule in optimizer.RULES]}
        if outcome["best"]:
            return optimizer.apply_winner(state, outcome["best"], outcome["steps"])
        return {"optimizer": outcome["steps"]}

    return node
