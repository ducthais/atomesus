from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.agents.llm import openai_llm
from src.agents.nodes.common import AgentDeps
from src.agents.resolve import ResolveError
from src.agents.service import SpecRejectedError, start_agent, start_continue
from src.codegen.sandbox import SUPPORTED_KINDS
from src.pipeline.auth import current_user, settings
from src.pipeline.postgres import SourceUnavailableError
from src.pipeline.settings import PipelineSettings
from src.policy.loader import PolicyError, load_policy
from src.run_report import RunReport
from src.run_report.store import get_run_report

router = APIRouter(prefix="/agent", tags=["agent"])


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


def agent_deps(config: PipelineSettings = Depends(settings)) -> AgentDeps:
    """Nạp lại chính sách mỗi run (sửa YAML có hiệu lực ngay, NFR-04)."""
    try:
        policy = load_policy()
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    return AgentDeps(llm=openai_llm(), policy=policy, settings=config)


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
    return [choice.model_dump() for choice in body.sources]


@router.post("/runs", status_code=201, response_model=RunReport)
def create_agent_run(
    body: AgentRunRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Yêu cầu → kế hoạch → Spec → sinh code → chạy thử trong sandbox.

    Trả ngay run ở trạng thái GENERATING; các bước chạy nền, tiến độ đọc lại qua GET /runs/{run_id}.
    """
    sources = _checked_sources(body, user, deps)
    started, job = start_agent(body.request.strip(), user["id"], deps, config.store_path, sources, body.parent_run_id)
    background.add_task(job)
    return started


@router.post("/drafts", status_code=201, response_model=RunReport)
def create_draft(
    body: AgentRunRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Chỉ lập kế hoạch và viết Spec, dừng ở trạng thái DRAFT để Engineer xem và sửa mapping trước khi chạy thử.

    Trả ngay run ở trạng thái GENERATING; lập kế hoạch và viết Spec chạy nền.
    """
    sources = _checked_sources(body, user, deps)
    started, job = start_agent(
        body.request.strip(), user["id"], deps, config.store_path, sources, body.parent_run_id, draft=True
    )
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
    draft = get_run_report(config.store_path, run_id)
    if draft is None:
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    if draft.created_by != user["id"]:
        raise HTTPException(403, "Chỉ người tạo bản nháp được chạy tiếp.")
    if draft.status != "DRAFT":
        raise HTTPException(409, f"Run đang ở trạng thái {draft.status}, chỉ bản nháp mới chạy tiếp được.")
    try:
        started, job = start_continue(draft, body.spec, user["name"], deps, config.store_path)
    except SpecRejectedError as exc:
        raise HTTPException(422, "Spec chưa hợp lệ: " + "; ".join(exc.errors)) from exc
    except (ResolveError, SourceUnavailableError) as exc:
        raise HTTPException(503, str(exc)) from exc
    background.add_task(job)
    return started


@router.get("/status")
async def agent_status():
    return {"status": "ready", "graph": ["planner", "codegen", "render", "test_run"]}
