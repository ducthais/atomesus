from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.codegen.render import file_hashes
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.run_report.compare import compare, pick_base
from src.run_report.models import Approval, Decision, RunReport, RunStatus
from src.run_report.store import get_run_files, get_run_report, list_run_reports, save_run_report
from src.spec.models import Spec

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(current_user)])


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files_hash: str | None = Field(description="files_hash Reviewer đã xem; lệch với run hiện tại thì từ chối")
    comment: str = Field(default="", max_length=500)


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


@router.get("/{run_id}/compare")
async def compare_run(run_id: str, base: str | None = None, config: PipelineSettings = Depends(settings)) -> dict:
    """So sánh run với một phiên bản khác của cùng pipeline và đánh giá ảnh hưởng nếu duyệt.

    Không truyền `base` thì tự chọn: run cha → bản đang chạy trên Dagster → bản trước của cùng model.
    """
    report = get_run_report(config.store_path, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy run {run_id}")
    if not (report.spec or {}).get("sources"):
        return {"available": False, "reason": "Run chưa có Spec nên chưa có gì để so sánh."}
    reports = list_run_reports(config.store_path)
    if base:
        chosen, reason = get_run_report(config.store_path, base), "bản được chọn để so"
        if chosen is None:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy run {base}")
    else:
        chosen, reason = pick_base(report, reports)
    if chosen is None:
        return {"available": False, "reason": "Chưa có phiên bản nào khác của pipeline này để so sánh."}
    return compare(
        report,
        chosen,
        reason,
        get_run_files(config.store_path, report.run_id, report.version),
        get_run_files(config.store_path, chosen.run_id, chosen.version),
        reports,
    )


@router.get("/{run_id}/files")
async def get_files(run_id: str, version: int | None = None, config: PipelineSettings = Depends(settings)) -> dict:
    """Nội dung đầy đủ các file sinh ra của một version (mặc định version hiện tại của run)."""
    report = get_run_report(config.store_path, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy run {run_id}")
    version = version or report.version
    return {"run_id": run_id, "version": version, "files": get_run_files(config.store_path, run_id, version)}


def _reviewable(run_id: str, body: ReviewRequest, user: dict, store_path: str) -> RunReport:
    """Điều kiện chung của mọi quyết định review (bất biến 3, SPEC §11.1)."""
    if user["role"] != "Reviewer":
        raise HTTPException(403, "Chỉ Reviewer được duyệt hoặc từ chối run.")
    report = get_run_report(store_path, run_id)
    if report is None:
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    if report.created_by == user["id"]:
        raise HTTPException(403, "Không được tự duyệt run của mình.")
    if report.status != "READY_FOR_REVIEW":
        raise HTTPException(409, f"Run đang ở trạng thái {report.status}, chỉ run chờ duyệt mới review được.")
    if not report.files_hash or not report.spec:
        raise HTTPException(409, "Run không có bộ file sinh ra (ví dụ run làm sạch thủ công) nên không review được.")
    if body.files_hash != report.files_hash:
        raise HTTPException(409, "Bộ file đã thay đổi kể từ lúc bạn xem. Tải lại run rồi review lại.")
    return report


def _decide(report: RunReport, decision: Decision, user: dict, comment: str, **changes) -> RunReport:
    """Ghi chữ ký review; validate lại cả RunReport để giữ bất biến của hợp đồng."""
    approval = Approval(
        decision=decision,
        reviewer=user["id"],
        files_hash=report.files_hash,
        version=report.version,
        at=datetime.now(UTC),
        comment=comment,
    )
    return RunReport.model_validate({**report.model_dump(), "approval": approval.model_dump(), **changes})


@router.post("/{run_id}/approve", response_model=RunReport)
def approve_run(
    run_id: str, body: ReviewRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> RunReport:
    """Duyệt đúng bộ file theo files_hash rồi ghi ra Dagster local (Sprint 1; Sprint 3 thay bằng pull request)."""
    # Import muộn: codegen.sandbox import run_report.models, import sớm ở đầu file sẽ thành vòng.
    from src.codegen.deploy import deploy_local

    report = _reviewable(run_id, body, user, config.store_path)
    files = get_run_files(config.store_path, run_id, report.version)
    if file_hashes(files)[1] != report.files_hash:
        raise HTTPException(409, "Nội dung file đã lưu không khớp files_hash nên không triển khai được.")
    approved = _decide(report, "approved", user, body.comment, status="APPROVED", stage_reached="review")
    save_run_report(config.store_path, approved)  # chữ ký được ghi trước, deploy lỗi vẫn còn dấu vết duyệt
    try:
        deploy_local(Spec.model_validate(report.spec), files, config.deploy_dir)
    except OSError as exc:
        trace = [*approved.error_trace, {"stage": "deploy", "error": f"Ghi project Dagster local thất bại: {exc}"}]
        result = approved.model_copy(
            update={"status": "DEPLOY_FAILED", "stage_reached": "deploy", "error_trace": trace}
        )
    else:
        result = approved.model_copy(update={"status": "DEPLOYED", "stage_reached": "deploy"})
    save_run_report(config.store_path, result)
    return result


@router.post("/{run_id}/reject", response_model=RunReport)
def reject_run(
    run_id: str, body: ReviewRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> RunReport:
    """Từ chối run; không ghi file nào ra Dagster."""
    report = _reviewable(run_id, body, user, config.store_path)
    rejected = _decide(report, "rejected", user, body.comment, status="REJECTED", stage_reached="review")
    save_run_report(config.store_path, rejected)
    return rejected
