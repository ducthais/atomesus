"""Gọi LLM lấy JSON (SPEC §14.3). Node chỉ phụ thuộc kiểu `JsonLLM`, test thay bằng hàm giả."""

import json
import re
from collections.abc import Callable
from typing import Literal

from src.config import get_settings

Tier = Literal["cheap", "strong"]
# (tier, system, user) → chuỗi trả về của model. LLM gọi nhà cung cấp thật còn có thuộc tính `models` (tier → tên model,
# để tra giá trước khi gọi) và nhận `max_output_tokens=` (trần token đầu ra, xem src/agents/nodes/common.py call_llm).
JsonLLM = Callable[[Tier, str, str], str]

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class LLMReply(str):
    """Nội dung model trả về, kèm model và số token nhà cung cấp báo (để tính chi phí). Dùng như `str` ở mọi chỗ khác."""

    model: str | None
    input_tokens: int | None
    output_tokens: int | None

    def __new__(
        cls, text: str, model: str | None = None, input_tokens: int | None = None, output_tokens: int | None = None
    ):
        reply = super().__new__(cls, text)
        reply.model, reply.input_tokens, reply.output_tokens = model, input_tokens, output_tokens
        return reply


class LLMOutputError(ValueError):
    """Model không trả về một object JSON."""


def parse_json(text: str) -> dict:
    """Bỏ markdown fence nếu có rồi parse; chỉ chấp nhận object JSON."""
    try:
        value = json.loads(FENCE.sub("", text))
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"LLM không trả về JSON hợp lệ: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMOutputError("LLM phải trả về một object JSON")
    return value


def openai_llm() -> JsonLLM:
    """Model rẻ cho lập kế hoạch, model mạnh cho sinh Spec (SPEC §6). Nhiệt độ 0 để kết quả ổn định."""
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    models = {
        "cheap": settings.model_name,
        "strong": settings.model_name_strong or settings.model_name,
    }

    def call(tier: Tier, system: str, user: str, max_output_tokens: int | None = None) -> str:
        model = ChatOpenAI(
            model=models[tier],
            api_key=settings.openai_api_key,
            temperature=0,
            timeout=120,
            max_tokens=max_output_tokens,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        message = model.invoke([("system", system), ("user", user)])
        usage = message.usage_metadata or {}
        return LLMReply(message.content, models[tier], usage.get("input_tokens"), usage.get("output_tokens"))

    call.models = models
    return call
