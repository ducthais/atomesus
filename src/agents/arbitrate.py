"""Bước `arbitrate` (P5, SPEC §9.2): chọn lịch chạy thỏa `objectives.yml` của domain, tất định, không gọi LLM.

Phương án là các lịch chạy: lịch trong Spec cộng vài tần suất chuẩn. Với mỗi phương án:
- độ tươi xấu nhất = khoảng cách lớn nhất giữa hai lần chạy liên tiếp (dữ liệu cũ nhất người dùng có thể thấy),
- chi phí mỗi ngày = chi phí ước tính một lần chạy (cổng budget, P8) × số lần chạy mỗi ngày.

Luật:
1. Loại phương án có độ tươi xấu nhất vượt `hard.freshness_minutes`.
2. Lịch trong Spec còn lại thì giữ: có thể là ý người dùng ("cập nhật mỗi 15 phút"), không đổi khi nó đã đạt.
   Bị loại thì đổi sang phương án rẻ nhất còn lại (`prefer: cheapest_feasible`) và ghi lý do.
3. Không phương án nào đạt → dừng chờ người, kèm bảng so sánh các phương án.
Chi phí vượt `soft.cost_usd_per_day` chỉ ghi cảnh báo, không chặn (ràng buộc mềm).
"""

from datetime import datetime, timedelta

from src.policy.models import DomainObjectives
from src.run_report.models import GateResult

CANDIDATES = ("*/15 * * * *", "*/30 * * * *", "0 * * * *", "0 */6 * * *", "0 2 * * *")
WINDOW_DAYS = 35  # đủ phủ lịch theo tuần / tháng
FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


class CronError(ValueError):
    pass


def _field(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, _, step = part.partition("/")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            start, end = (int(x) for x in base.split("-", 1))
        else:
            start = end = int(base)
        if step and base != "*" and "-" not in base:
            end = high
        values.update(range(start, end + 1, int(step) if step else 1))
    if not values or min(values) < low or max(values) > high:
        raise CronError(f"trường cron '{text}' ngoài khoảng {low}-{high}")
    return values


def cadence(cron: str) -> dict:
    """Số đo của một lịch cron 5 trường: khoảng cách lớn nhất giữa hai lần chạy (phút) và số lần chạy mỗi ngày."""
    parts = cron.split()
    if len(parts) != 5:
        raise CronError(f"cron '{cron}' phải có 5 trường")
    try:
        minutes, hours, days, months, weekdays = (_field(p, *r) for p, r in zip(parts, FIELD_RANGES, strict=True))
    except ValueError as exc:
        raise CronError(f"cron '{cron}' không đọc được: {exc}") from exc
    weekdays = {d % 7 for d in weekdays}
    dom_any, dow_any = parts[2] == "*", parts[4] == "*"
    start = datetime(2026, 1, 5)  # thứ Hai, cố định để kết quả tất định
    fires = []
    for day in range(WINDOW_DAYS):
        date = start + timedelta(days=day)
        dom_ok, dow_ok = date.day in days, (date.weekday() + 1) % 7 in weekdays
        day_ok = (dom_ok or dow_ok) if not (dom_any or dow_any) else (dom_ok and dow_ok)
        if date.month not in months or not day_ok:
            continue
        fires += [day * 1440 + h * 60 + m for h in sorted(hours) for m in sorted(minutes)]
    if not fires:
        return {"max_gap_minutes": None, "runs_per_day": 0.0}
    window = WINDOW_DAYS * 1440
    gaps = [b - a for a, b in zip(fires, fires[1:], strict=False)] + [fires[0] + window - fires[-1]]
    return {"max_gap_minutes": max(gaps), "runs_per_day": round(len(fires) / WINDOW_DAYS, 4)}


def arbitrate(cron: str, run_usd: float, objectives: DomainObjectives) -> tuple[GateResult, str | None, str | None]:
    """Trả (cổng arbitrate, cron được chọn hoặc None nếu không phương án nào đạt, lý do dừng)."""
    limit, soft = objectives.hard.freshness_minutes, objectives.soft.cost_usd_per_day
    table = []
    for candidate in dict.fromkeys([cron, *CANDIDATES]):
        measured = cadence(candidate)
        gap = measured["max_gap_minutes"]
        table.append(
            {
                "cron": candidate,
                "from_spec": candidate == cron,
                "max_gap_minutes": gap,
                "runs_per_day": measured["runs_per_day"],
                "usd_per_day": run_usd * measured["runs_per_day"],
                "feasible": gap is not None and gap <= limit,
            }
        )
    feasible = [row for row in table if row["feasible"]]
    observed = {"freshness_minutes": limit, "soft_cost_usd_per_day": soft, "proposals": table}
    if not feasible:
        densest = min(
            (row for row in table if row["max_gap_minutes"] is not None), key=lambda row: row["max_gap_minutes"]
        )
        reason = (
            f"Không lịch chạy nào giữ được dữ liệu tươi trong {limit} phút (objectives.yml hard.freshness_minutes); "
            f"lịch dày nhất đã thử là '{densest['cron']}' (dữ liệu cũ tới {densest['max_gap_minutes']} phút). "
            "Cần người quyết định nới ràng buộc hoặc dùng cách nạp khác."
        )
        return GateResult(name="arbitrate", result="fail", detail=reason, observed=observed), None, reason
    own = next(row for row in table if row["from_spec"])
    if own["feasible"]:
        chosen, why = own, f"giữ lịch của Spec '{cron}': dữ liệu cũ nhất {own['max_gap_minutes']} phút ≤ {limit} phút"
    else:
        chosen = min(feasible, key=lambda row: (row["usd_per_day"], -row["max_gap_minutes"]))
        why = (
            f"lịch của Spec '{cron}' để dữ liệu cũ tới {own['max_gap_minutes']} phút > {limit} phút "
            f"(hard.freshness_minutes); đổi sang '{chosen['cron']}', rẻ nhất trong các lịch đạt "
            f"(prefer: {objectives.prefer})"
        )
    warn = (
        f"; cảnh báo: ước tính {chosen['usd_per_day']:.6f} USD/ngày vượt soft.cost_usd_per_day = {soft}"
        if chosen["usd_per_day"] > soft
        else ""
    )
    observed["chosen"] = chosen["cron"]
    return GateResult(name="arbitrate", result="pass", detail=why + warn, observed=observed), chosen["cron"], None
