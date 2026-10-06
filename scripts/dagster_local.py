"""Chạy Dagster local (UI + daemon) cho mọi pipeline đã duyệt trong data/dagster_local/.

- workspace.yaml: mỗi project đã deploy là một code location.
- DAGSTER_HOME cố định (data/dagster_home) để lịch sử run, lịch chạy và backfill được giữ giữa các lần bật.
- Một warehouse DuckDB chung (data/warehouse/warehouse.duckdb) cho bronze / staging / gold của mọi pipeline.
- Credential nguồn lấy từ .env (PIPELINE_DATABASE_URL), không ghi ra file.
- Mỗi `COLLECT_SECONDS` thu số đo các lần chạy theo lịch vừa xong (src/monitor/runs.py) cho phần theo dõi Day 2.
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
COLLECT_SECONDS = 60


def start() -> tuple[subprocess.Popen, Path]:
    """Ghi workspace từ các project đã deploy rồi bật `dagster dev`. Đọc lại policy để có credential của nguồn mới."""
    settings = PipelineSettings()
    workspace = write_workspace(settings.deploy_dir).resolve()
    home = ROOT / "data" / "dagster_home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "dagster.yaml").touch()
    warehouse = ROOT / "data" / "warehouse" / "warehouse.duckdb"
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    scripts_dir = str(Path(sys.executable).parent)
    env = {
        **os.environ,
        **credentials_env(load_policy(), settings),
        "PATH": scripts_dir + os.pathsep + os.environ.get("PATH", ""),
        "DAGSTER_HOME": str(home),
        "WAREHOUSE_PATH": str(warehouse),
        "DAGSTER_IS_DEV_CLI": "1",
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


def collect() -> None:
    """Ghi số đo các lần chạy đã xong; Dagster chưa lên hoặc nguồn lỗi thì bỏ qua, lần sau thu tiếp."""
    from src.monitor.runs import MonitorError
    from src.monitor.runs import collect as collect_runs

    try:
        written = collect_runs(PipelineSettings(), load_policy())
    except MonitorError:
        return
    except Exception as exc:  # không để lỗi thu số đo làm tắt Dagster
        print(f"Thu số đo lỗi: {type(exc).__name__}: {exc}", flush=True)
        return
    if written:
        print(f"Đã ghi số đo {len(written)} lần chạy: {', '.join(r['model'] for r in written)}", flush=True)


def main() -> int:
    process, workspace = start()
    stamp = workspace.stat().st_mtime
    collected = time.monotonic()
    try:
        while True:
            time.sleep(POLL_SECONDS)
            if process.poll() is not None:
                return process.returncode
            if time.monotonic() - collected >= COLLECT_SECONDS:
                collect()
                collected = time.monotonic()
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
