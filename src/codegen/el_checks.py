"""Đối soát E-L (P10 `el_validate`, SPEC §10): mỗi nguồn so với bảng bronze của chính nó, ghi một check có `source_id`.

- `row_reconcile`: số bản ghi ở nguồn = số dòng bronze. Nạp `merge` thì dlt gộp bản ghi trùng khóa ngay ở bronze, nên
  phía nguồn đếm số khóa khác nhau.
- `hash_reconcile`: tập khóa chính đọc thẳng từ nguồn (Postgres bằng SQL, API đọc lại mọi trang) trùng tập khóa ở bronze,
  so bằng SHA-256; lệch thì ghi vài khóa thiếu / thừa làm mẫu. Nguồn không khai báo khóa thì dùng mọi cột staging đọc.
- `idempotent`: chạy file ingestion lần hai trong cùng sandbox, bronze phải giữ nguyên số dòng và mã băm. Nạp `append`
  không có cursor sẽ nhân đôi dữ liệu mỗi lần chạy theo lịch: bắt ở đây, trước khi lên lịch thật.
"""

import hashlib
import json
from dataclasses import dataclass

from src.codegen.render import dlt_name
from src.pipeline import files, postgres, rest_api
from src.pipeline.settings import PipelineSettings
from src.run_report.models import Check

SAMPLE_KEYS = 5


@dataclass
class Side:
    """Một phía của phép đối soát: số bản ghi đọc được và tập khóa (giá trị đã đổi sang chuỗi)."""

    records: int
    keys: set[tuple]

    @property
    def hash(self) -> str:
        return hashlib.sha256("\n".join(sorted(json.dumps(k) for k in self.keys)).encode()).hexdigest()


def key_columns(source) -> list[str]:
    return list(source.primary_key) or [column.from_ for column in source.staging.columns]


def _text(value) -> str | None:
    return None if value is None else str(value)


def endpoint_of(source) -> dict:
    return {"endpoint": source.endpoint, "records_path": source.records_path, "pagination": source.pagination}


def source_records(source, decl, settings: PipelineSettings):
    """Bản ghi của nguồn không phải bảng Postgres, đọc thẳng (không qua dlt): mọi trang API hoặc mọi file CSV."""
    if source.kind == "filesystem":
        return files.records(decl, source.path_pattern.split("/", 1)[0])
    return rest_api.records(decl, settings, endpoint_of(source))


def read_source(source, decl, settings: PipelineSettings) -> Side:
    columns = key_columns(source)
    if source.kind in ("rest_api", "filesystem"):
        count, keys = 0, set()
        for row in source_records(source, decl, settings):  # theo luồng: không giữ mọi dòng (CSV hàng triệu dòng)
            count += 1
            keys.add(tuple(_text(row.get(c)) for c in columns))
        return Side(count, keys)
    from psycopg import sql

    table = sql.Identifier(decl.schema_, source.table)
    with postgres.connection(settings, decl) as conn:
        count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(table)).fetchone()[0]
        query = sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(map(sql.Identifier, columns)), table)
        keys = {tuple(_text(v) for v in row) for row in conn.execute(query).fetchall()}
    return Side(count, keys)


def read_bronze(db, source) -> Side:
    table = f'bronze."{source.target.table}"'
    columns = ", ".join(f'"{dlt_name(c)}"' for c in key_columns(source))
    count = db.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608
    keys = {tuple(_text(v) for v in row) for row in db.execute(f"select {columns} from {table}").fetchall()}  # noqa: S608
    return Side(count, keys)


def row_reconcile(source, src: Side, bronze: Side) -> Check:
    merged = source.kind != "postgres" and source.write_disposition == "merge"
    expected = len(src.keys) if merged else src.records
    observed = {"source": expected, "bronze": bronze.records}
    if source.kind != "postgres":
        observed["source_records"] = src.records
    return Check(
        name="row_reconcile",
        source_id=source.id,
        result="pass" if expected == bronze.records else "fail",
        observed=observed,
    )


def _sample(keys: set[tuple]) -> list[list]:
    return [list(k) for k in sorted(keys, key=json.dumps)[:SAMPLE_KEYS]]


def hash_reconcile(source, src: Side, bronze: Side) -> Check:
    missing, extra = src.keys - bronze.keys, bronze.keys - src.keys
    return Check(
        name="hash_reconcile",
        source_id=source.id,
        result="pass" if src.hash == bronze.hash else "fail",
        observed={
            "key_columns": key_columns(source),
            "source_keys": len(src.keys),
            "bronze_keys": len(bronze.keys),
            "source_hash": src.hash[:16],
            "bronze_hash": bronze.hash[:16],
            "missing_in_bronze": _sample(missing),
            "extra_in_bronze": _sample(extra),
        },
    )


def idempotent(source, first: Side, second: Side) -> Check:
    same = first.records == second.records and first.hash == second.hash
    return Check(
        name="idempotent",
        source_id=source.id,
        result="pass" if same else "fail",
        observed={
            "first_rows": first.records,
            "second_rows": second.records,
            "first_hash": first.hash[:16],
            "second_hash": second.hash[:16],
            "write_disposition": source.write_disposition,
        },
    )
