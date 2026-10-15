from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.main import app
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.run_report import FIXTURES_DIR, RunReport, list_run_reports, save_run_report

READY_RUN_ID = "3f6c1a2e-8b4d-4c7a-9e21-0d5b7a1c9f10"


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """Sổ bằng chứng SQLite tạm, nạp sẵn các RunReport mẫu trong fixtures/run_reports.

    Chế độ deploy pr: các test Deploy cũ kiểm luồng pull request; test chế độ direct tự đổi `deploy_mode`."""
    config = PipelineSettings(
        _env_file=None,
        store_path=str(tmp_path / "runs.sqlite3"),
        deploy_mode="pr",
        release_dir=str(tmp_path / "releases"),
    )
    for path in FIXTURES_DIR.glob("*.json"):
        save_run_report(config.store_path, RunReport.model_validate_json(path.read_text(encoding="utf-8")))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    yield config.store_path
    app.dependency_overrides.pop(auth.settings, None)


@pytest_asyncio.fixture
async def logged_in(client, store_path):
    response = await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
    assert response.status_code == 200
    return client


@pytest.mark.asyncio
async def test_runs_require_session(client, store_path):
    assert (await client.get("/api/v1/runs")).status_code == 401
    assert (await client.get(f"/api/v1/runs/{READY_RUN_ID}")).status_code == 401


@pytest.mark.asyncio
async def test_list_runs_returns_newest_first(logged_in):
    response = await logged_in.get("/api/v1/runs")

    assert response.status_code == 200
    created = [RunReport.model_validate(r).created_at for r in response.json()]
    assert len(created) == 3
    assert created == sorted(created, reverse=True)


@pytest.mark.asyncio
async def test_list_runs_filters_by_status(logged_in):
    response = await logged_in.get("/api/v1/runs", params={"status": "DEPLOYED"})

    assert response.status_code == 200
    runs = response.json()
    assert [r["status"] for r in runs] == ["DEPLOYED"]
    assert runs[0]["approval"]["reviewer"] == "an"


@pytest.mark.asyncio
async def test_list_runs_rejects_unknown_status(logged_in):
    response = await logged_in.get("/api/v1/runs", params={"status": "DONE"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_run_returns_full_report(logged_in):
    response = await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")

    assert response.status_code == 200
    report = response.json()
    assert report["run_id"] == READY_RUN_ID
    assert report["created_by"] == "minh"
    assert report["version"] == 3
    assert report["approval"] is None
    for field in ("gates", "checks", "cost", "timing"):
        assert field in report


@pytest.mark.asyncio
async def test_get_run_unknown_id_returns_404(logged_in):
    response = await logged_in.get("/api/v1/runs/does-not-exist")

    assert response.status_code == 404


@pytest.fixture
def generated_run(store_path, tmp_path):
    """Run chờ duyệt có bộ file sinh thật từ spec mẫu, như run do agent tạo."""
    from src.codegen.render import file_hashes, render
    from src.policy.loader import load_policy
    from src.run_report.store import get_run_report, save_run_files
    from src.spec import SPEC_FIXTURES_DIR, Spec

    spec = Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    files = render(spec, load_policy())
    hashes, files_hash = file_hashes(files)
    ready = get_run_report(store_path, READY_RUN_ID)
    report = ready.model_copy(
        update={"spec": spec.model_dump(by_alias=True, mode="json"), "files": hashes, "files_hash": files_hash}
    )
    save_run_report(store_path, report)
    save_run_files(store_path, READY_RUN_ID, report.version, files)
    app.dependency_overrides[auth.settings]().deploy_dir = str(tmp_path / "dagster_local")
    return report


@pytest.mark.asyncio
async def test_approve_only_records_signature_and_writes_nothing(logged_in, generated_run, tmp_path):
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash, "comment": "ok"}
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "APPROVED" and report["stage_reached"] == "review"
    assert report["approval"]["reviewer"] == "an"
    assert report["approval"]["files_hash"] == generated_run.files_hash
    assert report["deployment"] is None
    assert not (tmp_path / "dagster_local").exists(), "duyệt không ghi file nào ra đĩa hay Dagster"


@pytest.mark.asyncio
async def test_batch_approve_only_records_signature(logged_in, generated_run, tmp_path):
    response = await logged_in.post(
        "/api/v1/runs/batch-review",
        json={
            "decision": "approve",
            "comment": "",
            "items": [{"run_id": READY_RUN_ID, "files_hash": generated_run.files_hash}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["results"] == [
        {"run_id": READY_RUN_ID, "ok": True, "status": "APPROVED", "message": "Đã duyệt"}
    ]
    assert not (tmp_path / "dagster_local").exists()


@pytest.fixture
def github():
    """GitHub giả cho POST /runs/{id}/deploy; không ra mạng."""
    from src.run_report.router import pipeline_github
    from tests.test_codegen.test_deploy_pr import FakeGithub

    fake = FakeGithub()
    app.dependency_overrides[pipeline_github] = fake.client
    yield fake
    app.dependency_overrides.pop(pipeline_github, None)


@pytest_asyncio.fixture
async def approved_run(logged_in, generated_run):
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash}
    )
    assert response.status_code == 200, response.text
    return RunReport.model_validate(response.json())


@pytest.mark.asyncio
async def test_deploy_opens_pull_request_and_keeps_approved(logged_in, approved_run, github, tmp_path):
    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "APPROVED" and report["stage_reached"] == "deploy"
    deployment = report["deployment"]
    assert deployment["repo"] == "acme/atomesus-pipelines"
    assert deployment["branch"] == f"pipeline/fct_daily_orders/{READY_RUN_ID[:8]}-v{approved_run.version}"
    assert deployment["pr_number"] == 1 and deployment["pr_url"].endswith("/pull/1")
    assert deployment["state"] == "open" and deployment["head_sha"] and deployment["opened_at"]
    stored = (await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")).json()
    assert stored["deployment"] == deployment and stored["status"] == "APPROVED"
    assert not (tmp_path / "dagster_local").exists()


@pytest.mark.asyncio
async def test_deploy_again_returns_the_same_pull_request(logged_in, approved_run, github):
    first = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")
    writes = len(github.writes())

    again = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert again.status_code == 200
    assert again.json()["deployment"]["pr_url"] == first.json()["deployment"]["pr_url"]
    assert len(github.writes()) == writes and len(github.pulls) == 1


@pytest.mark.asyncio
async def test_only_reviewer_can_deploy(client, approved_run, github):
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})

    response = await client.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 403 and github.requests == []


@pytest.mark.asyncio
async def test_deploy_requires_approved_status(logged_in, generated_run, github):
    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 409 and "READY_FOR_REVIEW" in response.json()["detail"]
    assert github.requests == []


@pytest.mark.asyncio
async def test_deploy_refuses_stored_files_that_no_longer_match_signature(logged_in, approved_run, github, store_path):
    from src.run_report.store import get_run_files, save_run_files

    files = get_run_files(store_path, READY_RUN_ID, approved_run.version)
    tampered = {**files, next(iter(files)): "-- sửa sau khi duyệt\n"}
    save_run_files(store_path, READY_RUN_ID, approved_run.version, tampered)

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 409 and "files_hash" in response.json()["detail"]
    assert github.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail", "status_code"),
    [(401, 502), (403, 502), (404, 502), (422, 502), (500, 503), ("timeout", 503), ("unconfigured", 503)],
)
async def test_github_failure_keeps_approved_and_records_deploy_error(
    logged_in, approved_run, github, fail, status_code, caplog
):
    from pydantic import SecretStr

    from src.codegen.github import GithubClient
    from src.run_report.router import pipeline_github
    from tests.test_codegen.test_deploy_pr import TOKEN

    if fail == "unconfigured":
        app.dependency_overrides[pipeline_github] = lambda: GithubClient("", SecretStr(TOKEN))
    else:
        github.fail = {"POST /git/trees" if fail == 422 else "GET": fail}
    caplog.set_level("DEBUG")

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == status_code
    detail = response.json()["detail"]
    assert detail and TOKEN not in response.text
    stored = await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")
    report = stored.json()
    assert report["status"] == "APPROVED" and report["deployment"] is None
    assert report["error_trace"][-1] == {"stage": "deploy", "error": f"Mở pull request thất bại: {detail}"}
    assert TOKEN not in stored.text and TOKEN not in caplog.text
    if fail == "unconfigured":
        assert "PIPELINE_GITHUB_REPO" in detail


@pytest.mark.asyncio
async def test_retry_after_github_failure_opens_pull_request(logged_in, approved_run, github):
    github.fail = {"GET": 500}
    assert (await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")).status_code == 503

    github.fail = {}
    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 200 and response.json()["deployment"]["pr_number"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["files_hash", "version"])
async def test_deploy_refuses_persisted_signature_mismatch_with_409(logged_in, approved_run, github, store_path, field):
    value = {"files_hash": "sha256:" + "0" * 64, "version": approved_run.version + 1}[field]
    corrupted = approved_run.model_copy(update={"approval": approved_run.approval.model_copy(update={field: value})})
    save_run_report(store_path, corrupted)  # model_copy không validate: giả lập bản lưu có chữ ký lệch

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 409 and "Chữ ký duyệt" in response.json()["detail"]
    assert github.requests == []


@pytest.mark.asyncio
async def test_branch_moved_while_opening_pull_request_is_409_without_deployment(logged_in, approved_run, github):
    branch = f"pipeline/fct_daily_orders/{READY_RUN_ID[:8]}-v{approved_run.version}"
    github.before = {"POST /pulls": lambda: github.push(branch, "pipelines/fct_daily_orders/x.sql", "select 2\n")}

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 409 and "vừa bị đổi" in response.json()["detail"]
    stored = (await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")).json()
    assert stored["status"] == "APPROVED" and stored["deployment"] is None


@pytest.mark.asyncio
async def test_failed_deploy_keeps_deployment_saved_by_overlapping_request(logged_in, approved_run, github, store_path):
    """Request A đọc run rồi chờ GitHub; trong lúc đó request B mở pull request và lưu deployment; sau đó A lỗi."""
    import httpx
    from fastapi import BackgroundTasks
    from pydantic import SecretStr

    from src.codegen.github import GithubClient
    from src.run_report.router import deploy_run, pipeline_github
    from src.run_report.store import get_run_report
    from tests.test_codegen.test_deploy_pr import REPO, TOKEN

    config = app.dependency_overrides[auth.settings]()
    overlapped = []

    def overlap_then_fail(request: httpx.Request) -> httpx.Response:
        if not overlapped:
            winner = GithubClient(REPO, SecretStr(TOKEN), transport=httpx.MockTransport(github.handle))
            reviewer = {"id": "an", "role": "Reviewer"}
            overlapped.append(deploy_run(READY_RUN_ID, BackgroundTasks(), user=reviewer, config=config, github=winner))
            return httpx.Response(500, json={"message": "lỗi giả"})
        return github.handle(request)

    app.dependency_overrides[pipeline_github] = lambda: GithubClient(
        REPO, SecretStr(TOKEN), transport=httpx.MockTransport(overlap_then_fail)
    )

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 503 and overlapped and overlapped[0].deployment is not None
    stored = get_run_report(store_path, READY_RUN_ID)
    assert stored.deployment == overlapped[0].deployment, "lỗi của request A không được xóa deployment B đã lưu"
    assert stored.status == "APPROVED" and stored.error_trace[-1]["stage"] == "deploy"


def _record_progress(store_path: str, **changes) -> None:
    """Giả lập webhook GitHub ghi tiến độ (CI / merge) vào deployment đang lưu."""
    from src.run_report.store import update_run_report

    def change(current: RunReport) -> RunReport:
        deployment = {**current.deployment.model_dump(), **changes}
        return RunReport.model_validate({**current.model_dump(), "deployment": deployment})

    assert update_run_report(store_path, READY_RUN_ID, change) is not None


@pytest.mark.asyncio
async def test_deploy_again_after_ci_update_keeps_ci_progress(logged_in, approved_run, github, store_path):
    from src.run_report.store import get_run_report

    assert (await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")).status_code == 200
    _record_progress(store_path, ci_conclusion="success", ci_url="https://github.com/x/actions/runs/1")

    again = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert again.status_code == 200, again.text
    assert again.json()["deployment"]["ci_conclusion"] == "success"
    assert get_run_report(store_path, READY_RUN_ID).deployment.ci_conclusion == "success"


@pytest.mark.asyncio
async def test_deploy_finishing_after_merge_webhook_keeps_merged(logged_in, approved_run, github, store_path):
    """Request Deploy đọc run lúc PR còn mở, chờ GitHub; webhook merge ghi trong lúc đó; Deploy ghi sau không mở lại."""
    from src.run_report.store import get_run_report

    assert (await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")).status_code == 200
    github.before = {"GET /pulls": lambda: _record_progress(store_path, state="merged", merge_commit_sha="m" * 40)}

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 200, response.text
    stored = get_run_report(store_path, READY_RUN_ID)
    assert stored.deployment.state == "merged" and stored.deployment.merge_commit_sha == "m" * 40
    assert response.json()["deployment"]["state"] == "merged"


@pytest.mark.asyncio
async def test_deploy_after_merge_returns_run_without_calling_github(logged_in, approved_run, github, store_path):
    assert (await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")).status_code == 200
    _record_progress(store_path, state="merged")
    calls = len(github.requests)

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 200 and response.json()["deployment"]["state"] == "merged"
    assert len(github.requests) == calls


@pytest.fixture
def direct_mode(store_path):
    """Chế độ deploy direct (mặc định của PipelineSettings): Deploy ghi bundle vào hộp thư release."""
    config = app.dependency_overrides[auth.settings]()
    config.deploy_mode = "direct"
    return config


def _bundles(config, folder: str = "inbox") -> list[str]:
    from pathlib import Path

    path = Path(config.release_dir) / folder
    return sorted(entry.name for entry in path.iterdir()) if path.is_dir() else []


def test_direct_is_the_default_deploy_mode():
    assert PipelineSettings(_env_file=None).deploy_mode == "direct"


@pytest.mark.asyncio
async def test_direct_deploy_writes_signed_bundle_without_github(
    logged_in, approved_run, direct_mode, store_path, tmp_path, monkeypatch
):
    import json
    from pathlib import Path

    from pydantic import SecretStr

    from src.codegen.github import GithubClient
    from src.deployer.manifest import REPORT_FILE, hash_files, verify_manifest, verify_report
    from src.run_report.router import pipeline_github
    from src.run_report.store import get_run_files

    monkeypatch.setitem(app.dependency_overrides, pipeline_github, lambda: GithubClient("", SecretStr("")))
    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "APPROVED" and report["stage_reached"] == "deploy" and report["deployment"] is None
    name = f"{READY_RUN_ID}-v{approved_run.version}"
    assert _bundles(direct_mode) == [name] and not [p for p in Path(direct_mode.release_dir).rglob(".tmp-*")]
    folder = Path(direct_mode.release_dir) / "inbox" / name
    files = get_run_files(store_path, READY_RUN_ID, approved_run.version)
    written = {
        path.relative_to(folder).as_posix(): path.read_bytes().decode()
        for path in folder.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", REPORT_FILE}
    }
    assert written == files, "bundle chứa đúng bộ file đã duyệt, không thêm không bớt"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    verify_manifest(manifest, written)
    verify_report(manifest, json.loads((folder / REPORT_FILE).read_text(encoding="utf-8")))
    assert manifest["files"] == hash_files(files)[0] and manifest["files_hash"] == approved_run.files_hash
    assert (manifest["run_id"], manifest["version"]) == (READY_RUN_ID, approved_run.version)
    assert manifest["model_name"] == "fct_daily_orders" and manifest["reviewer"] == approved_run.approval.reviewer
    stored = (await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")).json()
    assert stored["status"] == "APPROVED" and stored["stage_reached"] == "deploy"
    assert not (tmp_path / "dagster_local").exists(), "backend không tự cài lên Dagster"

    again = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert again.status_code == 200 and _bundles(direct_mode) == [name], "bấm Deploy lại không ghi bundle thứ hai"


@pytest.mark.asyncio
async def test_direct_deploy_ignores_optional_github_failure(logged_in, approved_run, github, direct_mode, monkeypatch):
    import src.codegen.deploy as deploy

    def fail_pr(*_args):
        raise RuntimeError("simulated GitHub failure")

    monkeypatch.setattr(deploy, "open_pull_request", fail_pr)

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "APPROVED" and response.json()["stage_reached"] == "deploy"
    assert _bundles(direct_mode) == [f"{READY_RUN_ID}-v{approved_run.version}"]


@pytest.mark.asyncio
async def test_admin_can_direct_deploy_approved_run(client, approved_run, direct_mode):
    await client.post("/api/v1/pipeline/session", json={"username": "admin", "password": "Admin@123"})

    response = await client.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 200, response.text
    assert response.json()["stage_reached"] == "deploy"


@pytest.mark.asyncio
async def test_direct_deploy_keeps_role_signature_and_files_checks(
    client, logged_in, approved_run, direct_mode, store_path
):
    from src.run_report.store import get_run_files, save_run_files

    files = get_run_files(store_path, READY_RUN_ID, approved_run.version)
    save_run_files(store_path, READY_RUN_ID, approved_run.version, {**files, next(iter(files)): "-- sửa\n"})
    tampered = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")
    assert tampered.status_code == 409 and "files_hash" in tampered.json()["detail"]
    save_run_files(store_path, READY_RUN_ID, approved_run.version, files)

    corrupted = approved_run.model_copy(
        update={"approval": approved_run.approval.model_copy(update={"files_hash": "sha256:" + "0" * 64})}
    )
    save_run_report(store_path, corrupted)
    signature = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")
    assert signature.status_code == 409 and "Chữ ký duyệt" in signature.json()["detail"]
    save_run_report(store_path, approved_run)

    await client.delete("/api/v1/pipeline/session")
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    engineer = await client.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")
    assert engineer.status_code == 403
    assert _bundles(direct_mode) == [], "không qua kiểm tra thì không ghi bundle"


@pytest.mark.asyncio
async def test_direct_deploy_requires_approved_status(logged_in, generated_run, direct_mode):
    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 409 and "READY_FOR_REVIEW" in response.json()["detail"]
    assert _bundles(direct_mode) == []


@pytest.mark.asyncio
async def test_direct_deploy_claims_model_name_before_writing_bundle(
    logged_in, approved_run, direct_mode, store_path, monkeypatch
):
    import importlib
    import shutil
    from pathlib import Path

    from src.run_report.access import model_owner

    router = importlib.import_module("src.run_report.router")  # gói src.run_report cũng có thuộc tính `router`

    claimed = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")
    assert claimed.status_code == 200 and model_owner(store_path, "fct_daily_orders") is not None

    monkeypatch.setattr(router, "claim_model", lambda *_args: "fct_daily_orders")
    shutil.rmtree(Path(direct_mode.release_dir) / "inbox")

    taken = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert taken.status_code == 409
    assert taken.json()["detail"] == "Tên pipeline fct_daily_orders đã có người dùng khác dùng, hãy đổi tên model."
    assert _bundles(direct_mode) == []


@pytest.mark.asyncio
async def test_direct_deploy_disk_error_keeps_approved_and_records_error(
    logged_in, approved_run, direct_mode, monkeypatch
):
    import importlib

    router = importlib.import_module("src.run_report.router")

    def full_disk(*_args):
        raise OSError("No space left on device")

    monkeypatch.setattr(router, "write_bundle", full_disk)

    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")

    assert response.status_code == 503 and "hộp thư release" in response.json()["detail"]
    stored = (await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")).json()
    assert stored["status"] == "APPROVED" and stored["error_trace"][-1]["stage"] == "deploy"


@pytest.mark.asyncio
async def test_direct_deploy_end_to_end_with_deployer_callback(logged_in, approved_run, direct_mode, monkeypatch):
    """Deploy ghi bundle → deployer cài → callback ký HMAC (không có commit_sha) → DEPLOYED, không cần merge."""
    import asyncio
    from pathlib import Path

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from src.api import webhooks
    from src.deployer import install, service

    assert (await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/deploy")).status_code == 200
    installed = []

    def fake_install(model, files, deploy_dir, verify):
        installed.append((model, sorted(files)))
        return Path(deploy_dir) / model

    monkeypatch.setattr(install, "install_project", fake_install)
    secret = "khoa-callback-e2e-7f3a9c1d"
    app.dependency_overrides[webhooks.deployer_settings] = lambda: webhooks.DeployerSettings(secret=SecretStr(secret))
    config = service.Config(
        repo_url="",
        secret=secret,
        deploy_dir=str(Path(direct_mode.release_dir).parent / "deploy"),
        state_dir=str(Path(direct_mode.release_dir).parent / "deployer"),
        release_dir=direct_mode.release_dir,
    )
    backend = TestClient(app)  # không chạy lifespan: chỉ cần route callback

    def post(url, body, headers):
        return backend.post(url.removeprefix(config.backend_url), content=body, headers=headers).status_code

    try:
        summary = await asyncio.to_thread(service.run_once, config, post=post, sleep=lambda _s: None)
    finally:
        app.dependency_overrides.pop(webhooks.deployer_settings, None)

    assert summary["pending"] == 0 and installed and installed[0][0] == "fct_daily_orders"
    assert _bundles(direct_mode) == [] and _bundles(direct_mode, "done") == [f"{READY_RUN_ID}-v{approved_run.version}"]
    stored = (await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")).json()
    assert stored["status"] == "DEPLOYED" and stored["stage_reached"] == "deploy"


@pytest.mark.asyncio
async def test_reports_without_deployment_field_still_load(logged_in):
    old = (FIXTURES_DIR / "deployed.json").read_text(encoding="utf-8")
    assert '"deployment"' not in old and RunReport.model_validate_json(old).deployment is None

    response = await logged_in.get("/api/v1/runs", params={"status": "DEPLOYED"})

    assert response.status_code == 200 and response.json()[0]["deployment"] is None


@pytest.mark.asyncio
async def test_engineer_cannot_approve(client, store_path, generated_run):
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    response = await client.post(f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_approve_rejects_stale_files_hash(logged_in, generated_run, tmp_path):
    response = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": "sha256:old"})

    assert response.status_code == 409
    assert (await logged_in.get(f"/api/v1/runs/{READY_RUN_ID}")).json()["status"] == "READY_FOR_REVIEW"
    assert not (tmp_path / "dagster_local").exists()


@pytest.mark.asyncio
async def test_reject_records_decision_without_deploy(logged_in, generated_run, tmp_path):
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/reject", json={"files_hash": generated_run.files_hash, "comment": "sai metric"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert response.json()["approval"]["decision"] == "rejected"
    assert not (tmp_path / "dagster_local").exists()
    second = await logged_in.post(f"/api/v1/runs/{READY_RUN_ID}/approve", json={"files_hash": generated_run.files_hash})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_batch_review_processes_each_run_and_reports_partial_failure(logged_in, generated_run):
    response = await logged_in.post(
        "/api/v1/runs/batch-review",
        json={
            "decision": "reject",
            "comment": "Sai định nghĩa metric",
            "items": [
                {"run_id": READY_RUN_ID, "files_hash": generated_run.files_hash},
                {"run_id": "missing-run", "files_hash": "sha256:missing"},
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["results"] == [
        {"run_id": READY_RUN_ID, "ok": True, "status": "REJECTED", "message": "Đã từ chối"},
        {"run_id": "missing-run", "ok": False, "status": None, "message": "Không tìm thấy run missing-run"},
    ]


@pytest.mark.asyncio
async def test_batch_review_validates_duplicates_and_role(client, store_path, generated_run, logged_in):
    item = {"run_id": READY_RUN_ID, "files_hash": generated_run.files_hash}
    duplicate = await logged_in.post(
        "/api/v1/runs/batch-review",
        json={"decision": "approve", "comment": "", "items": [item, item]},
    )
    assert duplicate.status_code == 422

    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
    forbidden = await client.post(
        "/api/v1/runs/batch-review",
        json={"decision": "approve", "comment": "", "items": [item]},
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_can_request_changes_and_restart_from_spec(logged_in, generated_run):
    from src.run_report.router import request_change_deps
    from src.run_report.store import get_run_report
    from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps

    llm = FakeLLM(SPEC)
    store_path = app.dependency_overrides[auth.settings]().store_path
    save_run_report(store_path, generated_run.model_copy(update={"plan": PLAN}))
    app.dependency_overrides[request_change_deps] = lambda: deps(llm)
    try:
        response = await logged_in.post(
            f"/api/v1/runs/{READY_RUN_ID}/request-changes",
            json={
                "files_hash": generated_run.files_hash,
                "version": generated_run.version,
                "feedback": "  Giữ metric, sửa lịch chạy  ",
            },
        )
    finally:
        app.dependency_overrides.pop(request_change_deps, None)

    assert response.status_code == 200, response.text
    assert response.json()["version"] == generated_run.version + 1
    assert response.json()["status"] == "GENERATING"
    revised = get_run_report(store_path, READY_RUN_ID)
    assert revised.status == "READY_FOR_REVIEW", revised.error_trace
    assert revised.review_history[-1].decision == "changes_requested"
    assert revised.review_history[-1].comment == "Giữ metric, sửa lịch chạy"
    assert "Giữ metric, sửa lịch chạy" in llm.calls[0]["user"]["yêu_cầu_thay_đổi"]


@pytest.mark.asyncio
async def test_request_changes_rejects_blank_feedback(logged_in, generated_run):
    response = await logged_in.post(
        f"/api/v1/runs/{READY_RUN_ID}/request-changes",
        json={"files_hash": generated_run.files_hash, "version": generated_run.version, "feedback": "   "},
    )

    assert response.status_code == 422


def test_save_overwrites_same_run_and_survives_reopen(store_path):
    ready = next(r for r in list_run_reports(store_path) if r.run_id == READY_RUN_ID)
    save_run_report(store_path, ready.model_copy(update={"status": "NEEDS_HUMAN"}))

    runs = list_run_reports(store_path)
    assert len(runs) == 3
    assert [r.status for r in runs if r.run_id == READY_RUN_ID] == ["NEEDS_HUMAN"]
    assert [r.run_id for r in list_run_reports(store_path, "READY_FOR_REVIEW")] == []


def test_cancelled_run_cannot_be_overwritten_by_late_background_save(store_path):
    from src.run_report.store import get_run_report

    ready = next(r for r in list_run_reports(store_path) if r.run_id == READY_RUN_ID)
    running = ready.model_copy(update={"status": "GENERATING", "approval": None})
    save_run_report(store_path, running)
    cancelled = running.model_copy(update={"status": "CANCELLED"})
    save_run_report(store_path, cancelled)
    save_run_report(store_path, ready)  # job nền hoàn tất muộn

    assert get_run_report(store_path, READY_RUN_ID).status == "CANCELLED"


@pytest.mark.asyncio
async def test_owner_can_cancel_running_run_but_not_reviewable_run(client, store_path):
    from src.run_report.store import get_run_report

    ready = get_run_report(store_path, READY_RUN_ID)
    save_run_report(store_path, ready.model_copy(update={"status": "GENERATING", "approval": None}))
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})

    response = await client.post(f"/api/v1/runs/{READY_RUN_ID}/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["timeline"][-1]["event"] == "cancelled"
    assert (await client.post(f"/api/v1/runs/{READY_RUN_ID}/cancel")).status_code == 409
