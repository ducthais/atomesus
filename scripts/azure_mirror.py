"""Chép các bảng nguồn Postgres lên Azure Blob Storage dạng Parquet, để cổng budget đo byte thật (src/agents/azure_storage.py).

Mỗi nguồn `kind: postgres` trong policy/domains.yml → mọi bảng trong schema của nó → `<source_ref>/<table>/part-0.parquet`
trong container `PIPELINE_AZURE_STORAGE_CONTAINER`. DuckDB đọc Postgres bằng tài khoản chỉ đọc (PIPELINE_DATABASE_URL)
qua extension `postgres` rồi ghi Parquet; không ghi gì vào Postgres.

Thử local không tốn tiền bằng Azurite (giả lập Storage của Microsoft):
    docker run -d --name p045-azurite -p 10000:10000 mcr.microsoft.com/azure-storage/azurite azurite-blob --blobHost 0.0.0.0
rồi đặt PIPELINE_AZURE_STORAGE_CONNECTION_STRING bằng connection string mặc định của Azurite (xem tài liệu Azurite).

Chạy: python scripts/azure_mirror.py [--tables sales_pg.orders,pagila_pg.payment]
"""

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline.settings import PipelineSettings  # noqa: E402
from src.policy.loader import load_policy  # noqa: E402


def tables(policy, only: set[str] | None) -> list[tuple[str, str]]:
    """(source_ref, schema) của mọi nguồn postgres; `only` lọc theo `source_ref.table` sau khi liệt kê."""
    return [
        (ref, decl.schema_)
        for domain in policy.domains.domains.values()
        for ref, decl in domain.sources.items()
        if decl.kind == "postgres"
        and not decl.connection_ref.startswith("conn:")
        and (not only or any(o.split(".")[0] == ref for o in only))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tables", default="", help="chỉ chép các bảng này: source_ref.table, phân cách bằng dấu phẩy")
    args = parser.parse_args()
    only = {t for t in args.tables.split(",") if t} or None
    settings = PipelineSettings()
    connection = settings.azure_storage_connection_string.get_secret_value()
    if not connection or not settings.azure_storage_container:
        print("Đặt PIPELINE_AZURE_STORAGE_CONNECTION_STRING và PIPELINE_AZURE_STORAGE_CONTAINER trong .env trước.")
        return 2

    import duckdb
    from azure.storage.blob import ContainerClient

    container = ContainerClient.from_connection_string(connection, settings.azure_storage_container)
    if not container.exists():
        container.create_container()
    db = duckdb.connect()
    db.execute("INSTALL postgres; LOAD postgres")
    url = settings.database_url.get_secret_value().replace("'", "''")  # ATTACH không nhận tham số `?`
    db.execute(f"ATTACH '{url}' AS pg (TYPE postgres, READ_ONLY)")
    copied = 0
    with tempfile.TemporaryDirectory(prefix="azure-mirror-") as directory:
        for ref, schema in tables(load_policy(), only):
            names = [
                row[0]
                for row in db.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_catalog = 'pg' AND table_schema = ?",
                    [schema],
                ).fetchall()
            ]
            for name in sorted(names):
                if only and f"{ref}.{name}" not in only:
                    continue
                path = Path(directory) / f"{ref}_{name}.parquet"
                db.execute(f'COPY (SELECT * FROM pg."{schema}"."{name}") TO \'{path.as_posix()}\' (FORMAT parquet)')
                blob = f"{ref}/{name}/part-0.parquet"
                with path.open("rb") as handle:
                    container.upload_blob(blob, handle, overwrite=True)
                print(f"{schema}.{name} → {settings.azure_storage_container}/{blob} ({path.stat().st_size:,} byte)")
                copied += 1
    print(f"Đã chép {copied} bảng.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
