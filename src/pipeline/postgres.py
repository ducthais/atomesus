"""Read only the registered source. Never expose connection credentials."""

from contextlib import contextmanager

from src.pipeline.settings import PipelineSettings


class SourceUnavailableError(Exception):
    pass


@contextmanager
def connection(settings: PipelineSettings):
    if not settings.database_url.get_secret_value():
        raise SourceUnavailableError("Chưa cấu hình PIPELINE_DATABASE_URL ở backend.")
    try:
        import psycopg
    except ImportError as exc:
        raise SourceUnavailableError("Backend cần cài requirements.txt.") from exc
    try:
        with psycopg.connect(
            settings.database_url.get_secret_value(),
            connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000",
        ) as conn:
            yield conn
    except psycopg.Error as exc:
        raise SourceUnavailableError("Không đọc được Postgres. Kiểm tra kết nối, seed và quyền SELECT.") from exc


def source_schema(settings: PipelineSettings) -> list[dict]:
    with connection(settings) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            ("sales_raw", "orders"),
        ).fetchall()
    if not rows:
        raise SourceUnavailableError("Không tìm thấy sales_raw.orders hoặc chưa được cấp quyền đọc.")
    return [{"name": name, "type": kind} for name, kind in rows]


def read_rows(settings: PipelineSettings, columns: list[str]) -> list[dict]:
    from psycopg import sql

    names = list(dict.fromkeys(columns))
    query = sql.SQL("SELECT {} FROM sales_raw.orders ORDER BY order_id LIMIT %s").format(
        sql.SQL(", ").join(map(sql.Identifier, names))
    )
    with connection(settings) as conn:
        rows = conn.execute(query, (settings.max_rows + 1,)).fetchall()
    if len(rows) > settings.max_rows:
        raise ValueError(f"Nguồn vượt giới hạn {settings.max_rows} dòng của sprint này.")
    result, size = [], 0
    for row in rows:
        values = [None if value is None else str(value) for value in row]
        size += sum(len(value.encode()) for value in values if value is not None)
        if size > settings.max_bytes:
            raise ValueError("Dữ liệu vượt giới hạn dung lượng sandbox.")
        result.append(dict(zip(names, values, strict=True)))
    return result
