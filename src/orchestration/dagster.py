"""Trạng thái pipeline đã deploy, đọc từ GraphQL của Dagster (dagster-webserver, `make dagster`).

Chỉ đọc: lần materialize gần nhất của từng asset, run gần nhất của job chứa asset đó, và lịch chạy
(bật/tắt, lần chạy kế tiếp). Khóa asset là đường dẫn nối bằng "/", ví dụ `gold/fct_daily_sales`, khớp với
tên dataset `gold.fct_daily_sales` trong catalog.
"""

from datetime import UTC, datetime

import httpx

QUERY = """
query PipelineStatus {
  assetsOrError {
    __typename
    ... on AssetConnection {
      nodes { key { path } definition { jobNames } assetMaterializations(limit: 1) { timestamp runId } }
    }
    ... on PythonError { message }
  }
  runsOrError(limit: 100) {
    __typename
    ... on Runs { results { runId jobName status startTime endTime } }
    ... on PythonError { message }
  }
  repositoriesOrError {
    __typename
    ... on RepositoryConnection {
      nodes {
        location { name }
        schedules {
          name cronSchedule executionTimezone pipelineName
          scheduleState { status }
          futureTicks(limit: 1) { results { timestamp } }
        }
      }
    }
    ... on PythonError { message }
  }
}
"""


class DagsterError(Exception):
    """Dagster trả lỗi hoặc dữ liệu không đọc được."""


def _iso(seconds: float | None) -> str | None:
    return datetime.fromtimestamp(seconds, UTC).isoformat() if seconds else None


def _section(data: dict, name: str, ok: str) -> dict:
    section = data.get(name) or {}
    if section.get("__typename") != ok:
        raise DagsterError(f"Dagster trả lỗi ở {name}: {section.get('message', section.get('__typename'))}")
    return section


def parse_status(data: dict) -> dict[str, dict]:
    """Phản hồi GraphQL → {khóa asset: {last_materialized_at, last_run, schedule}}. Job ẩn `__ASSET_JOB` bị bỏ qua."""
    runs = sorted(_section(data, "runsOrError", "Runs")["results"], key=lambda r: r.get("startTime") or 0, reverse=True)
    latest_run = {}
    for run in runs:
        latest_run.setdefault(run["jobName"], run)
    schedules = {}
    for repo in _section(data, "repositoriesOrError", "RepositoryConnection")["nodes"]:
        for schedule in repo["schedules"]:
            ticks = schedule["futureTicks"]["results"]
            running = schedule["scheduleState"]["status"] == "RUNNING"
            schedules.setdefault(
                schedule["pipelineName"],
                {
                    "name": schedule["name"],
                    "cron": schedule["cronSchedule"],
                    "timezone": schedule["executionTimezone"],
                    "running": running,
                    # Lịch đang tắt thì không có lần chạy kế tiếp, dù Dagster vẫn tính được tick.
                    "next_tick_at": _iso(ticks[0]["timestamp"]) if running and ticks else None,
                    "location": repo["location"]["name"],
                },
            )
    assets = {}
    for node in _section(data, "assetsOrError", "AssetConnection")["nodes"]:
        jobs = [job for job in (node.get("definition") or {}).get("jobNames", []) if not job.startswith("__")]
        if not jobs:
            continue  # asset nguồn bên ngoài (ví dụ bảng Postgres), không do pipeline nào tạo
        materialization = (node.get("assetMaterializations") or [None])[0]
        job_runs = [latest_run[job] for job in jobs if job in latest_run]
        run = max(job_runs, key=lambda r: r.get("startTime") or 0) if job_runs else None
        assets["/".join(node["key"]["path"])] = {
            "jobs": jobs,
            "last_materialized_at": _iso(int(materialization["timestamp"]) / 1000) if materialization else None,
            "last_materialized_run_id": materialization["runId"] if materialization else None,
            "last_run": {
                "run_id": run["runId"],
                "job": run["jobName"],
                "status": run["status"],
                "started_at": _iso(run.get("startTime")),
                "ended_at": _iso(run.get("endTime")),
            }
            if run
            else None,
            "schedule": next((schedules[job] for job in jobs if job in schedules), None),
        }
    return assets


def fetch_status(url: str, timeout: float = 3.0) -> dict[str, dict]:
    """Gọi GraphQL của Dagster. Không kết nối được: httpx.HTTPError; phản hồi lỗi: DagsterError."""
    response = httpx.post(f"{url.rstrip('/')}/graphql", json={"query": QUERY}, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise DagsterError("; ".join(error.get("message", "") for error in body["errors"]))
    return parse_status(body["data"])
