from fastapi import APIRouter, HTTPException

from src.models.run_report import RunReport, RunStatus
from src.services.run_report_store import get_run_report, list_run_reports

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[RunReport])
async def list_runs(status: RunStatus | None = None) -> list[RunReport]:
    """Lịch sử run, lọc theo status (SPEC §15). Hiện trả dữ liệu mẫu."""
    return list_run_reports(status)


@router.get("/{run_id}", response_model=RunReport)
async def get_run(run_id: str) -> RunReport:
    """RunReport đầy đủ của một run (SPEC §15). Hiện trả dữ liệu mẫu."""
    report = get_run_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy run {run_id}")
    return report
