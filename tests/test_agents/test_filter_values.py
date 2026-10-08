"""Cổng `filter_values`: bộ lọc dùng giá trị không có trong dòng mẫu (LLM đặt `status = 'confirmed'` làm gold rỗng)."""

import copy

from src.agents.graph import build_graph
from src.agents.validate import validate_spec
from src.policy.loader import load_policy
from tests.test_agents.test_graph import PLAN, SNAPSHOT, SPEC, FakeLLM, deps

STATUSES = ["paid", "paid", "pending", "cancelled", "paid", "pending"]
SAMPLED = copy.deepcopy(SNAPSHOT)
SAMPLED["orders"]["sample_rows"] = [
    {"order_id": str(i), "amount_text": "1000", "ordered_at_text": "2026-01-01", "status": status}
    for i, status in enumerate(STATUSES, 1)
]


def _with_filters(*filters: str) -> dict:
    spec = copy.deepcopy(SPEC)
    spec["transform"]["filters"] = list(filters)
    return spec


def _gate(spec: dict, snapshot: dict = SAMPLED):
    result = validate_spec(spec, snapshot, load_policy(), "Doanh thu theo ngày")
    return next((g for g in result.gates if g.name == "filter_values"), None), result.errors


def test_guessed_value_is_blocked_with_the_values_actually_seen():
    gate, errors = _gate(_with_filters("status = 'confirmed'"))
    assert gate.result == "fail"
    assert "'confirmed'" in errors[0] and "['cancelled', 'paid', 'pending']" in errors[0]


def test_values_in_the_sample_pass_and_inequality_is_not_checked():
    for filters in (["status = 'paid'"], ["orders.status IN ('paid', 'pending')"], ["status <> 'refunded'"]):
        gate, errors = _gate(_with_filters(*filters))
        assert gate.result == "pass" and not errors, filters


def test_in_list_reports_only_the_missing_value_and_case_mistakes():
    _, errors = _gate(_with_filters("status IN ('paid', 'shipped')"))
    assert len(errors) == 1 and "'shipped'" in errors[0]
    _, errors = _gate(_with_filters("status = 'PAID'"))
    assert "sai hoa / thường" in errors[0]
    gate, _ = _gate(_with_filters("lower(status) = 'paid'"))
    assert gate.result == "pass", "lower() so không phân biệt hoa thường"


def test_columns_that_do_not_look_categorical_are_not_checked():
    unique = copy.deepcopy(SAMPLED)
    for i, row in enumerate(unique["orders"]["sample_rows"]):
        row["status"] = f"s{i}"  # mỗi dòng một giá trị: 20 dòng mẫu không thể chứa hết
    gate, _ = _gate(_with_filters("status = 'confirmed'"), unique)
    assert gate.result == "pass"
    gate, _ = _gate(_with_filters("status = 'confirmed'"), SNAPSHOT)  # không có dòng mẫu cho cột
    assert gate.result == "pass"


def test_agent_is_told_and_fixes_the_filter():
    llm = FakeLLM(PLAN, _with_filters("status = 'confirmed'"), _with_filters("status = 'paid'"))
    agent = deps(llm)
    agent.snapshot = lambda plan, policy, settings: SAMPLED

    state = build_graph(agent).invoke(
        {"run_id": "r1", "version": 1, "user_request": "Doanh thu theo ngày", "llm_calls": 0, "error_trace": []}
    )

    assert state["status"] == "READY_FOR_REVIEW" and state["attempts"]["spec"] == 1
    assert "confirmed" in str(llm.calls[2]["user"]["lỗi_lần_trước"])
