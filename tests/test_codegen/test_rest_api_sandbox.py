"""Nguồn rest_api chạy thật: mock API (uvicorn) → connector, resolve, rồi dlt + dbt trong sandbox (P9–P12)."""

import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from infra.mock_api.app import app as mock_api
from src.agents import resolve
from src.agents.validate import validate_spec
from src.pipeline import connectors, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy

pytest.importorskip("dbt.cli.main")
uvicorn = pytest.importorskip("uvicorn")

# Mỗi đơn i trả góp đủ i*1000 VND = tổng 465000; payment 7 (600) và 19 (1500) đổi sang 1.50 và 2.00 USD,
# payment 42 (2200) có amount "N/A" nên bị tách. 465000 - 600 + 38100 - 1500 + 50800 - 2200 = 549600.
EXPECTED_TOTAL_VND = 549600

SPEC = {
    "spec_version": "1.2",
    "domain": "sales",
    "sources": [
        {
            "id": "payments",
            "kind": "rest_api",
            "source_ref": "sales_api",
            "endpoint": "/payments",
            "records_path": "data",
            "pagination": "page",
            "primary_key": ["payment_id"],
            "cursor_field": None,
            "write_disposition": "replace",
            "target": {"dataset": "bronze", "table": "raw_payments"},
            "staging": {
                "columns": [
                    {"from": "payment_id", "as": "payment_id", "type": "integer"},
                    {"from": "order_id", "as": "order_id", "type": "integer"},
                    {
                        "from": "amount",
                        "as": "amount_vnd",
                        "type": "decimal(18,2)",
                        "currency": {"code_column": "currency", "to": "VND"},
                    },
                    {"from": "paid_at", "as": "paid_at", "type": "timestamp"},
                ],
                "dedupe": {"keys": ["payment_id"], "keep": "first", "order_by": "payment_id"},
                "reject_if": [],
            },
        }
    ],
    "transform": {
        "model_name": "fct_order_payments",
        "from": "payments",
        "joins": [],
        "materialization": "table",
        "unique_key": ["order_id"],
        "grain": ["order_id"],
        "metrics": [
            {"name": "total_paid", "expr": "SUM(amount_vnd)"},
            {"name": "payment_count", "expr": "COUNT(payment_id)"},
        ],
        "filters": [],
        "tests": [
            {"column": "order_id", "test": "not_null"},
            {"column": "order_id", "test": "unique"},
            {"column": "total_paid", "test": "not_negative"},
        ],
    },
    "schedule": {"cron": "0 2 * * *"},
    "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
}


@pytest.fixture
def in_process(monkeypatch):
    """Connector gọi thẳng ứng dụng mock, không cần mạng."""
    monkeypatch.setattr(rest_api, "client", lambda url: TestClient(mock_api, base_url=url))
    return PipelineSettings(_env_file=None)


@pytest.fixture(scope="module")
def served():
    """Mock API thật trên một cổng trống: dlt trong tiến trình con cần gọi qua HTTP."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mock_api, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield PipelineSettings(_env_file=None, mock_api_url=f"http://127.0.0.1:{port}")
    server.should_exit = True
    thread.join(timeout=5)


def test_discovers_endpoints_from_openapi(in_process):
    policy = load_policy()
    found = connectors.discover(policy.domains.domains["sales"].sources["sales_api"], in_process)
    (payments,) = found["objects"]
    assert payments["name"] == "payments" and payments["kind"] == "endpoint"
    assert payments["primary_key"] == ["payment_id"] and found["foreign_keys"] == []
    types = {c["name"]: c["type"] for c in payments["columns"]}
    assert types == {
        "payment_id": "integer",
        "order_id": "integer",
        "amount": "text",
        "currency": "text",
        "paid_at": "timestamp with time zone",
        "method": "text",
    }
    marketing = connectors.discover(policy.domains.domains["marketing"].sources["mkt_api"], in_process)
    assert sorted(o["name"] for o in marketing["objects"]) == ["contacts", "leads"], "mỗi domain một mô tả OpenAPI"


def test_reads_every_page_and_counts_distinct_keys(in_process):
    decl = load_policy().domains.domains["sales"].sources["sales_api"]
    endpoint = rest_api.endpoints(decl, in_process)["payments"]
    assert (endpoint["records_path"], endpoint["pagination"]) == ("data", "page")
    rows = list(rest_api.records(decl, in_process, endpoint))
    assert len(rows) == 122 and rest_api.count(rows, ["payment_id"]) == 120, "2 bản ghi gửi lặp"
    assert len(rest_api.sample(decl, in_process, endpoint, 5)) == 5


def test_catalog_and_snapshot_for_the_agent(in_process, monkeypatch):
    policy = load_policy()
    monkeypatch.setattr(resolve, "_tables", lambda settings, schema: {})  # không cần Postgres cho phần này
    monkeypatch.setattr(resolve, "_relations", lambda settings, schema: [])
    catalog = resolve.catalog(policy, in_process)
    api = catalog["sales"]["sources"]["sales_api"]
    assert api["available"] and "paid_at" in api["objects"]["payments"]
    plan = {"domain": "sales", "sources": [{"id": "payments", "source_ref": "sales_api", "object": "payments"}]}
    snap = resolve.snapshot(plan, policy, in_process)["payments"]
    assert (snap["kind"], snap["endpoint"], snap["records_path"], snap["pagination"]) == (
        "rest_api",
        "/payments",
        "data",
        "page",
    )
    assert len(snap["sample_rows"]) == resolve.SAMPLE_ROWS and snap["sample_rows"][0]["payment_id"] == "1"
    result = validate_spec(SPEC, {"payments": snap}, policy)
    assert not result.errors, result.errors


def test_api_down_only_disables_that_source(monkeypatch):
    policy = load_policy()
    monkeypatch.setattr(resolve, "_tables", lambda settings, schema: {})
    monkeypatch.setattr(resolve, "_relations", lambda settings, schema: [])
    down = PipelineSettings(_env_file=None, mock_api_url="http://127.0.0.1:9")
    api = resolve.catalog(policy, down)["sales"]["sources"]["sales_api"]
    assert api["available"] is False and "mock_api" in api["reason"] and "127.0.0.1" not in api["reason"]


def test_sandbox_loads_api_into_bronze_and_cleans_it(served):
    from src.codegen.render import render
    from src.codegen.sandbox import run_in_sandbox
    from src.spec.models import Spec

    policy = load_policy()
    spec = Spec.model_validate(SPEC)
    result = run_in_sandbox(spec, render(spec, policy), policy, served)
    assert result.status == "READY_FOR_REVIEW", result.error_trace
    checks = {c.name: c for c in result.checks}
    assert checks["row_reconcile"].result == "pass"
    assert checks["row_reconcile"].observed == {"source": 122, "bronze": 122, "source_records": 122}
    staged = checks["staging_reconcile"].observed
    assert (staged["bronze"], staged["staging"], staged["duplicates"], staged["rejected"]) == (122, 119, 2, 1)
    assert checks["dbt_test"].result == "pass"
    gold = result.gates[0].observed
    assert gold["gold_rows"] == 30
    total = next(c for c in gold["gold_profile"] if c["name"] == "total_paid")
    assert float(total["sum"]) == EXPECTED_TOTAL_VND
    assert "rest_api" in gold["reconcile_skipped"], "chưa có đường đối chiếu độc lập cho API: ghi rõ, không im lặng"


def test_merge_dedupes_in_bronze_and_counts_distinct_keys_at_source(served):
    from src.codegen.render import render
    from src.codegen.sandbox import run_in_sandbox
    from src.spec.models import Spec

    raw = {**SPEC, "sources": [{**SPEC["sources"][0], "write_disposition": "merge", "cursor_field": "paid_at"}]}
    policy = load_policy()
    spec = Spec.model_validate(raw)
    result = run_in_sandbox(spec, render(spec, policy), policy, served)
    assert result.status == "READY_FOR_REVIEW", result.error_trace
    checks = {c.name: c.observed for c in result.checks}
    assert checks["row_reconcile"] == {"source": 120, "bronze": 120, "source_records": 122}
    assert (checks["staging_reconcile"]["duplicates"], checks["staging_reconcile"]["rejected"]) == (0, 1)


def test_hash_and_idempotent_checks_pass_for_a_replace_load(served):
    from src.codegen.render import render
    from src.codegen.sandbox import run_in_sandbox
    from src.spec.models import Spec

    policy = load_policy()
    spec = Spec.model_validate(SPEC)
    result = run_in_sandbox(spec, render(spec, policy), policy, served)
    checks = {c.name: c for c in result.checks}
    hashed = checks["hash_reconcile"].observed
    assert checks["hash_reconcile"].result == "pass" and hashed["source_keys"] == hashed["bronze_keys"] == 120
    assert hashed["source_hash"] == hashed["bronze_hash"] and hashed["missing_in_bronze"] == []
    again = checks["idempotent"].observed
    assert checks["idempotent"].result == "pass" and again["first_rows"] == again["second_rows"] == 122


def test_incremental_append_loads_nothing_new_on_the_second_run(served):
    from src.codegen.render import render
    from src.codegen.sandbox import run_in_sandbox
    from src.spec.models import Spec

    raw = {**SPEC, "sources": [{**SPEC["sources"][0], "write_disposition": "append", "cursor_field": "paid_at"}]}
    policy = load_policy()
    spec = Spec.model_validate(raw)
    result = run_in_sandbox(spec, render(spec, policy), policy, served)
    check = next(c for c in result.checks if c.name == "idempotent")
    assert check.result == "pass" and check.observed["first_rows"] == check.observed["second_rows"]


def test_a_second_run_that_changes_bronze_fails_idempotent():
    from src.codegen.el_checks import Side, idempotent
    from src.spec.models import Spec

    source = Spec.model_validate(SPEC).sources[0]
    first = Side(122, {(str(i),) for i in range(1, 121)})
    check = idempotent(source, first, Side(244, first.keys))
    assert check.result == "fail" and (check.observed["first_rows"], check.observed["second_rows"]) == (122, 244)
    assert idempotent(source, first, Side(122, set(first.keys))).result == "pass"
