from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_", env_file=(".env", "infra/postgres/.env"), extra="ignore")
    database_url: SecretStr = SecretStr("")
    store_path: str = "data/pipeline/runs.sqlite3"
    # Sprint 1: duyệt xong thì ghi project Dagster ra đây (Sprint 3 chuyển sang pull request).
    deploy_dir: str = "data/dagster_local"
    # Dagster local (make dagster): trang dataset đọc trạng thái run qua GraphQL ở đây.
    dagster_url: str = "http://127.0.0.1:3070"
    # Warehouse DuckDB mà Dagster local ghi bronze / staging / gold (scripts/dagster_local.py).
    warehouse_path: str = "data/warehouse/warehouse.duckdb"
    max_rows: int = Field(default=1000, ge=1, le=10000)
    max_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
