"""So sánh phiên bản pipeline và ảnh hưởng khi duyệt (src/run_report/compare.py) + endpoint GET /runs/{id}/compare."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.run_report import RunReport
from src.run_report.compare import compare, impact, pick_base
from src.run_report.store import save_run_files, save_run_report

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def spec(model="fct_daily_sales", grain="orders.ordered_at", amount_as="amount_vnd", bronze="raw_orders"):
    return {
        "domain": "sales",
        "sources": [
            {
                "id": "orders",
                "kind": "postgres",
                "source_ref": "sales_pg",
                "table": "orders",
                "primary_key": ["order_id"],
                "write_disposition": "replace",
                "target": {"dataset": "bronze", "table": bronze},
                "staging": {
                    "columns": [
                        {"from": "order_id", "as": "order_id", "type": "integer"},
                        {"from": "amount_text", "as": amount_as, "type": "decimal(18,2)"},
                    ],
                    "reject_if": ["order_id IS NULL"],
                },
            }
        ],
        "transform": {
            "model_name": model,
            "grain": [grain],
            "metrics": [{"name": "total_revenue", "expr": "SUM(amount)"}],
            "filters": [],
        },
        "schedule": {"cron": "0 2 * * *"},
    }


def report(
    run_id, minutes, status="READY_FOR_REVIEW", spec_=None, rows=30, total="465000.00", parent=None, profile=True
):
    observed = {"gold_rows": rows, "gold_samples": [{"ordered_at": "2026-01-01", "total_revenue": "1000.00"}]}
    if profile:
        observed["gold_profile"] = [
            {"name": "ordered_at", "type": "DATE", "non_null": rows},
            {
                "name": "total_revenue",
                "type": "DECIMAL(18,2)",
                "non_null": rows,
                "sum": total,
                "min": "1000.00",
                "max": "30000.00",
            },
        ]
    data = {
        "run_id": run_id,
        "version": 1,
        "domain": "sales",
        "created_by": "minh",
        "trigger": "user_request",
        "parent_run_id": parent,
        "created_at": (T0 + timedelta(minutes=minutes)).isoformat(),
        "user_request": "Doanh thu theo ngày",
        "status": status,
        "stage_reached": "report" if status not in ("APPROVED", "DEPLOYED") else "review",
        "spec": spec_ or spec(),
        "files": {"dbt_project/models/gold/fct_daily_sales.sql": "sha256:x"},
        "files_hash": f"sha256:{run_id}",
        "gates": [{"name": "post_run", "result": "pass", "detail": "ok", "observed": observed}],
        "checks": [{"name": "dbt_test", "source_id": None, "result": "pass", "observed": {"passed": 4, "failed": 0}}],
        "cost": {"estimated_bytes": 0, "actual_bytes": 0, "usd": 0},
        "llm_calls": 2,
    }
    if status in ("APPROVED", "DEPLOYED"):
        data["approval"] = {
            "decision": "approved",
            "reviewer": "an",
            "files_hash": f"sha256:{run_id}",
            "version": 1,
            "at": (T0 + timedelta(minutes=minutes + 5)).isoformat(),
        }
    return RunReport.model_validate(data)


def test_base_prefers_parent_then_deployed_then_previous():
    deployed = report("dep", 0, "DEPLOYED")
    older = report("old", 10)
    child = report("new", 20, parent="old")
    assert pick_base(child, [deployed, older, child])[0].run_id == "old"
    plain = report("new2", 30)
    base, reason = pick_base(plain, [deployed, older, plain])
    assert base.run_id == "dep" and "Dagster" in reason
    assert pick_base(report("x", 40), [older, report("x", 40)])[0].run_id == "old"
    other_model = report("y", 50, spec_=spec(model="fct_other"))
    assert pick_base(other_model, [deployed, older, other_model]) == (None, "")


def test_spec_result_and_code_changes():
    base, new = (
        report("old", 0),
        report("new", 10, spec_=spec(grain="orders.order_month", amount_as="amount_usd"), rows=3, total="465100.00"),
    )
    result = compare(
        new,
        base,
        "bản trước",
        {"a.sql": "select 1\nfrom t\n", "b.py": "x = 1\n"},
        {"a.sql": "select 1\n", "c.yml": "k: v\n"},
        [base, new],
    )
    changed = {c["field"]: c for c in result["spec"]}
    assert changed["Gộp theo"] == {
        "field": "Gộp theo",
        "before": ["orders.ordered_at"],
        "after": ["orders.order_month"],
    }
    assert "amount_text → amount_usd (decimal(18,2))" in changed["Mapping cột · orders"]["after"]
    assert "Bộ lọc" not in changed, "mục không đổi thì không liệt kê"
    assert result["results"]["rows"] == {"before": 30, "after": 3}
    metric = result["results"]["metrics"][0]
    assert (
        metric["name"] == "total_revenue" and metric["before"] == "465000.00" and round(metric["delta_pct"], 3) == 0.022
    )
    files = {f["path"]: f for f in result["files"]}
    assert files["a.sql"]["status"] == "changed" and files["a.sql"]["added_lines"] == 1
    assert files["b.py"]["status"] == "added" and files["c.yml"]["status"] == "removed"


def test_result_changes_without_profile_fall_back_to_sample_columns():
    base, new = report("old", 0, profile=False), report("new", 10)
    result = compare(new, base, "bản trước", {}, {}, [base, new])["results"]
    assert result["columns"] == {"added": [], "removed": [], "type_changed": []}
    assert result["profiled"] is False


def test_impact_lists_replaced_version_and_pipelines_sharing_bronze():
    live = report("dep", 0, "DEPLOYED")
    neighbour = report("other", 1, "DEPLOYED", spec_=spec(model="fct_customer_orders"))
    unrelated = report("far", 2, "DEPLOYED", spec_=spec(model="fct_x", bronze="raw_customers"))
    candidate = report("new", 10)
    result = impact(candidate, [live, neighbour, unrelated, candidate])
    assert result["replaces"]["run_id"] == "dep"
    assert result["shared_bronze"] == [
        {"model": "fct_customer_orders", "run_id": "other", "bronze": ["bronze.raw_orders"]}
    ]


@pytest.mark.asyncio
async def test_compare_endpoint(client, tmp_path, monkeypatch):
    from src.main import app
    from src.pipeline import auth
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    for r, files in [
        (report("dep", 0, "DEPLOYED"), {"a.sql": "select 1\n"}),
        (report("new", 10), {"a.sql": "select 2\n"}),
    ]:
        save_run_report(config.store_path, r)
        save_run_files(config.store_path, r.run_id, 1, files)
    lonely = report("lonely", 20, spec_=spec(model="fct_alone"))
    save_run_report(config.store_path, lonely)
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        body = (await client.get("/api/v1/runs/new/compare")).json()
        assert body["available"] is True and body["base"]["run_id"] == "dep"
        assert body["files"][0]["before"] == "select 1\n" and body["files"][0]["after"] == "select 2\n"
        assert body["impact"]["replaces"]["run_id"] == "dep"
        assert (await client.get("/api/v1/runs/lonely/compare")).json()["available"] is False
        assert (await client.get("/api/v1/runs/new/compare?base=missing")).status_code == 404
        json.dumps(body)  # trả về JSON được
    finally:
        app.dependency_overrides.pop(auth.settings, None)


def test_warehouse_timezone_change_is_shown_as_a_config_change():
    from src.run_report.compare import _config_changes

    def with_tz(r, tz):
        gates = [
            g.model_copy(update={"observed": {**g.observed, "warehouse_timezone": tz}}) if g.name == "post_run" else g
            for g in r.gates
        ]
        return r.model_copy(update={"gates": gates})

    old, new = with_tz(report("a", 0), "Asia/Ho_Chi_Minh"), with_tz(report("b", 5), "UTC")
    assert _config_changes(old, new) == [
        {"field": "Cấu hình chạy thử · múi giờ warehouse", "before": ["Asia/Ho_Chi_Minh"], "after": ["UTC"]}
    ]
    assert _config_changes(new, new) == [] and _config_changes(report("c", 0), new) == []
