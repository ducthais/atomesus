"""Run allowlisted column mappings in a fresh DuckDB file, then delete it."""

from pathlib import Path
from tempfile import TemporaryDirectory

from src.pipeline.models import ColumnRule


def _cast(column: ColumnRule) -> str:
    """Ép kiểu nghiêm bằng TRY_CAST; cột ngày có `date_formats` thì thử lần lượt từng định dạng (bỏ khoảng trắng hai đầu)."""
    if not column.date_formats:
        return f'TRY_CAST("{column.source}" AS {column.type})'
    attempts = ", ".join(f"TRY_STRPTIME(TRIM(\"{column.source}\"), '{fmt}')" for fmt in column.date_formats)
    return f"CAST(COALESCE({attempts}) AS {column.type})"


def clean_rows(rows: list[dict], columns: list[ColumnRule]) -> dict:
    import duckdb

    names = list(dict.fromkeys(col.source for col in columns))
    with TemporaryDirectory(prefix="pipeline-sandbox-") as directory:
        with duckdb.connect(
            str(Path(directory) / "run.duckdb"),
            config={"enable_external_access": "false", "memory_limit": "128MB", "threads": "1"},
        ) as db:
            definitions = ", ".join(f'"{name}" VARCHAR' for name in names)
            db.execute(f"CREATE TABLE bronze ({definitions})")
            if rows:
                placeholders = ", ".join("?" for _ in names)
                db.executemany(
                    f"INSERT INTO bronze VALUES ({placeholders})", [[row[name] for name in names] for row in rows]
                )
            expressions = ", ".join(f'{_cast(col)} AS "{col.target}"' for col in columns)
            cast_rows = db.execute(f"SELECT {expressions} FROM bronze").fetchall()
            output, rejected = [], []
            for index, (source, cast_row) in enumerate(zip(rows, cast_rows, strict=True), start=1):
                bad = [
                    col.target
                    for col, value in zip(columns, cast_row, strict=True)
                    if source[col.source] is not None and value is None
                ]
                if bad:
                    rejected.append({"row": index, "reason": "cast", "columns": bad, "values": source})
                else:
                    output.append(
                        {
                            col.target: None if value is None else str(value)
                            for col, value in zip(columns, cast_row, strict=True)
                        }
                    )
    return {
        "rows_read": len(rows),
        "rows_written": len(output),
        "rows_rejected": len(rejected),
        "output_schema": [{"name": col.target, "type": col.type} for col in columns],
        "output": output,
        "rejected": rejected,
    }
