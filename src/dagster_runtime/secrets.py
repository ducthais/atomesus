"""Credential theo từng pipeline đã deploy (lỗi N1): mỗi file `<model>.json` chỉ chứa biến của nguồn pipeline đó dùng.

Supervisor (scripts/dagster_local.py) giữ khóa giải mã và ghi các file này; code server, webserver và daemon của Dagster
không có biến `SOURCES__*` nào. Lúc chạy, thân op ingestion (`execute.run_ingestion`) đọc đúng file của model mình và
truyền vào subprocess dlt.

Danh sách nguồn lấy từ file đã ký `ingestion/<model>_pipeline.py` (AST các hằng `dlt.secrets["sources.<ref>.…"]` /
`dlt.config["sources.<ref>.…"]`), không lấy từ `run_report.json`: phần `spec` của nó không nằm trong files_hash.

Phạm vi cô lập: chống lộ do vô tình (log, traceback, pipeline đọc nhầm nguồn). Tiến trình cùng uid trong container vẫn
đọc được file và env của nhau; code pipeline vẫn phải qua review và ký duyệt.
"""

import ast
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from src.dagster_runtime import SECRETS_DIR_ENV
from src.dagster_runtime.contract import current_projects
from src.deployer.manifest import MODEL_NAME

DLT_STORES = ("secrets", "config")


class CredentialsUnavailableError(RuntimeError):
    """Chưa có file credential cho pipeline; thông báo không chứa giá trị nào."""


def secrets_dir() -> Path:
    """Production: tmpfs `/run/atomesus-secrets` (deploy/compose.yml). Local / HF Space: thư mục tạm của máy."""
    return Path(os.environ.get(SECRETS_DIR_ENV) or Path(tempfile.gettempdir()) / "atomesus-secrets")


def _is_dlt_store(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr in DLT_STORES
        and isinstance(node.value, ast.Name)
        and node.value.id == "dlt"
    )


def source_refs(project: Path) -> set[str]:
    """source_ref mà file ingestion đã ký của project đọc credential / cấu hình. Không thực thi file."""
    refs = set()
    for path in sorted((project / "ingestion").glob("*_pipeline.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            key = node.slice if isinstance(node, ast.Subscript) and _is_dlt_store(node.value) else None
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                parts = key.value.split(".")
                if len(parts) == 3 and parts[0] == "sources" and parts[1]:
                    refs.add(parts[1])
    return refs


def model_env(refs: set[str], policy, settings) -> dict[str, str]:
    """Biến dlt cho đúng các nguồn `refs`. Tra mọi domain, kể cả domain ẩn: pipeline đã deploy vẫn phải chạy."""
    from src.codegen.sandbox import source_env

    declared = {ref: decl for domain in policy.domains.domains.values() for ref, decl in domain.sources.items()}
    env = {}
    for ref in sorted(refs):
        if ref not in declared:
            raise CredentialsUnavailableError(f"Nguồn '{ref}' không có trong domains.yml.")
        env.update(source_env(ref, declared[ref], settings))
    return env


def _write_private(path: Path, text: str) -> None:
    """Ghi nguyên tử với quyền 0600 ngay từ lúc tạo file (không có khoảnh khắc file mở cho người khác đọc)."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sync_secrets(deploy_dir: str | Path, out_dir: Path, policy, settings) -> dict[str, str | None]:
    """Ghi `<out_dir>/<model>.json` cho mọi pipeline template v2 đang cài, xóa file của pipeline đã gỡ hoặc lỗi.

    Trả {model: None nếu đã có credential, tên lỗi nếu không}. Lỗi của một pipeline không chặn pipeline khác; lý do
    chỉ gồm tên nguồn / loại lỗi, không có giá trị.
    """
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    result: dict[str, str | None] = {}
    for project in current_projects(deploy_dir):
        model, target = project.name, out_dir / f"{project.name}.json"
        try:
            text = json.dumps(model_env(source_refs(project), policy, settings), sort_keys=True)
        except Exception as exc:  # nguồn chưa cấu hình, kết nối người dùng hỏng, file ingestion đọc không được…
            target.unlink(missing_ok=True)
            result[model] = str(exc) if isinstance(exc, CredentialsUnavailableError) else type(exc).__name__
            continue
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            _write_private(target, text)
        result[model] = None
    for stale in out_dir.glob("*.json"):
        if stale.stem not in result:
            stale.unlink(missing_ok=True)
    return result


def read_model_env(model: str, out_dir: Path | None = None) -> dict[str, str]:
    """Credential của đúng một pipeline, đọc lúc run chạy."""
    if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
        raise CredentialsUnavailableError("Tên pipeline không hợp lệ.")
    path = (out_dir or secrets_dir()) / f"{model}.json"
    try:
        env = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CredentialsUnavailableError(
            f"Chưa có credential cho pipeline {model}: supervisor Dagster chưa ghi (nguồn chưa cấu hình hoặc kết nối lỗi)."
        ) from None
    except (OSError, ValueError):
        raise CredentialsUnavailableError(f"File credential của pipeline {model} đọc không được.") from None
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise CredentialsUnavailableError(f"File credential của pipeline {model} sai định dạng.")
    return env
