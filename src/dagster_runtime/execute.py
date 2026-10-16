"""Thân op của bundle template v2: chạy dlt trong subprocess với env chỉ của pipeline đó (lỗi N1, #2).

Code server và run worker không có credential nguồn nào; `scoped_env(model)` ghép biến hệ thống tối thiểu, file DuckDB
riêng của model (`warehouse_file`, cùng quy tắc với `PipelineSettings.warehouse_for`) và credential đọc từ
`<ATOMESUS_SECRETS_DIR>/<model>.json` (secrets.sync_secrets). Template nguồn dlt giữ nguyên: chúng đọc env như trong
sandbox. Chữ ký `run_ingestion` / `warehouse_path` là API của bundle đã ký: đổi thì tăng RUNTIME_CONTRACT.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from dagster import AssetExecutionContext, Failure, MaterializeResult

from src.dagster_runtime import WAREHOUSE_DIR_ENV
from src.dagster_runtime.secrets import CredentialsUnavailableError, read_model_env
from src.pipeline.settings import warehouse_file

INGEST_TIMEOUT_ENV = "ATOMESUS_INGEST_TIMEOUT_SEC"
INGEST_TIMEOUT_SEC = 3600
OUTPUT_TAIL = 2000


def warehouse_path(model: str) -> str:
    """File DuckDB riêng của pipeline: <WAREHOUSE_DIR>/<model>.duckdb (scripts/dagster_local.py đặt WAREHOUSE_DIR)."""
    return str(warehouse_file(Path(os.environ.get(WAREHOUSE_DIR_ENV, "data/warehouse")).resolve(), model))


def scoped_env(model: str) -> dict[str, str]:
    """Env của subprocess dlt: whitelist hệ thống + DuckDB của model + đúng credential của model, không gì khác."""
    from src.codegen.sandbox import PASSTHROUGH_ENV

    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    env.update(
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        WAREHOUSE_PATH=warehouse_path(model),
        WAREHOUSE_TIMEZONE=os.environ.get("WAREHOUSE_TIMEZONE", "UTC"),
        PYTHONIOENCODING="utf-8",
        RUNTIME__LOG_LEVEL="WARNING",
        DO_NOT_TRACK="1",
    )
    env.update(read_model_env(model))
    return env


def redact(text: str, env: dict[str, str]) -> str:
    from src.codegen.sandbox import safe_output

    return safe_output(text, env)


def _timeout() -> int:
    raw = os.environ.get(INGEST_TIMEOUT_ENV, "")
    return int(raw) if raw.isdigit() and int(raw) > 0 else INGEST_TIMEOUT_SEC


def run_ingestion(context: AssetExecutionContext, model: str, repo_root: Path) -> Iterator[MaterializeResult]:
    """Chạy `ingestion/<model>_pipeline.py` (dlt → bronze trong DuckDB của model) rồi báo materialize các bảng bronze."""
    try:
        env = scoped_env(model)
    except CredentialsUnavailableError as exc:
        raise Failure(description=str(exc)) from None
    script = Path(repo_root) / "ingestion" / f"{model}_pipeline.py"
    timeout = _timeout()
    try:
        done = subprocess.run(
            [sys.executable, str(script)],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise Failure(description=f"Ingestion {model} chạy quá {timeout} giây ({INGEST_TIMEOUT_ENV}).") from None
    if done.returncode:
        detail = redact((done.stderr or done.stdout or "").strip(), env)[-OUTPUT_TAIL:]
        raise Failure(description=f"Ingestion {model} thất bại (mã {done.returncode}). {detail}".strip())
    if done.stdout.strip():
        context.log.info(redact(done.stdout.strip(), env)[-OUTPUT_TAIL:])
    for key in sorted(context.selected_asset_keys, key=lambda k: k.path):
        yield MaterializeResult(asset_key=key)
