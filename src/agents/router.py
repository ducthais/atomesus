"""Bảng định tuyến lỗi (SPEC §11.2, §11.3): lỗi ở phase nào, loại gì → quay về đâu, trần bao nhiêu lần.

Viết dạng bảng dữ liệu, không phải chuỗi `if`. Mỗi dòng là một `Route`; `goto` là một trong:
- `propose`: vòng spec, codegen viết lại Spec kèm lỗi cụ thể (P4). Đếm `attempts.spec`, trần `max_spec_attempts`.
- `sql_fix`: vòng SQL, codegen chỉ sửa biểu thức SQL rồi render lại (P6). Đếm `attempts.sql`, trần `max_sql_attempts`.
- `stop`: dừng ở `status`, không thử lại.
Trần đọc từ `budgets.yml: per_run`, không hardcode. Trần gọi LLM toàn cục (`max_llm_calls`) vẫn chặn mọi vòng.

`classify` đọc kết quả chạy thử (P9–P12) và trả các lỗi đã phân loại; `decide` chọn đường đi cho cả lượt: có lỗi phải
dừng thì dừng, có lỗi cần viết lại Spec thì đi vòng spec (sửa Spec cũng sửa được SQL), còn lại đi vòng SQL.
"""

from dataclasses import dataclass

from src.policy.models import Policy


@dataclass(frozen=True)
class Route:
    phase: str
    kind: str
    goto: str  # propose | sql_fix | stop
    status: str | None = None  # trạng thái khi dừng
    hint: str = ""  # hướng dẫn kèm lỗi khi quay lại codegen


ROUTES: tuple[Route, ...] = (
    Route("P3", "semantic", "stop", "BLOCKED_SEMANTIC_CONFLICT"),
    Route("P5", "no_feasible_option", "stop", "NEEDS_HUMAN"),
    Route("P6", "render_error", "stop", "INTERNAL_ERROR"),
    Route("P7", "spec_schema", "propose"),
    Route("P7", "schema_real", "propose"),
    Route("P7", "filter_values", "propose"),
    Route("P7", "ast_guard", "stop", "BLOCKED_AST"),
    Route("P8", "budget", "propose", hint="Giảm số byte phải quét xuống dưới trần (bỏ cột không dùng, nạp tăng dần)."),
    Route("P9", "source_unreachable", "stop", "INFRA_ERROR"),
    Route(
        "P9",
        "el_run",
        "propose",
        hint="dlt không nạp được nguồn: kiểm tra tên cột, kiểu, primary_key, cursor_field của nguồn đó.",
    ),
    Route("P10", "row_reconcile", "propose", hint="Số dòng bronze lệch nguồn: kiểm tra primary_key và chế độ ghi."),
    Route("P10", "hash_reconcile", "propose", hint="Tập khóa bronze lệch nguồn: kiểm tra primary_key và chế độ ghi."),
    Route(
        "P10",
        "idempotent",
        "propose",
        hint="Nạp lần hai làm đổi bronze: xem lại primary_key của nguồn này, khóa phải duy nhất trên mỗi dòng.",
    ),
    Route("P11", "dbt_run", "sql_fix", hint="dbt không chạy được model: sửa biểu thức SQL bị báo lỗi."),
    Route(
        "P12",
        "staging_rejects",
        "propose",
        hint="Quá nhiều dòng bị tách ở staging: sửa khối staging (date_formats, kiểu cột, reject_if) theo lý do và "
        "dòng mẫu bị tách.",
    ),
    Route("P12", "dbt_test", "sql_fix", hint="Sửa biểu thức hoặc khóa làm dbt test không đạt."),
    Route(
        "P12",
        "metric_reconcile",
        "sql_fix",
        hint="Chỉ số gold khác khi tính lại thẳng trên nguồn: sửa biểu thức metrics / filters cho đúng ý nghĩa.",
    ),
    Route(
        "P12",
        "empty_gold",
        "sql_fix",
        hint="Gold rỗng dù staging có dữ liệu: bỏ hoặc sửa transform.filters, chỉ dùng giá trị có trong dòng mẫu.",
    ),
    # Lệch đều một số giờ là lỗi cấu hình múi giờ của warehouse, sửa Spec không hết được: dừng cho người xem.
    Route("P12", "timezone_shift", "stop", "NEEDS_HUMAN"),
    Route("P13", "optimization_worse", "stop"),
    Route("P16", "ci_failed", "propose"),
    Route("P16", "deploy_not_green", "stop", "DEPLOY_FAILED"),
)
BY_KIND = {route.kind: route for route in ROUTES}
LOOPS = {"propose": "spec", "sql_fix": "sql"}  # vòng → khóa trong attempts


def limit(goto: str, policy: Policy) -> int:
    """Trần của một vòng, đọc từ `budgets.yml: per_run.max_<vòng>_attempts`."""
    return getattr(policy.budgets.per_run, f"max_{LOOPS[goto]}_attempts")


@dataclass(frozen=True)
class Failure:
    kind: str
    detail: str
    source_id: str | None = None

    @property
    def route(self) -> Route:
        return BY_KIND[self.kind]

    def feedback(self) -> str:
        where = f"[{self.route.phase} {self.kind}{f' · nguồn {self.source_id}' if self.source_id else ''}]"
        return " ".join(part for part in (where, self.detail, self.route.hint) if part)


@dataclass(frozen=True)
class Decision:
    goto: str  # propose | sql_fix | stop
    failures: tuple[Failure, ...]
    status: str | None = None


def _staging(check: dict) -> str:
    o = check["observed"]
    reasons = ", ".join(
        f"{reason}: {count} dòng" for reason, count in list((o.get("reject_reasons") or {}).items())[:3]
    )
    samples = [
        {k: v for k, v in sample.items() if not k.startswith("_") or k == "_reject_reason"}
        for sample in (o.get("reject_samples") or [])[:3]
    ]
    text = f"{o.get('rejected')}/{o.get('bronze')} dòng bị tách ({o.get('reject_pct')}% > {o.get('max_reject_pct')}%)"
    if o.get("duplicates", 0) < 0 or not o.get("bronze"):
        text = f"bronze {o.get('bronze')} dòng, staging {o.get('staging')}, tách {o.get('rejected')}"
    return (
        text
        + (f"; lý do nhiều nhất: {reasons}" if reasons else "")
        + (f"; dòng mẫu bị tách: {samples}" if samples else "")
    )


def _metric(check: dict) -> str:
    o = check["observed"]
    shown = (o.get("mismatched") or [])[:3]
    return (
        f"{o.get('mismatch_count')} giá trị khác, {o.get('missing_count')} dòng thiếu, {o.get('extra_count')} dòng thừa"
        f"; tổng {o.get('totals')}" + (f"; ví dụ {shown}" if shown else "")
    )


def classify(state: dict) -> list[Failure]:
    """Lỗi của lượt chạy thử vừa xong (state sau node test_run), mỗi lỗi một `Failure` có trong ROUTES."""
    stage = state.get("stage_reached")
    last = (state.get("error_trace") or [{}])[-1]
    if stage in ("el_run", "el_validate"):
        kind = "source_unreachable" if state.get("status") == "INFRA_ERROR" else "el_run"
        return [Failure(kind, str(last.get("error", ""))[-800:], last.get("source_id"))]
    if stage == "t_run":
        return [Failure("dbt_run", str(last.get("error", ""))[-800:])]
    failures = []
    for check in state.get("checks") or []:
        if check.get("result") != "fail":
            continue
        name, sid, o = check["name"], check.get("source_id"), check.get("observed") or {}
        if name in ("row_reconcile", "hash_reconcile", "idempotent"):
            failures.append(Failure(name, str({k: v for k, v in o.items() if not k.endswith("_hash")}), sid))
        elif name == "staging_reconcile":
            failures.append(Failure("staging_rejects", _staging(check), sid))
        elif name == "dbt_test":
            failures.append(Failure("dbt_test", str(o.get("failures", []))[:800]))
        elif name == "metric_reconcile":
            kind = "timezone_shift" if o.get("shift_hours") else "metric_reconcile"
            failures.append(Failure(kind, _metric(check)))
    post_run = next((g for g in reversed(state.get("gates") or []) if g.get("name") == "post_run"), None)
    if post_run and "gold rỗng" in (post_run.get("detail") or ""):
        failures.append(Failure("empty_gold", str(last.get("error", ""))))
    return failures


def decide(failures: list[Failure]) -> Decision:
    """Đường đi cho cả lượt: không phân loại được hoặc có lỗi phải dừng → stop; có lỗi cần viết lại Spec → propose."""
    if not failures:
        return Decision("stop", ())
    stops = [f for f in failures if f.route.goto == "stop"]
    if stops:
        return Decision("stop", tuple(failures), stops[0].route.status)
    goto = "propose" if any(f.route.goto == "propose" for f in failures) else "sql_fix"
    return Decision(goto, tuple(failures))
