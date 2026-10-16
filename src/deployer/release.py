"""Hộp thư release của chế độ deploy trực tiếp (ADR D6, PIPELINE_DEPLOY_MODE=direct).

Bấm Deploy sau khi Reviewer duyệt thì backend ghi một bundle bất biến vào `<release_dir>/inbox/<run_id>-v<version>/`:
đúng 4 + N file đã duyệt (cùng đường dẫn tương đối như trong `pipelines/<model>/`) và `manifest.json`
(src/deployer/manifest.py: sha256 từng file, files_hash, run_id, version, chữ ký duyệt). Ghi vào thư mục tạm
`inbox/.tmp-*` rồi rename nên deployer không bao giờ thấy bundle viết dở. Service deployer kiểm manifest, cài, báo
backend rồi chuyển bundle sang `done/` (hoặc `rejected/` khi bundle sai). Backend chỉ ghi `inbox/`.

Chỉ dùng thư viện chuẩn để deployer chạy độc lập với backend. File ghi và đọc dạng byte UTF-8 (không đổi xuống dòng)
để sha256 khớp đúng cách băm của chữ ký duyệt.
"""

import json
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from src.deployer.manifest import MANIFEST_FILE, REPORT_FILE, ManifestError, load_json, verify_report

INBOX, DONE, REJECTED = "inbox", "done", "rejected"
TEMP_PREFIX = ".tmp-"
# run_id đi vào tên thư mục: chỉ nhận ký tự an toàn trên mọi hệ file (UUID, run-a…).
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ReleaseError(RuntimeError):
    """Không ghi được bundle vào hộp thư release; thông báo tiếng Việt, an toàn để trả cho người dùng."""


def bundle_name(run_id: str, version: int) -> str:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ManifestError("run_id không hợp lệ để đặt tên bundle release.")
    if type(version) is not int or version < 1:
        raise ManifestError("version không hợp lệ để đặt tên bundle release.")
    return f"{run_id}-v{version}"


def manifest_text(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def find_bundle(release_dir: str, name: str) -> Path | None:
    """Bundle cùng tên đang ở inbox/, done/ hoặc rejected/ (đã gửi trước đó), None nếu chưa có."""
    for folder in (INBOX, DONE, REJECTED):
        path = Path(release_dir) / folder / name
        if path.is_dir():
            return path
    return None


def _same_bundle(path: Path, expected_manifest: str, manifest: dict) -> bool:
    try:
        if (path / MANIFEST_FILE).read_bytes() != expected_manifest.encode():
            return False
        report = load_json((path / REPORT_FILE).read_text(encoding="utf-8"), REPORT_FILE)
        verify_report(manifest, report)
        return True
    except (OSError, ManifestError, UnicodeDecodeError):
        return False


def write_bundle(release_dir: str, manifest: dict, files: dict[str, str], report_text: str) -> Path:
    """Ghi nguyên tử bundle của bộ file đã duyệt (manifest đã dựng bằng build_manifest cho đúng `files`).

    Gọi lại với cùng manifest thì trả bundle đã có (ở inbox/, done/ hay rejected/), không ghi lại. Đã có bundle cùng
    tên mà manifest khác thì ReleaseError: bundle là bất biến. Lỗi đĩa ra OSError.
    """
    name = bundle_name(manifest["run_id"], manifest["version"])
    expected = manifest_text(manifest)
    if not isinstance(report_text, str) or not report_text:
        raise ManifestError("run_report.json bị thiếu hoặc không hợp lệ.")
    verify_report(manifest, load_json(report_text, REPORT_FILE))
    if (existing := find_bundle(release_dir, name)) is not None:
        if _same_bundle(existing, expected, manifest):
            return existing
        raise ReleaseError(
            f"Hộp thư release đã có bundle {name} khác bản đã duyệt này (bộ file hoặc chữ ký) nên không ghi đè."
        )
    inbox = Path(release_dir) / INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    temporary = inbox / f"{TEMP_PREFIX}{uuid4().hex}"
    try:
        temporary.mkdir()
        for path, content in files.items():
            target = temporary / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode())
        (temporary / MANIFEST_FILE).write_bytes(expected.encode())
        (temporary / REPORT_FILE).write_bytes(report_text.encode())
        try:
            os.rename(temporary, inbox / name)
        except OSError:
            # Request Deploy khác vừa ghi cùng bundle: dùng bản đó nếu đúng cùng manifest.
            if (existing := find_bundle(release_dir, name)) is not None and _same_bundle(existing, expected, manifest):
                return existing
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return inbox / name


def pending_bundles(release_dir: str) -> list[Path]:
    """Bundle đang chờ trong inbox/, cũ trước (bỏ qua thư mục tạm đang ghi dở)."""
    inbox = Path(release_dir) / INBOX
    if not inbox.is_dir():
        return []
    found = [path for path in inbox.iterdir() if path.is_dir() and not path.name.startswith(".")]
    return sorted(found, key=lambda path: (path.stat().st_mtime_ns, path.name))


def read_bundle(path: Path) -> tuple[dict[str, str], list[str]]:
    """Đọc mọi file trong bundle: (file thường UTF-8 theo đường dẫn POSIX tương đối, các entry không cài được:
    symlink, file đặc biệt, file không phải UTF-8)."""
    files: dict[str, str] = {}
    irregular = []
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path).as_posix()
        if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
            irregular.append(f"{relative} (không phải file thường)")
        elif entry.is_file():
            try:
                files[relative] = entry.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                irregular.append(f"{relative} (không phải UTF-8)")
    return files, irregular


def archive(release_dir: str, path: Path, folder: str) -> Path:
    """Chuyển bundle đã xử lý sang done/ hoặc rejected/; trùng tên (gửi lại sau khi đã xử lý) thì thêm hậu tố."""
    target_dir = Path(release_dir) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.name}.{uuid4().hex[:8]}"
    os.rename(path, target)
    return target
