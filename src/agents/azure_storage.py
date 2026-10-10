"""Azure cho cổng `budget` (thay BigQuery, SPEC §12): byte thật lấy từ metadata Parquet, không đọc dữ liệu.

Bật khi đặt `PIPELINE_AZURE_STORAGE_CONNECTION_STRING` và `PIPELINE_AZURE_STORAGE_CONTAINER`. Container chứa bản sao
bảng nguồn Postgres dạng Parquet, mỗi bảng một thư mục `<source_ref>/<table>/*.parquet` (scripts/azure_mirror.py chép).

Azure không có `dry_run` như BigQuery. Synapse serverless SQL tính tiền theo dữ liệu đọc từ Storage, và Parquet được đọc
theo cột, nên con số đúng nhất mà không phải chạy query là kích thước nén của các cột câu truy vấn đọc. Với mỗi nguồn
postgres:
1. liệt kê file Parquet của bảng (chỉ metadata của blob, không tải),
2. mỗi file chỉ tải footer: 8 byte cuối cho độ dài metadata, rồi đúng phần metadata (vài KB dù file lớn),
3. DuckDB `parquet_metadata` đọc footer → cộng `total_compressed_size` của các cột pipeline dùng (staging, khóa, cursor).
Nguồn rest_api / filesystem không có bản sao nên vẫn ước tính như DuckDB. Cần `pip install azure-storage-blob`.
"""

import struct
import tempfile
from pathlib import Path
from typing import Protocol

from src.pipeline.postgres import SourceUnavailableError
from src.pipeline.settings import PipelineSettings
from src.spec.models import Spec

PARQUET_MAGIC = b"PAR1"
TRAILER = 8  # 4 byte độ dài metadata + 4 byte "PAR1"


class AzureStorageError(SourceUnavailableError):
    """Không đọc được bản sao trên Azure (thiếu thư viện, sai xác thực, chưa chép bảng): cổng budget dừng INFRA_ERROR."""


class FooterReader(Protocol):
    def files(self, prefix: str) -> list[tuple[str, int]]: ...  # (tên blob, số byte) của các file Parquet
    def tail(self, name: str, size: int, length: int) -> bytes: ...  # `length` byte cuối của blob


class BlobFooterReader:
    """Đọc blob qua azure-storage-blob, chỉ theo range; dùng được cả với Azurite (giả lập Storage chạy local)."""

    def __init__(self, connection_string: str, container: str):
        try:
            from azure.storage.blob import ContainerClient
        except ImportError as exc:
            raise AzureStorageError("Chưa cài azure-storage-blob: pip install azure-storage-blob") from exc
        try:
            self._client = ContainerClient.from_connection_string(connection_string, container)
        except Exception as exc:  # connection string sai định dạng
            raise AzureStorageError(f"Không tạo được Azure Storage client: {type(exc).__name__}") from exc
        self.container = container

    def files(self, prefix: str) -> list[tuple[str, int]]:
        try:
            blobs = list(self._client.list_blobs(name_starts_with=prefix))
        except Exception as exc:  # sai khóa, container không có, mất mạng
            raise AzureStorageError(f"Không liệt kê được {self.container}/{prefix}: {type(exc).__name__}") from exc
        return [(b.name, b.size) for b in blobs if b.name.endswith(".parquet")]

    def tail(self, name: str, size: int, length: int) -> bytes:
        try:
            return self._client.download_blob(name, offset=size - length, length=length).readall()
        except Exception as exc:
            raise AzureStorageError(f"Không đọc được footer {name}: {type(exc).__name__}") from exc


def enabled(settings: PipelineSettings) -> bool:
    return bool(settings.azure_storage_connection_string.get_secret_value() and settings.azure_storage_container)


def used_columns(source) -> list[str]:
    """Các cột pipeline đọc từ một nguồn: cột staging, khóa chính, cursor."""
    names = [column.from_ for column in source.staging.columns]
    names += [*source.primary_key, *([source.cursor_field] if source.cursor_field else [])]
    return list(dict.fromkeys(names))


def footer(reader: FooterReader, name: str, size: int) -> bytes:
    """Footer của một file Parquet: metadata + 8 byte cuối, tải bằng hai lần đọc theo range."""
    trailer = reader.tail(name, size, TRAILER)
    if len(trailer) != TRAILER or trailer[4:] != PARQUET_MAGIC:
        raise AzureStorageError(f"{name} không phải file Parquet")
    length = struct.unpack("<I", trailer[:4])[0] + TRAILER
    if length > size:
        raise AzureStorageError(f"{name}: footer dài hơn cả file, file hỏng")
    return reader.tail(name, size, length)


def column_bytes(footer_bytes: bytes) -> dict[str, int]:
    """Kích thước nén của từng cột, đọc từ footer (DuckDB chỉ cần footer, không cần phần dữ liệu)."""
    import duckdb

    with tempfile.TemporaryDirectory(prefix="parquet-footer-") as directory:
        path = Path(directory) / "footer.parquet"
        path.write_bytes(PARQUET_MAGIC + footer_bytes)
        rows = duckdb.sql(
            "select path_in_schema, sum(total_compressed_size) from parquet_metadata(?) group by 1",
            params=[str(path)],
        ).fetchall()
    return {name: int(size) for name, size in rows}


def source_bytes(spec: Spec, settings: PipelineSettings, reader: FooterReader | None = None) -> dict[str, dict]:
    """Byte của từng nguồn postgres theo metadata Parquet: {source_id: {bytes, files, footer_bytes, columns, prefix}}."""
    postgres = [s for s in spec.sources if s.kind == "postgres"]
    if not postgres:
        return {}
    reader = reader or BlobFooterReader(
        settings.azure_storage_connection_string.get_secret_value(), settings.azure_storage_container
    )
    found = {}
    for source in postgres:
        prefix = f"{source.source_ref}/{source.table}/"
        listed = reader.files(prefix)
        if not listed:
            raise AzureStorageError(f"Chưa có bản sao Parquet của {prefix} (chạy scripts/azure_mirror.py)")
        wanted, sizes, read = used_columns(source), {}, 0
        for name, size in listed:
            tail = footer(reader, name, size)
            read += len(tail) + TRAILER
            for column, count in column_bytes(tail).items():
                sizes[column] = sizes.get(column, 0) + count
        missing = [c for c in wanted if c not in sizes]
        if missing:
            raise AzureStorageError(
                f"Bản sao {prefix} không có cột {missing}: Spec đọc cột nguồn không có, hoặc bản sao đã cũ "
                "(chép lại bằng scripts/azure_mirror.py)"
            )
        found[source.id] = {
            "bytes": sum(sizes[c] for c in wanted),
            "files": len(listed),
            "footer_bytes": read,
            "columns": {c: sizes[c] for c in wanted},
            "prefix": prefix,
        }
    return found
