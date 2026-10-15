from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sales_visible(monkeypatch):
    """Bỏ `hidden` khỏi domains.yml cho riêng một test.

    Domain sales bị ẩn khỏi luồng thao tác mới (catalog, chọn nguồn, tạo run). Test cơ chế dùng sales làm dữ liệu mẫu,
    vì đây là domain duy nhất có đủ ba loại nguồn postgres, filesystem và rest_api, nên bật lại sales qua fixture này.
    """
    from src.policy import loader

    read = loader._read_yaml

    def unhidden(path):
        data = read(path)
        if path.name == "domains.yml":
            for domain in data.get("domains", {}).values():
                domain.pop("hidden", None)
        return data

    monkeypatch.setattr(loader, "_read_yaml", unhidden)


@pytest.fixture
def mock_llm():
    """Mock LLM to avoid calling OpenAI during tests.

    Usage in test:
        def test_something(mock_llm):
            # LLM calls will return mock response instead of hitting OpenAI
            ...
    """
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock
