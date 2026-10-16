"""review_api ⋈ pagila_pg trên code location hợp nhất: credential đúng nguồn, file DuckDB riêng, nạp cùng pipeline khác.

Phần đầu chạy offline. `test_review_pipeline_runs_end_to_end` chạy thật dlt + dbt qua Dagster (execute_in_process), cần
Postgres Pagila (PIPELINE_DATABASE_URL) và mock API (PIPELINE_MOCK_API_URL) đang chạy: bật bằng ATOMESUS_E2E=1.
Số liệu review_api: 4.764 bản ghi, 47 gửi trùng `feedback_id` (báo cáo Sync Gate, mục 4; infra/mock_api/app.py).
"""

import os
import sys

import pytest

pytest.importorskip("dagster_dbt")

from src.dagster_runtime import execute  # noqa: E402
from src.dagster_runtime.loader import load_definitions  # noqa: E402
from src.dagster_runtime.secrets import read_model_env, sync_secrets  # noqa: E402
from src.pipeline.settings import PipelineSettings, warehouse_file  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402
from tests.test_dagster_runtime.specs import (  # noqa: E402
    RENTAL_MODEL,
    REVIEW_MODEL,
    install_parsed,
    rental_spec,
    review_spec,
)

FEEDBACK_ROWS, FEEDBACK_UNIQUE = 4764, 4764 - 47


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for key in [k for k in os.environ if k.startswith("SOURCES__") or k == "WAREHOUSE_PATH"]:
        monkeypatch.delenv(key)
    deploy, out, warehouse = tmp_path / "deploy", tmp_path / "secrets", tmp_path / "warehouse"
    install_parsed(deploy, review_spec())
    install_parsed(deploy, rental_spec())
    monkeypatch.setenv("ATOMESUS_SECRETS_DIR", str(out))
    monkeypatch.setenv("WAREHOUSE_DIR", str(warehouse))
    yield deploy, out, warehouse
    for name in [m for m in sys.modules if m.startswith("atomesus_pipeline_")]:
        sys.modules.pop(name)


def test_review_pipeline_gets_exactly_pagila_and_review_api(runtime):
    deploy, out, _ = runtime
    settings = PipelineSettings(
        _env_file=None,
        database_url="postgresql://pipeline_reader:reader-secret@postgres:5432/pipeline_demo",
        mock_api_url="http://mock-api:8100",
    )

    assert sync_secrets(deploy, out, load_policy(), settings) == {REVIEW_MODEL: None, RENTAL_MODEL: None}

    assert set(read_model_env(REVIEW_MODEL, out)) == {
        "SOURCES__PAGILA_PG__CREDENTIALS",
        "SOURCES__REVIEW_API__BASE_URL",
    }
    assert set(read_model_env(RENTAL_MODEL, out)) == {"SOURCES__PAGILA_PG__CREDENTIALS"}


def test_both_pipelines_load_together_and_write_their_own_duckdb(runtime, monkeypatch):
    deploy, out, warehouse = runtime
    sync_secrets(deploy, out, load_policy(), PipelineSettings(_env_file=None, database_url="postgresql://r:x@h/db"))

    defs, skipped = load_definitions(deploy)

    assert skipped == {}
    keys = {key.to_user_string() for key in defs.resolve_asset_graph().get_all_asset_keys()}
    assert {f"{REVIEW_MODEL}/bronze/raw_rental", f"{RENTAL_MODEL}/bronze/raw_rental"} <= keys, "cùng bảng, khác key"
    review_db = execute.scoped_env(REVIEW_MODEL)["WAREHOUSE_PATH"]
    assert review_db == str(warehouse_file(warehouse.resolve(), REVIEW_MODEL))
    assert review_db != execute.scoped_env(RENTAL_MODEL)["WAREHOUSE_PATH"]
    settings = PipelineSettings(_env_file=None, warehouse_path=str(warehouse.resolve() / "warehouse.duckdb"))
    warehouse.mkdir(parents=True, exist_ok=True)
    open(review_db, "wb").close()  # file riêng có rồi thì results / export / Day 2 đọc đúng file đó
    assert settings.warehouse_for(REVIEW_MODEL) == review_db


@pytest.mark.skipif(os.environ.get("ATOMESUS_E2E") != "1", reason="cần Postgres Pagila và mock API (ATOMESUS_E2E=1)")
def test_review_pipeline_runs_end_to_end(runtime, monkeypatch, tmp_path):
    import duckdb

    deploy, out, _ = runtime
    sync_secrets(deploy, out, load_policy(), PipelineSettings())
    monkeypatch.setenv("DBT_TARGET_PATH", str(tmp_path / "dbt_target"))
    defs, _ = load_definitions(deploy)

    result = defs.resolve_job_def(f"{REVIEW_MODEL}_job").execute_in_process()

    assert result.success
    with duckdb.connect(execute.warehouse_path(REVIEW_MODEL), read_only=True) as db:
        bronze = db.execute("select count(*), count(distinct feedback_id) from bronze.raw_feedback").fetchone()
        staging = db.execute(f"select count(*) from staging.stg_{REVIEW_MODEL}__feedback").fetchone()[0]
        gold = db.execute(f"select count(*) from gold.{REVIEW_MODEL}").fetchone()[0]
    assert bronze[0] in (FEEDBACK_ROWS, FEEDBACK_UNIQUE) and bronze[1] == FEEDBACK_UNIQUE, "dlt đọc hết các trang"
    assert staging == FEEDBACK_UNIQUE, "staging khử 47 bản gửi trùng"
    assert gold > 0
    assert not os.path.exists(execute.warehouse_path(RENTAL_MODEL)), "pipeline khác không bị ghi"
