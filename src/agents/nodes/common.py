"""Phần dùng chung của các node: phụ thuộc được tiêm vào, trần gọi LLM, trạng thái kết thúc.

Trần thời gian mỗi phase nằm ở src/agents/timeout.py.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.agents import budget, resolve
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
    estimate_cost: Callable = budget.estimate
    spent_today: Callable = budget.spent_today
    checkpointer: object = None
    cancelled: Callable[[str], bool] | None = None


class CancellationRequestedError(Exception):
    """Run đã được Engineer hủy; dừng trước lần gọi LLM hoặc phase kế tiếp."""


class StopError(Exception):
    """Dừng run ở phase hiện tại với trạng thái kết thúc và lý do ghi vào error_trace."""

    def __init__(self, status: str, stage: str, error: str, **extra):
        super().__init__(error)
        self.status, self.stage, self.trace = status, stage, {"stage": stage, "error": error, **extra}

    def update(self, state: AgentState, **extra) -> dict:
        """Phần cập nhật state khi dừng: trạng thái, phase, thêm một dòng error_trace."""
        trace = [*state.get("error_trace", []), self.trace]
        return {"status": self.status, "stage_reached": self.stage, "error_trace": trace, **extra}


Node = Callable[[AgentState], dict]  # đọc state, trả phần cập nhật


def prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def llm_usage(reply: str, stage: str, tier: str, policy: Policy) -> dict:
    """Một lần gọi LLM → {stage, tier, model, input_tokens, output_tokens, usd}.

    Giá đọc từ `budgets.yml: pricing.llm` theo tên model. Model không có trong bảng giá hoặc nhà cung cấp không báo số
    token thì `usd` là None (không đoán giá).
    """
    model = getattr(reply, "model", None)
    tokens_in, tokens_out = getattr(reply, "input_tokens", None), getattr(reply, "output_tokens", None)
    price = policy.budgets.pricing.llm.get(model) if model else None
    usd = None
    if price is not None and tokens_in is not None and tokens_out is not None:
        usd = (tokens_in * price.input_usd_per_mtok + tokens_out * price.output_usd_per_mtok) / 1_000_000
    return {
        "stage": stage,
        "tier": tier,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "usd": usd,
    }


def call_llm(state: AgentState, deps: AgentDeps, stage: str, tier: str, system: str, user: str) -> str:
    """Gọi LLM nếu còn trong trần `budgets.yml: per_run.max_llm_calls`; chạm trần → NEEDS_HUMAN (SPEC §11.4)."""
    if deps.cancelled and deps.cancelled(state["run_id"]):
        raise CancellationRequestedError
    limit = deps.policy.budgets.per_run.max_llm_calls
    if state.get("llm_calls", 0) >= limit:
        raise StopError("NEEDS_HUMAN", stage, f"Đã chạm trần {limit} lần gọi LLM của run (budgets.yml max_llm_calls).")
    # Trần tiền trong ngày chặn cả trước khi gọi LLM, không chỉ ở cổng budget (sau khi Spec đã viết xong): ngày đã tiêu
    # hết thì không gửi thêm request nào tới nhà cung cấp LLM.
    day_limit, spent = deps.policy.budgets.per_day.max_usd, deps.spent_today(deps.settings)
    if spent >= day_limit:
        raise StopError(
            "BLOCKED_BUDGET",
            stage,
            f"Hôm nay đã tiêu {spent:.4f} USD, chạm trần {day_limit} USD/ngày (budgets.yml per_day.max_usd); "
            "không gọi LLM nữa tới ngày mai (UTC).",
        )
    try:
        return deps.llm(tier, system, user)
    except Exception as exc:  # lỗi mạng/khóa API của nhà cung cấp LLM
        raise StopError("INFRA_ERROR", stage, f"Gọi LLM thất bại: {type(exc).__name__}: {str(exc)[:300]}") from exc
