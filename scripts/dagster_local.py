"""Chạy Dagster local (UI + daemon) cho mọi pipeline đã duyệt trong data/dagster_local/.

- workspace.yaml: mỗi project đã deploy là một code location.
- DAGSTER_HOME cố định (data/dagster_home) để lịch sử run, lịch chạy và backfill được giữ giữa các lần bật.
- Một warehouse DuckDB chung (data/warehouse/warehouse.duckdb) cho bronze / staging / gold của mọi pipeline.
- Credential nguồn lấy từ .env (PIPELINE_DATABASE_URL), không ghi ra file.
- Mỗi `COLLECT_SECONDS` thu số đo các lần chạy theo lịch vừa xong (src/monitor/runs.py), rồi chạy 4 luật suy giảm
  (src/monitor/day2.py): nguồn ngừng cấp thì cảnh báo, pipeline suy giảm thì tạo run sửa chạy ở luồng riêng (không chặn
  vòng này), chờ người duyệt.
- Tự khởi động lại khi workspace.yaml đổi: cài một pipeline (src/deployer/install.py) thì file này được ghi lại, và
  `dagster dev` chỉ đọc danh sách code location lúc khởi động (nút Reload không thêm được location mới).
- Production (deploy/compose.yml, DAGSTER_CODE_READONLY=1): code location do service deployer ghi, Dagster mount chỉ
  đọc. Không ghi workspace.yaml (chờ deployer tạo), không chạy `dagster dev` (nó chạy `dbt parse` ghi vào thư mục
  project) mà chạy dagster-webserver + dagster-daemon; dbt ghi target/log của từng lần chạy vào DAGSTER_HOME.

Chạy: python scripts/dagster_local.py  (hoặc make dagster), mở http://127.0.0.1:3070
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # PipelineSettings đọc .env theo đường dẫn tương đối

from src.codegen.sandbox import credentials_env  # noqa: E402
from src.deployer.install import write_workspace  # noqa: E402
from src.pipeline.settings import PipelineSettings  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402

HOST, PORT = os.environ.get("DAGSTER_HOST", "127.0.0.1"), os.environ.get("DAGSTER_PORT", "3070")
READ_ONLY = os.environ.get("DAGSTER_CODE_READONLY") == "1"
POLL_SECONDS = 3
COLLECT_SECONDS = 60


def _wait_workspace(deploy_dir: str) -> Path:
    """Chế độ chỉ đọc: workspace.yaml do deployer ghi (kể cả khi chưa có pipeline nào); chưa có thì chờ."""
    workspace = Path(deploy_dir).resolve() / "workspace.yaml"
    if not workspace.exists():
        print(f"Chờ service deployer tạo {workspace}…", flush=True)
    while not workspace.exists():
        time.sleep(POLL_SECONDS)
    return workspace


def _executable(name: str) -> str:
    return str(Path(sys.executable).with_name(f"{name}.exe" if os.name == "nt" else name))


def start() -> tuple[list[subprocess.Popen], Path]:
    """Ghi workspace từ các project đã deploy rồi bật Dagster. Đọc lại policy để có credential của nguồn mới."""
    settings = PipelineSettings()
    workspace = _wait_workspace(settings.deploy_dir) if READ_ONLY else write_workspace(settings.deploy_dir).resolve()
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
        "PYTHONIOENCODING": "utf-8",
    }
    locations = workspace.read_text(encoding="utf-8").count("location_name:")
    print(f"Dagster: http://{HOST}:{PORT}  ·  {locations} pipeline  ·  warehouse {warehouse}", flush=True)
    if READ_ONLY:
        # Không đặt DAGSTER_IS_DEV_CLI: DbtProject.prepare_if_dev() khi đó không chạy `dbt parse` vào thư mục chỉ
        # đọc, dùng target/manifest.json deployer đã biên dịch lúc cài. dagster-dbt đặt target/log từng lần chạy
        # dưới DBT_TARGET_PATH.
        env.pop("DAGSTER_IS_DEV_CLI", None)
        env.update(DBT_TARGET_PATH=str(home / "dbt_target"), DO_NOT_TRACK="1")
        commands = [
            [_executable("dagster-webserver"), "-w", str(workspace), "-h", HOST, "-p", PORT],
            [_executable("dagster-daemon"), "run", "-w", str(workspace)],
        ]
    else:
        env["DAGSTER_IS_DEV_CLI"] = "1"
        commands = [[_executable("dagster"), "dev", "-w", str(workspace), "-h", HOST, "-p", PORT]]
    processes = [
        subprocess.Popen(command, cwd=workspace.parent, env=env, start_new_session=os.name != "nt")
        for command in commands
    ]
    return processes, workspace


def stop(processes: list[subprocess.Popen]) -> None:
    """Tắt cả cây tiến trình: `dagster dev` sinh webserver, daemon và code server; chỉ tắt cha thì cổng vẫn bị giữ."""
    for process in processes:
        if process.poll() is not None:
            continue
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    for process in processes:
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()


def _in_thread(job) -> None:
    threading.Thread(target=job, name="regression-fix", daemon=True).start()


def collect() -> None:
    """Ghi số đo các lần chạy đã xong rồi chạy 4 luật suy giảm; Dagster chưa lên hoặc lỗi thì bỏ qua, lần sau làm tiếp."""
    from src.agents import budget
    from src.agents.llm import openai_llm
    from src.agents.nodes.common import AgentDeps
    from src.monitor import day2
    from src.monitor.runs import MonitorError
    from src.monitor.runs import collect as collect_runs

    settings = PipelineSettings()
    try:
        policy = load_policy()  # đọc lại mỗi lần: sửa ngưỡng trong objectives.yml có hiệu lực ngay (NFR-04)
        written = collect_runs(settings, policy)
        deps = AgentDeps(llm=openai_llm(), policy=policy, settings=settings, reserve_daily=budget.reserve_daily)
        actions = day2.check(settings, policy, day2.launcher(deps, settings.store_path, _in_thread))
    except MonitorError:
        return
    except Exception as exc:  # không để lỗi thu số đo / phát hiện làm tắt Dagster
        print(f"Theo dõi Day 2 lỗi: {type(exc).__name__}: {exc}", flush=True)
        return
    if written:
        print(f"Đã ghi số đo {len(written)} lần chạy: {', '.join(r['model'] for r in written)}", flush=True)
    for action in actions:
        if action["action"] in ("alert", "fix_started"):
            print(
                f"Day 2 · {action['model']}: {action['action']} {action.get('run_id', action.get('rule'))}", flush=True
            )


def main() -> int:
    processes, workspace = start()
    stamp = workspace.stat().st_mtime
    collected = time.monotonic()
    try:
        while True:
            time.sleep(POLL_SECONDS)
            exited = next((process for process in processes if process.poll() is not None), None)
            if exited is not None:
                stop(processes)  # webserver hoặc daemon chết thì tắt cả, để container khởi động lại
                return exited.returncode
            if time.monotonic() - collected >= COLLECT_SECONDS:
                collect()
                collected = time.monotonic()
            if workspace.stat().st_mtime != stamp:
                print("workspace.yaml đổi (có pipeline vừa được cài): khởi động lại Dagster…", flush=True)
                stop(processes)
                processes, workspace = start()
                stamp = workspace.stat().st_mtime
    except KeyboardInterrupt:
        stop(processes)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
