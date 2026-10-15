"""Lỗi N1 và #2: Dagster production không nhận secret của backend, mỗi pipeline một file DuckDB, giới hạn run đồng thời.

- N1: deploy/compose.yml không cho dagster / mock-api đọc ../.env; scripts/dagster_local.py chỉ truyền whitelist cho
  tiến trình Dagster con (không OPENAI_API_KEY, PIPELINE_CONNECTION_KEY, token GitHub…). Vòng Day 2 vẫn chạy được
  không cần LLM: chỉ cảnh báo thay vì tạo run sửa.
- #2: code location mới ghi data/warehouse/<model>.duckdb; nơi đọc dùng PipelineSettings.warehouse_for; dagster.yaml
  có QueuedRunCoordinator.
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
}
SOURCE = {"SOURCES__PAGILA_PG__CREDENTIALS": "postgresql+psycopg://reader:x@postgres/pipeline_demo"}
CHILD_KEYS = {
    "PATH",
    "DAGSTER_HOME",
    "WAREHOUSE_DIR",
    "WAREHOUSE_PATH",
    "WAREHOUSE_TIMEZONE",
    "PYTHONIOENCODING",
    "DBT_SEND_ANONYMOUS_USAGE_STATS",
    "DO_NOT_TRACK",
}


@pytest.fixture
def module(tmp_path, monkeypatch):
    loaded = _dagster_local()
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(loaded, "HOME", tmp_path / "home")
    monkeypatch.setattr(loaded, "ROOT", tmp_path)
    monkeypatch.setattr(loaded, "effective_policy", lambda settings: load_policy())
    monkeypatch.setattr(loaded, "credentials_env", lambda policy, settings: dict(SOURCE))
    return loaded


def test_child_env_is_a_whitelist_without_any_backend_secret(module, tmp_path):
    env = module.child_env(PipelineSettings(_env_file=None), tmp_path / "warehouse")

    assert not set(SECRETS) & set(env), "tiến trình Dagster con không được thấy secret của tiến trình cha"
    assert set(env) <= set(PASSTHROUGH_ENV) | CHILD_KEYS | set(SOURCE)
    assert env["SOURCES__PAGILA_PG__CREDENTIALS"] == SOURCE["SOURCES__PAGILA_PG__CREDENTIALS"]
    assert env["WAREHOUSE_DIR"] == str(tmp_path / "warehouse")
    assert env["WAREHOUSE_PATH"] == str(tmp_path / "warehouse" / "warehouse.duckdb"), "bundle cũ vẫn ghi file chung"
    assert env["DAGSTER_HOME"] == str(tmp_path / "home")


@pytest.mark.parametrize("read_only", [True, False])
def test_dagster_processes_start_without_openai_key(module, tmp_path, monkeypatch, read_only):
    deploy = tmp_path / "code"
    install.write_workspace(str(deploy))
    monkeypatch.setattr(module, "READ_ONLY", read_only)
    monkeypatch.setattr(module, "PipelineSettings", lambda: PipelineSettings(_env_file=None, deploy_dir=str(deploy)))
    calls = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: calls.append(kwargs["env"]))

    module.start()

    assert calls and all(not set(SECRETS) & set(env) for env in calls)
    assert all(env.get("DAGSTER_IS_DEV_CLI") == ("1" if not read_only else None) for env in calls)
    assert (tmp_path / "data" / "warehouse").is_dir()


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


def test_readers_use_the_pipeline_file_and_fall_back_to_the_shared_one(tmp_path):
    settings = PipelineSettings(_env_file=None, warehouse_path=str(tmp_path / "warehouse.duckdb"))
    assert settings.warehouse_for("fct_old") == str(tmp_path / "warehouse.duckdb"), "bundle cũ: file chung"
    own = warehouse_file(tmp_path, "fct_new")
    own.touch()
    assert settings.warehouse_for("fct_new") == str(own)


def test_new_code_location_writes_its_own_duckdb_file():
    template = (ROOT / "templates" / "dagster_asset.py.j2").read_text(encoding="utf-8")
    assert 'if os.environ.get("WAREHOUSE_DIR"):' in template
    expected = (
        'os.environ["WAREHOUSE_PATH"] = str(Path(os.environ["WAREHOUSE_DIR"]) / [[ (name ~ ".duckdb") | pystr ]])'
    )
    assert expected in template


def test_install_parses_dbt_against_the_pipeline_file(tmp_path, monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", run)
    install._compile_manifest(tmp_path / "fct_x")
    assert Path(seen["WAREHOUSE_PATH"]).name == "fct_x.duckdb"
