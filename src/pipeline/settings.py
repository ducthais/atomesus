from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# File DuckDB chung của các pipeline deploy trước khi tách file theo pipeline (lỗi #2); bundle đã duyệt không sửa được
# nên vẫn ghi vào đây.
SHARED_WAREHOUSE = "warehouse.duckdb"


def warehouse_file(warehouse_dir: str | Path, model: str) -> Path:
    """File DuckDB riêng của một pipeline đã deploy: <thư mục warehouse>/<model>.duckdb (templates/dagster_asset.py.j2)."""
    return Path(warehouse_dir) / f"{model}.duckdb"


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_", env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr("")
    store_path: str = "data/pipeline/runs.sqlite3"
    connection_key: SecretStr = SecretStr("")
    # Chỉ cho phép nguồn nội bộ ở development/test; staging/production luôn chặn.
    connection_allow_private: bool = False
    # Tài khoản cài sẵn. Staging/production chỉ cho đăng nhập sau khi đổi khỏi các mật khẩu mặc định bên dưới.
    engineer_password: SecretStr = SecretStr("Engineer@123")
    reviewer_password: SecretStr = SecretStr("Reviewer@123")
    admin_password: SecretStr = SecretStr("Admin@123")
    # Nút "Truy cập nhanh" trên màn đăng nhập vào thẳng tài khoản demo engineer / reviewer, không cần mật khẩu
    # (POST /pipeline/session/demo). Development luôn bật; staging/production chỉ bật khi đặt PIPELINE_DEMO_LOGIN=1,
    # cho bản demo công khai: ai mở web cũng vào được Engineer / Reviewer demo. Admin không bao giờ có đăng nhập nhanh.
    demo_login: bool = False
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
    # Cách nút Deploy đưa bản đã duyệt lên production (ADR D6). direct: ghi bundle bất biến vào hộp thư release
    # (<release_dir>/inbox), service deployer cài thẳng, không cần GitHub. pr: mở pull request vào repo pipeline, cài
    # sau khi có người merge.
    deploy_mode: Literal["direct", "pr"] = "direct"
    # Hộp thư release dùng chung với deployer: backend chỉ ghi inbox/, deployer chuyển bundle sang done/ / rejected/.
    release_dir: str = "data/releases"
    # Dagster local (make dagster): trang dataset đọc trạng thái run qua GraphQL ở đây.
    dagster_url: str = "http://127.0.0.1:3070"
    # Warehouse DuckDB chung của bundle cũ; thư mục của nó chứa file riêng từng pipeline (warehouse_for).
    warehouse_path: str = f"data/warehouse/{SHARED_WAREHOUSE}"
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
    # VM 4 GB: dlt + dbt + DuckDB dùng chung hàng đợi, lập kế hoạch chỉ mở vài luồng.
    max_parallel_sandbox: int = Field(default=1, ge=1)
    max_parallel_planning: int = Field(default=3, ge=1)

    def warehouse_for(self, model: str) -> str:
        """File DuckDB chứa bronze / staging / gold của pipeline `model`: file riêng nếu có, không thì file chung."""
        own = warehouse_file(Path(self.warehouse_path).parent, model)
        return str(own) if own.is_file() else self.warehouse_path

    def validate_connection_key(self, app_env: str) -> None:
        key = self.connection_key.get_secret_value()
        if not key:
            if app_env in ("staging", "production"):
                raise RuntimeError("Phải đặt PIPELINE_CONNECTION_KEY để mã hóa mật khẩu kết nối Postgres.")
            return
        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("PIPELINE_CONNECTION_KEY phải là khóa Fernet hợp lệ.") from exc
