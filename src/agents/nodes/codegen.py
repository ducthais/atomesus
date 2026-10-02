"""Node `codegen` (P3 resolve + P4 propose, LLM mạnh): kế hoạch + schema thật + dòng mẫu → Spec (SPEC §14.2).

Spec được kiểm tra ngay (`src/agents/validate.py`). Sai `spec_schema`/`schema_real` thì gọi lại LLM kèm
lỗi cụ thể, tối đa `budgets.yml: per_run.max_spec_attempts` lần (vòng spec, SPEC §11.3); hết lượt → NEEDS_HUMAN.
Biểu thức nguy hiểm → BLOCKED_AST, không thử lại.
"""

import json

from src.agents.llm import LLMOutputError, parse_json
from src.agents.nodes.common import AgentDeps, StopError, call_llm, prompt
from src.agents.resolve import ResolveError
from src.agents.state import AgentState
from src.agents.validate import validate_spec
from src.pipeline.postgres import SourceUnavailableError
from src.spec import SPEC_FIXTURES_DIR

EXAMPLE_SPEC = SPEC_FIXTURES_DIR / "spec_02_sales_multi_source.json"


def _system_prompt() -> str:
    return prompt("codegen").replace("{example}", EXAMPLE_SPEC.read_text(encoding="utf-8"))


def _user_message(state: AgentState, deps: AgentDeps, snapshot: dict) -> str:
    domain = deps.policy.domains.domains[state["plan"]["domain"]]
    message = {
        "yêu_cầu": state["user_request"],
        "kế_hoạch": state["plan"],
        "schema_snapshot": snapshot,
        "chính_sách": {
            "domain": state["plan"]["domain"],
            "tiền_gốc": domain.currency.base,
            "tỷ_giá": domain.currency.rates,
            "nguồn_được_phép": {ref: decl.kind for ref, decl in domain.sources.items()},
        },
    }
    if state.get("feedback"):
        message["yêu_cầu_sửa"] = (
            "Spec lần trước bị từ chối vì các lỗi trong `lỗi_lần_trước`. Trả về Spec đầy đủ đã sửa TẤT CẢ các lỗi đó, "
            "giữ nguyên phần đúng. Không lặp lại giá trị đã bị báo sai."
        )
        message["lỗi_lần_trước"] = state["feedback"]
        message["spec_lần_trước"] = state.get("spec")
    return json.dumps(message, ensure_ascii=False)


def codegen(deps: AgentDeps):
    def node(state: AgentState) -> dict:
        calls = state.get("llm_calls", 0)
        attempts = dict(state.get("attempts") or {"spec": 0, "sql": 0})
        snapshot = state.get("schema_snapshot")
        try:
            if not snapshot:
                snapshot = deps.snapshot(state["plan"], deps.policy, deps.settings)
            text = call_llm(state, deps, "propose", "strong", _system_prompt(), _user_message(state, deps, snapshot))
            calls += 1
        except ResolveError as exc:
            return StopError("NEEDS_HUMAN", "resolve", str(exc)).update(state)
        except SourceUnavailableError as exc:
            return StopError("INFRA_ERROR", "resolve", str(exc)).update(state)
        except StopError as stop:
            return stop.update(state, llm_calls=calls, schema_snapshot=snapshot)

        try:
            raw = parse_json(text)
            result = validate_spec(raw, snapshot, deps.policy, state["user_request"])
            errors, gates = result.errors, [gate.model_dump() for gate in result.gates]
        except LLMOutputError as exc:
            raw, result, errors, gates = None, None, [str(exc)], []
        history = [
            *state.get("spec_history", []),
            {"attempt": len(state.get("spec_history", [])) + 1, "spec": raw, "errors": errors},
        ]
        update = {"schema_snapshot": snapshot, "llm_calls": calls, "spec_history": history, "gates": gates, "spec": raw}

        if result is not None and result.blocked:
            return StopError("BLOCKED_AST", "static", "; ".join(errors)).update(state, **update)
        if errors:
            attempts["spec"] += 1
            limit = deps.policy.budgets.per_run.max_spec_attempts
            if attempts["spec"] > limit:
                message = f"Spec vẫn sai sau {limit} lần sửa (budgets.yml max_spec_attempts): " + "; ".join(errors)
                return StopError("NEEDS_HUMAN", "static", message).update(state, attempts=attempts, **update)
            return {**update, "attempts": attempts, "feedback": errors, "stage_reached": "static"}
        spec = result.spec.model_dump(by_alias=True, mode="json")
        return {
            **update,
            "spec": spec,
            "feedback": None,
            "attempts": attempts,
            "status": "VALIDATING",
            "stage_reached": "static",
        }

    return node
