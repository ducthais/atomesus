"""Cổng semantic (P3, FR-05): `customer` có ba nghĩa ở sales, marketing và rental (Pagila) trong policy/domains.yml."""

from src.agents.semantic import entity_scan
from src.policy.loader import load_policy
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, run

SALES_ONLY = [{"source_ref": "sales_pg", "tables": ["customers", "orders"]}]
BOTH = [*SALES_ONLY, {"source_ref": "mkt_api", "tables": ["leads"]}]


def test_ambiguous_entity_is_blocked_with_both_definitions():
    gate, message = entity_scan("Đếm số khách hàng mới mỗi tuần", None, load_policy())
    assert gate.result == "fail" and message == gate.detail
    assert "Phát hiện thực thể `customer` ở 3 domain" in message
    assert "Sales định nghĩa là tài khoản đã ký hợp đồng (2 bảng Postgres)" in message
    assert "Marketing định nghĩa là người để lại email (2 endpoint REST)" in message
    assert "an@company.vn" in message and "binh@company.vn" in message
    assert set(gate.observed["customer"]["domains"]) == {"sales", "marketing", "rental"}


def test_english_entity_name_is_matched_too():
    assert entity_scan("count new customer per week", None, load_policy())[1]


def test_sources_of_one_domain_settle_the_meaning():
    gate, message = entity_scan("Tổng tiền đơn hàng của từng khách hàng", SALES_ONLY, load_policy())
    assert message is None and gate.result == "pass"
    assert gate.observed["resolved"] == {"customer": "sales"} and "tài khoản đã ký hợp đồng" in gate.detail


def test_sources_across_domains_stay_ambiguous():
    assert entity_scan("Khách hàng mua gì", BOTH, load_policy())[1]


def test_conformed_dimension_is_not_a_conflict():
    policy = load_policy()
    conformed = policy.domains.model_copy(update={"conformed_dimensions": ["customer"]})
    gate, message = entity_scan("Đếm khách hàng", None, policy.model_copy(update={"domains": conformed}))
    assert message is None and gate.result == "pass"


def test_request_without_shared_entity_passes():
    gate, message = entity_scan("Doanh thu theo ngày", None, load_policy())
    assert message is None and gate.detail == "Không thực thể nào mơ hồ"


def test_graph_stops_before_writing_a_spec():
    llm = FakeLLM(PLAN, SPEC)
    from src.agents.graph import build_graph
    from tests.test_agents.test_graph import deps

    state = build_graph(deps(llm)).invoke(
        {"run_id": "r1", "version": 1, "user_request": "Số khách hàng mới theo tuần", "llm_calls": 0, "error_trace": []}
    )
    assert (state["status"], state["stage_reached"]) == ("BLOCKED_SEMANTIC_CONFLICT", "resolve")
    assert len(llm.calls) == 1 and state.get("spec") is None, "chỉ planner đã gọi LLM, chưa tốn lượt sinh Spec"
    assert state["gates"][-1]["name"] == "semantic"


def test_passing_gate_is_kept_in_the_final_report():
    state = run(FakeLLM(PLAN, SPEC), scope=[{"source_ref": "sales_pg", "tables": ["orders"]}])
    assert state["status"] == "READY_FOR_REVIEW" and state["gates"][0]["name"] == "semantic"
