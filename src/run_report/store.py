"""Sổ bằng chứng: lưu RunReport vào SQLite (cùng file với phiên đăng nhập, `PIPELINE_STORE_PATH`).

Mỗi run một dòng, khóa theo `run_id`; bản ghi được kiểm tra bằng `RunReport` khi đọc ra.
"""

import sqlite3
from collections.abc import Callable, Iterator
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
            # Delivery webhook GitHub đã xử lý (X-GitHub-Delivery), để gửi lại không áp hai lần.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS webhook_deliveries ("
                " delivery_id TEXT PRIMARY KEY, event TEXT NOT NULL, run_id TEXT, received_at TEXT NOT NULL)"
            )
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


def record_webhook_delivery(
    path: str, delivery_id: str, event: str, run_id: str | None = None, change: Change | None = None
) -> tuple[Literal["duplicate", "ignored", "applied"], RunReport | None]:
    """Ghi nhận một delivery webhook và áp thay đổi của nó vào run trong cùng một giao dịch ghi.

    Delivery đã ghi → ("duplicate", None), không đổi gì. Không có run / `change` trả None → ghi delivery,
    ("ignored", None). Ngược lại → ("applied", bản mới). `change` lỗi thì delivery không được ghi, GitHub gửi lại được.
    Delivery cũ hơn DELIVERY_RETENTION_DAYS được dọn để bảng không phình mãi.
    """
    now = datetime.now(UTC)
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM webhook_deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone():
            return "duplicate", None
        updated = _apply_change(conn, run_id, change) if run_id and change else None
        conn.execute(
            "DELETE FROM webhook_deliveries WHERE received_at < ?",
            ((now - timedelta(days=DELIVERY_RETENTION_DAYS)).isoformat(),),
        )
        conn.execute(
            "INSERT INTO webhook_deliveries VALUES (?, ?, ?, ?)",
            (delivery_id, event, run_id if updated else None, now.isoformat()),
        )
        return ("applied", updated) if updated else ("ignored", None)


def reserve_budget(
    path: str, run_id: str, version: int, category: str, usd: float, daily_limit: float
) -> tuple[bool, float]:
    """Reserve budget under one SQLite write transaction; retries are idempotent."""
    now = datetime.now(UTC)
    day = now.date().isoformat()
    with _database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT 1 FROM budget_reservations WHERE run_id = ? AND version = ? AND category = ?",
            (run_id, version, category),
        ).fetchone()
        spent = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_ledger WHERE substr(spent_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
        reserved = conn.execute(
            "SELECT coalesce(sum(usd), 0) FROM budget_reservations WHERE substr(reserved_at, 1, 10) = ?", (day,)
        ).fetchone()[0]
        if existing:
            return True, float(spent + reserved)
        if spent + reserved + usd > daily_limit:
            return False, float(spent + reserved)
        conn.execute(
            "INSERT INTO budget_reservations VALUES (?, ?, ?, ?, ?)",
            (run_id, version, category, usd, now.isoformat()),
        )
        return True, float(spent + reserved)


def release_budget(path: str, run_id: str, version: int, category: str) -> None:
    with _database(path) as conn:
        conn.execute(
            "DELETE FROM budget_reservations WHERE run_id = ? AND version = ? AND category = ?",
            (run_id, version, category),
        )


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


def list_run_reports(path: str, status: RunStatus | None = None, limit: int = HISTORY_LIMIT) -> list[RunReport]:
    """Lịch sử run, mới nhất trước, lọc theo status nếu có."""
    query, params = "SELECT report FROM run_reports", []
    if status is not None:
        query, params = query + " WHERE status = ?", [status]
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
