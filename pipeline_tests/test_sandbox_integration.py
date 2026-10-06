"""Chạy thử thật P9–P12 (dlt → bronze → dbt build) trên Postgres seed qua PIPELINE_TEST_DATABASE_URL."""

import os

import pytest

pytest.importorskip("dlt")
pytest.importorskip("dbt.cli.main")

from src.codegen.render import render  # noqa: E402
from src.codegen.sandbox import run_in_sandbox  # noqa: E402
from src.pipeline.settings import PipelineSettings  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402
from src.spec import SPEC_FIXTURES_DIR, Spec  # noqa: E402


@pytest.fixture
def spec():
    return Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))


def test_seeded_orders_pass_every_check(spec):
    url = os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PIPELINE_TEST_DATABASE_URL to the dedicated seeded test database")
    policy = load_policy()
    result = run_in_sandbox(spec, render(spec, policy), policy, PipelineSettings(_env_file=None, database_url=url))
    assert result.status == "READY_FOR_REVIEW", result.error_trace
    checks = {check.name: check for check in result.checks}
    assert checks["row_reconcile"].observed == {"source": 30, "bronze": 30}
    assert checks["hash_reconcile"].result == "pass" and checks["hash_reconcile"].observed["source_keys"] == 30
    assert checks["idempotent"].result == "pass" and checks["idempotent"].observed["second_rows"] == 30
    assert checks["staging_reconcile"].observed["staging"] == 30
    assert checks["dbt_test"].observed["failed"] == 0
    (post_run,) = result.gates
    assert post_run.observed["gold_rows"] == 30
    assert post_run.observed["gold_samples"][0] == {
        "order_date": "2026-01-01",
        "revenue": "1000.00",
        "order_count": "1",
    }


def test_unreachable_source_is_infra_error(spec):
    policy = load_policy()
    config = PipelineSettings(_env_file=None, database_url="postgresql://reader:x@127.0.0.1:1/none")
    result = run_in_sandbox(spec, render(spec, policy), policy, config)
    assert result.status == "INFRA_ERROR" and result.stage_reached == "el_run"
    assert result.checks == [] and result.error_trace[-1]["source_id"] == "orders"
    assert "secret" not in str(result.error_trace) and "reader:x" not in str(result.error_trace)
