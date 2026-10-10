"""Chạy thử bộ file sinh ra trong sandbox (P9–P12, SPEC §9).

Mỗi lần chạy có thư mục và file DuckDB riêng, xóa khi xong dù thành công hay lỗi (bất biến 1).
- P9 `el_run`: chạy đúng file ingestion dlt đã sinh, đổ từng nguồn vào bronze.
- P10 `el_validate`: `row_reconcile`, `hash_reconcile`, `idempotent` từng nguồn (src/codegen/el_checks.py).
- P11/P12 `t_run`/`t_validate`: `dbt build` (model + test), rồi `staging_reconcile` từng nguồn và `dbt_test`.
- `metric_reconcile`: tính lại gold thẳng trên Postgres nguồn bằng đường độc lập và so từng dòng (`reference.py`).
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

from src.agents.cancellation import raise_if_phase_cancelled
from src.codegen import el_checks, reference
from src.codegen.render import rejected_table, staging_model
from src.pipeline import files, postgres, rest_api
from src.pipeline.settings import PipelineSettings
from src.policy.models import Policy
from src.run_report.models import Check, GateResult
from src.spec.models import Spec

DBT_SCAFFOLD = Path(__file__).resolve().parents[2] / "dbt_project"
SAMPLE_ROWS = 20
SUPPORTED_KINDS = {"postgres", "rest_api", "filesystem"}
CONNECTION_ERRORS = (
    "OperationalError",
    "ConnectionTimeout",
    "could not connect",
    "Connection refused",
    "ConnectionError",
    "Max retries exceeded",
)
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


def _source_env(ref: str, decl, settings: PipelineSettings) -> dict[str, str]:
    """Biến môi trường dlt cho một nguồn: chuỗi kết nối Postgres, địa chỉ gốc của API, hoặc thư mục file."""
    if decl.kind == "filesystem":
        try:
            return {f"SOURCES__{ref.upper()}__BUCKET_URL": files.bucket_url(decl)}
        except files.FilesUnavailableError as exc:
            raise _StopError("INFRA_ERROR", "el_run", str(exc)) from exc
    if decl.kind == "rest_api":
        try:
            return {f"SOURCES__{ref.upper()}__BASE_URL": rest_api.base_url(decl, settings)}
        except rest_api.ApiUnavailableError as exc:
            raise _StopError("INFRA_ERROR", "el_run", str(exc)) from exc
    return {f"SOURCES__{ref.upper()}__CREDENTIALS": _connection_url(decl.connection_ref, settings)}


def credentials_env(policy: Policy, settings: PipelineSettings) -> dict[str, str]:
    """Biến kết nối dlt cho mọi nguồn trong domains.yml đã có kết nối (dùng khi chạy project đã deploy)."""
    env = {}
    for domain in policy.domains.domains.values():
        for ref, decl in domain.sources.items():
            try:
                env.update(_source_env(ref, decl, settings))
            except _StopError:
                continue
    return env


def _env(sandbox: Path, spec: Spec, policy: Policy, settings: PipelineSettings) -> dict[str, str]:
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    env.update(
        WAREHOUSE_PATH=str(sandbox / "warehouse.duckdb"),
        WAREHOUSE_TIMEZONE=settings.warehouse_timezone,
        DLT_DATA_DIR=str(sandbox / ".dlt"),
        RUNTIME__LOG_LEVEL="WARNING",
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        DO_NOT_TRACK="1",
        PYTHONIOENCODING="utf-8",
    )
    declared = policy.domains.domains[spec.domain].sources
    for source in spec.sources:
        env.update(_source_env(source.source_ref, declared[source.source_ref], settings))
    return env


def _run(command: list[str], sandbox: Path, env: dict[str, str], timeout: int, stage: str) -> str:
    raise_if_phase_cancelled()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=sandbox,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=os.name != "nt",
        creationflags=flags,
    )
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            raise _StopError(
                "INFRA_ERROR", stage, f"Phase {stage} chạy quá {timeout} giây (budgets.yml phase_timeout_sec)."
            )
        try:
            stdout, stderr = process.communicate(timeout=min(0.05, remaining))
            break
        except subprocess.TimeoutExpired:
            try:
                raise_if_phase_cancelled()
            except TimeoutError:
                _terminate_process_tree(process)
                raise
    if process.returncode != 0:
        output = (stderr or stdout).strip()
        raise _StopError("NEEDS_HUMAN", stage, output[-2000:] or f"{stage} thất bại (mã {process.returncode}).")
    return stdout


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Dừng tiến trình phase và mọi tiến trình con trước khi trả kết quả timeout."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            done = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if done.returncode and process.poll() is None:
                process.kill()
        else:
            os.killpg(process.pid, 9)
    except (OSError, subprocess.SubprocessError):
        process.kill()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def prepare_project(sandbox: Path, files: dict[str, str]) -> None:
    """Dựng một project chạy được: scaffold dbt + bộ file sinh ra. Dùng chung cho sandbox và deploy local."""
    shutil.copytree(DBT_SCAFFOLD, sandbox / "dbt_project", ignore=shutil.ignore_patterns("target", "logs", "*.duckdb"))
    for path, content in files.items():
        target = sandbox / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _el_run(
    sandbox: Path, spec: Spec, env: dict[str, str], timeout: int, policy: Policy, settings: PipelineSettings
) -> None:
    # Thử kết nối từng nguồn trước, để lỗi kết nối ra INFRA_ERROR kèm đúng source_id (SPEC §11.2, P9).
    declared = policy.domains.domains[spec.domain].sources
    for source in spec.sources:
        try:
            if source.kind == "rest_api":
                rest_api.endpoints(declared[source.source_ref], settings)
            elif source.kind == "filesystem":
                files.root(declared[source.source_ref])
            else:
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


def _el_validate(sandbox: Path, spec: Spec, env: dict, timeout: int, policy: Policy, settings) -> list[Check]:
    """P10: row / hash / idempotent cho từng nguồn (src/codegen/el_checks.py). Chạy ingestion lần hai để đo idempotent."""
    import duckdb

    declared = policy.domains.domains[spec.domain].sources
    sources = {s.id: el_checks.read_source(s, declared[s.source_ref], settings) for s in spec.sources}
    with duckdb.connect(env["WAREHOUSE_PATH"], read_only=True) as db:
        first = {s.id: el_checks.read_bronze(db, s) for s in spec.sources}
    _run([sys.executable, f"ingestion/{spec.transform.model_name}_pipeline.py"], sandbox, env, timeout, "el_validate")
    with duckdb.connect(env["WAREHOUSE_PATH"], read_only=True) as db:
        second = {s.id: el_checks.read_bronze(db, s) for s in spec.sources}
    checks = [el_checks.row_reconcile(s, sources[s.id], first[s.id]) for s in spec.sources]
    checks += [el_checks.hash_reconcile(s, sources[s.id], first[s.id]) for s in spec.sources]
    checks += [el_checks.idempotent(s, first[s.id], second[s.id]) for s in spec.sources]
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


NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "DECIMAL", "DOUBLE", "FLOAT", "REAL")


def _gold_profile(db, table: str) -> list[dict]:
    """Hồ sơ từng cột của bảng gold: số giá trị khác rỗng; cột số thêm tổng, min, max.

    Đủ để so hai phiên bản của cùng một pipeline (tổng chỉ số trước → sau) mà không giữ cả bảng.
    """
    columns = db.execute(f"describe {table}").fetchall()
    parts, numeric = [], []
    for name, kind, *_ in columns:
        quoted = '"' + name.replace('"', '""') + '"'
        parts.append(f"count({quoted})")
        if kind.upper().startswith(NUMERIC_TYPES):
            numeric.append(name)
            parts += [f"sum({quoted})", f"min({quoted})", f"max({quoted})"]
    values = iter(db.execute(f"select {', '.join(parts)} from {table}").fetchone())
    text = lambda value: None if value is None else str(value)  # noqa: E731
    profile = []
    for name, kind, *_ in columns:
        entry = {"name": name, "type": kind, "non_null": next(values)}
        if name in numeric:
            entry |= {"sum": text(next(values)), "min": text(next(values)), "max": text(next(values))}
        profile.append(entry)
    return profile


def _gold_preview(db, spec: Spec) -> dict:
    table = f'gold."{spec.transform.model_name}"'
    cursor = db.execute(f"select * from {table} order by 1 limit {SAMPLE_ROWS}")
    columns = [column[0] for column in cursor.description]
    rows = [
        {key: None if value is None else str(value) for key, value in zip(columns, row, strict=True)}
        for row in cursor.fetchall()
    ]
    return {
        "gold_rows": db.execute(f"select count(*) from {table}").fetchone()[0],
        "gold_samples": rows,
        "gold_profile": _gold_profile(db, table),
    }


def _post_run(checks: list[Check], preview: dict) -> tuple[GateResult, list[str]]:
    """Cổng post_run: mọi kiểm tra đạt, và gold không rỗng khi staging có dữ liệu.

    dbt test trên bảng rỗng luôn đạt, nên một `filters` sai nghĩa (ví dụ loại hết đơn) sẽ lọt nếu không chặn ở đây.
    `metric_reconcile` lệch nghĩa là đường pipeline và đường tính lại thẳng trên nguồn ra số khác nhau.
    """
    failed = [f"{c.name}{f'[{c.source_id}]' if c.source_id else ''}" for c in checks if c.result == "fail"]
    errors = [f"Kiểm tra không đạt: {', '.join(failed)}"] if failed else []
    for check in checks:
        if check.name == "metric_reconcile" and check.result == "fail":
            o = check.observed
            errors.append(
                f"Đối chiếu độc lập với nguồn lệch: {o['mismatch_count']} giá trị khác, {o['missing_count']} dòng thiếu, "
                f"{o['extra_count']} dòng thừa so với tính lại thẳng trên Postgres."
            )
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
            error_trace=[{"stage": "el_run", "error": f"Chưa hỗ trợ chạy thử nguồn loại {unsupported}."}],
        )
    timeout = policy.budgets.per_run.phase_timeout_sec
    max_reject_pct = policy.objectives[spec.domain].quality.max_reject_pct
    started = time.perf_counter()
    result = SandboxResult("READY_FOR_REVIEW", "t_validate")
    with TemporaryDirectory(prefix="pipeline-sandbox-", ignore_cleanup_errors=True) as directory:
        sandbox = Path(directory)
        try:
            prepare_project(sandbox, files)
            env = _env(sandbox, spec, policy, settings)
            _el_run(sandbox, spec, env, timeout, policy, settings)
            import duckdb

            result.checks += _el_validate(sandbox, spec, env, timeout, policy, settings)
            results, test_error = _t_run(sandbox, env, timeout)
            with duckdb.connect(env["WAREHOUSE_PATH"], read_only=True) as db:
                result.checks += _staging_reconcile(db, spec, max_reject_pct)
                result.checks.append(_dbt_test_check(results))
                preview = {**_gold_preview(db, spec), "warehouse_timezone": settings.warehouse_timezone}
                gold = reference.read_gold(db, spec.transform.model_name)
            try:
                matched, observed = reference.reconcile(spec, gold, policy, settings)
                result.checks.append(
                    Check(name="metric_reconcile", result="pass" if matched else "fail", observed=observed)
                )
            except reference.UnsupportedError as exc:
                preview["reconcile_skipped"] = f"Chưa đối chiếu độc lập được: {exc}."
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
