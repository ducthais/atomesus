"""Số đo các lần pipeline chạy theo lịch (Day 2): thời gian, chi phí, test dbt, số dòng gold, bản ghi mới ở nguồn."""

import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.monitor import runs
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.run_report import RunReport, save_run_report
from src.spec import SPEC_FIXTURES_DIR

MODEL = "fct_daily_orders"  # spec_01
T0 = datetime(2026, 10, 6, 2, 0, tzinfo=UTC).timestamp()


def _run(run_id, start, seconds, status="SUCCESS", job=f"{MODEL}_job"):
    return {"runId": run_id, "jobName": job, "status": status, "startTime": start, "endTime": start + seconds}


@pytest.fixture
def env(tmp_path):
    settings = PipelineSettings(
        _env_file=None,
        store_path=str(tmp_path / "runs.sqlite3"),
        deploy_dir=str(tmp_path / "deploy"),
        warehouse_path=str(tmp_path / "warehouse.duckdb"),
    )
    project = tmp_path / "deploy" / MODEL
    (project / "dbt_project" / "target").mkdir(parents=True)
    (tmp_path / "deploy" / f"{MODEL}.tmp").mkdir()  # deploy đang ghi dở: bỏ qua
    spec = json.loads((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))
    report = RunReport.model_validate_json(open("fixtures/run_reports/deployed.json", encoding="utf-8").read())
    timing, cost = report.timing.model_copy(update={"baseline_sec": 12.5}), report.cost.model_copy(update={"usd": 1e-7})
    save_run_report(settings.store_path, report.model_copy(update={"spec": spec, "timing": timing, "cost": cost}))
    import duckdb

    with duckdb.connect(settings.warehouse_path) as db:
        db.execute("create schema gold")
        db.execute(f"create table gold.{MODEL} as select range as order_date from range(30)")
    return SimpleNamespace(settings=settings, project=project, report=report)


def _write_results(project, at, failed):
    path = project / "dbt_project" / "target" / f"run_{at}" / "run_results.json"
    path.parent.mkdir(parents=True)
    tests = [{"unique_id": f"test.x.t{i}", "status": "fail" if i < failed else "pass"} for i in range(4)]
    path.write_text(
        json.dumps({"results": [{"unique_id": "model.x.m", "status": "success"}, *tests]}), encoding="utf-8"
    )
    os.utime(path, (at + 30, at + 30))


def test_collects_finished_runs_once_with_their_measurements(env):
    _write_results(env.project, T0, failed=0)
    _write_results(env.project, T0 + 3600, failed=1)
    counts = iter([{"orders": 30}])
    found = [
        _run("r1", T0, 20),
        _run("r2", T0 + 3600, 45, status="FAILURE"),
        _run("r3", T0 + 7200, 5, status="STARTED"),  # chưa xong: lần sau mới ghi
        _run("x1", T0, 5, job="__ASSET_JOB"),  # job ẩn của Dagster
        _run("x2", T0, 5, job="other_model_job"),  # pipeline không còn deploy
    ]
    estimate = lambda spec, policy, settings: {"bytes": 16384, "usd": 1.024e-07}  # noqa: E731
    written = runs.collect(
        env.settings, load_policy(), runs=lambda url: found, estimate=estimate, counts=lambda *a: next(counts)
    )
    assert [r["run_id"] for r in written] == ["r1", "r2"]
    first, second = written
    assert (first["duration_sec"], first["status"], first["gold_rows"]) == (20, "SUCCESS", 30)
    assert (first["tests"], first["tests_failed"], second["tests_failed"]) == (4, 0, 1)
    assert first["estimated_bytes"] == 16384 and first["source_records"] == {"orders": 30}
    again = runs.collect(env.settings, load_policy(), runs=lambda url: found, estimate=estimate, counts=lambda *a: {})
    assert again == [], "thu lại không ghi trùng"


def test_series_has_a_baseline_and_new_records_per_source(env):
    sizes = iter([{"orders": 30}, {"orders": 30}, {"orders": 45}])
    estimate = lambda *a: {"bytes": 1, "usd": 0.0}  # noqa: E731
    for i in range(3):  # mỗi lần thu một lần chạy mới
        runs.collect(
            env.settings,
            load_policy(),
            runs=lambda url, i=i: [_run(f"r{i}", T0 + i * 3600, 10 + i)],
            estimate=estimate,
            counts=lambda *a: next(sizes),
        )
    found = runs.series(env.settings.store_path, MODEL)
    assert found["baseline"]["duration_sec"] == 12.5 and found["baseline"]["run_id"] == env.report.run_id
    assert [r["new_records"] for r in found["runs"]] == [None, {"orders": 0}, {"orders": 15}]
    assert [r["duration_sec"] for r in found["runs"]] == [10, 11, 12]


def test_unreadable_source_still_records_the_timing(env):
    def broken(*args):
        raise RuntimeError("API nguồn không trả lời")

    written = runs.collect(
        env.settings,
        load_policy(),
        runs=lambda url: [_run("r1", T0, 20)],
        estimate=lambda *a: {"bytes": 1, "usd": 0.0},
        counts=broken,
    )
    assert written[0]["duration_sec"] == 20 and "API nguồn không trả lời" in written[0]["source_records"]["error"]


def test_dagster_unreachable_is_a_monitor_error():
    with pytest.raises(runs.MonitorError):
        runs.fetch_runs("http://127.0.0.1:9", timeout=0.5)


@pytest.mark.asyncio
async def test_monitor_api(client, env, monkeypatch):
    from src.main import app
    from src.pipeline import auth

    app.dependency_overrides[auth.settings] = lambda: env.settings
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    monkeypatch.setattr(runs, "fetch_runs", lambda url, timeout=5.0: [_run("r1", T0, 20)])
    monkeypatch.setattr(runs, "source_records", lambda *a: {"orders": 30})
    monkeypatch.setattr(runs.budget, "estimate", lambda *a: {"bytes": 1, "usd": 0.0})
    try:
        assert (await client.post("/api/v1/monitor/collect")).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        collected = (await client.post("/api/v1/monitor/collect")).json()
        assert collected["collected"] == 1
        listing = (await client.get("/api/v1/monitor/pipelines")).json()
        (item,) = listing
        assert (item["model"], item["runs"], item["last"]["run_id"]) == (MODEL, 1, "r1")
        assert item["domain"] == "sales" and item["deployed_run_id"] == env.report.run_id
        assert item["baseline"]["duration_sec"] == 12.5
        # Lần chạy cuối là 06/10: quá độ tươi 60 phút của sales, luật chỉ đọc nên không tạo run sửa nào.
        assert [f["rule"] for f in item["findings"]] == ["freshness_breach"]
        assert (await client.get(f"/api/v1/monitor/pipelines/{MODEL}")).json()["baseline"]["duration_sec"] == 12.5
        assert (await client.get("/api/v1/monitor/pipelines/khong_co")).status_code == 404
    finally:
        app.dependency_overrides.pop(auth.settings, None)
