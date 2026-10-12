"""Khái niệm thiếu cột đi luồng hỏi người dùng; cột có thật được kiểm tra và gợi ý lại cho LLM."""

import json

import pytest
from pydantic import ValidationError

from src.agents.nodes.planner import Plan, planner
from src.agents.service import plan_agent
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, run

NULL_VALUES = [None, "null", "None", "N/A", "", "  NuLl  ", "\tNONE\n", " n/a ", " \t\n"]
PAGILA_CATALOG = {
    "sales": {
        "sources": {
            "sales_pg": {
                "kind": "postgres",
                "available": True,
                "objects": {
                    "payment": ["payment_id", "rental_id", "amount", "payment_date"],
                    "rental": ["rental_id", "inventory_id", "rental_date"],
                    "inventory": ["inventory_id", "film_id", "store_id"],
                    "store": ["store_id", "manager_staff_id"],
                },
                "relations": [
                    "payment.rental_id -> rental.rental_id",
                    "rental.inventory_id -> inventory.inventory_id",
                ],
            }
        },
        "entities": {},
    }
}
PAGILA_PLAN = {
    **PLAN,
    "summary": "Doanh thu và số lượt thuê theo tháng và cửa hàng sở hữu bản DVD được thuê",
    "model_name": "fct_monthly_store_rentals",
    "sources": [{"id": t, "source_ref": "sales_pg", "object": t} for t in ("payment", "rental", "inventory")],
    "joins": [
        {"left": "payment.rental_id", "right": "rental.rental_id", "type": "inner"},
        {"left": "rental.inventory_id", "right": "inventory.inventory_id", "type": "inner"},
    ],
    "grain": ["rental.rental_date theo tháng", "inventory.store_id"],
    "metrics": [
        {"name": "revenue", "definition": "tổng payment.amount"},
        {"name": "rental_count", "definition": "đếm rental.rental_id"},
    ],
    "concepts": [
        {"term": "doanh thu", "column": "payment.amount"},
        {"term": "số lượt thuê", "column": "rental.rental_id"},
        {"term": "tháng", "column": "rental.rental_date"},
        {"term": "cửa hàng", "column": "inventory.store_id"},
    ],
}


@pytest.mark.parametrize("value", NULL_VALUES)
def test_null_column_strings_become_unmapped_concepts(value):
    llm = FakeLLM({**PLAN, "concepts": [{"term": "doanh thu", "column": value}], "question": value})
    state = run(llm)

    assert (state["status"], state["stage_reached"], state["llm_calls"]) == ("NEEDS_HUMAN", "plan", 1)
    assert state["plan"]["concepts"] == [{"term": "doanh thu", "column": None}]
    trace = state["error_trace"][-1]
    assert trace["error"] == "Cần làm rõ yêu cầu"
    assert "không có cột nào thể hiện: doanh thu" in trace["question"]
    assert "không phải <id>.<cột>" not in trace["error"]
    assert len(llm.calls) == 1


@pytest.mark.parametrize("value", NULL_VALUES)
def test_null_question_strings_do_not_stop_a_mapped_plan(value):
    llm = FakeLLM(
        {**PLAN, "concepts": [{"term": "doanh thu", "column": "orders.amount_text"}], "question": value}, SPEC
    )
    state = run(llm)
    assert state["status"] == "READY_FOR_REVIEW" and state["llm_calls"] == 2
    assert state["plan"]["question"] is None


def test_real_question_is_preserved_for_an_unmapped_concept():
    question = "Bạn muốn dùng cột nào để đo doanh thu?"
    state = run(FakeLLM({**PLAN, "concepts": [{"term": "doanh thu", "column": "null"}], "question": question}))
    assert state["plan"]["question"] == state["error_trace"][-1]["question"] == question
    assert state["plan"]["concepts"][0]["column"] is None


@pytest.mark.parametrize("field", ["column", "question"])
@pytest.mark.parametrize("value", [False, 123, [], {}])
def test_optional_text_still_rejects_non_strings(field, value):
    raw = {**PLAN, "concepts": [{"term": "doanh thu", "column": "orders.amount_text"}]}
    if field == "column":
        raw["concepts"][0]["column"] = value
    else:
        raw["question"] = value
    with pytest.raises(ValidationError):
        Plan.model_validate(raw)


def test_pagila_store_concept_reaches_confirmation(tmp_path):
    llm = FakeLLM(PAGILA_PLAN)
    agent = deps(llm)
    agent.catalog = lambda policy, settings: PAGILA_CATALOG
    scope = [{"source_ref": "sales_pg", "tables": ["payment", "rental", "inventory"]}]
    report = plan_agent(PAGILA_PLAN["summary"], "minh", agent, str(tmp_path / "runs.sqlite3"), sources=scope)

    assert (report.status, report.stage_reached, report.llm_calls) == ("DRAFT", "confirm", 1)
    assert report.plan["concepts"][-1] == {"term": "cửa hàng", "column": "inventory.store_id"}
    assert report.plan["question"] is None and not report.error_trace
    assert report.spec is None
    system = llm.calls[0]["system"]
    example = json.loads(system.split("Định dạng trả về:", 1)[1])
    assert example["concepts"][0]["column"] is None and example["question"] is None
    assert "Không được trả chuỗi" in system
    assert '"cửa hàng sở hữu bản DVD được thuê" → `inventory.store_id`' in system


@pytest.mark.parametrize("column", ["inventory.missing", "store.store_id", "inventory"])
def test_concept_retry_lists_valid_columns_of_selected_sources(column):
    bad = {**PAGILA_PLAN, "concepts": [{"term": "cửa hàng", "column": column}]}
    llm = FakeLLM(bad, PAGILA_PLAN)
    agent = deps(llm)
    agent.catalog = lambda policy, settings: PAGILA_CATALOG
    state = planner(agent)(
        {
            "run_id": "pagila",
            "user_request": PAGILA_PLAN["summary"],
            "source_scope": [{"source_ref": "sales_pg", "tables": ["payment", "rental", "inventory"]}],
        }
    )

    assert state["status"] == "GENERATING" and state["llm_calls"] == 2
    errors = llm.calls[1]["user"]["lỗi_lần_trước"]
    assert len(errors) == 1 and "concepts 'cửa hàng'" in errors[0]
    assert 'JSON null (không phải chuỗi "null")' in errors[0]
    hint = errors[0].split("các cột hợp lệ của nguồn đã chọn: ", 1)[1]
    expected = {
        f"{s['id']}.{c}"
        for s in PAGILA_PLAN["sources"]
        for c in PAGILA_CATALOG["sales"]["sources"]["sales_pg"]["objects"][s["object"]]
    }
    assert set(hint.split(", ")) == expected
    assert "store.manager_staff_id" not in hint
    assert state["plan"]["concepts"][-1]["column"] == "inventory.store_id"
