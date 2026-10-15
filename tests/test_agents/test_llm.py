from types import SimpleNamespace

import langchain_openai
import pytest

from src.agents import llm


def test_openai_llm_bounds_retry_budget_and_propagates_failure(monkeypatch):
    calls = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def invoke(self, _messages):
            raise TimeoutError("provider timeout")

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(model_name="cheap-model", model_name_strong="strong-model", openai_api_key="fake"),
    )

    client = llm.openai_llm()
    with pytest.raises(TimeoutError, match="provider timeout"):
        client("cheap", "system", "user")

    assert calls[0]["timeout"] == 120
    assert calls[0]["max_retries"] == 1
    assert calls[0]["timeout"] * (calls[0]["max_retries"] + 1) < 300
