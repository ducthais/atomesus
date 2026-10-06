"""Đọc nguồn thật cho agent: catalog cho planner (P1) và schema + 20 dòng mẫu cho codegen (P3, SPEC §9).

Tất định, không gọi LLM. Prompt luôn kèm dữ liệu từ đây để LLM không bao giờ tự nghĩ tên cột (SPEC §14.3).
Đọc được nguồn `postgres` và `rest_api`; nguồn khác (và API không trả lời) hiện trong catalog với `available: false`.
"""

from src.pipeline import connectors, postgres, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy

SAMPLE_ROWS = 20


class ResolveError(Exception):
    """Kế hoạch trỏ tới nguồn/đối tượng không đọc được."""


def _tables(settings: PipelineSettings, schema: str) -> dict[str, list[str]]:
    """Bảng → tên cột, cùng nguồn với GET /catalog (connectors): bỏ view và partition con."""
    return {table: [c["name"] for c in cols] for table, cols in connectors.postgres_columns(settings, schema).items()}


def _relations(settings: PipelineSettings, schema: str) -> list[str]:
    """Khóa ngoại trong cùng schema dạng `bảng.cột -> bảng_đích.cột_đích`: planner ghép bảng theo đúng các quan hệ này."""
    _, foreign = connectors.postgres_keys(settings, schema)
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
                sources[ref] = {
                    "kind": decl.kind,
                    "available": True,
                    "objects": _tables(settings, decl.schema_),
                    "relations": _relations(settings, decl.schema_),
                }
            elif decl.kind == "rest_api":
                sources[ref] = _api_entry(decl, settings)
            else:
                sources[ref] = {"kind": decl.kind, "available": False, "reason": "chưa hỗ trợ"}
        result[domain_name] = {
            "sources": sources,
            "entities": {name: entity.definition for name, entity in domain.entities.items()},
        }
    return result


def snapshot(plan: dict, policy: Policy, settings: PipelineSettings) -> dict:
    """Schema thật và 20 dòng mẫu cho từng nguồn trong kế hoạch, theo `sources[].id` của plan."""
    from psycopg import sql

    domain = policy.domains.domains[plan["domain"]]
    result = {}
    for item in plan["sources"]:
        decl = domain.sources.get(item["source_ref"])
        if decl is None:
            raise ResolveError(f"nguồn '{item['source_ref']}' không thuộc domain '{plan['domain']}'")
        if decl.kind == "rest_api":
            result[item["id"]] = _api_snapshot(decl, settings, item["source_ref"], item["object"])
            continue
        if decl.kind != "postgres":
            raise ResolveError(f"chưa hỗ trợ đọc nguồn {decl.kind} '{item['source_ref']}'")
        table = sql.Identifier(decl.schema_, item["object"])
        with postgres.connection(settings) as conn:
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
        result[item["id"]] = {
            "source_ref": item["source_ref"],
            "kind": decl.kind,
            "table": item["object"],
            "columns": {name: kind for name, kind in columns},
            "sample_rows": rows,
        }
    return result
