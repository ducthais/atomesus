"""Persistent local session storage; independent of source Postgres.

RunReport nằm cùng file SQLite nhưng do `src.run_report.store` quản lý.
"""

import sqlite3
import time
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
                " password_hash TEXT NOT NULL, created_at REAL NOT NULL, email TEXT COLLATE NOCASE)"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "email" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN email TEXT COLLATE NOCASE")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email) WHERE email IS NOT NULL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pending_registrations ("
                " email TEXT PRIMARY KEY COLLATE NOCASE, username TEXT NOT NULL COLLATE NOCASE,"
                " name TEXT NOT NULL, password_hash TEXT NOT NULL, verification_hash TEXT NOT NULL,"
                " expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)"
            )
            # Lần đăng ký gần nhất của mỗi IP (giới hạn 1 tài khoản / IP / cửa sổ, xem api.register).
            conn.execute("CREATE TABLE IF NOT EXISTS registration_ips (ip TEXT PRIMARY KEY, at REAL NOT NULL)")
            yield conn
    finally:
        conn.close()


def _public_user(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, str | None]:
    return {"id": row[0], "username": row[1], "name": row[2], "role": row[3], "email": row[4]}


def get_user_by_username(path: str, username: str) -> tuple[dict[str, str], str] | None:
    """Đọc tài khoản đăng ký theo username; chỉ hàm xác thực nhận password_hash."""
    with database(path) as conn:
        row = conn.execute(
            "SELECT id, username, name, role, email, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    return (_public_user(row), row[5]) if row else None


def get_user_by_id(path: str, user_id: str) -> dict[str, str] | None:
    with database(path) as conn:
        row = conn.execute("SELECT id, username, name, role, email FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public_user(row) if row else None


def list_users(path: str) -> list[dict[str, str]]:
    with database(path) as conn:
        rows = conn.execute("SELECT id, username, name, role, email FROM users ORDER BY name, username").fetchall()
    return [_public_user(row) for row in rows]


def email_or_username_exists(path: str, email: str, username: str) -> bool:
    with database(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE OR username = ? COLLATE NOCASE",
            (email, username),
        ).fetchone()
    return row is not None


def create_user(
    path: str,
    *,
    user_id: str,
    username: str,
    name: str,
    role: str,
    password_hash: str,
    created_at: float,
    email: str | None = None,
) -> dict[str, str | None]:
    """Tạo tài khoản bằng một INSERT duy nhất để UNIQUE(username) chặn race giữa hai request đăng ký."""
    with database(path) as conn:
        conn.execute(
            "INSERT INTO users (id, username, name, role, password_hash, created_at, email) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, name, role, password_hash, created_at, email),
        )
    return {"id": user_id, "username": username, "name": name, "role": role, "email": email}


def update_user_role(path: str, user_id: str, role: str) -> dict[str, str] | None:
    """Đổi vai trò tài khoản đăng ký; CHECK của SQLite vẫn chặn mọi role ngoài Engineer/Reviewer."""
    with database(path) as conn:
        changed = conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id)).rowcount
        row = conn.execute("SELECT id, username, name, role, email FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public_user(row) if changed and row else None


def save_pending_registration(
    path: str, *, email: str, username: str, name: str, password_hash: str, verification_hash: str, expires: float
) -> None:
    with database(path) as conn:
        conn.execute("DELETE FROM pending_registrations WHERE expires < ?", (time.time(),))
        conn.execute(
            "INSERT INTO pending_registrations (email, username, name, password_hash, verification_hash, expires, attempts)"
            " VALUES (?, ?, ?, ?, ?, ?, 0)"
            " ON CONFLICT(email) DO UPDATE SET username=excluded.username, name=excluded.name,"
            " password_hash=excluded.password_hash, verification_hash=excluded.verification_hash,"
            " expires=excluded.expires, attempts=0",
            (email, username, name, password_hash, verification_hash, expires),
        )


def get_pending_registration(path: str, email: str) -> tuple[Any, ...] | None:
    with database(path) as conn:
        return conn.execute(
            "SELECT email, username, name, password_hash, verification_hash, expires, attempts"
            " FROM pending_registrations WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()


def increment_verification_attempts(path: str, email: str) -> None:
    with database(path) as conn:
        conn.execute(
            "UPDATE pending_registrations SET attempts = attempts + 1 WHERE email = ? COLLATE NOCASE", (email,)
        )


def delete_pending_registration(path: str, email: str) -> None:
    with database(path) as conn:
        conn.execute("DELETE FROM pending_registrations WHERE email = ? COLLATE NOCASE", (email,))


def claim_registration_slot(path: str, ip: str, window: float) -> float:
    """Giữ lượt đăng ký của `ip`: trả 0 nếu được phép (và ghi lại), ngược lại số giây còn phải chờ.

    Đọc và ghi trong cùng một giao dịch ghi để hai request đồng thời từ một IP không cùng lọt qua.
    """
    now = time.time()
    with database(path) as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM registration_ips WHERE at <= ?", (now - window,))
        row = conn.execute("SELECT at FROM registration_ips WHERE ip = ?", (ip,)).fetchone()
        if row:
            return row[0] + window - now
        conn.execute("INSERT INTO registration_ips (ip, at) VALUES (?, ?)", (ip, now))
    return 0.0


def release_registration_slot(path: str, ip: str) -> None:
    """Trả lại lượt khi yêu cầu không gửi được mã (lỗi SMTP), để người dùng thử lại ngay."""
    with database(path) as conn:
        conn.execute("DELETE FROM registration_ips WHERE ip = ?", (ip,))
