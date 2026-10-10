"""Manifest của một pipeline đã duyệt: bằng chứng bộ file trong pull request đúng là bộ Reviewer đã ký.

Ghi cùng commit P16 tại `pipelines/<model_name>/manifest.json` (src/codegen/deploy.py). CI của repo pipeline và
service deployer kiểm lại trước khi cài: sha256 từng file và files_hash phải khớp chữ ký duyệt. Chỉ dùng thư viện
chuẩn để deployer chạy độc lập với backend; cách băm giống hệt `src.codegen.render.file_hashes`.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath

MANIFEST_VERSION = 1
MANIFEST_FILE = "manifest.json"
REPORT_FILE = "run_report.json"
# Trùng IDENTIFIER của Spec: tên model đi vào tên nhánh và đường dẫn thư mục nên phải chặt.
MODEL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ManifestError(ValueError):
    """Manifest hoặc bộ file không khớp chữ ký duyệt; thông báo tiếng Việt, an toàn để trả cho người dùng."""


def project_dir(model_name: str) -> str:
    """Thư mục của pipeline trong repo pipeline."""
    if not MODEL_NAME.match(model_name):
        raise ManifestError(f"Tên model '{model_name}' không hợp lệ.")
    return f"pipelines/{model_name}"


def hash_files(files: dict[str, str]) -> tuple[dict[str, str], str]:
    """sha256 từng file và files_hash của cả bộ, cùng thuật toán với chữ ký duyệt."""
    hashes = {path: "sha256:" + hashlib.sha256(content.encode()).hexdigest() for path, content in sorted(files.items())}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return hashes, f"sha256:{digest}"


def _check_paths(paths) -> None:
    for path in paths:
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or "\\" in path or ".." in parts or path in (MANIFEST_FILE, REPORT_FILE):
            raise ManifestError(f"Đường dẫn file '{path}' không hợp lệ trong bộ file đã duyệt.")


def build_manifest(
    *, run_id: str, version: int, model_name: str, files: dict[str, str], reviewer: str, approved_at: datetime
) -> dict:
    """Manifest cho đúng bộ file đã duyệt (4 + N file, không gồm run_report.json và chính manifest)."""
    project_dir(model_name)
    if not files:
        raise ManifestError("Bộ file đã duyệt trống nên không dựng được manifest.")
    _check_paths(files)
    hashes, files_hash = hash_files(files)
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "version": version,
        "model_name": model_name,
        "files_hash": files_hash,
        "files": hashes,
        "reviewer": reviewer,
        "approved_at": approved_at.isoformat(),
    }


def verify_manifest(manifest: dict, files: dict[str, str]) -> None:
    """Kiểm bộ file (không gồm run_report.json, manifest.json) khớp manifest; lệch thì ManifestError."""
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestError(f"manifest_version phải là {MANIFEST_VERSION}.")
    for key in ("run_id", "model_name", "files_hash", "reviewer", "approved_at"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ManifestError(f"Manifest thiếu trường {key}.")
    if not isinstance(manifest.get("version"), int) or manifest["version"] < 1:
        raise ManifestError("Manifest thiếu version hợp lệ.")
    project_dir(manifest["model_name"])
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ManifestError("Manifest thiếu danh sách file.")
    _check_paths(expected)
    hashes, files_hash = hash_files(files)
    missing, extra = sorted(set(expected) - set(hashes)), sorted(set(hashes) - set(expected))
    if missing or extra:
        raise ManifestError(f"Bộ file khác manifest: thiếu {missing or 'không'}, thừa {extra or 'không'}.")
    changed = sorted(path for path, digest in hashes.items() if expected[path] != digest)
    if changed:
        raise ManifestError(f"sha256 không khớp manifest: {changed}.")
    if files_hash != manifest["files_hash"]:
        raise ManifestError("files_hash của bộ file không khớp chữ ký duyệt trong manifest.")
