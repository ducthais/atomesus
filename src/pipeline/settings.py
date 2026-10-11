from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_", env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr("")
    store_path: str = "data/pipeline/runs.sqlite3"
    # Tài khoản local/demo. Production vẫn chặn đăng nhập này trong auth.demo_only.
    engineer_password: SecretStr = SecretStr("Engineer@123")
    reviewer_password: SecretStr = SecretStr("Reviewer@123")
    admin_password: SecretStr = SecretStr("Admin@123")
    # SMTP dùng để gửi mã xác thực đăng ký. Development/test có thể để trống để nhận mã trong response.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = "noreply@atomesus.local"
    smtp_starttls: bool = True
    # Bật thì đăng ký phải nhập mã gửi qua email (cần SMTP ở staging); tắt thì tạo tài khoản và đăng nhập ngay.
    registration_email_verification: bool = False
    # Thư mục code location của Dagster local (src/deployer/install.py ghi, Day 2 đọc). Duyệt không ghi gì ra đây:
    # deploy mở pull request vào repo pipeline, cấu hình PIPELINE_GITHUB_* nằm riêng ở src/codegen/github.py để token
    # không đi theo PipelineSettings vào agent và sandbox.
    deploy_dir: str = "data/dagster_local"
    # Dagster local (make dagster): trang dataset đọc trạng thái run qua GraphQL ở đây.
    dagster_url: str = "http://127.0.0.1:3070"
    # Warehouse DuckDB mà Dagster local ghi bronze / staging / gold (scripts/dagster_local.py).
    warehouse_path: str = "data/warehouse/warehouse.duckdb"
    # Múi giờ phiên DuckDB khi dbt ép timestamptz → timestamp. Phải là UTC (giống Postgres và phép đối chiếu);
    # đặt khác (ví dụ Asia/Ho_Chi_Minh) là tái hiện lỗi lệch giờ đã gặp: đối chiếu độc lập sẽ chặn run.
    warehouse_timezone: str = "UTC"
    # Nguồn rest_api có connection_ref mock_api (infra/mock_api, `make mock-api`).
    mock_api_url: str = "http://127.0.0.1:8100"
    # Cổng budget lấy byte thật từ metadata Parquet trên Azure Blob Storage khi đặt cả hai (src/agents/azure_storage.py);
    # để trống thì ước tính kiểu DuckDB. Container chứa bản sao bảng nguồn Postgres (scripts/azure_mirror.py).
    azure_storage_connection_string: SecretStr = SecretStr("")
    azure_storage_container: str = ""
    max_rows: int = Field(default=1000, ge=1, le=10000)
    max_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
