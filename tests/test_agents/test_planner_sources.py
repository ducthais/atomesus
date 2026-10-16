"""Planner thật (và Chat Planner) với chính sách cho luồng tạo mới (`for_new_runs`): chặn sales, lập kế hoạch với review_api.

Catalog giả lọc theo `policy.domains` như catalog thật (chỉ trả domain policy có), nên domain ẩn sales không lọt vào
danh mục LLM thấy. review_api (rest_api, domain rental) ghép với pagila_pg.rental theo rental_id (báo cáo Sync Gate,
mục 4).
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.agents.service import plan_agent
from src.api.routes import for_new_runs
from src.run_report import get_run_report
from tests.test_agents.test_graph import CATALOG, PLAN, FakeLLM, deps

RENTAL = {
    "sources": {
        "pagila_pg": {
            "kind": "postgres",
            "available": True,
            "objects": {"rental": ["rental_id", "customer_id", "rental_date", "last_update"]},
        },
        "review_api": {
            "kind": "rest_api",
            "available": True,
            "objects": {"feedback": ["feedback_id", "rental_id", "customer_id", "rating", "submitted_at"]},
        },
    },
    "entities": {},
}
FULL = {**CATALOG, "rental": RENTAL}
REVIEW_PLAN = {
    "domain": "rental",
    "summary": "Số phản hồi theo khách hàng",
    "model_name": "fct_review_by_customer",
    "sources": [
        {"id": "rentals", "source_ref": "pagila_pg", "object": "rental", "purpose": "lượt thuê"},
        {"id": "feedback", "source_ref": "review_api", "object": "feedback", "purpose": "phản hồi"},
    ],
    "joins": [{"left": "rentals.rental_id", "right": "feedback.rental_id", "type": "left"}],
    "grain": ["customer_id"],
    "metrics": [{"name": "review_count", "definition": "đếm feedback_id"}],
    "schedule": "0 3 * * *",
    "question": None,
}
REVIEW_SCOPE = [{"source_ref": "review_api", "tables": ["feedback"]}, {"source_ref": "pagila_pg", "tables": ["rental"]}]


def scoped_catalog(policy, settings):
    return {name: domain for name, domain in FULL.items() if name in policy.domains.domains}


def existing_deps(llm):
    """Deps như run có sẵn (đủ domain), catalog lọc theo policy."""
    return replace(deps(llm), catalog=scoped_catalog)


def _seen_catalog(llm: FakeLLM) -> dict:
    return llm.calls[0]["user"]["danh_mục"]


def test_planner_plans_review_api_with_pagila_for_a_new_run(tmp_path):
    llm = FakeLLM(REVIEW_PLAN)

    report = plan_agent(
        "Số phản hồi theo khách hàng", "minh", for_new_runs(existing_deps(llm)), str(tmp_path / "r.db"), REVIEW_SCOPE
    )

    assert (report.status, report.stage_reached) == ("DRAFT", "confirm"), report.error_trace
    assert report.plan["domain"] == "rental"
    assert {s["source_ref"] for s in report.plan["sources"]} == {"review_api", "pagila_pg"}
    assert set(_seen_catalog(llm)) == {"rental"}, "LLM chỉ thấy nguồn đã chọn, không thấy domain ẩn sales"


def test_planner_refuses_sales_selected_for_a_new_run_without_calling_the_llm(tmp_path):
    llm = FakeLLM(PLAN)
    scope = [{"source_ref": "sales_pg", "tables": ["orders"]}]

    report = plan_agent("Doanh thu theo ngày", "minh", for_new_runs(existing_deps(llm)), str(tmp_path / "r.db"), scope)

    assert (report.status, report.stage_reached) == ("NEEDS_HUMAN", "plan")
    assert "sales_pg" in report.error_trace[-1]["error"] and "không có" in report.error_trace[-1]["error"]
    assert llm.calls == [], "nguồn ẩn bị chặn trước khi tốn lượt LLM"


def test_planner_rejects_a_sales_plan_when_no_source_was_selected(tmp_path):
    llm = FakeLLM(*[PLAN] * 5)

    report = plan_agent("Doanh thu theo ngày", "minh", for_new_runs(existing_deps(llm)), str(tmp_path / "r.db"))

    assert report.status == "NEEDS_HUMAN", report.error_trace
    assert not report.spec, "không sinh Spec cho domain ẩn"
    assert "sales" not in _seen_catalog(llm), "danh mục cho LLM không có domain ẩn"


def test_existing_runs_still_see_sales(tmp_path):
    # Thao tác trên run có sẵn (sửa, chạy tiếp) giữ đủ domain: chỉ luồng tạo mới bị thu hẹp.
    report = plan_agent("Doanh thu theo ngày", "minh", existing_deps(FakeLLM(PLAN)), str(tmp_path / "r.db"))

    assert report.status == "DRAFT" and report.plan["domain"] == "sales"


@pytest.fixture
def engineer(client, tmp_path, monkeypatch):
    from src.api import routes
    from src.main import app
    from src.pipeline import auth
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    llm = FakeLLM(*[REVIEW_PLAN] * 3)
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: existing_deps(llm)  # route tự thu hẹp cho run mới
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    yield SimpleNamespace(client=client, config=config, llm=llm)
    app.dependency_overrides.pop(auth.settings, None)
    app.dependency_overrides.pop(routes.agent_deps, None)


@pytest.mark.asyncio
async def test_chat_planner_creates_a_review_api_draft_and_blocks_sales(engineer):
    client = engineer.client
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})

    response = await client.post(
        "/api/v1/chat", json={"message": "Số phản hồi theo khách hàng", "sources": REVIEW_SCOPE}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "new_request"
    report = get_run_report(engineer.config.store_path, body["run_id"])
    assert report is not None and report.status == "DRAFT" and report.plan["domain"] == "rental"
    assert set(_seen_catalog(engineer.llm)) == {"rental"}, "Chat Planner cũng không thấy domain ẩn"
    calls = len(engineer.llm.calls)

    blocked = await client.post(
        "/api/v1/chat", json={"message": "Doanh thu theo ngày", "sources": [{"source_ref": "sales_pg", "tables": []}]}
    )

    assert blocked.status_code == 422 and "sales_pg" in blocked.text
    assert len(engineer.llm.calls) == calls, "chặn trước planner, không gọi LLM"
