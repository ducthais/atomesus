"""Hợp đồng của chat: body/response của POST /chat và một tin nhắn trong lịch sử run."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.run_report.models import RunReport

Intent = Literal["new_request", "ask"]
Role = Literal["user", "assistant"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # không nhận trường danh tính từ client (FR-01)
    message: str = Field(min_length=1, max_length=2000)
    run_id: str | None = None


class ChatResponse(BaseModel):
    intent: Intent
    reply: str
    run_id: str
    report: RunReport


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: int
    run_id: str
    role: Role
    author: str
    intent: Intent
    content: str
    at: datetime
