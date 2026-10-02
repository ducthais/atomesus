from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.agents.llm import openai_llm
from src.agents.nodes.common import AgentDeps
from src.agents.resolve import ResolveError
from src.agents.service import SpecRejectedError, continue_agent, draft_agent, run_agent
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
    """Chỉ Engineer tạo pipeline; nguồn chọn phải có trong domains.yml và là Postgres (Sprint 1)."""
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được tạo pipeline.")
    if not body.sources:
        return None
    declared = {ref: decl for domain in deps.policy.domains.domains.values() for ref, decl in domain.sources.items()}
    for choice in body.sources:
        decl = declared.get(choice.source_ref)
        if decl is None:
            raise HTTPException(422, f"Nguồn '{choice.source_ref}' không có trong domains.yml.")
        if decl.kind != "postgres":
            raise HTTPException(
                422, f"Nguồn '{choice.source_ref}' ({decl.kind}) chưa hỗ trợ ở Sprint 1; chọn nguồn Postgres."
            )
    return [choice.model_dump() for choice in body.sources]


@router.post("/runs", status_code=201, response_model=RunReport)
def create_agent_run(
    body: AgentRunRequest,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Yêu cầu → kế hoạch → Spec → sinh code → chạy thử trong sandbox. Chạy đồng bộ; SSE ở sprint sau."""
    sources = _checked_sources(body, user, deps)
    return run_agent(body.request.strip(), user["id"], deps, config.store_path, sources, body.parent_run_id)


@router.post("/drafts", status_code=201, response_model=RunReport)
def create_draft(
    body: AgentRunRequest,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Chỉ lập kế hoạch và viết Spec, dừng ở trạng thái DRAFT để Engineer xem và sửa mapping trước khi chạy thử."""
    sources = _checked_sources(body, user, deps)
    return draft_agent(body.request.strip(), user["id"], deps, config.store_path, sources, body.parent_run_id)


class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict = Field(description="Spec của bản nháp, có thể đã được Engineer sửa mapping")


@router.post("/runs/{run_id}/continue", response_model=RunReport)
def continue_draft(
    run_id: str,
    body: ContinueRequest,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Kiểm tra lại Spec đã sửa bằng đúng các cổng của agent, rồi sinh code và chạy thử trong sandbox."""
    draft = get_run_report(config.store_path, run_id)
    if draft is None:
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    if draft.created_by != user["id"]:
        raise HTTPException(403, "Chỉ người tạo bản nháp được chạy tiếp.")
    if draft.status != "DRAFT":
        raise HTTPException(409, f"Run đang ở trạng thái {draft.status}, chỉ bản nháp mới chạy tiếp được.")
    try:
        return continue_agent(draft, body.spec, user["name"], deps, config.store_path)
    except SpecRejectedError as exc:
        raise HTTPException(422, "Spec chưa hợp lệ: " + "; ".join(exc.errors)) from exc
    except (ResolveError, SourceUnavailableError) as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/status")
async def agent_status():
    return {"status": "ready", "graph": ["planner", "codegen", "render", "test_run"]}
