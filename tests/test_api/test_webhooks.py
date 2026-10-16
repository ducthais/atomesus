"""POST /webhooks/github và /deployments/callback (SPEC §9.5 bước 3–5, §11.2 dòng P16). Offline: không gọi GitHub."""

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from src.api.webhooks import DeployerSettings, deployer_settings, github_settings, rework_deps
from src.codegen.github import GithubSettings
from src.main import app
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.run_report import FIXTURES_DIR, RunReport, save_run_report
from src.run_report.models import Deployment
from src.run_report.store import get_run_report, save_run_files

RUN_ID = "3f6c1a2e-8b4d-4c7a-9e21-0d5b7a1c9f10"
REPO = "acme/atomesus-pipelines"
SECRET = "webhook-secret-for-tests-0123456789"
DEPLOYER_SECRET = "deployer-secret-for-tests-0123456789"
TOKEN = "github_pat_test_token_0123456789"
HEAD = "a" * 40
BRANCH = f"pipeline/fct_daily_orders/{RUN_ID[:8]}-v3"
WEBHOOK = "/api/v1/webhooks/github"
CALLBACK = "/api/v1/deployments/callback"


def github_config(**changes) -> GithubSettings:
    values = {"repo": REPO, "webhook_secret": SecretStr(SECRET), "token": SecretStr(TOKEN), **changes}
    return GithubSettings(_env_file=None, **values)


@pytest.fixture
def store_path(tmp_path):
    """Sổ bằng chứng tạm nạp RunReport mẫu; secret giả; agent của vòng P4 dùng LLM và sandbox giả."""
    from tests.test_agents.test_graph import SPEC, FakeLLM, deps

    # Webhook GitHub chỉ có ở chế độ deploy pr.
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"), deploy_mode="pr")
    for path in FIXTURES_DIR.glob("*.json"):
        save_run_report(config.store_path, RunReport.model_validate_json(path.read_text(encoding="utf-8")))
    llm = FakeLLM(SPEC)
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[github_settings] = lambda: github_config()
    app.dependency_overrides[deployer_settings] = lambda: DeployerSettings(_env_file=None, secret=DEPLOYER_SECRET)
    app.dependency_overrides[rework_deps] = lambda: lambda: deps(llm)
    yield config.store_path
    for dependency in (auth.settings, github_settings, deployer_settings, rework_deps):
        app.dependency_overrides.pop(dependency, None)


@pytest.fixture
def approved(store_path) -> RunReport:
    """Run đã duyệt (version 3) có pull request #7 đang mở với head HEAD, như sau POST /runs/{id}/deploy."""
    from src.codegen.render import file_hashes, render
    from src.policy.loader import load_policy
    from src.spec import SPEC_FIXTURES_DIR, Spec
    from tests.test_agents.test_graph import PLAN

    spec = Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    files = render(spec, load_policy())
    hashes, files_hash = file_hashes(files)
    ready = get_run_report(store_path, RUN_ID)
    now = datetime.now(UTC)
    approval = {"decision": "approved", "reviewer": "an", "files_hash": files_hash, "version": 3, "at": now}
    report = RunReport.model_validate(
        {
            **ready.model_dump(),
            "plan": PLAN,
            "spec": spec.model_dump(by_alias=True, mode="json"),
            "files": hashes,
            "files_hash": files_hash,
            "status": "APPROVED",
            "stage_reached": "deploy",
            "attempts": {"spec": 2, "sql": 1},
            "approval": approval,
            "review_history": [approval],
            "deployment": {
                "repo": REPO,
                "branch": BRANCH,
                "pr_number": 7,
                "pr_url": f"https://github.com/{REPO}/pull/7",
                "head_sha": HEAD,
                "opened_at": now,
            },
        }
    )
    save_run_report(store_path, report)
    save_run_files(store_path, RUN_ID, report.version, files)
    return report


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def send(client, event: str, payload: dict, delivery: str = "delivery-1", signature: str | None = None):
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sign(body) if signature is None else signature,
    }
    response = await client.post(WEBHOOK, content=body, headers=headers)
    assert SECRET not in response.text and TOKEN not in response.text
    return response


def pull_closed(merged: bool, sha: str = HEAD, number: int = 7, ref: str = BRANCH, repo: str = REPO) -> dict:
    return {
        "action": "closed",
        "repository": {"full_name": repo},
        "sender": {"login": "octo"},
        "pull_request": {
            "number": number,
            "merged": merged,
            "head": {"ref": ref, "sha": sha},
            "merged_at": "2026-10-10T10:00:00Z" if merged else None,
            "closed_at": "2026-10-10T10:00:00Z",
            "merge_commit_sha": "m" * 40 if merged else None,
        },
    }


def ci(event: str, conclusion: str, sha: str = HEAD, branch: str = BRANCH) -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": REPO},
        "sender": {"login": "github-actions[bot]"},
        event: {
            "head_sha": sha,
            "head_branch": branch,
            "conclusion": conclusion,
            "html_url": f"https://github.com/{REPO}/actions/runs/42",
            "updated_at": "2026-10-10T10:05:00Z",
        },
    }


def stored(store_path) -> str:
    return get_run_report(store_path, RUN_ID).model_dump_json()


# ---- Chữ ký, cấu hình, kích thước ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_without_configured_secret_returns_503(client, approved):
    app.dependency_overrides[github_settings] = lambda: github_config(webhook_secret=SecretStr(""))

    response = await send(client, "pull_request", pull_closed(merged=True))

    assert response.status_code == 503
    assert "PIPELINE_GITHUB_WEBHOOK_SECRET" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signature",
    ["", "sha1=" + "0" * 40, "sha256=" + "0" * 64, sign(b"other body"), sign(b"x", "wrong-secret")],
    ids=["missing", "wrong-algorithm", "zeros", "other-body", "wrong-secret"],
)
async def test_webhook_rejects_missing_or_wrong_signature(client, approved, store_path, signature):
    before = stored(store_path)

    response = await send(client, "pull_request", pull_closed(merged=True), signature=signature)

    assert response.status_code == 401, response.text
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_webhook_body_over_one_megabyte_returns_413(client, approved, store_path):
    body = b'{"pad": "' + b"x" * (1024 * 1024) + b'"}'

    response = await client.post(
        WEBHOOK,
        content=body,
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "big", "X-Hub-Signature-256": sign(body)},
    )

    assert response.status_code == 413


# ---- Pull request merge / đóng ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merged_pull_request_marks_deployment_merged_and_keeps_approved(client, approved, store_path):
    response = await send(client, "pull_request", pull_closed(merged=True))

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "applied", "run_id": RUN_ID, "version": 3, "run_status": "APPROVED"}
    report = get_run_report(store_path, RUN_ID)
    assert report.status == "APPROVED" and report.version == 3 and report.approval == approved.approval
    assert report.deployment.state == "merged"
    assert report.deployment.merged_at == datetime(2026, 10, 10, 10, tzinfo=UTC)
    assert report.deployment.merge_commit_sha == "m" * 40


@pytest.mark.asyncio
async def test_same_delivery_twice_changes_nothing(client, approved, store_path):
    first = await send(client, "pull_request", pull_closed(merged=True), delivery="same")
    after_first = stored(store_path)

    again = await send(client, "pull_request", pull_closed(merged=False), delivery="same")

    assert first.status_code == 200 and first.json()["status"] == "applied"
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    assert stored(store_path) == after_first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pull_closed(merged=True, sha="b" * 40),
        pull_closed(merged=True, number=8),
        pull_closed(merged=True, repo="someone/other-repo"),
    ],
    ids=["other-head-sha", "other-pr-number", "other-repo"],
)
async def test_event_for_another_pull_request_is_ignored(client, approved, store_path, payload):
    before = stored(store_path)

    response = await send(client, "pull_request", payload)

    assert response.status_code == 202 and response.json()["status"] == "ignored"
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_closed_without_merge_starts_new_version_from_p4(client, approved, store_path):
    response = await send(client, "pull_request", pull_closed(merged=False))

    assert response.status_code == 200, response.text
    assert response.json()["run_status"] == "GENERATING" and response.json()["version"] == 4
    revised = get_run_report(store_path, RUN_ID)
    assert revised.version == 4 and revised.status == "READY_FOR_REVIEW", revised.error_trace
    assert revised.approval is None and revised.deployment is None, "chữ ký và pull request cũ không còn hiệu lực"
    assert approved.approval in revised.review_history
    entry = next(item for item in revised.spec_history if "deployment" in item)
    assert entry["version"] == 3
    assert "#7" in entry["reason"] and "đóng mà không merge" in entry["reason"]
    assert entry["deployment"]["state"] == "closed" and entry["deployment"]["closed_at"]
    assert any(e.event == "changes_requested" and e.actor == "github:octo" for e in revised.timeline)


@pytest.mark.asyncio
async def test_closed_after_merge_does_not_reopen_the_loop(client, approved, store_path):
    await send(client, "pull_request", pull_closed(merged=True), delivery="merge")
    after_merge = stored(store_path)

    response = await send(client, "pull_request", pull_closed(merged=False), delivery="close")

    assert response.status_code == 202
    assert stored(store_path) == after_merge


# ---- CI ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "conclusion"),
    [("workflow_run", "failure"), ("check_suite", "timed_out"), ("check_suite", "cancelled")],
)
async def test_red_ci_starts_new_version_from_p4(client, approved, store_path, event, conclusion):
    response = await send(client, event, ci(event, conclusion))

    assert response.status_code == 200, response.text
    assert response.json()["run_status"] == "GENERATING"
    revised = get_run_report(store_path, RUN_ID)
    assert revised.version == 4 and revised.approval is None
    entry = next(item for item in revised.spec_history if "deployment" in item)
    assert entry["version"] == 3
    assert conclusion in entry["reason"] and HEAD[:12] in entry["reason"]
    assert entry["deployment"]["ci_conclusion"] == conclusion and entry["deployment"]["state"] == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["workflow_run", "check_suite"])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
@pytest.mark.parametrize("result", ["deployed", "failed"])
async def test_red_ci_after_merge_only_records_ci_and_leaves_status_to_deployer(
    client, approved, store_path, event, conclusion, result
):
    await send(client, "pull_request", pull_closed(merged=True), delivery="merge")

    response = await send(client, event, ci(event, conclusion), delivery="ci")

    assert response.status_code == 200, response.text
    assert response.json()["run_status"] == "APPROVED" and response.json()["version"] == 3
    report = get_run_report(store_path, RUN_ID)
    assert report.version == approved.version and report.approval == approved.approval
    assert report.spec_history == approved.spec_history and report.timeline == approved.timeline
    assert report.deployment.state == "merged" and report.deployment.ci_conclusion == conclusion
    assert report.deployment.ci_url.endswith("/actions/runs/42") and report.deployment.ci_at

    response = await callback(
        client, callback_body(approved, result, detail="Chạy thử Dagster FAILURE; đã quay về bản trước.")
    )

    assert response.status_code == 200
    report = get_run_report(store_path, RUN_ID)
    assert report.status == ("DEPLOYED" if result == "deployed" else "DEPLOY_FAILED")
    assert report.deployment.ci_conclusion == conclusion and report.version == 3


@pytest.mark.asyncio
async def test_run_behind_more_than_a_thousand_approved_runs_still_matches(client, approved, store_path):
    """Run khớp nằm sau hơn 1000 run APPROVED mới hơn cùng head sha (khác số PR): vẫn tìm ra, không bị cắt lịch sử."""
    from src.run_report.store import _database

    newer = approved.created_at + timedelta(days=1)
    rows = []
    for index in range(1001):
        other = approved.model_copy(
            update={
                "run_id": f"other-{index}",
                "created_at": newer,
                "deployment": approved.deployment.model_copy(update={"pr_number": 100 + index}),
            }
        )
        rows.append((other.run_id, "APPROVED", newer.isoformat(), other.model_dump_json(), newer.isoformat()))
    with _database(store_path) as conn:
        conn.executemany("INSERT INTO run_reports VALUES (?, ?, ?, ?, ?)", rows)

    response = await send(client, "pull_request", pull_closed(merged=True))

    assert response.status_code == 200 and response.json()["run_id"] == RUN_ID, response.text
    assert get_run_report(store_path, RUN_ID).deployment.state == "merged"


@pytest.mark.asyncio
async def test_green_ci_only_records_the_conclusion(client, approved, store_path):
    response = await send(client, "check_suite", ci("check_suite", "success"))

    assert response.status_code == 200, response.text
    report = get_run_report(store_path, RUN_ID)
    assert report.status == "APPROVED" and report.version == 3
    assert report.deployment.ci_conclusion == "success" and report.deployment.state == "open"
    assert report.deployment.ci_url.endswith("/actions/runs/42")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("ping", {"zen": "Keep it simple.", "repository": {"full_name": REPO}}),
        ("push", {"ref": "refs/heads/main", "repository": {"full_name": REPO}}),
        ("pull_request", {**pull_closed(merged=False), "action": "opened"}),
        ("pull_request", pull_closed(merged=False, ref="feature/x")),
        ("check_suite", ci("check_suite", "failure", branch="main")),
        ("check_suite", {**ci("check_suite", "success"), "action": "requested"}),
        ("workflow_run", ci("workflow_run", "skipped")),
    ],
    ids=["ping", "push", "pr-opened", "not-pipeline-branch", "ci-on-main", "ci-requested", "ci-skipped"],
)
async def test_unrelated_events_return_202_and_change_nothing(client, approved, store_path, event, payload):
    before = stored(store_path)

    response = await send(client, event, payload)

    assert response.status_code == 202 and response.json()["status"] == "ignored"
    assert stored(store_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("ping", {"zen": "Keep it simple.", "repository": {"full_name": REPO}}),
        ("pull_request", pull_closed(merged=True, repo="someone/other-repo")),
    ],
    ids=["ping", "other-repo"],
)
async def test_ignored_delivery_sent_again_is_duplicate(client, approved, store_path, event, payload):
    before = stored(store_path)

    first = await send(client, event, payload, delivery="ignored-1")
    again = await send(client, event, payload, delivery="ignored-1")

    assert first.status_code == 202 and first.json()["status"] == "ignored"
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_relevant_event_without_delivery_id_is_rejected(client, approved):
    body = json.dumps(pull_closed(merged=True)).encode()

    response = await client.post(
        WEBHOOK, content=body, headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body)}
    )

    assert response.status_code == 400


# ---- Callback deployer --------------------------------------------------------------------------------------------


def callback_body(report: RunReport, status: str = "deployed", **changes) -> dict:
    return {
        "run_id": report.run_id,
        "version": report.version,
        "files_hash": report.files_hash,
        "model_name": "fct_daily_orders",
        "commit_sha": "c" * 40,
        "status": status,
        "detail": "",
        "at": "2026-10-10T10:10:00Z",
        **changes,
    }


async def callback(
    client,
    payload: dict,
    signature: str | None = None,
    timestamp: str | None = None,
    include_timestamp: bool = True,
):
    body = json.dumps(payload).encode()
    timestamp = timestamp or str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Atomesus-Signature": (
            sign(timestamp.encode() + b"." + body, DEPLOYER_SECRET) if signature is None else signature
        ),
    }
    if include_timestamp:
        headers["X-Atomesus-Timestamp"] = timestamp
    response = await client.post(CALLBACK, content=body, headers=headers)
    assert DEPLOYER_SECRET not in response.text and TOKEN not in response.text
    return response


@pytest.mark.asyncio
async def test_callback_deployed_moves_run_to_deployed_and_exact_signed_replay_is_idempotent(
    client, approved, store_path
):
    await send(client, "pull_request", pull_closed(merged=True))

    body = json.dumps(callback_body(approved)).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Atomesus-Timestamp": timestamp,
        "X-Atomesus-Signature": sign(timestamp.encode() + b"." + body, DEPLOYER_SECRET),
    }

    response = await client.post(CALLBACK, content=body, headers=headers)
    after_first = stored(store_path)
    again = await client.post(CALLBACK, content=body, headers=headers)

    assert response.status_code == 200, response.text
    assert DEPLOYER_SECRET not in response.text and DEPLOYER_SECRET not in again.text
    report = get_run_report(store_path, RUN_ID)
    assert report.status == "DEPLOYED" and report.stage_reached == "deploy"
    assert report.deployment.state == "merged", "callback không ghi đè thay đổi của webhook merge"
    assert report.deployment.deployed_commit_sha == "c" * 40 and report.deployment.deployed_at
    assert again.status_code == 200 and again.json()["status"] == "DEPLOYED"
    assert stored(store_path) == after_first


@pytest.mark.asyncio
async def test_callback_failed_records_detail_in_error_trace(client, approved, store_path):
    detail = f"dagster reload lỗi: token={DEPLOYER_SECRET}"

    response = await callback(client, callback_body(approved, "failed", detail=detail))
    after_first = stored(store_path)
    again = await callback(client, callback_body(approved, "failed", detail=detail))

    assert response.status_code == 200, response.text
    report = get_run_report(store_path, RUN_ID)
    assert report.status == "DEPLOY_FAILED"
    error = report.error_trace[-1]
    assert error["stage"] == "deploy" and "dagster reload lỗi" in error["error"] and "c" * 12 in error["error"]
    assert DEPLOYER_SECRET not in report.model_dump_json()
    assert again.status_code == 200 and stored(store_path) == after_first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"files_hash": "sha256:" + "0" * 64}, {"version": 2}, {"model_name": "fct_other"}, {"run_id": "missing"}],
    ids=["files-hash", "version", "model-name", "unknown-run"],
)
async def test_callback_not_matching_signature_returns_409(client, approved, store_path, changes):
    before = stored(store_path)

    response = await callback(client, callback_body(approved, **changes))

    assert response.status_code == 409
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_callback_conflicting_result_or_unapproved_run_returns_409(client, approved, store_path):
    assert (await callback(client, callback_body(approved))).status_code == 200

    conflicting = await callback(client, callback_body(approved, "failed", detail="x"))

    assert conflicting.status_code == 409
    assert get_run_report(store_path, RUN_ID).status == "DEPLOYED"


@pytest.mark.asyncio
async def test_callback_for_run_waiting_review_returns_409(client, store_path):
    ready = get_run_report(store_path, RUN_ID)

    response = await callback(client, callback_body(ready))

    assert response.status_code == 409
    assert get_run_report(store_path, RUN_ID).status == "READY_FOR_REVIEW"


@pytest.mark.asyncio
@pytest.mark.parametrize("signature", ["", "sha256=" + "0" * 64, sign(b"{}", DEPLOYER_SECRET), sign(b"{}", SECRET)])
async def test_callback_rejects_missing_or_wrong_signature(client, approved, store_path, signature):
    before = stored(store_path)

    response = await callback(client, callback_body(approved), signature=signature)

    assert response.status_code == 401
    assert stored(store_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timestamp", "include_timestamp"),
    [(None, False), ("not-a-timestamp", True), (str(int(time.time()) - 301), True)],
    ids=["missing", "malformed", "expired"],
)
async def test_callback_rejects_missing_invalid_or_expired_timestamp(
    client, approved, store_path, timestamp, include_timestamp
):
    before = stored(store_path)

    response = await callback(
        client,
        callback_body(approved),
        timestamp=timestamp,
        include_timestamp=include_timestamp,
    )

    assert response.status_code == 401
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_callback_signature_binds_timestamp(client, approved, store_path):
    before = stored(store_path)
    signed_at = str(int(time.time()))
    body = json.dumps(callback_body(approved)).encode()
    signature = sign(signed_at.encode() + b"." + body, DEPLOYER_SECRET)

    response = await callback(client, callback_body(approved), signature=signature, timestamp=str(int(signed_at) + 1))

    assert response.status_code == 401
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_callback_without_configured_secret_returns_503(client, approved):
    app.dependency_overrides[deployer_settings] = lambda: DeployerSettings(_env_file=None, secret="")

    response = await callback(client, callback_body(approved))

    assert response.status_code == 503 and "PIPELINE_DEPLOYER_SECRET" in response.json()["detail"]


@pytest.mark.asyncio
async def test_callback_with_malformed_body_returns_422(client, approved):
    response = await callback(client, {**callback_body(approved), "status": "maybe"})

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "target"), [("deployed", "DEPLOYED"), ("failed", "DEPLOY_FAILED")])
async def test_merge_arriving_after_callback_still_records_merge(client, approved, store_path, status, target):
    """Deployer báo kết quả trước khi GitHub gửi webhook merge: merge vẫn ghi vào deployment, status giữ nguyên."""
    assert (await callback(client, callback_body(approved, status, detail="x"))).status_code == 200

    response = await send(client, "pull_request", pull_closed(merged=True))
    green = await send(client, "check_suite", ci("check_suite", "success"), delivery="ci-after-callback")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "applied", "run_id": RUN_ID, "version": 3, "run_status": target}
    assert green.status_code == 200 and green.json()["run_status"] == target
    report = get_run_report(store_path, RUN_ID)
    assert report.status == target and report.version == 3 and report.approval == approved.approval
    assert report.deployment.state == "merged" and report.deployment.merge_commit_sha == "m" * 40
    assert report.deployment.merged_at == datetime(2026, 10, 10, 10, tzinfo=UTC)
    assert report.deployment.ci_conclusion == "success"
    if status == "deployed":
        assert report.deployment.deployed_commit_sha == "c" * 40, "merge không xóa kết quả deployer đã ghi"


@pytest.mark.asyncio
async def test_red_ci_or_close_after_callback_does_not_reopen_the_loop(client, approved, store_path):
    assert (await callback(client, callback_body(approved))).status_code == 200

    closed = await send(client, "pull_request", pull_closed(merged=False), delivery="close")
    red = await send(client, "workflow_run", ci("workflow_run", "failure"), delivery="red")

    assert closed.status_code == 202
    assert red.status_code == 200 and red.json()["run_status"] == "DEPLOYED"
    report = get_run_report(store_path, RUN_ID)
    assert report.status == "DEPLOYED" and report.version == 3 and report.deployment.ci_conclusion == "failure"


HEX_SECRET = "ab" * 32  # secret sinh bằng `openssl rand -hex 32` có đúng dạng mã commit SHA-256


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deployed", "failed"])
@pytest.mark.parametrize(
    ("commit_sha", "configured"),
    [
        (SECRET, DEPLOYER_SECRET),
        (HEX_SECRET, HEX_SECRET),
        (HEX_SECRET[:40], HEX_SECRET),
        ("not a sha", DEPLOYER_SECRET),
    ],
    ids=["webhook-secret", "hex-deployer-secret", "secret-prefix", "free-text"],
)
async def test_callback_commit_sha_never_carries_a_secret(client, approved, store_path, status, commit_sha, configured):
    app.dependency_overrides[deployer_settings] = lambda: DeployerSettings(_env_file=None, secret=configured)
    before = stored(store_path)
    body = json.dumps(callback_body(approved, status, commit_sha=commit_sha, detail="x")).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Atomesus-Timestamp": timestamp,
        "X-Atomesus-Signature": sign(timestamp.encode() + b"." + body, configured),
    }

    response = await client.post(CALLBACK, content=body, headers=headers)

    assert response.status_code == 422 and "commit_sha" in response.json()["detail"]
    for secret in (SECRET, configured, commit_sha):
        assert secret not in response.text
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_callback_for_unknown_run_does_not_echo_run_id(client, approved):
    response = await callback(client, callback_body(approved, run_id=SECRET))

    assert response.status_code == 409 and SECRET not in response.text


# ---- Sự kiện đến trước khi Deploy lưu pull request ---------------------------------------------------------------


def deliver_now(event: str, payload: dict, delivery: str):
    """Gửi webhook qua HTTP ngay lúc đang chạy request Deploy (client đồng bộ riêng, như GitHub gọi song song)."""
    from fastapi.testclient import TestClient

    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sign(body)}
    return TestClient(app).post(WEBHOOK, content=body, headers=headers)


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("workflow_run", lambda sha: ci("workflow_run", "failure", sha=sha)),
        ("pull_request", lambda sha: pull_closed(merged=False, sha=sha, number=1)),
    ],
    ids=["red-ci", "closed"],
)
def test_event_during_initial_deploy_is_applied_once_deployment_is_saved(approved, store_path, event, payload):
    """GitHub báo CI đỏ / PR bị đóng khi Deploy đã mở PR nhưng chưa lưu deployment: sự kiện được giữ, áp khi lưu."""
    from fastapi import BackgroundTasks

    from src.run_report.router import deploy_run
    from tests.test_agents.test_graph import SPEC, FakeLLM, deps
    from tests.test_codegen.test_deploy_pr import FakeGithub

    save_run_report(store_path, approved.model_copy(update={"deployment": None}))
    github = FakeGithub()
    early = []
    github.before = {"POST /pulls": lambda: early.append(deliver_now(event, payload(github.refs[BRANCH]), "early"))}
    background = BackgroundTasks()
    config = app.dependency_overrides[auth.settings]()

    deployed = deploy_run(
        RUN_ID,
        background,
        user={"id": "an", "role": "Reviewer"},
        config=config,
        github=github.client(),
        make_deps=lambda: deps(FakeLLM(SPEC)),
    )

    assert early[0].status_code == 202, "lúc đó chưa có deployment khớp: giữ lại, chưa áp"
    assert deployed.version == 4 and deployed.status == "GENERATING" and deployed.approval is None
    assert len(background.tasks) == 1
    for task in background.tasks:
        task.func(*task.args, **task.kwargs)
    revised = get_run_report(store_path, RUN_ID)
    assert revised.version == 4 and revised.status == "READY_FOR_REVIEW", revised.error_trace
    entry = next(item for item in revised.spec_history if "deployment" in item)
    assert entry["version"] == 3 and entry["deployment"]["pr_number"] == 1
    if event == "pull_request":
        assert entry["deployment"]["state"] == "closed"
    else:
        assert entry["deployment"]["ci_conclusion"] == "failure"
    again = deliver_now(event, payload(github.refs[BRANCH]), "early")
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    assert get_run_report(store_path, RUN_ID).model_dump_json() == revised.model_dump_json()


def test_merge_during_initial_deploy_is_applied_once_deployment_is_saved(approved, store_path):
    from fastapi import BackgroundTasks

    from src.run_report.router import deploy_run
    from tests.test_codegen.test_deploy_pr import FakeGithub

    save_run_report(store_path, approved.model_copy(update={"deployment": None}))
    github = FakeGithub()
    merged = lambda: pull_closed(merged=True, sha=github.refs[BRANCH], number=1)  # noqa: E731
    github.before = {"POST /pulls": lambda: deliver_now("pull_request", merged(), "early-merge")}
    background = BackgroundTasks()

    report = deploy_run(
        RUN_ID,
        background,
        user={"id": "an", "role": "Reviewer"},
        config=app.dependency_overrides[auth.settings](),
        github=github.client(),
        make_deps=lambda: pytest.fail("merge không cần agent"),
    )

    assert report.status == "APPROVED" and report.deployment.state == "merged" and not background.tasks
    assert get_run_report(store_path, RUN_ID).deployment.merge_commit_sha == "m" * 40


def test_pending_event_for_another_pull_request_stays_unapplied(approved, store_path):
    """Sự kiện giữ lại của PR khác (số PR khác) không áp vào deployment vừa lưu."""
    from fastapi import BackgroundTasks

    from src.run_report.router import deploy_run
    from tests.test_codegen.test_deploy_pr import FakeGithub

    save_run_report(store_path, approved.model_copy(update={"deployment": None}))
    github = FakeGithub()
    other = lambda: pull_closed(merged=False, sha=github.refs[BRANCH], number=99)  # noqa: E731
    github.before = {"POST /pulls": lambda: deliver_now("pull_request", other(), "other-pr")}

    report = deploy_run(
        RUN_ID,
        BackgroundTasks(),
        user={"id": "an", "role": "Reviewer"},
        config=app.dependency_overrides[auth.settings](),
        github=github.client(),
        make_deps=lambda: pytest.fail("không được về P4"),
    )

    assert report.status == "APPROVED" and report.version == 3 and report.deployment.state == "open"


# ---- Secret, dữ liệu cũ, vòng người ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secrets_never_reach_logs(client, approved, caplog):
    caplog.set_level(logging.DEBUG)

    await send(client, "pull_request", pull_closed(merged=True), delivery="log-1")
    await send(client, "pull_request", pull_closed(merged=True), delivery="log-2", signature="sha256=" + "1" * 64)
    await callback(client, callback_body(approved))

    for secret in (SECRET, DEPLOYER_SECRET, TOKEN):
        assert secret not in caplog.text


def test_old_deployment_without_ci_fields_still_reads():
    old = {
        "repo": REPO,
        "branch": BRANCH,
        "pr_number": 7,
        "pr_url": f"https://github.com/{REPO}/pull/7",
        "head_sha": HEAD,
        "state": "open",
        "opened_at": "2026-10-10T09:00:00Z",
    }

    deployment = Deployment.model_validate(old)

    assert deployment.ci_conclusion is None and deployment.merged_at is None and deployment.deployed_at is None
    assert deployment.model_dump(mode="json").keys() == old.keys(), "chưa có tiến độ thì xuất đúng dạng cũ"


def test_openapi_lists_routes_and_new_deployment_fields():
    schema = app.openapi()

    assert {WEBHOOK, CALLBACK} <= set(schema["paths"])
    deployment = [body for name, body in schema["components"]["schemas"].items() if name.startswith("Deployment")]
    assert deployment and all({"ci_conclusion", "merged_at", "deployed_at"} <= set(d["properties"]) for d in deployment)


def test_deploy_rework_resets_attempts_and_drops_signature_without_writing(approved, store_path):
    from src.agents.service import deploy_rework
    from tests.test_agents.test_graph import SPEC, FakeLLM, deps

    before = stored(store_path)

    started, _job = deploy_rework(approved, "CI đỏ", "github:octo", lambda: deps(FakeLLM(SPEC)), store_path)

    assert started.version == 4 and started.status == "GENERATING" and started.stage_reached == "propose"
    assert started.attempts.spec == 0 and started.approval is None and started.files_hash is None
    assert started.spec_history[-1]["reason"] == "CI đỏ"
    assert stored(store_path) == before, "webhook tự ghi started cùng giao dịch với delivery"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deployed", "failed"])
@pytest.mark.parametrize("order", ["merge-first", "callback-first", "concurrent"])
async def test_merge_and_callback_preserve_both_results(client, approved, store_path, status, order):
    import asyncio

    async def merge():
        return await send(client, "pull_request", pull_closed(merged=True))

    async def deploy():
        return await callback(client, callback_body(approved, status, detail="reload lỗi"))

    if order == "concurrent":
        responses = await asyncio.gather(merge(), deploy())
    elif order == "merge-first":
        responses = [await merge(), await deploy()]
    else:
        responses = [await deploy(), await merge()]

    assert all(response.status_code == 200 for response in responses)
    report = get_run_report(store_path, RUN_ID)
    assert report.status == ("DEPLOYED" if status == "deployed" else "DEPLOY_FAILED")
    assert report.version == approved.version and report.approval == approved.approval
    assert report.deployment.state == "merged" and report.deployment.merged_at
    assert report.deployment.merge_commit_sha == "m" * 40
    if status == "failed":
        assert report.error_trace[-1]["stage"] == "deploy"
    else:
        assert report.deployment.deployed_commit_sha == "c" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deployed", "failed"])
@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "timed_out"])
async def test_ci_after_callback_preserves_status(client, approved, store_path, status, conclusion):
    assert (await callback(client, callback_body(approved, status))).status_code == 200

    response = await send(client, "workflow_run", ci("workflow_run", conclusion))

    assert response.status_code == 200
    report = get_run_report(store_path, RUN_ID)
    assert report.status == ("DEPLOYED" if status == "deployed" else "DEPLOY_FAILED")
    assert report.version == approved.version and report.approval == approved.approval
    assert report.deployment.ci_conclusion == conclusion


@pytest.mark.asyncio
async def test_concurrent_duplicate_delivery_applies_once(client, approved, store_path):
    import asyncio

    responses = await asyncio.gather(
        send(client, "pull_request", pull_closed(merged=True)),
        send(client, "pull_request", pull_closed(merged=True)),
    )

    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["status"] for response in responses) == ["applied", "duplicate"]
    assert get_run_report(store_path, RUN_ID).deployment.state == "merged"


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_sha", ["", "a" * 39, "a" * 41, "a" * 64, "g" * 40, "a" * 40 + "\n"])
async def test_callback_requires_exactly_forty_hex_characters(client, approved, store_path, commit_sha):
    before = stored(store_path)

    response = await callback(client, callback_body(approved, commit_sha=commit_sha))

    assert response.status_code == 422 and "commit_sha" in response.json()["detail"]
    assert stored(store_path) == before


@pytest.mark.asyncio
async def test_callback_accepts_hexadecimal_sha(client, approved, store_path):
    response = await callback(client, callback_body(approved, commit_sha="Ab" * 20))

    assert response.status_code == 200
    assert get_run_report(store_path, RUN_ID).deployment.deployed_commit_sha == "Ab" * 20


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["run_id", "files_hash", "model_name"])
@pytest.mark.parametrize("secret", [SECRET, DEPLOYER_SECRET])
async def test_callback_identifiers_cannot_echo_secrets(client, approved, store_path, caplog, field, secret):
    caplog.set_level(logging.DEBUG)
    before = stored(store_path)

    response = await callback(client, callback_body(approved, **{field: secret}))

    assert response.status_code == 409
    assert stored(store_path) == before and secret not in response.text and secret not in caplog.text


@pytest.mark.asyncio
async def test_callback_redacts_all_configured_secrets_from_detail(client, approved, store_path, caplog):
    caplog.set_level(logging.DEBUG)
    detail = f"reload lỗi: {SECRET}, {DEPLOYER_SECRET}, {TOKEN}"

    response = await callback(client, callback_body(approved, "failed", detail=detail))

    assert response.status_code == 200
    for secret in (SECRET, DEPLOYER_SECRET, TOKEN):
        assert secret not in stored(store_path) and secret not in response.text and secret not in caplog.text


@pytest.mark.asyncio
async def test_callback_does_not_return_a_secret_already_in_report(client, approved, store_path, caplog):
    caplog.set_level(logging.DEBUG)
    tainted = approved.model_copy(update={"error_trace": [{"stage": "deploy", "error": DEPLOYER_SECRET}]})
    save_run_report(store_path, tainted)
    before = stored(store_path)

    response = await callback(client, callback_body(approved))

    assert response.status_code == 409
    assert stored(store_path) == before
    assert DEPLOYER_SECRET not in response.text and DEPLOYER_SECRET not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["closed", "ci"])
async def test_github_redacts_actor_and_ci_url_before_rework(client, approved, store_path, caplog, kind):
    caplog.set_level(logging.DEBUG)
    if kind == "closed":
        event, payload = "pull_request", pull_closed(merged=False)
    else:
        event, payload = "workflow_run", ci("workflow_run", "failure")
        payload[event]["html_url"] += f"?secret={SECRET}&key={DEPLOYER_SECRET}"
    payload["sender"]["login"] = f"{SECRET}-{DEPLOYER_SECRET}"

    response = await send(client, event, payload)

    assert response.status_code == 200
    report = get_run_report(store_path, RUN_ID)
    assert report.version == 4
    for secret in (SECRET, DEPLOYER_SECRET):
        assert secret not in stored(store_path) and secret not in response.text and secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["head_sha", "merge_commit_sha", "event", "delivery"])
@pytest.mark.parametrize("secret", [SECRET, DEPLOYER_SECRET])
async def test_github_rejects_secrets_in_identifiers(client, approved, store_path, caplog, field, secret):
    caplog.set_level(logging.DEBUG)
    before = stored(store_path)
    payload = pull_closed(merged=True)
    if field == "head_sha":
        payload["pull_request"]["head"]["sha"] = secret
    elif field == "merge_commit_sha":
        payload["pull_request"]["merge_commit_sha"] = secret

    response = await send(
        client,
        secret if field == "event" else "pull_request",
        payload,
        delivery=secret if field == "delivery" else "delivery-1",
    )

    assert response.status_code == 400
    assert stored(store_path) == before and secret not in response.text and secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["action", "conclusion"])
async def test_ignored_github_fields_do_not_echo_secrets(client, approved, store_path, field):
    payload = ci("check_suite", "success")
    if field == "action":
        payload[field] = SECRET
    else:
        payload["check_suite"][field] = DEPLOYER_SECRET

    response = await send(client, "check_suite", payload)

    assert response.status_code == 202
    assert SECRET not in response.text and DEPLOYER_SECRET not in response.text


@pytest.mark.asyncio
async def test_invalid_ci_conclusion_type_is_ignored(client, approved):
    payload = ci("workflow_run", "success")
    payload["workflow_run"]["conclusion"] = {"unexpected": "value"}

    response = await send(client, "workflow_run", payload)

    assert response.status_code == 202


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["X-Hub-Signature-256", "X-Atomesus-Signature"])
@pytest.mark.parametrize(
    ("secret", "signature", "status"),
    [("", "", 503), (SECRET, "", 401), (SECRET, "sha256=" + "z" * 64, 401)],
)
async def test_authentication_configuration_and_header_checked_before_body(header, secret, signature, status):
    from fastapi import HTTPException, Request

    from src.api.webhooks import _signed_body

    async def receive():
        pytest.fail("Không được đọc nội dung khi thiếu cấu hình hoặc header chữ ký không hợp lệ.")

    request = Request({"type": "http", "headers": [(header.lower().encode(), signature.encode())]}, receive=receive)

    with pytest.raises(HTTPException) as exc:
        await _signed_body(request, SecretStr(secret), header, "WEBHOOK_SECRET")

    assert exc.value.status_code == status


@pytest.mark.asyncio
async def test_chunked_body_over_one_megabyte_returns_413(client, approved):
    async def chunks():
        yield b"x" * (1024 * 1024)
        yield b"x"

    response = await client.post(
        WEBHOOK,
        content=chunks(),
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "chunked", "X-Hub-Signature-256": sign(b"x")},
    )

    assert response.status_code == 413
