from src.chat import answers
from src.run_report import FIXTURES_DIR, RunReport


def _fixture(name: str) -> RunReport:
    return RunReport.model_validate_json((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_rows_question_cites_report_fields():
    reply = answers.answer(_fixture("ready_for_review.json"), "Nguồn có bao nhiêu dòng?")

    assert "Nguồn `orders`: 12480 dòng ở nguồn, 12480 dòng ở bronze" in reply
    assert "checks[staging_reconcile].observed" in reply


def test_why_lists_error_trace_and_failed_gate():
    reply = answers.answer(_fixture("blocked_semantic.json"), "Vì sao bị chặn?")

    assert "error_trace[-1]" in reply
    assert "Cổng không đạt: `semantic`" in reply


def test_review_question_reads_approval_signature():
    assert "`approved` bởi an" in answers.answer(_fixture("deployed.json"), "Ai đã duyệt?")
    assert "approval = null" in answers.answer(_fixture("ready_for_review.json"), "Đã duyệt chưa?")


def test_created_reply_for_ready_run_says_it_is_queued_for_review():
    assert "hàng đợi duyệt" in answers.created_reply(_fixture("ready_for_review.json"))
