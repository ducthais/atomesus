"""Sổ bằng chứng: lưu RunReport vào SQLite (cùng file với phiên đăng nhập, `PIPELINE_STORE_PATH`).

Mỗi run một dòng, khóa theo `run_id`; bản ghi được kiểm tra bằng `RunReport` khi đọc ra.
"""

import json
import sqlite3
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from src.run_report.models import RunReport, RunStatus

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "run_reports"
HISTORY_LIMIT = 100
# GitHub chỉ cho gửi lại delivery trong vài ngày; giữ lâu hơn hẳn mức đó.
DELIVERY_RETENTION_DAYS = 30


@contextmanager
def _database(path: str) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_reports ("
                " run_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, report TEXT NOT NULL,"
                " updated_at TEXT)"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(run_reports)")}
            if "updated_at" not in columns:
                conn.execute("ALTER TABLE run_reports ADD COLUMN updated_at TEXT")
            conn.execute("UPDATE run_reports SET updated_at = created_at WHERE updated_at IS NULL")
            # created_at lưu ở UTC để sắp xếp theo chuỗi đúng thứ tự thời gian.
            conn.execute("CREATE INDEX IF NOT EXISTS run_reports_created_at ON run_reports (created_at)")
            # Nội dung đầy đủ file sinh ra theo (run_id, version), để xem diff sau khi sandbox đã xóa (SPEC §8.2).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_files ("
                " run_id TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,"
                " PRIMARY KEY (run_id, version, path))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS budget_ledger ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, version INTEGER NOT NULL,"
                " category TEXT NOT NULL, usd REAL NOT NULL, spent_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS budget_reservations ("
                " run_id TEXT NOT NULL, version INTEGER NOT NULL, category TEXT NOT NULL, usd REAL NOT NULL,"
                " reserved_at TEXT NOT NULL, PRIMARY KEY (run_id, version, category))"
            )
            # Lượt run mới đã giữ cho chủ (hạn mức số run/ngày, src/run_report/quota.py), ghi trước khi run vào sổ.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_slots ("
                " run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, reserved_at TEXT NOT NULL)"
            )
            # Delivery webhook GitHub đã xử lý (X-GitHub-Delivery), để gửi lại không áp hai lần. `pending`: sự kiện
            # liên quan đến chưa có run nào khớp (Deploy chưa lưu pull request), áp lại khi deployment được lưu.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS webhook_deliveries ("
                " delivery_id TEXT PRIMARY KEY, event TEXT NOT NULL, run_id TEXT, received_at TEXT NOT NULL,"
                " head_sha TEXT, pending TEXT)"
            )
            delivery_columns = {row[1] for row in conn.execute("PRAGMA table_info(webhook_deliveries)")}
            for column in ("head_sha", "pending"):
                if column not in delivery_columns:
                    conn.execute(f"ALTER TABLE webhook_deliveries ADD COLUMN {column} TEXT")
            conn.commit()  # schema/migration work must finish before callers request BEGIN IMMEDIATE
            yield conn
    finally:
        conn.close()


def save_run_report(path: str, report: RunReport) -> None:
    """Ghi mới hoặc ghi đè RunReport của `run_id`.

    `CANCELLED` là trạng thái chốt: job nền có thể vừa hoàn tất một phase sau khi API hủy trả về, nhưng không được
    ghi đè quyết định hủy. Điều kiện nằm ngay trong câu UPSERT để không có khe race giữa đọc và ghi.
    """
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        recorded_total = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_ledger WHERE run_id = ?", (report.run_id,)
        ).fetchone()[0]
        scan_usd = 0.0 if report.status == "BLOCKED_BUDGET" else report.cost.usd
        new_total = scan_usd + report.cost.llm_usd
        delta = max(0.0, new_total - recorded_total)
        now = datetime.now(UTC).isoformat()
        if delta:
            conn.execute(
                "INSERT INTO budget_ledger (run_id, version, category, usd, spent_at) VALUES (?, ?, ?, ?, ?)",
                (report.run_id, report.version, "reported", delta, now),
            )
        conn.execute(
            "INSERT INTO run_reports (run_id, status, created_at, report, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, report = excluded.report,"
            " updated_at = excluded.updated_at"
            " WHERE run_reports.status <> 'CANCELLED' OR excluded.status = 'CANCELLED'",
            (
                report.run_id,
                report.status,
                report.created_at.astimezone(UTC).isoformat(),
                report.model_dump_json(),
                now,
            ),
        )
        # Chỉ giải phóng khoản giữ chỗ khi báo cáo chứng minh công việc tương ứng đã hoàn tất. Reservation của một
        # lần gọi LLM bị timeout/cancel vẫn ở lại đến hết ngày, vì nhà cung cấp có thể đã tính tiền dù usage chưa về.
        current_calls = report.llm_usage[-report.llm_calls :] if report.llm_calls else []
        completed = [f"llm:{call.stage}:{index}" for index, call in enumerate(current_calls, start=1)]
        budget_passes = sum(gate.name == "budget" for gate in report.gates)
        if budget_passes:
            completed.append(f"scan:{budget_passes}")
        if completed:
            conn.executemany(
                "DELETE FROM budget_reservations WHERE run_id = ? AND version = ? AND category = ?",
                [(report.run_id, report.version, category) for category in completed],
            )


def transition_run_report(
    path: str,
    report: RunReport,
    expected_status: str,
    expected_version: int,
    expected_files_hash: str | None = None,
) -> bool:
    """Atomically replace a report only when the caller still owns the observed state."""
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, report FROM run_reports WHERE run_id = ?", (report.run_id,)).fetchone()
        # So trạng thái trước khi đọc JSON: người thắng race đã đổi status thì trả False ngay, không phụ thuộc nội dung.
        if row is None or row[0] != expected_status:
            return False
        current = RunReport.model_validate_json(row[1])
        if current.version != expected_version:
            return False
        if expected_files_hash is not None and current.files_hash != expected_files_hash:
            return False
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "UPDATE run_reports SET status = ?, report = ?, updated_at = ? WHERE run_id = ? AND status = ?",
            (report.status, report.model_dump_json(), now, report.run_id, expected_status),
        )
        return cursor.rowcount == 1


Change = Callable[[RunReport], RunReport | None]


def _apply_change(conn: sqlite3.Connection, run_id: str, change: Change) -> RunReport | None:
    """Đọc bản đang lưu, áp `change`, ghi compare-and-swap theo status vừa đọc. Gọi trong giao dịch BEGIN IMMEDIATE.

    Trả None nếu run không có, không đọc được thành RunReport, hoặc `change` trả None (không cần đổi gì).
    """
    row = conn.execute("SELECT status, report FROM run_reports WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    try:
        current = RunReport.model_validate_json(row[1])
    except ValidationError:
        return None
    updated = change(current)
    if updated is None:
        return None
    cursor = conn.execute(
        "UPDATE run_reports SET status = ?, report = ?, updated_at = ? WHERE run_id = ? AND status = ?",
        (updated.status, updated.model_dump_json(), datetime.now(UTC).isoformat(), run_id, row[0]),
    )
    return updated if cursor.rowcount == 1 else None


def update_run_report(path: str, run_id: str, change: Change) -> RunReport | None:
    """Đọc - sửa - ghi một run trong cùng một giao dịch ghi: thay đổi đồng thời (webhook, callback, Deploy) không bị
    ghi đè vì `change` luôn nhận bản mới nhất. Ngoại lệ trong `change` hủy cả giao dịch."""
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _apply_change(conn, run_id, change)


Match = Callable[[RunReport], bool]


def record_webhook_delivery(
    path: str,
    delivery_id: str,
    event: str,
    statuses: tuple[RunStatus, ...] = (),
    head_sha: str | None = None,
    match: Match | None = None,
    change: Change | None = None,
    pending: str | None = None,
) -> tuple[Literal["duplicate", "ignored", "pending", "applied"], RunReport | None]:
    """Ghi nhận một delivery webhook và áp thay đổi của nó vào run trong cùng một giao dịch ghi.

    Run khớp được tìm ngay trong giao dịch (run ở `statuses` có JSON chứa `head_sha`, mới nhất trước, `match` đúng):
    Deploy lưu pull request trước hay sau delivery thì delivery cũng thấy đúng một trong hai trạng thái.
    Delivery đã ghi → ("duplicate", None), không đổi gì. Có run khớp, `change` trả bản mới → ("applied", bản mới);
    trả None → ("ignored", None). Không run nào khớp: có `pending` thì giữ lại để `replay_webhook_deliveries` áp khi
    Deploy lưu deployment → ("pending", None), không thì ("ignored", None). Sự kiện bỏ qua (ping, push, repo khác…)
    cũng ghi delivery để gửi lại vẫn ra "duplicate". `change` lỗi thì delivery không được ghi, GitHub gửi lại được.
    Delivery cũ hơn DELIVERY_RETENTION_DAYS được dọn để bảng không phình mãi.
    """
    now = datetime.now(UTC)
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM webhook_deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone():
            return "duplicate", None
        matched, updated = False, None
        if statuses and head_sha and match and change:
            matched, updated = _match_and_apply(conn, statuses, head_sha, match, change)
        kept = pending if not matched else None
        conn.execute(
            "DELETE FROM webhook_deliveries WHERE received_at < ?",
            ((now - timedelta(days=DELIVERY_RETENTION_DAYS)).isoformat(),),
        )
        conn.execute(
            "INSERT INTO webhook_deliveries (delivery_id, event, run_id, received_at, head_sha, pending)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (delivery_id, event, updated.run_id if updated else None, now.isoformat(), head_sha, kept),
        )
        if updated:
            return "applied", updated
        return ("pending", None) if kept else ("ignored", None)


def _match_and_apply(
    conn: sqlite3.Connection, statuses: tuple[RunStatus, ...], head_sha: str, match: Match, change: Change
) -> tuple[bool, RunReport | None]:
    """Run đầu tiên (mới nhất trước, không giới hạn lịch sử) thỏa `match` → (True, bản mới nếu `change` có đổi)."""
    marks = ", ".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT run_id, status, report FROM run_reports WHERE status IN ({marks}) AND instr(report, ?) > 0"
        " ORDER BY created_at DESC, rowid DESC",
        (*statuses, head_sha),
    ).fetchall()
    for run_id, status, raw in rows:
        try:
            current = RunReport.model_validate_json(raw)
        except ValidationError:
            continue
        if not match(current):
            continue
        updated = change(current)
        if updated is not None:
            _write(conn, run_id, status, updated)
        return True, updated
    return False, None


def _write(conn: sqlite3.Connection, run_id: str, status: str, updated: RunReport) -> None:
    conn.execute(
        "UPDATE run_reports SET status = ?, report = ?, updated_at = ? WHERE run_id = ? AND status = ?",
        (updated.status, updated.model_dump_json(), datetime.now(UTC).isoformat(), run_id, status),
    )


def replay_webhook_deliveries(
    path: str, run_id: str, head_sha: str, apply: Callable[[RunReport, str], tuple[bool, RunReport | None]]
) -> RunReport | None:
    """Áp các delivery đang giữ (`pending`) của `head_sha` vào run `run_id`, theo thứ tự nhận, trong một giao dịch ghi.

    `apply(bản hiện tại, pending)` → (khớp run không, bản mới hoặc None nếu không đổi). Delivery khớp thôi được giữ
    nên không áp lần hai; delivery không khớp ở lại. Trả bản mới nếu run có đổi, ngược lại None.
    """
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        deliveries = conn.execute(
            "SELECT delivery_id, pending FROM webhook_deliveries WHERE head_sha = ? AND pending IS NOT NULL"
            " ORDER BY received_at, rowid",
            (head_sha,),
        ).fetchall()
        row = conn.execute("SELECT status, report FROM run_reports WHERE run_id = ?", (run_id,)).fetchone()
        if not deliveries or row is None:
            return None
        try:
            current = RunReport.model_validate_json(row[1])
        except ValidationError:
            return None
        changed = None
        for delivery_id, pending in deliveries:
            matched, updated = apply(current, pending)
            if not matched:
                continue
            if updated is not None:
                current = changed = updated
            conn.execute(
                "UPDATE webhook_deliveries SET pending = NULL, run_id = ? WHERE delivery_id = ?",
                (run_id if updated else None, delivery_id),
            )
        if changed is not None:
            _write(conn, run_id, row[0], changed)
        return changed


# Trần của chủ run (src/run_report/quota.py), chạy trong cùng giao dịch ghi sau khi trần chung đã qua: nhận kết nối
# và số USD sắp giữ, trả False thì không giữ khoản nào.
OwnerGate = Callable[[sqlite3.Connection, float], bool]


def reserve_budget(
    path: str,
    run_id: str,
    version: int,
    category: str,
    usd: float,
    daily_limit: float,
    owner_gate: OwnerGate | None = None,
) -> tuple[bool, float]:
    """Reserve budget under one SQLite write transaction; retries are idempotent.

    Khoản cùng (run, version, category) đã có mà nhỏ hơn `usd` (cổng batch giữ trước per_run.max_usd) thì chỉ giữ thêm
    phần chênh, qua cùng các trần."""
    now = datetime.now(UTC)
    day = now.date().isoformat()
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT usd FROM budget_reservations WHERE run_id = ? AND version = ? AND category = ?",
            (run_id, version, category),
        ).fetchone()
        spent = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_ledger WHERE substr(spent_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
        reserved = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_reservations WHERE substr(reserved_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
        extra = usd - (existing[0] if existing else 0.0)
        if existing and extra <= 0:
            return True, float(spent + reserved)
        if spent + reserved + extra > daily_limit:
            return False, float(spent + reserved)
        if owner_gate and not owner_gate(conn, extra):
            return False, float(spent + reserved)
        if existing:
            conn.execute(
                "UPDATE budget_reservations SET usd = ? WHERE run_id = ? AND version = ? AND category = ?",
                (usd, run_id, version, category),
            )
        else:
            conn.execute(
                "INSERT INTO budget_reservations VALUES (?, ?, ?, ?, ?)",
                (run_id, version, category, usd, now.isoformat()),
            )
        return True, float(spent + reserved)


def reserve_budget_batch(
    path: str,
    reservations: list[tuple[str, int, str, float]],
    daily_limit: float,
    owner_gate: OwnerGate | None = None,
) -> tuple[bool, float]:
    """Giữ chỗ cả mẻ trong một transaction; không đủ hạn mức thì không ghi khoản nào."""
    if len({(run_id, version, category) for run_id, version, category, _usd in reservations}) != len(reservations):
        raise ValueError("Khoản giữ chỗ trong mẻ không được trùng nhau.")
    now = datetime.now(UTC)
    day = now.date().isoformat()
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        spent = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_ledger WHERE substr(spent_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
        reserved = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_reservations WHERE substr(reserved_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
        pending = []
        for run_id, version, category, usd in reservations:
            exists = conn.execute(
                "SELECT 1 FROM budget_reservations WHERE run_id = ? AND version = ? AND category = ?",
                (run_id, version, category),
            ).fetchone()
            if not exists:
                pending.append((run_id, version, category, usd, now.isoformat()))
        current = float(spent + reserved)
        if current + sum(item[3] for item in pending) > daily_limit:
            return False, current
        if owner_gate and not owner_gate(conn, sum(item[3] for item in pending)):
            return False, current
        conn.executemany("INSERT INTO budget_reservations VALUES (?, ?, ?, ?, ?)", pending)
        return True, current


def release_budget(path: str, run_id: str, version: int, category: str) -> None:
    with _database(path) as conn:
        conn.execute(
            "DELETE FROM budget_reservations WHERE run_id = ? AND version = ? AND category = ?",
            (run_id, version, category),
        )


def rollback_batch_start(path: str, run_ids: list[str]) -> None:
    """Xóa atomically mọi dấu vết chưa chạy của một mẻ không khởi tạo đủ.

    Endpoint chỉ gọi hàm này trước khi đăng ký background jobs, nên các run này chưa thể có chi phí thật trong ledger.
    """
    if not run_ids:
        return
    marks = ", ".join("?" for _ in run_ids)
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"DELETE FROM run_files WHERE run_id IN ({marks})", run_ids)
        conn.execute(f"DELETE FROM run_reports WHERE run_id IN ({marks})", run_ids)
        conn.execute(f"DELETE FROM budget_ledger WHERE run_id IN ({marks})", run_ids)
        conn.execute(f"DELETE FROM budget_reservations WHERE run_id IN ({marks})", run_ids)
        conn.execute(f"DELETE FROM run_slots WHERE run_id IN ({marks})", run_ids)


def spent_today(path: str) -> float:
    day = datetime.now(UTC).date().isoformat()
    with _database(path) as conn:
        spent = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_ledger WHERE substr(spent_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
        reserved = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_reservations WHERE substr(reserved_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
    return float(spent + reserved)


def is_run_cancelled(path: str, run_id: str) -> bool:
    """Đọc cờ hủy nhẹ, dùng giữa các phase và trước mỗi lần gọi LLM."""
    with _database(path) as conn:
        row = conn.execute("SELECT status FROM run_reports WHERE run_id = ?", (run_id,)).fetchone()
    return bool(row and row[0] == "CANCELLED")


def list_run_reports(
    path: str, status: RunStatus | None = None, limit: int = HISTORY_LIMIT, run_ids: Collection[str] | None = None
) -> list[RunReport]:
    """Lịch sử run, mới nhất trước, lọc theo status nếu có; `run_ids` (nếu có) giới hạn trong các run được thấy."""
    query, params = "SELECT report FROM run_reports WHERE 1 = 1", []
    if status is not None:
        query, params = query + " AND status = ?", [status]
    if run_ids is not None:
        query, params = query + " AND run_id IN (SELECT value FROM json_each(?))", [*params, json.dumps(list(run_ids))]
    with _database(path) as conn:
        rows = conn.execute(query + " ORDER BY created_at DESC, rowid DESC LIMIT ?", [*params, limit]).fetchall()
    return [RunReport.model_validate_json(row[0]) for row in rows]


def abandon_unfinished(path: str, stale_before: datetime | None = None) -> list[str]:
    """Run đang chạy nền lúc backend tắt sẽ không bao giờ xong: đánh dấu lỗi để giao diện không chờ mãi.

    `stale_before`: chỉ đóng run không cập nhật từ trước mốc này, để run còn sống ở worker khác (dùng chung store)
    không bị đóng nhầm; None = đóng mọi run đang chạy. DRAFT là nháp chờ người (xác nhận kế hoạch hoặc sửa
    mapping), không phải đang chạy. Mỗi run đọc lại và ghi trong cùng một giao dịch ghi: worker vừa ghi tiến độ
    giữa lúc liệt kê và lúc đóng thì run được giữ nguyên.
    """
    abandoned = []
    for status in ("PLAN_CONFIRMED", "GENERATING", "VALIDATING"):
        for listed in list_run_reports(path, status, limit=1000):
            with _database(path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT status, report, updated_at FROM run_reports WHERE run_id = ?", (listed.run_id,)
                ).fetchone()
                if row is None or row[0] != status:
                    continue
                report = RunReport.model_validate_json(row[1])
                updated_at = datetime.fromisoformat(row[2]) if row[2] else report.created_at
                if stale_before is not None and updated_at >= stale_before:
                    continue
                error = {
                    "stage": report.stage_reached,
                    "error": "Run không còn tiến triển (backend khởi động lại hoặc worker dừng); hãy gửi lại yêu cầu.",
                }
                failed = report.model_copy(
                    update={"status": "INTERNAL_ERROR", "error_trace": [*report.error_trace, error]}
                )
                # Chi phí giữ nguyên bản đã lưu nên sổ ngân sách không đổi; chỉ đổi trạng thái.
                conn.execute(
                    "UPDATE run_reports SET status = ?, report = ?, updated_at = ? WHERE run_id = ?",
                    (failed.status, failed.model_dump_json(), datetime.now(UTC).isoformat(), failed.run_id),
                )
            abandoned.append(listed.run_id)
    return abandoned


def list_run_lineage(path: str) -> dict[str, tuple[str | None, str | None]]:
    """run_id → (created_by, parent_run_id) của mọi run, đọc thẳng JSON (không dựng RunReport) để lọc quyền."""
    with _database(path) as conn:
        rows = conn.execute(
            "SELECT run_id, json_extract(report, '$.created_by'), json_extract(report, '$.parent_run_id')"
            " FROM run_reports"
        ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def model_deployments(path: str, model: str) -> list[str]:
    """run_id của các run đã đưa `model` lên production (đã deploy, deploy lỗi, hoặc đã mở pull request deploy), cũ
    trước theo lúc tạo run."""
    with _database(path) as conn:
        rows = conn.execute(
            "SELECT run_id FROM run_reports WHERE json_extract(report, '$.spec.transform.model_name') = ?"
            " AND (status IN ('DEPLOYED', 'DEPLOY_FAILED') OR json_extract(report, '$.deployment') IS NOT NULL)"
            " ORDER BY created_at, rowid",
            (model,),
        ).fetchall()
    return [row[0] for row in rows]


def get_run_lineage(path: str, run_id: str) -> tuple[str | None, str | None] | None:
    """(created_by, parent_run_id) của một run, đọc thẳng JSON để xét quyền trước khi dựng RunReport; None nếu không có."""
    with _database(path) as conn:
        row = conn.execute(
            "SELECT json_extract(report, '$.created_by'), json_extract(report, '$.parent_run_id')"
            " FROM run_reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def get_run_report(path: str, run_id: str) -> RunReport | None:
    with _database(path) as conn:
        row = conn.execute("SELECT report FROM run_reports WHERE run_id = ?", (run_id,)).fetchone()
    return RunReport.model_validate_json(row[0]) if row else None


def save_run_files(path: str, run_id: str, version: int, files: dict[str, str]) -> None:
    """Ghi nội dung file sinh ra của một version; ghi lại cùng version thì thay toàn bộ bộ file."""
    with _database(path) as conn:
        conn.execute("DELETE FROM run_files WHERE run_id = ? AND version = ?", (run_id, version))
        conn.executemany(
            "INSERT INTO run_files VALUES (?, ?, ?, ?)",
            [(run_id, version, file_path, content) for file_path, content in sorted(files.items())],
        )


def get_run_files(path: str, run_id: str, version: int) -> dict[str, str]:
    with _database(path) as conn:
        rows = conn.execute(
            "SELECT path, content FROM run_files WHERE run_id = ? AND version = ? ORDER BY path", (run_id, version)
        ).fetchall()
    return dict(rows)
