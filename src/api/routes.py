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


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=2000, description="Yêu cầu nghiệp vụ bằng lời")


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
    return run_agent(body.request.strip(), user["id"], deps, config.store_path)


@router.get("/status")
async def agent_status():
    return {"status": "ready", "graph": ["planner", "codegen", "render", "test_run"]}
