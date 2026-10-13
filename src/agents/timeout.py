"""Trần thời gian mỗi phase (NFR-05): một run không treo vô hạn.

Mọi node chạy dưới `budgets.yml: per_run.phase_timeout_sec` (đọc từ chính sách của run); quá hạn → INFRA_ERROR ghi
phase bị treo vào error_trace. Dùng cho graph (src/agents/graph.py), luồng chạy tiếp sau khi Engineer sửa mapping
(src/agents/service.py) và lần chạy lại bản đang deploy của run sửa Day 2 (src/agents/regression.py).
"""

import queue
import threading
from collections.abc import Callable

from src.agents.cancellation import bind_phase_cancel, raise_if_phase_cancelled
from src.agents.nodes.common import AgentDeps, CancellationRequestedError, Node, StopError
from src.agents.state import AgentState
from src.codegen.sandbox import SandboxWaitClock, sandbox_slot, sandbox_wait_context

CANCEL_POLL_SEC = 1.0  # chu kỳ đọc cờ hủy trong lúc một phase đang chạy

# Node → (phase ghi vào error_trace khi quá hạn, số phase node đó gánh). test_run gánh P9–P12: mỗi lệnh dlt / dbt
# trong sandbox đã có trần riêng, trần ngoài cùng (4 × trần một phase) chặn phần đọc nguồn và đối soát còn lại.
PHASES = {
    "planner": ("plan", 1),
    "semantic": ("resolve", 1),
    "codegen": ("propose", 1),
    "budget": ("budget", 1),
    "arbitrate": ("arbitrate", 1),
    "render": ("render", 1),
    "test_run": ("t_validate", 4),
    "repair": ("propose", 1),
    # P13: đo lại bản gốc + tối đa 2 bản ứng viên, mỗi lần một lượt chạy thử P9–P12 cộng các lần chạy lại model gold.
    "optimize": ("optimize", 12),
}


def with_timeout(
    node: Node,
    stage: str,
    seconds: float,
    label: str | None = None,
    cancelled: Callable[[AgentState], bool] | None = None,
    queue_progress: Callable[[AgentState, int], None] | None = None,
) -> Node:
    """Chạy node trong luồng riêng; quá `seconds` thì dừng run với INFRA_ERROR ghi phase bị treo.

    Python không dừng được một luồng đang chạy: luồng treo bị bỏ lại (daemon) và kết quả muộn của nó bị bỏ qua.
    Lỗi trong node vẫn ném ra như khi gọi trực tiếp. `cancelled(state)` được hỏi mỗi `CANCEL_POLL_SEC`: người dùng
    hủy run thì phase đang chạy nhận tín hiệu dừng (sandbox kill cả cây tiến trình) và ném CancellationRequestedError.
    """

    def run(state: AgentState) -> dict:
        box: queue.Queue = queue.Queue(maxsize=1)
        stop = threading.Event()
        clock = SandboxWaitClock()

        def check_cancelled() -> None:
            if cancelled is not None and cancelled(state):
                raise CancellationRequestedError

        def progress(position: int) -> None:
            if queue_progress:
                queue_progress(state, position)

        def target() -> None:
            bind_phase_cancel(stop)
            try:
                with sandbox_wait_context(clock, check_cancelled, progress):
                    check_cancelled()
                    value = node(state)
                    check_cancelled()
                    box.put((True, value))
            except BaseException as exc:  # chuyển lỗi về luồng của graph
                box.put((False, exc))

        threading.Thread(target=target, name=f"phase-{stage}", daemon=True).start()
        while True:
            remaining = clock.remaining(seconds)
            try:
                ok, value = box.get(timeout=max(0.0, min(remaining, CANCEL_POLL_SEC)))
                break
            except queue.Empty:
                pass
            if cancelled is not None and cancelled(state):
                stop.set()
                raise CancellationRequestedError
            if clock.remaining(seconds) <= 0:
                stop.set()
                message = f"Phase {label or stage} chạy quá {seconds:g} giây (budgets.yml phase_timeout_sec), đã dừng."
                return StopError("INFRA_ERROR", stage, message).update(state)
        if not ok:
            raise value
        return value

    return run


__all__ = ["raise_if_phase_cancelled", "timed", "with_timeout"]


def timed(deps: AgentDeps, name: str, node: Node) -> Node:
    """Bọc node `name` (khóa của PHASES) bằng trần thời gian đọc từ chính sách của run."""
    stage, phases = PHASES[name]
    label = "chạy thử P9–P12" if phases > 1 else None

    def cancelled(state: AgentState) -> bool:
        return bool(deps.cancelled and state.get("run_id") and deps.cancelled(state["run_id"]))

    def queue_progress(state: AgentState, position: int) -> None:
        from src.agents.service import _record_sandbox_queue

        if store_path := getattr(deps.settings, "store_path", None):
            _record_sandbox_queue(state, store_path, position)

    def run(state: AgentState) -> dict:
        if name == "test_run":  # bao gồm adapter chạy lại bản đang deploy của regression
            with sandbox_slot(deps.settings):
                return node(state)
        return node(state)

    return with_timeout(
        run, stage, deps.policy.budgets.per_run.phase_timeout_sec * phases, label, cancelled, queue_progress
    )
