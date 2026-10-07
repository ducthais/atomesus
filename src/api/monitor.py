from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.agents.nodes.common import AgentDeps
from src.api.routes import agent_deps
from src.monitor import alerts, day2, runs
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy.loader import PolicyError, load_policy

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(current_user)])


@router.post("/collect")
def collect_runs(config: PipelineSettings = Depends(settings)) -> dict:
    """Thu số đo các lần chạy theo lịch đã xong mà chưa ghi (đọc Dagster, dbt, warehouse, nguồn). Chỉ đọc."""
    try:
        written = runs.collect(config, load_policy())
    except runs.MonitorError as exc:
        raise HTTPException(503, str(exc)) from exc
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"collected": len(written), "runs": written}


@router.get("/pipelines")
def list_pipelines(config: PipelineSettings = Depends(settings)) -> list[dict]:
    """Pipeline đã deploy và lần chạy gần nhất đã ghi."""
    result = []
    for model in runs.deployed_models(config.deploy_dir):
        found = runs.series(config.store_path, model)
        result.append(
            {"model": model, "runs": len(found["runs"]), "last": found["runs"][-1] if found["runs"] else None}
        )
    return result


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
    config: PipelineSettings = Depends(settings),
    deps: AgentDeps = Depends(agent_deps),
) -> dict:
    """Chạy 4 luật suy giảm cho mọi pipeline đã deploy (Day 2). Run sửa chạy nền; trả việc đã làm."""
    actions = day2.check(config, deps.policy, day2.launcher(deps, config.store_path, background.add_task))
    return {"actions": actions}


@router.get("/alerts")
def list_alerts(
    model: str | None = None, include_resolved: bool = False, config: PipelineSettings = Depends(settings)
) -> list[dict]:
    """Cảnh báo Day 2: nguồn ngừng cấp, pipeline gắn cờ, run sửa cần người vào sửa. Mới nhất trước."""
    return alerts.list_alerts(config.store_path, model, open_only=not include_resolved)


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, config: PipelineSettings = Depends(settings)) -> dict:
    """Đánh dấu cảnh báo đã xử lý."""
    found = alerts.resolve_alert(config.store_path, alert_id)
    if found is None:
        raise HTTPException(404, f"Không có cảnh báo {alert_id}")
    return found
