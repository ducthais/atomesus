"""Số đo mỗi lần pipeline đã duyệt chạy theo lịch (Day 2, SPEC §9.6.2): thời gian, chi phí, test, độ lớn nguồn.

Sổ bằng chứng chuyển từ "mỗi run một dòng" thành chuỗi thời gian theo từng pipeline. Nguồn số đo:
- Dagster (GraphQL): lần chạy nào của job `<model>_job`, trạng thái, lúc bắt đầu / kết thúc;
- dbt `run_results.json` của lần chạy đó (trong `dbt_project/target` của project đã deploy): số test, số test fail;
- warehouse DuckDB: số dòng bảng gold sau lần chạy;
- chi phí: cùng cách ước tính của cổng budget (byte quét quy ra USD, DuckDB không có chi phí thật);
- số bản ghi hiện có ở từng nguồn của pipeline, để biết nguồn nào ngừng cấp dữ liệu (không có bản ghi mới).
Dòng đầu của chuỗi là đường cơ sở: số đo lúc chạy thử của run tạo ra pipeline (RunReport đã DEPLOYED).

Chỉ đọc: không chạy, không bật / tắt pipeline nào. Thu lại nhiều lần không ghi trùng (khóa là run id của Dagster).
"""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.agents import budget
from src.codegen import el_checks
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy
from src.run_report.models import RunReport
from src.run_report.store import list_run_reports
from src.spec.models import Spec

JOB_SUFFIX = "_job"
FINISHED = {"SUCCESS", "FAILURE", "CANCELED"}
RESULTS_SLACK_SEC = 120  # run_results.json được ghi ngay sau khi dbt xong
RUNS_QUERY = """
query Runs {
  runsOrError(limit: 200) {
    __typename
    ... on Runs { results { runId jobName status startTime endTime } }
    ... on PythonError { message }
  }
}
"""
COLUMNS = (
    "run_id",
    "model",
    "status",
    "started_at",
    "ended_at",
    "duration_sec",
    "gold_rows",
    "estimated_bytes",
    "usd",
    "tests",
    "tests_failed",
    "source_records",
    "collected_at",
)


class MonitorError(Exception):
    """Dagster không trả lời hoặc trả lỗi."""


@contextmanager
def _database(path: str) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pipeline_runs ("
                " run_id TEXT PRIMARY KEY, model TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,"
                " ended_at TEXT, duration_sec REAL, gold_rows INTEGER, estimated_bytes INTEGER, usd REAL,"
                " tests INTEGER, tests_failed INTEGER, source_records TEXT NOT NULL, collected_at TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS pipeline_runs_model ON pipeline_runs (model, started_at)")
            yield conn
    finally:
        conn.close()


def fetch_runs(url: str, timeout: float = 5.0) -> list[dict]:
    try:
        response = httpx.post(f"{url.rstrip('/')}/graphql", json={"query": RUNS_QUERY}, timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MonitorError(f"Không đọc được Dagster ở {url}: {type(exc).__name__}") from exc
    section = (body.get("data") or {}).get("runsOrError") or {}
    if body.get("errors") or section.get("__typename") != "Runs":
        raise MonitorError(f"Dagster trả lỗi: {body.get('errors') or section.get('message')}")
    return section["results"]


def _iso(seconds: float | None) -> str | None:
    return datetime.fromtimestamp(seconds, UTC).isoformat() if seconds else None


def dbt_results(project: Path, started: float, ended: float) -> dict | None:
    """`run_results.json` mới nhất ghi trong khoảng thời gian của lần chạy → số test và số test fail."""
    window = (started - 5, ended + RESULTS_SLACK_SEC)
    candidates = [
        p
        for p in (project / "dbt_project" / "target").rglob("run_results.json")
        if window[0] <= p.stat().st_mtime <= window[1]
    ]
    if not candidates:
        return None
    results = json.loads(max(candidates, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8"))["results"]
    tests = [r for r in results if r["unique_id"].startswith("test.")]
    return {"tests": len(tests), "tests_failed": sum(r["status"] in ("fail", "error") for r in tests)}


def gold_rows(warehouse: str, model: str) -> int | None:
    """Số dòng gold. Dagster đang ghi (khóa file) hoặc bảng chưa có thì None."""
    try:
        import duckdb

        with duckdb.connect(warehouse, read_only=True) as db:
            return db.execute(f'select count(*) from gold."{model}"').fetchone()[0]  # noqa: S608
    except Exception:
        return None


def deployed_report(store_path: str, model: str) -> RunReport | None:
    """RunReport đã duyệt và deploy gần nhất của pipeline: Spec đang chạy và đường cơ sở."""
    for report in list_run_reports(store_path, "DEPLOYED", limit=1000):
        if isinstance(report.spec, dict) and (report.spec.get("transform") or {}).get("model_name") == model:
            return report
    return None


def source_records(spec: Spec, policy: Policy, settings: PipelineSettings) -> dict[str, int]:
    """Số bản ghi hiện có ở từng nguồn của pipeline (đọc thẳng nguồn, như phép đối soát E-L)."""
    declared = policy.domains.domains[spec.domain].sources
    return {s.id: el_checks.read_source(s, declared[s.source_ref], settings).records for s in spec.sources}


def recorded(store_path: str) -> set[str]:
    with _database(store_path) as conn:
        return {row[0] for row in conn.execute("SELECT run_id FROM pipeline_runs")}


def collect(
    settings: PipelineSettings,
    policy: Policy,
    runs: Callable[[str], list[dict]] | None = None,
    estimate: Callable | None = None,
    counts: Callable | None = None,
) -> list[dict]:
    """Ghi số đo của các lần chạy đã xong mà chưa ghi. Trả các dòng vừa ghi.

    `runs` / `estimate` / `counts` mặc định là fetch_runs, budget.estimate, source_records (chọn lúc gọi).
    """
    runs, estimate, counts = runs or fetch_runs, estimate or budget.estimate, counts or source_records
    root = Path(settings.deploy_dir)
    deployed = (
        {p.name for p in root.iterdir() if p.is_dir() and not p.name.endswith(".tmp")} if root.exists() else set()
    )
    done = recorded(settings.store_path)
    pending = [
        r
        for r in runs(settings.dagster_url)
        if r["jobName"].endswith(JOB_SUFFIX)
        and r["jobName"].removesuffix(JOB_SUFFIX) in deployed
        and r["status"] in FINISHED
        and r["runId"] not in done
        and r.get("startTime")
    ]
    measured: dict[str, tuple[dict, dict]] = {}  # chi phí và độ lớn nguồn đo một lần cho mỗi pipeline
    rows = []
    for run in sorted(pending, key=lambda r: r["startTime"]):
        model = run["jobName"].removesuffix(JOB_SUFFIX)
        if model not in measured:
            report = deployed_report(settings.store_path, model)
            spec = Spec.model_validate(report.spec) if report else None
            try:
                cost = estimate(spec, policy, settings) if spec else {}
                sizes = counts(spec, policy, settings) if spec else {}
            except Exception as exc:  # nguồn tạm không đọc được: vẫn ghi thời gian chạy
                cost, sizes = {}, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            measured[model] = (cost, sizes)
        cost, sizes = measured[model]
        ended = run.get("endTime") or run["startTime"]
        tests = dbt_results(Path(settings.deploy_dir) / model, run["startTime"], ended) or {}
        rows.append(
            {
                "run_id": run["runId"],
                "model": model,
                "status": run["status"],
                "started_at": _iso(run["startTime"]),
                "ended_at": _iso(run.get("endTime")),
                "duration_sec": round(ended - run["startTime"], 3),
                "gold_rows": gold_rows(settings.warehouse_path, model),
                "estimated_bytes": cost.get("bytes"),
                "usd": cost.get("usd"),
                "tests": tests.get("tests"),
                "tests_failed": tests.get("tests_failed"),
                "source_records": sizes,
                "collected_at": datetime.now(UTC).isoformat(),
            }
        )
    with _database(settings.store_path) as conn:
        conn.executemany(
            f"INSERT OR IGNORE INTO pipeline_runs VALUES ({', '.join('?' for _ in COLUMNS)})",
            [[json.dumps(row[c]) if c == "source_records" else row[c] for c in COLUMNS] for row in rows],
        )
    return rows


def series(store_path: str, model: str) -> dict:
    """Chuỗi số đo của một pipeline, cũ trước: đường cơ sở + từng lần chạy, kèm số bản ghi mới ở mỗi nguồn."""
    with _database(store_path) as conn:
        found = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM pipeline_runs WHERE model = ? ORDER BY started_at", (model,)
        ).fetchall()
    runs, previous = [], None
    for values in found:
        row = dict(zip(COLUMNS, values, strict=True))
        row["source_records"] = json.loads(row["source_records"])
        if previous is not None and "error" not in row["source_records"] and "error" not in previous:
            row["new_records"] = {k: v - previous.get(k, 0) for k, v in row["source_records"].items()}
        else:
            row["new_records"] = None
        previous = row["source_records"]
        runs.append(row)
    report = deployed_report(store_path, model)
    baseline = (
        {
            "run_id": report.run_id,
            "at": report.created_at.isoformat(),
            "duration_sec": report.timing.baseline_sec,
            "estimated_bytes": report.cost.estimated_bytes,
            "usd": report.cost.usd,
        }
        if report
        else None
    )
    return {"model": model, "baseline": baseline, "runs": runs}
