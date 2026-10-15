"""Service deployer (SPEC §9.5 bước 4, mục 19): kéo nhánh mặc định của repo pipeline, cài lên Dagster production.

Chạy trong container riêng (deploy/compose.yml) với deploy key CHỈ ĐỌC; đây là nơi duy nhất ghi code location của
Dagster production, backend chỉ mount đọc. Mỗi chu kỳ (`run_once`):

1. Báo nốt các kết quả chưa báo được ở chu kỳ trước; còn sót thì dừng, chưa cài commit mới.
2. Fetch nhánh mặc định (HEAD của repo pipeline) vào repo bare trong volume của deployer.
3. Lấy các `pipelines/<model>/` đổi so với commit đã áp dụng gần nhất (lần đầu: mọi pipeline).
4. Mỗi pipeline: chỉ file thường theo `git ls-tree`, manifest.json khớp bộ file (manifest.py), run_report.json khớp
   chữ ký; sai thì không cài và báo failed. Đúng thì install_project (nguyên tử, có rollback); install ghi lại
   workspace.yaml, đó là tín hiệu để scripts/dagster_local.py khởi động lại Dagster nạp workspace mới. Lỗi lúc cài
   (môi trường: dbt parse, đĩa) thử lại ở chu kỳ sau, quá INSTALL_ATTEMPTS lần mới báo failed. Sau khi cài, chạy thử
   đúng một job trên Dagster: chỉ SUCCESS mới giữ bản mới; lỗi/hủy/quá hạn thì khôi phục bản trước và báo failed.
5. Báo backend từng kết quả (POST /api/v1/deployments/callback, ký HMAC theo .harness/plans/DEPLOY-PR.md); chỉ ghi
   nhận commit đã áp dụng khi mọi pipeline trong commit đã cài VÀ backend đã nhận báo `deployed`.

Kết quả từng pipeline lưu trong state.json theo sha cây git của `pipelines/<model>/` (đổi khi bất kỳ file nào đổi):
pipeline bị từ chối, cài lỗi, backend từ chối báo hay manifest không đọc được thì giữ chưa xong (commit đã áp dụng
không tiến), không cài/báo lặp lại cùng nội dung đó; nội dung mới (version mới) hoặc `--reinstall` thì xử lý lại.

Chế độ deploy trực tiếp (PIPELINE_DEPLOY_MODE=direct, mặc định; ADR D6): không dùng git, không cần repo hay khóa SSH.
Mỗi chu kỳ báo nốt kết quả chưa báo, rồi quét hộp thư release (release.py) theo thứ tự cũ trước: mỗi bundle kiểm
manifest + sha256 từng file (manifest.py), cài và chạy thử như trên, báo backend qua cùng callback ký HMAC (không có
commit_sha), rồi chuyển bundle sang done/ (bundle sai: rejected/). Kết quả lưu trong state.json theo tên bundle nên
chạy lại (kể cả khi deployer tắt trước lúc kịp chuyển bundle) không cài hay báo trùng; lỗi lúc cài giữ bundle trong
inbox/ để thử lại như trên.

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
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from uuid import uuid4

from src.deployer import install, release, verify
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
TIMESTAMP_HEADER = "X-Atomesus-Timestamp"
CALLBACK_ATTEMPTS = 3
CALLBACK_TIMEOUT_SEC = 10
# Backend đã nhận và từ chối hẳn (sai định dạng, run không khớp chữ ký): gửi lại cũng vậy nên không gửi lại, pipeline
# giữ trạng thái `refused`. Lỗi khác (mạng, 5xx, 401 sai secret, 404 backend chưa có route) giữ lại để chu kỳ sau báo.
CALLBACK_REFUSED = {400, 409, 422}
ACCEPTED, REFUSED = "accepted", "refused"
GIT_TIMEOUT_SEC = 120
DETAIL_MAX = 500
HEAD_REF = "refs/deployer/head"
REGULAR_FILE = "100644"
DEFAULT_KNOWN_HOSTS = str(Path(__file__).with_name("github_known_hosts").resolve())
INSTALL_ATTEMPTS = 3
# Trạng thái từng pipeline trong state.json. Chỉ `done` (đã cài và backend nhận `deployed`, hoặc thư mục đã bị xóa)
# là xong; `reporting` chờ báo, `retry` chờ cài lại; `failed`, `refused`, `unreported` chờ nội dung mới / --reinstall.
DONE = "done"
STATUSES = {DONE, "reporting", "retry", "failed", REFUSED, "unreported"}
DEPLOY_MODES = ("direct", "pr")


class DeployerError(RuntimeError):
    """Lỗi cấu hình hoặc git của deployer; thông báo tiếng Việt, không chứa URL repo hay secret."""


@dataclass(frozen=True)
class Config:
    repo_url: str = field(repr=False)
    secret: str = field(repr=False)
    deploy_dir: str = "data/dagster_local"
    state_dir: str = "data/deployer"
    backend_url: str = "http://backend:8000"
    ssh_key: str = field(default="", repr=False)
    known_hosts: str = field(default="", repr=False)
    interval: int = 60
    dagster_url: str = field(default="http://dagster:3070", repr=False)
    verify_timeout_sec: int = 120
    # direct: cài bundle trong hộp thư release (không git); pr: kéo nhánh mặc định của repo pipeline.
    mode: str = "direct"
    release_dir: str = "data/releases"

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        mode = env.get("PIPELINE_DEPLOY_MODE", "direct").strip().lower() or "direct"
        if mode not in DEPLOY_MODES:
            raise DeployerError("PIPELINE_DEPLOY_MODE phải là direct hoặc pr.")
        repo_url = env.get("DEPLOYER_REPO_URL", "").strip()
        secret = env.get("PIPELINE_DEPLOYER_SECRET", "")
        if mode == "pr" and not repo_url:
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
        dagster_url = env.get("DEPLOYER_DAGSTER_URL", "http://dagster:3070").strip().rstrip("/")
        try:
            parsed = urllib.parse.urlsplit(dagster_url)
            valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.port != 0
        except ValueError:
            valid_url = False
        if not valid_url:
            raise DeployerError("DEPLOYER_DAGSTER_URL phải là URL HTTP hoặc HTTPS hợp lệ của Dagster nội bộ.")
        try:
            verify_timeout_sec = int(env.get("DEPLOYER_VERIFY_TIMEOUT_SEC", "120"))
        except ValueError:
            raise DeployerError("DEPLOYER_VERIFY_TIMEOUT_SEC phải là số giây nguyên dương.") from None
        if verify_timeout_sec < 1:
            raise DeployerError("DEPLOYER_VERIFY_TIMEOUT_SEC phải từ 1 giây trở lên.")
        # Khóa SSH chỉ cần ở chế độ pr: direct không fetch git nên thiếu deploy key không chặn khởi động.
        ssh_key = env.get("DEPLOYER_SSH_KEY", "") if mode == "pr" else ""
        if ssh_key and not Path(ssh_key).is_file():
            raise DeployerError(
                "Không đọc được file khóa SSH ở DEPLOYER_SSH_KEY; kiểm tra mount chỉ đọc của deploy key."
            )
        known_hosts = env.get("DEPLOYER_KNOWN_HOSTS", "") or DEFAULT_KNOWN_HOSTS
        if mode == "pr" and not Path(known_hosts).is_file():
            raise DeployerError("Không đọc được DEPLOYER_KNOWN_HOSTS; cần file khóa host SSH đã xác minh.")
        return cls(
            repo_url=repo_url,
            secret=secret,
            deploy_dir=env.get("PIPELINE_DEPLOY_DIR", "data/dagster_local"),
            state_dir=env.get("DEPLOYER_STATE_DIR", "data/deployer"),
            backend_url=backend_url,
            ssh_key=ssh_key,
            known_hosts=known_hosts,
            interval=interval,
            dagster_url=dagster_url,
            verify_timeout_sec=verify_timeout_sec,
            mode=mode,
            release_dir=env.get("PIPELINE_RELEASE_DIR", "data/releases"),
        )


# Trạng thái ---------------------------------------------------------------------------------------------------------


def _state_path(config: Config) -> Path:
    return Path(config.state_dir) / "state.json"


def load_state(config: Config) -> dict:
    """`applied`: commit mà mọi pipeline đổi đã cài và báo xong; `pending`: callback chưa báo được, kèm sha cây của
    pipeline; `models`: kết quả từng pipeline đổi từ `applied` ({tree, status, attempts}), xóa khi commit được áp dụng."""
    path = _state_path(config)
    if not path.exists():
        return {"applied": None, "pending": [], "models": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        state = None
    valid = (
        isinstance(state, dict)
        and isinstance(state.get("applied", 0), str | None)
        and isinstance(state.get("pending"), list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("tree", 0), str | None)
            and isinstance(item.get("payload"), dict)
            for item in state["pending"]
        )
        and isinstance(state.get("models"), dict)
        and all(
            isinstance(record, dict)
            and isinstance(record.get("tree", 0), str | None)
            and record.get("status") in STATUSES
            and type(record.get("attempts")) is int
            for record in state["models"].values()
        )
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
    known_hosts = config.known_hosts or DEFAULT_KNOWN_HOSTS
    ssh = ["ssh", "-F", os.devnull, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
    ssh += ["-o", f"UserKnownHostsFile={known_hosts}", "-o", f"GlobalKnownHostsFile={os.devnull}"]
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
        # stderr của git/ssh có thể chứa URL, username hoặc đường dẫn khóa; chỉ chuyển lỗi quen thuộc sang câu cố định.
        error = done.stderr.decode("utf-8", "replace").lower()
        detail = "Không truy cập được <repo pipeline>; kiểm tra kết nối và cấu hình Git/SSH."
        if "permission denied (publickey)" in error:
            detail = "Permission denied (publickey); kiểm tra deploy key chỉ đọc."
        elif "host key verification failed" in error or "host identification has changed" in error:
            detail = "Host key verification failed; kiểm tra khóa host SSH đã ghim."
        raise DeployerError(f"git {args[0]} lỗi (mã {done.returncode}): {detail}")
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


def pipeline_trees(config: Config, head: str) -> dict[str, str]:
    """Các thư mục `pipelines/<model>/` ở commit head và sha cây git của chúng (đổi khi bất kỳ file nào bên trong đổi)."""
    listing = _git(config, "ls-tree", "-d", "-z", head, "pipelines/")
    trees = {}
    for line in listing.decode("utf-8", "replace").split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            trees[path.split("/", 1)[1]] = meta.split(" ")[2]
    return trees


def changed_models(config: Config, applied: str | None, head: str) -> list[str]:
    """Tên các thư mục `pipelines/<model>/` đổi từ commit đã áp dụng (hoặc mọi pipeline nếu chưa áp dụng commit nào)."""
    if applied and _has_commit(config, applied):
        names = _git(config, "diff", "--name-only", "-z", "--no-renames", applied, head, "--", "pipelines/")
        paths = [path.split("/") for path in names.decode("utf-8", "replace").split("\0") if path]
        return sorted({parts[1] for parts in paths if len(parts) >= 3})
    if applied:
        log.warning("Không thấy commit đã áp dụng %s trong repo (lịch sử bị viết lại?): cài lại mọi pipeline.", applied)
    return sorted(pipeline_trees(config, head))


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


def deliver(config: Config, payload: dict, post=http_post, sleep=time.sleep) -> str | None:
    """Báo một kết quả; trả ACCEPTED (backend nhận), REFUSED (từ chối hẳn) hoặc None (cần báo lại chu kỳ sau)."""
    body = _body(payload)
    timestamp = str(int(time.time()))
    signed = timestamp.encode() + b"." + body
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign(config.secret, signed),
    }
    what = f"{payload['model_name']} ({payload['status']})"
    for attempt in range(1, CALLBACK_ATTEMPTS + 1):
        try:
            status = post(config.backend_url + CALLBACK_PATH, body, headers)
        except (OSError, HTTPException) as exc:
            status, reason = None, type(exc).__name__
        else:
            reason = f"HTTP {status}"
        if status is not None and 200 <= status < 300:
            return ACCEPTED
        if status in CALLBACK_REFUSED:
            log.error("Backend từ chối kết quả %s (%s); không gửi lại, pipeline giữ chưa xong.", what, reason)
            return REFUSED
        log.warning("Báo backend kết quả %s lỗi (%s), lần %d/%d.", what, reason, attempt, CALLBACK_ATTEMPTS)
        if attempt < CALLBACK_ATTEMPTS:
            sleep(2 ** (attempt - 1))
    return None


def _flush(config: Config, state: dict, post, sleep) -> bool:
    """Báo mọi kết quả đang chờ và ghi phản hồi của backend vào kết quả từng pipeline; True nếu đã báo hết."""
    remaining = []
    for item in state["pending"]:
        answer = deliver(config, item["payload"], post, sleep)
        if answer is None:
            remaining.append(item)
            continue
        record = state["models"].get(item["payload"]["model_name"])
        if record is not None and record["tree"] == item["tree"] and record["status"] == "reporting":
            deployed = item["payload"]["status"] == "deployed"
            record["status"] = REFUSED if answer == REFUSED else DONE if deployed else "failed"
    state["pending"] = remaining
    save_state(config, state)
    return not remaining


# Cài đặt ------------------------------------------------------------------------------------------------------------


def _payload(manifest: dict, model: str, head: str | None, status: str, detail: str) -> dict:
    # head None: bundle của hộp thư release, không có commit git (callback nhận commit_sha null).
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


def apply_pipeline(config: Config, head: str, model: str) -> tuple[str, dict | None]:
    """Kiểm rồi cài một pipeline. Trả (kết quả, callback cần báo): `deployed`; `deleted` (thư mục đã bị xóa, không có
    gì để cài); `rejected` (bộ file / manifest sai; callback None nếu manifest không có danh tính để báo);
    `install_failed` (lỗi lúc cài, đã giữ bản đang chạy); `verify_failed` (chạy thử lỗi, đã quay về bản trước)."""
    manifest_text = None
    try:
        read = read_pipeline(config, head, model)
        if read is None:
            log.warning("pipelines/%s/ đã bị xóa khỏi repo pipeline; giữ nguyên code location đang chạy.", model)
            return "deleted", None
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
            return "rejected", None
        return "rejected", _payload(identity, model, head, "failed", f"Manifest không hợp lệ: {exc}")
    return _install_checked(config, model, files, manifest, head, f"commit {head[:12]}")


def _install_checked(
    config: Config, model: str, files: dict[str, str], manifest: dict, commit_sha: str | None, source: str
) -> tuple[str, dict]:
    """Cài bộ file đã qua kiểm manifest rồi chạy thử mức D2; `source` (commit / bundle) chỉ để ghi log và callback."""
    dagster_run_id = None

    def confirm(_target: Path) -> None:
        nonlocal dagster_run_id
        dagster_run_id = verify.verify_pipeline(config.dagster_url, model, config.verify_timeout_sec)

    try:
        target = install.install_project(model, files, config.deploy_dir, verify=confirm)
        if target is None:
            raise OSError("Cài project không trả về code location.")
    except verify.VerificationError as exc:
        detail = f"{exc} Đã quay về bản trước; lần cài đầu thì gỡ code location."
        log.error("Chạy thử %s lỗi: %s", model, detail)
        return "verify_failed", _payload(manifest, model, commit_sha, "failed", detail)
    except (OSError, subprocess.SubprocessError, ManifestError) as exc:
        log.error("Cài %s lỗi, giữ bản đang chạy: %s", model, exc)
        return "install_failed", _payload(
            manifest, model, commit_sha, "failed", f"Cài lên Dagster lỗi, giữ bản đang chạy: {exc}"
        )
    log.info("Đã cài %s v%s (run %s) từ %s.", model, manifest["version"], manifest["run_id"], source)
    return "deployed", _payload(
        manifest, model, commit_sha, "deployed", f"Chạy thử Dagster SUCCESS (run {dagster_run_id}), {source}."
    )


def _process(config: Config, state: dict, head: str, model: str, tree: str | None) -> bool:
    """Kiểm và cài một pipeline đổi, ghi kết quả vào state, callback cần báo vào hàng chờ. False nếu nội dung này đã xử
    lý rồi (xong, hoặc đang chờ nội dung mới / --reinstall): không cài và báo lặp lại."""
    record = state["models"].get(model)
    same = record is not None and record["tree"] == tree
    if same and record["status"] != "retry":
        return False
    attempts = record["attempts"] + 1 if same else 1
    outcome, payload = apply_pipeline(config, head, model)
    if outcome == "install_failed" and attempts < INSTALL_ATTEMPTS:
        log.warning("Sẽ cài lại %s ở chu kỳ sau (lần %d/%d); chưa báo backend.", model, attempts, INSTALL_ATTEMPTS)
        status = "retry"
    elif payload is None:
        status = DONE if outcome == "deleted" else "unreported"
    else:
        status = "reporting"
        state["pending"].append({"tree": tree, "payload": payload})
    state["models"][model] = {"tree": tree, "status": status, "attempts": attempts}
    return True


def apply_bundle(
    config: Config, name: str, files: dict[str, str], irregular: list[str], manifest_text: str | None
) -> tuple[str, dict | None]:
    """Kiểm rồi cài một bundle của hộp thư release (`files` không gồm manifest.json). Kết quả như apply_pipeline:
    `deployed`, `rejected` (callback None nếu manifest không có danh tính để báo), `install_failed`, `verify_failed`."""
    try:
        if irregular:
            raise ManifestError(f"Chỉ cài file thường UTF-8; bundle {name} có {', '.join(irregular)}.")
        if manifest_text is None:
            raise ManifestError(f"Thiếu {MANIFEST_FILE} trong bundle {name}.")
        manifest = load_json(manifest_text, MANIFEST_FILE)
        verify_manifest(manifest, files)
        if release.bundle_name(manifest["run_id"], manifest["version"]) != name:
            raise ManifestError(
                f"Manifest ghi run {manifest['run_id']} v{manifest['version']} nhưng bundle tên {name}."
            )
    except ManifestError as exc:
        log.error("Không cài bundle %s: %s", name, exc)
        identity = _identity(manifest_text)
        if identity is None:
            log.error(
                "Manifest của bundle %s không có run_id/version/files_hash hợp lệ nên không báo được backend.", name
            )
            return "rejected", None
        model = identity["model_name"]
        return "rejected", _payload(identity, model, None, "failed", f"Manifest không hợp lệ: {exc}")
    return _install_checked(config, manifest["model_name"], files, manifest, None, f"bundle {name}")


def _process_bundle(config: Config, state: dict, path: Path) -> tuple[str | None, bool]:
    """Kiểm và cài một bundle, ghi kết quả vào state (theo model, `tree` là tên bundle), callback vào hàng chờ.

    Trả (thư mục chuyển bundle tới: done / rejected, None nếu giữ trong inbox để cài lại; đã kiểm/cài ở lần gọi này
    chưa). Bundle đã xử lý rồi (deployer dừng trước khi kịp chuyển) thì chỉ trả thư mục, không cài và báo lặp lại.
    """
    files, irregular = release.read_bundle(path)
    manifest_text = files.pop(MANIFEST_FILE, None)
    identity = _identity(manifest_text)
    model = identity["model_name"] if identity else None
    record = state["models"].get(model) if model else None
    same = record is not None and record["tree"] == path.name
    if same and record["status"] != "retry":
        return record.get("folder", release.DONE), False
    attempts = record["attempts"] + 1 if same else 1
    outcome, payload = apply_bundle(config, path.name, files, irregular, manifest_text)
    folder = release.REJECTED if outcome == "rejected" else release.DONE
    if outcome == "install_failed" and attempts < INSTALL_ATTEMPTS:
        log.warning(
            "Sẽ cài lại bundle %s ở chu kỳ sau (lần %d/%d); chưa báo backend.", path.name, attempts, INSTALL_ATTEMPTS
        )
        status, folder = "retry", None
    elif payload is None:
        status = "unreported"
    else:
        status = "reporting"
        state["pending"].append({"tree": path.name, "payload": payload})
    if model:
        record = {"tree": path.name, "status": status, "attempts": attempts, "folder": folder or release.DONE}
        state["models"][model] = record
    return folder, True


def run_inbox(config: Config, *, post=http_post, sleep=time.sleep) -> dict:
    """Một chu kỳ ở chế độ direct: cài các bundle trong hộp thư release. Tóm tắt cùng dạng run_once: `head` luôn None,
    `models` là bundle vừa kiểm/cài, `unresolved` là bundle còn chờ cài lại ({tên bundle: "retry"})."""
    state = load_state(config)
    if not (Path(config.deploy_dir) / "workspace.yaml").exists():
        install.write_workspace(config.deploy_dir)
    if not _flush(config, state, post, sleep):
        log.warning("Còn %d kết quả chưa báo được backend; chưa cài bundle mới.", len(state["pending"]))
        return {"head": None, "applied": None, "models": [], "pending": len(state["pending"]), "unresolved": {}}
    processed, unresolved = [], {}
    for path in release.pending_bundles(config.release_dir):
        folder, checked = _process_bundle(config, state, path)
        save_state(config, state)  # callback chờ báo được lưu trước khi chuyển bundle: tắt giữa chừng thì lần sau báo
        if checked:
            processed.append(path.name)
        if folder is None:
            unresolved[path.name] = "retry"
        else:
            release.archive(config.release_dir, path, folder)
    _flush(config, state, post, sleep)
    return {
        "head": None,
        "applied": None,
        "models": processed,
        "pending": len(state["pending"]),
        "unresolved": unresolved,
    }


def run_once(config: Config, *, reinstall: bool = False, post=http_post, sleep=time.sleep) -> dict:
    """Một chu kỳ deployer; trả tóm tắt để log và test: `head`, `applied`, `models` (pipeline vừa kiểm/cài ở chu kỳ này),
    `pending` (số callback chưa báo được), `unresolved` (pipeline đổi chưa xong: {model: trạng thái}).

    Chế độ direct chạy run_inbox (không git; `reinstall` không áp dụng vì bundle đã cài được chuyển sang done/)."""
    if config.mode == "direct":
        return run_inbox(config, post=post, sleep=sleep)
    state = load_state(config)
    if not (Path(config.deploy_dir) / "workspace.yaml").exists():
        install.write_workspace(config.deploy_dir)  # Dagster cần workspace.yaml (rỗng) ngay cả khi chưa có pipeline
    if not _flush(config, state, post, sleep):
        log.warning("Còn %d kết quả chưa báo được backend; chưa cài commit mới.", len(state["pending"]))
        unresolved = {model: record["status"] for model, record in state["models"].items() if record["status"] != DONE}
        return {
            "head": None,
            "applied": state["applied"],
            "models": [],
            "pending": len(state["pending"]),
            "unresolved": unresolved,
        }
    head = fetch(config)
    if reinstall:
        state["models"] = {}
    applied = None if reinstall else state["applied"]
    if head == applied:
        return {"head": head, "applied": applied, "models": [], "pending": 0, "unresolved": {}}
    changed, trees = changed_models(config, applied, head), pipeline_trees(config, head)
    models = [model for model in changed if _process(config, state, head, model, trees.get(model))]
    save_state(config, state)  # callback chờ báo được lưu trước khi gửi: deployer tắt giữa chừng thì lần sau báo tiếp
    _flush(config, state, post, sleep)
    unresolved = {}
    for model in changed:
        record = state["models"].get(model)
        if record is None or record["tree"] != trees.get(model) or record["status"] != DONE:
            unresolved[model] = record["status"] if record else "chưa xử lý"
    if not unresolved:
        state.update(applied=head, models={})
        save_state(config, state)
    elif models:
        log.warning("Chưa ghi nhận commit %s: pipeline chưa cài và báo xong %s.", head[:12], unresolved)
    return {
        "head": head,
        "applied": state["applied"],
        "models": models,
        "pending": len(state["pending"]),
        "unresolved": unresolved,
    }
