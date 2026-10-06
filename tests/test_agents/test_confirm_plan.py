"""Cổng người P2: agent dừng sau kế hoạch, chỉ viết Spec khi Engineer đã xác nhận (FR-03, SPEC §11.1)."""

from types import SimpleNamespace

import pytest

from src.agents.service import PlanNotAwaitingError, confirm_plan, continue_agent, plan_agent, start_confirm
from src.pipeline import auth
from src.run_report import get_run_report
from src.run_report.store import abandon_unfinished
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, tiered_llm


def test_plan_stops_before_spec(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    llm = FakeLLM(PLAN)
    report = plan_agent("Doanh thu theo ngày", "minh", deps(llm), store)
    assert (report.status, report.stage_reached) == ("DRAFT", "confirm")
    assert report.plan["model_name"] == PLAN["model_name"] and report.spec is None and not report.files
    assert len(llm.calls) == 1 and llm.calls[0]["tier"] == "cheap", "chỉ gọi planner"
    assert [e.event for e in report.timeline] == ["submitted", "plan_ready"]
    assert get_run_report(store, report.run_id) == report


def test_confirm_runs_codegen_and_sandbox_on_the_same_run(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(FakeLLM(PLAN, SPEC))
    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)
    done = confirm_plan(planned, "minh", agent, store)
    assert done.run_id == planned.run_id and done.status == "READY_FOR_REVIEW"
    assert done.llm_calls == 2 and done.spec["transform"]["model_name"] == SPEC["transform"]["model_name"]
    assert [(e.event, e.actor) for e in done.timeline][1:3] == [("plan_ready", None), ("plan_confirmed", "minh")]


def test_confirm_with_mapping_review_stops_after_spec(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(FakeLLM(PLAN, SPEC))
    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)
    draft = confirm_plan(planned, "minh", agent, store, review_mapping=True)
    assert (draft.status, draft.stage_reached) == ("DRAFT", "static") and not draft.files
    done = continue_agent(draft, draft.spec, "Minh", agent, store)
    assert done.status == "READY_FOR_REVIEW"
    assert [e.event for e in done.timeline] == [
        "submitted",
        "plan_ready",
        "plan_confirmed",
        "spec_ready",
        "mapping_submitted",
        "finished",
    ]


def test_only_a_plan_waiting_for_confirmation_can_be_confirmed(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(FakeLLM(PLAN, SPEC))
    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)
    confirmed, _job = start_confirm(planned, "minh", agent, store)
    assert confirmed.status == "PLAN_CONFIRMED"
    with pytest.raises(PlanNotAwaitingError):
        start_confirm(confirmed, "minh", agent, store)

    unclear = plan_agent("Doanh thu", "minh", deps(FakeLLM({**PLAN, "question": "Theo ngày hay tháng?"})), store)
    assert unclear.status == "NEEDS_HUMAN", "kế hoạch còn câu hỏi thì không tới cổng xác nhận"
    with pytest.raises(PlanNotAwaitingError):
        start_confirm(unclear, "minh", agent, store)


def test_restart_closes_confirmed_runs_but_keeps_plans_waiting(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(FakeLLM(PLAN, SPEC))
    waiting = plan_agent("Doanh thu theo ngày", "minh", agent, store)
    confirmed, _job = start_confirm(plan_agent("x", "minh", deps(FakeLLM(PLAN)), store), "minh", agent, store)
    assert abandon_unfinished(store) == [confirmed.run_id]  # backend tắt trước khi việc nền chạy
    assert get_run_report(store, waiting.run_id).status == "DRAFT"


@pytest.mark.asyncio
async def test_confirm_plan_api(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        assert (await client.post("/api/v1/agent/runs/x/confirm-plan", json={})).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        assert (await client.post("/api/v1/agent/runs/missing/confirm-plan", json={})).status_code == 404
        run_id = (await client.post("/api/v1/agent/runs", json={"request": "Doanh thu theo ngày"})).json()["run_id"]
        url = f"/api/v1/agent/runs/{run_id}/confirm-plan"

        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        assert (await client.post(url, json={})).status_code == 403
        assert get_run_report(config.store_path, run_id).status == "DRAFT"

        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        assert (await client.post(url, json={"review_mapping": "có"})).status_code == 422
        confirmed = await client.post(url, json={})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "PLAN_CONFIRMED"
        assert get_run_report(config.store_path, run_id).status == "READY_FOR_REVIEW"
        again = await client.post(url, json={})
        assert again.status_code == 409 and "READY_FOR_REVIEW" in again.json()["detail"]
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


def test_gates_before_the_pause_survive_the_confirmation(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    agent = deps(FakeLLM(PLAN, SPEC))
    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)
    assert [g.name for g in planned.gates] == ["semantic"], "cổng semantic chạy trước khi dừng chờ xác nhận"
    done = confirm_plan(planned, "minh", agent, store)
    assert [g.name for g in done.gates][:2] == ["semantic", "spec_schema"] and done.gates[-1].name == "post_run"


def test_semantic_conflict_blocks_before_asking_for_confirmation(tmp_path):
    report = plan_agent("Số khách hàng mới theo tuần", "minh", deps(FakeLLM(PLAN)), str(tmp_path / "runs.sqlite3"))
    assert (report.status, report.stage_reached) == ("BLOCKED_SEMANTIC_CONFLICT", "resolve")
