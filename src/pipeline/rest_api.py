"""Đọc nguồn `rest_api` khai báo trong domains.yml. Chỉ GET, không bao giờ ghi; không lộ URL nội bộ trong lỗi.

Schema lấy từ mô tả OpenAPI của nguồn (`openapi`, SPEC §12.1): mỗi đường dẫn GET không có tham số đường dẫn và trả
về một mảng bản ghi là một đối tượng (tên = đường dẫn bỏ dấu `/`, SPEC §8.4). Từ OpenAPI suy ra luôn:
- `records_path`: thuộc tính mảng của body trả về (ví dụ `data`),
- `pagination`: theo tham số query (`page`, `offset`, `cursor`, không có thì `none`),
- `primary_key`: phần mở rộng `x-primary-key` của schema bản ghi (OpenAPI không có khái niệm khóa).

Kiểu cột ghi theo tên kiểu Postgres (`integer`, `text`, `timestamp with time zone`…) để codegen và kiểm tra Spec
dùng chung một bảng quy đổi với nguồn Postgres. Chuỗi `format: date-time` ghi là timestamp vì dlt tự nhận diện
chuỗi ISO 8601 thành timestamp khi đổ vào bronze.
"""

from collections.abc import Iterator
from posixpath import dirname

import httpx

from src.pipeline.postgres import SourceUnavailableError
from src.pipeline.settings import PipelineSettings
from src.policy.models import SourceDecl

TIMEOUT_SEC = 5
MAX_PAGES = 500  # chặn API phân trang không bao giờ trả trang rỗng
PAGE_PARAMS = (("page", "page"), ("offset", "offset"), ("cursor", "cursor"))
OPENAPI_TYPES = {
    ("integer", "int64"): "bigint",
    ("integer", None): "integer",
    ("number", None): "numeric",
    ("boolean", None): "boolean",
    ("string", "date-time"): "timestamp with time zone",
    ("string", None): "text",
}


class ApiUnavailableError(SourceUnavailableError):
    """API nguồn không trả lời đúng. Là SourceUnavailableError để mọi chỗ đang bắt lỗi nguồn bắt được luôn."""


def connection_url(connection_ref: str, settings: PipelineSettings) -> str | None:
    """connection_ref trong domains.yml → địa chỉ gốc của API (chỉ cấu hình ở backend, không nằm trong policy)."""
    return {"mock_api": settings.mock_api_url}.get(connection_ref)


def base_url(decl: SourceDecl, settings: PipelineSettings) -> str:
    """Địa chỉ gốc của nguồn: gốc kết nối + thư mục chứa mô tả OpenAPI (`/sales/openapi.json` → `/sales`)."""
    root = connection_url(decl.connection_ref, settings)
    if not root:
        raise ApiUnavailableError(f"Chưa cấu hình kết nối '{decl.connection_ref}' ở backend.")
    return root.rstrip("/") + dirname(decl.openapi).rstrip("/")


def client(url: str) -> httpx.Client:
    """Tách riêng để test thay bằng TestClient của mock API."""
    return httpx.Client(base_url=url, timeout=TIMEOUT_SEC, follow_redirects=False)


def _get(decl: SourceDecl, settings: PipelineSettings, path: str, params: dict | None = None) -> dict:
    error = f"Không đọc được API nguồn '{decl.connection_ref}' ({path})"
    try:
        with client(base_url(decl, settings)) as http:
            response = http.get(path, params=params)
    except httpx.HTTPError as exc:
        raise ApiUnavailableError(f"{error}: {type(exc).__name__}.") from exc
    if response.status_code != 200:
        raise ApiUnavailableError(f"{error}: HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise ApiUnavailableError(f"{error}: body không phải JSON.") from exc


def _resolve(spec: dict, schema: dict) -> dict:
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _column_type(prop: dict) -> str:
    kind, fmt = prop.get("type"), prop.get("format")
    if kind is None and "anyOf" in prop:  # trường cho phép null: lấy kiểu không phải null
        kind, fmt = next(
            ((p.get("type"), p.get("format")) for p in prop["anyOf"] if p.get("type") != "null"), (None, None)
        )
    return OPENAPI_TYPES.get((kind, fmt)) or OPENAPI_TYPES.get((kind, None)) or "text"


def _endpoint(spec: dict, path: str, operation: dict) -> dict | None:
    """Một đường dẫn GET → mô tả đối tượng, hoặc None nếu không trả về mảng bản ghi."""
    if "{" in path:
        return None
    content = operation.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {})
    body = _resolve(spec, content.get("schema", {}))
    records_path = next((name for name, p in body.get("properties", {}).items() if p.get("type") == "array"), None)
    if records_path is None:
        return None
    record = _resolve(spec, body["properties"][records_path].get("items", {}))
    params = {p.get("name") for p in operation.get("parameters", []) if p.get("in") == "query"}
    return {
        "endpoint": path,
        "records_path": records_path,
        "pagination": next((kind for name, kind in PAGE_PARAMS if name in params), "none"),
        "columns": [{"name": name, "type": _column_type(p)} for name, p in record.get("properties", {}).items()],
        "primary_key": list(record.get("x-primary-key", [])),
    }


def endpoints(decl: SourceDecl, settings: PipelineSettings) -> dict[str, dict]:
    """Tên đối tượng → {endpoint, records_path, pagination, columns, primary_key}, đọc từ mô tả OpenAPI."""
    spec = _get(decl, settings, "/" + decl.openapi.rsplit("/", 1)[-1])
    found = {}
    for path, item in sorted(spec.get("paths", {}).items()):
        described = _endpoint(spec, path, item["get"]) if "get" in item else None
        if described:
            found[path.strip("/")] = described
    if not found:
        raise ApiUnavailableError(
            f"Mô tả OpenAPI của '{decl.connection_ref}' không có endpoint GET nào trả về danh sách."
        )
    return found


def records(decl: SourceDecl, settings: PipelineSettings, endpoint: dict, limit: int | None = None) -> Iterator[dict]:
    """Đọc lần lượt các trang của một endpoint (đúng cách dlt đọc), dừng ở trang rỗng hoặc khi đủ `limit` bản ghi."""
    seen, offset, cursor = 0, 0, None
    for page in range(1, MAX_PAGES + 1):
        params = {"page": {"page": page}, "offset": {"offset": offset}, "cursor": {"cursor": cursor} if cursor else {}}
        body = _get(decl, settings, endpoint["endpoint"], params.get(endpoint["pagination"]))
        rows = body.get(endpoint["records_path"]) or []
        for row in rows:
            yield row
            seen += 1
            if limit is not None and seen >= limit:
                return
        offset += len(rows)
        cursor = body.get("next_cursor")
        if not rows or endpoint["pagination"] == "none" or (endpoint["pagination"] == "cursor" and not cursor):
            return
    raise ApiUnavailableError(f"Endpoint {endpoint['endpoint']} vẫn còn dữ liệu sau {MAX_PAGES} trang.")


def sample(decl: SourceDecl, settings: PipelineSettings, endpoint: dict, n: int) -> list[dict]:
    """n bản ghi đầu, giá trị đổi sang chuỗi như dòng mẫu của Postgres."""
    return [
        {key: None if value is None else str(value) for key, value in row.items()}
        for row in records(decl, settings, endpoint, limit=n)
    ]


def count(rows: list[dict], keys: list[str] | None = None) -> int:
    """Số bản ghi; có `keys` thì đếm số khóa khác nhau (dlt `merge` gộp bản ghi trùng khóa ngay ở bronze)."""
    if not keys:
        return len(rows)
    return len({tuple(row.get(key) for key in keys) for row in rows})
