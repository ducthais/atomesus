import httpx
from fastapi import APIRouter, Depends

from src.api.monitor import visible_models
from src.monitor.runs import JOB_SUFFIX
from src.orchestration import dagster
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings

router = APIRouter(prefix="/orchestration", tags=["orchestration"], dependencies=[Depends(current_user)])


def _visible_jobs(user: dict, store_path: str, assets: dict[str, dict]) -> dict[str, dict]:
    """Chỉ job của pipeline người dùng được thấy (job `<model>_job`, xét theo run đang deploy của model đó)."""
    if user["role"] == "Admin":
        return assets
    jobs = {job for asset in assets.values() for job in asset["jobs"]}
    models = visible_models(
        user, store_path, {job.removesuffix(JOB_SUFFIX) for job in jobs if job.endswith(JOB_SUFFIX)}
    )
    return dagster.filter_status(
        assets, lambda job: job.endswith(JOB_SUFFIX) and job.removesuffix(JOB_SUFFIX) in models
    )


@router.get("/status")
def orchestration_status(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Trạng thái vận hành của các pipeline đã deploy mà người dùng được thấy, đọc từ Dagster. Dagster chưa chạy thì
    `available: false`."""
    base = {"orchestrator": "dagster", "url": config.dagster_url}
    try:
        assets = dagster.fetch_status(config.dagster_url)
    except httpx.HTTPError:
        return {
            **base,
            "available": False,
            "reason": "Dagster chưa chạy hoặc không kết nối được. Bật bằng: make dagster",
        }
    except dagster.DagsterError as exc:
        return {**base, "available": False, "reason": str(exc)}
    return {**base, "available": True, "assets": _visible_jobs(user, config.store_path, assets)}
