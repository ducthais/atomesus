import httpx
from fastapi import APIRouter, Depends

from src.orchestration import dagster
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings

router = APIRouter(prefix="/orchestration", tags=["orchestration"], dependencies=[Depends(current_user)])


@router.get("/status")
def orchestration_status(config: PipelineSettings = Depends(settings)) -> dict:
    """Trạng thái vận hành của các pipeline đã deploy, đọc từ Dagster. Dagster chưa chạy thì `available: false`."""
    base = {"orchestrator": "dagster", "url": config.dagster_url}
    try:
        return {**base, "available": True, "assets": dagster.fetch_status(config.dagster_url)}
    except httpx.HTTPError:
        return {
            **base,
            "available": False,
            "reason": "Dagster chưa chạy hoặc không kết nối được. Bật bằng: make dagster",
        }
    except dagster.DagsterError as exc:
        return {**base, "available": False, "reason": str(exc)}
