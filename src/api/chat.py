"""POST /chat — cửa vào hội thoại của agent (SPEC §14.5, FR-27), bản Sprint 1.

- Không có `run_id`: `new_request`, chạy graph như `POST /agent/runs` (chỉ Engineer).
- Có `run_id`: `ask`, trả lời tất định từ RunReport, không đổi trạng thái run, trần `max_chat_calls`.
Chat không duyệt, không deploy: cổng người chỉ qua nút bấm.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.agents import chat
from src.agents.nodes.common import AgentDeps
from src.agents.service import run_agent
from src.api.routes import agent_deps
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.run_report.models import RunReport
from src.run_report.store import count_messages, get_run_report, save_message

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    run_id: str | None = None


class ChatResponse(BaseModel):
    intent: Literal["new_request", "ask"]
    reply: str
    run_id: str
    report: RunReport


@router.post("", response_model=ChatResponse)
def post_chat(
    body: ChatRequest,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> ChatResponse:
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "Tin nhắn không được để trống.")
    if body.run_id is None:
        return _new_request(message, user, deps, config.store_path)
    return _ask(message, body.run_id, user, deps, config.store_path)


def _new_request(message: str, user: dict, deps: AgentDeps, store_path: str) -> ChatResponse:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được tạo pipeline từ chat.")
    report = run_agent(message, user["id"], deps, store_path)
    reply = chat.created_reply(report)
    save_message(store_path, report.run_id, "user", user["id"], "new_request", message)
    save_message(store_path, report.run_id, "assistant", "agent", "new_request", reply)
    return ChatResponse(intent="new_request", reply=reply, run_id=report.run_id, report=report)


def _ask(message: str, run_id: str, user: dict, deps: AgentDeps, store_path: str) -> ChatResponse:
    report = get_run_report(store_path, run_id)
    if report is None:
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    limit = deps.policy.budgets.per_run.max_chat_calls
    if count_messages(store_path, run_id, "user", "ask") >= limit:
        raise HTTPException(429, f"Run đã dùng hết {limit} câu hỏi (budgets.yml: per_run.max_chat_calls).")
    reply = chat.answer(report, message)
    save_message(store_path, run_id, "user", user["id"], "ask", message)
    save_message(store_path, run_id, "assistant", "agent", "ask", reply)
    return ChatResponse(intent="ask", reply=reply, run_id=run_id, report=report)
