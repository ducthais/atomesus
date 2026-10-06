"""Cổng `budget` (P8, SPEC §10, §12): ước tính byte quét trước khi chạy, quy ra USD, chặn khi vượt trần.

Sandbox chạy DuckDB local nên không có `dry_run` thật như BigQuery. Ước tính theo SPEC §12 (DuckDB):
- nguồn `postgres`: kích thước bảng trên đĩa (`pg_relation_size`, cộng mọi partition con),
- nguồn `rest_api`: số byte JSON của mọi bản ghi, đọc lần lượt các trang như dlt sẽ đọc,
- model: đọc lại các bảng staging, ước bằng tổng byte của các nguồn.
USD = byte / 10^12 × `budgets.yml: pricing.usd_per_tb`, luôn gắn nhãn ước tính (`estimated: true`).

Chặn `BLOCKED_BUDGET` khi tổng vượt `per_run.max_bytes_scanned` hoặc `per_run.max_usd`, hoặc chi phí các run đã
ghi trong ngày (UTC, sổ bằng chứng) cộng ước tính vượt `per_day.max_usd`. Chi phí ở đây là chi phí quét kho dữ liệu,
chưa gồm tiền gọi LLM.
"""

import json
from datetime import UTC, datetime

from src.pipeline import postgres, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy
from src.run_report.models import GateResult
from src.run_report.store import list_run_reports
from src.spec.models import Spec

BYTES_PER_TB = 10**12
DAY_SCAN_LIMIT = 1000  # số run gần nhất đọc để cộng chi phí trong ngày

# Bảng thường: kích thước của chính nó. Bảng phân vùng (pagila.payment): bảng cha rỗng, dữ liệu nằm ở các partition.
TABLE_BYTES_SQL = """
SELECT pg_relation_size(c.oid)
     + coalesce((SELECT sum(pg_relation_size(t.relid)) FROM pg_partition_tree(c.oid) t WHERE t.relid <> c.oid), 0)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relname = %s
"""


def _source_bytes(spec: Spec, policy: Policy, settings: PipelineSettings) -> dict[str, int]:
    declared = policy.domains.domains[spec.domain].sources
    sizes = {}
    for source in spec.sources:
        decl = declared[source.source_ref]
        if source.kind == "rest_api":
            endpoint = {
                "endpoint": source.endpoint,
                "records_path": source.records_path,
                "pagination": source.pagination,
            }
            rows = rest_api.records(decl, settings, endpoint)
            sizes[source.id] = sum(len(json.dumps(row, ensure_ascii=False).encode()) for row in rows)
        else:
            with postgres.connection(settings) as conn:
                row = conn.execute(TABLE_BYTES_SQL, (decl.schema_, source.table)).fetchone()
            sizes[source.id] = int(row[0]) if row else 0  # bảng không còn: schema_real/el_run báo lỗi rõ hơn
    return sizes


def estimate(spec: Spec, policy: Policy, settings: PipelineSettings) -> dict:
    """Ước tính byte và USD của một lần chạy: E-L đọc mọi nguồn, model đọc lại staging (≈ cùng cỡ)."""
    sources = _source_bytes(spec, policy, settings)
    extract = sum(sources.values())
    total = extract * 2
    usd = total / BYTES_PER_TB * policy.budgets.pricing.usd_per_tb
    return {"sources": sources, "model_bytes": extract, "bytes": total, "usd": usd, "estimated": True}


def spent_today(settings: PipelineSettings) -> float:
    """Tổng chi phí (cost.usd) của các run tạo trong ngày hôm nay, theo giờ UTC."""
    today = datetime.now(UTC).date()
    spent = 0.0
    for report in list_run_reports(settings.store_path, limit=DAY_SCAN_LIMIT):  # mới nhất trước
        if report.created_at.astimezone(UTC).date() != today:
            break
        spent += report.cost.usd
    return spent


def gate(estimated: dict, policy: Policy, spent: float) -> tuple[GateResult, list[str]]:
    per_run, per_day = policy.budgets.per_run, policy.budgets.per_day
    size, usd = estimated["bytes"], estimated["usd"]
    by_source = ", ".join(f"{sid} {count:,} byte" for sid, count in estimated["sources"].items())
    errors = []
    if size > per_run.max_bytes_scanned:
        errors.append(
            f"Ước tính quét {size:,} byte ({by_source}, cộng model) vượt budgets.yml per_run.max_bytes_scanned "
            f"= {per_run.max_bytes_scanned:,}"
        )
    if usd > per_run.max_usd:
        errors.append(f"Ước tính {usd:.6f} USD vượt budgets.yml per_run.max_usd = {per_run.max_usd}")
    if spent + usd > per_day.max_usd:
        errors.append(
            f"Hôm nay đã dùng {spent:.6f} USD, cộng run này ({usd:.6f} USD) vượt budgets.yml per_day.max_usd "
            f"= {per_day.max_usd}"
        )
    detail = "; ".join(errors) or (
        f"Ước tính {size:,} byte ≈ {usd:.6f} USD ({by_source}, cộng model); hôm nay đã dùng {spent:.6f}/"
        f"{per_day.max_usd} USD"
    )
    observed = {
        **estimated,
        "spent_today_usd": spent,
        "max_bytes_scanned": per_run.max_bytes_scanned,
        "max_usd": per_run.max_usd,
        "max_usd_per_day": per_day.max_usd,
    }
    return GateResult(name="budget", result="fail" if errors else "pass", detail=detail, observed=observed), errors
