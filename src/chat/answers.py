"""Câu trả lời của trợ lý chat: tất định từ RunReport, không gọi LLM (SPEC §14.5).

Mỗi con số kèm tên trường nguồn trong RunReport (NFR-08). Không có dữ liệu thì nói không có, không đoán.
Tin nhắn trong một run được phân loại `ask` (hỏi, không đổi run) hoặc `revise` (câu mệnh lệnh đòi đổi pipeline) bằng
luật tất định: câu hỏi luôn là `ask`, nên một câu hỏi không bao giờ vô tình tạo version mới. `clarify_answer` chưa có.
"""

import re

from src.run_report.models import RunReport

_ASK_ROWS = re.compile(r"dòng|bao nhiêu|số lượng|rows?", re.I)
_ASK_WHY = re.compile(r"vì sao|tại sao|sao|lỗi|chặn|dừng|fail|thất bại", re.I)
_ASK_FILES = re.compile(r"file|code|sinh ra", re.I)
_ASK_REVIEW = re.compile(r"duyệt|approve|deploy|chữ ký", re.I)
_ASK_DIRTY = re.compile(r"bẩn|rác|dirty|bị loại|bị tách|reject|chất lượng|lỗi dữ liệu|không sạch", re.I)
# Câu hỏi: có dấu hỏi, hoặc mở đầu / kết thúc bằng từ để hỏi.
_QUESTION = re.compile(
    r"\?\s*$|^\s*(vì sao|tại sao|sao|bao nhiêu|có bao|ai|khi nào|ở đâu|nguồn nào|cột nào|bảng nào|"
    r"thế nào|như thế nào|làm sao|what|why|how|which|who|when)\b|\b(không|chưa|chứ|nhỉ|à|hả)\s*[.!]?\s*$",
    re.I,
)
# Mệnh lệnh đổi pipeline: mở đầu bằng động từ sửa (có thể có "hãy", "vui lòng", "cho tôi").
_REVISE = re.compile(
    r"^\s*(?:(?:hãy|vui lòng|làm ơn|xin|cho tôi|giúp tôi)\s+)*"
    r"(đổi|thêm|bỏ|xóa|xoá|sửa|thay|chuyển|gộp|lọc|loại|tách|dùng|đặt|tính thêm|tính lại|giữ|ghép|nối|"
    r"change|add|remove|drop|rename|replace|filter|group|use)\b",
    re.I,
)
# Mã lý do loại dòng do staging ghi (templates/dbt_staging.sql.j2) → lời giải thích.
_REASONS = {
    "date_format": "ngày ở cột `{}` không khớp định dạng nào trong date_formats",
    "cast": "giá trị cột `{}` không ép được sang kiểu đã khai báo",
    "unknown_currency": "cột `{}` có mã tiền tệ không có trong bảng tỷ giá",
    "reject_if": "dòng thỏa luật loại `{}`",
}


def _short(run_id: str) -> str:
    return run_id[:8]


def _why(report: RunReport) -> str:
    lines = []
    if report.error_trace:
        last = report.error_trace[-1]
        reason = last.get("error") or last.get("message") or "không có mô tả"
        lines.append(f"Dừng ở phase `{last.get('stage', report.stage_reached)}`: {reason} (error_trace[-1])")
        if last.get("question"):
            lines.append(f"Agent cần bạn làm rõ: {last['question']} (trả lời trong chat có ở Sprint 2)")
    lines += [f"Cổng không đạt: `{g.name}` {g.detail}".rstrip() for g in report.gates if g.result == "fail"]
    lines += [
        f"Kiểm tra không đạt: `{c.name}`" + (f" · nguồn {c.source_id}" if c.source_id else "")
        for c in report.checks
        if c.result == "fail"
    ]
    return "\n".join(lines) or f"Run không có lỗi nào được ghi; trạng thái `{report.status}`."


def _rows(report: RunReport) -> str:
    lines = []
    for check in report.checks:
        observed = check.observed
        if check.name == "row_reconcile":
            lines.append(
                f"Nguồn `{check.source_id}`: {observed.get('source')} dòng ở nguồn, {observed.get('bronze')} dòng "
                f"ở bronze (checks[row_reconcile].observed)"
            )
        elif check.name == "staging_reconcile":
            lines.append(
                f"Staging `{check.source_id}`: {observed.get('staging')} sạch, {observed.get('rejected')} bị tách, "
                f"{observed.get('duplicates', 0)} trùng (checks[staging_reconcile].observed)"
            )
    return "\n".join(lines) or "RunReport chưa có số dòng: run dừng trước bước chạy thử trong sandbox."


def _reason(code: str) -> str:
    kind, _, detail = code.partition(":")
    return _REASONS[kind].format(detail) if kind in _REASONS else f"`{code}`"


def _dirty(report: RunReport) -> str:
    """Nguồn nào đang bẩn, vì sao: dòng bị tách ở staging theo lý do, trùng, và lệch nguồn ↔ bronze theo từng nguồn."""
    if not report.checks:
        return f"Run chưa chạy thử nên chưa có số đo chất lượng dữ liệu (checks rỗng); trạng thái `{report.status}`."
    lines = []
    staging = sorted(
        (c for c in report.checks if c.name == "staging_reconcile"),
        key=lambda c: c.observed.get("reject_pct", 0),
        reverse=True,
    )
    for check in staging:
        o, where = check.observed, f"checks[staging_reconcile][{check.source_id}].observed"
        limit = f", ngưỡng {o['max_reject_pct']}%" if o.get("max_reject_pct") is not None else ""
        verdict = "không đạt" if check.result == "fail" else "đạt"
        line = (
            f"Nguồn `{check.source_id}`: {o.get('rejected', 0)}/{o.get('bronze', 0)} dòng bị tách "
            f"({o.get('reject_pct', 0)}%{limit}: {verdict}), "
            f"{max(o.get('duplicates', 0), 0)} dòng trùng bị bỏ ({where})"
        )
        reasons = list((o.get("reject_reasons") or {}).items())[:3]
        if reasons:
            line += "\n  Vì: " + "; ".join(f"{_reason(code)}: {n} dòng" for code, n in reasons)
            line += f" ({where}.reject_reasons)"
        samples = [s for s in o.get("reject_samples") or [] if isinstance(s, dict)][:2]
        if samples:
            shown = [{k: v for k, v in s.items() if not k.startswith("_")} for s in samples]
            line += f"\n  Ví dụ dòng bị tách: {shown} ({where}.reject_samples)"
        lines.append(line)
    for check in report.checks:
        if check.result != "fail" or check.name not in ("row_reconcile", "hash_reconcile", "idempotent"):
            continue
        o, where = check.observed, f"checks[{check.name}][{check.source_id}].observed"
        if check.name == "row_reconcile":
            lines.append(
                f"Nguồn `{check.source_id}`: {o.get('source')} dòng ở nguồn nhưng {o.get('bronze')} ở bronze ({where})"
            )
        elif check.name == "hash_reconcile":
            lines.append(
                f"Nguồn `{check.source_id}`: tập khóa lệch, thiếu ở bronze {o.get('missing_in_bronze')}, "
                f"thừa {o.get('extra_in_bronze')} ({where})"
            )
        else:
            lines.append(
                f"Nguồn `{check.source_id}`: nạp lần hai làm bronze đổi {o.get('first_rows')} → {o.get('second_rows')} "
                f"dòng, khóa chính có thể không duy nhất ({where})"
            )
    if not lines:
        return "RunReport chưa có số đo staging: run dừng trước bước làm sạch."
    dirty = [c.source_id for c in staging if c.observed.get("rejected") or c.result == "fail"]
    head = f"Nguồn có dòng bẩn: {', '.join(f'`{s}`' for s in dirty)}." if dirty else "Không nguồn nào có dòng bị tách."
    return head + "\n" + "\n".join(lines)


def intent(message: str) -> str:
    """`revise` khi tin nhắn là mệnh lệnh đổi pipeline, còn lại (kể cả mọi câu hỏi) là `ask`."""
    if _QUESTION.search(message):
        return "ask"
    return "revise" if _REVISE.search(message) else "ask"


def revised_reply(report: RunReport, message: str) -> str:
    """Câu trả lời khi chat đã tạo version mới để sửa pipeline."""
    return (
        f"Đã tạo version {report.version} của run `{_short(report.run_id)}` để sửa theo yêu cầu: «{message}» "
        "(spec_history[-1]). Agent viết lại Spec rồi chạy thử; bản duyệt cũ (nếu có) không còn hiệu lực, "
        "version mới phải được duyệt lại."
    )


def _files(report: RunReport) -> str:
    if not report.files:
        return "Run chưa sinh file nào (files rỗng)."
    names = "\n".join(f"- `{name}`" for name in sorted(report.files))
    return f"{len(report.files)} file, files_hash `{report.files_hash}`:\n{names}"


def _review(report: RunReport) -> str:
    if report.approval is None:
        if report.status == "READY_FOR_REVIEW":
            return "Run đang chờ Reviewer duyệt; chưa có chữ ký (approval = null)."
        return f"Run chưa có chữ ký phê duyệt; trạng thái `{report.status}` (approval = null)."
    a = report.approval
    return f"`{a.decision}` bởi {a.reviewer} lúc {a.at.isoformat()} cho version {a.version} (approval)."


def summary(report: RunReport) -> str:
    return (
        f"Run `{_short(report.run_id)}` v{report.version}: trạng thái `{report.status}`, dừng ở phase "
        f"`{report.stage_reached}`, {report.llm_calls} lần gọi LLM (llm_calls)."
    )


def created_reply(report: RunReport) -> str:
    """Câu trả lời sau khi chat tạo run mới."""
    if report.status == "DRAFT" and report.stage_reached == "confirm" and report.plan:
        plan = report.plan
        tables = ", ".join(
            f"{s['source_ref']}.{s['object']}" if isinstance(s, dict) else str(s) for s in plan.get("sources", [])
        )
        return (
            f"{summary(report)}\nKế hoạch: {plan.get('summary') or '(không có tóm tắt)'} (plan.summary)\n"
            f"Bảng đọc: {tables} (plan.sources)\n"
            "Mở run và bấm **Xác nhận kế hoạch** để agent viết Spec và chạy thử; chat không xác nhận thay bạn."
        )
    if report.status == "READY_FOR_REVIEW":
        return f"{summary(report)}\nMọi kiểm tra trong sandbox đều đạt, run đã vào hàng đợi duyệt."
    return f"{summary(report)}\n{_why(report)}"


def answer(report: RunReport, question: str) -> str:
    """Trả lời câu hỏi `ask` về một run, chỉ từ các trường của RunReport."""
    if _ASK_DIRTY.search(question):
        return _dirty(report)
    if _ASK_ROWS.search(question):
        return _rows(report)
    if _ASK_FILES.search(question):
        return _files(report)
    if _ASK_REVIEW.search(question):
        return _review(report)
    if _ASK_WHY.search(question):
        return _why(report)
    return f"{summary(report)}\n{_why(report)}"
