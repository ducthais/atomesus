"""Bốn luật phát hiện suy giảm (SPEC §9.6.3, FR-22): tất định, không dùng LLM, ngưỡng đọc từ objectives.yml.

Đầu vào là chuỗi số đo của một pipeline (`runs.series`): đường cơ sở lúc chạy thử và từng lần chạy theo lịch, cũ trước.

| Luật                 | Kích hoạt khi                                                                    |
|----------------------|----------------------------------------------------------------------------------|
| `runtime_regression` | trung vị thời gian `window_runs` lần thành công gần nhất > cơ sở × (1 + `runtime_increase_pct`) |
| `cost_regression`    | trung vị chi phí `window_runs` lần gần nhất > cơ sở × (1 + `cost_increase_pct`)  |
| `test_degradation`   | tỷ lệ test dbt fail trong `window_runs` lần gần nhất > `test_fail_rate_pct`      |
| `freshness_breach`   | lần chạy thành công cuối, hoặc lần cuối một nguồn có bản ghi mới, cũ hơn `hard.freshness_minutes` |

Chưa đủ `window_runs` lần chạy thì hai luật trung vị chưa xét (tránh báo nhầm vì một lần chạy chậm).
`freshness_breach` tách riêng nguồn ngừng cấp (`stalled_sources`): đã quan sát nguồn đủ lâu mà không có bản ghi mới.
Đó là sự cố hạ tầng, chỉ cảnh báo, không tạo run sửa (SPEC §9.6.4).
"""

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.policy.models import DomainObjectives

RULES = ("runtime_regression", "cost_regression", "test_degradation", "freshness_breach")


@dataclass(frozen=True)
class Finding:
    rule: str
    observed: float
    threshold: float
    baseline: float | None
    detail: str
    stalled_sources: tuple[str, ...] = ()

    def evidence(self) -> dict:
        return {
            "rule": self.rule,
            "observed": self.observed,
            "threshold": self.threshold,
            "baseline": self.baseline,
            "detail": self.detail,
            "stalled_sources": list(self.stalled_sources),
        }


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _window(runs: list[dict], field: str, size: int, successes_only: bool = False) -> list[float]:
    values = [r[field] for r in runs if r.get(field) is not None and (not successes_only or r["status"] == "SUCCESS")]
    return values[-size:]


def _median_rule(
    rule: str, values: list[float], size: int, baseline: float | None, pct: float, unit: str, word: str
) -> Finding | None:
    if len(values) < size or not baseline:
        return None
    median, limit = statistics.median(values), baseline * (1 + pct / 100)
    if median <= limit:
        return None
    change = round((median - baseline) * 100 / baseline, 1)
    detail = (
        f"đang {word} hơn {change}% so với lúc deploy (trung vị {size} lần gần nhất {median:g} {unit}, "
        f"cơ sở {baseline:g} {unit}, ngưỡng +{pct:g}%)"
    )
    return Finding(rule, median, limit, baseline, detail)


def runtime_regression(runs: list[dict], baseline: dict | None, objectives: DomainObjectives) -> Finding | None:
    rules = objectives.regression
    values = _window(runs, "duration_sec", rules.window_runs, successes_only=True)
    base = (baseline or {}).get("duration_sec")
    return _median_rule(
        "runtime_regression", values, rules.window_runs, base, rules.runtime_increase_pct, "giây", "chậm"
    )


def cost_regression(runs: list[dict], baseline: dict | None, objectives: DomainObjectives) -> Finding | None:
    rules = objectives.regression
    values = _window(runs, "usd", rules.window_runs)
    base = (baseline or {}).get("usd")
    return _median_rule("cost_regression", values, rules.window_runs, base, rules.cost_increase_pct, "USD", "tốn")


def test_degradation(runs: list[dict], objectives: DomainObjectives) -> Finding | None:
    rules = objectives.regression
    window = [r for r in runs if r.get("tests")][-rules.window_runs :]
    total = sum(r["tests"] for r in window)
    if not total:
        return None
    failed = sum(r.get("tests_failed") or 0 for r in window)
    rate = round(failed * 100 / total, 2)
    if rate <= rules.test_fail_rate_pct:
        return None
    detail = (
        f"{failed}/{total} test dbt fail ({rate}%) trong {len(window)} lần chạy gần nhất, "
        f"ngưỡng {rules.test_fail_rate_pct:g}%"
    )
    return Finding("test_degradation", rate, rules.test_fail_rate_pct, None, detail)


def _last_new_records(runs: list[dict]) -> tuple[dict[str, datetime], datetime | None]:
    """Lần cuối từng nguồn có bản ghi mới, và lần quan sát nguồn gần nhất. Lần đọc nguồn lỗi thì bỏ qua."""
    last_new: dict[str, datetime] = {}
    observed = None
    for run in runs:
        sizes = run.get("source_records") or {}
        if not sizes or "error" in sizes:
            continue
        at, new = _at(run["started_at"]), run.get("new_records")
        for source in sizes:
            if new is None:  # lần đầu thấy nguồn (hoặc lần trước đọc lỗi): chưa biết, tính là còn cấp
                last_new.setdefault(source, at)
            elif new.get(source, 0) > 0:
                last_new[source] = at
        observed = at
    return last_new, observed


def freshness_breach(runs: list[dict], objectives: DomainObjectives, now: datetime) -> Finding | None:
    if not runs:
        return None  # chưa có lần chạy nào được ghi: chưa quan sát được gì
    minutes = objectives.hard.freshness_minutes
    limit = timedelta(minutes=minutes)
    last_new, observed = _last_new_records(runs)
    stalled = tuple(
        sorted(source for source, at in last_new.items() if observed is not None and observed - at >= limit)
    )
    successes = [r for r in runs if r["status"] == "SUCCESS" and r.get("ended_at")]
    fresh_at = _at(successes[-1]["ended_at"]) if successes else _at(runs[0]["started_at"])
    age = (now - fresh_at).total_seconds() / 60
    if stalled:
        quiet = max((observed - last_new[s]).total_seconds() / 60 for s in stalled)
        detail = (
            f"nguồn {', '.join(stalled)} không có bản ghi mới trong {quiet:.0f} phút "
            f"(độ tươi tối đa {minutes} phút): nguồn ngừng cấp dữ liệu"
        )
        return Finding("freshness_breach", round(quiet, 1), minutes, None, detail, stalled)
    if age <= minutes:
        return None
    detail = f"lần chạy thành công cuối cách đây {age:.0f} phút, vượt độ tươi tối đa {minutes} phút"
    return Finding("freshness_breach", round(age, 1), minutes, None, detail)


def detect(series: dict, objectives: DomainObjectives, now: datetime) -> list[Finding]:
    """Chạy cả bốn luật trên chuỗi số đo của một pipeline; trả các luật đã kích hoạt."""
    runs, baseline = series.get("runs", []), series.get("baseline")
    found = (
        runtime_regression(runs, baseline, objectives),
        cost_regression(runs, baseline, objectives),
        test_degradation(runs, objectives),
        freshness_breach(runs, objectives, now),
    )
    return [finding for finding in found if finding is not None]
