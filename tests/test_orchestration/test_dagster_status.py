"""Đọc trạng thái pipeline từ GraphQL của Dagster. Phản hồi mẫu lấy đúng hình dạng Dagster 1.13 trả về."""

from types import SimpleNamespace

import httpx
import pytest

from src.orchestration import dagster

RUN = "0c3774b7-266b-49d5-8cfa-cf94ca099b0f"


def response(schedule_status="STOPPED", runs=None):
    return {
        "assetsOrError": {
            "__typename": "AssetConnection",
            "nodes": [
                {
                    "key": {"path": ["gold", "fct_daily_sales"]},
                    "definition": {"jobNames": ["__ASSET_JOB", "fct_daily_sales_job"]},
                    "assetMaterializations": [{"timestamp": "1790928745474", "runId": RUN}],
                },
                {
                    "key": {"path": ["bronze", "raw_orders"]},
                    "definition": {"jobNames": ["__ASSET_JOB", "fct_daily_sales_job"]},
                    "assetMaterializations": [],
                },
                # Bảng nguồn bên ngoài: không job nào tạo ra, bị bỏ qua.
                {
                    "key": {"path": ["fct_daily_sales_orders"]},
                    "definition": {"jobNames": []},
                    "assetMaterializations": [],
                },
            ],
        },
        "runsOrError": {
            "__typename": "Runs",
            "results": runs
            if runs is not None
            else [
                {
                    "runId": RUN,
                    "jobName": "fct_daily_sales_job",
                    "status": "SUCCESS",
                    "startTime": 1790928707.03,
                    "endTime": 1790928747.62,
                },
                {
                    "runId": "old",
                    "jobName": "fct_daily_sales_job",
                    "status": "FAILURE",
                    "startTime": 1790900000.0,
                    "endTime": 1790900010.0,
                },
            ],
        },
        "repositoriesOrError": {
            "__typename": "RepositoryConnection",
            "nodes": [
                {
                    "location": {"name": "fct_daily_sales"},
                    "schedules": [
                        {
                            "name": "fct_daily_sales_job_schedule",
                            "cronSchedule": "0 2 * * *",
                            "executionTimezone": "Asia/Ho_Chi_Minh",
                            "pipelineName": "fct_daily_sales_job",
                            "scheduleState": {"status": schedule_status},
                            "futureTicks": {"results": [{"timestamp": 1790967600.0}]},
                        }
                    ],
                }
            ],
        },
    }


def test_status_per_asset_uses_latest_run_and_materialization():
    assets = dagster.parse_status(response())
    assert set(assets) == {"gold/fct_daily_sales", "bronze/raw_orders"}
    gold = assets["gold/fct_daily_sales"]
    assert gold["jobs"] == ["fct_daily_sales_job"]
    assert gold["last_materialized_at"].startswith("2026-10-02T") and gold["last_materialized_run_id"] == RUN
    assert gold["last_run"]["run_id"] == RUN and gold["last_run"]["status"] == "SUCCESS"
    assert gold["schedule"]["cron"] == "0 2 * * *" and gold["schedule"]["running"] is False
    assert gold["schedule"]["next_tick_at"] is None, "lịch tắt thì không có lần chạy kế tiếp"
    assert assets["bronze/raw_orders"]["last_materialized_at"] is None


def test_running_schedule_has_next_tick_and_never_run_asset_has_no_run():
    assets = dagster.parse_status(response(schedule_status="RUNNING", runs=[]))
    gold = assets["gold/fct_daily_sales"]
    assert gold["schedule"]["running"] is True and gold["schedule"]["next_tick_at"] == "2026-10-02T19:00:00+00:00"
    assert gold["last_run"] is None


def test_graphql_error_section_is_reported():
    data = response()
    data["runsOrError"] = {"__typename": "PythonError", "message": "boom"}
    with pytest.raises(dagster.DagsterError, match="boom"):
        dagster.parse_status(data)


@pytest.mark.asyncio
async def test_status_endpoint_reports_unavailable_dagster(client, tmp_path, monkeypatch):
    from src.main import app
    from src.pipeline import auth
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"), dagster_url="http://dagster")
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        assert (await client.get("/api/v1/orchestration/status")).status_code == 401
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        monkeypatch.setattr(dagster, "fetch_status", lambda url: dagster.parse_status(response()))
        body = (await client.get("/api/v1/orchestration/status")).json()
        assert body["available"] is True and body["url"] == "http://dagster"
        assert body["assets"]["gold/fct_daily_sales"]["last_run"]["status"] == "SUCCESS"

        def down(url):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(dagster, "fetch_status", down)
        body = (await client.get("/api/v1/orchestration/status")).json()
        assert body["available"] is False and "make dagster" in body["reason"]
    finally:
        app.dependency_overrides.pop(auth.settings, None)
