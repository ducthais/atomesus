from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.codegen.github import GithubClient, GithubError
from src.codegen.render import file_hashes
from src.deployer.manifest import ManifestError
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy import PolicyError, effective_policy
from src.run_report.access import is_own_run, model_taken, run_visible, viewable_run, visible_run_ids
from src.run_report.compare import compare, pick_base
from src.run_report.models import Approval, Decision, Deployment, RunReport, RunStatus
from src.run_report.store import _database as run_report_database
from src.run_report.store import get_run_files, list_run_reports, save_run_report, transition_run_report

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(current_user)])


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files_hash: str | None = Field(description="files_hash Reviewer đã xem; lệch với run hiện tại thì từ chối")
    comment: str = Field(default="", max_length=500)


class BatchReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1)
    files_hash: str = Field(min_length=1)


class BatchReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[BatchReviewItem] = Field(min_length=1, max_length=50)
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _valid_batch(self) -> "BatchReviewRequest":
        ids = [item.run_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Mỗi run chỉ được xuất hiện một lần trong mẻ duyệt.")
        self.comment = self.comment.strip()
        if self.decision == "reject" and not self.comment:
            raise ValueError("Từ chối nhiều run cần ghi lý do.")
        return self


class BatchReviewResult(BaseModel):
    run_id: str
    ok: bool
    status: str | None = None
    message: str


class BatchReviewResponse(BaseModel):
    results: list[BatchReviewResult]


class RequestChangesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str = Field(min_length=1, max_length=2000)
    files_hash: str | None = Field(default=None, description="Hash Reviewer đang xem; để trống vẫn hỗ trợ client cũ")
    version: int | None = Field(default=None, ge=1, description="Version Reviewer đang xem")

    @field_validator("feedback")
    @classmethod
    def _feedback_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Nhận xét yêu cầu sửa không được để trống.")
        return value.strip()


def request_change_deps(config: PipelineSettings = Depends(settings)):
    """Phụ thuộc của agent cho "Yêu cầu sửa".

    Import muộn: src.agents.* import gói src.run_report (models, store), nên import agent ở đầu file này tạo vòng
    run_report → router → agents → run_report và làm hỏng mọi chỗ import src.run_report trước src.main
    (scripts/dagster_local.py, src.codegen.sandbox, src.monitor.runs).
    """
    from src.agents import budget
    from src.agents.llm import openai_llm
    from src.agents.nodes.common import AgentDeps
    from src.run_report.store import is_run_cancelled

    try:
        policy = effective_policy(config)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc

    return AgentDeps(
        llm=openai_llm(),
        policy=policy,
        settings=config,
        cancelled=lambda run_id: is_run_cancelled(config.store_path, run_id),
        reserve_daily=budget.reserve_daily,
    )


def rework_deps(config: PipelineSettings = Depends(settings)) -> Callable[[], Any]:
    """AgentDeps cho vòng P16 → P4 (CI đỏ / pull request bị đóng), tạo khi job nền chạy: webhook, Deploy không nạp
    policy hay LLM khi không cần."""
    return lambda: request_change_deps(config)


@router.get("", response_model=list[RunReport])
async def list_runs(
    status: RunStatus | None = None, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> list[RunReport]:
    """Lịch sử run từ sổ bằng chứng, mới nhất trước, lọc theo status (SPEC §15); chỉ run người dùng được thấy."""
    return list_run_reports(config.store_path, status, run_ids=visible_run_ids(user, config.store_path))


@router.get("/{run_id}", response_model=RunReport)
async def get_run(
    run_id: str, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> RunReport:
    """RunReport đầy đủ của một run (SPEC §15)."""
    return viewable_run(config.store_path, run_id, user)


@router.get("/{run_id}/compare")
async def compare_run(
    run_id: str,
    base: str | None = None,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> dict:
    """So sánh run với một phiên bản khác của cùng pipeline và đánh giá ảnh hưởng nếu duyệt.

    Không truyền `base` thì tự chọn: run cha → bản đang chạy trên Dagster → bản trước của cùng model. Chỉ so với
    run người dùng được thấy: bản của người khác không bao giờ làm base.
    """
    report = viewable_run(config.store_path, run_id, user)
    if not (report.spec or {}).get("sources"):
        return {"available": False, "reason": "Run chưa có Spec nên chưa có gì để so sánh."}
    reports = list_run_reports(config.store_path, run_ids=visible_run_ids(user, config.store_path))
    if base:
        chosen, reason = viewable_run(config.store_path, base, user), "bản được chọn để so"
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
async def get_files(
    run_id: str,
    version: int | None = None,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> dict:
    """Nội dung đầy đủ các file sinh ra của một version (mặc định version hiện tại của run)."""
    report = viewable_run(config.store_path, run_id, user)
    version = version or report.version
    return {"run_id": run_id, "version": version, "files": get_run_files(config.store_path, run_id, version)}


def _reviewable(run_id: str, body: ReviewRequest, user: dict, store_path: str) -> RunReport:
    """Điều kiện chung của mọi quyết định review (bất biến 3, SPEC §11.1)."""
    # Xét quyền thấy trước vai: run không được thấy là 404 với mọi vai. Reviewer chỉ thấy run của Engineer đã mời mình.
    report = viewable_run(store_path, run_id, user)
    if user["role"] != "Reviewer":
        raise HTTPException(403, "Chỉ Reviewer được duyệt hoặc từ chối run.")
    if is_own_run(store_path, report, user):
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
    return RunReport.model_validate(
        {
            **report.model_dump(),
            "approval": approval.model_dump(),
            "review_history": [*(item.model_dump() for item in report.review_history), approval.model_dump()],
            **changes,
        }
    )


@router.post("/{run_id}/approve", response_model=RunReport)
def approve_run(
    run_id: str, body: ReviewRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> RunReport:
    """Duyệt đúng bộ file theo files_hash: chỉ ghi chữ ký và chuyển APPROVED, không ghi file nào ra đĩa hay Dagster.

    Đưa lên production là bước riêng: POST /runs/{id}/deploy mở pull request (SPEC §9.5).
    """
    report = _reviewable(run_id, body, user, config.store_path)
    files = get_run_files(config.store_path, run_id, report.version)
    if file_hashes(files)[1] != report.files_hash:
        raise HTTPException(409, "Nội dung file đã lưu không khớp files_hash nên không duyệt được.")
    approved = _decide(report, "approved", user, body.comment, status="APPROVED", stage_reached="review")
    if not transition_run_report(config.store_path, approved, "READY_FOR_REVIEW", report.version, report.files_hash):
        raise HTTPException(409, "Run đã thay đổi kể từ lúc review. Tải lại trước khi duyệt.")
    return approved


def pipeline_github() -> GithubClient:
    """Client GitHub của repo pipeline (PIPELINE_GITHUB_*); tách thành dependency để test thay transport giả."""
    return GithubClient.from_settings()


def _record_deploy(store_path: str, signed: RunReport, change: Callable[[RunReport], RunReport]) -> RunReport | None:
    """Gộp kết quả Deploy vào bản đang lưu trong một giao dịch ghi: đọc lại, áp `change`, ghi.

    Không ghi lại bản đọc trước lúc gọi GitHub: request Deploy song song có thể đã lưu deployment hoặc error_trace
    trong lúc chờ (transition_run_report chỉ thay nguyên bản nên sẽ xóa mất phần đó). Trả None nếu run không còn
    APPROVED đúng version và files_hash đã ký.
    """
    with run_report_database(store_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, report FROM run_reports WHERE run_id = ?", (signed.run_id,)).fetchone()
        if row is None or row[0] != "APPROVED":
            return None
        try:
            current = RunReport.model_validate_json(row[1])
        except ValidationError:
            return None
        if current.version != signed.version or current.files_hash != signed.files_hash:
            return None
        updated = change(current)
        conn.execute(
            "UPDATE run_reports SET report = ?, updated_at = ? WHERE run_id = ? AND status = 'APPROVED'",
            (updated.model_dump_json(), datetime.now(UTC).isoformat(), signed.run_id),
        )
        return updated


def _merge_deployment(current: RunReport, opened: Deployment) -> dict:
    """Deployment cần lưu sau khi Deploy mở (hoặc tìm lại) pull request `opened`.

    Cùng pull request (repo, số PR, head sha) với bản đang lưu thì giữ bản đang lưu: webhook có thể đã ghi CI / merge
    trong lúc request này chờ GitHub, ghi đè bằng bản vừa mở sẽ làm mất tiến độ đó.
    """
    stored = current.deployment
    if stored is not None and (stored.repo.lower(), stored.pr_number, stored.head_sha) == (
        opened.repo.lower(),
        opened.pr_number,
        opened.head_sha,
    ):
        return stored.model_dump()
    return opened.model_dump()


@router.post("/{run_id}/deploy", response_model=RunReport)
def deploy_run(
    run_id: str,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
    github: GithubClient = Depends(pipeline_github),
    make_deps: Callable[[], Any] = Depends(rework_deps),
) -> RunReport:
    """P16: mở pull request chứa đúng bộ file đã duyệt vào repo pipeline (SPEC §9.5, FR-17).

    Không merge, không ghi Dagster; status giữ APPROVED đến khi deploy production xong. Gọi lại an toàn: pull request
    đã mở thì trả lại pull request đó (giữ CI / merge webhook đã ghi), đã merge thì trả run như đang lưu. Lỗi GitHub ghi error_trace stage deploy và giữ APPROVED để bấm Deploy lại.
    Sự kiện GitHub đến trong lúc chờ mở pull request (CI, đóng, merge) được webhook giữ lại và áp ngay sau khi lưu.
    """
    # Import muộn: codegen.deploy import run_report.models, import sớm ở đầu file sẽ thành vòng; webhooks import router.
    from src.api.webhooks import replay_pending
    from src.codegen.deploy import open_pull_request

    if not run_visible(user, config.store_path, run_id):
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    if user["role"] != "Reviewer":
        raise HTTPException(403, "Chỉ Reviewer được deploy run.")
    try:
        report = viewable_run(config.store_path, run_id, user)
    except ValidationError:
        # RunReport tự kiểm chữ ký: bản lưu có approval lệch files_hash / version thì không đọc được thành RunReport.
        raise HTTPException(
            409, "Chữ ký duyệt không khớp bộ file hoặc version hiện tại nên không deploy được."
        ) from None
    if taken := model_taken(config.store_path, report):
        raise HTTPException(409, f"Tên pipeline {taken} đã có người dùng khác dùng, hãy đổi tên model.")
    if report.status != "APPROVED":
        raise HTTPException(409, f"Run đang ở trạng thái {report.status}, chỉ run đã duyệt mới deploy được.")
    approval = report.approval
    if (
        approval is None
        or approval.decision != "approved"
        or not report.files_hash
        or approval.files_hash != report.files_hash
        or approval.version != report.version
        or not report.spec
    ):
        raise HTTPException(409, "Chữ ký duyệt không khớp bộ file hoặc version hiện tại nên không deploy được.")
    if report.deployment is not None and report.deployment.state == "merged":
        return report  # pull request đã merge, chờ deployer: không mở lại
    files = get_run_files(config.store_path, run_id, report.version)
    if not files or file_hashes(files)[1] != report.files_hash:
        raise HTTPException(409, "Nội dung file đã lưu không khớp files_hash nên không deploy được.")
    try:
        deployment = open_pull_request(report, files, github)
    except ManifestError as exc:
        raise HTTPException(409, str(exc)) from None
    except GithubError as exc:
        error = {"stage": "deploy", "error": f"Mở pull request thất bại: {exc.message}"}
        # Giữ nguyên deployment request khác có thể vừa lưu; chỉ thêm lỗi vào error_trace.
        _record_deploy(
            config.store_path,
            report,
            lambda current: current.model_copy(
                update={"stage_reached": "deploy", "error_trace": [*current.error_trace, error]}
            ),
        )
        raise HTTPException(exc.status_code, exc.message) from None
    opened = _record_deploy(
        config.store_path,
        report,
        lambda current: RunReport.model_validate(
            {**current.model_dump(), "stage_reached": "deploy", "deployment": _merge_deployment(current, deployment)}
        ),
    )
    if opened is None:
        raise HTTPException(
            409, f"Đã mở pull request {deployment.pr_url} nhưng run vừa thay đổi; tải lại run rồi bấm Deploy lại."
        )
    replayed, job = replay_pending(config.store_path, opened, make_deps)
    if job is not None:
        background.add_task(job)
    return replayed or opened


@router.post("/{run_id}/reject", response_model=RunReport)
def reject_run(
    run_id: str, body: ReviewRequest, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> RunReport:
    """Từ chối run; không ghi file nào ra Dagster."""
    report = _reviewable(run_id, body, user, config.store_path)
    rejected = _decide(report, "rejected", user, body.comment, status="REJECTED", stage_reached="review")
    if not transition_run_report(config.store_path, rejected, "READY_FOR_REVIEW", report.version, report.files_hash):
        raise HTTPException(409, "Run đã thay đổi kể từ lúc review. Tải lại trước khi từ chối.")
    return rejected


@router.post("/batch-review", response_model=BatchReviewResponse)
def batch_review_runs(
    body: BatchReviewRequest,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> BatchReviewResponse:
    """Duyệt hoặc từ chối tối đa 50 run trong một request; lỗi của một run không chặn các run còn lại."""
    # Vai xét sau quyền thấy: mục không được thấy báo không tìm thấy như run không tồn tại (xem _reviewable).
    if user["role"] != "Reviewer" and any(run_visible(user, config.store_path, item.run_id) for item in body.items):
        raise HTTPException(403, "Chỉ Reviewer được duyệt hoặc từ chối run.")
    results = []
    for item in body.items:
        request = ReviewRequest(files_hash=item.files_hash, comment=body.comment)
        try:
            report = (
                approve_run(item.run_id, request, user=user, config=config)
                if body.decision == "approve"
                else reject_run(item.run_id, request, user=user, config=config)
            )
            message = "Đã duyệt" if body.decision == "approve" else "Đã từ chối"
            results.append(BatchReviewResult(run_id=item.run_id, ok=True, status=report.status, message=message))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Thao tác thất bại"
            results.append(BatchReviewResult(run_id=item.run_id, ok=False, message=detail))
        except OSError as exc:
            results.append(BatchReviewResult(run_id=item.run_id, ok=False, message=str(exc)))
    return BatchReviewResponse(results=results)


@router.post("/{run_id}/request-changes", response_model=RunReport)
def request_changes(
    run_id: str,
    body: RequestChangesRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps=Depends(request_change_deps),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Reviewer yêu cầu sửa đúng bản đang xem; version mới chạy lại từ bước viết Spec với feedback."""
    report = viewable_run(config.store_path, run_id, user)
    if user["role"] != "Reviewer":
        raise HTTPException(403, "Chỉ Reviewer được yêu cầu sửa run.")
    if is_own_run(config.store_path, report, user):
        raise HTTPException(403, "Không được review run của mình.")
    if report.status != "READY_FOR_REVIEW":
        raise HTTPException(409, f"Run đang ở trạng thái {report.status}, chỉ run chờ duyệt mới yêu cầu sửa được.")
    if not report.files_hash or not report.spec:
        raise HTTPException(409, "Run không có Spec và bộ file để yêu cầu sửa.")
    if body.files_hash is not None and body.files_hash != report.files_hash:
        raise HTTPException(409, "Bộ file đã thay đổi kể từ lúc bạn xem. Tải lại run rồi review lại.")
    if body.version is not None and body.version != report.version:
        raise HTTPException(409, "Version đã thay đổi kể từ lúc bạn xem. Tải lại run rồi review lại.")
    from src.agents.service import RunChangedError, start_request_changes  # import muộn, xem request_change_deps

    try:
        started, job = start_request_changes(report, body.feedback, user["id"], deps, config.store_path)
    except RunChangedError as exc:
        raise HTTPException(409, str(exc)) from exc
    background.add_task(job)
    return started


@router.post("/{run_id}/cancel", response_model=RunReport)
def cancel_run(
    run_id: str,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Engineer hủy run của mình; job nền dừng hợp tác sau phase hiện tại và không thể ghi đè trạng thái này."""
    report = viewable_run(config.store_path, run_id, user)
    if user["role"] != "Engineer" or report.created_by != user["id"]:
        raise HTTPException(403, "Chỉ Engineer tạo run được hủy run của mình.")
    cancellable = {"DRAFT", "PLAN_CONFIRMED", "GENERATING", "VALIDATING"}
    if report.status not in cancellable:
        raise HTTPException(409, f"Run đang ở trạng thái {report.status}, không thể hủy.")
    event = {"event": "cancelled", "at": datetime.now(UTC).isoformat(), "actor": user["id"]}
    cancelled = RunReport.model_validate(
        {
            **report.model_dump(),
            "status": "CANCELLED",
            "timeline": [*(item.model_dump(mode="json") for item in report.timeline), event],
        }
    )
    save_run_report(config.store_path, cancelled)
    return cancelled
