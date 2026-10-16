"""Subprocess dlt của pipeline đã deploy chỉ thấy credential và file DuckDB của chính nó, không có secret của backend."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from dagster import AssetKey, Failure

from src.dagster_runtime import execute

SECRETS = {
    "OPENAI_API_KEY": "sk-test",
    "PIPELINE_CONNECTION_KEY": "fernet-key",
    "PIPELINE_GITHUB_TOKEN": "ghp_test",
    "PIPELINE_DEPLOYER_SECRET": "deployer-secret",
    "SOURCES__SALES_PG__CREDENTIALS": "postgresql://other:pw@h/db",
}
OWN = {"SOURCES__PAGILA_PG__CREDENTIALS": "postgresql+psycopg://reader:very-secret@postgres/pipeline_demo"}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    out = tmp_path / "secrets"
    out.mkdir()
    (out / "fct_a.json").write_text(json.dumps(OWN), encoding="utf-8")
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ATOMESUS_SECRETS_DIR", str(out))
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path / "warehouse"))
    return tmp_path


def test_scoped_env_has_only_the_pipeline_sources_and_its_duckdb(runtime):
    env = execute.scoped_env("fct_a")

    assert not set(SECRETS) & set(env), "không có OpenAI key, khóa giải mã, token hay nguồn của pipeline khác"
    assert env["SOURCES__PAGILA_PG__CREDENTIALS"] == OWN["SOURCES__PAGILA_PG__CREDENTIALS"]
    assert Path(env["WAREHOUSE_PATH"]) == (runtime / "warehouse" / "fct_a.duckdb").resolve()
    assert execute.warehouse_path("fct_a") == env["WAREHOUSE_PATH"]


def _context(keys):
    logged = []
    return SimpleNamespace(selected_asset_keys=set(keys), log=SimpleNamespace(info=logged.append)), logged


def test_run_ingestion_runs_the_pipeline_script_with_the_scoped_env(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(
        execute.subprocess,
        "run",
        lambda command, **kw: calls.append((command, kw)) or SimpleNamespace(returncode=0, stdout="load ok", stderr=""),
    )
    keys = [AssetKey(["fct_a", "bronze", "raw_rental"]), AssetKey(["fct_a", "bronze", "raw_feedback"])]
    context, logged = _context(keys)

    results = list(execute.run_ingestion(context, "fct_a", runtime / "project"))

    ((command, kw),) = calls
    assert command[1] == str(runtime / "project" / "ingestion" / "fct_a_pipeline.py")
    assert kw["cwd"] == runtime / "project" and kw["env"] == execute.scoped_env("fct_a")
    assert [r.asset_key for r in results] == sorted(keys, key=lambda k: k.path) and logged == ["load ok"]


def test_failed_ingestion_hides_credentials(runtime, monkeypatch):
    leaked = f"could not connect to {OWN['SOURCES__PAGILA_PG__CREDENTIALS']}"
    monkeypatch.setattr(
        execute.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr=leaked)
    )
    context, _ = _context([AssetKey(["fct_a", "bronze", "raw_rental"])])

    with pytest.raises(Failure) as failure:
        list(execute.run_ingestion(context, "fct_a", runtime))

    assert "very-secret" not in failure.value.description and "mã 1" in failure.value.description


def test_missing_credentials_fail_the_run_without_starting_dlt(runtime, monkeypatch):
    monkeypatch.setattr(execute.subprocess, "run", lambda *a, **kw: pytest.fail("không được chạy dlt"))
    context, _ = _context([])

    with pytest.raises(Failure, match="Chưa có credential cho pipeline fct_b"):
        list(execute.run_ingestion(context, "fct_b", runtime))


def test_timeout_is_reported(runtime, monkeypatch):
    def slow(*args, **kwargs):
        raise execute.subprocess.TimeoutExpired("dlt", kwargs["timeout"])

    monkeypatch.setattr(execute.subprocess, "run", slow)
    monkeypatch.setenv(execute.INGEST_TIMEOUT_ENV, "7")
    context, _ = _context([])

    with pytest.raises(Failure, match="quá 7 giây"):
        list(execute.run_ingestion(context, "fct_a", runtime))
