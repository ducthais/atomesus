"""UC-3 — Engineer yêu cầu tối ưu một pipeline đã deploy (SPEC §3.3, FR-14): chỉ chạy optimizer, không gọi LLM.

`P0 intake` → kiểm Spec đang chạy với schema nguồn hiện tại → `P6 render` → `P9–P12` chạy thử bản gốc kèm đo chạy lại
(phải đạt) → `P13 optimize` → `P14 report`.

- Không có bước nào gọi LLM: `deps.llm` bị thay bằng hàm ném lỗi, nên `llm_calls = 0` được bảo đảm bằng cấu trúc.
- Bỏ P1–P5 và cổng P7–P8 cho Spec đang chạy (đã qua lúc tạo mới); **bản ứng viên** vẫn qua `ast_guard` và `budget`.
- Schema của nguồn đổi đến mức Spec đang chạy không còn qua kiểm tra → NEEDS_HUMAN, gợi ý tạo run mới (UC-1).
- Có bản vừa đúng vừa nhanh hơn → READY_FOR_REVIEW với Spec, bộ file, bằng chứng của bản đó. Không có →
  NO_IMPROVEMENT, không vào hàng đợi review, `optimizer` ghi lý do từng luật bị loại.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from src.agents import optimizer
from src.agents.nodes.common import AgentDeps
from src.agents.service import Job, _deps_for_store, _event, _guarded, _save
from src.agents.state import AgentState
from src.agents.timeout import timed
from src.agents.validate import validate_spec
from src.codegen.render import RenderError, file_hashes, render
from src.codegen.sandbox import call_sandbox
from src.pipeline.postgres import SourceUnavailableError
from src.run_report import RunReport
from src.spec.models import Spec


class LLMForbiddenError(RuntimeError):
    """UC-3 không được gọi LLM (SPEC FR-14)."""


def _no_llm(*_args, **_kwargs):
    raise LLMForbiddenError("Luồng tối ưu thủ công (UC-3) không được gọi LLM (SPEC FR-14).")


def initial_state(parent: RunReport, actor: str) -> AgentState:
    """Run tối ưu: Spec và kế hoạch của bản đang deploy, bắt đầu ở VALIDATING (không sinh Spec, SPEC §9.6.1)."""
    return {
        "run_id": str(uuid4()),
        "version": 1,
        "created_by": actor,
        "trigger": "manual_optimize",
        "parent_run_id": parent.run_id,
        "source_scope": parent.source_scope,
        "user_request": parent.user_request,
        "domain": parent.domain,
        "plan": parent.plan,
        "spec": parent.spec,
        "attempts": {"spec": 0, "sql": 0},
        "llm_calls": 0,
        "llm_usage": [],
        "error_trace": [],
        "status": "VALIDATING",
        "stage_reached": "intake",
        "timeline": [_event("submitted", actor)],
    }


def _stop(state: AgentState, status: str, stage: str, error: str, **extra) -> AgentState:
    trace = [*state.get("error_trace", []), {"stage": stage, "error": error}]
    return {**state, "status": status, "stage_reached": stage, "error_trace": trace, **extra}


def run(state: AgentState, deps: AgentDeps) -> AgentState:
    """Toàn bộ UC-3 trên một state; trả state kết thúc (READY_FOR_REVIEW, NO_IMPROVEMENT hoặc trạng thái dừng)."""
    deps = replace(deps, llm=_no_llm)
    if not state.get("spec") or not state.get("plan"):
        return _stop(state, "NEEDS_HUMAN", "intake", "Pipeline không có Spec / kế hoạch gốc để tối ưu.")
    try:
        snapshot = deps.snapshot(state["plan"], deps.policy, deps.settings)
    except SourceUnavailableError as exc:
        return _stop(state, "INFRA_ERROR", "resolve", str(exc))
    checked = validate_spec(state["spec"], snapshot, deps.policy, state["user_request"])
    gates = [gate.model_dump() for gate in checked.gates]
    if checked.errors:
        reason = (
            "Schema nguồn đã đổi so với lúc tạo pipeline, Spec đang chạy không còn qua kiểm tra: "
            + "; ".join(checked.errors)[:800]
            + ". Tạo run mới từ yêu cầu (UC-1) thay vì tối ưu."
        )
        return _stop(state, "NEEDS_HUMAN", "static", reason, gates=gates)
    spec = Spec.model_validate(state["spec"])
    try:
        files = render(spec, deps.policy)
    except RenderError as exc:
        return _stop(state, "INTERNAL_ERROR", "render", str(exc), gates=gates)
    hashes, files_hash = file_hashes(files)
    state = {
        **state,
        "gates": gates,
        "generated_files": files,
        "files": hashes,
        "files_hash": files_hash,
        "stage_reached": "render",
    }
    samples = deps.policy.budgets.optimizer.rerun_samples
    measure = timed(
        deps,
        "test_run",
        lambda _s: {"result": call_sandbox(deps.sandbox, spec, files, deps.policy, deps.settings, rerun_model=samples)},
    )
    measured = measure(state)
    baseline = measured.get("result")
    if baseline is None:  # quá trần thời gian
        return {**state, **measured}
    state = {
        **state,
        "checks": [check.model_dump() for check in baseline.checks],
        "gates": [*gates, *(gate.model_dump() for gate in baseline.gates)],
        "duration_sec": baseline.duration_sec,
        "error_trace": [*state.get("error_trace", []), *baseline.error_trace],
        "stage_reached": baseline.stage_reached,
    }
    if baseline.status != "READY_FOR_REVIEW":
        reason = "Bản đang chạy không chạy thử đạt trên dữ liệu hiện tại nên không có gì để so khi tối ưu."
        return _stop(state, baseline.status, baseline.stage_reached, reason)
    step = timed(
        deps,
        "optimize",
        lambda s: {
            "outcome": optimizer.optimize(
                s["spec"],
                baseline,
                deps,
                rows=optimizer.main_rows(baseline.checks, spec),
                run_id=s["run_id"],
                version=s.get("version", 1),
            )
        },
    )
    done = step(state)
    if "outcome" not in done:  # quá trần thời gian của P13
        return {**state, **done}
    outcome = done["outcome"]
    if outcome["best"]:
        winner = optimizer.apply_winner(state, outcome["best"], outcome["steps"])
        return {**state, **winner, "status": "READY_FOR_REVIEW"}
    return {**state, "optimizer": outcome["steps"], "status": "NO_IMPROVEMENT", "stage_reached": "report"}


def start_manual_optimize(parent: RunReport, actor: str, deps: AgentDeps, store_path: str) -> tuple[RunReport, Job]:
    """Ghi run tối ưu (VALIDATING) ngay; việc chạy nền chạy lại pipeline và optimizer, không gọi LLM."""
    initial, created_at = initial_state(parent, actor), datetime.now(UTC)
    deps = _deps_for_store(deps, store_path)
    started = _save(initial, created_at, store_path)

    def job() -> RunReport:
        finished = run(initial, deps)
        return _save({**finished, "timeline": [*finished["timeline"], _event("finished")]}, created_at, store_path)

    return started, _guarded(job, initial, created_at, store_path)
