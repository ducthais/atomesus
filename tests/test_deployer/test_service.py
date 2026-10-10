"""Service deployer chạy offline: một repo git cục bộ đóng vai repo pipeline, backend là hàm post giả."""

import hashlib
import hmac
import json
import subprocess
import threading
import urllib.error
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.deployer import install, service
from src.deployer.manifest import build_manifest
from src.deployer.service import CALLBACK_PATH, SIGNATURE_HEADER, Config, DeployerError

SECRET = "khoa-ky-callback-cho-test"


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
def installs(monkeypatch):
    """Cài thật (scaffold dbt + bộ file), chỉ bỏ bước dbt parse; ghi lại mỗi lần install_project được gọi."""
    monkeypatch.setattr(install, "_compile_manifest", lambda _root: None)
    calls = []
    real = install.install_project

    def recording(model, files, deploy_dir):
        calls.append(model)
        return real(model, files, deploy_dir)

    monkeypatch.setattr(install, "install_project", recording)
    return calls


def run(config, backend, **kwargs):
    return service.run_once(config, post=backend, sleep=lambda _seconds: None, **kwargs)


def live_entries(config) -> list[str]:
    return sorted(path.name for path in Path(config.deploy_dir).iterdir())


def test_first_run_installs_pipeline_and_reports_signed_callback(config, remote, installs):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    backend = Backend()

    summary = run(config, backend)

    assert summary == {"head": head, "applied": head, "models": ["fct_x"], "pending": 0}
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
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    tampered = signed(files_v(2), 2)
    tampered["dbt_project/models/gold/fct_x.sql"] = "select 'sửa sau khi duyệt' as n\n"
    head = remote.commit("fct_x", tampered, "sửa tay")
    backend = Backend()

    summary = run(config, backend)

    assert installs == ["fct_x"], "bộ file lệch manifest thì không gọi install"
    sql = Path(config.deploy_dir) / "fct_x/dbt_project/models/gold/fct_x.sql"
    assert sql.read_text(encoding="utf-8") == "select 1 as n\n"
    [payload] = backend.payloads()
    assert payload["status"] == "failed" and payload["version"] == 2 and "sha256" in payload["detail"]
    assert summary["applied"] == head, "đã báo failed: commit coi như đã xử lý"


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
    head = remote.commit("fct_x", content)
    backend = Backend()

    summary = run(config, backend)

    assert installs == [] and backend.calls == [] and summary["applied"] == head


def test_install_failure_reports_failed_and_keeps_previous(config, remote, installs, monkeypatch):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    remote.commit("fct_x", signed(files_v(2), 2), "v2")

    def broken(_root):
        raise OSError("dbt parse thất bại")

    monkeypatch.setattr(install, "_compile_manifest", broken)
    backend = Backend()

    run(config, backend)

    sql = Path(config.deploy_dir) / "fct_x/dbt_project/models/gold/fct_x.sql"
    assert sql.read_text(encoding="utf-8") == "select 1 as n\n"
    assert live_entries(config) == ["fct_x", "workspace.yaml"]
    [payload] = backend.payloads()
    assert payload["status"] == "failed" and "dbt parse" in payload["detail"]


def test_failed_callback_is_retried_next_cycle_before_commit_is_applied(config, remote, installs):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    down = Backend(*[urllib.error.URLError("connection refused")] * service.CALLBACK_ATTEMPTS)

    summary = run(config, down)

    assert summary == {"head": head, "applied": None, "models": ["fct_x"], "pending": 1}
    assert down.calls == [] and installs == ["fct_x"]
    state = service.load_state(config)
    assert state["installed"] == head and state["applied"] is None

    backend = Backend()
    summary = run(config, backend)

    assert installs == ["fct_x"], "đã cài rồi: lần sau chỉ báo lại, không cài lại"
    assert [p["status"] for p in backend.payloads()] == ["deployed"] and summary["applied"] == head
    assert service.load_state(config) == {"applied": head, "installed": None, "pending": []}


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


def test_refused_callback_is_not_retried_forever(config, remote, installs):
    head = remote.commit("fct_x", signed(files_v(1), 1))
    backend = Backend(409)

    summary = run(config, backend)

    assert summary["applied"] == head and summary["pending"] == 0 and backend.calls == []


def test_deleted_pipeline_is_skipped(config, remote, installs):
    remote.commit("fct_x", signed(files_v(1), 1))
    run(config, Backend())
    remote.git("rm", "-r", "-q", "pipelines/fct_x")
    remote.git("commit", "-q", "-m", "xóa")
    backend = Backend()

    summary = run(config, backend)

    assert summary["models"] == ["fct_x"] and backend.calls == [] and installs == ["fct_x"]
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


def test_corrupt_state_is_reported_not_silently_reset(config):
    service.save_state(config, {"applied": None, "installed": None, "pending": []})
    service._state_path(config).write_text("{hỏng", encoding="utf-8")

    with pytest.raises(DeployerError, match="state.json"):
        service.load_state(config)
