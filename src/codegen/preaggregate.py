"""Gộp trước bảng phía "nhiều" để ghép một-nhiều không cộng trùng (dùng chung cho model gold và đối chiếu độc lập).

Ghép `orders` với `payments` theo `order_id` làm mỗi đơn lặp lại theo số khoản thanh toán, nên `SUM(orders.amount)`
cộng trùng. Khi cột của bảng phía nhiều N chỉ xuất hiện bên trong hàm gộp của `metrics`, có thể thay N bằng một truy
vấn con đã gộp theo khóa ghép (một dòng mỗi khóa), giữ nguyên bí danh N nên điều kiện ghép không đổi:

    LEFT JOIN (SELECT payments.order_id, SUM(payments.amount_vnd) AS __m1
               FROM stg_payments AS payments GROUP BY payments.order_id) AS payments ON ...

Viết lại từng lời gọi hàm gộp trên N: SUM → SUM(N.__mK); COUNT → COALESCE(SUM(N.__mK), 0); MIN/MAX → MIN/MAX;
AVG → SUM(tổng) / NULLIF(SUM(đếm), 0); COUNT(DISTINCT N.<khóa duy nhất của N>) → COALESCE(SUM(N.__mK), 0).

Không gộp trước (cổng `join_fanout` chặn như cũ) khi: cột của N có trong grain, trong bộ lọc lẫn với bảng khác, ngoài hàm
gộp, khi có COUNT(*) ở bất kỳ chỉ số nào, trong COUNT(DISTINCT) cột không phải khóa, trong biểu thức gộp lẫn bảng khác, hoặc có bảng khác
ghép qua N.
"""

import re
from dataclasses import dataclass, field

from src.spec.models import Spec

AGGREGATE = re.compile(r"\b(sum|avg|count|min|max)\s*\(\s*(distinct\s+)?([^()]*)\)", re.IGNORECASE)
REF = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", re.IGNORECASE)


@dataclass
class PreAggregate:
    source: str
    keys: list[str]  # cột của N trong điều kiện ghép, dạng N.<cột>
    filters: list[str] = field(default_factory=list)  # bộ lọc chỉ dùng cột của N: chạy trong truy vấn con
    measures: list[tuple[str, str]] = field(default_factory=list)  # (bí danh, biểu thức gộp trong truy vấn con)


@dataclass
class GoldPlan:
    metrics: dict[str, str]  # tên chỉ số → biểu thức ở truy vấn ngoài
    filters: list[str]  # bộ lọc còn lại ở truy vấn ngoài
    preaggregated: dict[str, PreAggregate]


def unique_keys(source) -> set[str]:
    """Cột đầu ra staging bảo đảm duy nhất mỗi dòng: khóa dedupe (mặc định là primary_key), theo tên sau staging."""
    keys = (source.staging.dedupe.keys if source.staging.dedupe else None) or source.primary_key
    renamed = {c.from_: c.as_ for c in source.staging.columns}
    return {renamed.get(key, key) for key in keys}


def _owners(text: str) -> set[str]:
    return {owner for owner, _ in REF.findall(text)}


def _join_columns(join) -> dict[str, set[str]]:
    sides: dict[str, set[str]] = {}
    for pair in join.on:
        for ref in pair:
            owner, _, column = ref.partition(".")
            sides.setdefault(owner, set()).add(column)
    return sides


def _rewrite(expr: str, sid: str, keys: set[str], measures: list[tuple[str, str]]) -> str | None:
    """Viết lại các lời gọi hàm gộp trên `sid` trong một biểu thức chỉ số; None nếu không viết lại được."""
    out, last = [], 0
    for match in AGGREGATE.finditer(expr):
        function, distinct, inner = match.group(1).upper(), bool(match.group(2)), match.group(3).strip()
        owners = _owners(inner)
        if sid not in owners:
            continue
        if owners != {sid} or inner == "*":
            return None
        if distinct and (function != "COUNT" or REF.fullmatch(inner) is None or inner.partition(".")[2] not in keys):
            return None

        def measure(text: str) -> str:
            alias = f"__m{len(measures) + 1}"
            measures.append((alias, text))
            return f"{sid}.{alias}"

        if function == "SUM":
            outer = f"SUM({measure(f'SUM({inner})')})"
        elif function == "COUNT":
            counted = f"COUNT(DISTINCT {inner})" if distinct else f"COUNT({inner})"
            outer = f"COALESCE(SUM({measure(counted)}), 0)"
        elif function in ("MIN", "MAX"):
            outer = f"{function}({measure(f'{function}({inner})')})"
        else:  # AVG
            total, count = measure(f"SUM({inner})"), measure(f"COUNT({inner})")
            outer = f"(SUM({total}) / NULLIF(SUM({count}), 0))"
        out += [expr[last : match.start()], outer]
        last = match.end()
    out.append(expr[last:])
    rewritten = "".join(out)
    leftover = AGGREGATE.sub("", rewritten.replace(f"{sid}.__m", "__m"))
    return None if sid in _owners(leftover) else rewritten


def plan(spec: Spec) -> GoldPlan:
    t = spec.transform
    sources = {s.id: s for s in spec.sources}
    metrics = {m.name: m.expr for m in t.metrics}
    filters = list(t.filters)
    preaggregated: dict[str, PreAggregate] = {}
    if any(re.search(r"\bcount\s*\(\s*\*\s*\)", expr, re.IGNORECASE) for expr in metrics.values()):
        # COUNT(*) đếm dòng sau khi ghép: gộp trước sẽ lặng lẽ đổi nó từ "số khoản" thành "số đơn". Để cổng
        # join_fanout yêu cầu viết rõ COUNT(DISTINCT <id>.<khóa>).
        return GoldPlan(metrics=metrics, filters=filters, preaggregated={})
    for index, join in enumerate(t.joins):
        sid = join.source
        sides = _join_columns(join)
        keys = unique_keys(sources[sid])
        if keys and keys <= sides.get(sid, set()):
            continue  # N đã duy nhất trên khóa ghép: không lặp dòng
        others = [j for i, j in enumerate(t.joins) if i != index]
        if any(sid in _owners(" ".join(ref for pair in j.on for ref in pair)) for j in others):
            continue  # có bảng khác ghép qua N
        if any(sid in _owners(g) for g in t.grain):
            continue
        own_filters = [f for f in filters if _owners(f) == {sid}]
        if any(sid in _owners(f) and _owners(f) != {sid} for f in filters):
            continue
        measures: list[tuple[str, str]] = []
        rewritten = {name: _rewrite(expr, sid, keys, measures) for name, expr in metrics.items()}
        if any(value is None for value in rewritten.values()):
            continue  # không chỉ số nào dùng N thì vẫn gộp: truy vấn con chỉ còn khóa ghép, mỗi khóa một dòng
        metrics = rewritten
        filters = [f for f in filters if f not in own_filters]
        preaggregated[sid] = PreAggregate(
            source=sid,
            keys=[f"{sid}.{column}" for column in sorted(sides.get(sid, set()))],
            filters=own_filters,
            measures=measures,
        )
    return GoldPlan(metrics=metrics, filters=filters, preaggregated=preaggregated)


def subquery(pre: PreAggregate, relation: str) -> str:
    """Truy vấn con gộp N theo khóa ghép; `relation` là bảng staging của N (ref dbt hoặc CTE đối chiếu)."""
    select = ", ".join([*pre.keys, *(f"{expr} AS {alias}" for alias, expr in pre.measures)])
    where = " WHERE " + " AND ".join(f"({f})" for f in pre.filters) if pre.filters else ""
    return f"(SELECT {select} FROM {relation} AS {pre.source}{where} GROUP BY {', '.join(pre.keys)})"
