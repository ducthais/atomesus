"""Lỗi N1, #2 và #4: môi trường Dagster không chứa secret nào của backend, mỗi pipeline một file DuckDB, nạp động.

- N1: deploy/compose.yml không cho dagster / mock-api đọc ../.env; scripts/dagster_local.py chỉ truyền whitelist cho
  tiến trình Dagster con (code server, webserver, daemon): không OPENAI_API_KEY, PIPELINE_CONNECTION_KEY, token GitHub
  hay `SOURCES__*` nào. Credential của từng pipeline nằm trong file riêng (src/dagster_runtime/secrets.py). Vòng Day 2
  vẫn chạy được không cần LLM: chỉ cảnh báo thay vì tạo run sửa.
- #2: code location ghi data/warehouse/<model>.duckdb; nơi đọc dùng PipelineSettings.warehouse_for; dagster.yaml có
  QueuedRunCoordinator.
- #4: một code location hợp nhất; `.generation` đổi thì ghi credential rồi reload qua GraphQL, không tắt Dagster.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.codegen.sandbox import PASSTHROUGH_ENV
from src.deployer import install
from src.pipeline.settings import PipelineSettings, warehouse_file
from src.policy.loader import load_policy
from tests.test_deployer.test_install import _dagster_local

ROOT = Path(__file__).resolve().parents[2]
SECRETS = {
    "OPENAI_API_KEY": "sk-test",
    "PIPELINE_CONNECTION_KEY": "fernet-key",
    "PIPELINE_GITHUB_TOKEN": "ghp_test",
    "PIPELINE_DEPLOYER_SECRET": "deployer-secret",
    "PIPELINE_SMTP_PASSWORD": "smtp-secret",
    "PIPELINE_ENGINEER_PASSWORD": "engineer-secret",
    "LANGCHAIN_API_KEY": "ls-test",
    "SOURCES__PAGILA_PG__CREDENTIALS": "postgresql+psycopg://reader:x@postgres/pipeline_demo",
}
CHILD_KEYS = {
    "PATH",
    "DAGSTER_HOME",
    "PIPELINE_DEPLOY_DIR",
    "ATOMESUS_SECRETS_DIR",
    "WAREHOUSE_DIR",
    "WAREHOUSE_TIMEZONE",
    "DBT_TARGET_PATH",
    "PYTHONIOENCODING",
    "DBT_SEND_ANONYMOUS_USAGE_STATS",
    "DO_NOT_TRACK",
}


@pytest.fixture
def module(tmp_path, monkeypatch):
    loaded = _dagster_local()
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ATOMESUS_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setattr(loaded, "HOME", tmp_path / "home")
    monkeypatch.setattr(loaded, "ROOT", tmp_path)
    monkeypatch.setattr(loaded, "effective_policy", lambda settings: load_policy())
    monkeypatch.setattr(loaded, "_wait_port", lambda port: None)
    return loaded


def test_child_env_is_a_whitelist_without_any_backend_secret(module, tmp_path):
    env = module.child_env(PipelineSettings(_env_file=None), tmp_path / "warehouse")

    assert not set(SECRETS) & set(env), "tiến trình Dagster con không được thấy secret hay credential nguồn nào"
    assert not [key for key in env if key.startswith("SOURCES__")]
    assert set(env) <= set(PASSTHROUGH_ENV) | CHILD_KEYS
    assert env["WAREHOUSE_DIR"] == str(tmp_path / "warehouse")
    assert "WAREHOUSE_PATH" not in env, "mỗi run tự chọn file DuckDB của model mình"
    assert env["DAGSTER_HOME"] == str(tmp_path / "home")
    assert env["ATOMESUS_SECRETS_DIR"] == str(tmp_path / "secrets")
    assert env["DBT_TARGET_PATH"] == str(tmp_path / "home" / "dbt_target")


@pytest.mark.parametrize("read_only", [True, False])
def test_dagster_processes_start_without_openai_key(module, tmp_path, monkeypatch, read_only):
    deploy = tmp_path / "code"
    deploy.mkdir()
    monkeypatch.setattr(module, "READ_ONLY", read_only)
    monkeypatch.setattr(module, "PipelineSettings", lambda: PipelineSettings(_env_file=None, deploy_dir=str(deploy)))
    order, calls = [], []
    monkeypatch.setattr(module, "refresh_secrets", lambda settings: order.append("secrets") or {})
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda command, **kwargs: order.append("popen") or calls.append((command, kwargs))
    )

    module.start()

    assert order[0] == "secrets", "credential được ghi trước khi bật code server"
    assert len(calls) == 3, "3 tiến trình cố định, không phụ thuộc số pipeline"
    (code_server, _), (webserver, _), (daemon, _) = calls
    assert code_server[1:3] == ["code-server", "start"] and "src.dagster_runtime.loader" in code_server
    assert code_server[code_server.index("--location-name") + 1] == "atomesus"
    assert "dagster-webserver" in webserver[0] and "dagster-daemon" in daemon[0]
    for _, kwargs in calls:
        # Dagster CLI tự nạp `.env` ở thư mục làm việc: gốc repo có .env chứa OPENAI_API_KEY nên không được là cwd.
        assert kwargs["cwd"] == tmp_path / "home" and not (kwargs["cwd"] / ".env").exists()
        env = kwargs["env"]
        assert not set(SECRETS) & set(env) and "OPENAI_API_KEY" not in env
        assert not [key for key in env if key.startswith("SOURCES__")] and "DAGSTER_IS_DEV_CLI" not in env
    workspace = yaml.safe_load((tmp_path / "home" / "workspace.yaml").read_text(encoding="utf-8"))
    assert workspace == {
        "load_from": [{"grpc_server": {"host": "127.0.0.1", "port": 4266, "location_name": "atomesus"}}]
    }
    assert list(deploy.iterdir()) == [], "không ghi gì vào code location"
    assert (tmp_path / "data" / "warehouse").is_dir()


def test_path_prefix_is_passed_to_the_webserver_and_the_reload_url(module, tmp_path, monkeypatch):
    # HF Space phục vụ Dagster dưới /dagster: env DAGSTER_WEBSERVER_PATH_PREFIX không qua whitelist nên phải là tham số.
    monkeypatch.setattr(module, "PATH_PREFIX", "/dagster")
    monkeypatch.setattr(module, "PipelineSettings", lambda: PipelineSettings(_env_file=None, deploy_dir=str(tmp_path)))
    monkeypatch.setattr(module, "refresh_secrets", lambda settings: {})
    calls, posted = [], []
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: calls.append(command))
    module.start()
    webserver = calls[1]
    assert webserver[webserver.index("--path-prefix") + 1] == "/dagster"

    def post(url, **kwargs):
        posted.append((url, kwargs["json"]["variables"]))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"reloadRepositoryLocation": {"__typename": "WorkspaceLocationEntry"}}},
        )

    monkeypatch.setattr(module.httpx, "post", post)
    assert module.reload_location() is True
    assert posted == [(f"http://127.0.0.1:{module.PORT}/dagster/graphql", {"name": "atomesus"})]


@pytest.mark.parametrize(
    "answer",
    [
        {"data": {"reloadRepositoryLocation": {"__typename": "PythonError", "message": "boom"}}},
        {"data": None, "errors": [{"message": "x"}]},
    ],
)
def test_reload_failure_is_reported_not_raised(module, monkeypatch, answer):
    monkeypatch.setattr(
        module.httpx, "post", lambda url, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: answer)
    )
    assert module.reload_location() is False


def test_new_generation_reloads_without_restarting_dagster(module, tmp_path, monkeypatch):
    deploy = tmp_path / "code"
    deploy.mkdir()
    (deploy / ".generation").write_text("1.a", encoding="utf-8")
    monkeypatch.setattr(module, "PipelineSettings", lambda: PipelineSettings(_env_file=None, deploy_dir=str(deploy)))
    process = SimpleNamespace(poll=lambda: None)
    events, ticks = [], []
    monkeypatch.setattr(module, "start", lambda: [process])
    monkeypatch.setattr(module, "stop", lambda processes: events.append("stop"))
    monkeypatch.setattr(module, "refresh_secrets", lambda settings: events.append("secrets"))
    answers = iter([False, True, True])  # lần đầu webserver chưa lên: thử lại ở vòng sau
    monkeypatch.setattr(module, "reload_location", lambda: events.append("reload") or next(answers))
    monkeypatch.setattr(module.time, "monotonic", lambda: 0)

    def tick(_seconds):
        ticks.append(1)
        if len(ticks) == 3:
            (deploy / ".generation").write_text("2.b", encoding="utf-8")  # deployer vừa cài pipeline
        if len(ticks) == 5:
            raise KeyboardInterrupt

    monkeypatch.setattr(module.time, "sleep", tick)

    assert module.main() == 0
    assert events == ["reload", "reload", "secrets", "reload", "stop"], "chỉ tắt khi Ctrl+C, không khởi động lại"


def test_instance_config_queues_runs_and_keeps_other_settings(module, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "dagster.yaml").write_text("telemetry:\n  enabled: false\n", encoding="utf-8")

    config = yaml.safe_load(module.write_instance_config(home).read_text(encoding="utf-8"))

    assert config["telemetry"] == {"enabled": False}
    assert config["run_coordinator"] == {
        "module": "dagster._core.run_coordinator",
        "class": "QueuedRunCoordinator",
        "config": {"max_concurrent_runs": 1},
    }
    monkeypatch.setenv("DAGSTER_MAX_CONCURRENT_RUNS", "2")
    again = yaml.safe_load(module.write_instance_config(home).read_text(encoding="utf-8"))
    assert again["run_coordinator"]["config"] == {"max_concurrent_runs": 2}
    monkeypatch.setenv("DAGSTER_MAX_CONCURRENT_RUNS", "0")
    with pytest.raises(SystemExit, match="DAGSTER_MAX_CONCURRENT_RUNS"):
        module.write_instance_config(home)


@pytest.mark.parametrize("key,launches", [("", False), ("sk-test", True)])
def test_day2_loop_runs_without_llm_and_only_launches_fixes_with_a_key(module, monkeypatch, key, launches):
    from src.monitor import day2, runs

    seen = []
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(openai_api_key=key))
    monkeypatch.setattr(module, "PipelineSettings", lambda: PipelineSettings(_env_file=None))
    monkeypatch.setattr(runs, "collect", lambda settings, policy: [])
    monkeypatch.setattr(day2, "check", lambda settings, policy, launch: seen.append(launch) or [])

    module.collect()

    assert len(seen) == 1 and (seen[0] is not None) is launches


def _compose() -> dict:
    return yaml.safe_load((ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8"))


def test_compose_gives_backend_secrets_only_to_backend():
    services = _compose()["services"]
    assert services["backend"]["env_file"] == "../.env"
    for name in ("dagster", "mock-api"):
        assert "env_file" not in services[name], f"{name} không được đọc ../.env (lỗi N1)"
    assert "environment" not in services["mock-api"]
    dagster = services["dagster"]["environment"]
    forbidden = ("OPENAI", "GITHUB", "DEPLOYER", "SMTP", "PASSWORD", "LANGCHAIN")
    assert not [key for key in dagster if any(word in key for word in forbidden)]
    assert {"PIPELINE_CONNECTION_KEY", "PIPELINE_DATABASE_URL", "PIPELINE_MOCK_API_URL"} <= set(dagster)
    assert "../data:/app/data" in services["dagster"]["volumes"], "Day 2 vẫn ghi runs.sqlite3"
    # Credential theo pipeline chỉ nằm trong RAM của container dagster, không ghi vào ../data.
    assert dagster["ATOMESUS_SECRETS_DIR"] == "/run/atomesus-secrets"
    assert services["dagster"]["tmpfs"] == ["/run/atomesus-secrets:mode=0700,uid=1000,gid=1000"]
    assert not str(dagster["ATOMESUS_SECRETS_DIR"]).startswith("/app/data")


def test_readers_use_the_pipeline_file_and_fall_back_to_the_shared_one(tmp_path):
    settings = PipelineSettings(_env_file=None, warehouse_path=str(tmp_path / "warehouse.duckdb"))
    assert settings.warehouse_for("fct_old") == str(tmp_path / "warehouse.duckdb"), "bundle cũ: file chung"
    own = warehouse_file(tmp_path, "fct_new")
    own.touch()
    assert settings.warehouse_for("fct_new") == str(own)


def test_new_code_location_writes_its_own_duckdb_file():
    # Template v2: không đặt env lúc import (run worker nạp mọi pipeline); dbt nhận file của model ngay trong thân op,
    # dlt nhận qua scoped_env (src/dagster_runtime/execute.py), cùng quy tắc warehouse_file.
    template = (ROOT / "templates" / "dagster_asset.py.j2").read_text(encoding="utf-8")
    assert "RUNTIME_CONTRACT = 2" in template
    body = template.split("_transform(context: AssetExecutionContext):", 1)[1]
    assert 'os.environ["WAREHOUSE_PATH"] = warehouse_path(MODEL)' in body
    assert template.count("os.environ[") == 1, "chỉ đặt WAREHOUSE_PATH trong thân op dbt"


def test_install_parses_dbt_against_the_pipeline_file(tmp_path, monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", run)
    install._compile_manifest(tmp_path / "fct_x")
    assert Path(seen["WAREHOUSE_PATH"]).name == "fct_x.duckdb"
