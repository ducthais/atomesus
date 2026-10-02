"""Chạy graph cho một yêu cầu và ghi kết quả vào sổ bằng chứng (RunReport + nội dung file sinh ra)."""

from datetime import UTC, datetime
from uuid import uuid4

from src.agents.graph import build_graph
from src.agents.nodes.common import AgentDeps
from src.agents.state import AgentState
from src.run_report import Attempts, Check, Cost, GateResult, RunReport, Timing, save_run_report
from src.run_report.store import save_run_files


def _report(state: AgentState, created_at: datetime) -> RunReport:
    status = state.get("status", "INTERNAL_ERROR")
    return RunReport(
        run_id=state["run_id"],
        version=state["version"],
        domain=state.get("domain") or "unknown",
        created_by=state["created_by"],
        trigger=state["trigger"],
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


def run_agent(
    user_request: str, created_by: str, deps: AgentDeps, store_path: str, sources: list[dict] | None = None
) -> RunReport:
    """Chạy planner → codegen → render → test_run đồng bộ, lưu và trả RunReport.

    `sources` giới hạn nguồn/bảng planner được dùng ([{source_ref, tables}]); None = mọi nguồn trong danh mục.
    """
    created_at = datetime.now(UTC)
    initial: AgentState = {
        "run_id": str(uuid4()),
        "version": 1,
        "created_by": created_by,
        "trigger": "user_request",
        "user_request": user_request,
        "source_scope": sources,
        "attempts": {"spec": 0, "sql": 0},
        "llm_calls": 0,
        "error_trace": [],
        "status": "DRAFT",
        "stage_reached": "intake",
    }
    try:
        state = build_graph(deps).invoke(initial)
    except Exception as exc:  # lỗi ngoài dự kiến vẫn phải để lại dấu vết trong sổ bằng chứng
        state = {
            **initial,
            "status": "INTERNAL_ERROR",
            "error_trace": [{"stage": "intake", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}],
        }
    report = _report(state, created_at)
    save_run_report(store_path, report)
    if state.get("generated_files"):
        save_run_files(store_path, report.run_id, report.version, state["generated_files"])
    return report
