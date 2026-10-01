"""File Dagster sinh ra nạp được bằng Dagster thật: đúng lineage dlt → staging → gold, có job và lịch chạy.

Không cần Postgres: bảng nguồn chỉ được đọc cấu trúc lúc chạy (defer_table_reflect)."""

import importlib
import subprocess
import sys

import pytest

dg = pytest.importorskip("dagster")
pytest.importorskip("dagster_dbt")
pytest.importorskip("dagster_dlt")

from src.codegen import sandbox  # noqa: E402
from src.codegen.render import render  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402
from src.spec import SPEC_FIXTURES_DIR, Spec  # noqa: E402


def test_generated_code_location_loads(tmp_path, monkeypatch):
    spec = Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    sandbox._prepare(tmp_path, render(spec, load_policy()))
    monkeypatch.setenv("WAREHOUSE_PATH", str(tmp_path / "warehouse.duckdb"))
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    monkeypatch.setenv("SOURCES__SALES_PG__CREDENTIALS", "postgresql+psycopg://reader:x@127.0.0.1:1/none")
    project = str(tmp_path / "dbt_project")
    parsed = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "parse", "--project-dir", project, "--profiles-dir", project],
        capture_output=True,
        text=True,
    )
    assert parsed.returncode == 0, parsed.stdout[-2000:]
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("infra.dagster.generated.fct_daily_orders_asset")
    try:
        dg.Definitions.validate_loadable(module.defs)
        repo = module.defs.get_repository_def()
        graph = repo.asset_graph
        parents = {
            key.to_user_string(): {p.to_user_string() for p in graph.get(key).parent_keys}
            for key in graph.get_all_asset_keys()
        }
        assert parents["staging/stg_fct_daily_orders__orders"] == {"bronze/raw_orders"}
        assert parents["gold/fct_daily_orders"] == {"staging/stg_fct_daily_orders__orders"}
        (schedule,) = repo.schedule_defs
        assert schedule.cron_schedule == "0 2 * * *" and schedule.job_name == "fct_daily_orders_job"
    finally:
        for name in [m for m in sys.modules if m.startswith(("infra", "ingestion"))]:
            sys.modules.pop(name)
