from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from src.monitor import runs
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
    root = Path(config.deploy_dir)
    models = (
        sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.endswith(".tmp")) if root.exists() else []
    )
    result = []
    for model in models:
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
