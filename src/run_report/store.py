"""Sổ bằng chứng: lưu RunReport vào SQLite (cùng file với phiên đăng nhập, `PIPELINE_STORE_PATH`).

Mỗi run một dòng, khóa theo `run_id`; bản ghi được kiểm tra bằng `RunReport` khi đọc ra.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from pathlib import Path

from src.run_report.models import RunReport, RunStatus

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "run_reports"
HISTORY_LIMIT = 100


@contextmanager
def _database(path: str) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_reports ("
                " run_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, report TEXT NOT NULL)"
            )
            # created_at lưu ở UTC để sắp xếp theo chuỗi đúng thứ tự thời gian.
            conn.execute("CREATE INDEX IF NOT EXISTS run_reports_created_at ON run_reports (created_at)")
            # Nội dung đầy đủ file sinh ra theo (run_id, version), để xem diff sau khi sandbox đã xóa (SPEC §8.2).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_files ("
                " run_id TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,"
                " PRIMARY KEY (run_id, version, path))"
            )
            yield conn
    finally:
        conn.close()


def save_run_report(path: str, report: RunReport) -> None:
    """Ghi mới hoặc ghi đè RunReport của `run_id`.

    `CANCELLED` là trạng thái chốt: job nền có thể vừa hoàn tất một phase sau khi API hủy trả về, nhưng không được
    ghi đè quyết định hủy. Điều kiện nằm ngay trong câu UPSERT để không có khe race giữa đọc và ghi.
    """
    with _database(path) as conn:
        conn.execute(
            "INSERT INTO run_reports VALUES (?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, report = excluded.report"
            " WHERE run_reports.status <> 'CANCELLED' OR excluded.status = 'CANCELLED'",
            (report.run_id, report.status, report.created_at.astimezone(UTC).isoformat(), report.model_dump_json()),
        )


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


def abandon_unfinished(path: str) -> list[str]:
    """Run đang chạy nền lúc backend tắt sẽ không bao giờ xong: đánh dấu lỗi để giao diện không chờ mãi.

    Chỉ gọi lúc backend khởi động (chưa có việc nền nào). DRAFT là nháp chờ người (xác nhận kế hoạch hoặc sửa
    mapping), không phải đang chạy.
    """
    abandoned = []
    for status in ("PLAN_CONFIRMED", "GENERATING", "VALIDATING"):
        for report in list_run_reports(path, status, limit=1000):
            error = {
                "stage": report.stage_reached,
                "error": "Backend khởi động lại khi run đang chạy; hãy gửi lại yêu cầu.",
            }
            save_run_report(
                path,
                report.model_copy(update={"status": "INTERNAL_ERROR", "error_trace": [*report.error_trace, error]}),
            )
            abandoned.append(report.run_id)
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
