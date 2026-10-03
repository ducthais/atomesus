"""Phần dùng chung của các node: phụ thuộc được tiêm vào, trần gọi LLM, trạng thái kết thúc."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.agents import resolve
from src.agents.llm import JsonLLM
from src.agents.state import AgentState
from src.codegen.sandbox import run_in_sandbox
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
# Trạng thái còn đang chạy; mọi trạng thái khác là kết thúc, graph đi thẳng tới END.
RUNNING = frozenset({"DRAFT", "GENERATING", "VALIDATING"})


@dataclass
class AgentDeps:
    llm: JsonLLM
    policy: Policy
    settings: PipelineSettings
    catalog: Callable = resolve.catalog
    snapshot: Callable = resolve.snapshot
    sandbox: Callable = run_in_sandbox
    checkpointer: object = None


class StopError(Exception):
    """Dừng run ở phase hiện tại với trạng thái kết thúc và lý do ghi vào error_trace."""

    def __init__(self, status: str, stage: str, error: str, **extra):
        super().__init__(error)
        self.status, self.stage, self.trace = status, stage, {"stage": stage, "error": error, **extra}

    def update(self, state: AgentState, **extra) -> dict:
        """Phần cập nhật state khi dừng: trạng thái, phase, thêm một dòng error_trace."""
        trace = [*state.get("error_trace", []), self.trace]
        return {"status": self.status, "stage_reached": self.stage, "error_trace": trace, **extra}


def prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def call_llm(state: AgentState, deps: AgentDeps, stage: str, tier: str, system: str, user: str) -> str:
    """Gọi LLM nếu còn trong trần `budgets.yml: per_run.max_llm_calls`; chạm trần → NEEDS_HUMAN (SPEC §11.4)."""
    limit = deps.policy.budgets.per_run.max_llm_calls
    if state.get("llm_calls", 0) >= limit:
        raise StopError("NEEDS_HUMAN", stage, f"Đã chạm trần {limit} lần gọi LLM của run (budgets.yml max_llm_calls).")
    try:
        return deps.llm(tier, system, user)
    except Exception as exc:  # lỗi mạng/khóa API của nhà cung cấp LLM
        raise StopError("INFRA_ERROR", stage, f"Gọi LLM thất bại: {type(exc).__name__}: {str(exc)[:300]}") from exc
