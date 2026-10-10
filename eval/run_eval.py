"""Chấm điểm agent trên 15 ca của SPEC §18.2: chạy thật từng ca bằng LLM thật và nguồn thật.

Cần Postgres nguồn và `make mock-api` đang chạy. Mỗi ca: lập kế hoạch → tự xác nhận (P2) → viết Spec → chạy thử
trong sandbox. Run ghi vào sổ bằng chứng riêng (`--store`), không lẫn dữ liệu demo.

| Nhóm      | Số ca | File            | Đạt khi                                                               |
|-----------|-------|-----------------|-----------------------------------------------------------------------|
| hợp lệ    | 6     | eval/golden/    | tới READY_FOR_REVIEW và bảng gold khớp golden từng dòng               |
| vi phạm   | 6     | eval/cases/     | dừng đúng mã `BLOCKED_*` mong đợi (2 ca mỗi cổng semantic, budget, ast_guard) |
| mơ hồ     | 3     | eval/cases/     | dừng NEEDS_HUMAN ở bước `plan` kèm câu hỏi làm rõ, không đoán         |

So với golden: tên cột do LLM đặt nên ghép theo giá trị. Bảng gold được lấy nguyên vẹn ngay trong sandbox trước khi
sandbox bị xóa (bọc `reference.read_gold`, không sửa code sản phẩm).
- Cột khóa: cột có đúng tập giá trị của một trong các khóa của ca (`key`), và gold có đúng số dòng.
- Mỗi chỉ số (`measures`): cột số khớp golden ở mọi dòng, sai số ≤ 0,01% (tối thiểu 0,005). Chỉ số golden bằng 0 khớp
  cả với NULL (LEFT JOIN không có bản ghi), theo ghi chú của ca.

Ca vi phạm có thể hạ trần ngân sách cho riêng lượt chạy (`policy`, `spent_today_usd`) vì dữ liệu seed không đủ lớn để
vượt trần thật; báo cáo ghi rõ điều này. Kết quả luôn ghi kèm mẫu số (SPEC §18.3).

Chạy: python eval/run_eval.py [--only <id>,<id>] [--group valid|violation|ambiguous] [--store data/eval/runs.sqlite3]
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLDEN_DIR = ROOT / "eval" / "golden"
CASES_DIR = ROOT / "eval" / "cases"
RESULTS_DIR = ROOT / "eval" / "results"
GROUPS = ("valid", "violation", "ambiguous")
REL_TOLERANCE, ABS_TOLERANCE = Decimal("0.0001"), Decimal("0.005")


# ---------- so khớp (hàm thuần, có test) ----------


def key_text(value) -> str | None:
    """Giá trị khóa dạng chuỗi để so: ngày → YYYY-MM-DD, số nguyên dạng 1.0 / Decimal('1') → '1'."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    number = to_number(value)
    if number is not None and number == number.to_integral_value():
        return str(int(number))
    return str(value).strip()


def to_number(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def close(expected: Decimal, actual: Decimal | None) -> bool:
    if actual is None:
        return expected == 0  # LEFT JOIN không có bản ghi: NULL tương đương 0
    return abs(actual - expected) <= max(abs(expected) * REL_TOLERANCE, ABS_TOLERANCE)


def match_case(case: dict, gold: list[dict]) -> dict:
    """So bảng gold với golden của một ca. Trả {matched, key, measures: {tên golden: cột gold | None}, problems}."""
    rows = case["rows"]
    columns = list(gold[0]) if gold else []
    problems: list[str] = []
    if len(gold) != len(rows):
        problems.append(f"gold có {len(gold)} dòng, golden có {len(rows)} dòng")
    found_key = None
    for field in case["key"]:
        wanted = {key_text(r["key"][field]) for r in rows}
        for column in columns:
            if {key_text(row[column]) for row in gold} == wanted:
                found_key = (field, column)
                break
        if found_key:
            break
    if found_key is None:
        problems.append(f"không cột nào của gold có đúng tập giá trị khóa {case['key']}")
        return {"matched": False, "key": None, "measures": {m: None for m in case["measures"]}, "problems": problems}
    field, key_column = found_key
    by_key = {key_text(row[key_column]): row for row in gold}
    mapping: dict[str, str | None] = {}
    for measure in case["measures"]:
        expected = {key_text(r["key"][field]): to_number(r["values"][measure]) for r in rows}
        mapping[measure] = next(
            (
                column
                for column in columns
                if column != key_column
                and all(k in by_key and close(v, to_number(by_key[k][column])) for k, v in expected.items())
            ),
            None,
        )
        if mapping[measure] is None:
            problems.append(f"không cột nào khớp chỉ số '{measure}' ({case['measures'][measure]})")
    return {
        "matched": not problems,
        "key": {"golden": field, "gold": key_column},
        "measures": mapping,
        "problems": problems,
    }


def summarize(results: list[dict]) -> dict:
    """Tổng hợp kèm mẫu số, tách theo loại nguồn (một loại nguồn / đa nguồn)."""

    def group(result: dict) -> str:
        kinds = result["source_kinds"]
        return kinds[0] if len(kinds) == 1 else "đa nguồn"

    groups: dict[str, list[dict]] = {}
    for result in results:
        groups.setdefault(group(result), []).append(result)
    return {
        "matched": f"{sum(r['matched'] for r in results)}/{len(results)}",
        "first_try": f"{sum(r['matched'] and r['spec_attempts'] == 0 for r in results)}/{len(results)}",
        "by_kind": {k: f"{sum(r['matched'] for r in v)}/{len(v)}" for k, v in sorted(groups.items())},
        "llm_usd": round(sum(r["llm_usd"] for r in results), 6),
    }


def score_outcome(case: dict, status: str, stage: str, last_error: dict) -> dict:
    """Ca vi phạm / mơ hồ: dừng đúng trạng thái (và phase nếu ca nêu); ca mơ hồ phải có câu hỏi làm rõ."""
    expected, problems = case["expected"], []
    if status != expected["status"]:
        problems.append(f"mong đợi {expected['status']}, ra {status} ({stage})")
    elif expected.get("stage") and stage != expected["stage"]:
        problems.append(f"đúng {status} nhưng dừng ở {stage}, mong đợi {expected['stage']}")
    if case["group"] == "ambiguous" and status == "NEEDS_HUMAN" and not last_error.get("question"):
        problems.append("dừng chờ người nhưng không có câu hỏi làm rõ")
    return {"matched": not problems, "problems": problems}


def with_policy(policy, overrides: dict | None):
    """Chính sách của riêng một ca: thay vài trần trong budgets (per_run / per_day), phần còn lại giữ nguyên."""
    budgets = policy.budgets
    for section, values in (overrides or {}).items():
        budgets = budgets.model_copy(update={section: getattr(budgets, section).model_copy(update=values)})
    return policy.model_copy(update={"budgets": budgets})


def _ratio(results: list[dict]) -> str:
    return f"{sum(r['matched'] for r in results)}/{len(results)}"


def summarize_all(results: list[dict]) -> dict:
    """Chỉ số SPEC §18.3 theo nhóm, luôn kèm mẫu số; ca vi phạm tách theo cổng."""
    by_group = {g: [r for r in results if r["group"] == g] for g in GROUPS}
    violations = by_group["violation"]
    gates = sorted({r["gate"] for r in violations})
    valid = by_group["valid"]
    return {
        "valid": summarize(valid) if valid else None,
        "blocked": _ratio(violations) if violations else None,
        "blocked_by_gate": {g: _ratio([r for r in violations if r["gate"] == g]) for g in gates},
        "asked": _ratio(by_group["ambiguous"]) if by_group["ambiguous"] else None,
        "avg_spec_repairs": round(sum(r["spec_attempts"] for r in valid) / len(valid), 2) if valid else None,
        "total": _ratio(results),
        "llm_usd": round(sum(r["llm_usd"] for r in results), 6),
        "seconds": round(sum(r["seconds"] for r in results), 1),
    }


# ---------- chạy thật ----------


def load_cases(only: set[str] | None = None, groups: set[str] | None = None) -> list[dict]:
    """15 ca: 6 ca hợp lệ (eval/golden) rồi 6 ca vi phạm và 3 ca mơ hồ (eval/cases)."""
    golden = [
        {**json.loads(p.read_text(encoding="utf-8")), "group": "valid"} for p in sorted(GOLDEN_DIR.glob("*.json"))
    ]
    others = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES_DIR.glob("*.json"))]
    others.sort(key=lambda c: GROUPS.index(c["group"]))
    return [c for c in golden + others if (not only or c["id"] in only) and (not groups or c["group"] in groups)]


def capturing_sandbox(captured: dict) -> Callable:
    """Sandbox thật, giữ lại toàn bộ bảng gold mà phép đối chiếu độc lập đọc ra trước khi sandbox bị xóa."""
    from src.codegen import reference
    from src.codegen.sandbox import run_in_sandbox

    def run(spec, files, policy, settings):
        original = reference.read_gold

        def keep(db, model):
            captured["gold"] = original(db, model)
            return captured["gold"]

        reference.read_gold = keep
        try:
            return run_in_sandbox(spec, files, policy, settings)
        finally:
            reference.read_gold = original

    return run


def run_case(case: dict, store: str) -> dict:
    from src.agents.llm import openai_llm
    from src.agents.nodes.common import AgentDeps
    from src.agents.service import awaiting_plan, confirm_plan, plan_agent
    from src.pipeline.settings import PipelineSettings
    from src.policy.loader import load_policy

    captured: dict = {}
    settings = PipelineSettings().model_copy(update={"store_path": store})
    deps = AgentDeps(
        llm=openai_llm(),
        policy=with_policy(load_policy(), case.get("policy")),
        settings=settings,
        sandbox=capturing_sandbox(captured),
    )
    if "spent_today_usd" in case:  # ca ngân sách ngày: coi như hôm nay đã tiêu đúng số này
        deps.spent_today = lambda _settings: case["spent_today_usd"]
    started = time.perf_counter()
    report = plan_agent(case["request"], "eval", deps, store, sources=case.get("sources"))
    if awaiting_plan(report):
        report = confirm_plan(report, "eval", deps, store)
    last = report.error_trace[-1] if report.error_trace else {}
    mapping = None
    if case["group"] == "valid":
        gold = captured.get("gold") or []
        scored = match_case(case, gold) if report.status == "READY_FOR_REVIEW" and gold else None
        matched = bool(scored and scored["matched"])
        problems = scored["problems"] if scored else [f"run dừng ở {report.status} ({report.stage_reached})"]
        if not scored and last:
            problems.append(str(last.get("error", ""))[:300])
        mapping = scored and {"key": scored["key"], "measures": scored["measures"]}
    else:
        scored = score_outcome(case, report.status, report.stage_reached, last)
        matched, problems = scored["matched"], scored["problems"]
    return {
        "id": case["id"],
        "group": case["group"],
        "gate": case.get("gate"),
        "source_kinds": case.get("source_kinds", []),
        "run_id": report.run_id,
        "status": report.status,
        "stage": report.stage_reached,
        "matched": matched,
        "mapping": mapping,
        "problems": problems,
        "evidence": str(last.get("question") or last.get("error") or "")[:300],
        "spec_attempts": report.attempts.spec,
        "llm_calls": report.llm_calls,
        "llm_usd": report.cost.llm_usd,
        "seconds": round(time.perf_counter() - started, 1),
    }


GROUP_TITLES = {"valid": "Hợp lệ", "violation": "Vi phạm", "ambiguous": "Mơ hồ"}


def _note(result: dict) -> str:
    if result["problems"]:
        return "; ".join(result["problems"])
    if result["mapping"]:
        measures = ", ".join(f"{k}→`{v}`" for k, v in result["mapping"]["measures"].items())
        return f"khóa `{result['mapping']['key']['gold']}`, chỉ số {measures}"
    return result["evidence"]


def summary_lines(summary: dict) -> list[str]:
    """Các dòng chỉ số dùng chung cho latest.md và report.md."""
    lines = [f"- **Tổng: {summary['total']} ca đạt**"]
    if summary["valid"]:
        valid = summary["valid"]
        kinds = ", ".join(f"{k} {v}" for k, v in valid["by_kind"].items())
        lines.append(f"- Khớp golden: **{valid['matched']}** ca hợp lệ (theo loại nguồn: {kinds})")
        lines.append(
            f"- Chạy được ngay lần viết Spec đầu: {valid['first_try']}; tự sửa Spec trung bình "
            f"{summary['avg_spec_repairs']} lần mỗi ca"
        )
    if summary["blocked"]:
        gates = ", ".join(f"{k} {v}" for k, v in summary["blocked_by_gate"].items())
        lines.append(f"- Chặn đúng: **{summary['blocked']}** ca vi phạm ({gates})")
    if summary["asked"]:
        lines.append(f"- Hỏi lại thay vì đoán: **{summary['asked']}** ca mơ hồ")
    lines.append(f"- Tiền LLM cả lượt: ${summary['llm_usd']:.4f}; tổng thời gian {summary['seconds']} giây")
    return lines


def write_report(results: list[dict], summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    (RESULTS_DIR / "latest.json").write_text(
        json.dumps({"at": stamp, "summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Kết quả chấm 15 ca (SPEC §18)",
        "",
        f"Chạy lúc {stamp} bằng `python eval/run_eval.py`; số liệu đầy đủ ở `latest.json`.",
        "",
        *summary_lines(summary),
        "",
        "| Nhóm | Ca | Mong đợi | Kết quả | Đạt | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        expected = "khớp golden" if r["group"] == "valid" else r["expected"]
        lines.append(
            f"| {GROUP_TITLES[r['group']]} | `{r['id']}` | {expected} | {r['status']} ({r['stage']}) | "
            f"{'✅' if r['matched'] else '❌'} | {r['spec_attempts']} | {r['llm_calls']} | {r['seconds']} giây | "
            f"{_note(r).replace('|', '/')} |"
        )
    path = RESULTS_DIR / "latest.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="chỉ chạy các ca này (id, phân cách bằng dấu phẩy)")
    parser.add_argument("--group", default="", help="chỉ chạy các nhóm này: valid, violation, ambiguous")
    parser.add_argument("--store", default="data/eval/runs.sqlite3", help="sổ bằng chứng riêng cho lượt chấm")
    args = parser.parse_args()
    Path(args.store).parent.mkdir(parents=True, exist_ok=True)
    only = {c for c in args.only.split(",") if c} or None
    cases = load_cases(only, {g for g in args.group.split(",") if g} or None)
    results = []
    for case in cases:
        print(f"→ [{case['group']}] {case['id']} …", flush=True)
        result = run_case(case, args.store)
        result["expected"] = case["expected"]["status"] if "expected" in case else "khớp golden"
        verdict = "đạt" if result["matched"] else "KHÔNG đạt"
        print(f"  {result['status']} · {verdict} · {result['seconds']} giây", flush=True)
        results.append(result)
    summary = summarize_all(results)
    print("\n".join(summary_lines(summary)))
    print(f"Báo cáo: {write_report(results, summary).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
