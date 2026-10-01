import pytest

READY_RUN_ID = "3f6c1a2e-8b4d-4c7a-9e21-0d5b7a1c9f10"


@pytest.mark.asyncio
async def test_list_runs_returns_newest_first(client):
    response = await client.get("/api/v1/runs")

    assert response.status_code == 200
    created = [r["created_at"] for r in response.json()]
    assert len(created) == 3
    assert created == sorted(created, reverse=True)


@pytest.mark.asyncio
async def test_list_runs_filters_by_status(client):
    response = await client.get("/api/v1/runs", params={"status": "DEPLOYED"})

    assert response.status_code == 200
    runs = response.json()
    assert [r["status"] for r in runs] == ["DEPLOYED"]
    assert runs[0]["approval"]["reviewer"] == "an"


@pytest.mark.asyncio
async def test_list_runs_rejects_unknown_status(client):
    response = await client.get("/api/v1/runs", params={"status": "DONE"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_run_returns_full_report(client):
    response = await client.get(f"/api/v1/runs/{READY_RUN_ID}")

    assert response.status_code == 200
    report = response.json()
    assert report["run_id"] == READY_RUN_ID
    assert report["created_by"] == "minh"
    assert report["version"] == 3
    assert report["approval"] is None
    for field in ("gates", "checks", "cost", "timing"):
        assert field in report


@pytest.mark.asyncio
async def test_get_run_unknown_id_returns_404(client):
    response = await client.get("/api/v1/runs/does-not-exist")

    assert response.status_code == 404
