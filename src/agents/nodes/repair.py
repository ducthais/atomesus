"""Node `repair` (tất định): chạy thử không đạt thì tra bảng định tuyến (src/agents/router.py) để tự sửa.

- Vòng spec (`propose`): codegen viết lại Spec kèm lỗi cụ thể, rồi đi lại budget → arbitrate → render → test_run.
- Vòng SQL (`sql_fix`): codegen chỉ sửa biểu thức SQL, rồi render và chạy thử lại ngay (P6).
- Hết lượt (`budgets.yml: per_run.max_spec_attempts` / `max_sql_attempts`) hoặc lỗi phải dừng: giữ trạng thái dừng.

Lần chạy thử không đạt vẫn để lại dấu vết: một mục `spec_history` (Spec đã chạy, lỗi, hướng sửa) và các dòng
`error_trace` có `repair`. Cổng `post_run` và các check của lần đó được thay bằng kết quả lần chạy sau.
"""

from src.agents import router
from src.agents.nodes.common import AgentDeps
from src.agents.state import AgentState

LOOP_LABEL = {"propose": "viết lại Spec", "sql_fix": "sửa biểu thức SQL"}


def repair_node(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        failures = router.classify(state)
        decision = router.decide(failures)
        if decision.goto == "stop":
            return {"status": decision.status or state["status"]} if decision.status else {}
        attempts = dict(state.get("attempts") or {"spec": 0, "sql": 0})
        key, limit = router.LOOPS[decision.goto], router.limit(decision.goto, deps.policy)
        if attempts.get(key, 0) >= limit:
            trace = {
                "stage": "repair",
                "error": f"Chạy thử vẫn không đạt sau {limit} lần {LOOP_LABEL[decision.goto]} "
                f"(budgets.yml max_{key}_attempts).",
            }
            return {"error_trace": [*state.get("error_trace", []), trace]}
        attempts[key] = attempts.get(key, 0) + 1
        feedback = [failure.feedback() for failure in failures]
        history = [
            *state.get("spec_history", []),
            {
                "attempt": len(state.get("spec_history", [])) + 1,
                "spec": state.get("spec"),
                "errors": feedback,
                "reason": f"Chạy thử không đạt, tự {LOOP_LABEL[decision.goto]} (lần {attempts[key]}/{limit})",
            },
        ]
        trace = [*state.get("error_trace", [])]
        trace += [{"stage": "repair", "error": item, "repair": decision.goto} for item in feedback]
        return {
            "status": "VALIDATING",
            "stage_reached": "propose",
            "attempts": attempts,
            "feedback": feedback,
            "repair": decision.goto,
            "spec_history": history,
            "error_trace": trace,
            "gates": [g for g in state.get("gates", []) if g.get("name") != "post_run"],
            "checks": [],
        }

    return node
