import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/agent/status")
    assert response.status_code == 200
    assert response.json()["graph"] == ["planner", "codegen", "render", "test_run"]
