"""Sổ kết nối Postgres riêng của người dùng; không trả credential về client."""

import ipaddress
import re
import socket
import time
from typing import Literal
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from src.config import get_settings
from src.pipeline import store
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings

INTERNAL_HOSTS = {"localhost", "postgres", "backend", "dagster", "deployer", "mock-api", "web", "caddy"}
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000"


class SafeValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Lỗi validation mặc định chứa input, có thể lộ mật khẩu trong body không hợp lệ.
                raise HTTPException(
                    422,
                    "Thông tin kết nối không hợp lệ. Kiểm tra tên, host, cổng, database, user, mật khẩu và sslmode.",
                ) from exc

        return safe_handler


router = APIRouter(prefix="/connections", tags=["Postgres connections"], route_class=SafeValidationRoute)


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Postgres", min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    user: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=4096)
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "require"

    @field_validator("name", "host", "database", "user", mode="before")
    @classmethod
    def _trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("name", "host", "database", "user", "password")
    @classmethod
    def _no_null(cls, value):
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if "\x00" in text:
            raise ValueError("Thông tin kết nối không được chứa ký tự NUL.")
        return value

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        value = value.lower().rstrip(".")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value):
                raise ValueError("Host phải là tên miền hoặc địa chỉ IP, không phải URL hay socket.")
        if "%" in value:
            raise ValueError("Host không được chứa vùng mạng IPv6.")
        return value


def engineer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được thêm, kiểm tra hoặc xóa kết nối.")
    return user


def resolve_host(host: str, port: int, config: PipelineSettings) -> str:
    """Kiểm mọi địa chỉ DNS, rồi ghim IP khi kết nối để chặn DNS rebinding."""
    allow_private = config.connection_allow_private and get_settings().app_env in ("development", "test")
    if not allow_private and (host in INTERNAL_HOSTS or host.endswith(".localhost")):
        raise HTTPException(422, "Không được kết nối tới host nội bộ của nền tảng.")
    try:
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [
                ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ]
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "Không phân giải được host. Kiểm tra tên miền và DNS.") from exc
    if not addresses:
        raise HTTPException(422, "Không phân giải được host. Kiểm tra tên miền và DNS.")
    if not allow_private and any(
        not address.is_global or address.is_reserved or address.is_multicast for address in addresses
    ):
        raise HTTPException(422, "Không được kết nối tới địa chỉ loopback, private, link-local hoặc reserved.")
    return str(addresses[0])


def test_connection(body: ConnectionRequest, config: PipelineSettings) -> None:
    address = resolve_host(body.host, body.port, config)
    try:
        with psycopg.connect(
            host=body.host,
            hostaddr=address,
            port=body.port,
            dbname=body.database,
            user=body.user,
            password=body.password.get_secret_value(),
            sslmode=body.sslmode,
            connect_timeout=5,
            options=READ_ONLY_OPTIONS,
        ) as conn:
            readable = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c"
                " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
                " WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')"
                " AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'"
                " AND has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT'))"
            ).fetchone()
            if not readable or not readable[0]:
                raise HTTPException(
                    422, "User chưa có quyền SELECT trên bảng dữ liệu nào. Hãy cấp quyền đọc bảng và schema."
                )
    except psycopg.Error as exc:
        message = str(exc).lower()
        if "ssl" in message or "no encryption" in message:
            detail = "Máy chủ yêu cầu SSL hoặc không hỗ trợ chế độ SSL đã chọn. Kiểm tra sslmode."
        elif exc.sqlstate in ("28P01", "28000") or "password authentication failed" in message:
            detail = "Tên user hoặc mật khẩu Postgres không đúng, hoặc user chưa được phép đăng nhập."
        elif exc.sqlstate == "42501":
            detail = "User chưa có quyền SELECT. Hãy cấp quyền đọc bảng và schema."
        elif exc.sqlstate == "3D000":
            detail = "Database không tồn tại. Kiểm tra tên database."
        else:
            detail = "Không kết nối được Postgres. Kiểm tra host, cổng, firewall và trạng thái máy chủ."
        raise HTTPException(422, detail) from exc


@router.post("/test", dependencies=[Depends(engineer)])
def check_connection(body: ConnectionRequest, config: PipelineSettings = Depends(settings)):
    test_connection(body, config)
    return {"ok": True, "message": "Kết nối Postgres thành công; user có quyền SELECT."}


@router.post("", status_code=201)
def create_connection(
    body: ConnectionRequest, user: dict = Depends(engineer), config: PipelineSettings = Depends(settings)
):
    config.validate_connection_key(get_settings().app_env)
    key = config.connection_key.get_secret_value()
    if not key:
        raise HTTPException(503, "Chưa cấu hình PIPELINE_CONNECTION_KEY để lưu kết nối an toàn.")
    test_connection(body, config)
    encrypted = Fernet(key.encode()).encrypt(body.password.get_secret_value().encode()).decode()
    metadata = {
        "id": uuid4().hex,
        "owner_id": user["id"],
        **body.model_dump(exclude={"password"}),
        "created_at": time.time(),
    }
    store.save_connection(config.store_path, metadata, encrypted)
    return metadata


@router.get("")
def connections(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)):
    items = store.list_connections(config.store_path, user["id"])
    if user["role"] != "Engineer":
        return [{"id": item["id"], "name": item["name"]} for item in items]
    return items


@router.delete("/{connection_id}", status_code=204)
def remove_connection(connection_id: str, user: dict = Depends(engineer), config: PipelineSettings = Depends(settings)):
    try:
        deleted = store.delete_connection(config.store_path, connection_id, user["id"])
    except store.ConnectionInUseError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Không tìm thấy kết nối.")
    return Response(status_code=204)
