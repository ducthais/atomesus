"""Supervisor Dagster (local và production) cho mọi pipeline đã duyệt trong data/dagster_local/.

- Một code location hợp nhất `atomesus` (src/dagster_runtime/loader.py, lỗi #4): 3 tiến trình cố định
  (`dagster code-server start`, dagster-webserver, dagster-daemon) dù có bao nhiêu pipeline. `workspace.yaml` ghi trong
  DAGSTER_HOME, trỏ tới code server qua gRPC 127.0.0.1.
- Cài / gỡ / rollback pipeline: deployer ghi `<deploy_dir>/.generation` (src/deployer/install.py). Thấy file đổi thì
  supervisor ghi credential của các pipeline rồi gọi GraphQL `reloadRepositoryLocation`: code mới được nạp, run đang
  chạy không bị dừng, Dagster không khởi động lại. Chỉ tắt cả khi một tiến trình chết (để container khởi động lại).
- Credential nguồn (lỗi N1): supervisor giữ khóa giải mã (PIPELINE_CONNECTION_KEY) và ghi
  `<ATOMESUS_SECRETS_DIR>/<model>.json` chỉ gồm nguồn của đúng pipeline đó (src/dagster_runtime/secrets.py). Tiến trình
  Dagster con nhận whitelist (`child_env`): không có OPENAI_API_KEY, PIPELINE_CONNECTION_KEY, token, mật khẩu hay
  `SOURCES__*` nào. Run đọc file credential của model mình lúc chạy (src/dagster_runtime/execute.py). Phạm vi: chống lộ
  do vô tình; tiến trình cùng uid vẫn đọc được file của nhau.
- DAGSTER_HOME cố định (data/dagster_home) để lịch sử run, lịch chạy và backfill được giữ giữa các lần bật.
  dagster.yaml đặt QueuedRunCoordinator: tối đa DAGSTER_MAX_CONCURRENT_RUNS run cùng lúc (mặc định 1).
- Mỗi pipeline một file DuckDB (data/warehouse/<model>.duckdb, warehouse_file). dbt ghi target/log của từng lần chạy vào
  DAGSTER_HOME/dbt_target, không ghi vào code location; Day 2 đọc số test từ đó (day2_settings).
- Mỗi `COLLECT_SECONDS` thu số đo các lần chạy theo lịch vừa xong (src/monitor/runs.py), rồi chạy 4 luật suy giảm
  (src/monitor/day2.py): nguồn ngừng cấp thì cảnh báo, pipeline suy giảm thì tạo run sửa chạy ở luồng riêng (không chặn
  vòng này), chờ người duyệt. Thu số đo và 4 luật là tất định; run sửa thì gọi LLM (codegen), nên tiến trình không có
  OPENAI_API_KEY (container dagster production) chỉ ghi cảnh báo để chủ pipeline tạo run sửa từ app (backend có key).
- Production (deploy/compose.yml, DAGSTER_CODE_READONLY=1): code location do service deployer ghi, Dagster mount chỉ
  đọc; supervisor không tạo thư mục trong đó.

Chạy: python scripts/dagster_local.py  (hoặc make dagster), mở http://127.0.0.1:3070
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # PipelineSettings đọc .env theo đường dẫn tương đối

import httpx  # noqa: E402
import yaml  # noqa: E402

from src.codegen.sandbox import PASSTHROUGH_ENV  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.dagster_runtime import (  # noqa: E402
    DEPLOY_DIR_ENV,
    GENERATION_FILE,
    LOCATION,
    SECRETS_DIR_ENV,
    WAREHOUSE_DIR_ENV,
)
from src.dagster_runtime.secrets import secrets_dir, sync_secrets  # noqa: E402
from src.deployer.manifest import MODEL_NAME  # noqa: E402
from src.pipeline.settings import PipelineSettings  # noqa: E402
from src.policy import effective_policy  # noqa: E402

HOST, PORT = os.environ.get("DAGSTER_HOST", "127.0.0.1"), os.environ.get("DAGSTER_PORT", "3070")
GRPC_PORT = os.environ.get("DAGSTER_GRPC_PORT", "4266")
# HF Space phục vụ Dagster dưới /dagster (deploy/hf-space/start.sh); webserver nhận qua tham số, không qua env.
PATH_PREFIX = os.environ.get("DAGSTER_WEBSERVER_PATH_PREFIX", "").rstrip("/")
READ_ONLY = os.environ.get("DAGSTER_CODE_READONLY") == "1"
HOME = ROOT / "data" / "dagster_home"
POLL_SECONDS = 3
COLLECT_SECONDS = 60
GRPC_WAIT_SECONDS = 120
RELOAD_TIMEOUT_SECONDS = 120
MAX_CONCURRENT_RUNS = 1  # VM nhỏ: mặc định không chạy song song
RELOAD = """
mutation ReloadLocation($name: String!) {
  reloadRepositoryLocation(repositoryLocationName: $name) {
    __typename
    ... on WorkspaceLocationEntry { name }
    ... on PythonError { message }
  }
}
"""


def child_env(settings: PipelineSettings, warehouse_dir: Path) -> dict[str, str]:
    """Môi trường của tiến trình Dagster con (code server, webserver, daemon và run): whitelist, không chép os.environ.

    Chỉ biến hệ thống tối thiểu (PASSTHROUGH_ENV của sandbox) và đường dẫn runtime. Không có credential nguồn nào: run
    đọc `<ATOMESUS_SECRETS_DIR>/<model>.json` của đúng pipeline đó (lỗi N1).
    """
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    env.update(
        {
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
            "DAGSTER_HOME": str(HOME),
            DEPLOY_DIR_ENV: str(Path(settings.deploy_dir).resolve()),
            SECRETS_DIR_ENV: str(secrets_dir()),
            WAREHOUSE_DIR_ENV: str(warehouse_dir),  # <WAREHOUSE_DIR>/<model>.duckdb (dagster_runtime.execute)
            "WAREHOUSE_TIMEZONE": settings.warehouse_timezone,
            # dagster-dbt đặt target/log từng lần chạy dưới đây, không ghi vào code location (day2_settings đọc lại).
            "DBT_TARGET_PATH": str(HOME / "dbt_target"),
            "PYTHONIOENCODING": "utf-8",
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
            "DO_NOT_TRACK": "1",
        }
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


def write_workspace(home: Path) -> Path:
    """workspace.yaml của webserver và daemon: đúng một location, là code server gRPC cục bộ."""
    path = home / "workspace.yaml"
    workspace = {
        "load_from": [{"grpc_server": {"host": "127.0.0.1", "port": int(GRPC_PORT), "location_name": LOCATION}}]
    }
    path.write_text(yaml.safe_dump(workspace, sort_keys=False), encoding="utf-8")
    return path


def day2_settings(settings: PipelineSettings, home: Path | None = None) -> PipelineSettings:
    """Settings để src/monitor/runs.py đọc được số test dbt khi dbt ghi kết quả ngoài code location.

    runs.py tìm run_results.json trong `<deploy_dir>/<model>/dbt_project/target/`, nhưng dagster-dbt ghi kết quả mọi
    pipeline vào `<home>/dbt_target/<op>-<run id 7 ký tự>-<mã>/` (DBT_TARGET_PATH). Op dbt của pipeline là
    `<model>_transform` (templates/dagster_asset.py.j2), nên gom bản sao run_results.json của từng pipeline vào
    `<home>/day2_dbt/<model>/dbt_project/target/` (giữ mtime: runs.py khớp lần chạy theo thời gian) và trả settings có
    deploy_dir trỏ vào đó. Chỉ chép file đổi; pipeline không còn cài thì bỏ thư mục của nó.
    """
    from src.monitor.runs import deployed_models

    home = home or HOME
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


def refresh_secrets(settings: PipelineSettings) -> dict[str, str | None]:
    """Ghi credential theo từng pipeline (đọc lại policy: nguồn / kết nối mới có hiệu lực). In lý do, không in giá trị."""
    result = sync_secrets(settings.deploy_dir, secrets_dir(), effective_policy(settings), settings)
    for model, problem in result.items():
        if problem:
            print(f"Chưa cấp được credential cho {model}: {problem}", flush=True)
    return result


def _executable(name: str) -> str:
    return str(Path(sys.executable).with_name(f"{name}.exe" if os.name == "nt" else name))


def _wait_port(port: str, seconds: float = GRPC_WAIT_SECONDS) -> None:
    """Chờ code server mở cổng gRPC trước khi bật webserver / daemon (không thì chúng thấy location lỗi lúc đầu)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    print(f"Code server chưa mở cổng {port} sau {seconds:.0f} giây; vẫn bật webserver.", flush=True)


def generation(settings: PipelineSettings) -> str | None:
    """Mốc cài đặt deployer ghi (src/deployer/install.py: write_generation); None khi chưa có."""
    try:
        return (Path(settings.deploy_dir) / GENERATION_FILE).read_text(encoding="utf-8")
    except OSError:
        return None


def reload_location(url: str = "") -> bool:
    """GraphQL `reloadRepositoryLocation`: code server nạp lại pipeline, run đang chạy giữ nguyên. Lỗi thì False."""
    endpoint = url or f"http://127.0.0.1:{PORT}{PATH_PREFIX}/graphql"
    try:
        response = httpx.post(
            endpoint, json={"query": RELOAD, "variables": {"name": LOCATION}}, timeout=RELOAD_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        entry = (response.json().get("data") or {}).get("reloadRepositoryLocation") or {}
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Chưa nạp lại được code location ({type(exc).__name__}); thử lại ở vòng sau.", flush=True)
        return False
    if entry.get("__typename") != "WorkspaceLocationEntry":
        print(f"Dagster từ chối nạp lại code location ({entry.get('__typename')}); thử lại ở vòng sau.", flush=True)
        return False
    return True


def start() -> list[subprocess.Popen]:
    """Ghi credential, cấu hình instance và workspace, rồi bật code server, webserver, daemon."""
    settings = PipelineSettings()
    if not READ_ONLY:
        Path(settings.deploy_dir).mkdir(parents=True, exist_ok=True)
    HOME.mkdir(parents=True, exist_ok=True)
    write_instance_config(HOME)
    workspace = write_workspace(HOME)
    warehouse_dir = ROOT / "data" / "warehouse"
    warehouse_dir.mkdir(parents=True, exist_ok=True)
    refresh_secrets(settings)  # trước khi bật code server: run đầu tiên đã có credential
    env = child_env(settings, warehouse_dir)
    print(
        f"Dagster: http://{HOST}:{PORT}{PATH_PREFIX}  ·  location {LOCATION}  ·  warehouse {warehouse_dir}", flush=True
    )
    code_server = [
        _executable("dagster"),
        "code-server",
        "start",
        "-m",
        "src.dagster_runtime.loader",
        "--working-directory",
        str(ROOT),
        "-h",
        "127.0.0.1",
        "-p",
        GRPC_PORT,
        "--location-name",
        LOCATION,
    ]
    webserver = [_executable("dagster-webserver"), "-w", str(workspace), "-h", HOST, "-p", PORT]
    if PATH_PREFIX:
        webserver += ["--path-prefix", PATH_PREFIX]
    daemon = [_executable("dagster-daemon"), "run", "-w", str(workspace)]

    def spawn(command: list[str]) -> subprocess.Popen:
        # cwd là DAGSTER_HOME, không phải gốc repo: Dagster CLI tự nạp `.env` ở thư mục làm việc, và `.env` của repo có
        # OPENAI_API_KEY / mật khẩu (lỗi N1). Code server vẫn import được `src` nhờ --working-directory.
        return subprocess.Popen(command, cwd=HOME, env=env, start_new_session=os.name != "nt")

    processes = [spawn(code_server)]
    _wait_port(GRPC_PORT)
    return processes + [spawn(webserver), spawn(daemon)]


def stop(processes: list[subprocess.Popen]) -> None:
    """Tắt cả cây tiến trình: code server sinh tiến trình con nạp code; chỉ tắt cha thì cổng vẫn bị giữ."""
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
        written = collect_runs(day2_settings(settings), policy)
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
    processes = start()
    settings = PipelineSettings()
    stamp = generation(settings)
    pending = True  # nạp lại một lần khi webserver đã lên, phòng code server nạp xong sau lúc webserver hỏi
    collected = time.monotonic()
    try:
        while True:
            time.sleep(POLL_SECONDS)
            exited = next((process for process in processes if process.poll() is not None), None)
            if exited is not None:
                stop(processes)  # một tiến trình chết thì tắt cả, để container khởi động lại
                return exited.returncode
            current = generation(settings)
            if current != stamp:
                print("Có pipeline vừa được cài / gỡ: ghi credential rồi nạp lại code location…", flush=True)
                refresh_secrets(settings)
                stamp, pending = current, True
            if pending:
                pending = not reload_location()
            if time.monotonic() - collected >= COLLECT_SECONDS:
                refresh_secrets(
                    settings
                )  # kết nối người dùng đổi mật khẩu: file credential cập nhật, không cần nạp lại
                collect()
                collected = time.monotonic()
    except KeyboardInterrupt:
        stop(processes)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
