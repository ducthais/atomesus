"""Cảnh báo và cờ của Day 2, lưu cùng file SQLite với sổ bằng chứng (`PIPELINE_STORE_PATH`).

| kind             | Khi nào                                                             | Ai cần làm gì                    |
|------------------|---------------------------------------------------------------------|----------------------------------|
| `source_stalled` | nguồn ngừng cấp dữ liệu (SPEC §9.6.4): sự cố hạ tầng, không tạo run sửa | kiểm tra nguồn               |
| `no_improvement` | run sửa không tìm được bản tốt hơn (NO_IMPROVEMENT): gắn cờ pipeline | kỹ sư xem khi rảnh (FR-24)      |
| `needs_human`    | run sửa chạm trần / bị chặn, không tự giải quyết được               | mời người sở hữu pipeline vào sửa |

Cùng (pipeline, kind) còn mở trong `cooldown_hours` thì không ghi thêm, để monitor chạy mỗi phút không spam.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

AlertKind = Literal["source_stalled", "no_improvement", "needs_human"]
COLUMNS = ("id", "model", "kind", "message", "detail", "run_id", "owner", "at", "resolved_at")


@contextmanager
def _database(path: str) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pipeline_alerts ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, kind TEXT NOT NULL,"
                " message TEXT NOT NULL, detail TEXT NOT NULL, run_id TEXT, owner TEXT, at TEXT NOT NULL,"
                " resolved_at TEXT)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS pipeline_alerts_model ON pipeline_alerts (model, kind, at)")
            yield conn
    finally:
        conn.close()


def _row(values: tuple) -> dict:
    row = dict(zip(COLUMNS, values, strict=True))
    row["detail"] = json.loads(row["detail"])
    return row


def raise_alert(
    path: str,
    model: str,
    kind: AlertKind,
    message: str,
    detail: dict | None = None,
    run_id: str | None = None,
    owner: str | None = None,
    quiet_hours: float = 0,
    now: datetime | None = None,
) -> dict | None:
    """Ghi một cảnh báo; đã có cảnh báo cùng (model, kind) chưa xử lý trong `quiet_hours` thì bỏ qua (trả None)."""
    now = now or datetime.now(UTC)
    since = (now - timedelta(hours=quiet_hours)).isoformat()
    with _database(path) as conn:
        open_one = conn.execute(
            "SELECT 1 FROM pipeline_alerts WHERE model = ? AND kind = ? AND resolved_at IS NULL AND at >= ?",
            (model, kind, since),
        ).fetchone()
        if quiet_hours and open_one:
            return None
        cursor = conn.execute(
            "INSERT INTO pipeline_alerts (model, kind, message, detail, run_id, owner, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (model, kind, message, json.dumps(detail or {}, ensure_ascii=False), run_id, owner, now.isoformat()),
        )
        found = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM pipeline_alerts WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row(found)


def list_alerts(path: str, model: str | None = None, open_only: bool = True, limit: int = 100) -> list[dict]:
    """Cảnh báo mới nhất trước; mặc định chỉ những cái chưa xử lý."""
    query, params = f"SELECT {', '.join(COLUMNS)} FROM pipeline_alerts WHERE 1 = 1", []
    if model is not None:
        query, params = query + " AND model = ?", [*params, model]
    if open_only:
        query += " AND resolved_at IS NULL"
    with _database(path) as conn:
        rows = conn.execute(query + " ORDER BY at DESC, id DESC LIMIT ?", [*params, limit]).fetchall()
    return [_row(row) for row in rows]


def get_alert(path: str, alert_id: int) -> dict | None:
    with _database(path) as conn:
        found = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM pipeline_alerts WHERE id = ?", (alert_id,)).fetchone()
    return _row(found) if found else None


def resolve_alert(path: str, alert_id: int, now: datetime | None = None) -> dict | None:
    """Đánh dấu đã xử lý. Không có cảnh báo đó → None."""
    with _database(path) as conn:
        conn.execute(
            "UPDATE pipeline_alerts SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            ((now or datetime.now(UTC)).isoformat(), alert_id),
        )
        found = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM pipeline_alerts WHERE id = ?", (alert_id,)).fetchone()
    return _row(found) if found else None
