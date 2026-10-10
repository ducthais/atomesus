"""Cổng `budget` (P8, SPEC §10, §12): ước tính byte quét trước khi chạy, quy ra USD, chặn khi vượt trần.

Đặt `PIPELINE_BIGQUERY_PROJECT` / `_DATASET` thì nguồn postgres dùng `dry_run` thật của BigQuery (src/agents/bigquery.py).
Mặc định sandbox chạy DuckDB local nên không có `dry_run` thật. Ước tính theo SPEC §12 (DuckDB):
- nguồn `postgres`: kích thước bảng trên đĩa (`pg_relation_size`, cộng mọi partition con),
- nguồn `rest_api`: số byte JSON của mọi bản ghi, đọc lần lượt các trang như dlt sẽ đọc,
- nguồn `filesystem`: tổng dung lượng các file khớp `path_pattern`,
- model: đọc lại các bảng staging, ước bằng tổng byte của các nguồn.
USD = byte / 10^12 × `budgets.yml: pricing.usd_per_tb`, luôn gắn nhãn ước tính (`estimated: true`).

Chặn `BLOCKED_BUDGET` khi tổng vượt `per_run.max_bytes_scanned` hoặc `per_run.max_usd`, hoặc chi phí các run đã
ghi trong ngày (UTC, sổ bằng chứng, gồm cả tiền gọi LLM `cost.llm_usd`) cộng ước tính vượt `per_day.max_usd`.
Trần một run (`per_run.max_usd`) chỉ áp cho chi phí quét dữ liệu, như `dry_run` của SPEC.
"""

import json

from src.agents import bigquery
from src.pipeline import files, postgres, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy
from src.run_report.models import GateResult
from src.run_report.store import reserve_budget
from src.run_report.store import spent_today as ledger_spent_today
from src.spec.models import Spec

BYTES_PER_TB = 10**12

# Bảng thường: kích thước của chính nó. Bảng phân vùng (pagila.payment): bảng cha rỗng, dữ liệu nằm ở các partition.
TABLE_BYTES_SQL = """
SELECT pg_relation_size(c.oid)
     + coalesce((SELECT sum(pg_relation_size(t.relid)) FROM pg_partition_tree(c.oid) t WHERE t.relid <> c.oid), 0)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relname = %s
"""


def _source_bytes(
    spec: Spec, policy: Policy, settings: PipelineSettings, skip: frozenset[str] = frozenset()
) -> dict[str, int]:
    declared = policy.domains.domains[spec.domain].sources
    sizes = {}
    for source in spec.sources:
        if source.id in skip:  # đã có byte dry_run thật của BigQuery
            continue
        decl = declared[source.source_ref]
        if source.kind == "filesystem":
            folder = source.path_pattern.split("/", 1)[0]
            sizes[source.id] = files.objects(decl).get(folder, {}).get("bytes", 0)
        elif source.kind == "rest_api":
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


def estimate(spec: Spec, policy: Policy, settings: PipelineSettings, dry_run: bigquery.DryRun | None = None) -> dict:
    """Byte và USD của một lần chạy: E-L đọc mọi nguồn, model đọc lại staging (≈ cùng cỡ).

    Đặt `PIPELINE_BIGQUERY_PROJECT` / `_DATASET` thì nguồn postgres lấy byte từ `dry_run` thật của BigQuery
    (src/agents/bigquery.py); `estimated` chỉ False khi mọi nguồn đều có số thật.
    """
    real = bigquery.source_bytes(spec, settings, dry_run) if bigquery.enabled(settings) else {}
    found = _source_bytes(spec, policy, settings, frozenset(real))
    sources = {s.id: real[s.id]["bytes"] if s.id in real else found[s.id] for s in spec.sources}
    extract = sum(sources.values())
    total = extract * 2
    usd = total / BYTES_PER_TB * policy.budgets.pricing.usd_per_tb
    result = {
        "sources": sources,
        "model_bytes": extract,
        "bytes": total,
        "usd": usd,
        "estimated": len(real) < len(sources),
    }
    if real:
        result["dry_run"] = {"warehouse": "bigquery", "sql": {sid: item["sql"] for sid, item in real.items()}}
    return result


def spent_today(settings: PipelineSettings) -> float:
    """Tổng chi phí đã ghi và khoản đang giữ chỗ trong ngày UTC hiện tại."""
    return ledger_spent_today(settings.store_path)


def reserve_daily(
    settings: PipelineSettings,
    run_id: str,
    version: int,
    category: str,
    usd: float,
    daily_limit: float,
) -> tuple[bool, float]:
    return reserve_budget(settings.store_path, run_id, version, category, usd, daily_limit)


def gate(estimated: dict, policy: Policy, spent: float) -> tuple[GateResult, list[str]]:
    per_run, per_day = policy.budgets.per_run, policy.budgets.per_day
    size, usd = estimated["bytes"], estimated["usd"]
    by_source = ", ".join(f"{sid} {count:,} byte" for sid, count in estimated["sources"].items())
    word = "BigQuery dry_run" if "dry_run" in estimated else "Ước tính"
    errors = []
    if size > per_run.max_bytes_scanned:
        errors.append(
            f"{word} quét {size:,} byte ({by_source}, cộng model) vượt budgets.yml per_run.max_bytes_scanned "
            f"= {per_run.max_bytes_scanned:,}"
        )
    if usd > per_run.max_usd:
        errors.append(f"{word} {usd:.6f} USD vượt budgets.yml per_run.max_usd = {per_run.max_usd}")
    if spent + usd > per_day.max_usd:
        errors.append(
            f"Hôm nay đã dùng {spent:.6f} USD, cộng run này ({usd:.6f} USD) vượt budgets.yml per_day.max_usd "
            f"= {per_day.max_usd}"
        )
    detail = "; ".join(errors) or (
        f"{word} {size:,} byte ≈ {usd:.6f} USD ({by_source}, cộng model); hôm nay đã dùng {spent:.6f}/"
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
