"""Service deployer chạy offline: một repo git cục bộ đóng vai repo pipeline, backend là hàm post giả."""

import hashlib
import hmac
import json
import os
import shlex
import subprocess
import threading
import urllib.error
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.deployer import install, service, verify
from src.deployer.manifest import build_manifest
from src.deployer.service import CALLBACK_PATH, SIGNATURE_HEADER, Config, DeployerError
from tests.test_deployer.test_verify import Clock, Dagster

SECRET = "khoa-ky-callback-cho-test"
VERIFY_PIPELINE = verify.verify_pipeline


def files_v(version: int) -> dict[str, str]:
    return {
        "ingestion/fct_x_pipeline.py": f"print('el v{version}')\n",
        "dbt_project/models/gold/fct_x.sql": f"select {version} as n\n",
        "dbt_project/models/gold/fct_x.yml": "version: 2\n",
        "infra/dagster/generated/fct_x_asset.py": f"# asset v{version}\n",
    }


def signed(files: dict[str, str], version: int, model: str = "fct_x", run_id: str = "run-1") -> dict[str, str]:
    """Thư mục pipelines/<model>/ đúng như backend ghi khi mở pull request: bộ file, run_report.json, manifest.json."""
    manifest = build_manifest(
        run_id=run_id,
        version=version,
        model_name=model,
        files=files,
        reviewer="an",
        approved_at=datetime(2026, 10, 10, tzinfo=UTC),
    )
    report = {
        "run_id": run_id,
        "version": version,
        "files_hash": manifest["files_hash"],
        "status": "APPROVED",
        "spec": {"transform": {"model_name": model}},
        "approval": {
            "decision": "approved",
            "reviewer": "an",
            "files_hash": manifest["files_hash"],
            "version": version,
            "at": "2026-10-10T00:00:00Z",
            "comment": "",
        },
    }
    return {**files, "run_report.json": json.dumps(report, indent=2) + "\n", "manifest.json": json.dumps(manifest)}


class Remote:
    """Repo pipeline giả: nhánh mặc định main, mỗi commit ghi lại một số thư mục pipelines/<model>/."""

    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.git("init", "-q", "-b", "main")
        (root / "README.md").write_text("repo pipeline\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "init")

    def git(self, *args) -> str:
        command = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "core.autocrlf=false"]
        done = subprocess.run([*command, *args], cwd=self.root, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def commit(self, model: str, content: dict[str, str], message: str = "pipeline") -> str:
        folder = self.root / "pipelines" / model
        for path, text in content.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode())
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


class Backend:
    """Backend giả nhận callback; `fail` lỗi đầu tiên ném ra (hoặc mã HTTP trả về) trước khi nhận."""

    def __init__(self, *fail):
        self.fail = list(fail)
        self.calls = []

    def __call__(self, url, body, headers):
        if self.fail:
            outcome = self.fail.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        self.calls.append((url, body, headers))
        return 200

    def payloads(self) -> list[dict]:
        return [json.loads(body) for _url, body, _headers in self.calls]


@pytest.fixture
def remote(tmp_path):
    return Remote(tmp_path / "remote")


@pytest.fixture
def config(tmp_path, remote):
    return Config(
        repo_url=str(remote.root),
        secret=SECRET,
        deploy_dir=str(tmp_path / "deploy"),
        state_dir=str(tmp_path / "state"),
    )


@pytest.fixture
def dagster(monkeypatch):
    fake = Dagster()
    clock = Clock()
    monkeypatch.setattr(verify.httpx, "post", fake)

    def success(url, model, timeout):
        return VERIFY_PIPELINE(url, model, timeout, sleep=clock.sleep, clock=clock)

    monkeypatch.setattr(verify, "verify_pipeline", success)
    return fake


@pytest.fixture
def installs(monkeypatch, dagster):
    """Cài thật (scaffold dbt + bộ file), chỉ bỏ bước dbt parse; ghi lại mỗi lần install_project được gọi."""
    monkeypatch.setattr(install, "_compile_manifest", lambda _root: None)
    calls = []
    real = install.install_project

    def recording(model, files, deploy_dir, **kwargs):
        calls.append(model)
        return real(model, files, deploy_dir, **kwargs)

    monkeypatch.setattr(install, "install_project", recording)
    return calls


def run(config, backend, **kwargs):
    return service.run_once(config, post=backend, sleep=lambda _seconds: None, **kwargs)


def live_entries(config) -> list[str]:
    return sorted(path.name for path in Path(config.deploy_dir).iterdir())


def test_first_run_installs_pipeline_and_reports_signed_callback(config, remote, installs, dagster):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    backend = Backend()

    summary = run(config, backend)

    assert summary == {"head": head, "applied": head, "models": ["fct_x"], "pending": 0, "unresolved": {}}
    assert service.load_state(config) == {"applied": head, "pending": [], "models": {}}
    project = Path(config.deploy_dir) / "fct_x"
    assert (project / "dbt_project/models/gold/fct_x.sql").read_text(encoding="utf-8") == "select 1 as n\n"
    assert not (project / "manifest.json").exists() and not (project / "run_report.json").exists()
    assert "location_name: fct_x" in (Path(config.deploy_dir) / "workspace.yaml").read_text(encoding="utf-8")
    [(url, body, headers)] = backend.calls
    assert url == "http://backend:8000" + CALLBACK_PATH
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert headers[SIGNATURE_HEADER] == expected and headers["Content-Type"] == "application/json"
    payload = json.loads(body)
    manifest = json.loads(signed(files_v(1), 1)["manifest.json"])
    assert set(payload) == {"run_id", "version", "files_hash", "model_name", "commit_sha", "status", "detail", "at"}
    assert payload["run_id"] == "run-1" and payload["version"] == 1 and payload["model_name"] == "fct_x"
    assert payload["files_hash"] == manifest["files_hash"] and payload["commit_sha"] == head
    assert payload["status"] == "deployed" and payload["detail"]
    assert "dagster-run-1" in payload["detail"]
    assert len(dagster.launches) == 1
    assert dagster.launches[0]["selector"]["pipelineName"] == "fct_x_job"
    assert datetime.fromisoformat(payload["at"]).tzinfo is not None


def test_new_version_replaces_the_single_location_without_leftovers(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    head = remote.commit("fct_x", signed(files_v(2), 2), "v2")
    backend = Backend()

    summary = run(config, backend)

    assert summary["applied"] == head and summary["models"] == ["fct_x"]
    root = Path(config.deploy_dir)
    assert live_entries(config) == ["fct_x", "workspace.yaml"], "không còn thư mục .old-/.tmp-"
    assert (root / "fct_x/dbt_project/models/gold/fct_x.sql").read_text(encoding="utf-8") == "select 2 as n\n"
    assert (root / "workspace.yaml").read_text(encoding="utf-8").count("location_name:") == 1
    assert [(p["version"], p["status"]) for p in backend.payloads()] == [(2, "deployed")]


def test_rerun_without_new_commit_does_nothing(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    backend = Backend()

    summary = run(config, backend)

    assert summary["models"] == [] and summary["pending"] == 0
    assert installs == ["fct_x"] and backend.calls == []


def test_only_changed_pipelines_are_installed(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    other = {path.replace("fct_x", "fct_y"): text for path, text in files_v(1).items()}
    remote.commit("fct_y", signed(other, 1, model="fct_y", run_id="run-2"))
    backend = Backend()

    run(config, backend)

    assert installs == ["fct_x", "fct_y"] and [p["model_name"] for p in backend.payloads()] == ["fct_y"]
    assert live_entries(config) == ["fct_x", "fct_y", "workspace.yaml"]


def test_tampered_file_is_rejected_and_running_version_kept(config, remote, installs):
    first = remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    tampered = signed(files_v(2), 2)
    tampered["dbt_project/models/gold/fct_x.sql"] = "select 'sửa sau khi duyệt' as n\n"
    remote.commit("fct_x", tampered, "sửa tay")
    backend = Backend()

    summary = run(config, backend)

    assert installs == ["fct_x"], "bộ file lệch manifest thì không gọi install"
    sql = Path(config.deploy_dir) / "fct_x/dbt_project/models/gold/fct_x.sql"
    assert sql.read_text(encoding="utf-8") == "select 1 as n\n"
    [payload] = backend.payloads()
    assert payload["status"] == "failed" and payload["version"] == 2 and "sha256" in payload["detail"]
    assert summary["applied"] == first, "pipeline chưa cài được: commit đã áp dụng không tiến"
    assert summary["unresolved"] == {"fct_x": "failed"}

    again = Backend()
    summary = run(config, again)

    assert again.calls == [] and installs == ["fct_x"], "cùng nội dung đã báo failed: không kiểm/báo lặp lại"
    assert summary["applied"] == first and summary["models"] == []

    # Sửa bằng version mới đúng chữ ký (pull request mới): cài được, commit được ghi nhận.
    fixed = remote.commit("fct_x", signed(files_v(3), 3), "v3")
    summary = run(config, Backend())

    assert installs == ["fct_x", "fct_x"] and summary["applied"] == fixed and summary["unresolved"] == {}
    assert sql.read_text(encoding="utf-8") == "select 3 as n\n"


def test_forged_manifest_without_matching_run_report_is_rejected(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    # Kẻ sửa file rồi tự tính lại manifest: sha256 khớp nhưng run_report.json vẫn mang chữ ký cũ.
    forged = signed(files_v(2), 2)
    forged["run_report.json"] = signed(files_v(1), 1)["run_report.json"]
    remote.commit("fct_x", forged, "giả manifest")
    backend = Backend()

    run(config, backend)

    assert installs == ["fct_x"]
    assert (Path(config.deploy_dir) / "fct_x/infra/dagster/generated/fct_x_asset.py").read_text(
        encoding="utf-8"
    ) == "# asset v1\n"
    [payload] = backend.payloads()
    assert payload["status"] == "failed" and "run_report.json" in payload["detail"]


def test_non_regular_file_is_rejected(config, remote, installs):
    content = signed(files_v(1), 1)
    remote.commit("fct_x", content)
    remote.git("update-index", "--chmod=+x", "pipelines/fct_x/ingestion/fct_x_pipeline.py")
    remote.git("commit", "-q", "-m", "chmod")
    backend = Backend()

    run(config, backend)

    assert installs == [] and not (Path(config.deploy_dir) / "fct_x").exists()
    [payload] = backend.payloads()
    assert payload["status"] == "failed" and "file thường" in payload["detail"]


def test_unreadable_manifest_is_not_installed_and_not_reported(config, remote, installs):
    content = {**signed(files_v(1), 1), "manifest.json": "[1, 2"}
    remote.commit("fct_x", content)
    backend = Backend()

    summary = run(config, backend)

    assert installs == [] and backend.calls == [] and summary["applied"] is None
    assert summary["unresolved"] == {"fct_x": "unreported"}
    assert run(config, backend)["models"] == [] and backend.calls == [], "không kiểm lặp lại cùng nội dung"


def test_install_failure_is_retried_then_reported_failed_and_keeps_previous(config, remote, installs, monkeypatch):
    first = remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    remote.commit("fct_x", signed(files_v(2), 2), "v2")

    def broken(_root):
        raise OSError("dbt parse thất bại")

    monkeypatch.setattr(install, "_compile_manifest", broken)
    backend = Backend()

    for _ in range(service.INSTALL_ATTEMPTS - 1):
        summary = run(config, backend)
        assert summary["unresolved"] == {"fct_x": "retry"} and summary["applied"] == first
    assert backend.calls == [], "lỗi cài có thể chỉ thoáng qua: chưa báo failed"

    summary = run(config, backend)

    assert installs == ["fct_x"] * (1 + service.INSTALL_ATTEMPTS)
    sql = Path(config.deploy_dir) / "fct_x/dbt_project/models/gold/fct_x.sql"
    assert sql.read_text(encoding="utf-8") == "select 1 as n\n"
    assert live_entries(config) == ["fct_x", "workspace.yaml"]
    [payload] = backend.payloads()
    assert payload["status"] == "failed" and "dbt parse" in payload["detail"]
    assert summary["applied"] == first and summary["unresolved"] == {"fct_x": "failed"}
    run(config, backend)
    assert len(installs) == 1 + service.INSTALL_ATTEMPTS and len(backend.calls) == 1, "không cài/báo lặp lại"


def test_transient_install_failure_recovers_next_cycle(config, remote, installs, monkeypatch):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    failures = [OSError("đĩa đầy")]

    def flaky(_root):
        if failures:
            raise failures.pop()

    monkeypatch.setattr(install, "_compile_manifest", flaky)
    backend = Backend()

    summary = run(config, backend)

    assert summary["applied"] is None and summary["unresolved"] == {"fct_x": "retry"} and backend.calls == []
    assert not (Path(config.deploy_dir) / "fct_x").exists()

    summary = run(config, backend)

    assert summary["applied"] == head and summary["unresolved"] == {}
    assert [p["status"] for p in backend.payloads()] == ["deployed"]


def test_failed_callback_is_retried_next_cycle_before_commit_is_applied(config, remote, installs):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    down = Backend(*[urllib.error.URLError("connection refused")] * service.CALLBACK_ATTEMPTS)

    summary = run(config, down)

    assert summary == {
        "head": head,
        "applied": None,
        "models": ["fct_x"],
        "pending": 1,
        "unresolved": {"fct_x": "reporting"},
    }
    assert down.calls == [] and installs == ["fct_x"]
    state = service.load_state(config)
    assert state["applied"] is None and state["models"]["fct_x"]["status"] == "reporting"
    assert state["pending"][0]["payload"]["status"] == "deployed"

    backend = Backend()
    summary = run(config, backend)

    assert installs == ["fct_x"], "đã cài rồi: lần sau chỉ báo lại, không cài lại"
    assert [p["status"] for p in backend.payloads()] == ["deployed"] and summary["applied"] == head
    assert service.load_state(config) == {"applied": head, "pending": [], "models": {}}


def test_callback_retries_within_a_cycle(config, remote, installs):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    backend = Backend(503, urllib.error.URLError("timeout"))

    summary = run(config, backend)

    assert summary["applied"] == head and len(backend.calls) == 1


def test_pending_report_blocks_new_commit_until_delivered(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend(*[503] * service.CALLBACK_ATTEMPTS))
    remote.commit("fct_x", signed(files_v(2), 2), "v2")

    summary = run(config, Backend(*[503] * service.CALLBACK_ATTEMPTS))

    assert summary["head"] is None and summary["pending"] == 1 and installs == ["fct_x"]


@pytest.mark.parametrize("status", [400, 409, 422])
def test_refused_callback_stays_unresolved_without_resending(config, remote, installs, status, caplog):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    backend = Backend(status)

    summary = run(config, backend)

    assert summary["applied"] is None and summary["pending"] == 0 and backend.calls == []
    assert summary["unresolved"] == {"fct_x": "refused"}
    assert f"HTTP {status}" in caplog.text and "Backend từ chối" in caplog.text
    again = Backend()
    assert run(config, again)["applied"] is None and again.calls == [], "backend đã từ chối: không gửi lại"

    # Người vận hành xử lý xong phía backend rồi --reinstall: cài và báo lại, commit được ghi nhận.
    summary = run(config, again, reinstall=True)

    assert summary["applied"] == head and [p["status"] for p in again.payloads()] == ["deployed"]


def test_one_bad_pipeline_holds_the_commit_but_good_ones_are_installed_once(config, remote, installs):
    first = remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    other = {path.replace("fct_x", "fct_y"): text for path, text in files_v(1).items()}
    remote.commit("fct_y", signed(other, 1, model="fct_y", run_id="run-2"))
    remote.commit("fct_x", {**signed(files_v(2), 2), "manifest.json": "{}"}, "manifest hỏng")
    backend = Backend()

    summary = run(config, backend)
    run(config, backend)

    assert summary["applied"] == first and summary["unresolved"] == {"fct_x": "unreported"}
    assert installs == ["fct_x", "fct_y"], "fct_y cài một lần, không cài lại khi commit còn bị giữ"
    assert [p["model_name"] for p in backend.payloads()] == ["fct_y"]


def test_deleted_pipeline_is_skipped(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    remote.git("rm", "-r", "-q", "pipelines/fct_x")
    remote.git("commit", "-q", "-m", "xóa")
    head = remote.git("rev-parse", "HEAD")
    backend = Backend()

    summary = run(config, backend)

    assert summary["models"] == ["fct_x"] and backend.calls == [] and installs == ["fct_x"]
    assert summary["applied"] == head, "không còn gì để cài: commit được ghi nhận"
    assert (Path(config.deploy_dir) / "fct_x").is_dir()


def test_reinstall_installs_every_pipeline_again(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())

    run(config, Backend(), reinstall=True)

    assert installs == ["fct_x", "fct_x"]


def test_git_error_does_not_leak_repo_url(tmp_path):
    url = str(tmp_path / "khong-co-repo-token-bi-mat")
    config = Config(repo_url=url, secret=SECRET, deploy_dir=str(tmp_path / "d"), state_dir=str(tmp_path / "s"))

    with pytest.raises(DeployerError) as error:
        run(config, Backend())

    assert "token-bi-mat" not in str(error.value) and "<repo pipeline>" in str(error.value)
    assert "token-bi-mat" not in repr(config) and SECRET not in repr(config)


def test_http_post_sends_signed_json_to_backend(config, remote, installs):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote.commit("fct_x", signed(files_v(1), 1))
        local = Config(**{**config.__dict__, "backend_url": f"http://127.0.0.1:{server.server_port}"})
        summary = service.run_once(local, sleep=lambda _seconds: None)
    finally:
        server.shutdown()

    assert summary["pending"] == 0
    [(path, headers, body)] = received
    assert path == CALLBACK_PATH and headers[SIGNATURE_HEADER] == service.sign(SECRET, body)
    assert json.loads(body)["status"] == "deployed"


def test_http_post_returns_error_status_instead_of_raising():
    server = HTTPServer(("127.0.0.1", 0), type("H", (BaseHTTPRequestHandler,), {"log_message": lambda *a: None}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status = service.http_post(f"http://127.0.0.1:{server.server_port}/x", b"{}", {})
    finally:
        server.shutdown()

    assert status == 501


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "DEPLOYER_REPO_URL"),
        ({"DEPLOYER_REPO_URL": "git@github.com:o/r.git"}, "PIPELINE_DEPLOYER_SECRET"),
        ({"DEPLOYER_REPO_URL": "u", "PIPELINE_DEPLOYER_SECRET": "s", "DEPLOYER_INTERVAL_SEC": "x"}, "số giây"),
        ({"DEPLOYER_REPO_URL": "u", "PIPELINE_DEPLOYER_SECRET": "s", "DEPLOYER_INTERVAL_SEC": "1"}, "5 giây"),
        ({"DEPLOYER_REPO_URL": "u", "PIPELINE_DEPLOYER_SECRET": "s", "DEPLOYER_BACKEND_URL": "backend"}, "http"),
        ({"DEPLOYER_REPO_URL": "u", "PIPELINE_DEPLOYER_SECRET": "s", "DEPLOYER_SSH_KEY": "/khong/co"}, "khóa SSH"),
    ],
)
def test_config_rejects_missing_or_invalid_env(env, message):
    with pytest.raises(DeployerError, match=message):
        Config.from_env(env)


def test_config_defaults():
    config = Config.from_env({"DEPLOYER_REPO_URL": "git@github.com:o/r.git", "PIPELINE_DEPLOYER_SECRET": "s"})

    assert config.interval == 60 and config.backend_url == "http://backend:8000"
    assert config.deploy_dir == "data/dagster_local"
    assert config.dagster_url == "http://dagster:3070" and config.verify_timeout_sec == 120


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DEPLOYER_DAGSTER_URL", "dagster:3070"),
        ("DEPLOYER_DAGSTER_URL", "http://"),
        ("DEPLOYER_DAGSTER_URL", "http://dagster:bad"),
        ("DEPLOYER_DAGSTER_URL", "http://dagster:0"),
        ("DEPLOYER_VERIFY_TIMEOUT_SEC", "x"),
        ("DEPLOYER_VERIFY_TIMEOUT_SEC", "0"),
        ("DEPLOYER_VERIFY_TIMEOUT_SEC", "-1"),
    ],
)
def test_config_rejects_invalid_verification_env(key, value):
    with pytest.raises(DeployerError, match=key):
        Config.from_env({"DEPLOYER_REPO_URL": "u", "PIPELINE_DEPLOYER_SECRET": "s", key: value})


def test_config_reads_verification_env():
    config = Config.from_env(
        {
            "DEPLOYER_REPO_URL": "u",
            "PIPELINE_DEPLOYER_SECRET": "s",
            "DEPLOYER_DAGSTER_URL": "http://internal:3071/",
            "DEPLOYER_VERIFY_TIMEOUT_SEC": "30",
        }
    )
    assert config.dagster_url == "http://internal:3071" and config.verify_timeout_sec == 30


def test_corrupt_state_is_reported_not_silently_reset(config):
    service.save_state(config, {"applied": None, "pending": [], "models": {}})
    service._state_path(config).write_text("{hỏng", encoding="utf-8")

    with pytest.raises(DeployerError, match="state.json"):
        service.load_state(config)


def test_install_returning_none_is_retried_without_deployed_callback(config, remote, monkeypatch):
    remote.commit("fct_x", signed(files_v(1), 1))
    monkeypatch.setattr(install, "install_project", lambda *args, **kwargs: None)
    backend = Backend()

    summary = run(config, backend)

    assert summary["applied"] is None and summary["unresolved"] == {"fct_x": "retry"}
    assert backend.calls == []


def test_ssh_uses_pinned_hosts_and_never_accepts_new_hosts(config):
    env = service._git_env(config)
    options = shlex.split(env["GIT_SSH_COMMAND"])

    assert "StrictHostKeyChecking=yes" in options
    assert f"UserKnownHostsFile={service.DEFAULT_KNOWN_HOSTS}" in options
    assert f"GlobalKnownHostsFile={os.devnull}" in options and options[1:3] == ["-F", os.devnull]
    assert "accept-new" not in env["GIT_SSH_COMMAND"] and env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_errors_never_include_key_paths_or_expanded_remote(config, monkeypatch):
    config = Config(**{**config.__dict__, "ssh_key": "/private/key-secret"})
    stderr = (
        b"Identity file /private/key-secret inaccessible; git@github.com:private/repo: Permission denied (publickey)."
    )
    monkeypatch.setattr(
        service.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 128, b"", stderr)
    )

    with pytest.raises(DeployerError) as error:
        service._git(config, "fetch", config.repo_url)

    assert "Permission denied (publickey)" in str(error.value)
    assert "key-secret" not in str(error.value) and "private/repo" not in str(error.value)
    assert config.ssh_key not in repr(config)


def test_config_rejects_unreadable_known_hosts():
    with pytest.raises(DeployerError, match="DEPLOYER_KNOWN_HOSTS"):
        Config.from_env({"DEPLOYER_REPO_URL": "u", "PIPELINE_DEPLOYER_SECRET": "s", "DEPLOYER_KNOWN_HOSTS": "/missing"})


def test_compose_has_only_one_code_location_writer_and_a_separate_git_image():
    import yaml

    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "deploy/compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    writers, readers = [], []
    for name, spec in services.items():
        for volume in spec.get("volumes", []):
            if isinstance(volume, dict) and volume.get("source") == "dagster_code":
                (readers if volume.get("read_only") else writers).append(name)
                if volume.get("read_only"):
                    assert volume["volume"]["nocopy"] is True
            elif isinstance(volume, str) and volume.split(":")[0] == "dagster_code":
                (readers if volume.endswith(":ro") else writers).append(name)
    assert writers == ["deployer"] and sorted(readers) == ["backend", "dagster"]
    deployer = services["deployer"]
    assert "env_file" not in deployer
    assert not ({"OPENAI_API_KEY", "PIPELINE_DATABASE_URL", "POSTGRES_PASSWORD"} & deployer["environment"].keys())
    dockerfile = deployer["build"]["dockerfile_inline"]
    assert "FROM app" in dockerfile and "git openssh-client" in dockerfile
    assert dockerfile.strip().endswith("USER app") and "USER root" in dockerfile
    assert deployer["build"]["additional_contexts"] == {"app": "service:backend"}
    assert any(volume.endswith(":/run/secrets/deployer:ro") for volume in deployer["volumes"])
    assert "github_known_hosts" in deployer["environment"]["DEPLOYER_KNOWN_HOSTS"]
    assert "openssh-client" not in (root / "Dockerfile").read_text(encoding="utf-8")


def test_pinned_github_host_key_matches_official_fingerprint():
    import base64

    lines = Path(service.DEFAULT_KNOWN_HOSTS).read_text(encoding="utf-8").splitlines()
    [entry] = [line for line in lines if line and not line.startswith("#")]
    host, kind, key = entry.split()
    digest = base64.b64encode(hashlib.sha256(base64.b64decode(key)).digest()).decode().rstrip("=")

    assert host == "github.com" and kind == "ssh-ed25519"
    assert digest == "+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"


@pytest.mark.parametrize("failure", ["FAILURE", "CANCELED", "timeout", "connection", "graphql"])
@pytest.mark.parametrize("first_install", [False, True])
def test_verification_failure_rolls_back_and_waits_for_new_pipeline_content(
    config, remote, installs, dagster, first_install, failure
):
    root = Path(config.deploy_dir)
    backend = Backend()
    previous = None
    if not first_install:
        previous = remote.commit("fct_x", signed(files_v(1), 1))
        run(config, backend)
    old_files = {
        path.relative_to(root / "fct_x"): path.read_bytes() for path in (root / "fct_x").rglob("*") if path.is_file()
    }
    old_workspace = install.write_workspace(config.deploy_dir).read_bytes()
    backend.calls.clear()
    dagster.calls.clear()
    if failure in {"FAILURE", "CANCELED"}:
        dagster.statuses = [failure]
    elif failure == "timeout":
        dagster.statuses = ["STARTED"]
    else:
        import httpx

        dagster.error_query = verify.STATUS
        dagster.error = (
            httpx.ConnectError("secret-url") if failure == "connection" else {"errors": [{"message": "secret-url"}]}
        )
    failed_head = remote.commit("fct_x", signed(files_v(2), 2), "v2")

    summary = run(config, backend)

    assert summary["applied"] == previous and summary["unresolved"] == {"fct_x": "failed"}
    assert (root / "workspace.yaml").read_bytes() == old_workspace
    assert live_entries(config) == (["workspace.yaml"] if first_install else ["fct_x", "workspace.yaml"])
    restored_files = {
        path.relative_to(root / "fct_x"): path.read_bytes() for path in (root / "fct_x").rglob("*") if path.is_file()
    }
    assert restored_files == old_files
    [payload] = backend.payloads()
    assert payload["status"] == "failed" and payload["commit_sha"] == failed_head
    assert "Đã quay về bản trước" in payload["detail"] and "secret-url" not in payload["detail"]
    assert len(dagster.launches) == 1
    record = service.load_state(config)["models"]["fct_x"]
    assert record["status"] == "failed" and record["attempts"] == 1
    installed = len(installs)
    assert run(config, backend)["models"] == []
    assert len(installs) == installed and len(backend.calls) == 1 and len(dagster.launches) == 1

    # Commit chỉ đụng README hoặc pipeline khác không cho phép thử lại nội dung đã thất bại.
    remote.commit("fct_y", signed({p.replace("fct_x", "fct_y"): t for p, t in files_v(1).items()}, 1, "fct_y"))
    dagster.statuses, dagster.error_query = ["SUCCESS"], None
    assert run(config, backend)["models"] == ["fct_y"]
    assert service.load_state(config)["models"]["fct_x"] == record
    assert len(installs) == installed + 1

    new_head = remote.commit("fct_x", signed(files_v(3), 3), "v3")
    summary = run(config, backend)

    assert summary["models"] == ["fct_x"] and summary["applied"] == new_head
    assert (root / "fct_x/dbt_project/models/gold/fct_x.sql").read_text(encoding="utf-8") == "select 3 as n\n"
    assert backend.payloads()[-1]["status"] == "deployed"


def test_keeps_backup_and_defers_deployed_callback_until_success(config, remote, installs, dagster):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    remote.commit("fct_x", signed(files_v(2), 2), "v2")
    root = Path(config.deploy_dir)
    backend = Backend()
    dagster.statuses = ["STARTED", "SUCCESS"]
    seen = []

    def check():
        assert backend.calls == []
        [backup] = list(root.glob("fct_x.old-*"))
        assert (backup / "dbt_project/models/gold/fct_x.sql").read_text(encoding="utf-8") == "select 1 as n\n"
        assert (root / "fct_x/dbt_project/models/gold/fct_x.sql").read_text(encoding="utf-8") == "select 2 as n\n"
        assert ".old-" not in (root / "workspace.yaml").read_text(encoding="utf-8")
        seen.append(True)

    dagster.on_status = check
    run(config, backend)

    assert seen == [True, True] and not list(root.glob("*.old-*"))
    assert backend.payloads()[0]["status"] == "deployed"


def test_slow_dagster_restart_succeeds_without_rollback(config, remote, installs, dagster, monkeypatch):
    import httpx

    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    head = remote.commit("fct_x", signed(files_v(2), 2), "v2")
    dagster.calls.clear()
    dagster.executions.clear()
    failures = {verify.WORKSPACE: 5, verify.LAUNCH: 2, verify.STATUS: 3}

    def restarting(url, **kwargs):
        query = kwargs["json"]["query"]
        if failures.get(query, 0):
            failures[query] -= 1
            raise httpx.ConnectError("Dagster đang restart")
        return dagster(url, **kwargs)

    monkeypatch.setattr(verify.httpx, "post", restarting)
    backend = Backend()

    summary = run(config, backend)

    assert summary["applied"] == head and summary["unresolved"] == {}
    assert backend.payloads()[0]["status"] == "deployed"
    assert "SUCCESS" in backend.payloads()[0]["detail"] and len(dagster.launches) == 1
    assert (Path(config.deploy_dir) / "fct_x/dbt_project/models/gold/fct_x.sql").read_text(
        encoding="utf-8"
    ) == "select 2 as n\n"
    assert live_entries(config) == ["fct_x", "workspace.yaml"]
    assert not any(body["query"] == verify.TERMINATE for _, body, _ in dagster.calls)


def test_dbt_parse_diagnostics_reach_deployer_log_and_callback(config, monkeypatch, caplog):
    monkeypatch.setenv("PIPELINE_DEPLOYER_SECRET", SECRET)
    monkeypatch.setattr(service, "read_pipeline", lambda *args: (signed(files_v(1), 1), []))
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            2,
            "x" * 3000 + f"\nCompilation Error: {SECRET}",
            "y" * 3000 + "\nUnknown macro; password=private-password",
        ),
    )

    status, payload = service.apply_pipeline(config, "a" * 40, "fct_x")

    assert status == "install_failed" and payload["status"] == "failed"
    assert len(payload["detail"]) <= service.DETAIL_MAX
    for output in (caplog.text, payload["detail"]):
        assert "stdout:" in output and "stderr:" in output
        assert "Compilation Error" in output and "Unknown macro" in output
        assert SECRET not in output and "private-password" not in output
        assert "x" * 200 not in output and "y" * 200 not in output


def test_failed_verification_callback_retries_without_reinstall_or_launch(config, remote, installs, dagster):
    remote.commit("fct_x", signed(files_v(1), 1))
    dagster.statuses = ["FAILURE"]

    summary = run(config, Backend(*[503] * service.CALLBACK_ATTEMPTS))

    assert summary["pending"] == 1 and live_entries(config) == ["workspace.yaml"]
    assert service.load_state(config)["pending"][0]["payload"]["status"] == "failed"
    backend = Backend()
    assert run(config, backend)["unresolved"] == {"fct_x": "failed"}
    assert installs == ["fct_x"] and len(dagster.launches) == 1
    assert backend.payloads()[0]["status"] == "failed"
