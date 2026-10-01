"""Kiểm tra Spec do LLM sinh trước khi render (phần tất định của P7, SPEC §10).

- `spec_schema`: đúng hợp đồng `Spec` và chính sách `policy/*.yml`.
- `schema_real`: mọi cột Spec dùng có thật trong schema đã đọc ở P3; cột chuỗi ép sang ngày phải có
  `date_formats`; khóa join cùng kiểu sau staging.
- `ast_guard` (tạm): biểu thức không chứa lệnh/hàm nguy hiểm, xem `src/codegen/guard.py`.

Lỗi `spec_schema`/`schema_real` quay lại codegen kèm thông báo cụ thể (vòng spec); lỗi `ast_guard` dừng hẳn.
"""

from dataclasses import dataclass, field

from pydantic import ValidationError

from src.codegen.guard import expression_violations
from src.policy.models import Policy
from src.run_report.models import GateResult
from src.spec.models import Spec
from src.spec.policy_check import policy_violations

TEXT_TYPES = {"text", "character varying", "varchar", "character", "char"}


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


def _schema_real(spec: Spec, snapshot: dict) -> list[str]:
    by_table = {(item["source_ref"], item["table"]): item for item in snapshot.values()}
    errors = []
    for source in spec.sources:
        item = by_table.get((source.source_ref, getattr(source, "table", None)))
        if item is None:
            errors.append(
                f"nguồn '{source.id}': ({source.source_ref}, {getattr(source, 'table', '?')}) không có trong kế hoạch"
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


def validate_spec(raw: dict, snapshot: dict, policy: Policy) -> Validation:
    try:
        spec = Spec.model_validate(raw)
    except ValidationError as exc:
        errors = _format(exc)
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
            observed={"sources": len(spec.sources)},
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
