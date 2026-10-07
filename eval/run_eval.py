"""Chấm điểm agent trên các ca có kết quả đúng (SPEC §18): chạy thật từng ca rồi so bảng gold với golden, từng dòng.

Mỗi ca: lập kế hoạch → tự xác nhận (P2) → viết Spec → chạy thử trong sandbox, bằng LLM thật và nguồn thật (cần
Postgres nguồn và `make mock-api` đang chạy). Bảng gold được lấy nguyên vẹn ngay trong sandbox trước khi sandbox bị xóa
(bọc `reference.read_gold`, không sửa code sản phẩm). Run ghi vào sổ bằng chứng riêng (`--store`), không lẫn dữ liệu demo.

So với golden (eval/golden/*.json): tên cột do LLM đặt nên ghép theo giá trị.
- Cột khóa: cột có đúng tập giá trị của một trong các khóa của ca (`key`), và gold có đúng số dòng.
- Mỗi chỉ số (`measures`): cột số khớp golden ở mọi dòng, sai số ≤ 0,01% (tối thiểu 0,005). Chỉ số golden bằng 0 khớp
  cả với NULL (LEFT JOIN không có bản ghi), theo ghi chú của ca.
Ca khớp khi run tới READY_FOR_REVIEW và mọi chỉ số khớp. Kết quả ghi kèm mẫu số và tách theo loại nguồn (SPEC §18.3).

Ca vi phạm / mơ hồ (`eval/cases/`, Người 3 viết) chưa có: khi có, thêm vào `load_cases`.

Chạy: python eval/run_eval.py [--only <id>,<id>] [--store data/eval/runs.sqlite3]
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
RESULTS_DIR = ROOT / "eval" / "results"
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


# ---------- chạy thật ----------


def load_cases(only: set[str] | None = None) -> list[dict]:
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(GOLDEN_DIR.glob("*.json"))]
    return [c for c in cases if not only or c["id"] in only]


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
    deps = AgentDeps(llm=openai_llm(), policy=load_policy(), settings=settings, sandbox=capturing_sandbox(captured))
    started = time.perf_counter()
    report = plan_agent(case["request"], "eval", deps, store, sources=case["sources"])
    if awaiting_plan(report):
        report = confirm_plan(report, "eval", deps, store)
    gold = captured.get("gold") or []
    scored = match_case(case, gold) if report.status == "READY_FOR_REVIEW" and gold else None
    problems = scored["problems"] if scored else [f"run dừng ở {report.status} ({report.stage_reached})"]
    if not scored and report.error_trace:
        problems.append(str(report.error_trace[-1].get("error", ""))[:300])
    return {
        "id": case["id"],
        "source_kinds": case["source_kinds"],
        "run_id": report.run_id,
        "status": report.status,
        "matched": bool(scored and scored["matched"]),
        "mapping": scored and {"key": scored["key"], "measures": scored["measures"]},
        "problems": problems,
        "spec_attempts": report.attempts.spec,
        "llm_calls": report.llm_calls,
        "llm_usd": report.cost.llm_usd,
        "seconds": round(time.perf_counter() - started, 1),
    }


def write_report(results: list[dict], summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    (RESULTS_DIR / "golden_latest.json").write_text(
        json.dumps({"at": stamp, "summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Kết quả chấm golden (ca hợp lệ)",
        "",
        f"Chạy lúc {stamp} bằng `python eval/run_eval.py`; số liệu đầy đủ ở `golden_latest.json`.",
        "",
        f"- **Khớp golden: {summary['matched']} ca**; khớp ngay lần viết Spec đầu: {summary['first_try']}",
        "- Theo loại nguồn: " + ", ".join(f"{k} {v}" for k, v in summary["by_kind"].items()),
        f"- Tiền LLM cả lượt: ${summary['llm_usd']:.4f}",
        "",
        "| Ca | Nguồn | Trạng thái | Khớp | Sửa Spec | Gọi LLM | Thời gian | Ghi chú |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        note = "; ".join(r["problems"]) or (
            f"khóa `{r['mapping']['key']['gold']}`, chỉ số "
            + ", ".join(f"{k}→`{v}`" for k, v in r["mapping"]["measures"].items())
        )
        lines.append(
            f"| `{r['id']}` | {' + '.join(r['source_kinds'])} | {r['status']} | {'✅' if r['matched'] else '❌'} | "
            f"{r['spec_attempts']} | {r['llm_calls']} | {r['seconds']} giây | {note.replace('|', '/')} |"
        )
    path = RESULTS_DIR / "golden_latest.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="chỉ chạy các ca này (id, phân cách bằng dấu phẩy)")
    parser.add_argument("--store", default="data/eval/runs.sqlite3", help="sổ bằng chứng riêng cho lượt chấm")
    args = parser.parse_args()
    Path(args.store).parent.mkdir(parents=True, exist_ok=True)
    cases = load_cases({c for c in args.only.split(",") if c} or None)
    results = []
    for case in cases:
        print(f"→ {case['id']} …", flush=True)
        result = run_case(case, args.store)
        print(
            f"  {result['status']} · {'khớp' if result['matched'] else 'KHÔNG khớp'} · {result['seconds']} giây",
            flush=True,
        )
        results.append(result)
    summary = summarize(results)
    print(f"Khớp golden {summary['matched']} · theo loại nguồn {summary['by_kind']}")
    print(f"Báo cáo: {write_report(results, summary).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
