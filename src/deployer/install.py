"""Cài một pipeline đã duyệt vào thư mục code location của Dagster.

Mỗi pipeline một thư mục `<deploy_dir>/<model_name>/`, gồm scaffold dbt + đúng bộ file Reviewer đã duyệt. Cài lại
cùng model thì thay nguyên tử toàn bộ thư mục; lỗi giữa chừng thì khôi phục bản đang chạy. Mỗi lần đổi bộ project (cài,
rollback) ghi `<deploy_dir>/.generation`: supervisor Dagster nạp lại code location hợp nhất, không khởi động lại
(scripts/dagster_local.py). Dùng cho service deployer (cài từ bundle hoặc nhánh main của repo pipeline sau khi đã kiểm
manifest).
"""

import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, quote_plus
from uuid import uuid4

from src.codegen.sandbox import PASSTHROUGH_ENV, prepare_project
from src.dagster_runtime import GENERATION_FILE
from src.deployer.manifest import project_dir
from src.deployer.verify import WORKSPACE_UPDATED_AFTER, VerificationError
from src.pipeline.settings import warehouse_file

DBT_PARSE_TIMEOUT_SEC = 120
DBT_OUTPUT_TAIL = 140
SECRET_KEY = r"password|passwd|pwd|secret|token|api[_-]?key|authorization|credentials|private[_-]?key|access[_-]?key"


class DbtParseTimeoutError(subprocess.TimeoutExpired):
    """Giữ kiểu lỗi timeout cũ nhưng chỉ đưa output đã che secret vào thông báo."""

    def __init__(self, detail: str):
        super().__init__("dbt parse", DBT_PARSE_TIMEOUT_SEC)
        self.detail = detail

    def __str__(self) -> str:
        return f"dbt parse quá {DBT_PARSE_TIMEOUT_SEC} giây. {self.detail}".strip()


def write_generation(deploy_dir: str) -> Path:
    """Ghi `<deploy_dir>/.generation` (`<epoch giây>.<uuid>`, ghi nguyên tử) sau mỗi lần đổi bộ project đã cài.

    Supervisor Dagster (scripts/dagster_local.py) thấy file đổi thì ghi credential của các pipeline rồi nạp lại code
    location hợp nhất qua GraphQL: không khởi động lại Dagster, run đang chạy không bị dừng (lỗi #4).
    """
    root = Path(deploy_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / GENERATION_FILE
    temporary = root / f".generation-{uuid4().hex}.tmp"
    temporary.write_text(f"{time.time():.6f}.{uuid4().hex}\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _parse_detail(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    """Che secret trước khi cắt đuôi output; giữ cả hai luồng trong giới hạn detail callback (500 ký tự)."""
    parts = []
    for name, output in (("stdout", stdout), ("stderr", stderr)):
        text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output or ""
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        values = {
            value
            for key, value in os.environ.items()
            if value and re.search(SECRET_KEY + r"|url|ssh_key", key, re.IGNORECASE)
        }
        for value in sorted(values, key=len, reverse=True):
            for variant in {value, quote(value, safe=""), quote_plus(value)}:
                text = text.replace(variant, "***")
        text = re.sub(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)", "***", text, flags=re.DOTALL
        )
        text = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]+", "***", text)
        text = re.sub(r"\b(?:Bearer|Basic)\s+\S+", "***", text, flags=re.IGNORECASE)
        text = re.sub(
            rf"((?:{SECRET_KEY})[\w-]*[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
            r"\1***",
            text,
            flags=re.IGNORECASE,
        )
        text = " ".join(text.split())
        if text:
            parts.append(f"{name}: {text[-DBT_OUTPUT_TAIL:]}")
    return "; ".join(parts)


def _compile_manifest(project_root: Path) -> None:
    """Biên dịch trước manifest.json cho dbt để Dagster nạp được code location ngay."""
    dbt_dir = project_root / "dbt_project"
    warehouse = warehouse_file(Path("data/warehouse").resolve(), project_root.name)  # parse không mở file này
    env = {
        **{key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ},
        "WAREHOUSE_PATH": str(warehouse),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DO_NOT_TRACK": "1",
    }
    try:
        done = subprocess.run(
            [
                sys.executable,
                "-c",
                "from dbt.cli.main import cli; cli()",
                "parse",
                "--project-dir",
                str(dbt_dir),
                "--profiles-dir",
                str(dbt_dir),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=DBT_PARSE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _parse_detail(exc.stdout, exc.stderr)
        raise DbtParseTimeoutError(detail) from None
    if done.returncode:
        detail = _parse_detail(done.stdout, done.stderr)
        raise OSError(f"dbt parse thất bại (mã {done.returncode}); kiểm tra bộ file dbt của pipeline. {detail}".strip())


def install_project(
    model_name: str, files: dict[str, str], deploy_dir: str, *, verify: Callable[[Path], None] | None = None
) -> Path:
    """Ghi project ra đĩa; giữ bản cũ tới khi verify xác nhận. Lỗi thì khôi phục project và workspace."""
    project_dir(model_name)  # chặn tên model lạ trước khi ghép đường dẫn
    target = Path(deploy_dir) / model_name
    token = uuid4().hex
    staging = target.with_name(f"{target.name}.tmp-{token}")
    backup = target.with_name(f"{target.name}.old-{token}")
    old_moved = new_active = False
    try:
        prepare_project(staging, files)
        _compile_manifest(staging)
        installed_at = time.time()
        if target.exists():
            target.rename(backup)
            old_moved = True
        staging.rename(target)
        new_active = True
        write_generation(deploy_dir)
        if verify is not None:
            context = WORKSPACE_UPDATED_AFTER.set(installed_at)
            try:
                verify(target)
            finally:
                WORKSPACE_UPDATED_AFTER.reset(context)
    except Exception as exc:
        rollback_errors = []
        if new_active and target.exists():
            try:
                shutil.rmtree(target)
            except OSError as cleanup_error:
                rollback_errors.append(f"Không xóa được thư mục mới ({type(cleanup_error).__name__}).")
                # Cách ly bản mới ở tên tạm để vẫn khôi phục được backup và bỏ location khi cài lần đầu.
                try:
                    target.rename(staging)
                except OSError as move_error:
                    rollback_errors.append(f"Không cách ly được thư mục mới ({type(move_error).__name__}).")
        if old_moved and backup.exists():
            try:
                backup.rename(target)
            except OSError as restore_error:
                rollback_errors.append(f"Không khôi phục được bản sao lưu ({type(restore_error).__name__}).")
        if new_active or old_moved:
            try:
                write_generation(deploy_dir)
            except OSError as generation_error:
                rollback_errors.append(f"Không ghi lại được {GENERATION_FILE} ({type(generation_error).__name__}).")
        if rollback_errors:
            detail = f"{exc} {' '.join(rollback_errors)}"
            if isinstance(exc, VerificationError):
                raise VerificationError(detail) from exc
            raise OSError(detail) from exc
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    return target
