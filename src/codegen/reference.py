"""Đối chiếu độc lập (`metric_reconcile`): tính lại bảng gold thẳng trên nguồn Postgres rồi so từng dòng.

Pipeline đi đường dlt → DuckDB (bronze) → dbt (staging, gold) theo template. Ở đây Spec được dịch sang **một câu
SQL Postgres** bằng code riêng, không dùng template, dlt, DuckDB hay dbt, và chạy trên chính bảng nguồn bằng tài
khoản chỉ đọc. Hai đường tính cùng một Spec mà ra cùng con số thì con số đó không phải do một bước nào đó làm lệch
(ép kiểu, múi giờ, mất dòng khi tải, loại trùng...).

Giới hạn: đối chiếu chỉ kiểm tra pipeline **thực hiện đúng Spec**. Spec hiểu sai yêu cầu thì cả hai đường cùng sai và
vẫn khớp. Spec có đúng nhu cầu nghiệp vụ hay không do Engineer xác nhận (bản nháp / tab Mapping) và Reviewer duyệt.

Quy tắc làm sạch phải giống staging (templates/dbt_staging.sql.j2): ép kiểu lỗi hoặc thỏa `reject_if` thì loại,
rồi khử trùng theo khóa. Thời gian có múi giờ quy về UTC.

Nguồn không phải bảng Postgres (REST API, file CSV) được đọc độc lập lần nữa (không qua dlt / DuckDB) rồi nhúng vào
câu SQL dưới dạng `VALUES`, mỗi giá trị ép đúng kiểu đọc từ OpenAPI. Nhờ vậy cùng một đường làm sạch / gộp chạy được cho
pipeline ghép Postgres với API. Tối đa `MAX_INLINE_ROWS` bản ghi mỗi nguồn.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from src.codegen import preaggregate
from src.policy.models import Policy
from src.spec.models import Spec

MAX_GROUPS = 100_000
MAX_INLINE_ROWS = 50_000
MAX_SHOWN = 10
# Hàm chỉ DuckDB có (hoặc khác nghĩa ở Postgres): gặp thì không đối chiếu, nói rõ lý do.
DUCKDB_ONLY = ("try_cast", "median", "any_value")
TEXT = ("text", "character varying", "character", "varchar", "char")
NUMERIC = ("integer", "smallint", "bigint", "numeric", "real", "double precision", "decimal")
PG_TYPE = {"bigint": "bigint", "integer": "integer", "double": "double precision", "varchar": "text",
           "boolean": "boolean", "date": "date", "timestamp": "timestamp"}  # fmt: skip
INTEGER_TEXT = r"^\s*[+-]?\d+\s*$"
NUMBER_TEXT = r"^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*$"
ISO_DATETIME = "%Y-%m-%d %H:%M:%S"
DIRECTIVES = {"Y": (r"(\d{4})", "y"), "m": (r"(\d{1,2})", "m"), "d": (r"(\d{1,2})", "d"),
              "H": (r"(\d{1,2})", "H"), "M": (r"(\d{1,2})", "M"), "S": (r"(\d{1,2})", "S")}  # fmt: skip


class UnsupportedError(Exception):
    """Spec dùng thứ mà bản đối chiếu này chưa dịch được sang Postgres (nói rõ lý do, không đoán)."""


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _pg_type(kind: str) -> str:
    if kind.startswith("decimal"):
        return "numeric" + kind[len("decimal") :]
    return PG_TYPE[kind]


def _parse_formats(raw: str, formats: list[str], kind: str) -> str:
    """try_strptime theo từng định dạng (cái đầu khớp thắng), an toàn với ngày không tồn tại như 31/02."""
    attempts = []
    for fmt in formats:
        pattern, order = "", []
        for literal, directive in re.findall(r"([^%]*)(?:%(.))?", fmt):
            pattern += re.escape(literal)
            if directive:
                if directive not in DIRECTIVES:
                    raise UnsupportedError(f"định dạng ngày %{directive} trong '{fmt}' chưa hỗ trợ đối chiếu")
                group, part = DIRECTIVES[directive]
                pattern, order = pattern + group, [*order, part]
        if not {"y", "m", "d"} <= set(order):
            raise UnsupportedError(f"định dạng ngày '{fmt}' thiếu năm, tháng hoặc ngày")
        m = f"regexp_match({raw}, {_literal('^' + pattern + '$')})"
        part = {name: f"({m})[{order.index(name) + 1}]::int" if name in order else "0" for name in "ymdHMS"}
        last_day = f"extract(day from (make_date({part['y']}, {part['m']}, 1) + interval '1 month - 1 day'))"
        # CASE lồng nhau: make_date chỉ chạy khi tháng hợp lệ, make_timestamp chỉ chạy khi cả ngày giờ hợp lệ.
        attempts.append(
            f"CASE WHEN {m} IS NOT NULL AND {part['m']} BETWEEN 1 AND 12 THEN CASE WHEN {part['d']} BETWEEN 1 AND "
            f"{last_day} AND {part['H']} < 24 AND {part['M']} < 60 AND {part['S']} < 60 THEN make_timestamp("
            f"{part['y']}, {part['m']}, {part['d']}, {part['H']}, {part['M']}, {part['S']}) END END"
        )
    return f"CAST(coalesce({', '.join(attempts)}) AS {_pg_type(kind)})"


def _typed(raw: str, pg_type: str, column, rates: dict[str, float], code: str | None) -> str:
    """Giá trị đã ép kiểu của một cột staging; NULL khi DuckDB try_cast cũng ra NULL (dòng đó bị loại)."""
    kind, target = column.type, _pg_type(column.type)
    textual = pg_type.startswith(TEXT)
    if column.date_formats:
        return _parse_formats(f"btrim({raw}::text)", column.date_formats, kind)
    if kind in ("date", "timestamp"):
        if pg_type.startswith("timestamp with time zone"):
            return f"CAST(({raw} AT TIME ZONE 'UTC') AS {target})"
        if pg_type.startswith(("timestamp without time zone", "date")):
            return f"CAST({raw} AS {target})"
        if textual:
            return _parse_formats(f"btrim({raw})", ["%Y-%m-%d", ISO_DATETIME], kind)
        raise UnsupportedError(f"ép {pg_type} sang {kind}")
    if kind in ("bigint", "integer", "double") or kind.startswith("decimal"):
        if pg_type.startswith(NUMERIC):
            value = f"CAST({raw} AS {target})"
        elif textual:
            guard = INTEGER_TEXT if kind in ("bigint", "integer") else NUMBER_TEXT
            value = f"CASE WHEN {raw} ~ {_literal(guard)} THEN CAST(btrim({raw}) AS {target}) END"
        else:
            raise UnsupportedError(f"ép {pg_type} sang {kind}")
        if code:  # đổi tiền theo tỷ giá policy; mã tiền lạ → NULL → dòng bị loại như ở staging
            cases = " ".join(f"WHEN {_literal(ccy)} THEN {rate}" for ccy, rate in rates.items())
            value = f"CAST({value} * CASE upper({code}::text) {cases} END AS {target})"
        return value
    if kind == "boolean":
        if pg_type == "boolean":
            return raw
        if textual:
            return (
                f"CASE WHEN lower(btrim({raw})) IN ('true', 't', '1', 'yes', 'y') THEN true "
                f"WHEN lower(btrim({raw})) IN ('false', 'f', '0', 'no', 'n') THEN false END"
            )
        raise UnsupportedError(f"ép {pg_type} sang boolean")
    if kind == "varchar":
        if textual or pg_type.startswith(NUMERIC) or pg_type == "boolean":
            return f"CAST({raw} AS text)"
        raise UnsupportedError(f"ép {pg_type} sang varchar (định dạng chuỗi có thể khác DuckDB)")
    raise UnsupportedError(f"kiểu {kind}")


@dataclass
class Inline:
    """Bản ghi của một nguồn không phải bảng Postgres, đọc độc lập, kèm kiểu Postgres của từng trường."""

    types: dict[str, str]
    rows: list[dict]


def _inline_relation(inline: Inline) -> str:
    """`(VALUES ...) AS src(<cột>..., _ord)`: mỗi giá trị ép kiểu tường minh; `_ord` giữ thứ tự đọc (thay cho ctid)."""
    if not inline.rows:
        raise UnsupportedError("nguồn không có bản ghi nào để tính lại")
    if len(inline.rows) > MAX_INLINE_ROWS:
        raise UnsupportedError(f"nguồn hơn {MAX_INLINE_ROWS:,} bản ghi, chỉ đối chiếu nguồn nhỏ hơn")
    names = list(inline.types)

    def value(row: dict, name: str) -> str:
        raw = row.get(name)
        text = "NULL" if raw is None else _literal(str(raw).lower() if isinstance(raw, bool) else str(raw))
        return f"CAST({text} AS {inline.types[name]})"

    rows = ", ".join(
        "(" + ", ".join([*(value(row, n) for n in names), str(i)]) + ")" for i, row in enumerate(inline.rows)
    )
    return f"(VALUES {rows}) AS src({', '.join([*map(_ident, names), '_ord'])})"


def _staging_cte(source, relation: str, order: str, pg_types: dict[str, str], rates: dict[str, float]) -> str:
    """`relation` là `<schema>.<bảng> AS src` hoặc `(VALUES ...) AS src(...)`; `order` là cột giữ thứ tự đọc."""
    columns = source.staging.columns
    typed, raws = [], []
    for i, column in enumerate(columns):
        if column.from_ not in pg_types:
            raise UnsupportedError(f"cột {source.id}.{column.from_} không còn ở nguồn")
        raw = f"src.{_ident(column.from_)}"
        code = f"src.{_ident(column.currency.code_column)}" if column.currency else None
        typed.append(f"{_typed(raw, pg_types[column.from_], column, rates, code)} AS {_ident(column.as_)}")
        raws.append(f"{raw} AS _raw_{i}")
    dedupe = source.staging.dedupe
    keys = dedupe.keys if dedupe and dedupe.keys else source.primary_key
    extra = [f"src.{_ident(k)} AS _key_{i}" for i, k in enumerate(keys)]
    if dedupe:
        extra.append(f"src.{_ident(dedupe.order_by)} AS _order")
    broken = [f"(_raw_{i} IS NOT NULL AND {_ident(c.as_)} IS NULL)" for i, c in enumerate(columns)]
    rejected = broken + [f"({condition}) IS TRUE" for condition in source.staging.reject_if]
    clean = (
        f"SELECT * FROM (SELECT {', '.join(typed + raws + extra)}, src.{order} AS _ctid "
        f"FROM {relation}) AS typed WHERE NOT ({' OR '.join(rejected)})"
    )
    output = ", ".join(_ident(c.as_) for c in columns)
    if not keys:
        return f"SELECT {output} FROM ({clean}) AS clean"
    direction = "DESC" if dedupe and dedupe.keep == "latest" else "ASC"
    order = (f"_order {direction} NULLS LAST, " if dedupe else "") + "_ctid"
    partition = ", ".join(f"_key_{i}" for i in range(len(keys)))
    ranked = f"SELECT *, row_number() OVER (PARTITION BY {partition} ORDER BY {order}) AS _rank FROM ({clean}) AS clean"
    return f"SELECT {output} FROM ({ranked}) AS ranked WHERE _rank = 1"


def reference_sql(
    spec: Spec,
    policy: Policy,
    columns_of: Callable[[str], dict[str, dict[str, str]]],
    inline: dict[str, Inline] | None = None,
) -> str:
    """Spec → một câu SQL Postgres tính lại bảng gold. `columns_of(schema)` → {bảng: {cột: kiểu Postgres}};
    `inline` → bản ghi đọc độc lập của các nguồn không phải bảng Postgres, theo `sources[].id`."""
    inline = inline or {}
    t = spec.transform
    expressions = [m.expr for m in t.metrics] + t.filters + [r for s in spec.sources for r in s.staging.reject_if]
    for expression in expressions:
        for name in DUCKDB_ONLY:
            if re.search(rf"\b{name}\s*\(", expression, re.IGNORECASE):
                raise UnsupportedError(f"hàm {name}() chỉ có ở DuckDB")
    domain = policy.domains.domains[spec.domain]
    ctes = []
    for source in spec.sources:
        if source.id in inline:
            data = inline[source.id]
            cte = _staging_cte(source, _inline_relation(data), "_ord", data.types, domain.currency.rates)
            ctes.append(f"{_ident('stg_' + source.id)} AS ({cte})")
            continue
        if source.kind != "postgres":
            raise UnsupportedError(f"nguồn loại {source.kind}")
        schema = domain.sources[source.source_ref].schema_
        tables = columns_of(schema)
        if source.table not in tables:
            raise UnsupportedError(f"bảng {schema}.{source.table} không còn ở nguồn")
        relation = f"{_ident(schema)}.{_ident(source.table)} AS src"
        cte = _staging_cte(source, relation, "ctid", tables[source.table], domain.currency.rates)
        ctes.append(f"{_ident('stg_' + source.id)} AS ({cte})")
    period = t.time_grain
    grains = [
        (f"CAST(date_trunc({_literal(period.unit)}, {g}) AS date)" if period and period.column == g else g)
        + f" AS {_ident(g.rpartition('.')[2])}"
        for g in t.grain
    ]
    gold = preaggregate.plan(spec)  # gộp trước bảng phía "nhiều" giống model gold, để không cộng trùng
    metrics = [f"{gold.metrics[m.name]} AS {_ident(m.name)}" for m in t.metrics]
    joins = "".join(
        f" {j.type.upper()} JOIN "
        + (
            preaggregate.subquery(gold.preaggregated[j.source], _ident("stg_" + j.source))
            if j.source in gold.preaggregated
            else _ident("stg_" + j.source)
        )
        + f" AS {_ident(j.source)} ON "
        + " AND ".join(f"{left} = {right}" for left, right in j.on)
        for j in t.joins
    )
    where = "".join(f" AND ({condition})" for condition in gold.filters)
    group = ", ".join(str(i + 1) for i in range(len(grains)))
    return (
        f"WITH {', '.join(ctes)}\nSELECT {', '.join(grains + metrics)}\n"
        f"FROM {_ident('stg_' + t.from_)} AS {_ident(t.from_)}{joins}\nWHERE true{where}\nGROUP BY {group}"
    )


# ---------- so sánh ----------


def _key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float | Decimal) and value == int(value):
        return str(int(value))
    return str(value)


def _text(value: Any) -> str | None:
    """Giá trị để hiện cho người đọc (giữ nguyên số lẻ như 1000.00)."""
    if isinstance(value, date | datetime):
        return _key(value)
    return None if value is None else str(value)


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _same(a: Any, b: Any) -> bool:
    x, y = _number(a), _number(b)
    if x is None or y is None:
        return (a is None) == (b is None) and (x is not None or str(a) == str(b))
    # Khớp tới từng xu; số thực (AVG, double) cho phép sai số làm tròn rất nhỏ.
    return abs(x - y) <= Decimal("0.005") + Decimal("1e-9") * max(abs(x), abs(y))


def compare(spec: Spec, gold: list[dict], source: list[dict]) -> dict:
    """So từng dòng gold với dòng tính lại từ nguồn theo khóa grain; trả số liệu cho check `metric_reconcile`."""
    grain = [g.rpartition(".")[2] for g in spec.transform.grain]
    metrics = [m.name for m in spec.transform.metrics]

    def index(rows: list[dict]) -> dict[tuple, dict]:
        return {tuple(_key(row[g]) for g in grain): row for row in rows}

    ours, theirs = index(gold), index(source)
    show = lambda key: dict(zip(grain, key, strict=True))  # noqa: E731
    mismatched = []
    for key in sorted(ours.keys() & theirs.keys(), key=str):
        for metric in metrics:
            if not _same(ours[key][metric], theirs[key][metric]):
                mismatched.append(
                    {
                        "key": show(key),
                        "metric": metric,
                        "gold": _text(ours[key][metric]),
                        "source": _text(theirs[key][metric]),
                    }
                )
    missing = sorted(theirs.keys() - ours.keys(), key=str)
    extra = sorted(ours.keys() - theirs.keys(), key=str)
    totals = {}
    for metric in metrics:
        sums = [sum((_number(r[metric]) or 0) for r in rows) for rows in (gold, source)]
        totals[metric] = {"gold": str(sums[0]), "source": str(sums[1])}
    return {
        "rows": {"gold": len(gold), "source": len(source)},
        "matched": len(ours.keys() & theirs.keys()) - len({tuple(m["key"].values()) for m in mismatched}),
        "mismatch_count": len(mismatched),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "mismatched": mismatched[:MAX_SHOWN],
        "missing_in_gold": [show(k) for k in missing[:MAX_SHOWN]],
        "extra_in_gold": [show(k) for k in extra[:MAX_SHOWN]],
        "totals": totals,
        "shift_hours": _shift_hours(missing, extra, ours.keys()),
    }


def _shift_hours(missing: list[tuple], extra: list[tuple], gold_keys) -> float | None:
    """Các dòng thiếu là cùng thời điểm ở nguồn bị dịch đúng một khoảng trong gold (dấu hiệu lệch múi giờ) → số giờ.

    Lấy khoảng dịch hay gặp nhất giữa các dòng thiếu và thừa, rồi xác nhận ≥ 95% dòng thiếu có bản dịch trong gold.
    """
    if not missing or not extra or len(missing[0]) != 1:
        return None
    try:
        parse = datetime.fromisoformat
        offsets = Counter(
            parse(e[0]) - parse(m[0]) for m, e in zip(sorted(missing)[:200], sorted(extra)[:200], strict=False)
        )
        offset = offsets.most_common(1)[0][0]
        shifted = sum(1 for (m,) in missing if (_key(parse(m) + offset),) in gold_keys)
    except (TypeError, ValueError):
        return None
    if not offset or shifted < 0.95 * len(missing):
        return None
    return offset.total_seconds() / 3600


def is_match(observed: dict) -> bool:
    return not (observed["mismatch_count"] or observed["missing_count"] or observed["extra_count"])


# ---------- chạy ----------


def source_rows(spec: Spec, policy: Policy, settings) -> tuple[str, list[dict]]:
    """Chạy SQL tính lại trên Postgres nguồn (tài khoản chỉ đọc). Lỗi SQL → UnsupportedError kèm thông báo của Postgres."""
    import psycopg

    from src.codegen.el_checks import source_records
    from src.pipeline import connectors, files, postgres, rest_api

    def columns_of(schema: str) -> dict[str, dict[str, str]]:
        tables = connectors.postgres_columns(settings, schema)
        return {table: {c["name"]: c["type"] for c in columns} for table, columns in tables.items()}

    declared = policy.domains.domains[spec.domain].sources
    inline = {}
    for source in spec.sources:
        if source.kind == "rest_api":
            decl = declared[source.source_ref]
            described = rest_api.endpoints(decl, settings).get(source.endpoint.strip("/"))
            if described is None:
                raise UnsupportedError(f"API không còn endpoint {source.endpoint}")
            rows = list(rest_api.records(decl, settings, described, limit=MAX_INLINE_ROWS + 1))
            inline[source.id] = Inline({c["name"]: c["type"] for c in described["columns"]}, rows)
        elif source.kind == "filesystem":
            decl = declared[source.source_ref]
            folder = source.path_pattern.split("/", 1)[0]
            described = files.objects(decl).get(folder)
            if described is None:
                raise UnsupportedError(f"không còn thư mục {folder}/ ở nguồn file")
            rows = []
            for row in source_records(source, decl, settings):
                rows.append(row)
                if len(rows) > MAX_INLINE_ROWS:
                    break
            inline[source.id] = Inline({c["name"]: c["type"] for c in described["columns"]}, rows)
    query = reference_sql(spec, policy, columns_of, inline)
    if all(source.kind != "postgres" for source in spec.sources):
        # Chỉ dùng Postgres làm máy tính: không mở được thì bỏ qua đối chiếu, không làm hỏng run của nguồn API.
        try:
            with postgres.connection(settings):
                pass
        except postgres.SourceUnavailableError as exc:
            raise UnsupportedError(f"cần Postgres để tính lại nhưng không mở được: {exc}") from exc
    with postgres.connection(settings) as conn:
        try:
            cursor = conn.execute(f"SELECT * FROM ({query}) AS reference LIMIT {MAX_GROUPS + 1}")  # noqa: S608
            names = [column.name for column in cursor.description]
            rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        except psycopg.Error as exc:
            conn.rollback()
            raise UnsupportedError(f"Postgres báo lỗi khi tính lại: {str(exc).splitlines()[0]}") from exc
    if len(rows) > MAX_GROUPS:
        raise UnsupportedError(f"kết quả hơn {MAX_GROUPS:,} dòng, chỉ đối chiếu bảng nhỏ hơn")
    return query, rows


def reconcile(spec: Spec, gold: list[dict], policy: Policy, settings) -> tuple[bool, dict]:
    """Tính lại từ nguồn và so với `gold` (mọi dòng của bảng gold). Trả (khớp?, số liệu); không đối chiếu được → raise."""
    if len(gold) > MAX_GROUPS:
        raise UnsupportedError(f"bảng gold hơn {MAX_GROUPS:,} dòng, chỉ đối chiếu bảng nhỏ hơn")
    query, rows = source_rows(spec, policy, settings)
    observed = {**compare(spec, gold, rows), "engine": "postgres", "sql": query}
    return is_match(observed), observed


def read_gold(db, model: str) -> list[dict]:
    cursor = db.execute(f'select * from gold."{model}" limit {MAX_GROUPS + 1}')  # noqa: S608
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
