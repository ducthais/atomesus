"""Service deployer (SPEC §9.5 bước 4, mục 19): kéo nhánh mặc định của repo pipeline, cài lên Dagster production.

Chạy trong container riêng (deploy/compose.yml) với deploy key CHỈ ĐỌC; đây là nơi duy nhất ghi code location của
Dagster production, backend chỉ mount đọc. Mỗi chu kỳ (`run_once`):

1. Báo nốt các kết quả chưa báo được ở chu kỳ trước; còn sót thì dừng, chưa cài commit mới.
2. Fetch nhánh mặc định (HEAD của repo pipeline) vào repo bare trong volume của deployer.
3. Lấy các `pipelines/<model>/` đổi so với commit đã áp dụng gần nhất (lần đầu: mọi pipeline).
4. Mỗi pipeline: chỉ file thường theo `git ls-tree`, manifest.json khớp bộ file (manifest.py), run_report.json khớp
   chữ ký; sai thì không cài và báo failed. Đúng thì install_project (nguyên tử, có rollback); install ghi lại
   workspace.yaml, đó là tín hiệu để scripts/dagster_local.py khởi động lại Dagster nạp workspace mới.
5. Báo backend từng kết quả (POST /api/v1/deployments/callback, ký HMAC theo .harness/plans/DEPLOY-PR.md); chỉ ghi
   nhận commit đã áp dụng khi mọi pipeline trong commit đã cài và báo xong.

URL repo, khóa SSH và secret chỉ đọc từ biến môi trường, không bao giờ ghi ra log.
"""

import hashlib
import hmac
import json
import logging
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from uuid import uuid4

from src.deployer import install
from src.deployer.manifest import (
    MANIFEST_FILE,
    REPORT_FILE,
    ManifestError,
    load_json,
    verify_manifest,
    verify_report,
)

log = logging.getLogger(__name__)

CALLBACK_PATH = "/api/v1/deployments/callback"
SIGNATURE_HEADER = "X-Atomesus-Signature"
CALLBACK_ATTEMPTS = 3
CALLBACK_TIMEOUT_SEC = 10
# Backend đã nhận và từ chối hẳn (sai định dạng, run không khớp chữ ký): gửi lại cũng vậy nên bỏ, ghi log.
# Lỗi khác (mạng, 5xx, 401 sai secret, 404 backend chưa có route) giữ lại để chu kỳ sau báo tiếp.
CALLBACK_REFUSED = {400, 409, 422}
GIT_TIMEOUT_SEC = 120
DETAIL_MAX = 500
HEAD_REF = "refs/deployer/head"
REGULAR_FILE = "100644"


class DeployerError(RuntimeError):
    """Lỗi cấu hình hoặc git của deployer; thông báo tiếng Việt, không chứa URL repo hay secret."""


@dataclass(frozen=True)
class Config:
    repo_url: str = field(repr=False)
    secret: str = field(repr=False)
    deploy_dir: str = "data/dagster_local"
    state_dir: str = "data/deployer"
    backend_url: str = "http://backend:8000"
    ssh_key: str = ""
    known_hosts: str = ""
    interval: int = 60

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        repo_url = env.get("DEPLOYER_REPO_URL", "").strip()
        secret = env.get("PIPELINE_DEPLOYER_SECRET", "")
        if not repo_url:
            raise DeployerError("Thiếu DEPLOYER_REPO_URL (URL SSH của repo pipeline).")
        if not secret:
            raise DeployerError("Thiếu PIPELINE_DEPLOYER_SECRET (khóa ký callback, cùng giá trị với backend).")
        backend_url = env.get("DEPLOYER_BACKEND_URL", "http://backend:8000").rstrip("/")
        if not backend_url.startswith(("http://", "https://")):
            raise DeployerError("DEPLOYER_BACKEND_URL phải bắt đầu bằng http:// hoặc https://.")
        try:
            interval = int(env.get("DEPLOYER_INTERVAL_SEC", "60"))
        except ValueError:
            raise DeployerError("DEPLOYER_INTERVAL_SEC phải là số giây nguyên.") from None
        if interval < 5:
            raise DeployerError("DEPLOYER_INTERVAL_SEC phải từ 5 giây trở lên.")
        ssh_key = env.get("DEPLOYER_SSH_KEY", "")
        if ssh_key and not Path(ssh_key).is_file():
            raise DeployerError(
                "Không đọc được file khóa SSH ở DEPLOYER_SSH_KEY; kiểm tra mount chỉ đọc của deploy key."
            )
        return cls(
            repo_url=repo_url,
            secret=secret,
            deploy_dir=env.get("PIPELINE_DEPLOY_DIR", "data/dagster_local"),
            state_dir=env.get("DEPLOYER_STATE_DIR", "data/deployer"),
            backend_url=backend_url,
            ssh_key=ssh_key,
            known_hosts=env.get("DEPLOYER_KNOWN_HOSTS", ""),
            interval=interval,
        )


# Trạng thái ---------------------------------------------------------------------------------------------------------


def _state_path(config: Config) -> Path:
    return Path(config.state_dir) / "state.json"


def load_state(config: Config) -> dict:
    """`applied`: commit đã cài và báo xong; `installed`: commit đã cài, còn kết quả chờ báo; `pending`: callback chưa báo."""
    path = _state_path(config)
    if not path.exists():
        return {"applied": None, "installed": None, "pending": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        state = None
    valid = (
        isinstance(state, dict)
        and all(isinstance(state.get(key), str | None) for key in ("applied", "installed"))
        and isinstance(state.get("pending"), list)
        and all(isinstance(item, dict) for item in state["pending"])
    )
    if not valid:
        raise DeployerError(f"{path} hỏng. Xóa file này để deployer cài lại mọi pipeline ở commit mới nhất.")
    return state


def save_state(config: Config, state: dict) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".state-{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


# Git ----------------------------------------------------------------------------------------------------------------


def _git_env(config: Config) -> dict[str, str]:
    known_hosts = config.known_hosts or str(Path(config.state_dir).resolve() / "known_hosts")
    # Có file known_hosts cấu hình sẵn thì chỉ tin đúng host trong đó; chưa có thì tin lần đầu và ghi vào volume.
    checking = "yes" if config.known_hosts else "accept-new"
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", f"StrictHostKeyChecking={checking}"]
    ssh += ["-o", f"UserKnownHostsFile={known_hosts}"]
    if config.ssh_key:
        ssh += ["-i", config.ssh_key, "-o", "IdentitiesOnly=yes"]
    return {**os.environ, "GIT_SSH_COMMAND": shlex.join(ssh), "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _git(config: Config, *args: str) -> bytes:
    repo = Path(config.state_dir) / "repo.git"
    command = ["git", "--git-dir", str(repo), *args]
    try:
        done = subprocess.run(command, capture_output=True, env=_git_env(config), timeout=GIT_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired:
        raise DeployerError(f"git {args[0]} quá {GIT_TIMEOUT_SEC} giây.") from None
    except OSError as exc:
        raise DeployerError(f"Không chạy được git ({type(exc).__name__}); image cần có git và ssh.") from None
    if done.returncode:
        detail = done.stderr.decode("utf-8", "replace").replace(config.repo_url, "<repo pipeline>").strip()
        raise DeployerError(f"git {args[0]} lỗi (mã {done.returncode}): {detail[-DETAIL_MAX:]}")
    return done.stdout


def fetch(config: Config) -> str:
    """Kéo nhánh mặc định (HEAD) của repo pipeline, trả sha commit đầu nhánh."""
    repo = Path(config.state_dir) / "repo.git"
    if not (repo / "HEAD").exists():
        repo.mkdir(parents=True, exist_ok=True)
        _git(config, "init", "--bare", "--quiet")
    _git(config, "fetch", "--quiet", "--no-tags", "--force", config.repo_url, f"+HEAD:{HEAD_REF}")
    return _git(config, "rev-parse", "--verify", f"{HEAD_REF}^{{commit}}").decode().strip()


def _has_commit(config: Config, sha: str) -> bool:
    try:
        _git(config, "cat-file", "-e", f"{sha}^{{commit}}")
    except DeployerError:
        return False
    return True


def changed_models(config: Config, applied: str | None, head: str) -> list[str]:
    """Tên các thư mục `pipelines/<model>/` đổi từ commit đã áp dụng (hoặc mọi pipeline nếu chưa áp dụng commit nào)."""
    if applied and _has_commit(config, applied):
        names = _git(config, "diff", "--name-only", "-z", "--no-renames", applied, head, "--", "pipelines/")
        paths = [path.split("/") for path in names.decode("utf-8", "replace").split("\0") if path]
        return sorted({parts[1] for parts in paths if len(parts) >= 3})
    if applied:
        log.warning("Không thấy commit đã áp dụng %s trong repo (lịch sử bị viết lại?): cài lại mọi pipeline.", applied)
    names = _git(config, "ls-tree", "-d", "-z", "--name-only", head, "pipelines/")
    return sorted(path.split("/", 1)[1] for path in names.decode("utf-8", "replace").split("\0") if path)


def read_pipeline(config: Config, head: str, model: str) -> tuple[dict[str, str], list[str]] | None:
    """Đọc `pipelines/<model>/` ở commit head: (file thường 100644 theo git ls-tree, các entry không cài được: symlink,
    submodule, file thực thi, file không phải UTF-8); None nếu thư mục đã bị xóa."""
    folder = f"pipelines/{model}/"
    listing = _git(config, "ls-tree", "-r", "-z", "--full-tree", head, "--", folder)
    entries = [line for line in listing.decode("utf-8", "replace").split("\0") if line]
    if not entries:
        return None
    files: dict[str, str] = {}
    irregular = []
    for line in entries:
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split(" ")
        relative = path.removeprefix(folder)
        if mode != REGULAR_FILE or kind != "blob":
            irregular.append(f"{relative} (mode {mode})")
            continue
        try:
            files[relative] = _git(config, "cat-file", "blob", sha).decode("utf-8")
        except UnicodeDecodeError:
            irregular.append(f"{relative} (không phải UTF-8)")
    return files, irregular


# Callback -----------------------------------------------------------------------------------------------------------


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()


def http_post(url: str, body: bytes, headers: dict[str, str]) -> int:
    """POST, trả mã HTTP; lỗi mạng ra OSError hoặc HTTPException."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=CALLBACK_TIMEOUT_SEC) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def deliver(config: Config, payload: dict, post=http_post, sleep=time.sleep) -> bool:
    """Báo một kết quả; True khi backend đã nhận (hoặc từ chối hẳn), False khi cần báo lại chu kỳ sau."""
    body = _body(payload)
    headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(config.secret, body)}
    what = f"{payload['model_name']} ({payload['status']})"
    for attempt in range(1, CALLBACK_ATTEMPTS + 1):
        try:
            status = post(config.backend_url + CALLBACK_PATH, body, headers)
        except (OSError, HTTPException) as exc:
            status, reason = None, type(exc).__name__
        else:
            reason = f"HTTP {status}"
        if status is not None and 200 <= status < 300:
            return True
        if status in CALLBACK_REFUSED:
            log.error("Backend từ chối kết quả %s (%s); bỏ, không gửi lại.", what, reason)
            return True
        log.warning("Báo backend kết quả %s lỗi (%s), lần %d/%d.", what, reason, attempt, CALLBACK_ATTEMPTS)
        if attempt < CALLBACK_ATTEMPTS:
            sleep(2 ** (attempt - 1))
    return False


def _flush(config: Config, state: dict, post, sleep) -> bool:
    """Báo mọi kết quả đang chờ; báo xong hết thì commit đã cài thành commit đã áp dụng."""
    remaining = [payload for payload in state["pending"] if not deliver(config, payload, post, sleep)]
    state["pending"] = remaining
    if not remaining and state["installed"]:
        state["applied"], state["installed"] = state["installed"], None
    save_state(config, state)
    return not remaining


# Cài đặt ------------------------------------------------------------------------------------------------------------


def _payload(manifest: dict, model: str, head: str, status: str, detail: str) -> dict:
    return {
        "run_id": manifest["run_id"],
        "version": manifest["version"],
        "files_hash": manifest["files_hash"],
        "model_name": manifest["model_name"],
        "commit_sha": head,
        "status": status,
        "detail": detail[:DETAIL_MAX],
        "at": datetime.now(UTC).isoformat(),
    }


def _identity(text: str | None) -> dict | None:
    """Các trường của hợp đồng callback lấy từ manifest; manifest hỏng tới mức thiếu chúng thì không báo được."""
    try:
        manifest = load_json(text or "", MANIFEST_FILE)
    except ManifestError:
        return None
    usable = (
        all(isinstance(manifest.get(key), str) for key in ("run_id", "files_hash", "model_name"))
        and type(manifest.get("version")) is int
    )
    return manifest if usable else None


def apply_pipeline(config: Config, head: str, model: str) -> dict | None:
    """Kiểm rồi cài một pipeline; trả callback cần báo (None nếu thư mục đã bị xóa hoặc manifest không đọc được)."""
    manifest_text = None
    try:
        read = read_pipeline(config, head, model)
        if read is None:
            log.warning("pipelines/%s/ đã bị xóa khỏi repo pipeline; giữ nguyên code location đang chạy.", model)
            return None
        files, irregular = read
        manifest_text, report_text = files.pop(MANIFEST_FILE, None), files.pop(REPORT_FILE, None)
        if irregular:
            raise ManifestError(f"Chỉ cài file thường UTF-8; pipelines/{model}/ có {', '.join(irregular)}.")
        if manifest_text is None or report_text is None:
            raise ManifestError(f"Thiếu {MANIFEST_FILE} hoặc {REPORT_FILE} trong pipelines/{model}/.")
        manifest = load_json(manifest_text, MANIFEST_FILE)
        verify_manifest(manifest, files)
        if manifest["model_name"] != model:
            raise ManifestError(f"Manifest ghi model {manifest['model_name']} nhưng nằm ở pipelines/{model}/.")
        verify_report(manifest, load_json(report_text, REPORT_FILE))
    except ManifestError as exc:
        log.error("Không cài %s ở commit %s: %s", model, head[:12], exc)
        identity = _identity(manifest_text)
        if identity is None:
            log.error("Manifest của %s không có run_id/version/files_hash hợp lệ nên không báo được backend.", model)
            return None
        return _payload(identity, model, head, "failed", f"Manifest không hợp lệ: {exc}")
    try:
        install.install_project(model, files, config.deploy_dir)
    except (OSError, subprocess.SubprocessError, ManifestError) as exc:
        log.error("Cài %s lỗi, giữ bản đang chạy: %s", model, exc)
        return _payload(manifest, model, head, "failed", f"Cài lên Dagster lỗi, giữ bản đang chạy: {exc}")
    log.info("Đã cài %s v%s (run %s) từ commit %s.", model, manifest["version"], manifest["run_id"], head[:12])
    return _payload(manifest, model, head, "deployed", f"Đã cài lên Dagster production từ commit {head[:12]}.")


def run_once(config: Config, *, reinstall: bool = False, post=http_post, sleep=time.sleep) -> dict:
    """Một chu kỳ deployer; trả tóm tắt {head, applied, models, pending} để log và test."""
    state = load_state(config)
    if not (Path(config.deploy_dir) / "workspace.yaml").exists():
        install.write_workspace(config.deploy_dir)  # Dagster cần workspace.yaml (rỗng) ngay cả khi chưa có pipeline
    if not _flush(config, state, post, sleep):
        log.warning("Còn %d kết quả chưa báo được backend; chưa cài commit mới.", len(state["pending"]))
        return {"head": None, "applied": state["applied"], "models": [], "pending": len(state["pending"])}
    head = fetch(config)
    applied = None if reinstall else state["applied"]
    if head == applied:
        return {"head": head, "applied": applied, "models": [], "pending": 0}
    models = changed_models(config, applied, head)
    log.info("Commit %s: %d pipeline đổi %s.", head[:12], len(models), models)
    payloads = [payload for model in models if (payload := apply_pipeline(config, head, model))]
    state.update(installed=head, pending=payloads)
    save_state(config, state)
    _flush(config, state, post, sleep)
    return {"head": head, "applied": state["applied"], "models": models, "pending": len(state["pending"])}
