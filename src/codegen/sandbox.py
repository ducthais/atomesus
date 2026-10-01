"""Chạy thử bộ file sinh ra trong sandbox (P9–P12, SPEC §9).

Mỗi lần chạy có thư mục và file DuckDB riêng, xóa khi xong dù thành công hay lỗi (bất biến 1).
- P9 `el_run`: chạy đúng file ingestion dlt đã sinh, đổ từng nguồn vào bronze.
- P10 `el_validate`: `row_reconcile` từng nguồn (đếm ở nguồn và ở bronze).
- P11/P12 `t_run`/`t_validate`: `dbt build` (model + test), rồi `staging_reconcile` từng nguồn và `dbt_test`.
Tiến trình con chỉ nhận các biến môi trường cần thiết, không nhận khóa LLM hay bí mật khác của backend.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from src.codegen.render import rejected_table, staging_model
from src.pipeline import postgres
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy
from src.run_report.models import Check, GateResult
from src.spec.models import Spec

DBT_SCAFFOLD = Path(__file__).resolve().parents[2] / "dbt_project"
SAMPLE_ROWS = 20
SUPPORTED_KINDS = {"postgres"}
CONNECTION_ERRORS = ("OperationalError", "ConnectionTimeout", "could not connect", "Connection refused")
PASSTHROUGH_ENV = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


@dataclass
class SandboxResult:
    status: str
    stage_reached: str
    gates: list[GateResult] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    error_trace: list[dict] = field(default_factory=list)
    duration_sec: float = 0.0


class _StopError(Exception):
    def __init__(self, status: str, stage: str, error: str, **extra):
        super().__init__(error)
        self.status, self.stage, self.trace = status, stage, {"stage": stage, "error": error, **extra}


def _connection_url(connection_ref: str, settings: PipelineSettings) -> str:
    """connection_ref trong domains.yml → URL. Sprint 1 chỉ có Postgres sandbox của B."""
    urls = {"sandbox_pg": settings.database_url.get_secret_value()}
    url = urls.get(connection_ref)
    if not url:
        raise _StopError("INFRA_ERROR", "el_run", f"Chưa cấu hình kết nối '{connection_ref}' ở backend.")
    # dlt đọc Postgres qua SQLAlchemy; backend dùng psycopg 3. Timeout ngắn để nguồn chết báo lỗi ngay.
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url + ("&" if "?" in url else "?") + "connect_timeout=10"


def _env(sandbox: Path, spec: Spec, policy: Policy, settings: PipelineSettings) -> dict[str, str]:
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    env.update(
        WAREHOUSE_PATH=str(sandbox / "warehouse.duckdb"),
        DLT_DATA_DIR=str(sandbox / ".dlt"),
        RUNTIME__LOG_LEVEL="WARNING",
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        DO_NOT_TRACK="1",
        PYTHONIOENCODING="utf-8",
    )
    declared = policy.domains.domains[spec.domain].sources
    for source in spec.sources:
        decl = declared[source.source_ref]
        env[f"SOURCES__{source.source_ref.upper()}__CREDENTIALS"] = _connection_url(decl.connection_ref, settings)
    return env


def _run(command: list[str], sandbox: Path, env: dict[str, str], timeout: int, stage: str) -> str:
    try:
        done = subprocess.run(
            command, cwd=sandbox, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise _StopError(
            "INFRA_ERROR", stage, f"Phase {stage} chạy quá {timeout} giây (budgets.yml phase_timeout_sec)."
        ) from exc
    if done.returncode != 0:
        output = (done.stderr or done.stdout).strip()
        raise _StopError("NEEDS_HUMAN", stage, output[-2000:] or f"{stage} thất bại (mã {done.returncode}).")
    return done.stdout


def _prepare(sandbox: Path, files: dict[str, str]) -> None:
    shutil.copytree(DBT_SCAFFOLD, sandbox / "dbt_project", ignore=shutil.ignore_patterns("target", "logs", "*.duckdb"))
    for path, content in files.items():
        target = sandbox / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _el_run(sandbox: Path, spec: Spec, env: dict[str, str], timeout: int, settings: PipelineSettings) -> None:
    # Thử kết nối từng nguồn trước, để lỗi kết nối ra INFRA_ERROR kèm đúng source_id (SPEC §11.2, P9).
    for source in spec.sources:
        try:
            with postgres.connection(settings):
                pass
        except postgres.SourceUnavailableError as exc:
            raise _StopError("INFRA_ERROR", "el_run", str(exc), source_id=source.id) from exc
    ingestion = f"ingestion/{spec.transform.model_name}_pipeline.py"
    try:
        _run([sys.executable, ingestion], sandbox, env, timeout, "el_run")
    except _StopError as stop:
        message = str(stop)
        if any(marker in message for marker in CONNECTION_ERRORS):
            raise _StopError(
                "INFRA_ERROR", "el_run", "Không kết nối được nguồn khi chạy dlt.", detail=message[-500:]
            ) from None
        raise


def _row_reconcile(db, spec: Spec, policy: Policy, settings: PipelineSettings) -> list[Check]:
    from psycopg import sql

    declared = policy.domains.domains[spec.domain].sources
    checks = []
    for source in spec.sources:
        schema = declared[source.source_ref].schema_
        with postgres.connection(settings) as conn:
            source_rows = conn.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(source.table))
            ).fetchone()[0]
        bronze_rows = db.execute(f'select count(*) from bronze."{source.target.table}"').fetchone()[0]
        checks.append(
            Check(
                name="row_reconcile",
                source_id=source.id,
                result="pass" if source_rows == bronze_rows else "fail",
                observed={"source": source_rows, "bronze": bronze_rows},
            )
        )
    return checks


def _dbt_results(sandbox: Path) -> list[dict]:
    path = sandbox / "dbt_project" / "target" / "run_results.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["results"]


def _t_run(sandbox: Path, env: dict[str, str], timeout: int) -> tuple[list[dict], str | None]:
    project = str(sandbox / "dbt_project")
    command = [sys.executable, "-m", "dbt.cli.main", "build", "--project-dir", project, "--profiles-dir", project]
    try:
        _run(command, sandbox, env, timeout, "t_run")
    except _StopError as stop:
        results = _dbt_results(sandbox)
        broken = [r for r in results if r["unique_id"].startswith("model.") and r["status"] == "error"]
        if broken or not results:
            message = "; ".join(f"{r['unique_id']}: {r['message']}" for r in broken) or str(stop)
            raise _StopError("NEEDS_HUMAN", "t_run", message[-2000:]) from None
        return results, str(stop)  # model chạy được, chỉ có test fail → ghi vào check dbt_test
    return _dbt_results(sandbox), None


def _staging_reconcile(db, spec: Spec, max_reject_pct: float) -> list[Check]:
    name = spec.transform.model_name
    checks = []
    for source in spec.sources:
        bronze = db.execute(f'select count(*) from bronze."{source.target.table}"').fetchone()[0]
        staging = db.execute(f'select count(*) from staging."{staging_model(name, source.id)}"').fetchone()[0]
        rejected_name = f'staging."{rejected_table(name, source.id)}"'
        reasons = dict(
            db.execute(f"select _reject_reason, count(*) from {rejected_name} group by 1 order by 2 desc").fetchall()
        )
        samples = [
            {"_reject_reason": reason, **json.loads(raw)}
            for reason, raw in db.execute(
                f"select _reject_reason, _raw_values from {rejected_name} limit {SAMPLE_ROWS}"
            ).fetchall()
        ]
        rejected = sum(reasons.values())
        duplicates = bronze - staging - rejected
        reject_pct = round(rejected * 100 / bronze, 2) if bronze else 0.0
        checks.append(
            Check(
                name="staging_reconcile",
                source_id=source.id,
                result="pass" if bronze and duplicates >= 0 and reject_pct <= max_reject_pct else "fail",
                observed={
                    "bronze": bronze,
                    "staging": staging,
                    "duplicates": duplicates,
                    "rejected": rejected,
                    "reject_pct": reject_pct,
                    "max_reject_pct": max_reject_pct,
                    "reject_reasons": reasons,
                    "reject_samples": samples,
                },
            )
        )
    return checks


def _dbt_test_check(results: list[dict]) -> Check:
    tests = [r for r in results if r["unique_id"].startswith("test.")]
    failed = [r for r in tests if r["status"] in ("fail", "error")]
    return Check(
        name="dbt_test",
        result="fail" if failed or not tests else "pass",
        observed={
            "passed": len(tests) - len(failed),
            "failed": len(failed),
            "failures": [{"test": r["unique_id"], "message": r["message"]} for r in failed],
        },
    )


def _gold_preview(db, spec: Spec) -> dict:
    table = f'gold."{spec.transform.model_name}"'
    cursor = db.execute(f"select * from {table} order by 1 limit {SAMPLE_ROWS}")
    columns = [column[0] for column in cursor.description]
    rows = [
        {key: None if value is None else str(value) for key, value in zip(columns, row, strict=True)}
        for row in cursor.fetchall()
    ]
    return {"gold_rows": db.execute(f"select count(*) from {table}").fetchone()[0], "gold_samples": rows}


def _post_run(checks: list[Check], preview: dict) -> tuple[GateResult, list[str]]:
    """Cổng post_run: mọi kiểm tra đạt, và gold không rỗng khi staging có dữ liệu.

    dbt test trên bảng rỗng luôn đạt, nên một `filters` sai nghĩa (ví dụ loại hết đơn) sẽ lọt nếu không chặn ở đây.
    Đối soát tổng chỉ số giữa gold và staging (`metric_reconcile`) bổ sung ở Sprint 3.
    """
    failed = [f"{c.name}{f'[{c.source_id}]' if c.source_id else ''}" for c in checks if c.result == "fail"]
    errors = [f"Kiểm tra không đạt: {', '.join(failed)}"] if failed else []
    staging_rows = sum(c.observed["staging"] for c in checks if c.name == "staging_reconcile")
    empty_gold = bool(staging_rows) and not preview["gold_rows"]
    if empty_gold:
        errors.append(
            f"Model gold rỗng dù staging có {staging_rows} dòng: kiểm tra transform.filters và điều kiện join."
        )
    passed = len(checks) - len(failed)
    gate = GateResult(
        name="post_run",
        result="fail" if errors else "pass",
        detail=f"{passed}/{len(checks)} phép kiểm tra đạt" + ("; gold rỗng" if empty_gold else ""),
        observed=preview,
    )
    return gate, errors


def run_in_sandbox(spec: Spec, files: dict[str, str], policy: Policy, settings: PipelineSettings) -> SandboxResult:
    """Chạy P9–P12 cho bộ file đã render. Không ném lỗi: mọi kết cục đều thành SandboxResult."""
    unsupported = sorted({s.kind for s in spec.sources} - SUPPORTED_KINDS)
    if unsupported:
        return SandboxResult(
            "NEEDS_HUMAN",
            "el_run",
            error_trace=[{"stage": "el_run", "error": f"Chưa hỗ trợ chạy thử nguồn loại {unsupported} (Sprint 2)."}],
        )
    timeout = policy.budgets.per_run.phase_timeout_sec
    max_reject_pct = policy.objectives[spec.domain].quality.max_reject_pct
    started = time.perf_counter()
    result = SandboxResult("READY_FOR_REVIEW", "t_validate")
    with TemporaryDirectory(prefix="pipeline-sandbox-", ignore_cleanup_errors=True) as directory:
        sandbox = Path(directory)
        try:
            _prepare(sandbox, files)
            env = _env(sandbox, spec, policy, settings)
            _el_run(sandbox, spec, env, timeout, settings)
            import duckdb

            with duckdb.connect(env["WAREHOUSE_PATH"], read_only=True) as db:
                result.checks += _row_reconcile(db, spec, policy, settings)
            results, test_error = _t_run(sandbox, env, timeout)
            with duckdb.connect(env["WAREHOUSE_PATH"], read_only=True) as db:
                result.checks += _staging_reconcile(db, spec, max_reject_pct)
                result.checks.append(_dbt_test_check(results))
                preview = _gold_preview(db, spec)
            if test_error and not any(c.name == "dbt_test" and c.result == "fail" for c in result.checks):
                result.error_trace.append({"stage": "t_validate", "error": test_error[-1000:]})
            gate, errors = _post_run(result.checks, preview)
            result.gates.append(gate)
            if errors:
                result.status = "NEEDS_HUMAN"
                result.error_trace += [{"stage": "t_validate", "error": error} for error in errors]
        except _StopError as stop:
            result.status, result.stage_reached = stop.status, stop.stage
            result.error_trace.append(stop.trace)
        except postgres.SourceUnavailableError as exc:
            result.status, result.stage_reached = "INFRA_ERROR", "el_validate"
            result.error_trace.append({"stage": "el_validate", "error": str(exc)})
    result.duration_sec = round(time.perf_counter() - started, 3)
    return result
