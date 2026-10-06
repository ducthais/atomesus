"""Chạy graph cho một yêu cầu và ghi kết quả vào sổ bằng chứng (RunReport + nội dung file sinh ra).

Hai cách chạy:
- `run_agent`: một mạch planner → codegen → render → test_run.
- `draft_agent` rồi `continue_agent`: dừng sau khi có Spec (trạng thái DRAFT) để Engineer xem và sửa mapping,
  sau đó kiểm tra lại Spec đã sửa bằng đúng các cổng của agent rồi mới render và chạy thử.

Một run mất khoảng 15–30 giây (2 lần gọi LLM, dlt tải dữ liệu thật, dbt build). API dùng `start_agent` /
`start_continue`: ghi run vào sổ bằng chứng ngay và trả về, phần còn lại chạy nền và ghi lại tiến độ sau mỗi
bước để giao diện theo dõi.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver

from src.agents.graph import build_graph
from src.agents.nodes.arbitrate import arbitrate_node
from src.agents.nodes.budget import budget_node
from src.agents.nodes.common import RUNNING, AgentDeps
from src.agents.nodes.render import render_node
from src.agents.nodes.sandbox_run import sandbox_run_node
from src.agents.state import AgentState
from src.agents.validate import validate_spec
from src.run_report import Attempts, Check, Cost, GateResult, RunReport, Timing, save_run_report
from src.run_report.store import save_run_files

log = logging.getLogger(__name__)
Job = Callable[[], RunReport]
DEFAULT_CHECKPOINTER = MemorySaver()


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
        source_scope=state.get("source_scope"),
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
        # Ước tính ở cổng budget (P8); DuckDB local không có chi phí thật nên actual = ước tính (SPEC §12).
        cost=Cost.model_validate(state.get("cost") or {"estimated_bytes": 0, "actual_bytes": 0, "usd": 0}),
        timing=Timing(baseline_sec=state.get("duration_sec")),
        attempts=Attempts.model_validate(state.get("attempts") or {}),
        llm_calls=state.get("llm_calls", 0),
        error_trace=state.get("error_trace", []),
        timeline=state.get("timeline", []),
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
        "status": "GENERATING",
        "stage_reached": "intake",
        "timeline": [_event("submitted", created_by)],
    }


def _event(name: str, actor: str | None = None) -> dict:
    return {"event": name, "at": datetime.now(UTC).isoformat(), "actor": actor}


def _invoke(
    deps: AgentDeps, initial: AgentState, stop_after_spec: bool, progress: Callable[[AgentState], None] | None = None
) -> AgentState:
    try:
        state = initial
        checkpointer = getattr(deps, "checkpointer", None) or DEFAULT_CHECKPOINTER
        graph = build_graph(deps, stop_after_spec=stop_after_spec, checkpointer=checkpointer)
        thread_id = initial.get("run_id") or str(uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        for state in graph.stream(initial, config=config, stream_mode="values"):
            if progress and state.get("status") in RUNNING:
                progress(state)
        return state
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


class _Progress:
    """Ghi trạng thái đang chạy vào sổ bằng chứng sau mỗi bước (giao diện đọc lại để hiện tiến độ),
    và mốc spec_ready lần đầu Spec qua các cổng."""

    def __init__(self, timeline: list[dict], created_at: datetime, store_path: str):
        self.timeline, self.created_at, self.store_path = list(timeline), created_at, store_path

    def __call__(self, state: AgentState) -> None:
        if state.get("status") == "VALIDATING" and not any(e["event"] == "spec_ready" for e in self.timeline):
            self.timeline.append(_event("spec_ready"))
        save_run_report(self.store_path, _report({**state, "timeline": self.timeline}, self.created_at))


def _run(initial: AgentState, created_at: datetime, deps: AgentDeps, store_path: str, draft: bool) -> RunReport:
    progress = _Progress(initial.get("timeline", []), created_at, store_path)
    state = _invoke(deps, initial, stop_after_spec=draft, progress=progress)
    if draft and state.get("status") == "VALIDATING":  # Spec hợp lệ, dừng chờ Engineer
        return _save({**state, "status": "DRAFT", "timeline": progress.timeline}, created_at, store_path)
    return _save({**state, "timeline": [*progress.timeline, _event("finished")]}, created_at, store_path)


def _guarded(job: Job, state: AgentState, created_at: datetime, store_path: str) -> Job:
    """Việc chạy nền không có ai nhận ngoại lệ: lỗi bất ngờ vẫn phải kết thúc run trong sổ bằng chứng."""

    def run() -> RunReport:
        try:
            return job()
        except Exception as exc:
            log.exception("Run %s lỗi khi chạy nền", state["run_id"])
            error = {"stage": state.get("stage_reached", "intake"), "error": f"{type(exc).__name__}: {exc}"[:500]}
            return _save({**state, "status": "INTERNAL_ERROR", "error_trace": [error]}, created_at, store_path)

    return run


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
    initial = _initial(user_request, created_by, sources, parent_run_id)
    return _run(initial, datetime.now(UTC), deps, store_path, draft=False)


def draft_agent(
    user_request: str,
    created_by: str,
    deps: AgentDeps,
    store_path: str,
    sources: list[dict] | None = None,
    parent_run_id: str | None = None,
) -> RunReport:
    """Chỉ lập kế hoạch và viết Spec (đã qua các cổng), lưu ở trạng thái DRAFT để Engineer xem và sửa mapping."""
    initial = _initial(user_request, created_by, sources, parent_run_id)
    return _run(initial, datetime.now(UTC), deps, store_path, draft=True)


def start_agent(
    user_request: str,
    created_by: str,
    deps: AgentDeps,
    store_path: str,
    sources: list[dict] | None = None,
    parent_run_id: str | None = None,
    draft: bool = False,
) -> tuple[RunReport, Job]:
    """Ghi run mới (GENERATING) vào sổ bằng chứng ngay; trả kèm việc cần chạy nền để đi hết các bước."""
    initial, created_at = _initial(user_request, created_by, sources, parent_run_id), datetime.now(UTC)
    started = _save(initial, created_at, store_path)
    job = _guarded(lambda: _run(initial, created_at, deps, store_path, draft), initial, created_at, store_path)
    return started, job


def continue_agent(draft: RunReport, spec: dict, edited_by: str, deps: AgentDeps, store_path: str) -> RunReport:
    """Kiểm tra lại Spec (có thể đã sửa) với schema thật, rồi render và chạy thử; cập nhật chính run nháp.

    Spec không hợp lệ → `SpecRejectedError` (run nháp giữ nguyên để sửa tiếp).
    """
    _started, job = start_continue(draft, spec, edited_by, deps, store_path)
    return job()


def start_continue(
    draft: RunReport, spec: dict, edited_by: str, deps: AgentDeps, store_path: str
) -> tuple[RunReport, Job]:
    """Kiểm tra Spec ngay (lỗi trả về tức thì), ghi run sang VALIDATING; render + chạy thử là việc chạy nền."""
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
            "reason": f"{edited_by} chỉnh mapping và xác nhận Spec đúng nhu cầu trước khi chạy thử"
            if edited
            else f"{edited_by} giữ nguyên Spec của agent, xác nhận đúng nhu cầu và cho chạy thử",
        },
    ]
    state: AgentState = {
        "run_id": draft.run_id,
        "version": draft.version,
        "created_by": draft.created_by,
        "trigger": draft.trigger,
        "parent_run_id": draft.parent_run_id,
        "source_scope": draft.source_scope,
        "user_request": draft.user_request,
        "domain": draft.domain,
        "plan": draft.plan,
        "spec": checked,
        "spec_history": history,
        "schema_snapshot": snapshot,
        "gates": [
            *(g.model_dump() for g in draft.gates if g.name == "semantic"),
            *(g.model_dump() for g in result.gates),
        ],
        "attempts": draft.attempts.model_dump(),
        "llm_calls": draft.llm_calls,
        "error_trace": [],
        "status": "VALIDATING",
        "stage_reached": "static",
        "timeline": [*(e.model_dump(mode="json") for e in draft.timeline), _event("mapping_submitted", edited_by)],
    }
    started = _save(state, draft.created_at, store_path)

    def job() -> RunReport:
        current = state
        for node in (budget_node(deps), arbitrate_node(deps), render_node(deps), sandbox_run_node(deps)):
            if current["status"] not in RUNNING:
                break
            current = {**current, **node(current)}
            if current["status"] in RUNNING:
                save_run_report(store_path, _report(current, draft.created_at))
        return _save({**current, "timeline": [*current["timeline"], _event("finished")]}, draft.created_at, store_path)

    return started, _guarded(job, state, draft.created_at, store_path)
