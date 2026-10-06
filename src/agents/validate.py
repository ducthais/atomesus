"""Kiểm tra Spec do LLM sinh trước khi render (phần tất định của P7, SPEC §10).

- `spec_schema`: đúng hợp đồng `Spec` và chính sách `policy/*.yml`.
- `schema_real`: mọi cột Spec dùng có thật trong schema đã đọc ở P3; cột chuỗi ép sang ngày phải có
  `date_formats`; khóa join cùng kiểu sau staging.
- `currency`: nguồn có cột mã tiền tệ (thấy trong dòng mẫu) thì cột tiền phải khai báo `currency` để quy đổi về tiền
  gốc của domain; quên khai báo thì 1,50 USD bị cộng như 1,50 VND.
- `join_fanout`: ghép sang bảng phía "nhiều" (khóa ghép không phải khóa chính) làm lặp dòng của các bảng đã ghép
  trước đó; SUM/AVG/COUNT không DISTINCT trên cột của bảng bị lặp sẽ cộng trùng. Đối chiếu độc lập dựng lại cùng
  phép ghép nên không bắt được lỗi này, phải chặn ở đây.
- `time_grain`: yêu cầu nói "theo ngày / tuần / tháng / quý / năm" thì cột thời gian của grain phải gộp đúng kỳ đó,
  không gộp theo từng thời điểm chính xác (mỗi giao dịch một dòng). Chỉ chạy khi yêu cầu có nói kỳ.
- `ast_guard` (tạm): biểu thức không chứa lệnh/hàm nguy hiểm, xem `src/codegen/guard.py`.

Lỗi `spec_schema`/`schema_real`/`currency`/`join_fanout`/`time_grain` quay lại codegen kèm thông báo cụ thể
(vòng spec); lỗi `ast_guard` dừng hẳn.
"""

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from src.codegen import preaggregate
from src.codegen.guard import expression_violations
from src.policy.models import Policy
from src.run_report.models import GateResult
from src.spec.models import Spec
from src.spec.policy_check import policy_violations

TEXT_TYPES = {"text", "character varying", "varchar", "character", "char"}
NUMERIC_TYPES = ("bigint", "integer", "double", "decimal")
# Tên cột tiền (nguồn hoặc staging). Chỉ dùng khi nguồn có cột mã tiền tệ, nên khớp rộng cũng ít báo nhầm.
MONEY = re.compile(r"amount|price|total|revenue|cost|fee|paid|money|tien|gia|vnd|usd", re.IGNORECASE)
_AGGREGATE = re.compile(r"\b(sum|avg|count)\s*\(\s*(distinct\s+)?([^()]*)\)", re.IGNORECASE)
_REF = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", re.IGNORECASE)
_NAME = re.compile(r"\b[a-z_][a-z0-9_]*\b", re.IGNORECASE)

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


def currency_columns(item: dict, codes: set[str]) -> dict[str, list[str]]:
    """Cột thô mà mọi giá trị khác rỗng trong dòng mẫu là mã tiền tệ có trong bảng tỷ giá → các mã đã thấy."""
    rows = item.get("sample_rows") or []
    found = {}
    for name in item["columns"]:
        values = {str(row[name]).strip().upper() for row in rows if row.get(name) not in (None, "")}
        if values and values <= codes:
            found[name] = sorted(values)
    return found


def _currency(spec: Spec, snapshot: dict, policy: Policy) -> list[str]:
    domain = policy.domains.domains[spec.domain].currency
    codes = {code.upper() for code in domain.rates}
    by_table = {(item["source_ref"], item["table"]): item for item in snapshot.values()}
    errors = []
    for source in spec.sources:
        item = by_table.get((source.source_ref, _object(source)))
        code_columns = currency_columns(item, codes) if item else {}
        if not code_columns:
            continue
        code, seen = next(iter(code_columns.items()))
        for column in source.staging.columns:
            money = MONEY.search(column.from_) or MONEY.search(column.as_)
            if column.currency or column.from_ in code_columns or not column.type.startswith(NUMERIC_TYPES):
                continue
            if money:
                errors.append(
                    f"nguồn '{source.id}', cột '{column.as_}': nguồn có cột mã tiền tệ '{code}' (dòng mẫu: {seen}) "
                    f"nhưng cột tiền '{column.from_}' chưa khai báo currency; thêm "
                    f'"currency": {{"code_column": "{code}", "to": "{domain.base}"}} để quy đổi theo tỷ giá domains.yml'
                )
    return errors


_unique_on = preaggregate.unique_keys


def _multiplied(spec: Spec) -> dict[str, str]:
    """Nguồn có dòng bị lặp sau các phép ghép → lý do. Ghép theo cột không chứa trọn khóa là ghép sang phía "nhiều",
    trừ khi bảng đó được gộp trước theo khóa ghép (src/codegen/preaggregate.py): khi đó mỗi khóa còn một dòng."""
    sources = {s.id: s for s in spec.sources}
    preaggregated = preaggregate.plan(spec).preaggregated
    joined, multiplied = [spec.transform.from_], {}
    for join in spec.transform.joins:
        new, before = join.source, set(multiplied)  # bảng đã bị lặp trước phép ghép này
        sides: dict[str, set[str]] = {}
        for pair in join.on:
            for ref in pair:
                owner, _, column = ref.partition(".")
                sides.setdefault(owner, set()).add(column)
        keys = _unique_on(sources[new])
        if new not in preaggregated and (not keys or not keys <= sides.get(new, set())):
            reason = (
                f"mỗi dòng đã ghép khớp nhiều dòng '{new}' (ghép theo {sorted(sides.get(new, set()))}, "
                f"khóa của '{new}' là {sorted(keys)})"
            )
            for done in joined:
                multiplied.setdefault(done, reason)
        for owner, columns in sides.items():
            if owner == new or owner not in sources:
                continue
            owner_keys = _unique_on(sources[owner])
            if owner in before or not owner_keys or not owner_keys <= columns:
                multiplied.setdefault(new, f"mỗi dòng '{new}' khớp nhiều dòng '{owner}' đã ghép trước đó")
        joined.append(new)
    return multiplied


def _join_fanout(spec: Spec) -> list[str]:
    multiplied = _multiplied(spec)
    if not multiplied:
        return []
    outputs = {s.id: s.outputs for s in spec.sources}
    errors = []
    for metric in spec.transform.metrics:
        for function, distinct, inner in _AGGREGATE.findall(metric.expr):
            if distinct:
                continue
            call = f"{function.upper()}({inner.strip()})"
            if inner.strip() == "*" and function.lower() == "count":
                errors.append(
                    f"metrics '{metric.name}': {call} đếm số dòng sau khi ghép, mà {next(iter(multiplied.values()))}; "
                    "đếm thực thể thì dùng COUNT(DISTINCT <id>.<khóa chính>)"
                )
                continue
            refs = {owner for owner, _ in _REF.findall(inner)}
            if not refs:  # tên cột không kèm <id>: nguồn nào có cột đầu ra đó
                names = set(_NAME.findall(inner))
                refs = {sid for sid, cols in outputs.items() if names & set(cols)}
            for owner in sorted(refs & multiplied.keys()):
                errors.append(
                    f"metrics '{metric.name}': {call} cộng trùng dòng của '{owner}' vì {multiplied[owner]}. "
                    f"Đếm '{owner}' thì dùng COUNT(DISTINCT {owner}.<khóa chính>); MIN/MAX không bị ảnh hưởng. "
                    "Bảng phía nhiều chỉ được gộp trước tự động khi cột của nó chỉ nằm trong hàm gộp của metrics "
                    "(không ở grain, không lẫn bộ lọc với bảng khác, không có bảng khác ghép qua nó); hoặc đặt "
                    "transform.from là bảng phía một. Không được bỏ chỉ số người dùng yêu cầu"
                )
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
    errors = _currency(spec, snapshot, policy)
    gates.append(
        GateResult(
            name="currency",
            result="fail" if errors else "pass",
            detail="; ".join(errors) or "Cột tiền có mã tiền tệ ở nguồn đều được quy đổi về tiền gốc của domain",
        )
    )
    fanout = _join_fanout(spec)
    gates.append(
        GateResult(
            name="join_fanout",
            result="fail" if fanout else "pass",
            detail="; ".join(fanout) or "Không chỉ số nào cộng trùng dòng do ghép bảng",
            observed={"multiplied": _multiplied(spec), "preaggregated": sorted(preaggregate.plan(spec).preaggregated)},
        )
    )
    errors += fanout
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
