"""Chạy Dagster local (UI + daemon) cho mọi pipeline đã duyệt trong data/dagster_local/.

- workspace.yaml: mỗi project đã deploy là một code location.
- DAGSTER_HOME cố định (data/dagster_home) để lịch sử run, lịch chạy và backfill được giữ giữa các lần bật.
- Một warehouse DuckDB chung (data/warehouse/warehouse.duckdb) cho bronze / staging / gold của mọi pipeline.
- Credential nguồn lấy từ .env (PIPELINE_DATABASE_URL), không ghi ra file.
- Tự khởi động lại khi workspace.yaml đổi: Reviewer duyệt một pipeline thì deploy ghi lại file này, và
  `dagster dev` chỉ đọc danh sách code location lúc khởi động (nút Reload không thêm được location mới).

Chạy: python scripts/dagster_local.py  (hoặc make dagster), mở http://127.0.0.1:3070
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # PipelineSettings đọc .env theo đường dẫn tương đối

from src.codegen.deploy import write_workspace  # noqa: E402
from src.codegen.sandbox import credentials_env  # noqa: E402
from src.pipeline.settings import PipelineSettings  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402

HOST, PORT = os.environ.get("DAGSTER_HOST", "127.0.0.1"), os.environ.get("DAGSTER_PORT", "3070")
POLL_SECONDS = 3


def start() -> tuple[subprocess.Popen, Path]:
    """Ghi workspace từ các project đã deploy rồi bật `dagster dev`. Đọc lại policy để có credential của nguồn mới."""
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
    locations = workspace.read_text(encoding="utf-8").count("location_name:")
    print(f"Dagster: http://{HOST}:{PORT}  ·  {locations} pipeline  ·  warehouse {warehouse}", flush=True)
    dagster = Path(sys.executable).with_name("dagster.exe" if os.name == "nt" else "dagster")
    command = [str(dagster), "dev", "-w", str(workspace), "-h", HOST, "-p", PORT]
    process = subprocess.Popen(command, cwd=workspace.parent, env=env, start_new_session=os.name != "nt")
    return process, workspace


def stop(process: subprocess.Popen) -> None:
    """Tắt cả cây tiến trình: `dagster dev` sinh webserver, daemon và code server; chỉ tắt cha thì cổng vẫn bị giữ."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    process, workspace = start()
    stamp = workspace.stat().st_mtime
    try:
        while True:
            time.sleep(POLL_SECONDS)
            if process.poll() is not None:
                return process.returncode
            if workspace.stat().st_mtime != stamp:
                print("workspace.yaml đổi (có pipeline vừa được duyệt): khởi động lại Dagster…", flush=True)
                stop(process)
                process, workspace = start()
                stamp = workspace.stat().st_mtime
    except KeyboardInterrupt:
        stop(process)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
