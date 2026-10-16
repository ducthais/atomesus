"""Spec mẫu domain rental cho test runtime Dagster: pipeline review_api ⋈ pagila_pg và một pipeline chỉ đọc Pagila.

Cả hai cùng đọc bảng `pagila.rental` vào bronze `raw_rental`, để kiểm tiền tố asset key và credential theo từng model.
"""

import os
import subprocess
import sys
from pathlib import Path

from src.codegen.render import render
from src.codegen.sandbox import prepare_project
from src.policy.loader import load_policy
from src.spec import Spec

REVIEW_MODEL = "fct_review_by_customer"
RENTAL_MODEL = "fct_rental_daily"


def _rental(columns: list[dict]) -> dict:
    return {
        "id": "rentals",
        "kind": "postgres",
        "source_ref": "pagila_pg",
        "table": "rental",
        "primary_key": ["rental_id"],
        "cursor_field": "last_update",
        "write_disposition": "merge",
        "target": {"dataset": "bronze", "table": "raw_rental"},
        "staging": {"columns": columns, "dedupe": {"keys": ["rental_id"], "keep": "latest", "order_by": "last_update"}},
    }


def review_spec() -> dict:
    feedback = {
        "id": "feedback",
        "kind": "rest_api",
        "source_ref": "review_api",
        "endpoint": "/feedback",
        "records_path": "data",
        "pagination": "page",
        "primary_key": ["feedback_id"],
        "cursor_field": "submitted_at",
        "write_disposition": "merge",
        "target": {"dataset": "bronze", "table": "raw_feedback"},
        "staging": {
            "columns": [
                {"from": "feedback_id", "as": "feedback_id", "type": "varchar"},
                {"from": "rental_id", "as": "rental_id", "type": "bigint"},
                {"from": "rating", "as": "rating", "type": "varchar"},
            ],
            "dedupe": {"keys": ["feedback_id"], "keep": "latest", "order_by": "feedback_id"},
        },
    }
    columns = [
        {"from": "rental_id", "as": "rental_id", "type": "bigint"},
        {"from": "customer_id", "as": "customer_id", "type": "bigint"},
    ]
    return {
        "spec_version": "1.2",
        "domain": "rental",
        "sources": [_rental(columns), feedback],
        "transform": {
            "model_name": REVIEW_MODEL,
            "from": "rentals",
            "joins": [{"source": "feedback", "type": "left", "on": [["rentals.rental_id", "feedback.rental_id"]]}],
            "materialization": "table",
            "grain": ["rentals.customer_id"],
            "metrics": [{"name": "review_count", "expr": "COUNT(feedback.feedback_id)"}],
            "filters": [],
            "tests": [],
        },
        "schedule": {"cron": "0 3 * * *"},
        "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
    }


def rental_spec() -> dict:
    columns = [
        {"from": "rental_id", "as": "rental_id", "type": "bigint"},
        {"from": "rental_date", "as": "rental_date", "type": "date"},
    ]
    return {
        "spec_version": "1.2",
        "domain": "rental",
        "sources": [_rental(columns)],
        "transform": {
            "model_name": RENTAL_MODEL,
            "from": "rentals",
            "joins": [],
            "materialization": "table",
            "grain": ["rentals.rental_date"],
            "metrics": [{"name": "rental_count", "expr": "COUNT(rentals.rental_id)"}],
            "filters": [],
            "tests": [],
        },
        "schedule": {"cron": "0 2 * * *"},
        "estimates": {"bytes_scanned": 0, "usd": 0.0, "latency_sec": 0},
    }


def files_for(raw: dict) -> dict[str, str]:
    return render(Spec.model_validate(raw), load_policy())


def install(deploy_dir: Path, raw: dict) -> Path:
    """Dựng project như deployer cài (không chạy dbt parse): <deploy_dir>/<model>/."""
    project = deploy_dir / raw["transform"]["model_name"]
    prepare_project(project, files_for(raw))
    return project


def install_parsed(deploy_dir: Path, raw: dict) -> Path:
    """Như `install` rồi biên dịch manifest dbt (như install._compile_manifest) để Dagster nạp được code location."""
    project = install(deploy_dir, raw)
    dbt = str(project / "dbt_project")
    env = {**os.environ, "WAREHOUSE_PATH": str(deploy_dir / "parse.duckdb")}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    parsed = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "parse", "--project-dir", dbt, "--profiles-dir", dbt],
        capture_output=True,
        text=True,
        env=env,
    )
    assert parsed.returncode == 0, parsed.stdout[-2000:]
    return project
