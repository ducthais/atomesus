"""Đọc nguồn thật cho agent: catalog cho planner (P1) và schema + 20 dòng mẫu cho codegen (P3, SPEC §9).

Tất định, không gọi LLM. Prompt luôn kèm dữ liệu từ đây để LLM không bao giờ tự nghĩ tên cột (SPEC §14.3).
Sprint 1 chỉ đọc được nguồn postgres; nguồn khác hiện trong catalog với `available: false`.
"""

from src.pipeline import postgres
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy

SAMPLE_ROWS = 20


class ResolveError(Exception):
    """Kế hoạch trỏ tới nguồn/đối tượng không đọc được."""


def _tables(settings: PipelineSettings, schema: str) -> dict[str, list[str]]:
    with postgres.connection(settings) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s ORDER BY table_name, ordinal_position",
            (schema,),
        ).fetchall()
    tables: dict[str, list[str]] = {}
    for table, column in rows:
        tables.setdefault(table, []).append(column)
    return tables


def catalog(policy: Policy, settings: PipelineSettings) -> dict:
    """Mọi domain → nguồn → đối tượng đọc được (tên bảng và cột). Đưa cho planner chọn nguồn."""
    result = {}
    for domain_name, domain in policy.domains.domains.items():
        sources = {}
        for ref, decl in domain.sources.items():
            if decl.kind == "postgres":
                sources[ref] = {"kind": decl.kind, "available": True, "objects": _tables(settings, decl.schema_)}
            else:
                sources[ref] = {"kind": decl.kind, "available": False, "reason": "chưa hỗ trợ ở Sprint 1"}
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
        if decl.kind != "postgres":
            raise ResolveError(f"chưa hỗ trợ đọc nguồn {decl.kind} '{item['source_ref']}' ở Sprint 1")
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
