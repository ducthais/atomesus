from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.agents.llm import openai_llm
from src.agents.nodes.common import AgentDeps
from src.agents.service import run_agent
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy.loader import PolicyError, load_policy
from src.run_report import RunReport

router = APIRouter(prefix="/agent", tags=["agent"])


class SourceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: str = Field(min_length=1, description="Khóa nguồn trong domains.yml, ví dụ sales_pg")
    tables: list[str] = Field(default_factory=list, max_length=50, description="Bảng được dùng; rỗng = mọi bảng")


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=2000, description="Yêu cầu nghiệp vụ bằng lời")
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


@router.post("/runs", status_code=201, response_model=RunReport)
def create_agent_run(
    body: AgentRunRequest,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Yêu cầu → kế hoạch → Spec → sinh code → chạy thử trong sandbox. Chạy đồng bộ; SSE ở sprint sau."""
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được tạo pipeline.")
    if body.sources:
        declared = {
            ref: decl for domain in deps.policy.domains.domains.values() for ref, decl in domain.sources.items()
        }
        for choice in body.sources:
            decl = declared.get(choice.source_ref)
            if decl is None:
                raise HTTPException(422, f"Nguồn '{choice.source_ref}' không có trong domains.yml.")
            if decl.kind != "postgres":
                raise HTTPException(
                    422, f"Nguồn '{choice.source_ref}' ({decl.kind}) chưa hỗ trợ ở Sprint 1; chọn nguồn Postgres."
                )
    sources = [choice.model_dump() for choice in body.sources] if body.sources else None
    return run_agent(body.request.strip(), user["id"], deps, config.store_path, sources)


@router.get("/status")
async def agent_status():
    return {"status": "ready", "graph": ["planner", "codegen", "render", "test_run"]}
