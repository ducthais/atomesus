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
    """Sổ bằng chứng SQLite tạm, nạp sẵn các RunReport mẫu trong fixtures/run_reports."""
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
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
            overlapped.append(
                deploy_run(READY_RUN_ID, user={"id": "an", "role": "Reviewer"}, config=config, github=winner)
            )
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
