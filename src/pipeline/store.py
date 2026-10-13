"""Persistent local session storage; independent of source Postgres.

RunReport nằm cùng file SQLite nhưng do `src.run_report.store` quản lý.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ConnectionInUseError(Exception):
    pass


@contextmanager
def database(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
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
            conn.execute(
                "CREATE TABLE IF NOT EXISTS connections ("
                " id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL, host TEXT NOT NULL,"
                " port INTEGER NOT NULL, database_name TEXT NOT NULL, database_user TEXT NOT NULL,"
                " password_encrypted TEXT NOT NULL, sslmode TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS connections_owner ON connections(owner_id)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS connection_selections ("
                " connection_id TEXT PRIMARY KEY REFERENCES connections(id) ON DELETE CASCADE,"
                " schema_name TEXT NOT NULL, tables_json TEXT NOT NULL)"
            )
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


def save_connection(path: str, metadata: dict[str, Any], password_encrypted: str) -> None:
    """Chỉ nhận mật khẩu đã mã hóa; metadata công khai không chứa credential."""
    with database(path) as conn:
        conn.execute(
            "INSERT INTO connections"
            " (id, owner_id, name, host, port, database_name, database_user, password_encrypted, sslmode, created_at)"
            " VALUES (:id, :owner_id, :name, :host, :port, :database, :user, :password_encrypted, :sslmode, :created_at)",
            {**metadata, "password_encrypted": password_encrypted},
        )


def list_connections(path: str, owner_id: str) -> list[dict[str, Any]]:
    with database(path) as conn:
        rows = conn.execute(
            "SELECT id, owner_id, name, host, port, database_name, database_user, sslmode, created_at"
            " FROM connections WHERE owner_id = ? ORDER BY created_at DESC, id",
            (owner_id,),
        ).fetchall()
    fields = ("id", "owner_id", "name", "host", "port", "database", "user", "sslmode", "created_at")
    return [dict(zip(fields, row, strict=True)) for row in rows]


def get_connection(path: str, connection_id: str, owner_id: str) -> tuple[dict[str, Any], str] | None:
    """Credential mã hóa chỉ đi tới hàm mở kết nối, không đi vào metadata công khai."""
    with database(path) as conn:
        row = conn.execute(
            "SELECT id, owner_id, name, host, port, database_name, database_user, sslmode, created_at, password_encrypted"
            " FROM connections WHERE id = ? AND owner_id = ?",
            (connection_id, owner_id),
        ).fetchone()
    fields = ("id", "owner_id", "name", "host", "port", "database", "user", "sslmode", "created_at")
    return (dict(zip(fields, row[:-1], strict=True)), row[-1]) if row else None


def get_connection_internal(path: str, connection_id: str) -> tuple[dict[str, Any], str] | None:
    """Chỉ runtime dùng id đã được khai báo trong policy; API phải dùng get_connection có owner."""
    with database(path) as conn:
        row = conn.execute(
            "SELECT id, owner_id, name, host, port, database_name, database_user, sslmode, created_at, password_encrypted"
            " FROM connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
    fields = ("id", "owner_id", "name", "host", "port", "database", "user", "sslmode", "created_at")
    return (dict(zip(fields, row[:-1], strict=True)), row[-1]) if row else None


def save_connection_selection(path: str, connection_id: str, owner_id: str, schema: str, tables: list[str]) -> bool:
    with database(path) as conn:
        return bool(
            conn.execute(
                "INSERT INTO connection_selections (connection_id, schema_name, tables_json)"
                " SELECT id, ?, ? FROM connections WHERE id = ? AND owner_id = ?"
                " ON CONFLICT(connection_id) DO UPDATE SET schema_name=excluded.schema_name,"
                " tables_json=excluded.tables_json",
                (schema, json.dumps(tables, ensure_ascii=False), connection_id, owner_id),
            ).rowcount
        )


def get_connection_selection(path: str, connection_id: str, owner_id: str) -> dict[str, Any] | None:
    with database(path) as conn:
        row = conn.execute(
            "SELECT s.schema_name, s.tables_json FROM connection_selections s"
            " JOIN connections c ON c.id = s.connection_id WHERE c.id = ? AND c.owner_id = ?",
            (connection_id, owner_id),
        ).fetchone()
    return {"schema": row[0], "tables": json.loads(row[1])} if row else None


def delete_connection(path: str, connection_id: str, owner_id: str) -> bool:
    with database(path) as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            "SELECT 1 FROM connections WHERE id = ? AND owner_id = ?", (connection_id, owner_id)
        ).fetchone():
            return False
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_reports'").fetchone():
            used = conn.execute(
                "SELECT 1 FROM run_reports r, json_each(r.report, '$.spec.sources') source"
                " WHERE (r.status = 'DEPLOYED' OR json_extract(r.report, '$.deployment.deployed_at') IS NOT NULL)"
                " AND json_extract(source.value, '$.connection_ref') = ? LIMIT 1",
                (f"conn:{connection_id}",),
            ).fetchone()
            if used:
                raise ConnectionInUseError("Kết nối đang được pipeline đã deploy sử dụng; chưa thể xóa.")
        return bool(
            conn.execute("DELETE FROM connections WHERE id = ? AND owner_id = ?", (connection_id, owner_id)).rowcount
        )
