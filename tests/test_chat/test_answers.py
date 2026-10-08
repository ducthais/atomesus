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


def _with_rejects(report: RunReport) -> RunReport:
    checks = []
    for check in report.checks:
        if check.name == "staging_reconcile" and check.source_id == "payments":
            observed = {
                **check.observed,
                "rejected": 40,
                "reject_pct": 4.0,
                "reject_reasons": {"date_format:paid_at": 30, "reject_if:amount < 0": 10},
                "reject_samples": [{"_reject_reason": "date_format:paid_at", "paid_at": "31.01.2026"}],
            }
            check = check.model_copy(update={"result": "fail", "observed": observed})
        checks.append(check)
    return report.model_copy(update={"checks": checks})


def test_dirty_sources_are_ranked_and_explained_from_staging_evidence():
    reply = answers.answer(_with_rejects(_fixture("ready_for_review.json")), "Nguồn nào đang bẩn, vì sao?")

    assert reply.startswith("Nguồn có dòng bẩn: `payments`, `orders`.")
    assert reply.index("Nguồn `payments`") < reply.index("Nguồn `orders`"), "nguồn bẩn nhất đứng đầu"
    assert "ngày ở cột `paid_at` không khớp định dạng nào trong date_formats: 30 dòng" in reply
    assert "dòng thỏa luật loại `amount < 0`: 10 dòng" in reply and "31.01.2026" in reply
    assert "checks[staging_reconcile][payments].observed.reject_reasons" in reply


def test_dirty_question_without_test_run_says_there_is_no_data():
    report = _fixture("blocked_semantic.json")
    assert "chưa chạy thử" in answers.answer(report.model_copy(update={"checks": []}), "Dữ liệu có bẩn không?")


def test_only_commands_are_revisions():
    assert answers.intent("Thêm số đơn hàng của mỗi khách hàng") == "revise"
    assert answers.intent("Hãy bỏ nguồn refunds") == "revise"
    for question in ("Vì sao run này dừng?", "Thêm cột status được không?", "Nguồn nào đang bẩn", "tổng tiền sai rồi"):
        assert answers.intent(question) == "ask", question
