"""Phát hiện schema nguồn thay đổi so với lúc pipeline được duyệt (schema drift).

Với mỗi pipeline đang chạy (bản duyệt sau cùng của mỗi model), so các cột Spec đọc với schema hiện tại của nguồn:
- `breaking`: bảng hoặc cột pipeline dùng không còn (bị xóa / đổi tên) → lần chạy tới sẽ lỗi.
- `warning`: cột đổi kiểu so với mốc lưu ở cổng `schema_real` lúc duyệt (run cũ chưa có mốc thì bỏ qua bước này).
- `ok`: không có thay đổi ảnh hưởng.
"""

import re
from collections.abc import Callable

from src.run_report.compare import DEPLOYED, model_of
from src.run_report.models import RunReport


def live_pipelines(reports: list[RunReport]) -> list[RunReport]:
    """Bản DEPLOYED duyệt sau cùng của mỗi (domain, model): bản đang chạy trên Dagster (bản mới chỉ APPROVED thì chưa)."""
    live: dict[tuple[str, str | None], RunReport] = {}
    for r in sorted((r for r in reports if r.status in DEPLOYED and model_of(r)), key=lambda r: r.approval.at):
        live[(r.domain, model_of(r))] = r
    return list(live.values())


def baseline(report: RunReport) -> dict[str, dict[str, str | None]]:
    """Cột mỗi nguồn pipeline đọc, kèm kiểu lúc kiểm tra nếu có mốc (cổng schema_real); không có thì kiểu = None."""
    recorded = next((g.observed.get("columns") for g in report.gates if g.name == "schema_real"), None) or {}
    result = {}
    for source in (report.spec or {}).get("sources", []):
        if source["id"] in recorded:
            result[source["id"]] = dict(recorded[source["id"]])
            continue
        used = {c["from"] for c in (source.get("staging") or {}).get("columns", [])} | set(
            source.get("primary_key", [])
        )
        if source.get("cursor_field"):
            used.add(source["cursor_field"])
        result[source["id"]] = dict.fromkeys(sorted(used))
    return result


def _normalize(kind: str | None) -> str | None:
    """So kiểu bỏ phần tham số và schema: `numeric(5,2)` ≈ `numeric`, `pagila.year` ≈ `year`. Kiểu lạ thì không so."""
    if not kind or kind.upper() == "USER-DEFINED":
        return None
    return re.sub(r"\(.*\)", "", kind).split(".")[-1].strip().lower()


def detect(report: RunReport, tables_of: Callable[[str], dict[str, dict[str, str]]]) -> dict:
    """So một pipeline với schema hiện tại. `tables_of(source_ref)` → {bảng: {cột: kiểu}}; lỗi kết nối thì ném lỗi."""
    sources, worst = [], "ok"
    base = baseline(report)
    for source in (report.spec or {}).get("sources", []):
        table = source.get("table") or source["id"]
        entry = {"source_id": source["id"], "source_ref": source["source_ref"], "table": table}
        try:
            live_tables = tables_of(source["source_ref"])
        except Exception as exc:  # nguồn không đọc được: không kết luận được, không coi là breaking
            sources.append({**entry, "status": "unknown", "reason": str(exc)})
            worst = "unknown" if worst == "ok" else worst
            continue
        live = live_tables.get(table)
        used = base.get(source["id"], {})
        if live is None:
            sources.append(
                {
                    **entry,
                    "status": "breaking",
                    "missing_table": True,
                    "missing_columns": sorted(used),
                    "type_changes": [],
                }
            )
            worst = "breaking"
            continue
        missing = sorted(c for c in used if c not in live)
        changes = [
            {"column": c, "before": kind, "after": live[c]}
            for c, kind in sorted(used.items())
            if c in live and _normalize(kind) and _normalize(live[c]) and _normalize(kind) != _normalize(live[c])
        ]
        status = "breaking" if missing else "warning" if changes else "ok"
        sources.append(
            {**entry, "status": status, "missing_table": False, "missing_columns": missing, "type_changes": changes}
        )
        if status == "breaking" or (status == "warning" and worst in ("ok", "unknown")):
            worst = status
    return {
        "run_id": report.run_id,
        "model": model_of(report),
        "domain": report.domain,
        "approved_at": report.approval.at.isoformat() if report.approval else None,
        "has_type_baseline": any(g.name == "schema_real" and "columns" in g.observed for g in report.gates),
        "status": worst,
        "sources": sources,
    }
