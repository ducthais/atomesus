"""File Dagster sinh ra (template v2) nạp được bằng Dagster thật mà không cần credential hay WAREHOUSE_PATH.

- Lineage dlt → staging → gold, mọi asset key có tiền tố model; job dùng in_process_executor; có lịch chạy.
- Import không đổi os.environ (run worker nạp mọi pipeline của code location hợp nhất).
- Hai pipeline cùng đọc `pagila.rental` gộp được bằng Definitions.merge: không trùng asset key, không trùng resource key.

Không cần Postgres: bảng nguồn chỉ được đọc lúc job chạy.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

dg = pytest.importorskip("dagster")
pytest.importorskip("dagster_dbt")

from src.codegen import sandbox  # noqa: E402
from src.codegen.render import render  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402
from src.spec import SPEC_FIXTURES_DIR, Spec  # noqa: E402
from tests.test_dagster_runtime.specs import RENTAL_MODEL, REVIEW_MODEL, rental_spec, review_spec  # noqa: E402


def _project(root: Path, spec: Spec) -> Path:
    project = root / spec.transform.model_name
    sandbox.prepare_project(project, render(spec, load_policy()))
    dbt = str(project / "dbt_project")
    env = {**os.environ, "WAREHOUSE_PATH": str(root / "parse.duckdb")}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    parsed = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "parse", "--project-dir", dbt, "--profiles-dir", dbt],
        capture_output=True,
        text=True,
        env=env,
    )
    assert parsed.returncode == 0, parsed.stdout[-2000:]
    return project


def _load(project: Path):
    path = project / "infra" / "dagster" / "generated" / f"{project.name}_asset.py"
    spec = importlib.util.spec_from_file_location(f"atomesus_pipeline_{project.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    for key in [k for k in os.environ if k.startswith("SOURCES__") or k == "WAREHOUSE_PATH"]:
        monkeypatch.delenv(key)


def test_generated_code_location_loads_without_credentials(tmp_path, clean_env):
    spec = Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    project = _project(tmp_path, spec)
    before = dict(os.environ)

    module = _load(project)

    assert dict(os.environ) == before, "import không được đặt biến môi trường"
    assert module.RUNTIME_CONTRACT == 2
    dg.Definitions.validate_loadable(module.defs)
    repo = module.defs.get_repository_def()
    graph = repo.asset_graph
    parents = {
        key.to_user_string(): {p.to_user_string() for p in graph.get(key).parent_keys}
        for key in graph.get_all_asset_keys()
    }
    assert parents["fct_daily_orders/staging/stg_fct_daily_orders__orders"] == {"fct_daily_orders/bronze/raw_orders"}
    assert parents["fct_daily_orders/gold/fct_daily_orders"] == {
        "fct_daily_orders/staging/stg_fct_daily_orders__orders"
    }
    assert parents["fct_daily_orders/bronze/raw_orders"] == set()
    job = repo.get_job("fct_daily_orders_job")
    assert job.executor_def.name == dg.in_process_executor.name
    assert {node.name for node in job.graph.node_defs} == {"fct_daily_orders_ingestion", "fct_daily_orders_transform"}
    (schedule,) = repo.schedule_defs
    assert schedule.cron_schedule == "0 2 * * *" and schedule.job_name == "fct_daily_orders_job"


def test_two_pipelines_reading_the_same_table_merge(tmp_path, clean_env):
    review = _load(_project(tmp_path, Spec.model_validate(review_spec())))
    rental = _load(_project(tmp_path, Spec.model_validate(rental_spec())))

    merged = dg.Definitions.merge(review.defs, rental.defs)

    keys = {key.to_user_string() for key in merged.resolve_asset_graph().get_all_asset_keys()}
    assert {f"{REVIEW_MODEL}/bronze/raw_rental", f"{RENTAL_MODEL}/bronze/raw_rental"} <= keys
    assert f"{REVIEW_MODEL}/bronze/raw_feedback" in keys
    jobs = {job.name for job in merged.get_repository_def().get_all_jobs() if not job.name.startswith("__")}
    assert jobs == {f"{REVIEW_MODEL}_job", f"{RENTAL_MODEL}_job"}
