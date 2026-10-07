"""Bản nháp: agent dừng sau Spec (DRAFT) → Engineer sửa mapping → kiểm tra lại → render + chạy thử."""

import copy
from types import SimpleNamespace

import pytest

from src.agents.service import SpecRejectedError, continue_agent, draft_agent
from src.pipeline import auth
from src.run_report import get_run_report
from src.run_report.store import get_run_files
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, tiered_llm


def test_draft_stops_after_spec_without_files(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    llm = FakeLLM(PLAN, SPEC)
    report = draft_agent("Doanh thu theo ngày", "minh", deps(llm), store)
    assert report.status == "DRAFT" and report.stage_reached == "static"
    assert report.spec["transform"]["model_name"] == SPEC["transform"]["model_name"]
    assert not report.files and len(llm.calls) == 2, "chưa render, chưa chạy thử"


def test_continue_unchanged_spec_runs_sandbox_on_same_run(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    draft = draft_agent("Doanh thu theo ngày", "minh", deps(FakeLLM(PLAN, SPEC)), store)
    done = continue_agent(draft, draft.spec, "Minh", deps(FakeLLM()), store)
    assert done.run_id == draft.run_id and done.status == "READY_FOR_REVIEW"
    assert done.llm_calls == draft.llm_calls, "chạy tiếp không gọi thêm LLM"
    reasons = [item.get("reason", "") for item in done.spec_history]
    assert any("giữ nguyên Spec" in r for r in reasons) and "P5 đổi lịch" in reasons[-1], "P5 ghi bản đổi lịch sau cùng"
    assert len(get_run_files(store, done.run_id, 1)) == 5
    assert get_run_report(store, done.run_id).status == "READY_FOR_REVIEW"
    # Mốc thời gian để đo công sức: máy chạy và người chỉnh mapping tách bạch.
    assert [e.event for e in draft.timeline] == ["submitted", "spec_ready"]
    assert [e.event for e in done.timeline] == ["submitted", "spec_ready", "mapping_submitted", "finished"]
    assert done.timeline[0].actor == "minh" and done.timeline[2].actor == "Minh"
    assert all(a.at <= b.at for a, b in zip(done.timeline, done.timeline[1:], strict=False))


def test_continue_with_edited_mapping_records_the_edit(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    draft = draft_agent("Doanh thu theo ngày", "minh", deps(FakeLLM(PLAN, SPEC)), store)
    edited = copy.deepcopy(draft.spec)
    edited["sources"][0]["staging"]["reject_if"] = [
        *edited["sources"][0]["staging"].get("reject_if", []),
        "order_id < 0",
    ]
    done = continue_agent(draft, edited, "Minh", deps(FakeLLM()), store)
    assert done.status == "READY_FOR_REVIEW"
    assert any("chỉnh mapping" in item.get("reason", "") for item in done.spec_history)
    assert "order_id < 0" in done.spec["sources"][0]["staging"]["reject_if"]


def test_continue_rejects_column_that_does_not_exist(tmp_path):
    store = str(tmp_path / "runs.sqlite3")
    draft = draft_agent("Doanh thu theo ngày", "minh", deps(FakeLLM(PLAN, SPEC)), store)
    edited = copy.deepcopy(draft.spec)
    edited["sources"][0]["staging"]["columns"][0]["from"] = "khong_co_cot_nay"
    with pytest.raises(SpecRejectedError) as error:
        continue_agent(draft, edited, "Minh", deps(FakeLLM()), store)
    assert any("khong_co_cot_nay" in e for e in error.value.errors)
    assert get_run_report(store, draft.run_id).status == "DRAFT", "nháp giữ nguyên để sửa tiếp"


@pytest.mark.asyncio
async def test_draft_api_flow(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps(tiered_llm)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        started = (await client.post("/api/v1/agent/runs", json={"request": "Doanh thu theo ngày"})).json()
        assert started["status"] == "GENERATING" and started["spec"] is None  # trả ngay, agent chạy nền
        early = await client.post(f"/api/v1/agent/runs/{started['run_id']}/continue", json={"spec": {}})
        assert early.status_code == 409, "chưa xác nhận kế hoạch thì chưa có Spec để chạy tiếp"
        await client.post(f"/api/v1/agent/runs/{started['run_id']}/confirm-plan", json={"review_mapping": True})
        draft = (await client.get(f"/api/v1/runs/{started['run_id']}")).json()
        assert (draft["status"], draft["stage_reached"]) == ("DRAFT", "static")
        bad = copy.deepcopy(draft["spec"])
        bad["sources"][0]["staging"]["columns"][0]["from"] = "khong_co"
        rejected = await client.post(f"/api/v1/agent/runs/{draft['run_id']}/continue", json={"spec": bad})
        assert rejected.status_code == 422 and "khong_co" in rejected.json()["detail"]
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        other = await client.post(f"/api/v1/agent/runs/{draft['run_id']}/continue", json={"spec": draft["spec"]})
        assert other.status_code == 403
        await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})
        done = await client.post(f"/api/v1/agent/runs/{draft['run_id']}/continue", json={"spec": draft["spec"]})
        assert done.status_code == 200 and done.json()["status"] == "VALIDATING"  # Spec đã qua cổng, chạy thử ở nền
        finished = (await client.get(f"/api/v1/runs/{draft['run_id']}")).json()
        assert finished["status"] == "READY_FOR_REVIEW"
        again = await client.post(f"/api/v1/agent/runs/{draft['run_id']}/continue", json={"spec": draft["spec"]})
        assert again.status_code == 409
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)
