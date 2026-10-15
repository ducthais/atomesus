from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.agents.llm import openai_llm
from src.agents.nodes.common import AgentDeps
from src.agents.resolve import ResolveError
from src.agents.service import (
    Job,
    PlanNotAwaitingError,
    PlanRejectedError,
    RunChangedError,
    SpecRejectedError,
    start_agent,
    start_confirm,
    start_continue,
)
from src.codegen.sandbox import SUPPORTED_KINDS
from src.pipeline.auth import current_user, settings
from src.pipeline.postgres import SourceUnavailableError
from src.pipeline.settings import PipelineSettings
from src.policy import PolicyError, without_hidden
from src.run_report import RunReport
from src.run_report.access import policy_for, viewable_run
from src.run_report.quota import claim_runs, release_runs
from src.run_report.store import is_run_cancelled

router = APIRouter(prefix="/agent", tags=["agent"])


def _run_batch_jobs(jobs: list[Job], config: PipelineSettings | None = None) -> None:
    """Fan-out các run sau khi response đã được gửi; mỗi job tự ghi trạng thái cuối vào sổ bằng chứng."""
    if not jobs:
        return
    limit = (config or PipelineSettings()).max_parallel_planning
    with ThreadPoolExecutor(max_workers=min(len(jobs), limit), thread_name_prefix="pipeline-batch") as executor:
        futures = [executor.submit(job) for job in jobs]
        for future in futures:
            future.result()


class SourceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: str = Field(min_length=1, description="Khóa nguồn trong domains.yml, ví dụ sales_pg")
    tables: list[str] = Field(default_factory=list, max_length=50, description="Bảng được dùng; rỗng = mọi bảng")


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=2000, description="Yêu cầu nghiệp vụ bằng lời")
    parent_run_id: str | None = Field(
        default=None, description="Run gốc khi sửa và chạy lại; dùng để so sánh phiên bản"
    )
    sources: list[SourceChoice] | None = Field(
        default=None, min_length=1, max_length=10, description="Nguồn người dùng chọn; agent chỉ lập kế hoạch trên đó"
    )


class AgentBatchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests: list[AgentRunRequest] = Field(
        min_length=2, description="Các pipeline cần tạo; giới hạn thực tế đọc từ budgets.yml"
    )


class AgentBatchRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runs: list[RunReport]
    budget: dict


def agent_deps(config: PipelineSettings = Depends(settings), user: dict = Depends(current_user)) -> AgentDeps:
    """Nạp lại chính sách mỗi run (sửa YAML có hiệu lực ngay, NFR-04)."""
    from src.agents import budget

    try:
        # Đủ domain, kể cả domain ẩn: dependency này còn phục vụ thao tác trên run có sẵn (xác nhận, chạy tiếp).
        # Tạo run mới thì thu hẹp bằng for_new_runs.
        policy = policy_for(user, config, include_hidden=True)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    return AgentDeps(
        llm=openai_llm(),
        policy=policy,
        settings=config,
        cancelled=lambda run_id: is_run_cancelled(config.store_path, run_id),
        reserve_daily=budget.reserve_daily,
    )


def for_new_runs(deps: AgentDeps) -> AgentDeps:
    """Run mới không thấy domain ẩn: planner, cổng semantic và kiểm nguồn đều dùng chính sách đã bỏ chúng."""
    return replace(deps, policy=without_hidden(deps.policy))


def _checked_sources(body: AgentRunRequest, user: dict, deps: AgentDeps) -> list[dict] | None:
    """Chỉ Engineer tạo pipeline; nguồn chọn phải có trong domains.yml và đã có connector chạy thử được."""
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được tạo pipeline.")
    if not body.sources:
        return None
    declared = {ref: decl for domain in deps.policy.domains.domains.values() for ref, decl in domain.sources.items()}
    for choice in body.sources:
        decl = declared.get(choice.source_ref)
        if decl is None:
            raise HTTPException(422, f"Nguồn '{choice.source_ref}' không có trong domains.yml.")
        if decl.kind not in SUPPORTED_KINDS:
            raise HTTPException(
                422, f"Nguồn '{choice.source_ref}' ({decl.kind}) chưa hỗ trợ; chọn nguồn Postgres hoặc REST API."
            )
        for table in choice.tables:
            if decl.tables is not None and table not in decl.tables:
                raise HTTPException(422, f"bảng {table} không nằm trong các bảng đã chọn cho nguồn {choice.source_ref}")
    return [choice.model_dump() for choice in body.sources]


def _checked_parent(body: AgentRunRequest, user: dict, store_path: str) -> None:
    """Run gốc (Sửa & chạy lại) làm bản so sánh nên phải là run người dùng được thấy; không thấy thì 404."""
    if body.parent_run_id is not None:
        viewable_run(store_path, body.parent_run_id, user)


@router.post("/runs", status_code=201, response_model=RunReport)
def create_agent_run(
    body: AgentRunRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Yêu cầu → kế hoạch, dừng ở DRAFT / stage `confirm` chờ Engineer xác nhận (P2, FR-03).

    Trả ngay run ở trạng thái GENERATING; lập kế hoạch chạy nền, tiến độ đọc lại qua GET /runs/{run_id}.
    Hết lượt run trong ngày (budgets.yml per_user.max_runs_per_day) → 429, không gọi LLM.
    """
    deps = for_new_runs(deps)
    sources = _checked_sources(body, user, deps)
    _checked_parent(body, user, config.store_path)
    [run_id] = claim_runs(config.store_path, user, deps.policy.budgets)
    try:
        started, job = start_agent(
            body.request.strip(), user["id"], deps, config.store_path, sources, body.parent_run_id, run_id
        )
    finally:
        release_runs(config.store_path, [run_id])  # run đã vào sổ thì lượt vẫn tính; khởi tạo lỗi thì trả lượt
    background.add_task(job)
    return started


@router.get("/runs/batch/budget")
def batch_budget(
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> dict:
    """Ngân sách hiện tại để UI ước tính mẻ; POST vẫn kiểm tra lại atomically vì số dư có thể đổi."""
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được tạo pipeline.")
    from src.agents import budget

    return budget.estimate_batch(0, deps.policy, config, user["id"])


@router.post("/runs/batch", status_code=201, response_model=AgentBatchRunResponse)
def create_agent_run_batch(
    body: AgentBatchRunRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> AgentBatchRunResponse:
    """Tạo nhiều pipeline all-or-none ở cổng ngân sách rồi mới fan-out các job lập kế hoạch."""
    from src.agents import budget
    from src.run_report.store import rollback_batch_start

    deps = for_new_runs(deps)
    count = len(body.requests)
    estimate = budget.estimate_batch(count, deps.policy, config, user["id"])
    if count > estimate["max_batch_runs"]:
        raise HTTPException(
            422,
            f"Một mẻ tối đa {estimate['max_batch_runs']} pipeline theo budgets.yml per_day.max_batch_runs.",
        )
    checked = [(item, _checked_sources(item, user, deps)) for item in body.requests]
    for item in body.requests:
        _checked_parent(item, user, config.store_path)
    run_ids = claim_runs(config.store_path, user, deps.policy.budgets, len(checked))
    reservation_usd = estimate["reservation_usd_per_run"]
    reservations = [(run_id, 1, "llm:plan:1", reservation_usd) for run_id in run_ids]
    started_jobs: list[tuple[RunReport, Job]] = []
    # Mọi lỗi sau khi giữ lượt (bị chặn ngân sách, sqlite3.OperationalError lúc giữ chỗ, khởi tạo lỗi) đều trả lại
    # lượt run, khoản giữ chỗ và run đã ghi của cả mẻ.
    try:
        reserved, spent, user_spent = budget.reserve_batch_daily(
            config, reservations, estimate["max_usd_per_day"], user["id"], deps.policy.budgets.per_user.max_usd_per_day
        )
        if not reserved and user_spent is not None:
            raise HTTPException(
                409,
                budget.user_limit_error(user_spent, estimate["estimated_usd"], deps.policy)
                + "; không tạo pipeline nào.",
            )
        if not reserved:
            raise HTTPException(
                409,
                f"Cả mẻ ước tính {estimate['estimated_usd']:.4f} USD nhưng hôm nay đã dùng hoặc giữ chỗ "
                f"{spent:.4f}/{estimate['max_usd_per_day']:.4f} USD (trần chung budgets.yml per_day.max_usd); "
                "không tạo pipeline nào.",
            )
        for run_id, (item, sources) in zip(run_ids, checked, strict=True):
            started_jobs.append(
                start_agent(
                    item.request.strip(),
                    user["id"],
                    deps,
                    config.store_path,
                    sources,
                    item.parent_run_id,
                    run_id=run_id,
                )
            )
    except Exception:
        rollback_batch_start(config.store_path, run_ids)
        raise

    background.add_task(_run_batch_jobs, [job for _started, job in started_jobs], config)
    estimate = {
        **estimate,
        "spent_today_usd": spent,
        "remaining_usd": max(0.0, estimate["max_usd_per_day"] - spent),
        "remaining_after_usd": max(0.0, estimate["max_usd_per_day"] - spent - estimate["estimated_usd"]),
        "allowed": True,
    }
    return AgentBatchRunResponse(runs=[started for started, _job in started_jobs], budget=estimate)


class ConfirmPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_mapping: bool = Field(
        default=False, description="Dừng sau khi có Spec để xem và sửa mapping trước khi chạy thử"
    )
    plan: dict | None = Field(default=None, description="Kế hoạch Engineer đã chỉnh ngay tại cổng xác nhận")


@router.post("/runs/{run_id}/confirm-plan", response_model=RunReport)
def confirm_run_plan(
    run_id: str,
    body: ConfirmPlanRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Cổng HITL 1 (P2): Engineer tạo run xác nhận kế hoạch; viết Spec, sinh code và chạy thử ở nền."""
    report = viewable_run(config.store_path, run_id, user)
    if user["role"] != "Engineer" or report.created_by != user["id"]:
        raise HTTPException(403, "Chỉ Engineer tạo run được xác nhận kế hoạch.")
    try:
        started, job = start_confirm(
            report,
            user["id"],
            deps,
            config.store_path,
            body.review_mapping,
            edited_plan=body.plan,
        )
    except PlanNotAwaitingError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PlanRejectedError as exc:
        raise HTTPException(422, "Kế hoạch chưa hợp lệ: " + "; ".join(exc.errors)) from exc
    background.add_task(job)
    return started


class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict = Field(description="Spec của bản nháp, có thể đã được Engineer sửa mapping")


@router.post("/runs/{run_id}/continue", response_model=RunReport)
def continue_draft(
    run_id: str,
    body: ContinueRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Kiểm tra lại Spec đã sửa bằng đúng các cổng của agent (lỗi trả về ngay), rồi sinh code và chạy thử ở nền."""
    draft = viewable_run(config.store_path, run_id, user)
    if draft.created_by != user["id"]:
        raise HTTPException(403, "Chỉ người tạo bản nháp được chạy tiếp.")
    if draft.status != "DRAFT" or draft.spec is None:
        raise HTTPException(409, f"Run đang ở trạng thái {draft.status}, chỉ bản nháp đã có Spec mới chạy tiếp được.")
    try:
        started, job = start_continue(draft, body.spec, user["name"], deps, config.store_path)
    except SpecRejectedError as exc:
        raise HTTPException(422, "Spec chưa hợp lệ: " + "; ".join(exc.errors)) from exc
    except RunChangedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ResolveError, SourceUnavailableError) as exc:
        raise HTTPException(503, str(exc)) from exc
    background.add_task(job)
    return started


@router.get("/status")
async def agent_status():
    return {
        "status": "ready",
        "graph": ["planner", "semantic", "codegen", "budget", "arbitrate", "render", "test_run", "repair"],
    }
