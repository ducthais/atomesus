"""Kiểm tra Spec do LLM sinh trước khi render (phần tất định của P7, SPEC §10).

- `spec_schema`: đúng hợp đồng `Spec` và chính sách `policy/*.yml`.
- `schema_real`: mọi cột Spec dùng có thật trong schema đã đọc ở P3; cột chuỗi ép sang ngày phải có
  `date_formats`; khóa join cùng kiểu sau staging.
- `time_grain`: yêu cầu nói "theo ngày / tuần / tháng / quý / năm" thì cột thời gian của grain phải gộp đúng kỳ đó,
  không gộp theo từng thời điểm chính xác (mỗi giao dịch một dòng). Chỉ chạy khi yêu cầu có nói kỳ.
- `ast_guard` (tạm): biểu thức không chứa lệnh/hàm nguy hiểm, xem `src/codegen/guard.py`.

Lỗi `spec_schema`/`schema_real` quay lại codegen kèm thông báo cụ thể (vòng spec); lỗi `ast_guard` dừng hẳn.
"""

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from src.codegen.guard import expression_violations
from src.policy.models import Policy
from src.run_report.models import GateResult
from src.spec.models import Spec
from src.spec.policy_check import policy_violations

TEXT_TYPES = {"text", "character varying", "varchar", "character", "char"}

PERIODS = {"ngày": "day", "tuần": "week", "tháng": "month", "quý": "quarter", "năm": "year"}
PERIOD_LABEL = {unit: word for word, unit in PERIODS.items()}
# "theo tháng", "theo từng ngày", "mỗi tuần", "từng quý"… ("hằng ngày" thường nói về lịch chạy nên không tính).
_PERIOD = re.compile(r"(?<!\w)(?:theo|mỗi|từng)\s+(?:từng\s+|mỗi\s+)?(ngày|tuần|tháng|quý|năm)(?!\w)", re.IGNORECASE)
_PERIOD_EN = re.compile(
    r"(?<![a-z])(?:daily|weekly|monthly|quarterly|yearly|per (day|week|month|quarter|year))(?![a-z])", re.IGNORECASE
)
_PERIOD_EN_WORDS = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}


def requested_period(request: str | None) -> str | None:
    """Kỳ thời gian người dùng muốn xem kết quả (day/week/month/quarter/year), hoặc None nếu không nói."""
    if not request:
        return None
    if match := _PERIOD.search(request):
        return PERIODS[match.group(1).lower()]
    if match := _PERIOD_EN.search(request):
        return match.group(1).lower() if match.group(1) else _PERIOD_EN_WORDS[match.group(0).lower()]
    return None


def _time_grain(spec: Spec, unit: str) -> list[str]:
    """Grain có gộp cột thời gian đúng kỳ người dùng yêu cầu không."""
    t = spec.transform
    outputs = {s.id: s.outputs for s in spec.sources}
    kinds = {g: outputs[g.rpartition(".")[0] or t.from_].get(g.rpartition(".")[2]) for g in t.grain}
    times = [g for g, kind in kinds.items() if kind in ("date", "timestamp")]
    label = PERIOD_LABEL[unit]
    if not times:
        return [
            f"Yêu cầu muốn xem theo {label} nhưng grain {t.grain} không có cột thời gian (date/timestamp); "
            f'thêm cột thời gian vào grain và đặt transform.time_grain = {{"column": "<cột đó>", "unit": "{unit}"}}'
        ]
    if t.time_grain:
        if t.time_grain.unit == unit:
            return []
        return [
            f"Yêu cầu muốn xem theo {label} nhưng transform.time_grain.unit là '{t.time_grain.unit}'; đổi thành '{unit}'"
        ]
    if unit == "day" and all(kinds[g] == "date" for g in times):
        return []
    return [
        f"Yêu cầu muốn xem theo {label} nhưng grain {times} đang gộp theo từng thời điểm chính xác "
        f'(mỗi giao dịch một dòng); đặt transform.time_grain = {{"column": "{times[0]}", "unit": "{unit}"}}'
    ]


@dataclass
class Validation:
    spec: Spec | None
    gates: list[GateResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # quay lại codegen
    blocked: bool = False  # ast_guard: dừng, không thử lại


def _format(error: ValidationError) -> list[str]:
    """Lỗi kèm giá trị đang sai, để LLM biết sửa chỗ nào (SPEC §14.3: thông báo lỗi cụ thể)."""
    messages = []
    for e in error.errors():
        where = ".".join(map(str, e["loc"])) or "<gốc>"
        value = e.get("input")
        shown = f" (giá trị hiện tại: {value!r})" if not isinstance(value, dict | list) else ""
        messages.append(f"{where}: {e['msg']}{shown}")
    return messages


def _join_coverage(raw: object) -> list[str]:
    """Luật ghép nguồn của Spec chỉ chạy khi mọi trường đã hợp lệ; trên Spec còn lỗi trường thì kiểm tra thô ở đây để
    báo cùng một lượt, LLM không mất một lần sửa chỉ để thấy lỗi tiếp theo."""
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("sources"), list)
        or not isinstance(raw.get("transform"), dict)
    ):
        return []
    ids = {s.get("id") for s in raw["sources"] if isinstance(s, dict)}
    joins = raw["transform"].get("joins") or []
    joined = [j.get("source") for j in joins if isinstance(j, dict)]
    expected = ids - {raw["transform"].get("from")}
    if sorted(map(str, joined)) == sorted(map(str, expected)):
        return []
    return [
        "<gốc>: mọi nguồn khác transform.from phải xuất hiện đúng một lần trong joins và mọi nguồn trong joins phải "
        f"khai báo đủ ở sources (kèm staging từ schema_snapshot); cần {sorted(map(str, expected))}, đang có {joined}"
    ]


def _object(source) -> str | None:
    """Tên đối tượng nguồn như trong schema_snapshot: bảng (postgres) hoặc endpoint bỏ dấu `/` (rest_api)."""
    if source.kind == "rest_api":
        return source.endpoint.strip("/")
    return getattr(source, "table", None)


def _schema_real(spec: Spec, snapshot: dict) -> list[str]:
    by_table = {(item["source_ref"], item["table"]): item for item in snapshot.values()}
    errors = []
    for source in spec.sources:
        item = by_table.get((source.source_ref, _object(source)))
        if item is None:
            errors.append(
                f"nguồn '{source.id}': ({source.source_ref}, {_object(source) or '?'}) không có trong kế hoạch"
            )
            continue
        columns = item["columns"]
        used = [(f"staging.columns[{c.as_}].from", c.from_) for c in source.staging.columns]
        used += [
            (f"staging.columns[{c.as_}].currency.code_column", c.currency.code_column)
            for c in source.staging.columns
            if c.currency
        ]
        used += [("primary_key", key) for key in source.primary_key]
        if source.cursor_field:
            used.append(("cursor_field", source.cursor_field))
        if source.staging.dedupe:
            used.append(("staging.dedupe.order_by", source.staging.dedupe.order_by))
            used += [("staging.dedupe.keys", key) for key in source.staging.dedupe.keys or []]
        for where, column in used:
            if column not in columns:
                errors.append(
                    f"nguồn '{source.id}', {where}: cột '{column}' không có ở nguồn; các cột thật: {sorted(columns)}"
                )
        for column in source.staging.columns:
            raw_type = columns.get(column.from_)
            if raw_type in TEXT_TYPES and column.type in ("date", "timestamp") and not column.date_formats:
                errors.append(
                    f"nguồn '{source.id}', cột '{column.as_}': cột thô '{column.from_}' là {raw_type}, "
                    "ép sang date/timestamp bắt buộc có date_formats suy ra từ dòng mẫu"
                )
    outputs = {source.id: source.outputs for source in spec.sources}
    for join in spec.transform.joins:
        for left, right in join.on:
            kinds = {outputs[ref.split(".")[0]][ref.split(".")[1]] for ref in (left, right)}
            if len(kinds) > 1:
                errors.append(f"join '{join.source}': {left} và {right} khác kiểu sau staging ({sorted(kinds)})")
    return errors


def _used_columns(spec: Spec, snapshot: dict) -> dict[str, dict[str, str]]:
    """Mốc schema của pipeline: với mỗi nguồn, các cột Spec đọc và kiểu của chúng lúc kiểm tra.

    Dùng để phát hiện schema nguồn đổi về sau (cột bị xóa / đổi tên / đổi kiểu), xem src/run_report/drift.py.
    """
    by_table = {(item["source_ref"], item["table"]): item["columns"] for item in snapshot.values()}
    result = {}
    for source in spec.sources:
        columns = by_table.get((source.source_ref, _object(source)), {})
        used = {c.from_ for c in source.staging.columns} | set(source.primary_key)
        if source.cursor_field:
            used.add(source.cursor_field)
        result[source.id] = {name: columns[name] for name in sorted(used) if name in columns}
    return result


def validate_spec(raw: dict, snapshot: dict, policy: Policy, request: str | None = None) -> Validation:
    try:
        spec = Spec.model_validate(raw)
    except ValidationError as exc:
        errors = _format(exc) + _join_coverage(raw)
        return Validation(None, [GateResult(name="spec_schema", result="fail", detail="; ".join(errors))], errors)
    errors = policy_violations(spec, policy)
    gates = [
        GateResult(name="spec_schema", result="fail" if errors else "pass", detail="; ".join(errors) or "Spec hợp lệ")
    ]
    if errors:
        return Validation(spec, gates, errors)
    errors = _schema_real(spec, snapshot)
    gates.append(
        GateResult(
            name="schema_real",
            result="fail" if errors else "pass",
            detail="; ".join(errors) or "Mọi cột Spec dùng có ở nguồn",
            observed={"sources": len(spec.sources), "columns": _used_columns(spec, snapshot)},
        )
    )
    if errors:
        return Validation(spec, gates, errors)
    if unit := requested_period(request):
        errors = _time_grain(spec, unit)
        gates.append(
            GateResult(
                name="time_grain",
                result="fail" if errors else "pass",
                detail="; ".join(errors) or f"Kết quả gộp theo {PERIOD_LABEL[unit]} đúng như yêu cầu",
                observed={
                    "requested": unit,
                    "time_grain": spec.transform.time_grain and spec.transform.time_grain.unit,
                },
            )
        )
        if errors:
            return Validation(spec, gates, errors)
    violations = expression_violations(spec)
    gates.append(
        GateResult(
            name="ast_guard",
            result="fail" if violations else "pass",
            detail="; ".join(violations) or "Biểu thức chỉ dùng cột và hàm được phép",
        )
    )
    return Validation(spec, gates, violations, blocked=bool(violations))
