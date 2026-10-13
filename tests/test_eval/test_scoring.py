"""So khớp bảng gold với golden trong eval/run_eval.py: ghép cột theo giá trị vì tên cột do LLM đặt."""

import importlib.util
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)


def _case(case_id: str) -> dict:
    return json.loads((ROOT / "eval" / "golden" / f"{case_id}.json").read_text(encoding="utf-8"))


def _gold_from(case: dict, key: str, rename: dict[str, str], transform=lambda m, v: v) -> list[dict]:
    """Dựng bảng gold "như LLM viết" từ golden: tên cột khác, kiểu khác (Decimal, date)."""
    return [
        {key: row["key"][key], **{rename[m]: transform(m, v) for m, v in row["values"].items()}} for row in case["rows"]
    ]


def test_columns_are_matched_by_value_not_name():
    case = _case("valid_postgres_rest_api_by_customer")
    gold = _gold_from(
        case,
        "customer_name",
        {"order_count": "number_of_orders", "order_total": "total_order_amount", "paid": "total_payment_amount"},
        lambda m, v: Decimal(str(v)),
    )
    result = run_eval.match_case(case, gold)
    assert result["matched"] and result["key"] == {"golden": "customer_name", "gold": "customer_name"}
    assert result["measures"] == {
        "order_count": "number_of_orders",
        "order_total": "total_order_amount",
        "paid": "total_payment_amount",
    }


def test_dates_and_int_like_keys_are_normalized():
    case = _case("valid_postgres_daily_revenue")
    gold = [
        {"order_date": date.fromisoformat(r["key"]["day"]), "total_revenue": Decimal(r["values"]["revenue"])}
        for r in case["rows"]
    ]
    assert run_eval.match_case(case, gold)["matched"]
    assert run_eval.key_text(1.0) == "1" and run_eval.key_text(Decimal("12")) == "12"


def test_wrong_totals_are_reported():
    # Lỗi đã gặp ở run b8d45355: tổng tiền đơn bị nhân theo số khoản thanh toán.
    case = _case("valid_postgres_rest_api_by_customer")
    gold = _gold_from(case, "customer_id", {"order_count": "n", "order_total": "total", "paid": "paid"},
                      lambda m, v: Decimal(str(v)) * (4 if m == "order_total" else 1))  # fmt: skip
    result = run_eval.match_case(case, gold)
    assert not result["matched"] and result["measures"]["order_total"] is None
    assert any("order_total" in p for p in result["problems"])


def test_missing_rows_and_null_for_zero():
    case = _case("valid_postgres_filesystem_by_customer")
    gold = _gold_from(case, "customer_id", {"order_total": "orders", "refunded": "refunds"},
                      lambda m, v: None if m == "refunded" and v == "0.00" else Decimal(v))  # fmt: skip
    assert run_eval.match_case(case, gold)["matched"], "LEFT JOIN không có khoản hoàn: NULL chấp nhận như 0"
    assert not run_eval.match_case(case, gold[:-1])["matched"]


def test_summary_states_denominators_by_source_kind():
    results = [
        {"source_kinds": ["postgres"], "matched": True, "spec_attempts": 0, "llm_usd": 0.001},
        {"source_kinds": ["rest_api"], "matched": False, "spec_attempts": 1, "llm_usd": 0.002},
        {"source_kinds": ["postgres", "rest_api"], "matched": True, "spec_attempts": 1, "llm_usd": 0.003},
    ]
    assert run_eval.summarize(results) == {
        "matched": "2/3",
        "first_try": "1/3",
        "by_kind": {"postgres": "1/1", "rest_api": "0/1", "đa nguồn": "1/1"},
        "llm_usd": 0.006,
    }


def test_fifteen_cases_follow_the_spec_split():
    cases = run_eval.load_cases()
    groups = [c["group"] for c in cases]
    gates = sorted(c["gate"] for c in cases if c["group"] == "violation")
    counts = tuple(groups.count(g) for g in ("valid", "violation", "ambiguous", "optimize"))
    assert counts == (6, 6, 3, 4), "15 ca của SPEC §18.2 cộng 4 ca optimizer"
    assert gates == ["ast_guard", "ast_guard", "budget", "budget", "semantic", "semantic"]
    assert all(c["expected"]["status"].startswith("BLOCKED_") for c in cases if c["group"] == "violation")
    assert len({c["id"] for c in cases}) == len(cases) == 19


def test_violation_passes_only_with_the_expected_block_code():
    case = {"group": "violation", "expected": {"status": "BLOCKED_BUDGET", "stage": "budget"}}
    assert run_eval.score_outcome(case, "BLOCKED_BUDGET", "budget", {})["matched"]
    missed = run_eval.score_outcome(case, "READY_FOR_REVIEW", "t_validate", {})
    assert not missed["matched"] and "mong đợi BLOCKED_BUDGET" in missed["problems"][0]
    early = run_eval.score_outcome(case, "BLOCKED_BUDGET", "plan", {})
    assert not early["matched"] and "dừng ở plan" in early["problems"][0]


def test_ambiguous_case_needs_a_clarifying_question():
    case = {"group": "ambiguous", "expected": {"status": "NEEDS_HUMAN", "stage": "plan"}}
    assert run_eval.score_outcome(case, "NEEDS_HUMAN", "plan", {"question": "Chỉ số nào?"})["matched"]
    silent = run_eval.score_outcome(case, "NEEDS_HUMAN", "plan", {"error": "Cần làm rõ"})
    assert not silent["matched"] and "câu hỏi" in silent["problems"][0]


def test_case_policy_lowers_only_the_named_ceiling():
    from src.policy.loader import load_policy

    policy = load_policy()
    lowered = run_eval.with_policy(policy, {"per_run": {"max_bytes_scanned": 1000}})
    assert lowered.budgets.per_run.max_bytes_scanned == 1000
    assert lowered.budgets.per_run.max_llm_calls == policy.budgets.per_run.max_llm_calls
    assert lowered.budgets.per_day == policy.budgets.per_day
    assert run_eval.with_policy(policy, None) == policy


def _step(rule, applied, before=None, after=None, reason="lý do"):
    return {"rule": rule, "applied": applied, "before_sec": before, "after_sec": after, "reason": reason}


SMALL = {
    "group": "optimize",
    "expected": {"uc3_status_in": ["NO_IMPROVEMENT"], "uc3_skipped": {"table_to_incremental": "dưới ngưỡng"}},
}
TRIED = {
    "group": "optimize",
    "expected": {"uc3_status_in": ["READY_FOR_REVIEW", "NO_IMPROVEMENT"], "uc3_measured": ["table_to_incremental"]},
}
READY = {"status": "READY_FOR_REVIEW", "llm_calls": 2, "optimizer": []}


def test_optimizer_case_passes_when_behaviour_matches_the_measurements():
    below = _step("table_to_incremental", False, reason="30 dòng, dưới ngưỡng")
    skipped = {"status": "NO_IMPROVEMENT", "llm_calls": 0, "optimizer": [below]}
    assert run_eval.score_optimize(SMALL, READY, skipped, 10)["matched"]
    kept = {"status": "READY_FOR_REVIEW", "llm_calls": 0, "optimizer": [_step("table_to_incremental", True, 1.0, 0.5)]}
    assert run_eval.score_optimize(TRIED, READY, kept, 10)["matched"]
    dropped = {"status": "NO_IMPROVEMENT", "llm_calls": 0, "optimizer": [_step("table_to_incremental", False, 1, 0.98)]}
    assert run_eval.score_optimize(TRIED, READY, dropped, 10)["matched"], "bị loại vì không nhanh hơn cũng là đúng"


def test_optimizer_case_fails_on_llm_calls_or_a_kept_candidate_that_is_not_faster():
    called = {"status": "NO_IMPROVEMENT", "llm_calls": 1, "optimizer": [_step("table_to_incremental", False, 1, 1)]}
    assert "SPEC FR-14" in " ".join(run_eval.score_optimize(TRIED, READY, called, 10)["problems"])
    slow = {"status": "READY_FOR_REVIEW", "llm_calls": 0, "optimizer": [_step("table_to_incremental", True, 1.0, 0.95)]}
    assert "không nhanh hơn đủ" in " ".join(run_eval.score_optimize(TRIED, READY, slow, 10)["problems"])
    unmeasured = {"status": "NO_IMPROVEMENT", "llm_calls": 0, "optimizer": [_step("table_to_incremental", False)]}
    assert "không đo luật" in " ".join(run_eval.score_optimize(TRIED, READY, unmeasured, 10)["problems"])
    blocked = {"status": "NEEDS_HUMAN", "llm_calls": 3, "optimizer": []}
    assert not run_eval.score_optimize(TRIED, blocked, unmeasured, 10)["matched"]


def test_optimizer_case_can_require_the_candidate_to_be_kept():
    must_keep = {
        "group": "optimize",
        "expected": {"uc3_status_in": ["READY_FOR_REVIEW"], "uc3_applied": ["table_to_incremental"]},
    }
    kept = {"status": "READY_FOR_REVIEW", "llm_calls": 0, "optimizer": [_step("table_to_incremental", True, 1.0, 0.6)]}
    assert run_eval.score_optimize(must_keep, READY, kept, 10)["matched"]
    dropped = {"status": "NO_IMPROVEMENT", "llm_calls": 0, "optimizer": [_step("table_to_incremental", False, 1, 0.98)]}
    problems = run_eval.score_optimize(must_keep, READY, dropped, 10)["problems"]
    assert any("không giữ table_to_incremental" in p for p in problems)


def test_large_case_requires_uc3_to_keep_incremental_not_just_a_partition_filter_win():
    """Ca 3 triệu dòng: P13 giữ missing_partition_filter không thay được việc UC-3 giữ table_to_incremental."""
    case = json.loads((ROOT / "eval" / "cases" / "optimize_large_events_by_day.json").read_text(encoding="utf-8"))
    p13_kept = {**READY, "optimizer": [_step("missing_partition_filter", True, 0.222, 0.155)]}
    uc3_kept = {"status": "READY_FOR_REVIEW", "llm_calls": 0, "optimizer": [_step("table_to_incremental", True, 0.2235, 0.1382)]}  # fmt: skip
    uc3_dropped = {"status": "NO_IMPROVEMENT", "llm_calls": 0, "optimizer": [_step("table_to_incremental", False, 0.2235, 0.1382)]}  # fmt: skip

    assert run_eval.score_optimize(case, p13_kept, uc3_kept, 10)["matched"]
    problems = run_eval.score_optimize(case, p13_kept, uc3_dropped, 10)["problems"]
    assert any("không giữ table_to_incremental" in p for p in problems)
    assert any("UC-3 ra NO_IMPROVEMENT" in p for p in problems)


def _optimize_result(case_id, p13, uc3_steps):
    return {
        "id": case_id,
        "group": "optimize",
        "matched": True,
        "mapping": {"p13": p13, "uc3": {"status": "NO_IMPROVEMENT", "llm_calls": 0, "optimizer": uc3_steps}},
    }


def test_before_after_table_lists_every_rule_of_every_run():
    large = _optimize_result(
        "optimize_large",
        [_step("table_to_incremental", False, reason="model đang là `incremental`")],
        [_step("table_to_incremental", True, 0.22, 0.145), _step("missing_partition_filter", False, 0.2, 0.25)],
    )
    small = _optimize_result("optimize_small", [], [_step("table_to_incremental", False, reason="dưới ngưỡng")])
    lines = run_eval.before_after_lines([{"group": "valid", "id": "v"}, large, small])
    assert lines[0] == "## Optimizer: trước → sau"
    rows = [line for line in lines if line.startswith("| `")]
    assert rows == [
        "| `optimize_large` | P13 | `table_to_incremental` | — | — | — | không thử |",
        "| `optimize_large` | UC-3 | `table_to_incremental` | 0.22 | 0.145 | 34.1% | giữ |",
        "| `optimize_large` | UC-3 | `missing_partition_filter` | 0.2 | 0.25 | chậm hơn 25.0% | bỏ |",
        "| `optimize_small` | UC-3 | `table_to_incremental` | — | — | — | không thử |",
    ]
    assert run_eval.before_after_lines([_result("valid", True)]) == []
    assert run_eval.gain_text(1.0, None) == "—" and run_eval.gain_text(None, 0.5) == "—"


def test_summary_counts_cases_with_a_kept_optimization():
    large = _optimize_result("a", [], [_step("table_to_incremental", True, 0.22, 0.145)])
    small = _optimize_result("b", [_step("missing_partition_filter", False, 0.2, 0.25)], [])
    summary = run_eval.summarize_all([{**r, **_result("optimize", True)} for r in (large, small)])
    assert summary["optimizer_kept"] == "1/2" and summary["optimized"] == "2/2"
    assert "- **1/2 ca có bản tối ưu được giữ** (gold giống hệt, nhanh hơn đủ ngưỡng)" in run_eval.summary_lines(
        summary
    )
    assert run_eval.summarize_all([_result("valid", True)])["optimizer_kept"] is None


def test_write_report_appends_the_before_after_table(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "RESULTS_DIR", tmp_path)
    result = {
        **_result("optimize", True),
        **_optimize_result("optimize_large", [], [_step("table_to_incremental", True, 0.22, 0.145)]),
        "status": "READY_FOR_REVIEW",
        "stage": "t_validate",
        "problems": [],
        "llm_calls": 2,
        "expected": "đúng hành vi optimizer",
    }
    text = run_eval.write_report([result], run_eval.summarize_all([result]), "latest_optimize").read_text("utf-8")
    assert "1/1 ca có bản tối ưu được giữ" in text
    assert "| `optimize_large` | UC-3 | `table_to_incremental` | 0.22 | 0.145 | 34.1% | giữ |" in text


def test_data_is_prepared_only_for_cases_that_need_it(monkeypatch):
    from eval.bench import make_events

    calls = []
    monkeypatch.setattr(make_events, "ensure_events", lambda rows: calls.append(rows) or True)
    assert run_eval.prepare_data({"id": "nho"}) is False and calls == []
    assert run_eval.prepare_data({"id": "lon", "bench_rows": 5_000_000}) is True and calls == [5_000_000]


def _result(group, matched, gate=None):
    return {
        "group": group,
        "gate": gate,
        "matched": matched,
        "source_kinds": ["postgres"],
        "spec_attempts": 1,
        "llm_usd": 0.001,
        "seconds": 2.0,
    }


def test_summary_of_all_groups_keeps_denominators():
    summary = run_eval.summarize_all(
        [
            _result("valid", True),
            _result("violation", True, "budget"),
            _result("violation", False, "semantic"),
            _result("ambiguous", True),
        ]
    )
    assert summary["blocked"] == "1/2" and summary["blocked_by_gate"] == {"budget": "1/1", "semantic": "0/1"}
    assert (summary["asked"], summary["total"], summary["valid"]["matched"]) == ("1/1", "3/4", "1/1")
