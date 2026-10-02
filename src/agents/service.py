"""Chạy graph cho một yêu cầu và ghi kết quả vào sổ bằng chứng (RunReport + nội dung file sinh ra).

Hai cách chạy:
- `run_agent`: một mạch planner → codegen → render → test_run.
- `draft_agent` rồi `continue_agent`: dừng sau khi có Spec (trạng thái DRAFT) để Engineer xem và sửa mapping,
  sau đó kiểm tra lại Spec đã sửa bằng đúng các cổng của agent rồi mới render và chạy thử.
"""

from datetime import UTC, datetime
from uuid import uuid4

from src.agents.graph import build_graph
from src.agents.nodes.common import RUNNING, AgentDeps
from src.agents.nodes.render import render_node
from src.agents.nodes.sandbox_run import sandbox_run_node
from src.agents.state import AgentState
from src.agents.validate import validate_spec
from src.run_report import Attempts, Check, Cost, GateResult, RunReport, Timing, save_run_report
from src.run_report.store import save_run_files


class SpecRejectedError(Exception):
    """Spec Engineer sửa không qua được các cổng kiểm tra; `errors` là thông báo cụ thể cho từng lỗi."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _report(state: AgentState, created_at: datetime) -> RunReport:
    status = state.get("status", "INTERNAL_ERROR")
    return RunReport(
        run_id=state["run_id"],
        version=state["version"],
        domain=state.get("domain") or "unknown",
        created_by=state["created_by"],
        trigger=state["trigger"],
        parent_run_id=state.get("parent_run_id"),
        created_at=created_at,
        user_request=state["user_request"],
        status=status,
        stage_reached=state.get("stage_reached", "intake"),
        plan=state.get("plan"),
        spec=state.get("spec"),
        spec_history=state.get("spec_history", []),
        files=state.get("files", {}),
        files_hash=state.get("files_hash"),
        gates=[GateResult.model_validate(gate) for gate in state.get("gates", [])],
        checks=[Check.model_validate(check) for check in state.get("checks", [])],
        # Sandbox DuckDB local không quét warehouse; chi phí thật có ở P8/P14 với BigQuery.
        cost=Cost(estimated_bytes=0, actual_bytes=0, usd=0),
        timing=Timing(baseline_sec=state.get("duration_sec")),
        attempts=Attempts.model_validate(state.get("attempts") or {}),
        llm_calls=state.get("llm_calls", 0),
        error_trace=state.get("error_trace", []),
    )


def _initial(user_request: str, created_by: str, sources: list[dict] | None, parent_run_id: str | None) -> AgentState:
    return {
        "run_id": str(uuid4()),
        "version": 1,
        "created_by": created_by,
        "trigger": "user_request",
        "user_request": user_request,
        "source_scope": sources,
        "parent_run_id": parent_run_id,
        "attempts": {"spec": 0, "sql": 0},
        "llm_calls": 0,
        "error_trace": [],
        "status": "DRAFT",
        "stage_reached": "intake",
    }


def _invoke(deps: AgentDeps, initial: AgentState, stop_after_spec: bool) -> AgentState:
    try:
        return build_graph(deps, stop_after_spec=stop_after_spec).invoke(initial)
    except Exception as exc:  # lỗi ngoài dự kiến vẫn phải để lại dấu vết trong sổ bằng chứng
        return {
            **initial,
            "status": "INTERNAL_ERROR",
            "error_trace": [{"stage": "intake", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}],
        }


def _save(state: AgentState, created_at: datetime, store_path: str) -> RunReport:
    report = _report(state, created_at)
    save_run_report(store_path, report)
    if state.get("generated_files"):
        save_run_files(store_path, report.run_id, report.version, state["generated_files"])
    return report


def run_agent(
    user_request: str,
    created_by: str,
    deps: AgentDeps,
    store_path: str,
    sources: list[dict] | None = None,
    parent_run_id: str | None = None,
) -> RunReport:
    """Chạy planner → codegen → render → test_run đồng bộ, lưu và trả RunReport.

    `sources` giới hạn nguồn/bảng planner được dùng ([{source_ref, tables}]); None = mọi nguồn trong danh mục.
    """
    created_at = datetime.now(UTC)
    state = _invoke(deps, _initial(user_request, created_by, sources, parent_run_id), stop_after_spec=False)
    return _save(state, created_at, store_path)


def draft_agent(
    user_request: str,
    created_by: str,
    deps: AgentDeps,
    store_path: str,
    sources: list[dict] | None = None,
    parent_run_id: str | None = None,
) -> RunReport:
    """Chỉ lập kế hoạch và viết Spec (đã qua các cổng), lưu ở trạng thái DRAFT để Engineer xem và sửa mapping."""
    created_at = datetime.now(UTC)
    state = _invoke(deps, _initial(user_request, created_by, sources, parent_run_id), stop_after_spec=True)
    if state.get("status") == "VALIDATING":  # Spec hợp lệ, dừng chờ Engineer
        state = {**state, "status": "DRAFT"}
    return _save(state, created_at, store_path)


def continue_agent(draft: RunReport, spec: dict, edited_by: str, deps: AgentDeps, store_path: str) -> RunReport:
    """Kiểm tra lại Spec (có thể đã sửa) với schema thật, rồi render và chạy thử; cập nhật chính run nháp.

    Spec không hợp lệ → `SpecRejectedError` (run nháp giữ nguyên để sửa tiếp).
    """
    snapshot = deps.snapshot(draft.plan, deps.policy, deps.settings)
    result = validate_spec(spec, snapshot, deps.policy, draft.user_request)
    if result.errors:
        raise SpecRejectedError(result.errors)
    checked = result.spec.model_dump(by_alias=True, mode="json")
    edited = checked != draft.spec
    history = [
        *draft.spec_history,
        {
            "version": draft.version,
            "reason": f"{edited_by} chỉnh mapping trước khi chạy thử"
            if edited
            else f"{edited_by} giữ nguyên Spec của agent và cho chạy thử",
        },
    ]
    state: AgentState = {
        "run_id": draft.run_id,
        "version": draft.version,
        "created_by": draft.created_by,
        "trigger": draft.trigger,
        "parent_run_id": draft.parent_run_id,
        "user_request": draft.user_request,
        "domain": draft.domain,
        "plan": draft.plan,
        "spec": checked,
        "spec_history": history,
        "schema_snapshot": snapshot,
        "gates": [gate.model_dump() for gate in result.gates],
        "attempts": draft.attempts.model_dump(),
        "llm_calls": draft.llm_calls,
        "error_trace": [],
        "status": "VALIDATING",
        "stage_reached": "static",
    }
    for node in (render_node(deps), sandbox_run_node(deps)):
        if state["status"] not in RUNNING:
            break
        state = {**state, **node(state)}
    return _save(state, draft.created_at, store_path)
