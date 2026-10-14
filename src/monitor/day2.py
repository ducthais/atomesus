"""Vòng Day 2 (SPEC §3.4, §9.6): sau mỗi lần thu số đo, chạy 4 luật cho mọi pipeline đã deploy rồi hành động.

- Nguồn ngừng cấp dữ liệu → cảnh báo `source_stalled`, không tạo run sửa (sự cố hạ tầng, SPEC §9.6.4).
- Luật khác kích hoạt → tạo run sửa (`trigger = regression`) nếu pipeline chưa có run sửa nào trong `cooldown_hours`.
- Run sửa xong:
  - READY_FOR_REVIEW: bản sửa vừa đúng vừa tốt hơn, nằm trong hàng đợi review chờ người duyệt (không tự deploy);
  - NO_IMPROVEMENT: gắn cờ `no_improvement`, không làm phiền ai;
  - dừng giữa chừng (chạm trần gọi LLM / vòng spec, quá giờ, bị chặn, lỗi): cảnh báo `needs_human` mời người sở hữu
    pipeline mở run, sửa Spec rồi chạy lại.

Chỉ xét các lần chạy sau lần deploy hiện tại: bản sửa vừa được duyệt có đường cơ sở mới, số đo của bản cũ không tính.
"""

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta

from src.agents.nodes.common import AgentDeps
from src.agents.regression import start_regression
from src.monitor import alerts, detectors, runs
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy
from src.run_report import RunReport
from src.run_report.access import record_system_run_owner, run_owner
from src.run_report.store import list_run_reports

Launch = Callable[[RunReport, dict], RunReport]  # tạo run sửa, đưa việc chạy nền đi, trả run vừa ghi
Submit = Callable[[Callable[[], object]], None]  # cách chạy nền: BackgroundTasks.add_task, luồng riêng...
RECENT_RUNS = 1000


def pipeline_owner(store_path: str, parent: RunReport) -> str | None:
    """Chủ pipeline ghi vào cảnh báo: bản đang deploy có thể là run sửa (`system`), nên lần về chủ của run gốc. None:
    pipeline cũ không rõ chủ, cảnh báo chỉ Admin và cặp demo thấy; UNKNOWN (không lần ra run gốc): chỉ Admin thấy."""
    return run_owner(store_path, parent.run_id)


def deployed_at(report: RunReport) -> datetime:
    return (report.approval.at if report.approval else report.created_at).astimezone(UTC)


def recent_fix(store_path: str, model: str, hours: float, now: datetime) -> RunReport | None:
    """Run sửa gần nhất của pipeline trong `hours` giờ qua (mọi trạng thái), để không tạo bản sửa lặp lại."""
    since = now - timedelta(hours=hours)
    for report in list_run_reports(store_path, limit=RECENT_RUNS):  # mới nhất trước
        if report.created_at.astimezone(UTC) < since:
            return None
        evidence = report.regression or {}
        detected = datetime.fromisoformat(evidence.get("detected_at") or report.created_at.isoformat())
        if report.trigger == "regression" and evidence.get("model") == model and detected >= since:
            return report
    return None


def _since_deploy(series: dict, parent: RunReport) -> dict:
    start = deployed_at(parent)
    return {**series, "runs": [r for r in series["runs"] if datetime.fromisoformat(r["started_at"]) >= start]}


def overview(settings: PipelineSettings, policy: Policy, now: datetime | None = None) -> list[dict]:
    """Chỉ đọc, cho màn theo dõi: mỗi pipeline đã deploy kèm đường cơ sở, lần chạy gần nhất và các luật đang kích hoạt.

    Cùng cách xét như `check` (chỉ các lần chạy sau lần deploy hiện tại) nhưng không cảnh báo, không tạo run sửa.
    """
    now = now or datetime.now(UTC)
    result = []
    for model in runs.deployed_models(settings.deploy_dir):
        series = runs.series(settings.store_path, model)
        parent = runs.deployed_report(settings.store_path, model)
        objectives = policy.objectives.get(parent.domain) if parent else None
        current = _since_deploy(series, parent) if parent else series
        findings = detectors.detect(current, objectives, now) if objectives else []
        result.append(
            {
                "model": model,
                "domain": parent.domain if parent else None,
                "owner": parent.created_by if parent else None,
                "deployed_run_id": parent.run_id if parent else None,
                "schedule": ((parent.spec or {}).get("schedule") or {}).get("cron") if parent else None,
                "baseline": series["baseline"],
                "runs": len(current["runs"]),
                "last": current["runs"][-1] if current["runs"] else None,
                "findings": [finding.evidence() for finding in findings],
            }
        )
    return result


def check(
    settings: PipelineSettings,
    policy: Policy,
    launch: Launch,
    now: datetime | None = None,
    models: Collection[str] | None = None,
) -> list[dict]:
    """Chạy 4 luật cho mọi pipeline đã deploy (hoặc chỉ `models`, khi người dùng tự bấm: không đụng pipeline người
    khác). Trả danh sách việc đã làm (cảnh báo, run sửa, bỏ qua vì cooldown)."""
    now = now or datetime.now(UTC)
    actions = []
    for model in runs.deployed_models(settings.deploy_dir):
        if models is not None and model not in models:
            continue
        parent = runs.deployed_report(settings.store_path, model)
        if parent is None or parent.domain not in policy.objectives:
            continue
        objectives = policy.objectives[parent.domain]
        cooldown = objectives.regression.cooldown_hours
        series = _since_deploy(runs.series(settings.store_path, model), parent)
        findings = detectors.detect(series, objectives, now)
        for finding in (f for f in findings if f.stalled_sources):
            alert = alerts.raise_alert(
                settings.store_path,
                model,
                "source_stalled",
                f"Pipeline `{model}`: {finding.detail}. Đây là sự cố nguồn, không tạo bản sửa.",
                finding.evidence(),
                owner=pipeline_owner(settings.store_path, parent),
                quiet_hours=cooldown,
                now=now,
            )
            actions.append({"model": model, "action": "alert" if alert else "already_alerted", "rule": finding.rule})
        fixable = [f for f in findings if not f.stalled_sources]
        if not fixable:
            continue
        rules = [f.rule for f in fixable]
        previous = recent_fix(settings.store_path, model, cooldown, now)
        if previous is not None:
            actions.append({"model": model, "action": "cooldown", "run_id": previous.run_id, "rules": rules})
            continue
        evidence = {"model": model, "detected_at": now.isoformat(), "rules": [f.evidence() for f in fixable]}
        started = launch(parent, evidence)
        actions.append({"model": model, "action": "fix_started", "run_id": started.run_id, "rules": rules})
    return actions


def follow_up(report: RunReport, parent: RunReport, store_path: str, now: datetime | None = None) -> dict | None:
    """Sau khi run sửa kết thúc: gắn cờ khi không có bản tốt hơn, mời người vào sửa khi run dừng giữa chừng."""
    model = (report.regression or {}).get("model") or "unknown"
    owner = pipeline_owner(store_path, parent)
    if report.status == "READY_FOR_REVIEW":
        return None  # đã nằm trong hàng đợi review
    if report.status == "NO_IMPROVEMENT":
        reason = ((report.regression or {}).get("verdict") or {}).get("reason", "")
        return alerts.raise_alert(
            store_path,
            model,
            "no_improvement",
            f"Pipeline `{model}` đang suy giảm nhưng run sửa {report.run_id} không tìm được bản tốt hơn. {reason} "
            "Đã gắn cờ để kỹ sư xem khi rảnh.",
            {"rules": (report.regression or {}).get("rules", []), "verdict": (report.regression or {}).get("verdict")},
            run_id=report.run_id,
            owner=owner,
            now=now,
        )
    last = report.error_trace[-1] if report.error_trace else {}
    return alerts.raise_alert(
        store_path,
        model,
        "needs_human",
        f"Run sửa tự động {report.run_id} cho `{model}` dừng ở {report.status} "
        f"(phase {last.get('stage', report.stage_reached)}): {str(last.get('error', ''))[:300]} "
        f"Agent không tự giải quyết được. Mời {owner} mở run, sửa Spec rồi chạy lại.",
        {"status": report.status, "stage": report.stage_reached, "error_trace": report.error_trace[-3:]},
        run_id=report.run_id,
        owner=owner,
        now=now,
    )


def launcher(deps: AgentDeps, store_path: str, submit: Submit) -> Launch:
    """Launch thật: ghi run sửa kèm chủ pipeline, chạy nền (graph + so sánh), xong thì gắn cờ / mời người nếu cần."""

    def launch(parent: RunReport, evidence: dict) -> RunReport:
        started, job = start_regression(parent, evidence, deps, store_path)
        record_system_run_owner(store_path, started.run_id, parent.run_id)
        submit(lambda: follow_up(job(), parent, store_path))
        return started

    return launch
