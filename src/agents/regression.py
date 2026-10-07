"""Run sửa tự động của Day 2 (SPEC §3.4, §9.6): trigger `regression`, vào thẳng P4 với Spec đang chạy và bằng chứng.

- `created_by = "system"`: mọi Reviewer duyệt được. Day 2 không bao giờ tự deploy (FR-25): bản sửa tốt dừng ở
  READY_FOR_REVIEW chờ người.
- Bỏ P1–P3: kế hoạch và Spec lấy từ run đã deploy; codegen đọc lại schema thật nên nguồn đổi schema vẫn được thấy.
- Chạy thử xong thì so bản đề xuất với chính bản đang chạy, chạy lại trên cùng dữ liệu hiện tại (P13 rút gọn):
  vừa đúng (mọi kiểm tra đạt) vừa tốt hơn ở mọi luật đã kích hoạt → giữ READY_FOR_REVIEW kèm nhãn ba con số;
  không thì NO_IMPROVEMENT (không vào hàng đợi review, monitor gắn cờ pipeline).
- Run dừng giữa chừng (chạm trần gọi LLM, trần vòng spec, quá giờ, bị chặn) giữ nguyên trạng thái để monitor mời
  người sở hữu pipeline vào sửa.
"""

from datetime import UTC, datetime
from uuid import uuid4

from src.agents.nodes.common import AgentDeps
from src.agents.service import Job, _event, _guarded, _run, _save
from src.agents.state import AgentState
from src.agents.timeout import timed
from src.pipeline.postgres import SourceUnavailableError
from src.run_report import RunReport, save_run_report
from src.run_report.store import get_run_files
from src.spec.models import Spec

SYSTEM = "system"


def initial_state(parent: RunReport, evidence: dict) -> AgentState:
    """State của run sửa: Spec và kế hoạch của run đã deploy, bằng chứng suy giảm, bắt đầu ở GENERATING."""
    return {
        "run_id": str(uuid4()),
        "version": 1,
        "created_by": SYSTEM,
        "trigger": "regression",
        "parent_run_id": parent.run_id,
        "source_scope": parent.source_scope,
        "user_request": parent.user_request,
        "domain": parent.domain,
        "plan": parent.plan,
        "spec": parent.spec,
        "regression": {**evidence, "current_spec": parent.spec},
        "attempts": {"spec": 0, "sql": 0},
        "llm_calls": 0,
        "llm_usage": [],
        "error_trace": [],
        "status": "GENERATING",
        "stage_reached": "propose",
        "timeline": [_event("submitted", SYSTEM)],
    }


def _rerun_current(parent: RunReport, deps: AgentDeps, store_path: str):
    """Chạy lại bộ file đang deploy trên dữ liệu hiện tại, cùng trần thời gian. Không có file / quá giờ → None."""
    files = get_run_files(store_path, parent.run_id, parent.version)
    if not files or not parent.spec:
        return None
    spec = Spec.model_validate(parent.spec)
    step = timed(deps, "test_run", lambda _state: {"result": deps.sandbox(spec, files, deps.policy, deps.settings)})
    return step({}).get("result")


def _dbt_failed(checks) -> int | None:
    found = next((c for c in checks if c.name == "dbt_test"), None)
    return found.observed.get("failed") if found else None


def _pct(before: float, after: float) -> float:
    return round((before - after) * 100 / before, 1) if before else 0.0


def compare(report: RunReport, parent: RunReport, current, current_usd: float | None) -> list[dict]:
    """So từng luật đã kích hoạt: bản đề xuất (`report`) với bản đang chạy chạy lại lúc này (`current`)."""
    broken = current is None or current.status != "READY_FOR_REVIEW"
    rows = []
    for rule in (report.regression or {}).get("rules", []):
        name, row = rule["rule"], {"rule": rule["rule"]}
        if broken:
            reason = "bản đang chạy không còn chạy thử đạt trên dữ liệu hiện tại"
            row |= {"better": True, "detail": reason}
        elif name == "runtime_regression":
            before, after = current.duration_sec, report.timing.baseline_sec or 0.0
            row |= {"before": before, "after": after, "better": after < before, "gain_pct": _pct(before, after)}
        elif name == "cost_regression" and current_usd is None:
            row |= {"better": False, "detail": "không đo được chi phí của bản đang chạy để so"}
        elif name == "cost_regression":
            before, after = current_usd, report.cost.usd
            row |= {"before": before, "after": after, "better": after < before, "gain_pct": _pct(before, after)}
        elif name == "test_degradation":
            before, after = _dbt_failed(current.checks) or 0, _dbt_failed(report.checks) or 0
            row |= {"before": before, "after": after, "better": after < before}
        else:  # freshness_breach: bản đang chạy vẫn chạy được thì chỉ lịch chạy mới giúp dữ liệu tươi hơn
            before, after = (parent.spec or {}).get("schedule"), (report.spec or {}).get("schedule")
            row |= {"before": before, "after": after, "better": after != before}
        rows.append(row)
    return rows


def _label(report: RunReport, rows: list[dict]) -> str:
    """Nhãn trong hàng đợi review (SPEC §9.6.5): mức suy giảm, mức cải thiện, độ khớp."""
    evidence = report.regression or {}
    worse = "; ".join(rule["detail"] for rule in evidence.get("rules", []))
    gains = ", ".join(
        f"{row['rule']} tốt hơn {row['gain_pct']}%" if "gain_pct" in row else f"{row['rule']} đã khắc phục"
        for row in rows
    )
    passed = sum(check.result == "pass" for check in report.checks)
    return (
        f"Bản sửa cho `{evidence.get('model')}` · {worse} · bản đề xuất đã thử trên sandbox: {gains} · "
        f"{passed}/{len(report.checks)} phép kiểm tra đạt"
    )


def judge(report: RunReport, parent: RunReport, deps: AgentDeps, store_path: str) -> RunReport:
    """Quyết định bản sửa đáng đưa người duyệt hay chỉ gắn cờ. Run chưa tới READY_FOR_REVIEW thì giữ nguyên."""
    if report.status != "READY_FOR_REVIEW":
        return report
    evidence = dict(report.regression or {})
    if report.spec == parent.spec:
        rows, reason = [], "Agent giữ nguyên Spec đang chạy, không có bản sửa nào để so."
    else:
        current = _rerun_current(parent, deps, store_path)
        try:
            current_usd = deps.estimate_cost(Spec.model_validate(parent.spec), deps.policy, deps.settings)["usd"]
        except SourceUnavailableError:  # nguồn tạm không đọc được: không so được chi phí, không làm hỏng run sửa
            current_usd = None
        rows = compare(report, parent, current, current_usd)
        reason = None if rows and all(row["better"] for row in rows) else "Bản đề xuất không tốt hơn bản đang chạy."
    if reason is None:
        verdict = {"improved": True, "comparison": rows, "label": _label(report, rows)}
        return report.model_copy(update={"regression": {**evidence, "verdict": verdict}})
    verdict = {"improved": False, "comparison": rows, "reason": reason}
    return report.model_copy(
        update={"status": "NO_IMPROVEMENT", "stage_reached": "report", "regression": {**evidence, "verdict": verdict}}
    )


def start_regression(parent: RunReport, evidence: dict, deps: AgentDeps, store_path: str) -> tuple[RunReport, Job]:
    """Ghi run sửa (GENERATING) ngay; việc chạy nền: codegen → budget → arbitrate → render → chạy thử → so sánh."""
    initial, created_at = initial_state(parent, evidence), datetime.now(UTC)
    started = _save(initial, created_at, store_path)

    def job() -> RunReport:
        report = judge(
            _run(initial, created_at, deps, store_path, draft=False, entry="codegen"), parent, deps, store_path
        )
        save_run_report(store_path, report)
        return report

    return started, _guarded(job, initial, created_at, store_path)
