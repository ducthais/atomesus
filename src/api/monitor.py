from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.agents.manual_optimize import start_manual_optimize
from src.agents.nodes.common import AgentDeps
from src.api.routes import agent_deps
from src.monitor import alerts, day2, runs
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy.loader import PolicyError, load_policy
from src.run_report import RunReport

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(current_user)])
pipelines_router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@pipelines_router.post("/{model}/optimize", status_code=202, response_model=RunReport)
def optimize_pipeline(
    model: str,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
    deps: AgentDeps = Depends(agent_deps),
) -> RunReport:
    """Engineer yêu cầu tối ưu pipeline đã deploy (UC-3): chạy nền lại pipeline và optimizer P13, không gọi LLM."""
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được yêu cầu tối ưu pipeline.")
    parent = runs.deployed_report(config.store_path, model)
    if parent is None:
        raise HTTPException(404, f"Pipeline '{model}' chưa được deploy.")
    started, job = start_manual_optimize(parent, user["id"], deps, config.store_path)
    background.add_task(job)
    return started


def _engineer(user: dict) -> None:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được chạy monitor hoặc xử lý cảnh báo.")


@router.post("/collect")
def collect_runs(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Thu số đo các lần chạy theo lịch đã xong mà chưa ghi (đọc Dagster, dbt, warehouse, nguồn). Chỉ đọc."""
    _engineer(user)
    try:
        written = runs.collect(config, load_policy())
    except runs.MonitorError as exc:
        raise HTTPException(503, str(exc)) from exc
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"collected": len(written), "runs": written}


@router.get("/pipelines")
def list_pipelines(config: PipelineSettings = Depends(settings)) -> list[dict]:
    """Pipeline đã deploy: lần chạy gần nhất kể từ lần deploy hiện tại, đường cơ sở và các luật suy giảm đang kích
    hoạt (chỉ đọc: không cảnh báo, không tạo run sửa; việc đó là của POST /monitor/check)."""
    try:
        policy = load_policy()
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    return day2.overview(config, policy)


@router.get("/pipelines/{model}")
def pipeline_series(model: str, config: PipelineSettings = Depends(settings)) -> dict:
    """Chuỗi số đo của một pipeline: đường cơ sở (lúc chạy thử) và từng lần chạy theo lịch."""
    found = runs.series(config.store_path, model)
    if found["baseline"] is None and not found["runs"]:
        raise HTTPException(404, f"Pipeline '{model}' chưa deploy hoặc chưa có số đo")
    return found


@router.post("/check")
def check_pipelines(
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
    deps: AgentDeps = Depends(agent_deps),
) -> dict:
    """Chạy 4 luật suy giảm cho mọi pipeline đã deploy (Day 2). Run sửa chạy nền; trả việc đã làm."""
    _engineer(user)
    actions = day2.check(config, deps.policy, day2.launcher(deps, config.store_path, background.add_task))
    return {"actions": actions}


@router.get("/alerts")
def list_alerts(
    model: str | None = None, include_resolved: bool = False, config: PipelineSettings = Depends(settings)
) -> list[dict]:
    """Cảnh báo Day 2: nguồn ngừng cấp, pipeline gắn cờ, run sửa cần người vào sửa. Mới nhất trước."""
    return alerts.list_alerts(config.store_path, model, open_only=not include_resolved)


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: int, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> dict:
    """Đánh dấu cảnh báo đã xử lý."""
    _engineer(user)
    found = alerts.resolve_alert(config.store_path, alert_id)
    if found is None:
        raise HTTPException(404, f"Không có cảnh báo {alert_id}")
    return found
