"""Câu trả lời của trợ lý chat bản Sprint 1: tất định từ RunReport, không gọi LLM (SPEC §14.5).

Mỗi con số kèm tên trường nguồn trong RunReport (NFR-08). Không có dữ liệu thì nói không có, không đoán.
Phân loại 4 ý định bằng model rẻ, `clarify_answer` và `revise` thuộc Sprint 2.
"""

import re

from src.run_report.models import RunReport

_ASK_ROWS = re.compile(r"dòng|bao nhiêu|số lượng|rows?", re.I)
_ASK_WHY = re.compile(r"vì sao|tại sao|sao|lỗi|chặn|dừng|fail|thất bại", re.I)
_ASK_FILES = re.compile(r"file|code|sinh ra", re.I)
_ASK_REVIEW = re.compile(r"duyệt|approve|deploy|chữ ký", re.I)


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
    if _ASK_ROWS.search(question):
        return _rows(report)
    if _ASK_FILES.search(question):
        return _files(report)
    if _ASK_REVIEW.search(question):
        return _review(report)
    if _ASK_WHY.search(question):
        return _why(report)
    return f"{summary(report)}\n{_why(report)}"
