"""Gọi LLM lấy JSON (SPEC §14.3). Node chỉ phụ thuộc kiểu `JsonLLM`, test thay bằng hàm giả."""

import json
import re
from collections.abc import Callable
from typing import Literal

from src.config import get_settings

Tier = Literal["cheap", "strong"]
# (tier, system, user) → chuỗi trả về của model
JsonLLM = Callable[[Tier, str, str], str]

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


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

    def call(tier: Tier, system: str, user: str) -> str:
        model = ChatOpenAI(
            model=models[tier],
            api_key=settings.openai_api_key,
            temperature=0,
            timeout=120,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        return model.invoke([("system", system), ("user", user)]).content

    return call
