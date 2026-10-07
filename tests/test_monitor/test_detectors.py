"""Bốn luật phát hiện suy giảm: tất định, ngưỡng đọc từ objectives.yml (window_runs, *_pct, freshness_minutes)."""

from datetime import UTC, datetime, timedelta

from src.monitor import detectors
from src.policy.loader import load_policy

T0 = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
SALES = load_policy().objectives["sales"]  # window 7, runtime +50%, cost +40%, test 5%, độ tươi 60 phút
BASELINE = {"duration_sec": 10.0, "usd": 1e-6}


def _run(i, seconds=10.0, usd=1e-6, status="SUCCESS", tests=4, failed=0, records=None, new=None):
    start = T0 + timedelta(hours=i)
    return {
        "run_id": f"r{i}",
        "status": status,
        "started_at": start.isoformat(),
        "ended_at": (start + timedelta(seconds=seconds)).isoformat(),
        "duration_sec": seconds,
        "usd": usd,
        "tests": tests,
        "tests_failed": failed,
        "source_records": records if records is not None else {"orders": 100 + i},
        "new_records": new if new is not None else ({"orders": 1} if i else None),
    }


def _rules(found):
    return [f.rule for f in found]


def _now(runs):
    return datetime.fromisoformat(runs[-1]["ended_at"]) + timedelta(minutes=1)


def test_healthy_pipeline_fires_nothing():
    runs = [_run(i) for i in range(7)]
    assert detectors.detect({"baseline": BASELINE, "runs": runs}, SALES, _now(runs)) == []


def test_runtime_median_over_threshold_fires_only_with_a_full_window():
    slow = [_run(i, seconds=16.0) for i in range(7)]  # +60% > +50%
    found = detectors.detect({"baseline": BASELINE, "runs": slow}, SALES, _now(slow))
    assert _rules(found) == ["runtime_regression"]
    assert found[0].observed == 16.0 and "chậm hơn 60.0%" in found[0].detail
    assert detectors.detect({"baseline": BASELINE, "runs": slow[:6]}, SALES, _now(slow[:6])) == []


def test_one_slow_run_does_not_move_the_median():
    runs = [_run(i) for i in range(6)] + [_run(6, seconds=60.0)]
    assert detectors.detect({"baseline": BASELINE, "runs": runs}, SALES, _now(runs)) == []


def test_cost_regression_reads_its_own_threshold():
    runs = [_run(i, usd=1.5e-6) for i in range(7)]  # +50% > +40%
    assert _rules(detectors.detect({"baseline": BASELINE, "runs": runs}, SALES, _now(runs))) == ["cost_regression"]
    looser = SALES.model_copy(update={"regression": SALES.regression.model_copy(update={"cost_increase_pct": 60})})
    assert detectors.detect({"baseline": BASELINE, "runs": runs}, looser, _now(runs)) == []


def test_test_failure_rate_over_threshold_fires():
    runs = [_run(i) for i in range(6)] + [_run(6, failed=2)]  # 2/28 ≈ 7.1% > 5%
    found = detectors.detect({"baseline": BASELINE, "runs": runs}, SALES, _now(runs))
    assert _rules(found) == ["test_degradation"] and found[0].observed == 7.14


def test_stale_pipeline_is_a_freshness_breach_to_fix():
    runs = [_run(i) for i in range(3)]
    found = detectors.detect({"baseline": BASELINE, "runs": runs}, SALES, _now(runs) + timedelta(hours=2))
    assert _rules(found) == ["freshness_breach"] and found[0].stalled_sources == ()


def test_source_without_new_records_is_reported_as_stalled():
    runs = [_run(0), _run(1, new={"orders": 0}), _run(2, new={"orders": 0})]
    found = detectors.detect({"baseline": BASELINE, "runs": runs}, SALES, _now(runs))
    assert _rules(found) == ["freshness_breach"] and found[0].stalled_sources == ("orders",)
    assert "nguồn ngừng cấp" in found[0].detail


def test_unreadable_source_is_not_called_stalled():
    runs = [_run(0), _run(1, records={"error": "timeout"}), _run(2, records={"error": "timeout"})]
    assert detectors.detect({"baseline": BASELINE, "runs": runs}, SALES, _now(runs)) == []
