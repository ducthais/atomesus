"""BigQuery cho cổng `budget` (SPEC §12): `dry_run` thật thay cho ước tính theo kích thước nguồn.

Bật khi đặt `PIPELINE_BIGQUERY_PROJECT` và `PIPELINE_BIGQUERY_DATASET` (dataset chứa bản sao các bảng nguồn Postgres).
Mỗi nguồn postgres → một câu SELECT đúng các cột pipeline đọc (staging, khóa, cursor), chạy với
`QueryJobConfig(dry_run=True, use_query_cache=False)`: BigQuery trả `total_bytes_processed` mà không chạy, không tính
tiền. BigQuery tính theo cột nên con số phản ánh đúng việc bỏ cột không dùng. Nguồn rest_api / filesystem không có
trong BigQuery nên vẫn ước tính như DuckDB.

Xác thực bằng Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` hoặc
`gcloud auth application-default login`). Cần `pip install google-cloud-bigquery`.
"""

from typing import Protocol

from src.pipeline.postgres import SourceUnavailableError
from src.pipeline.settings import PipelineSettings
from src.spec.models import Spec


class BigQueryError(SourceUnavailableError):
    """Không gọi được BigQuery (thiếu thư viện, sai xác thực, không có bảng): cổng budget dừng INFRA_ERROR."""


class DryRun(Protocol):
    def __call__(self, sql: str) -> int: ...


class BigQueryDryRun:
    """`dry_run` qua google-cloud-bigquery; mỗi job gắn label để lọc trong INFORMATION_SCHEMA.JOBS (SPEC §12)."""

    def __init__(self, project: str, labels: dict[str, str] | None = None):
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise BigQueryError("Chưa cài google-cloud-bigquery: pip install google-cloud-bigquery") from exc
        self._bigquery, self._labels = bigquery, labels or {}
        try:
            self._client = bigquery.Client(project=project)
        except Exception as exc:  # thiếu Application Default Credentials
            raise BigQueryError(f"Không tạo được BigQuery client: {type(exc).__name__}: {str(exc)[:300]}") from exc

    def __call__(self, sql: str) -> int:
        config = self._bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, labels=self._labels)
        try:
            job = self._client.query(sql, job_config=config)
        except Exception as exc:  # bảng không có, sai quyền, sai cú pháp
            raise BigQueryError(f"BigQuery dry_run lỗi: {type(exc).__name__}: {str(exc)[:300]}") from exc
        return int(job.total_bytes_processed or 0)


def enabled(settings: PipelineSettings) -> bool:
    return bool(settings.bigquery_project and settings.bigquery_dataset)


def _columns(source) -> list[str]:
    names = [column.from_ for column in source.staging.columns]
    names += [*source.primary_key, *([source.cursor_field] if source.cursor_field else [])]
    return list(dict.fromkeys(names))


def source_sql(source, project: str, dataset: str) -> str:
    """Câu đọc một nguồn postgres trên bản sao BigQuery: đúng các cột pipeline dùng."""
    columns = ", ".join(f"`{name}`" for name in _columns(source))
    return f"SELECT {columns} FROM `{project}.{dataset}.{source.table}`"


def source_bytes(spec: Spec, settings: PipelineSettings, dry_run: DryRun | None = None) -> dict[str, dict]:
    """Byte `dry_run` của từng nguồn postgres: {source_id: {"bytes", "sql"}}. Nguồn loại khác không có ở đây."""
    postgres = [s for s in spec.sources if s.kind == "postgres"]
    if not postgres:
        return {}
    run = dry_run or BigQueryDryRun(settings.bigquery_project, labels={"model": spec.transform.model_name})
    found = {}
    for source in postgres:
        sql = source_sql(source, settings.bigquery_project, settings.bigquery_dataset)
        found[source.id] = {"bytes": run(sql), "sql": sql}
    return found
