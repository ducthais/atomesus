"""Persistent local session storage; independent of source Postgres.

RunReport nằm cùng file SQLite nhưng do `src.run_report.store` quản lý.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def database(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT, expires REAL)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS users ("
                " id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,"
                " name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('Engineer', 'Reviewer')),"
                " password_hash TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            yield conn
    finally:
        conn.close()


def _public_user(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, str]:
    return {"id": row[0], "username": row[1], "name": row[2], "role": row[3]}


def get_user_by_username(path: str, username: str) -> tuple[dict[str, str], str] | None:
    """Đọc tài khoản đăng ký theo username; chỉ hàm xác thực nhận password_hash."""
    with database(path) as conn:
        row = conn.execute(
            "SELECT id, username, name, role, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    return (_public_user(row), row[4]) if row else None


def get_user_by_id(path: str, user_id: str) -> dict[str, str] | None:
    with database(path) as conn:
        row = conn.execute("SELECT id, username, name, role FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public_user(row) if row else None


def list_users(path: str) -> list[dict[str, str]]:
    with database(path) as conn:
        rows = conn.execute("SELECT id, username, name, role FROM users ORDER BY name, username").fetchall()
    return [_public_user(row) for row in rows]


def create_user(
    path: str,
    *,
    user_id: str,
    username: str,
    name: str,
    role: str,
    password_hash: str,
    created_at: float,
) -> dict[str, str]:
    """Tạo tài khoản bằng một INSERT duy nhất để UNIQUE(username) chặn race giữa hai request đăng ký."""
    with database(path) as conn:
        conn.execute(
            "INSERT INTO users (id, username, name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, name, role, password_hash, created_at),
        )
    return {"id": user_id, "username": username, "name": name, "role": role}
