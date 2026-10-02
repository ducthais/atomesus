"""So sánh hai phiên bản của một pipeline và đánh giá ảnh hưởng nếu duyệt bản mới.

Dùng khi Engineer "Sửa & chạy lại" hoặc khi Reviewer duyệt một model đã có bản đang chạy. Chỉ dựa trên
RunReport và file đã lưu trong sổ bằng chứng (hàm thuần, không chạy lại gì):
- Spec: nguồn, mapping cột, gộp theo gì, chỉ số, bộ lọc, join, lịch.
- Kết quả: số dòng, cột thêm / bớt / đổi kiểu, tổng chỉ số (từ `gold_profile`), kiểm tra đổi kết quả.
- Code: từng file thêm / xóa / sửa, kèm nội dung hai bên để hiển thị diff.
- Ảnh hưởng: bản sẽ bị thay thế trên Dagster, pipeline khác dùng chung bảng bronze.
"""

import difflib
from decimal import Decimal, InvalidOperation

from src.run_report.models import RunReport

DEPLOYED = ("APPROVED", "DEPLOYED")


def model_of(report: RunReport) -> str | None:
    spec = report.spec or {}
    return (spec.get("transform") or {}).get("model_name") if "sources" in spec else None


def pick_base(report: RunReport, reports: list[RunReport]) -> tuple[RunReport | None, str]:
    """Bản để so: run cha (Sửa & chạy lại) → bản đang chạy của cùng model → bản trước gần nhất của cùng model."""
    others = [r for r in reports if r.run_id != report.run_id]
    if report.parent_run_id:
        parent = next((r for r in others if r.run_id == report.parent_run_id), None)
        if parent:
            return parent, "run gốc mà bản này được sửa từ đó"
    model = model_of(report)
    if not model:
        return None, ""
    same = [r for r in others if r.domain == report.domain and model_of(r) == model and r.files]
    deployed = sorted((r for r in same if r.status in DEPLOYED), key=lambda r: r.approval.at, reverse=True)
    if deployed:
        return deployed[0], "bản đang chạy trên Dagster"
    earlier = sorted((r for r in same if r.created_at < report.created_at), key=lambda r: r.created_at, reverse=True)
    if earlier:
        return earlier[0], "bản trước của cùng model"
    return None, ""


def _sources(spec: dict) -> dict[str, dict]:
    return {s["id"]: s for s in spec.get("sources", [])}


def _mapping(source: dict) -> list[str]:
    staging = source.get("staging") or {}
    lines = []
    for c in staging.get("columns", []):
        extra = [f"định dạng {', '.join(c['date_formats'])}"] if c.get("date_formats") else []
        if c.get("currency"):
            extra.append(f"quy đổi {c['currency'].get('from', '?')} → {c['currency'].get('to', '?')}")
        rename = c["as"] if c["from"] == c["as"] else f"{c['from']} → {c['as']}"
        lines.append(f"{rename} ({c['type']}{'; ' + '; '.join(extra) if extra else ''})")
    return lines


def _spec_changes(before: dict, after: dict) -> list[dict]:
    """Các mục Spec khác nhau giữa hai bản, viết dạng danh sách chuỗi để giao diện hiện trước / sau."""
    changes = []

    def add(field: str, old: list[str], new: list[str]) -> None:
        if old != new:
            changes.append({"field": field, "before": old, "after": new})

    b_src, a_src = _sources(before), _sources(after)
    add(
        "Nguồn và bảng",
        sorted(f"{s['source_ref']}.{s.get('table') or s['id']}" for s in b_src.values()),
        sorted(f"{s['source_ref']}.{s.get('table') or s['id']}" for s in a_src.values()),
    )
    for sid in sorted(set(b_src) | set(a_src)):
        b, a = b_src.get(sid, {}), a_src.get(sid, {})
        add(f"Mapping cột · {sid}", _mapping(b), _mapping(a))
        add(
            f"Luật loại dòng · {sid}",
            (b.get("staging") or {}).get("reject_if", []),
            (a.get("staging") or {}).get("reject_if", []),
        )
        add(
            f"Chế độ ghi · {sid}",
            [b.get("write_disposition", "")] if b else [],
            [a.get("write_disposition", "")] if a else [],
        )
    bt, at = before.get("transform", {}), after.get("transform", {})
    add("Gộp theo", bt.get("grain", []), at.get("grain", []))
    period = lambda t: [f"{g['column']} theo {g['unit']}"] if (g := t.get("time_grain")) else []  # noqa: E731
    add("Gộp theo kỳ", period(bt), period(at))
    add(
        "Chỉ số",
        [f"{m['name']} = {m['expr']}" for m in bt.get("metrics", [])],
        [f"{m['name']} = {m['expr']}" for m in at.get("metrics", [])],
    )
    add("Bộ lọc", bt.get("filters", []), at.get("filters", []))
    add(
        "Ghép bảng",
        [f"{j['type']} {j['source']} on {j['on']}" for j in bt.get("joins", [])],
        [f"{j['type']} {j['source']} on {j['on']}" for j in at.get("joins", [])],
    )
    add("Lịch chạy", [(before.get("schedule") or {}).get("cron", "")], [(after.get("schedule") or {}).get("cron", "")])
    return changes


def _gate(report: RunReport, name: str) -> dict:
    return next((g.observed for g in report.gates if g.name == name), {})


def _number(value: str | None) -> Decimal | None:
    try:
        return None if value is None else Decimal(value)
    except InvalidOperation:
        return None


def _result_changes(base: RunReport, new: RunReport) -> dict:
    b, n = _gate(base, "post_run"), _gate(new, "post_run")
    b_profile = {c["name"]: c for c in b.get("gold_profile", [])}
    n_profile = {c["name"]: c for c in n.get("gold_profile", [])}
    # Bản cũ chưa có gold_profile: lấy tên cột từ dòng mẫu.
    b_cols = list(b_profile) or list((b.get("gold_samples") or [{}])[0])
    n_cols = list(n_profile) or list((n.get("gold_samples") or [{}])[0])
    metrics = []
    for name in n_cols:
        old, cur = b_profile.get(name, {}), n_profile.get(name, {})
        if "sum" in old or "sum" in cur:
            before, after = _number(old.get("sum")), _number(cur.get("sum"))
            delta = None if before in (None, 0) or after is None else float((after - before) / abs(before) * 100)
            metrics.append({"name": name, "before": old.get("sum"), "after": cur.get("sum"), "delta_pct": delta})
    checks_b = {f"{c.name}:{c.source_id or ''}": c.result for c in base.checks}
    checks_n = {f"{c.name}:{c.source_id or ''}": c.result for c in new.checks}
    return {
        "rows": {"before": b.get("gold_rows"), "after": n.get("gold_rows")},
        "columns": {
            "added": [c for c in n_cols if c not in b_cols],
            "removed": [c for c in b_cols if c not in n_cols],
            "type_changed": [
                {"name": c, "before": b_profile[c]["type"], "after": n_profile[c]["type"]}
                for c in n_cols
                if c in b_profile and c in n_profile and b_profile[c]["type"] != n_profile[c]["type"]
            ],
        },
        "metrics": metrics,
        "checks": [
            {"id": key, "before": checks_b.get(key), "after": checks_n.get(key)}
            for key in sorted(set(checks_b) | set(checks_n))
            if checks_b.get(key) != checks_n.get(key)
        ],
        "profiled": bool(b_profile and n_profile),
    }


def _file_changes(base_files: dict[str, str], new_files: dict[str, str]) -> list[dict]:
    files = []
    for path in sorted(set(base_files) | set(new_files)):
        before, after = base_files.get(path), new_files.get(path)
        if before == after:
            files.append({"path": path, "status": "same"})
            continue
        status = "added" if before is None else "removed" if after is None else "changed"
        diff = list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), lineterm="", n=0))
        files.append(
            {
                "path": path,
                "status": status,
                "added_lines": sum(1 for line in diff if line.startswith("+") and not line.startswith("+++")),
                "removed_lines": sum(1 for line in diff if line.startswith("-") and not line.startswith("---")),
                "before": before,
                "after": after,
            }
        )
    return files


def _bronze_tables(report: RunReport) -> set[str]:
    return {(s.get("target") or {}).get("table") for s in (report.spec or {}).get("sources", [])} - {None}


def impact(report: RunReport, reports: list[RunReport]) -> dict:
    """Ảnh hưởng nếu duyệt bản này: bản bị thay thế, và pipeline đang chạy khác dùng chung bảng bronze."""
    model = model_of(report)
    live = {}
    for r in sorted(
        (r for r in reports if r.status in DEPLOYED and r.run_id != report.run_id), key=lambda r: r.approval.at
    ):
        live[(r.domain, model_of(r))] = r  # bản duyệt sau cùng của mỗi model là bản đang chạy
    replaces = live.get((report.domain, model))
    shared = []
    mine = _bronze_tables(report)
    for (domain, other_model), r in live.items():
        common = sorted(mine & _bronze_tables(r))
        if other_model != model and domain == report.domain and common:
            shared.append({"model": other_model, "run_id": r.run_id, "bronze": [f"bronze.{t}" for t in common]})
    return {
        "replaces": {"run_id": replaces.run_id, "model": model, "approved_at": replaces.approval.at.isoformat()}
        if replaces
        else None,
        "shared_bronze": shared,
    }


def compare(
    new: RunReport,
    base: RunReport,
    reason: str,
    new_files: dict[str, str],
    base_files: dict[str, str],
    reports: list[RunReport],
) -> dict:
    return {
        "available": True,
        "base": {
            "run_id": base.run_id,
            "status": base.status,
            "created_at": base.created_at.isoformat(),
            "user_request": base.user_request,
            "reason": reason,
        },
        "spec": _spec_changes(base.spec or {}, new.spec or {}),
        "results": _result_changes(base, new),
        "files": _file_changes(base_files, new_files),
        "impact": impact(new, reports),
    }
