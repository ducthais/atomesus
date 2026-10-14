"""Phần dùng chung của các node: phụ thuộc được tiêm vào, trần gọi LLM, trạng thái kết thúc.

Trần thời gian mỗi phase nằm ở src/agents/timeout.py.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.agents import budget, resolve
from src.agents.cancellation import raise_if_phase_cancelled
from src.agents.llm import JsonLLM
from src.agents.state import AgentState
from src.codegen.sandbox import run_in_sandbox
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
# Trạng thái còn đang chạy; mọi trạng thái khác là kết thúc, graph đi thẳng tới END.
RUNNING = frozenset({"DRAFT", "GENERATING", "VALIDATING"})
# Token định dạng tin nhắn nhà cung cấp cộng thêm ngoài nội dung prompt (vài token mỗi tin nhắn), lấy dư.
MESSAGE_OVERHEAD_TOKENS = 64


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
    reserve_daily: Callable | None = None
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


def llm_bound(deps: AgentDeps, stage: str, tier: str, system: str, user: str) -> tuple[float, dict]:
    """Khoản giữ chỗ cho một lần gọi LLM và tham số gọi đi kèm, sao cho chi phí thật không vượt khoản giữ chỗ.

    LLM gọi nhà cung cấp thật (có `models`): giá input của cận trên số token prompt cộng giá trần token đầu ra
    `per_run.max_output_tokens`, và gọi kèm đúng trần đó. Model không có giá trong `pricing.llm` thì không gọi, vì
    không chặn được chi phí. LLM giả trong test (không có `models`, không tốn tiền) giữ `per_run.max_usd` như trước.
    """
    budgets = deps.policy.budgets
    models = getattr(deps.llm, "models", None)
    if models is None:
        return budgets.per_run.max_usd, {}
    model = models[tier]
    price = budgets.pricing.llm.get(model)
    if price is None:
        raise StopError(
            "BLOCKED_BUDGET",
            stage,
            f"Model {model} chưa có giá trong budgets.yml pricing.llm; không gọi LLM vì không giới hạn được chi phí.",
        )
    # Mỗi token của bộ tách byte-level BPE phủ ít nhất một byte UTF-8: số byte prompt là cận trên số token đầu vào.
    tokens_in = len(system.encode()) + len(user.encode()) + MESSAGE_OVERHEAD_TOKENS
    tokens_out = budgets.per_run.max_output_tokens
    usd = (tokens_in * price.input_usd_per_mtok + tokens_out * price.output_usd_per_mtok) / 1_000_000
    return usd, {"max_output_tokens": tokens_out}


def call_llm(state: AgentState, deps: AgentDeps, stage: str, tier: str, system: str, user: str) -> str:
    """Gọi LLM nếu còn trong trần `budgets.yml: per_run.max_llm_calls`; chạm trần → NEEDS_HUMAN (SPEC §11.4)."""
    raise_if_phase_cancelled()
    if deps.cancelled and deps.cancelled(state["run_id"]):
        raise CancellationRequestedError
    limit = deps.policy.budgets.per_run.max_llm_calls
    if state.get("llm_calls", 0) >= limit:
        raise StopError("NEEDS_HUMAN", stage, f"Đã chạm trần {limit} lần gọi LLM của run (budgets.yml max_llm_calls).")
    usd, bound = llm_bound(deps, stage, tier, system, user)
    # Cổng batch có thể đã giữ chỗ cho đúng lần gọi này. Thử giữ chỗ idempotent trước khi kết luận ngày đã hết tiền:
    # reserve_daily nhận ra reservation cùng (run, version, category) và cho phép dùng nó ngay cả khi tổng đã chạm trần,
    # chỉ giữ thêm phần chênh nếu khoản cũ nhỏ hơn cận trên của lần gọi này.
    # Cùng giao dịch giữ chỗ xét cả trần cá nhân của chủ run (per_user.max_usd_per_day, ADR-0001 D10).
    day_limit = deps.policy.budgets.per_day.max_usd
    category = f"llm:{stage}:{state.get('llm_calls', 0) + 1}"
    if deps.reserve_daily:
        reserved, spent, user_spent = deps.reserve_daily(
            deps.settings,
            state["run_id"],
            state.get("version", 1),
            category,
            usd,
            day_limit,
            deps.policy.budgets.per_user.max_usd_per_day,
        )
        if not reserved and user_spent is not None:
            raise StopError(
                "BLOCKED_BUDGET",
                stage,
                budget.user_limit_error(user_spent, usd, deps.policy) + "; không gọi LLM nữa tới 0:00 UTC.",
            )
        if not reserved:
            raise StopError(
                "BLOCKED_BUDGET",
                stage,
                f"Hôm nay đã dùng hoặc giữ chỗ {spent:.4f} USD; không đủ trần chung {day_limit} USD/ngày "
                "(budgets.yml per_day.max_usd) cho lần gọi LLM.",
            )
    else:
        # Các dependency không có sổ reservation (ví dụ adapter/test tối giản) vẫn phải chặn trước khi gọi nhà cung cấp.
        spent = deps.spent_today(deps.settings)
        if spent >= day_limit:
            raise StopError(
                "BLOCKED_BUDGET",
                stage,
                f"Hôm nay đã tiêu {spent:.4f} USD, chạm trần {day_limit} USD/ngày (budgets.yml per_day.max_usd); "
                "không gọi LLM nữa tới ngày mai (UTC).",
            )
    try:
        reply = deps.llm(tier, system, user, **bound)
        raise_if_phase_cancelled()
        return reply
    except Exception as exc:  # lỗi mạng/khóa API của nhà cung cấp LLM
        raise StopError("INFRA_ERROR", stage, f"Gọi LLM thất bại: {type(exc).__name__}: {str(exc)[:300]}") from exc
