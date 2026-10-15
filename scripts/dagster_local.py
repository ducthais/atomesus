"""Chạy Dagster local (UI + daemon) cho mọi pipeline đã duyệt trong data/dagster_local/.

- workspace.yaml: mỗi project đã deploy là một code location.
- DAGSTER_HOME cố định (data/dagster_home) để lịch sử run, lịch chạy và backfill được giữ giữa các lần bật.
  dagster.yaml đặt QueuedRunCoordinator: tối đa DAGSTER_MAX_CONCURRENT_RUNS run cùng lúc (mặc định 1).
- Mỗi pipeline một file DuckDB (data/warehouse/<model>.duckdb, templates/dagster_asset.py.j2) để pipeline chạy cùng
  lúc không tranh khóa file. Bundle deploy trước khi tách file vẫn ghi data/warehouse/warehouse.duckdb chung.
- Credential nguồn lấy từ .env (PIPELINE_DATABASE_URL), không ghi ra file. Tiến trình Dagster con chỉ nhận whitelist
  (`child_env`): biến hệ thống tối thiểu và chuỗi kết nối đã giải mã, không có OPENAI_API_KEY, PIPELINE_CONNECTION_KEY
  hay token nào của tiến trình này (lỗi N1).
- Mỗi `COLLECT_SECONDS` thu số đo các lần chạy theo lịch vừa xong (src/monitor/runs.py), rồi chạy 4 luật suy giảm
  (src/monitor/day2.py): nguồn ngừng cấp thì cảnh báo, pipeline suy giảm thì tạo run sửa chạy ở luồng riêng (không chặn
  vòng này), chờ người duyệt. Thu số đo và 4 luật là tất định; run sửa thì gọi LLM (codegen), nên tiến trình không có
  OPENAI_API_KEY (container dagster production) chỉ ghi cảnh báo để chủ pipeline tạo run sửa từ app (backend có key).
- Tự khởi động lại khi workspace.yaml đổi: cài một pipeline (src/deployer/install.py) thì file này được ghi lại, và
  `dagster dev` chỉ đọc danh sách code location lúc khởi động (nút Reload không thêm được location mới).
- Production (deploy/compose.yml, DAGSTER_CODE_READONLY=1): code location do service deployer ghi, Dagster mount chỉ
  đọc. Không ghi workspace.yaml (chờ deployer tạo), không chạy `dagster dev` (nó chạy `dbt parse` ghi vào thư mục
  project) mà chạy dagster-webserver + dagster-daemon; dbt ghi target/log của từng lần chạy vào DAGSTER_HOME/dbt_target,
  Day 2 đọc số test từ đó (day2_settings).

Chạy: python scripts/dagster_local.py  (hoặc make dagster), mở http://127.0.0.1:3070
"""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # PipelineSettings đọc .env theo đường dẫn tương đối

import yaml  # noqa: E402

from src.codegen.sandbox import PASSTHROUGH_ENV, credentials_env  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.deployer.install import write_workspace  # noqa: E402
from src.deployer.manifest import MODEL_NAME  # noqa: E402
from src.pipeline.settings import SHARED_WAREHOUSE, PipelineSettings  # noqa: E402
from src.policy import effective_policy  # noqa: E402

HOST, PORT = os.environ.get("DAGSTER_HOST", "127.0.0.1"), os.environ.get("DAGSTER_PORT", "3070")
READ_ONLY = os.environ.get("DAGSTER_CODE_READONLY") == "1"
HOME = ROOT / "data" / "dagster_home"
POLL_SECONDS = 3
COLLECT_SECONDS = 60
MAX_CONCURRENT_RUNS = 1  # VM nhỏ; bundle cũ còn chung một file DuckDB nên mặc định không chạy song song


def child_env(settings: PipelineSettings, warehouse_dir: Path) -> dict[str, str]:
    """Môi trường của tiến trình Dagster con (webserver, daemon, code pipeline): whitelist, không chép os.environ.

    Giống sandbox (src/codegen/sandbox.py: PASSTHROUGH_ENV, credentials_env): biến hệ thống tối thiểu cộng chuỗi kết nối
    nguồn đã giải mã. Không có OPENAI_API_KEY, PIPELINE_CONNECTION_KEY, PIPELINE_GITHUB_* hay secret nào khác của tiến
    trình này (lỗi N1): code pipeline chỉ cần credential của nguồn nó đọc.
    """
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    env.update(credentials_env(effective_policy(settings), settings))
    env.update(
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        DAGSTER_HOME=str(HOME),
        WAREHOUSE_DIR=str(warehouse_dir),  # bundle mới: <WAREHOUSE_DIR>/<model>.duckdb (dagster_asset.py.j2)
        WAREHOUSE_PATH=str(warehouse_dir / SHARED_WAREHOUSE),  # bundle deploy trước khi tách file
        WAREHOUSE_TIMEZONE=settings.warehouse_timezone,
        PYTHONIOENCODING="utf-8",
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        DO_NOT_TRACK="1",
    )
    return env


def write_instance_config(home: Path) -> Path:
    """dagster.yaml: QueuedRunCoordinator giới hạn số run chạy cùng lúc (lỗi #2), giữ mọi cấu hình khác đã có."""
    raw = os.environ.get("DAGSTER_MAX_CONCURRENT_RUNS", str(MAX_CONCURRENT_RUNS))
    if not raw.isdigit() or int(raw) < 1:
        raise SystemExit(f"DAGSTER_MAX_CONCURRENT_RUNS phải là số nguyên ≥ 1, đang là {raw!r}.")
    path = home / "dagster.yaml"
    config = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    config["run_coordinator"] = {
        "module": "dagster._core.run_coordinator",
        "class": "QueuedRunCoordinator",
        "config": {"max_concurrent_runs": int(raw)},
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def day2_settings(settings: PipelineSettings, home: Path = HOME) -> PipelineSettings:
    """Production: settings để src/monitor/runs.py đọc được số test dbt khi code location chỉ đọc.

    runs.py tìm run_results.json trong `<deploy_dir>/<model>/dbt_project/target/`, nhưng ở production dagster-dbt ghi
    kết quả mọi pipeline vào `<home>/dbt_target/<op>-<run id 7 ký tự>-<mã>/` (DBT_TARGET_PATH). Op dbt của pipeline là
    `<model>_transform` (templates/dagster_asset.py.j2), nên gom bản sao run_results.json của từng pipeline vào
    `<home>/day2_dbt/<model>/dbt_project/target/` (giữ mtime: runs.py khớp lần chạy theo thời gian) và trả settings có
    deploy_dir trỏ vào đó. Chỉ chép file đổi; pipeline không còn cài thì bỏ thư mục của nó.
    """
    from src.monitor.runs import deployed_models

    view, artifacts = home / "day2_dbt", home / "dbt_target"
    models = [model for model in deployed_models(settings.deploy_dir) if MODEL_NAME.match(model)]
    if view.exists():
        for stale in view.iterdir():
            if stale.name not in models:
                shutil.rmtree(stale, ignore_errors=True)
    for model in models:
        target = view / model / "dbt_project" / "target"
        target.mkdir(parents=True, exist_ok=True)
        for results in artifacts.glob(f"{model}_transform-*/run_results.json"):
            copy = target / results.parent.name / "run_results.json"
            if not copy.exists() or copy.stat().st_mtime != results.stat().st_mtime:
                copy.parent.mkdir(exist_ok=True)
                shutil.copy2(results, copy)
    return settings.model_copy(update={"deploy_dir": str(view)})


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
    HOME.mkdir(parents=True, exist_ok=True)
    write_instance_config(HOME)
    warehouse_dir = ROOT / "data" / "warehouse"
    warehouse_dir.mkdir(parents=True, exist_ok=True)
    env = child_env(settings, warehouse_dir)
    locations = workspace.read_text(encoding="utf-8").count("location_name:")
    print(f"Dagster: http://{HOST}:{PORT}  ·  {locations} pipeline  ·  warehouse {warehouse_dir}", flush=True)
    if READ_ONLY:
        # Không đặt DAGSTER_IS_DEV_CLI: DbtProject.prepare_if_dev() khi đó không chạy `dbt parse` vào thư mục chỉ
        # đọc, dùng target/manifest.json deployer đã biên dịch lúc cài. dagster-dbt đặt target/log từng lần chạy
        # dưới DBT_TARGET_PATH (day2_settings đọc lại ở đó).
        env["DBT_TARGET_PATH"] = str(HOME / "dbt_target")
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
        # Đọc lại mỗi lần: sửa ngưỡng trong objectives.yml có hiệu lực ngay (NFR-04).
        policy = effective_policy(settings)
        written = collect_runs(day2_settings(settings) if READ_ONLY else settings, policy)
        launch = None  # không có LLM: Day 2 chỉ cảnh báo, run sửa tạo từ app (src/monitor/day2.py: check)
        if get_settings().openai_api_key:
            deps = AgentDeps(llm=openai_llm(), policy=policy, settings=settings, reserve_daily=budget.reserve_daily)
            launch = day2.launcher(deps, settings.store_path, _in_thread)
        actions = day2.check(settings, policy, launch)
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
        elif action["action"] == "quota_exhausted":  # chủ pipeline hết lượt run: hoãn run sửa, pipeline khác vẫn xét
            print(f"Day 2 · {action['model']}: hoãn run sửa — {action['detail']}", flush=True)


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
