"""Chạy Dagster local (UI + daemon) cho mọi pipeline đã duyệt trong data/dagster_local/.

- workspace.yaml: mỗi project đã deploy là một code location.
- DAGSTER_HOME cố định (data/dagster_home) để lịch sử run, lịch chạy và backfill được giữ giữa các lần bật.
- Một warehouse DuckDB chung (data/warehouse/warehouse.duckdb) cho bronze / staging / gold của mọi pipeline.
- Credential nguồn lấy từ .env (PIPELINE_DATABASE_URL), không ghi ra file.

Chạy: python scripts/dagster_local.py  (hoặc make dagster), mở http://127.0.0.1:3070
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # PipelineSettings đọc .env theo đường dẫn tương đối

from src.codegen.deploy import write_workspace  # noqa: E402
from src.codegen.sandbox import credentials_env  # noqa: E402
from src.pipeline.settings import PipelineSettings  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402

HOST, PORT = os.environ.get("DAGSTER_HOST", "127.0.0.1"), os.environ.get("DAGSTER_PORT", "3070")


def main() -> int:
    settings = PipelineSettings()
    workspace = write_workspace(settings.deploy_dir).resolve()
    home = ROOT / "data" / "dagster_home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "dagster.yaml").touch()
    warehouse = ROOT / "data" / "warehouse" / "warehouse.duckdb"
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        **credentials_env(load_policy(), settings),
        "DAGSTER_HOME": str(home),
        "WAREHOUSE_PATH": str(warehouse),
        "PYTHONIOENCODING": "utf-8",
    }
    print(f"Dagster: http://{HOST}:{PORT}  ·  workspace {workspace}  ·  warehouse {warehouse}")
    dagster = Path(sys.executable).with_name("dagster.exe" if os.name == "nt" else "dagster")
    command = [str(dagster), "dev", "-w", str(workspace), "-h", HOST, "-p", PORT]
    return subprocess.call(command, cwd=workspace.parent, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
