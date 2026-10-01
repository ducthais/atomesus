"""Lịch sử chat của run (SPEC §14.5, FR-20): bảng `run_messages` trong cùng file SQLite với sổ bằng chứng."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.chat.models import ChatMessage, Intent

COLUMNS = ("id", "run_id", "role", "author", "intent", "content", "at")
AGENT_AUTHOR = "agent"


@contextmanager
def _database(path: str) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_messages ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, role TEXT NOT NULL,"
                " author TEXT NOT NULL, intent TEXT NOT NULL, content TEXT NOT NULL, at TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS run_messages_run ON run_messages (run_id, id)")
            yield conn
    finally:
        conn.close()


def save_exchange(path: str, run_id: str, author: str, intent: Intent, question: str, reply: str) -> None:
    """Ghi câu hỏi của người dùng và câu trả lời của agent trong một transaction."""
    at = datetime.now(UTC).isoformat()
    with _database(path) as conn:
        conn.executemany(
            "INSERT INTO run_messages (run_id, role, author, intent, content, at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (run_id, "user", author, intent, question, at),
                (run_id, "assistant", AGENT_AUTHOR, intent, reply, at),
            ],
        )


def list_messages(path: str, run_id: str) -> list[ChatMessage]:
    """Lịch sử chat của run, cũ nhất trước."""
    with _database(path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM run_messages WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
    return [ChatMessage.model_validate(dict(zip(COLUMNS, row, strict=True))) for row in rows]


def count_questions(path: str, run_id: str) -> int:
    """Số câu hỏi `ask` người dùng đã gửi cho run, để so với `budgets.yml: per_run.max_chat_calls`."""
    with _database(path) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM run_messages WHERE run_id = ? AND role = 'user' AND intent = 'ask'", (run_id,)
        ).fetchone()
    return count
