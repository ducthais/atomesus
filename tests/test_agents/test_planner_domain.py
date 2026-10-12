"""Domain suy tất định từ nguồn; lỗi gửi lại LLM nêu domain hợp lệ và nguồn sở hữu."""

import pytest

from src.agents.nodes.planner import planner
from src.agents.service import plan_agent
from tests.test_agents.test_graph import CATALOG, PLAN, FakeLLM, deps

CATALOG_WITH_RENTAL = {
    **CATALOG,
    "rental": {
        "sources": {
            "pagila_pg": {
                "kind": "postgres",
                "available": True,
                "objects": {
                    "payment": ["payment_id", "rental_id", "amount"],
                    "rental": ["rental_id", "rental_date"],
                },
                "relations": ["payment.rental_id -> rental.rental_id"],
            },
            "rental_csv": {
                "kind": "filesystem",
                "available": True,
                "objects": {"targets": ["amount"]},
            },
        },
        "entities": {},
    },
}
RENTAL_PLAN = {
    **PLAN,
    "domain": "rental",
    "sources": [{"id": "payment", "source_ref": "pagila_pg", "object": "payment"}],
    "grain": ["payment.rental_id"],
    "metrics": [{"name": "revenue", "definition": "tổng payment.amount"}],
    "concepts": [{"term": "doanh thu", "column": "payment.amount"}],
}
RENTAL_SCOPE = [{"source_ref": "pagila_pg"}]
MIXED_SCOPE = [*RENTAL_SCOPE, {"source_ref": "sales_pg"}, {"source_ref": "rental_csv"}]
MIXED_PLAN = {**RENTAL_PLAN, "sources": [*RENTAL_PLAN["sources"], *PLAN["sources"]]}


def rental_deps(llm):
    agent = deps(llm)
    agent.catalog = lambda policy, settings: CATALOG_WITH_RENTAL
    return agent


def planned(llm, scope=None):
    return planner(rental_deps(llm))(
        {"run_id": "pagila", "user_request": "Doanh thu theo lượt thuê", "source_scope": scope}
    )


@pytest.mark.parametrize("domain", ["pagila_pg", "pagila", "sales", "unknown", "rental"])
@pytest.mark.parametrize("scope", [None, [], RENTAL_SCOPE, MIXED_SCOPE])
def test_single_domain_sources_override_llm_domain_without_retry(domain, scope):
    llm = FakeLLM({**RENTAL_PLAN, "domain": domain})
    state = planned(llm, scope)

    assert (state["status"], state["stage_reached"], state["llm_calls"]) == ("GENERATING", "plan", 1)
    assert state["domain"] == state["plan"]["domain"] == "rental"
    assert state["plan"]["concepts"] == RENTAL_PLAN["concepts"]
    assert len(llm.calls) == 1 and not state.get("error_trace")


@pytest.mark.parametrize("scope", [None, [*RENTAL_SCOPE, {"source_ref": "rental_csv"}]])
def test_multiple_sources_in_one_domain_are_inferred_together(scope):
    llm = FakeLLM(
        {
            **RENTAL_PLAN,
            "domain": "pagila_pg",
            "sources": [
                *RENTAL_PLAN["sources"],
                {"id": "targets", "source_ref": "rental_csv", "object": "targets"},
            ],
        }
    )
    state = planned(llm, scope)

    assert state["status"] == "GENERATING" and state["llm_calls"] == 1
    assert state["domain"] == state["plan"]["domain"] == "rental"
    assert {source["source_ref"] for source in state["plan"]["sources"]} == {"pagila_pg", "rental_csv"}


def test_pagila_source_name_as_domain_reaches_confirmation(tmp_path):
    llm = FakeLLM({**RENTAL_PLAN, "domain": "pagila_pg"})
    report = plan_agent(
        "Doanh thu theo lượt thuê", "minh", rental_deps(llm), str(tmp_path / "runs.sqlite3"), sources=RENTAL_SCOPE
    )

    assert (report.status, report.stage_reached, report.llm_calls) == ("DRAFT", "confirm", 1)
    assert report.plan["domain"] == report.domain == "rental"
    assert not report.error_trace and len(llm.calls) == 1
    assert "`domain` là tên domain trong danh mục (ví dụ `rental`)" in llm.calls[0]["system"]
    assert "không phải tên nguồn (`pagila_pg`) hay tên dataset (`pagila`)" in llm.calls[0]["system"]


def test_domain_is_inferred_before_completing_join_sources():
    llm = FakeLLM(
        {
            **RENTAL_PLAN,
            "domain": "pagila",
            "joins": [{"left": "payment.rental_id", "right": "rental.rental_id", "type": "inner"}],
        }
    )
    state = planned(llm, RENTAL_SCOPE)

    assert state["status"] == "GENERATING" and state["llm_calls"] == 1
    assert state["domain"] == "rental"
    assert [source["object"] for source in state["plan"]["sources"]] == ["payment", "rental"]


@pytest.mark.parametrize("scope", [None, MIXED_SCOPE])
def test_unknown_domain_with_mixed_sources_lists_domains_and_owners(scope):
    bad = {**MIXED_PLAN, "domain": "unknown"}
    llm = FakeLLM(bad, bad)
    state = planned(llm, scope)

    assert (state["status"], state["stage_reached"], state["llm_calls"]) == ("NEEDS_HUMAN", "plan", 2)
    errors = llm.calls[1]["user"]["lỗi_lần_trước"]
    assert len(errors) == 1
    assert "domain 'unknown' không có trong danh mục" in errors[0]
    assert "các domain hợp lệ trong danh mục: rental, sales" in errors[0]
    assert "'pagila_pg' → rental" in errors[0] and "'sales_pg' → sales" in errors[0]
    if scope:
        assert "'rental_csv' → rental" in errors[0]
    assert llm.calls[1]["user"]["kế_hoạch_lần_trước"]["domain"] == "unknown"
    assert errors[0] in state["error_trace"][-1]["error"]


def test_llm_can_use_domain_feedback_to_retry_with_one_domain():
    llm = FakeLLM({**MIXED_PLAN, "domain": "unknown"}, RENTAL_PLAN)
    state = planned(llm, MIXED_SCOPE)

    assert state["status"] == "GENERATING" and state["llm_calls"] == 2
    assert state["domain"] == "rental" and state["plan"]["sources"] == [{**RENTAL_PLAN["sources"][0], "purpose": ""}]
    assert "các domain hợp lệ" in llm.calls[1]["user"]["lỗi_lần_trước"][0]


@pytest.mark.parametrize("domain", ["rental", "sales"])
def test_sources_from_multiple_domains_are_still_rejected(domain):
    bad = {**MIXED_PLAN, "domain": domain}
    llm = FakeLLM(bad, bad)
    state = planned(llm, MIXED_SCOPE)

    assert state["status"] == "NEEDS_HUMAN" and state["llm_calls"] == 2
    errors = llm.calls[1]["user"]["lỗi_lần_trước"]
    assert any(f"không thuộc domain '{domain}'" in error for error in errors)
    assert all("các domain hợp lệ trong danh mục: rental, sales" in error for error in errors)
    assert llm.calls[1]["user"]["kế_hoạch_lần_trước"]["domain"] == domain


def test_selected_domain_does_not_allow_sources_outside_scope():
    bad = {**PLAN, "domain": "pagila_pg"}
    llm = FakeLLM(bad, bad)
    state = planned(llm, RENTAL_SCOPE)

    assert state["status"] == "NEEDS_HUMAN" and state["llm_calls"] == 2
    assert llm.calls[1]["user"]["kế_hoạch_lần_trước"]["domain"] == "rental"
    assert "nguồn 'sales_pg' không thuộc domain 'rental'" in state["error_trace"][-1]["error"]


def test_unknown_source_prevents_inferring_domain_from_only_known_sources():
    bad = {
        **RENTAL_PLAN,
        "domain": "unknown",
        "sources": [*RENTAL_PLAN["sources"], {"id": "missing", "source_ref": "missing", "object": "payment"}],
    }
    llm = FakeLLM(bad, bad)
    state = planned(llm)

    assert state["status"] == "NEEDS_HUMAN" and state["llm_calls"] == 2
    assert llm.calls[1]["user"]["kế_hoạch_lần_trước"]["domain"] == "unknown"
    assert "'missing' → không có trong danh mục" in llm.calls[1]["user"]["lỗi_lần_trước"][0]
