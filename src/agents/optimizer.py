"""Optimizer P13 (SPEC §9.3): tất định, không gọi LLM. Thử từng luật, chỉ giữ bản vừa đúng vừa nhanh hơn.

Hai luật cố định:

| Luật                       | Áp khi                                                        | Bản ứng viên                         |
|----------------------------|---------------------------------------------------------------|--------------------------------------|
| `table_to_incremental`     | model `table`, grain có cột ngày/giờ, bảng nguồn chính ≥ `optimizer.incremental_min_rows` dòng | `incremental`, `unique_key` = cột grain, `partition_by` = cột ngày/giờ |
| `missing_partition_filter` | model `incremental` chưa có `partition_by` (mỗi lần chạy tính lại toàn bộ) | thêm `partition_by` = cột ngày/giờ: chỉ tính lại từ phân vùng mới nhất |

Mỗi bản ứng viên đi qua đúng các bước của bản gốc: Spec hợp lệ → `ast_guard` → `budget` (src/agents/budget.py, dùng
chung với P8) → render → chạy thử P9–P12 rồi chạy lại model gold `optimizer.rerun_samples` lần.

"Nhanh hơn" đo bằng trung vị thời gian **chạy lại** model gold: lợi ích của incremental chỉ có ở các lần chạy theo
lịch sau lần đầu (lần build đầu luôn tính lại toàn bộ). "Đúng" là bản ứng viên đạt mọi kiểm tra của P9–P12 **và** bảng
gold giống hệt bản gốc, cả sau lần build đầu lẫn sau các lần chạy lại. Bản ứng viên giữ lại khi trung vị nhanh hơn
bản gốc ít nhất `optimizer.min_gain_pct` % **và** các lần chạy lại của nó nhanh hơn mọi lần của bản gốc: thời gian chạy
lại chỉ vài phần mười giây nên chênh lệch nhỏ thường là nhiễu (eval 2026-10-13: cùng một bản 30 dòng, lượt này đo nhanh
hơn 26,9%, lượt kia chậm hơn 41%). Khi bản ứng viên có ≥ 3 mẫu, bỏ **một** mẫu chậm nhất của nó trước khi so, để một
mẫu nhiễu lẻ không làm hỏng cả bản tối ưu (eval 3 triệu dòng: ứng viên [0,138 0,234 0,155 0,135 0,138] so với gốc
[0,213..0,271] được giữ; bảng 30 dòng ứng viên có hai mẫu 0,36/0,50 vẫn bị bỏ). Mọi bản bị loại đều ghi lý do;
optimizer không bao giờ làm hỏng run.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from src.agents import budget
from src.codegen import guard
from src.codegen.render import RenderError, file_hashes, render
from src.codegen.sandbox import call_sandbox
from src.pipeline.postgres import SourceUnavailableError
from src.spec.models import Spec

RULES = ("table_to_incremental", "missing_partition_filter")
TEMPORAL = ("date", "timestamp")
PROFILE_KEYS = ("name", "non_null", "sum", "min", "max")


@dataclass(frozen=True)
class Proposal:
    rule: str
    candidate: dict | None  # Spec ứng viên (dạng JSON); None = luật không áp, xem `reason`
    reason: str


def time_column(spec: Spec) -> str | None:
    """Phần tử grain là cột ngày/giờ sau staging (ưu tiên `time_grain.column`); None nếu grain không có."""
    t = spec.transform
    types = {s.id: {c.as_: c.type for c in s.staging.columns} for s in spec.sources}
    candidates = [t.time_grain.column] if t.time_grain else []
    candidates += [g for g in t.grain if g not in candidates]
    for grain in candidates:
        source_id, _, name = grain.rpartition(".")
        if types.get(source_id or t.from_, {}).get(name) in TEMPORAL:
            return grain
    return None


def main_rows(checks: list, spec: Spec) -> int | None:
    """Số dòng staging của nguồn chính (`transform.from`), đọc từ phép `staging_reconcile` của lần chạy thử."""
    for check in checks:
        name = check.name if hasattr(check, "name") else check.get("name")
        source_id = check.source_id if hasattr(check, "source_id") else check.get("source_id")
        observed = check.observed if hasattr(check, "observed") else check.get("observed", {})
        if name == "staging_reconcile" and source_id == spec.transform.from_:
            return observed.get("staging")
    return None


def _with_transform(spec: Spec, **changes) -> dict:
    raw = spec.model_dump(by_alias=True, mode="json")
    raw["transform"].update(changes)
    return raw


def propose(spec: Spec, rows: int | None, policy) -> list[Proposal]:
    """Bản ứng viên của từng luật, hoặc lý do luật không áp (ghi vào RunReport.optimizer)."""
    t, limits = spec.transform, policy.budgets.optimizer
    time = time_column(spec)
    partition = time.rpartition(".")[2] if time else None
    unique_key = [g.rpartition(".")[2] for g in t.grain]
    if t.materialization != "table":
        first = Proposal(RULES[0], None, f"model đang là `{t.materialization}`, luật chỉ áp cho `table`")
    elif not time:
        first = Proposal(RULES[0], None, "grain không có cột ngày/giờ để chỉ tính lại phân vùng mới")
    elif rows is None or rows < limits.incremental_min_rows:
        first = Proposal(
            RULES[0],
            None,
            f"bảng nguồn chính có {rows if rows is not None else 'không rõ'} dòng, dưới ngưỡng "
            f"{limits.incremental_min_rows:,} (budgets.yml optimizer.incremental_min_rows)",
        )
    else:
        candidate = _with_transform(spec, materialization="incremental", unique_key=unique_key, partition_by=partition)
        first = Proposal(RULES[0], candidate, f"{rows:,} dòng; đổi sang incremental theo `{partition}`")
    if t.materialization != "incremental":
        second = Proposal(RULES[1], None, "chỉ áp cho model `incremental` (model `table` tính lại toàn bộ mỗi lần)")
    elif t.partition_by:
        second = Proposal(RULES[1], None, f"đã có điều kiện trên `{t.partition_by}`")
    elif not time:
        second = Proposal(RULES[1], None, "cảnh báo: model incremental nhưng grain không có cột ngày/giờ để lọc")
    else:
        second = Proposal(RULES[1], _with_transform(spec, partition_by=partition), f"thêm điều kiện `{partition}`")
    return [first, second]


def same_output(first: list[dict] | None, second: list[dict] | None) -> bool:
    """Hai bảng gold giống nhau theo hồ sơ từng cột (số giá trị khác rỗng, tổng, min, max)."""
    if not first or not second:
        return False

    def key(profile: list[dict]) -> list[tuple]:
        return sorted(tuple(str(column.get(k)) for k in PROFILE_KEYS) for column in profile)

    return key(first) == key(second)


def _pct(before: float, after: float) -> float:
    return round((before - after) * 100 / before, 1) if before else 0.0


def _evaluate(proposal: Proposal, baseline, deps, run_id: str, version: int) -> tuple[dict, dict | None]:
    """Một bản ứng viên qua cổng → chạy thử → đo. Trả (bước optimizer, bản thắng hoặc None)."""
    policy, settings = deps.policy, deps.settings
    before, gain = baseline.model_rerun_sec, policy.budgets.optimizer.min_gain_pct
    step = {"rule": proposal.rule, "applied": False, "before_sec": before, "after_sec": None, "reason": None}

    def reject(reason: str) -> tuple[dict, None]:
        return {**step, "reason": reason}, None

    try:
        candidate = Spec.model_validate(proposal.candidate)
    except ValidationError as exc:
        return reject(f"Spec ứng viên không hợp lệ: {str(exc)[:300]}")
    if violations := guard.expression_violations(candidate):
        return reject("ast_guard: " + "; ".join(violations))
    try:
        gate, errors, estimated = budget.check(
            candidate,
            policy,
            settings,
            estimate_cost=deps.estimate_cost,
            spent_fn=deps.spent_today,
            reserve=deps.reserve_daily,
            run_id=run_id,
            version=version,
            category=f"optimize:{proposal.rule}",
        )
    except SourceUnavailableError as exc:
        return reject(f"budget: không ước tính được chi phí ({exc})")
    if errors:
        return reject("budget: " + "; ".join(errors))
    try:
        files = render(candidate, policy)
    except RenderError as exc:
        return reject(f"render lỗi: {exc}")
    result = call_sandbox(
        deps.sandbox, candidate, files, policy, settings, rerun_model=policy.budgets.optimizer.rerun_samples
    )
    if result.status != "READY_FOR_REVIEW":
        last = result.error_trace[-1].get("error", "") if result.error_trace else ""
        return reject(f"chạy thử không đạt ({result.status}): {str(last)[:300]}")
    if result.model_rerun_sec is None:
        return reject(f"không đo được thời gian chạy lại: {result.rerun_error or 'không rõ'}")
    step["after_sec"] = result.model_rerun_sec
    if not same_output(baseline.gold_profile, result.gold_profile) or not same_output(
        baseline.gold_profile, result.rerun_profile
    ):
        return reject("bảng gold khác bản gốc (sau lần build đầu hoặc sau khi chạy lại): bản ứng viên sai")
    limit = before * (1 - gain / 100)
    if result.model_rerun_sec > limit:
        return reject(
            f"không nhanh hơn đủ: chạy lại {result.model_rerun_sec:g}s so với bản gốc {before:g}s "
            f"(cần ≤ {limit:.4g}s, budgets.yml optimizer.min_gain_pct = {gain:g})"
        )
    samples = result.rerun_samples or []
    trimmed = sorted(samples)[:-1] if len(samples) >= 3 else samples  # bỏ một mẫu chậm nhất (nhiễu lẻ)
    if baseline.rerun_samples and trimmed and max(trimmed) >= min(baseline.rerun_samples):
        dropped = " sau khi bỏ 1 mẫu chậm nhất" if len(trimmed) < len(samples) else ""
        return reject(
            f"chênh lệch nằm trong nhiễu đo: các lần chạy lại của bản ứng viên {samples}{dropped} vẫn chồng lên "
            f"bản gốc {baseline.rerun_samples} (các lần còn lại của bản ứng viên phải nhanh hơn mọi lần của bản gốc)"
        )
    applied = {
        **step,
        "applied": True,
        "reason": f"nhanh hơn {_pct(before, result.model_rerun_sec)}%, bảng gold giống hệt bản gốc",
    }
    winner = {"spec": proposal.candidate, "files": files, "result": result, "gate": gate, "estimated": estimated}
    return applied, winner


def apply_winner(state: dict, best: dict, steps: list[dict]) -> dict:
    """Phần cập nhật state của run khi có bản thắng: Spec, bộ file, bằng chứng chạy thử của bản ứng viên; lịch sử Spec
    ghi lý do đổi. Dùng chung cho P13 trong graph (src/agents/nodes/optimize.py) và UC-3 (src/agents/manual_optimize.py).
    """
    hashes, files_hash = file_hashes(best["files"])
    result, estimated = best["result"], best["estimated"]
    applied = next(s for s in steps if s["applied"] and s["after_sec"] == result.model_rerun_sec)
    kept = [g for g in state.get("gates", []) if g.get("name") != "post_run"]
    previous = state.get("cost") or {"estimated_bytes": 0, "actual_bytes": 0, "usd": 0}
    return {
        "spec": best["spec"],
        "generated_files": best["files"],
        "files": hashes,
        "files_hash": files_hash,
        "checks": [check.model_dump() for check in result.checks],
        "gates": [*kept, best["gate"].model_dump(), *(gate.model_dump() for gate in result.gates)],
        "cost": {
            "estimated_bytes": previous.get("estimated_bytes", 0) + estimated["bytes"],
            "actual_bytes": previous.get("actual_bytes", 0) + estimated["bytes"],
            "usd": previous.get("usd", 0) + estimated["usd"],
        },
        "optimized_sec": result.duration_sec,
        "spec_history": [
            *state.get("spec_history", []),
            {"version": state.get("version", 1), "reason": f"P13 {applied['rule']}: {applied['reason']}"},
        ],
        "optimizer": steps,
        "stage_reached": "optimize",
    }


def optimize(spec: dict, baseline, deps, *, rows: int | None, run_id: str, version: int) -> dict:
    """Chạy P13 trên Spec đã chạy thử đạt. Trả {"steps": [OptimizerStep], "best": bản thắng nhanh nhất | None}.

    `baseline` là kết quả chạy thử bản gốc có đo chạy lại (`rerun_model`), hoặc một callable trả kết quả đó: chỉ gọi khi
    có luật áp, để pipeline nhỏ không tốn thêm một lần chạy thử.
    """
    proposals = propose(Spec.model_validate(spec), rows, deps.policy)
    skipped = [{"rule": p.rule, "applied": False, "reason": p.reason} for p in proposals if p.candidate is None]
    tried = [p for p in proposals if p.candidate is not None]
    if not tried:
        return {"steps": skipped, "best": None, "baseline": None}
    measured = baseline() if callable(baseline) else baseline
    if measured is None or measured.status != "READY_FOR_REVIEW" or measured.model_rerun_sec is None:
        why = getattr(measured, "rerun_error", None) or getattr(measured, "status", "không chạy được")
        steps = skipped + [{"rule": p.rule, "applied": False, "reason": f"không đo được bản gốc: {why}"} for p in tried]
        return {"steps": steps, "best": None, "baseline": measured}
    steps, winners = list(skipped), []
    for proposal in tried:
        step, winner = _evaluate(proposal, measured, deps, run_id, version)
        steps.append(step)
        if winner:
            winners.append(winner)
    best = min(winners, key=lambda w: w["result"].model_rerun_sec) if winners else None
    ordered = sorted(steps, key=lambda s: RULES.index(s["rule"]))
    return {"steps": ordered, "best": best, "baseline": measured}
