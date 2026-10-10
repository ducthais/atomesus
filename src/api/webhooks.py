"""Webhook GitHub của repo pipeline và callback của deployer (SPEC §9.5 bước 3–5, §11.2 dòng P16).

Không dùng phiên đăng nhập: mọi request phải ký HMAC-SHA256 trên nguyên body. Chưa cấu hình secret → 503, thiếu chữ
ký → 401, cả hai trước khi đọc body; body lớn hơn MAX_BODY_BYTES → 413; chữ ký không khớp → 401 trước khi parse.

- `POST /webhooks/github` (X-Hub-Signature-256, PIPELINE_GITHUB_WEBHOOK_SECRET): pull request merge → deployment
  merged, status vẫn APPROVED chờ deployer; đóng không merge hoặc CI đỏ (kể cả sau merge) → version mới về P4 (vòng
  người); CI xanh chỉ ghi lại. Mỗi X-GitHub-Delivery, kể cả sự kiện bỏ qua, chỉ xử lý một lần, ghi cùng giao dịch với
  thay đổi của run. Merge / CI đến sau callback deployer vẫn ghi vào deployment (giữ status). Sự kiện đến khi Deploy
  chưa lưu pull request được giữ lại và áp khi lưu (`replay_pending`).
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
from src.run_report.models import RunReport, RunStatus
from src.run_report.router import rework_deps
from src.run_report.store import (
    get_run_report,
    record_webhook_delivery,
    replay_webhook_deliveries,
    update_run_report,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])

MAX_BODY_BYTES = 1024 * 1024
SIGNATURE_PREFIX = "sha256="
PIPELINE_BRANCH_PREFIX = "pipeline/"
# Kết luận CI đưa run về P4 (SPEC §11.2); success chỉ ghi lại; neutral, skipped, action_required… bỏ qua.
CI_FAILED = frozenset({"failure", "cancelled", "timed_out"})
DETAIL_LIMIT = 2000
DELIVERY_LIMIT = 200
# Mã commit GitHub theo hợp đồng callback: đúng 40 ký tự hex.
GIT_SHA = r"^[0-9a-fA-F]{40}$"
DUPLICATE = {"status": "duplicate", "detail": "Delivery này đã được xử lý."}


class DeployerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_DEPLOYER_", env_file=".env", extra="ignore")
    # Khóa HMAC dùng chung với service deployer; để trống thì /deployments/callback trả 503.
    secret: SecretStr = SecretStr("")


def github_settings() -> GithubSettings:
    return GithubSettings()


def deployer_settings() -> DeployerSettings:
    return DeployerSettings()


async def _signed_body(request: Request, secret: SecretStr, header: str, secret_name: str) -> bytes:
    """Body đã kiểm chữ ký `sha256=<hex HMAC-SHA256>` (so sánh constant-time)."""
    key = secret.get_secret_value()
    if not key.strip():
        raise HTTPException(503, f"Chưa cấu hình {secret_name} ở backend nên chưa nhận sự kiện này.")
    signature = request.headers.get(header, "")
    digest = signature.removeprefix(SIGNATURE_PREFIX)
    if (
        not signature.startswith(SIGNATURE_PREFIX)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
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
    """Phần cần dùng của một sự kiện GitHub liên quan đến run (cũng là dạng lưu của sự kiện đang giữ chờ Deploy)."""

    kind: Literal["merged", "closed", "ci"]
    repo: str = Field(min_length=1, max_length=200)
    head_sha: str = Field(min_length=1, max_length=100)
    pr_number: int | None = None
    at: datetime
    actor: str = Field(max_length=200)
    merge_commit_sha: str | None = Field(default=None, max_length=100)
    conclusion: str | None = None
    url: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _pull_request_has_number(self) -> "_Signal":
        if self.kind != "ci" and self.pr_number is None:
            raise ValueError("sự kiện pull_request phải có số pull request")
        return self


def _signal(event: str, payload: dict, repo: str) -> _Signal | str:
    """Sự kiện làm đổi run → _Signal; còn lại → lý do bỏ qua. Dữ liệu sai kiểu ra ValidationError."""
    action = payload.get("action")
    actor = f"github:{_field(payload, 'sender', 'login') or 'unknown'}"
    now = datetime.now(UTC)
    if event == "pull_request":
        pull = payload.get("pull_request")
        if action != "closed":
            return "Sự kiện pull_request chưa đóng không làm đổi trạng thái run."
        if not str(_field(pull, "head", "ref") or "").startswith(PIPELINE_BRANCH_PREFIX):
            return "Pull request không đến từ nhánh pipeline/..."
        merged = _field(pull, "merged") is True
        return _Signal(
            kind="merged" if merged else "closed",
            repo=repo,
            head_sha=_field(pull, "head", "sha"),
            pr_number=_field(pull, "number"),
            at=_field(pull, "merged_at" if merged else "closed_at") or now,
            actor=actor,
            merge_commit_sha=_field(pull, "merge_commit_sha") if merged else None,
        )
    if event in ("check_suite", "workflow_run"):
        suite = payload.get(event)
        if action != "completed":
            return "Sự kiện CI chưa hoàn tất."
        if not str(_field(suite, "head_branch") or "").startswith(PIPELINE_BRANCH_PREFIX):
            return "CI không chạy trên nhánh pipeline/..."
        conclusion = _field(suite, "conclusion")
        if not isinstance(conclusion, str) or (conclusion != "success" and conclusion not in CI_FAILED):
            return "Kết luận CI không làm đổi trạng thái run."
        return _Signal(
            kind="ci",
            repo=repo,
            head_sha=_field(suite, "head_sha"),
            at=_field(suite, "updated_at") or now,
            actor=actor,
            conclusion=conclusion,
            url=_field(suite, "html_url"),
        )
    return "Sự kiện không liên quan đến run."


def _statuses(signal: _Signal) -> tuple[RunStatus, ...]:
    """Status run có thể nhận sự kiện. Deployer có thể báo kết quả trước khi webhook merge / CI đến, nên merge và CI
    vẫn ghi vào deployment của run DEPLOYED / DEPLOY_FAILED (giữ nguyên status); đóng không merge chỉ áp khi APPROVED."""
    return ("APPROVED",) if signal.kind == "closed" else ("APPROVED", "DEPLOYED", "DEPLOY_FAILED")


def _matches(report: RunReport, signal: _Signal) -> bool:
    """Đúng pull request của bản đã duyệt: status hợp lệ, cùng repo, cùng head sha (và cùng số PR nếu sự kiện có)."""
    deployment = report.deployment
    return (
        report.status in _statuses(signal)
        and deployment is not None
        and deployment.repo.lower() == signal.repo.lower()
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


def _change(
    signal: _Signal, make_deps: Callable[[], Any], store_path: str, jobs: list[Callable]
) -> Callable[[RunReport], RunReport | None]:
    """Thay đổi `signal` gây ra trên run đã khớp (`_matches`); None nếu không cần đổi. Việc chạy nền vào `jobs`."""
    from src.agents.service import deploy_rework  # import muộn: src.agents import gói src.run_report

    def change(current: RunReport) -> RunReport | None:
        deployment = current.deployment
        if signal.kind == "ci":
            ci = {"ci_conclusion": signal.conclusion, "ci_url": signal.url, "ci_at": signal.at}
            if signal.conclusion in CI_FAILED and current.status == "APPROVED":
                # CI đỏ về P4 kể cả khi webhook merge đến trước (run vẫn APPROVED chờ deployer).
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
        started, job = deploy_rework(marked, _rework_reason(marked, signal), signal.actor, make_deps, store_path)
        jobs.append(job)
        return started

    return change


def _handle_github(
    store_path: str, delivery: str, event: str, signal: _Signal, make_deps: Callable[[], Any]
) -> tuple[str, RunReport | None, Callable | None]:
    """Áp một sự kiện vào run khớp, tìm và ghi cùng giao dịch với delivery. Chưa có run khớp (Deploy chưa lưu pull
    request) thì giữ sự kiện để `replay_pending` áp sau. Trả (kết quả, run sau khi đổi, việc chạy nền)."""
    jobs: list[Callable] = []
    outcome, updated = record_webhook_delivery(
        store_path,
        delivery,
        event,
        _statuses(signal),
        signal.head_sha,
        lambda report: _matches(report, signal),
        _change(signal, make_deps, store_path, jobs),
        pending=signal.model_dump_json(),
    )
    return outcome, updated, jobs[0] if outcome == "applied" and jobs else None


def replay_pending(
    store_path: str, report: RunReport, make_deps: Callable[[], Any]
) -> tuple[RunReport | None, Callable | None]:
    """Gọi ngay sau khi Deploy lưu deployment: áp các sự kiện GitHub của pull request này đến trước lúc lưu (CI, đóng,
    merge), theo thứ tự nhận. Trả (run sau khi đổi hoặc None, việc chạy nền nếu run về P4)."""
    if report.deployment is None:
        return None, None
    jobs: list[Callable] = []

    def apply(current: RunReport, pending: str) -> tuple[bool, RunReport | None]:
        try:
            signal = _Signal.model_validate_json(pending)
        except ValidationError:
            return False, None
        if not _matches(current, signal):
            return False, None
        return True, _change(signal, make_deps, store_path, jobs)(current)

    updated = replay_webhook_deliveries(store_path, report.run_id, report.deployment.head_sha, apply)
    if updated is not None:
        log.info("Áp sự kiện GitHub đến trước Deploy: %s", updated.status)
    # Run về P4 thì không còn APPROVED nên sự kiện giữ sau đó không khớp nữa: nhiều nhất một job.
    return updated, jobs[0] if updated is not None and jobs else None


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    github: GithubSettings = Depends(github_settings),
    deployer: DeployerSettings = Depends(deployer_settings),
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
    delivery = request.headers.get("x-github-delivery", "").strip()
    if not delivery or len(delivery) > DELIVERY_LIMIT:
        raise HTTPException(400, "Thiếu X-GitHub-Delivery hoặc giá trị không hợp lệ.")
    secrets = (github.webhook_secret, deployer.secret, github.token)
    if _redact(event, *secrets) != event or _redact(delivery, *secrets) != delivery:
        raise HTTPException(400, "Header sự kiện hoặc delivery không hợp lệ.")
    if str(_field(payload, "repository", "full_name") or "").lower() != repo.lower():
        signal: _Signal | str = "Sự kiện không thuộc repo pipeline đã cấu hình."
    else:
        try:
            signal = _signal(event, payload, repo)
        except ValidationError:
            raise HTTPException(400, "Nội dung sự kiện thiếu trường hoặc sai kiểu.") from None
        if isinstance(signal, _Signal):
            # Không sửa định danh dùng để khớp run; actor / URL có thể mang chuỗi tùy ý, phải che trước khi lưu.
            if any(
                _redact(value, *secrets) != value
                for value in (signal.repo, signal.head_sha, signal.merge_commit_sha or "")
            ):
                raise HTTPException(400, "Định danh repo hoặc commit của sự kiện không hợp lệ.")
            signal = signal.model_copy(
                update={
                    "actor": _redact(signal.actor, *secrets),
                    "url": _redact(signal.url, *secrets) if signal.url else None,
                }
            )
    if isinstance(signal, str):
        # Sự kiện bỏ qua cũng ghi delivery: GitHub gửi lại thì trả "duplicate" như mọi delivery khác.
        outcome, _ = await run_in_threadpool(record_webhook_delivery, config.store_path, delivery, event)
        if outcome == "duplicate":
            return DUPLICATE
        response.status_code = 202
        return {"status": "ignored", "detail": signal}
    outcome, updated, job = await run_in_threadpool(
        _handle_github, config.store_path, delivery, event, signal, make_deps
    )
    log.info("Webhook GitHub %s delivery %s: %s", event, delivery, outcome)
    if outcome == "duplicate":
        return DUPLICATE
    if outcome == "ignored":
        response.status_code = 202
        return {"status": "ignored", "detail": "Run khớp pull request / commit này không cần thay đổi."}
    if outcome == "pending":
        response.status_code = 202
        return {
            "status": "ignored",
            "detail": "Chưa có run đã duyệt nào khớp pull request / commit này; sự kiện được giữ để áp khi Deploy "
            "lưu pull request.",
        }
    if job is not None:
        background.add_task(job)
    return {
        "status": "applied",
        "run_id": _redact(updated.run_id, *secrets),
        "version": updated.version,
        "run_status": updated.status,
    }


class DeployCallback(BaseModel):
    """Kết quả cài đặt deployer gửi về (hợp đồng chung DEPLOY-WEBHOOK / DEPLOYER-PROD)."""

    model_config = ConfigDict(extra="ignore")
    run_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    files_hash: str = Field(min_length=1, max_length=200)
    model_name: str = Field(min_length=1, max_length=200)
    commit_sha: str = Field(pattern=GIT_SHA)
    status: Literal["deployed", "failed"]
    detail: str = ""
    at: datetime


def _redact(text: str, *secrets: SecretStr) -> str:
    for secret in secrets:
        value = secret.get_secret_value()
        if value.strip():
            text = text.replace(value, "***")
    return text


def _contains_secret(value: Any, secrets: tuple[SecretStr, ...]) -> bool:
    if isinstance(value, str):
        return _redact(value, *secrets) != value
    if isinstance(value, dict):
        return any(_contains_secret(item, secrets) for pair in value.items() for item in pair)
    if isinstance(value, list):
        return any(_contains_secret(item, secrets) for item in value)
    return False


def _apply_callback(store_path: str, result: DeployCallback, secrets: tuple[SecretStr, ...]) -> RunReport:
    target = "DEPLOYED" if result.status == "deployed" else "DEPLOY_FAILED"
    seen: list[RunReport] = []

    def change(current: RunReport) -> RunReport | None:
        seen.append(current)
        if _contains_secret(current.model_dump(mode="json"), secrets):
            raise HTTPException(409, "Bản lưu run chứa dữ liệu nhạy cảm nên chưa nhận kết quả deploy.")
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
        # Không lặp lại run_id trong response: đó là chuỗi bên gửi tự đặt.
        raise HTTPException(409, "Không tìm thấy run trong callback (hoặc bản lưu không đọc được).")
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
    secrets = (deployer.secret, github.webhook_secret, github.token)
    if _redact(result.commit_sha, *secrets) != result.commit_sha or any(
        result.commit_sha in secret.get_secret_value() for secret in secrets
    ):
        # Không nhận cả phần mã commit bị cắt từ secret dạng hex.
        raise HTTPException(422, "Callback không đúng hợp đồng (trường lỗi: commit_sha).")
    if any(_redact(value, *secrets) != value for value in (result.run_id, result.files_hash, result.model_name)):
        raise HTTPException(409, "Định danh trong callback không khớp chữ ký duyệt của run.")
    report = await run_in_threadpool(_apply_callback, config.store_path, result, secrets)
    log.info("Deployer báo %s cho run %s version %s", result.status, result.run_id, result.version)
    return report
