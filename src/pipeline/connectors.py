"""Khám phá nguồn: kết nối trực tiếp tới một nguồn khai báo trong domains.yml để xem nó đang có bảng nào.

Mỗi loại nguồn (`kind`) có một connector trong `CONNECTORS`. Connector chỉ đọc metadata (tên bảng, cột,
khóa, số dòng ước tính), không đọc dữ liệu. Thêm loại nguồn mới, ví dụ MongoDB: viết một hàm cùng chữ ký
trả về `objects` (mỗi collection một object, `kind: "collection"`), đăng ký vào `CONNECTORS`, và thêm kind
đó vào `SourceKind` trong `src/policy/models.py`.
"""

from collections.abc import Callable

from src.pipeline import files, postgres, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.models import SourceDecl


class UnsupportedSourceError(Exception):
    """Chưa có connector cho loại nguồn này."""


# Chỉ bảng thật và bảng cha của partition: bỏ view và từng partition con (payment_p2022_01…),
# để catalog và agent thấy cùng một danh sách bảng.
COLUMNS_SQL = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = %s AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY c.relname, a.attnum
"""


def postgres_columns(config: PipelineSettings, schema: str) -> dict[str, list[dict]]:
    """Bảng và cột (tên, kiểu) mà tài khoản reader đọc được trong một schema."""
    with postgres.connection(config) as conn:
        rows = conn.execute(COLUMNS_SQL, (schema,)).fetchall()
    tables: dict[str, list[dict]] = {}
    for table, column, kind in rows:
        tables.setdefault(table, []).append({"name": column, "type": kind})
    return tables


# pg_constraint thay vì information_schema: role chỉ có SELECT không thấy constraint trong information_schema.
# Khóa khai báo trên partition con (Pagila: payment_p2017_01…) được tính cho bảng cha.
KEYS_SQL = """
SELECT c.contype, CASE WHEN src.relispartition THEN parent.relname ELSE src.relname END,
       ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
             JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num ORDER BY k.pos),
       tns.nspname, tgt.relname,
       ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
             JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num ORDER BY k.pos),
       c.conname
FROM pg_constraint c
JOIN pg_class src ON src.oid = c.conrelid
JOIN pg_namespace ns ON ns.oid = src.relnamespace
LEFT JOIN pg_inherits inh ON src.relispartition AND inh.inhrelid = src.oid
LEFT JOIN pg_class parent ON parent.oid = inh.inhparent
LEFT JOIN pg_class tgt ON tgt.oid = c.confrelid
LEFT JOIN pg_namespace tns ON tns.oid = tgt.relnamespace
WHERE ns.nspname = %s AND c.contype IN ('p', 'f')
ORDER BY src.relname, c.conname
"""


def postgres_keys(config: PipelineSettings, schema: str) -> tuple[dict[str, list[str]], list[dict]]:
    """Khóa chính theo bảng và khóa ngoại (bảng, cột → bảng đích, cột đích) của một schema."""
    with postgres.connection(config) as conn:
        rows = conn.execute(KEYS_SQL, (schema,)).fetchall()
    primary: dict[str, list[str]] = {}
    foreign: list[dict] = []
    seen = set()
    for kind, table, columns, ref_schema, ref_table, ref_columns, name in rows:
        if kind == "p":
            primary.setdefault(table, list(columns))
            continue
        key = (table, tuple(columns), ref_schema, ref_table, tuple(ref_columns))
        if key in seen:  # cùng một khóa lặp lại trên mỗi partition con
            continue
        seen.add(key)
        foreign.append(
            {
                "name": name,
                "table": table,
                "columns": list(columns),
                "ref_schema": ref_schema,
                "ref_table": ref_table,
                "ref_columns": list(ref_columns),
            }
        )
    return primary, foreign


ROW_COUNT_CAP = 100_000


def postgres_row_estimates(config: PipelineSettings, schema: str) -> dict[str, int]:
    """Số dòng theo bảng mà không quét bảng lớn.

    Dùng thống kê của Postgres (pg_class.reltuples). Bảng chưa từng ANALYZE (reltuples = -1) thì đếm
    nhưng dừng ở ROW_COUNT_CAP + 1 dòng; giá trị đó nghĩa là "hơn ROW_COUNT_CAP".
    """
    from psycopg import sql

    with postgres.connection(config) as conn:
        rows = conn.execute(
            "SELECT c.relname, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind IN ('r', 'p') AND NOT c.relispartition",
            (schema,),
        ).fetchall()
        counts = {}
        for table, estimate in rows:
            if estimate >= 0:
                counts[table] = estimate
                continue
            query = sql.SQL("SELECT count(*) FROM (SELECT 1 FROM {} LIMIT %s) capped").format(
                sql.Identifier(schema, table)
            )
            counts[table] = conn.execute(query, (ROW_COUNT_CAP + 1,)).fetchone()[0]
    return counts


def schema_visibility(config: PipelineSettings, schema: str) -> tuple[bool, bool]:
    """(schema có trong database, tài khoản đang dùng có quyền USAGE trên schema đó)."""
    with postgres.connection(config) as conn:
        row = conn.execute(
            "SELECT n.oid IS NOT NULL, COALESCE(has_schema_privilege(n.oid, 'USAGE'), false) "
            "FROM (SELECT %s::text AS name) s LEFT JOIN pg_namespace n ON n.nspname = s.name",
            (schema,),
        ).fetchone()
    return bool(row[0]), bool(row[1])


def _empty_reason(config: PipelineSettings, schema: str) -> str:
    """Kết nối được nhưng không thấy bảng nào: nói rõ vì sao và cách sửa, thay vì trả danh sách rỗng."""
    exists, usable = schema_visibility(config, schema)
    fix = (
        " Chạy `python scripts/load_pagila.py` (Postgres tạo từ trước khi repo có Pagila không tự nạp lại)."
        if schema == "pagila"
        else " Kiểm tra dữ liệu seed của nguồn này."
    )
    if not exists:
        return f"Schema '{schema}' chưa có trong Postgres nguồn.{fix}"
    if not usable:
        return f"Tài khoản chỉ đọc chưa được cấp quyền đọc schema '{schema}'.{fix.replace('load_pagila.py', 'load_pagila.py --replace')}"
    return f"Schema '{schema}' có nhưng không có bảng nào tài khoản chỉ đọc đọc được.{fix}"


def _postgres(decl: SourceDecl, config: PipelineSettings) -> dict:
    schema = decl.schema_
    columns = postgres_columns(config, schema)
    if not columns:
        raise postgres.SourceUnavailableError(_empty_reason(config, schema))
    primary, foreign = postgres_keys(config, schema)
    estimates = postgres_row_estimates(config, schema)
    objects = [
        {
            "name": table,
            "kind": "table",
            "columns": cols,
            "primary_key": primary.get(table, []),
            "row_estimate": estimates.get(table),
        }
        for table, cols in columns.items()
    ]
    return {"objects": objects, "foreign_keys": foreign}


def _rest_api(decl: SourceDecl, config: PipelineSettings) -> dict:
    """Mỗi endpoint trả danh sách là một đối tượng; schema đọc từ mô tả OpenAPI, không gọi endpoint dữ liệu."""
    objects = [
        {
            "name": name,
            "kind": "endpoint",
            "columns": found["columns"],
            "primary_key": found["primary_key"],
            "row_estimate": None,
        }
        for name, found in rest_api.endpoints(decl, config).items()
    ]
    return {"objects": objects, "foreign_keys": []}


def _filesystem(decl: SourceDecl, config: PipelineSettings) -> dict:
    """Mỗi thư mục con có file CSV là một đối tượng; chỉ đọc dòng tiêu đề, không đọc dữ liệu."""
    objects = [
        {
            "name": name,
            "kind": "folder",
            "columns": found["columns"],
            "primary_key": found["primary_key"],
            "row_estimate": None,
        }
        for name, found in files.objects(decl).items()
    ]
    return {"objects": objects, "foreign_keys": []}


CONNECTORS: dict[str, Callable[[SourceDecl, PipelineSettings], dict]] = {
    "postgres": _postgres,
    "rest_api": _rest_api,
    "filesystem": _filesystem,
}


def supported(kind: str) -> bool:
    return kind in CONNECTORS


def discover(decl: SourceDecl, config: PipelineSettings) -> dict:
    """Kết nối tới nguồn ngay lúc gọi, trả {objects, foreign_keys}.

    Lỗi kết nối: `postgres.SourceUnavailableError` (thông báo chung, không lộ credential; lỗi API là lớp con).
    Loại nguồn chưa có connector: `UnsupportedSourceError`.
    """
    connector = CONNECTORS.get(decl.kind)
    if connector is None:
        raise UnsupportedSourceError(f"Chưa có connector cho nguồn {decl.kind}.")
    return connector(decl, config)
