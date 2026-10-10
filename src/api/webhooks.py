"""Webhook GitHub của repo pipeline và callback của deployer (SPEC §9.5 bước 3–5, §11.2 dòng P16).

Không dùng phiên đăng nhập: mọi request phải ký HMAC-SHA256 trên nguyên body. Chưa cấu hình secret → 503, thiếu chữ
ký → 401, cả hai trước khi đọc body; body lớn hơn MAX_BODY_BYTES → 413; chữ ký không khớp → 401 trước khi parse.

- `POST /webhooks/github` (X-Hub-Signature-256, PIPELINE_GITHUB_WEBHOOK_SECRET): pull request merge → deployment
  merged, status vẫn APPROVED chờ deployer; đóng không merge hoặc CI đỏ → version mới về P4 (vòng người); CI xanh chỉ
  ghi lại. Mỗi X-GitHub-Delivery chỉ áp một lần, ghi cùng giao dịch với thay đổi của run.
- `POST /deployments/callback` (X-Atomesus-Signature, PIPELINE_DEPLOYER_SECRET): deployer báo deployed / failed →
  DEPLOYED / DEPLOY_FAILED (hợp đồng chung trong kế hoạch DEPLOY-PR).

Secret không bao giờ ghi vào log, response hay RunReport.
"""

import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.concurrency import run_in_threadpool

from src.codegen.github import GithubSettings
from src.pipeline.auth import settings
from src.pipeline.settings import PipelineSettings
from src.run_report.models import RunReport
from src.run_report.store import get_run_report, list_run_reports, record_webhook_delivery, update_run_report

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])

MAX_BODY_BYTES = 1024 * 1024
SIGNATURE_PREFIX = "sha256="
PIPELINE_BRANCH_PREFIX = "pipeline/"
# Kết luận CI đưa run về P4 (SPEC §11.2); success chỉ ghi lại; neutral, skipped, action_required… bỏ qua.
CI_FAILED = frozenset({"failure", "cancelled", "timed_out"})
DETAIL_LIMIT = 2000
DELIVERY_LIMIT = 200


class DeployerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_DEPLOYER_", env_file=".env", extra="ignore")
    # Khóa HMAC dùng chung với service deployer; để trống thì /deployments/callback trả 503.
    secret: SecretStr = SecretStr("")


def github_settings() -> GithubSettings:
    return GithubSettings()


def deployer_settings() -> DeployerSettings:
    return DeployerSettings()


def rework_deps(config: PipelineSettings = Depends(settings)) -> Callable[[], Any]:
    """Tạo AgentDeps khi cần (chỉ sự kiện đưa run về P4); ping, CI xanh không nạp policy hay LLM."""
    from src.run_report.router import request_change_deps  # import muộn, xem request_change_deps

    return lambda: request_change_deps(config)


async def _signed_body(request: Request, secret: SecretStr, header: str, secret_name: str) -> bytes:
    """Body đã kiểm chữ ký `sha256=<hex HMAC-SHA256>` (so sánh constant-time)."""
    key = secret.get_secret_value()
    if not key.strip():
        raise HTTPException(503, f"Chưa cấu hình {secret_name} ở backend nên chưa nhận sự kiện này.")
    signature = request.headers.get(header, "")
    if not signature.startswith(SIGNATURE_PREFIX) or len(signature) != len(SIGNATURE_PREFIX) + 64:
        raise HTTPException(401, f"Thiếu chữ ký {header} hoặc chữ ký sai định dạng.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(413, "Nội dung lớn hơn 1 MB.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Nội dung lớn hơn 1 MB.")
    expected = SIGNATURE_PREFIX + hmac.new(key.encode(), bytes(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise HTTPException(401, f"Chữ ký {header} không khớp.")
    return bytes(body)


def _field(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


class _Signal(BaseModel):
    """Phần cần dùng của một sự kiện GitHub liên quan đến run."""

    kind: Literal["merged", "closed", "ci"]
    head_sha: str = Field(min_length=1)
    pr_number: int | None = None
    at: datetime
    actor: str
    merge_commit_sha: str | None = None
    conclusion: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _pull_request_has_number(self) -> "_Signal":
        if self.kind != "ci" and self.pr_number is None:
            raise ValueError("sự kiện pull_request phải có số pull request")
        return self


def _signal(event: str, payload: dict) -> _Signal | str:
    """Sự kiện làm đổi run → _Signal; còn lại → lý do bỏ qua. Dữ liệu sai kiểu ra ValidationError."""
    action = payload.get("action")
    actor = f"github:{_field(payload, 'sender', 'login') or 'unknown'}"
    now = datetime.now(UTC)
    if event == "pull_request":
        pull = payload.get("pull_request")
        if action != "closed":
            return f"pull_request {action} không làm đổi trạng thái run."
        if not str(_field(pull, "head", "ref") or "").startswith(PIPELINE_BRANCH_PREFIX):
            return "Pull request không đến từ nhánh pipeline/..."
        merged = _field(pull, "merged") is True
        return _Signal(
            kind="merged" if merged else "closed",
            head_sha=_field(pull, "head", "sha"),
            pr_number=_field(pull, "number"),
            at=_field(pull, "merged_at" if merged else "closed_at") or now,
            actor=actor,
            merge_commit_sha=_field(pull, "merge_commit_sha") if merged else None,
        )
    if event in ("check_suite", "workflow_run"):
        suite = payload.get(event)
        if action != "completed":
            return f"{event} {action} chưa có kết luận CI."
        if not str(_field(suite, "head_branch") or "").startswith(PIPELINE_BRANCH_PREFIX):
            return "CI không chạy trên nhánh pipeline/..."
        conclusion = _field(suite, "conclusion")
        if conclusion != "success" and conclusion not in CI_FAILED:
            return f"Kết luận CI {conclusion} không làm đổi trạng thái run."
        return _Signal(
            kind="ci",
            head_sha=_field(suite, "head_sha"),
            at=_field(suite, "updated_at") or now,
            actor=actor,
            conclusion=conclusion,
            url=_field(suite, "html_url"),
        )
    return f"Sự kiện {event or 'không rõ'} không liên quan đến run."


def _matches(report: RunReport, repo: str, signal: _Signal) -> bool:
    """Đúng pull request của bản đã duyệt: run APPROVED, cùng repo, cùng head sha (và cùng số PR nếu sự kiện có)."""
    deployment = report.deployment
    return (
        report.status == "APPROVED"
        and deployment is not None
        and deployment.repo.lower() == repo.lower()
        and deployment.head_sha == signal.head_sha
        and (signal.pr_number is None or deployment.pr_number == signal.pr_number)
    )


def _with_deployment(report: RunReport, **changes) -> RunReport:
    return RunReport.model_validate(
        {**report.model_dump(), "deployment": {**report.deployment.model_dump(), **changes}}
    )


def _rework_reason(report: RunReport, signal: _Signal) -> str:
    deployment = report.deployment
    if signal.kind == "closed":
        cause = f"Pull request #{deployment.pr_number} trên repo pipeline bị đóng mà không merge ({signal.actor})."
    else:
        link = f" ({signal.url})" if signal.url else ""
        cause = (
            f"CI của pull request #{deployment.pr_number} kết luận {signal.conclusion} cho commit "
            f"{signal.head_sha[:12]}{link}."
        )
    return (
        f"{cause} Chữ ký duyệt version {report.version} hết hiệu lực; sửa theo nhận xét trên pull request hoặc lỗi CI "
        "rồi gửi duyệt lại."
    )


def _handle_github(
    store_path: str, repo: str, delivery: str, event: str, signal: _Signal, make_deps: Callable[[], Any] | None
) -> tuple[str, RunReport | None, Callable | None]:
    """Áp một sự kiện vào run khớp, ghi cùng giao dịch với delivery. Trả (kết quả, run sau khi đổi, việc chạy nền)."""
    from src.agents.service import deploy_rework  # import muộn: src.agents import gói src.run_report

    found = next((r for r in list_run_reports(store_path, "APPROVED", limit=1000) if _matches(r, repo, signal)), None)
    jobs: list[Callable] = []

    def change(current: RunReport) -> RunReport | None:
        # Đọc lại trong giao dịch ghi: request khác có thể vừa đổi run sau lúc tìm.
        if not _matches(current, repo, signal):
            return None
        deployment = current.deployment
        if signal.kind == "ci":
            ci = {"ci_conclusion": signal.conclusion, "ci_url": signal.url, "ci_at": signal.at}
            if signal.conclusion in CI_FAILED and deployment.state == "open":
                marked = _with_deployment(current, **ci)
            elif deployment.ci_conclusion == signal.conclusion and deployment.ci_url == signal.url:
                return None
            else:
                return _with_deployment(current, **ci)
        elif deployment.state != "open":
            return None
        elif signal.kind == "merged":
            return _with_deployment(
                current, state="merged", merged_at=signal.at, merge_commit_sha=signal.merge_commit_sha
            )
        else:
            marked = _with_deployment(current, state="closed", closed_at=signal.at)
        started, job = deploy_rework(marked, _rework_reason(marked, signal), signal.actor, deps, store_path)
        jobs.append(job)
        return started

    # AgentDeps chỉ tạo khi sự kiện có thể đưa run về P4, và tạo trước giao dịch ghi để không giữ khóa store.
    reworks = signal.kind == "closed" or signal.conclusion in CI_FAILED
    deps = make_deps() if found and reworks and make_deps else None
    outcome, updated = record_webhook_delivery(
        store_path, delivery, event, found.run_id if found else None, change if found else None
    )
    return outcome, updated, jobs[0] if outcome == "applied" and jobs else None


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    github: GithubSettings = Depends(github_settings),
    config: PipelineSettings = Depends(settings),
    make_deps: Callable[[], Any] = Depends(rework_deps),
) -> dict:
    """Sự kiện của repo pipeline (SPEC §9.5, §15). 200: đã áp hoặc delivery lặp; 202: bỏ qua."""
    repo = github.repo.strip()
    if not repo:
        raise HTTPException(503, "Chưa cấu hình PIPELINE_GITHUB_REPO ở backend nên chưa nhận sự kiện GitHub.")
    body = await _signed_body(request, github.webhook_secret, "X-Hub-Signature-256", "PIPELINE_GITHUB_WEBHOOK_SECRET")
    event = request.headers.get("x-github-event", "")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Nội dung webhook không phải JSON.") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Nội dung webhook phải là một object JSON.")
    if str(_field(payload, "repository", "full_name") or "").lower() != repo.lower():
        response.status_code = 202
        return {"status": "ignored", "detail": "Sự kiện không thuộc repo pipeline đã cấu hình."}
    try:
        signal = _signal(event, payload)
    except ValidationError:
        raise HTTPException(400, f"Nội dung sự kiện {event} thiếu trường hoặc sai kiểu.") from None
    if isinstance(signal, str):
        response.status_code = 202
        return {"status": "ignored", "detail": signal}
    delivery = request.headers.get("x-github-delivery", "").strip()
    if not delivery or len(delivery) > DELIVERY_LIMIT:
        raise HTTPException(400, "Thiếu X-GitHub-Delivery hoặc giá trị không hợp lệ.")
    outcome, updated, job = await run_in_threadpool(
        _handle_github, config.store_path, repo, delivery, event, signal, make_deps
    )
    log.info("Webhook GitHub %s delivery %s: %s", event, delivery, outcome)
    if outcome == "duplicate":
        return {"status": "duplicate", "detail": "Delivery này đã được xử lý."}
    if outcome == "ignored":
        response.status_code = 202
        return {"status": "ignored", "detail": "Không có run đã duyệt nào khớp pull request / commit này."}
    if job is not None:
        background.add_task(job)
    return {"status": "applied", "run_id": updated.run_id, "version": updated.version, "run_status": updated.status}


class DeployCallback(BaseModel):
    """Kết quả cài đặt deployer gửi về (hợp đồng chung DEPLOY-WEBHOOK / DEPLOYER-PROD)."""

    model_config = ConfigDict(extra="ignore")
    run_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    files_hash: str = Field(min_length=1, max_length=200)
    model_name: str = Field(min_length=1, max_length=200)
    commit_sha: str = Field(max_length=100)
    status: Literal["deployed", "failed"]
    detail: str = ""
    at: datetime


def _redact(text: str, *secrets: SecretStr) -> str:
    for secret in secrets:
        if value := secret.get_secret_value().strip():
            text = text.replace(value, "***")
    return text


def _apply_callback(store_path: str, result: DeployCallback, secrets: tuple[SecretStr, ...]) -> RunReport:
    target = "DEPLOYED" if result.status == "deployed" else "DEPLOY_FAILED"
    seen: list[RunReport] = []

    def change(current: RunReport) -> RunReport | None:
        seen.append(current)
        approval = current.approval
        model_name = _field(current.spec, "transform", "model_name")
        if (
            approval is None
            or approval.decision != "approved"
            or (result.version, result.files_hash) != (current.version, current.files_hash)
            or (approval.version, approval.files_hash) != (current.version, current.files_hash)
            or (model_name and model_name != result.model_name)
        ):
            raise HTTPException(409, "run_id / version / files_hash / model_name không khớp chữ ký duyệt của run.")
        if current.status == target:
            return None  # deployer gọi lại cùng kết quả: idempotent
        if current.status != "APPROVED":
            raise HTTPException(
                409, f"Run đang ở trạng thái {current.status}, chỉ run APPROVED mới nhận kết quả deploy."
            )
        updates: dict[str, Any] = {"status": target, "stage_reached": "deploy"}
        if result.status == "deployed" and current.deployment is not None:
            updates["deployment"] = {
                **current.deployment.model_dump(),
                "deployed_commit_sha": result.commit_sha or None,
                "deployed_at": result.at,
            }
        if result.status == "failed":
            detail = _redact(result.detail, *secrets)[:DETAIL_LIMIT] or "không có chi tiết"
            commit = f" (commit {result.commit_sha[:12]})" if result.commit_sha else ""
            error = {"stage": "deploy", "error": f"Deployer cài đặt lên production thất bại{commit}: {detail}"}
            updates["error_trace"] = [*current.error_trace, error]
        return RunReport.model_validate({**current.model_dump(), **updates})

    updated = update_run_report(store_path, result.run_id, change)
    if updated is not None:
        return updated
    if not seen:
        raise HTTPException(409, f"Không tìm thấy run {result.run_id} (hoặc bản lưu không đọc được).")
    return get_run_report(store_path, result.run_id) or seen[-1]


@router.post("/deployments/callback", response_model=RunReport)
async def deployer_callback(
    request: Request,
    deployer: DeployerSettings = Depends(deployer_settings),
    github: GithubSettings = Depends(github_settings),
    config: PipelineSettings = Depends(settings),
) -> RunReport:
    """Deployer báo kết quả cài code location lên Dagster production: APPROVED → DEPLOYED / DEPLOY_FAILED."""
    body = await _signed_body(request, deployer.secret, "X-Atomesus-Signature", "PIPELINE_DEPLOYER_SECRET")
    try:
        result = DeployCallback.model_validate_json(body)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in error["loc"]) or "body" for error in exc.errors())
        raise HTTPException(422, f"Callback không đúng hợp đồng (trường lỗi: {fields}).") from None
    report = await run_in_threadpool(
        _apply_callback, config.store_path, result, (deployer.secret, github.webhook_secret, github.token)
    )
    log.info("Deployer báo %s cho run %s version %s", result.status, result.run_id, result.version)
    return report
