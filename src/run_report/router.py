from fastapi import APIRouter, Depends, HTTPException

from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.run_report.models import RunReport, RunStatus
from src.run_report.store import get_run_files, get_run_report, list_run_reports

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(current_user)])


@router.get("", response_model=list[RunReport])
async def list_runs(status: RunStatus | None = None, config: PipelineSettings = Depends(settings)) -> list[RunReport]:
    """Lịch sử run từ sổ bằng chứng, mới nhất trước, lọc theo status (SPEC §15)."""
    return list_run_reports(config.store_path, status)


@router.get("/{run_id}", response_model=RunReport)
async def get_run(run_id: str, config: PipelineSettings = Depends(settings)) -> RunReport:
    """RunReport đầy đủ của một run (SPEC §15)."""
    report = get_run_report(config.store_path, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy run {run_id}")
    return report


@router.get("/{run_id}/files")
async def get_files(run_id: str, version: int | None = None, config: PipelineSettings = Depends(settings)) -> dict:
    """Nội dung đầy đủ các file sinh ra của một version (mặc định version hiện tại của run)."""
    report = get_run_report(config.store_path, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy run {run_id}")
    version = version or report.version
    return {"run_id": run_id, "version": version, "files": get_run_files(config.store_path, run_id, version)}
