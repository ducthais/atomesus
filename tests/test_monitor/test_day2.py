"""Vòng Day 2: luật kích hoạt → run sửa (một lần trong cooldown), nguồn ngừng cấp → chỉ cảnh báo, run sửa kẹt → mời người."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.agents import regression
from src.monitor import alerts, day2, runs
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.run_report import RunReport, get_run_report, save_run_report
from src.run_report.store import FIXTURES_DIR
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps

MODEL = "fct_daily_orders"
T0 = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)


@pytest.fixture
def env(tmp_path):
    settings = PipelineSettings(
        _env_file=None,
        store_path=str(tmp_path / "runs.sqlite3"),
        deploy_dir=str(tmp_path / "deploy"),
        warehouse_path=str(tmp_path / "warehouse.duckdb"),
    )
    (tmp_path / "deploy" / MODEL / "dbt_project" / "target").mkdir(parents=True)
    report = RunReport.model_validate_json((FIXTURES_DIR / "deployed.json").read_text(encoding="utf-8"))
    timing, cost = report.timing.model_copy(update={"baseline_sec": 10.0}), report.cost.model_copy(update={"usd": 1e-6})
    report = report.model_copy(update={"spec": SPEC, "plan": PLAN, "timing": timing, "cost": cost})
    save_run_report(settings.store_path, report)
    return SimpleNamespace(settings=settings, report=report, policy=load_policy())


def record(env, seconds: list[float], sizes: list[int]) -> datetime:
    """Ghi các lần chạy theo lịch, mỗi giờ một lần; trả thời điểm ngay sau lần cuối."""
    for i, (duration, size) in enumerate(zip(seconds, sizes, strict=True)):
        start = (T0 + timedelta(hours=i)).timestamp()
        found = [
            {
                "runId": f"r{i}",
                "jobName": f"{MODEL}_job",
                "status": "SUCCESS",
                "startTime": start,
                "endTime": start + duration,
            }
        ]
        runs.collect(
            env.settings,
            env.policy,
            runs=lambda url, found=found: found,
            estimate=lambda *a: {"bytes": 1, "usd": 1e-6},
            counts=lambda *a, size=size: {"orders": size},
        )
    return T0 + timedelta(hours=len(seconds) - 1, minutes=1)


def system_launch(env):
    """Launch thật nhưng không chạy job nền: chỉ ghi run sửa vào sổ bằng chứng."""
    return day2.launcher(deps(FakeLLM()), env.settings.store_path, submit=lambda job: None)


def test_slow_pipeline_gets_one_fix_run_per_cooldown(env):
    now = record(env, [16.0] * 7, [100 + i for i in range(7)])  # trung vị +60% so với cơ sở 10 giây

    first = day2.check(env.settings, env.policy, system_launch(env), now=now)
    again = day2.check(env.settings, env.policy, system_launch(env), now=now + timedelta(hours=1))
    later = day2.check(env.settings, env.policy, system_launch(env), now=now + timedelta(hours=25))

    assert [(a["action"], a["rules"]) for a in first] == [("fix_started", ["runtime_regression"])]
    assert again[0]["action"] == "cooldown" and again[0]["run_id"] == first[0]["run_id"]
    assert later[0]["action"] == "fix_started", "hết 24 giờ (cooldown_hours) thì được tạo bản sửa mới"


def test_runs_before_the_current_deploy_are_ignored(env):
    now = record(env, [16.0] * 7, [100 + i for i in range(7)])
    approval = env.report.approval.model_copy(update={"at": now})
    save_run_report(env.settings.store_path, env.report.model_copy(update={"approval": approval}))

    assert day2.check(env.settings, env.policy, system_launch(env), now=now) == []


def test_stalled_source_only_alerts_once(env):
    now = record(env, [10.0] * 3, [100, 100, 100])  # 2 giờ không có bản ghi mới, độ tươi sales là 60 phút
    launched = []

    first = day2.check(env.settings, env.policy, lambda *a: launched.append(a), now=now)
    again = day2.check(env.settings, env.policy, lambda *a: launched.append(a), now=now)

    assert [a["action"] for a in first] == ["alert"] and [a["action"] for a in again] == ["already_alerted"]
    assert launched == [], "nguồn ngừng cấp là sự cố hạ tầng, không tạo run sửa"
    (alert,) = alerts.list_alerts(env.settings.store_path, MODEL)
    assert alert["kind"] == "source_stalled" and alert["detail"]["stalled_sources"] == ["orders"]


def _fix(env, status: str, **changes) -> RunReport:
    started, _job = regression.start_regression(
        env.report, {"model": MODEL, "rules": []}, deps(FakeLLM()), env.settings.store_path
    )
    return started.model_copy(update={"status": status, **changes})


def test_fix_without_improvement_is_flagged(env):
    regression_info = {"model": MODEL, "rules": [], "verdict": {"improved": False, "reason": "Không nhanh hơn."}}
    report = _fix(env, "NO_IMPROVEMENT", regression=regression_info)

    alert = day2.follow_up(report, env.report, env.settings.store_path)

    assert alert["kind"] == "no_improvement" and alert["run_id"] == report.run_id and "gắn cờ" in alert["message"]


def test_fix_stuck_at_a_ceiling_invites_the_owner(env):
    trace = [{"stage": "static", "error": "Spec vẫn sai sau 2 lần sửa (budgets.yml max_spec_attempts)"}]
    report = _fix(env, "NEEDS_HUMAN", stage_reached="static", error_trace=trace)

    alert = day2.follow_up(report, env.report, env.settings.store_path)

    assert alert["kind"] == "needs_human" and alert["owner"] == "minh"
    assert "Mời minh mở run" in alert["message"] and "max_spec_attempts" in alert["message"]


def test_ready_fix_needs_no_alert(env):
    assert day2.follow_up(_fix(env, "READY_FOR_REVIEW"), env.report, env.settings.store_path) is None


@pytest.mark.asyncio
async def test_alert_api(client, env, monkeypatch):
    from src.api import routes
    from src.main import app
    from src.pipeline import auth

    record(env, [10.0] * 3, [100, 100, 100])
    app.dependency_overrides[auth.settings] = lambda: env.settings
    app.dependency_overrides[routes.agent_deps] = lambda: deps(FakeLLM(), policy=env.policy)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        assert (await client.post("/api/v1/monitor/check")).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        checked = (await client.post("/api/v1/monitor/check")).json()
        assert [a["action"] for a in checked["actions"]] == ["alert"]
        (alert,) = (await client.get("/api/v1/monitor/alerts")).json()
        resolved = (await client.post(f"/api/v1/monitor/alerts/{alert['id']}/resolve")).json()
        assert resolved["resolved_at"] is not None
        assert (await client.get("/api/v1/monitor/alerts")).json() == []
        assert json.dumps((await client.get("/api/v1/monitor/alerts?include_resolved=true")).json()).count('"id"') == 1
        assert (await client.post("/api/v1/monitor/alerts/999/resolve")).status_code == 404
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


@pytest.mark.asyncio
async def test_manual_optimize_api_is_engineer_only_and_starts_person_three_flow(client, env, monkeypatch):
    from src.api import routes
    from src.main import app
    from src.pipeline import auth

    app.dependency_overrides[auth.settings] = lambda: env.settings
    app.dependency_overrides[routes.agent_deps] = lambda: deps(FakeLLM(SPEC), policy=env.policy)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        forbidden = await client.post(f"/api/v1/pipelines/{MODEL}/optimize")
        assert forbidden.status_code == 403

        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        assert (await client.post("/api/v1/pipelines/missing/optimize")).status_code == 404
        response = await client.post(f"/api/v1/pipelines/{MODEL}/optimize")

        assert response.status_code == 202
        started = response.json()
        assert started["trigger"] == "manual_optimize" and started["created_by"] == "minh"
        saved = get_run_report(env.settings.store_path, started["run_id"])
        assert saved is not None and saved.status in {"READY_FOR_REVIEW", "NO_IMPROVEMENT"}
        assert saved.parent_run_id == env.report.run_id
        assert saved.regression["kind"] == "manual_optimize"
        assert saved.regression["verdict"]["improved"] is (saved.status == "READY_FOR_REVIEW")
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)
