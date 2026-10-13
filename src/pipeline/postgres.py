"""Read only the registered source. Never expose connection credentials."""

from contextlib import contextmanager
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from src.pipeline.settings import PipelineSettings
from src.policy.models import SourceDecl


class SourceUnavailableError(Exception):
    pass


def _saved_record(settings: PipelineSettings, connection_ref: str) -> tuple[dict, str]:
    from src.pipeline import store

    record = store.get_connection_internal(settings.store_path, connection_ref.removeprefix("conn:"))
    if record is None:
        raise SourceUnavailableError("Không tìm thấy kết nối Postgres đã lưu. Kiểm tra nguồn đã khai báo.")
    return record


def connection_url(settings: PipelineSettings, connection_ref: str) -> str:
    """URL dlt cho kết nối đã lưu, cùng credential và IP đã kiểm như open_connection."""
    from src.pipeline import connections

    metadata, encrypted = _saved_record(settings, connection_ref)
    try:
        parameters = connections.connection_parameters(settings, metadata, encrypted)
    except HTTPException as exc:
        raise SourceUnavailableError(exc.detail) from None
    host = parameters["host"]
    if ":" in host:
        host = f"[{host}]"
    query = urlencode({key: parameters[key] for key in ("sslmode", "hostaddr", "connect_timeout", "options")})
    return (
        f"postgresql+psycopg://{quote(parameters['user'], safe='')}:{quote(parameters['password'], safe='')}"
        f"@{host}:{parameters['port']}/{quote(parameters['dbname'], safe='')}?{query}"
    )


@contextmanager
def connection(settings: PipelineSettings, decl: SourceDecl | None = None):
    if decl is not None and decl.connection_ref.startswith("conn:"):
        from src.pipeline import connections

        metadata, encrypted = _saved_record(settings, decl.connection_ref)
        try:
            with connections.open_connection(settings, metadata, encrypted) as conn:
                yield conn
        except HTTPException as exc:
            raise SourceUnavailableError(exc.detail) from None
        return
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


def source_schema(settings: PipelineSettings, decl: SourceDecl | None = None) -> list[dict]:
    schema = decl.schema_ if decl is not None else "sales_raw"
    with connection(settings, decl) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema, "orders"),
        ).fetchall()
    if not rows:
        raise SourceUnavailableError(f"Không tìm thấy {schema}.orders hoặc chưa được cấp quyền đọc.")
    return [{"name": name, "type": kind} for name, kind in rows]


def read_rows(settings: PipelineSettings, columns: list[str], decl: SourceDecl | None = None) -> list[dict]:
    from psycopg import sql

    names = list(dict.fromkeys(columns))
    schema = decl.schema_ if decl is not None else "sales_raw"
    query = sql.SQL("SELECT {} FROM {} ORDER BY order_id LIMIT %s").format(
        sql.SQL(", ").join(map(sql.Identifier, names)), sql.Identifier(schema, "orders")
    )
    with connection(settings, decl) as conn:
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
