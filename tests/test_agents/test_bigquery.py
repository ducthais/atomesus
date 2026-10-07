"""Cổng budget với `dry_run` thật của BigQuery (SPEC §12). Client BigQuery được thay bằng hàm giả (không gọi mạng)."""

import sys

import pytest

from src.agents import bigquery, budget
from src.agents.nodes.budget import budget_node
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.spec.models import Spec
from tests.test_agents.test_graph import SPEC, FakeLLM, deps

BQ = PipelineSettings(_env_file=None, bigquery_project="demo-project", bigquery_dataset="sales_pg")


def test_dry_run_sql_reads_only_the_columns_the_pipeline_uses():
    source = Spec.model_validate(SPEC).sources[0]

    sql = bigquery.source_sql(source, "demo-project", "sales_pg")

    used = {c.from_ for c in source.staging.columns} | set(source.primary_key)
    assert sql.startswith("SELECT ") and sql.endswith("FROM `demo-project.sales_pg.orders`")
    assert {part.strip(" `") for part in sql[len("SELECT ") : sql.index(" FROM")].split(",")} == used


def test_estimate_uses_real_dry_run_bytes_when_bigquery_is_configured():
    asked = []

    def dry_run(sql):
        asked.append(sql)
        return 3_000_000_000

    estimated = budget.estimate(Spec.model_validate(SPEC), load_policy(), BQ, dry_run=dry_run)

    assert estimated["sources"] == {"orders": 3_000_000_000} and estimated["estimated"] is False
    assert estimated["dry_run"] == {"warehouse": "bigquery", "sql": {"orders": asked[0]}}
    gate, errors = budget.gate(estimated, load_policy(), spent=0.0)
    assert gate.result == "fail" and errors[0].startswith("BigQuery dry_run quét 6,000,000,000 byte")


def test_bigquery_failure_stops_the_run_as_infra_error():
    def broken(spec, policy, settings):
        raise bigquery.BigQueryError("BigQuery dry_run lỗi: NotFound: Table sales_pg.orders")

    node = budget_node(deps(FakeLLM(), estimate_cost=broken))

    update = node({"spec": SPEC, "error_trace": []})

    assert update["status"] == "INFRA_ERROR" and "NotFound" in update["error_trace"][-1]["error"]


def test_missing_client_library_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", None)

    with pytest.raises(bigquery.BigQueryError, match="pip install google-cloud-bigquery"):
        bigquery.BigQueryDryRun("demo-project")


def test_without_bigquery_settings_the_estimate_is_unchanged(monkeypatch):
    monkeypatch.setattr(budget, "_source_bytes", lambda spec, policy, settings, skip=frozenset(): {"orders": 10})

    estimated = budget.estimate(Spec.model_validate(SPEC), load_policy(), PipelineSettings(_env_file=None))

    assert estimated["estimated"] is True and "dry_run" not in estimated and estimated["bytes"] == 20
