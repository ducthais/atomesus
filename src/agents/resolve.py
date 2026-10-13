"""Đọc nguồn thật cho agent: catalog cho planner (P1) và schema + 20 dòng mẫu cho codegen (P3, SPEC §9).

Tất định, không gọi LLM. Prompt luôn kèm dữ liệu từ đây để LLM không bao giờ tự nghĩ tên cột (SPEC §14.3).
Đọc được nguồn `postgres`, `rest_api` và `filesystem` (CSV); nguồn không đọc được hiện với `available: false`.
"""

from concurrent.futures import ThreadPoolExecutor

from src.pipeline import connectors, files, postgres, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy, SourceDecl

SAMPLE_ROWS = 20
MAX_PARALLEL_SOURCES = 6  # bằng trần budgets.yml rules.max_tables_joined


class ResolveError(Exception):
    """Kế hoạch trỏ tới nguồn/đối tượng không đọc được."""


def _tables(settings: PipelineSettings, schema: str, decl: SourceDecl | None = None) -> dict[str, list[str]]:
    """Bảng → tên cột, cùng nguồn với GET /catalog (connectors): bỏ view và partition con."""
    options = {"decl": decl} if decl and decl.connection_ref.startswith("conn:") else {}
    return {
        table: [c["name"] for c in cols]
        for table, cols in connectors.postgres_columns(settings, schema, **options).items()
    }


def _relations(settings: PipelineSettings, schema: str, decl: SourceDecl | None = None) -> list[str]:
    """Khóa ngoại trong cùng schema dạng `bảng.cột -> bảng_đích.cột_đích`: planner ghép bảng theo đúng các quan hệ này."""
    options = {"decl": decl} if decl and decl.connection_ref.startswith("conn:") else {}
    _, foreign = connectors.postgres_keys(settings, schema, **options)
    return [
        f"{fk['table']}.{col} -> {fk['ref_table']}.{ref}"
        for fk in foreign
        if fk["ref_schema"] == schema
        for col, ref in zip(fk["columns"], fk["ref_columns"], strict=True)
    ]


def _api_entry(decl, settings: PipelineSettings) -> dict:
    """Endpoint → tên trường. API không trả lời thì chỉ nguồn này `available: false`, nguồn khác vẫn dùng được."""
    try:
        found = rest_api.endpoints(decl, settings)
    except postgres.SourceUnavailableError as exc:
        return {"kind": decl.kind, "available": False, "reason": str(exc)}
    return {
        "kind": decl.kind,
        "available": True,
        "objects": {name: [c["name"] for c in item["columns"]] for name, item in found.items()},
    }


def _files_entry(decl) -> dict:
    """Thư mục → tên cột (dòng tiêu đề CSV). Thư mục không đọc được thì chỉ nguồn này `available: false`."""
    try:
        found = files.objects(decl)
    except postgres.SourceUnavailableError as exc:
        return {"kind": decl.kind, "available": False, "reason": str(exc)}
    return {
        "kind": decl.kind,
        "available": True,
        "objects": {name: [c["name"] for c in item["columns"]] for name, item in found.items()},
    }


def _files_snapshot(decl, source_ref: str, name: str) -> dict:
    found = files.objects(decl)
    if name not in found:
        raise ResolveError(f"nguồn file '{source_ref}' không có thư mục {name}/ chứa file CSV")
    item = found[name]
    return {
        "source_ref": source_ref,
        "kind": decl.kind,
        "table": name,
        # Spec chép nguyên hai trường này (codegen điền tất định).
        "path_pattern": item["path_pattern"],
        "format": item["format"],
        "primary_key": item["primary_key"],
        "columns": {c["name"]: c["type"] for c in item["columns"]},
        "sample_rows": files.sample(decl, name, SAMPLE_ROWS),
    }


def _api_snapshot(decl, settings: PipelineSettings, source_ref: str, name: str) -> dict:
    found = rest_api.endpoints(decl, settings)
    if name not in found:
        raise ResolveError(f"API nguồn '{source_ref}' không có endpoint /{name}")
    endpoint = found[name]
    return {
        "source_ref": source_ref,
        "kind": decl.kind,
        "table": name,
        # Spec chép nguyên ba trường này (codegen điền tất định, LLM không tự nghĩ).
        "endpoint": endpoint["endpoint"],
        "records_path": endpoint["records_path"],
        "pagination": endpoint["pagination"],
        "primary_key": endpoint["primary_key"],
        "columns": {c["name"]: c["type"] for c in endpoint["columns"]},
        "sample_rows": rest_api.sample(decl, settings, endpoint, SAMPLE_ROWS),
    }


def catalog(policy: Policy, settings: PipelineSettings) -> dict:
    """Mọi domain → nguồn → đối tượng đọc được (tên bảng và cột) và khóa ngoại. Đưa cho planner chọn nguồn."""
    result = {}
    for domain_name, domain in policy.domains.domains.items():
        sources = {}
        for ref, decl in domain.sources.items():
            if decl.kind == "postgres":
                options = {"decl": decl} if decl.connection_ref.startswith("conn:") else {}
                sources[ref] = {
                    "kind": decl.kind,
                    "available": True,
                    "objects": _tables(settings, decl.schema_, **options),
                    "relations": _relations(settings, decl.schema_, **options),
                }
            elif decl.kind == "rest_api":
                sources[ref] = _api_entry(decl, settings)
            elif decl.kind == "filesystem":
                sources[ref] = _files_entry(decl)
            else:
                sources[ref] = {"kind": decl.kind, "available": False, "reason": "chưa hỗ trợ"}
        result[domain_name] = {
            "sources": sources,
            "entities": {name: entity.definition for name, entity in domain.entities.items()},
        }
    return result


def snapshot(plan: dict, policy: Policy, settings: PipelineSettings) -> dict:
    """Schema thật và 20 dòng mẫu cho từng nguồn trong kế hoạch, theo `sources[].id` của plan.

    Các nguồn được đọc song song (mỗi nguồn một kết nối riêng); lỗi của bất kỳ nguồn nào vẫn ném ra như khi đọc tuần tự.
    """
    domain = policy.domains.domains[plan["domain"]]
    for item in plan["sources"]:  # kiểm tra khai báo trước khi mở kết nối nào
        if item["source_ref"] not in domain.sources:
            raise ResolveError(f"nguồn '{item['source_ref']}' không thuộc domain '{plan['domain']}'")
    items = plan["sources"]
    with ThreadPoolExecutor(max_workers=max(1, min(len(items), MAX_PARALLEL_SOURCES))) as pool:
        found = list(pool.map(lambda item: _snapshot_one(domain.sources[item["source_ref"]], item, settings), items))
    return {item["id"]: entry for item, entry in zip(items, found, strict=True)}


def _snapshot_one(decl, item: dict, settings: PipelineSettings) -> dict:
    from psycopg import sql

    if decl.kind == "rest_api":
        return _api_snapshot(decl, settings, item["source_ref"], item["object"])
    if decl.kind == "filesystem":
        return _files_snapshot(decl, item["source_ref"], item["object"])
    if decl.kind != "postgres":
        raise ResolveError(f"chưa hỗ trợ đọc nguồn {decl.kind} '{item['source_ref']}'")
    table = sql.Identifier(decl.schema_, item["object"])
    with postgres.connection(settings, decl) as conn:
        columns = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (decl.schema_, item["object"]),
        ).fetchall()
        if not columns:
            raise ResolveError(f"không tìm thấy bảng {decl.schema_}.{item['object']} hoặc chưa được cấp quyền đọc")
        cursor = conn.execute(sql.SQL("SELECT * FROM {} LIMIT %s").format(table), (SAMPLE_ROWS,))
        names = [column.name for column in cursor.description]
        rows = [
            {key: None if value is None else str(value) for key, value in zip(names, row, strict=True)}
            for row in cursor.fetchall()
        ]
    return {
        "source_ref": item["source_ref"],
        "kind": decl.kind,
        "table": item["object"],
        "columns": {name: kind for name, kind in columns},
        "sample_rows": rows,
    }
