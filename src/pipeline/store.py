"""Persistent local session storage; independent of source Postgres.

RunReport nằm cùng file SQLite nhưng do `src.run_report.store` quản lý.
"""

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ConnectionInUseError(Exception):
    pass


class UploadQuotaError(Exception):
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
            pending_columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_registrations)")}
            if "invite_hash" not in pending_columns:
                # Đăng ký qua link mời: hash token mời, tài khoản tạo ra là Reviewer (xem api.verify_registration).
                conn.execute("ALTER TABLE pending_registrations ADD COLUMN invite_hash TEXT")
            # Engineer ↔ Reviewer đã liên kết (nhiều-nhiều, ADR-0001 D1). Cặp demo minh ↔ an không ghi ở đây.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reviewer_links ("
                " engineer_id TEXT NOT NULL, reviewer_id TEXT NOT NULL, created_at REAL NOT NULL,"
                " PRIMARY KEY (engineer_id, reviewer_id))"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS reviewer_links_reviewer ON reviewer_links(reviewer_id)")
            # Lời mời Reviewer: chỉ lưu sha256 của token, dùng một lần, ghi người đã dùng.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reviewer_invites ("
                " token_hash TEXT PRIMARY KEY, engineer_id TEXT NOT NULL, created_at REAL NOT NULL,"
                " expires REAL NOT NULL, used_by TEXT, used_at REAL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS reviewer_invites_engineer ON reviewer_invites(engineer_id)")
            # Chủ của mỗi tên model (job Dagster, bảng gold dùng chung tên): giữ trước khi deploy, khóa chính chặn hai
            # chủ cùng giữ một tên (xem src.run_report.access.claim_model).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS model_owners ("
                " model_name TEXT PRIMARY KEY, owner_id TEXT NOT NULL, claimed_at REAL NOT NULL)"
            )
            # Chủ của run hệ thống (Day 2) ghi lúc tạo, để xét quyền đọc thẳng thay vì lần theo chuỗi run gốc (xem
            # src.run_report.access.record_system_run_owner).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_owners ("
                " run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, recorded_at REAL NOT NULL)"
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
                "CREATE TABLE IF NOT EXISTS uploads ("
                " id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, metadata TEXT NOT NULL,"
                " size_bytes INTEGER NOT NULL, selected_json TEXT NOT NULL DEFAULT '[]')"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS uploads_owner ON uploads(owner_id)")
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
    path: str,
    *,
    email: str,
    username: str,
    name: str,
    password_hash: str,
    verification_hash: str,
    expires: float,
    invite_hash: str | None = None,
) -> None:
    with database(path) as conn:
        conn.execute("DELETE FROM pending_registrations WHERE expires < ?", (time.time(),))
        conn.execute(
            "INSERT INTO pending_registrations"
            " (email, username, name, password_hash, verification_hash, expires, attempts, invite_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, ?)"
            " ON CONFLICT(email) DO UPDATE SET username=excluded.username, name=excluded.name,"
            " password_hash=excluded.password_hash, verification_hash=excluded.verification_hash,"
            " expires=excluded.expires, attempts=0, invite_hash=excluded.invite_hash",
            (email, username, name, password_hash, verification_hash, expires, invite_hash),
        )


def get_pending_registration(path: str, email: str) -> tuple[Any, ...] | None:
    with database(path) as conn:
        return conn.execute(
            "SELECT email, username, name, password_hash, verification_hash, expires, attempts, invite_hash"
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


def create_invite(path: str, engineer_id: str, token_hash: str, created_at: float, expires: float, limit: int) -> bool:
    """Ghi lời mời mới; False khi Engineer đã có `limit` lời mời còn hạn chưa dùng (chống spam bảng)."""
    with database(path) as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT COUNT(*) FROM reviewer_invites WHERE engineer_id = ? AND used_at IS NULL AND expires > ?",
            (engineer_id, created_at),
        ).fetchone()[0]
        if active >= limit:
            return False
        conn.execute(
            "INSERT INTO reviewer_invites (token_hash, engineer_id, created_at, expires) VALUES (?, ?, ?, ?)",
            (token_hash, engineer_id, created_at, expires),
        )
    return True


def _invite_state(conn: sqlite3.Connection, token_hash: str, now: float) -> tuple[str, str | None]:
    """("ok" | "missing" | "expired" | "used", engineer_id). Chỉ API gọi nội bộ; không trả engineer_id cho client."""
    row = conn.execute(
        "SELECT engineer_id, expires, used_at FROM reviewer_invites WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    if row is None:
        return "missing", None
    if row[2] is not None:
        return "used", row[0]
    if row[1] <= now:
        return "expired", row[0]
    return "ok", row[0]


def invite_state(path: str, token_hash: str, now: float) -> str:
    with database(path) as conn:
        return _invite_state(conn, token_hash, now)[0]


def _use_invite(conn: sqlite3.Connection, token_hash: str, engineer_id: str, reviewer_id: str, now: float) -> None:
    conn.execute(
        "UPDATE reviewer_invites SET used_by = ?, used_at = ? WHERE token_hash = ?", (reviewer_id, now, token_hash)
    )
    conn.execute(
        "INSERT OR IGNORE INTO reviewer_links (engineer_id, reviewer_id, created_at) VALUES (?, ?, ?)",
        (engineer_id, reviewer_id, now),
    )


def accept_invite(path: str, token_hash: str, reviewer_id: str, now: float) -> str:
    """Reviewer đã có tài khoản dùng lời mời: kiểm, đánh dấu đã dùng và tạo liên kết trong cùng một giao dịch ghi."""
    with database(path) as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        state, engineer_id = _invite_state(conn, token_hash, now)
        if state == "ok" and engineer_id:
            _use_invite(conn, token_hash, engineer_id, reviewer_id, now)
    return state


def create_invited_reviewer(
    path: str, token_hash: str, now: float, *, user_id: str, username: str, name: str, password_hash: str, email: str
) -> tuple[str, dict[str, str | None] | None]:
    """Đăng ký qua link mời: tạo Reviewer, dùng lời mời và liên kết trong một giao dịch, để không bao giờ có
    Reviewer mồ côi (đã tạo tài khoản nhưng lời mời vừa bị người khác dùng). Trùng email/username: IntegrityError."""
    with database(path) as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        state, engineer_id = _invite_state(conn, token_hash, now)
        if state != "ok" or not engineer_id:
            return state, None
        conn.execute(
            "INSERT INTO users (id, username, name, role, password_hash, created_at, email)"
            " VALUES (?, ?, ?, 'Reviewer', ?, ?, ?)",
            (user_id, username, name, password_hash, now, email),
        )
        _use_invite(conn, token_hash, engineer_id, user_id, now)
    return "ok", {"id": user_id, "username": username, "name": name, "role": "Reviewer", "email": email}


def linked_engineers(path: str, reviewer_id: str) -> set[str]:
    with database(path) as conn:
        rows = conn.execute("SELECT engineer_id FROM reviewer_links WHERE reviewer_id = ?", (reviewer_id,)).fetchall()
    return {row[0] for row in rows}


def list_linked_reviewers(path: str, engineer_id: str) -> list[dict[str, Any]]:
    """Reviewer đã liên kết với Engineer; Reviewer là tài khoản hệ thống (an) thì không có dòng trong `users`."""
    with database(path) as conn:
        rows = conn.execute(
            "SELECT l.reviewer_id, u.username, u.name, l.created_at FROM reviewer_links l"
            " LEFT JOIN users u ON u.id = l.reviewer_id WHERE l.engineer_id = ? ORDER BY l.created_at, l.reviewer_id",
            (engineer_id,),
        ).fetchall()
    return [{"id": row[0], "username": row[1], "name": row[2], "linked_at": row[3]} for row in rows]


def remove_reviewer_link(path: str, engineer_id: str, reviewer_id: str) -> bool:
    with database(path) as conn:
        return bool(
            conn.execute(
                "DELETE FROM reviewer_links WHERE engineer_id = ? AND reviewer_id = ?", (engineer_id, reviewer_id)
            ).rowcount
        )


def claim_model(path: str, model: str, owner_id: str, now: float) -> str:
    """Giữ tên `model` cho `owner_id` nếu chưa ai giữ; trả chủ đang giữ (là `owner_id` khi giữ được). Một câu INSERT trên
    khóa chính nên hai người giữ cùng lúc chỉ một người thắng."""
    with database(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO model_owners (model_name, owner_id, claimed_at) VALUES (?, ?, ?)",
            (model, owner_id, now),
        )
        return conn.execute("SELECT owner_id FROM model_owners WHERE model_name = ?", (model,)).fetchone()[0]


def get_model_owner(path: str, model: str) -> str | None:
    with database(path) as conn:
        row = conn.execute("SELECT owner_id FROM model_owners WHERE model_name = ?", (model,)).fetchone()
    return row[0] if row else None


def record_run_owner(path: str, run_id: str, owner_id: str, now: float) -> None:
    """Ghi chủ của run hệ thống một lần; ghi lại không đổi chủ đã ghi."""
    with database(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO run_owners (run_id, owner_id, recorded_at) VALUES (?, ?, ?)", (run_id, owner_id, now)
        )


def get_run_owner(path: str, run_id: str) -> str | None:
    with database(path) as conn:
        row = conn.execute("SELECT owner_id FROM run_owners WHERE run_id = ?", (run_id,)).fetchone()
    return row[0] if row else None


def list_run_owners(path: str) -> dict[str, str]:
    with database(path) as conn:
        return dict(conn.execute("SELECT run_id, owner_id FROM run_owners").fetchall())


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


def save_upload(path: str, metadata: dict, max_total_bytes: int) -> None:
    """Kiểm tổng dung lượng và lưu trong cùng giao dịch ghi; hai upload không cùng vượt quota."""
    with database(path) as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        used = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM uploads WHERE owner_id = ?", (metadata["owner_id"],)
        ).fetchone()[0]
        if used + metadata["size_bytes"] > max_total_bytes:
            raise UploadQuotaError(
                "Tổng dung lượng file của bạn vượt giới hạn trong budgets.yml. Xóa file cũ rồi thử lại."
            )
        conn.execute(
            "INSERT INTO uploads (id, owner_id, metadata, size_bytes) VALUES (?, ?, ?, ?)",
            (metadata["id"], metadata["owner_id"], json.dumps(metadata, ensure_ascii=False), metadata["size_bytes"]),
        )


def list_uploads(path: str, owner_id: str) -> list[dict]:
    with database(path) as conn:
        rows = conn.execute(
            "SELECT metadata, selected_json FROM uploads WHERE owner_id = ? ORDER BY rowid DESC", (owner_id,)
        ).fetchall()
    return [{**json.loads(row[0]), "selected_tables": json.loads(row[1])} for row in rows]


def get_upload(path: str, upload_id: str, owner_id: str) -> dict | None:
    with database(path) as conn:
        row = conn.execute(
            "SELECT metadata, selected_json FROM uploads WHERE id = ? AND owner_id = ?", (upload_id, owner_id)
        ).fetchone()
    return {**json.loads(row[0]), "selected_tables": json.loads(row[1])} if row else None


def select_upload_tables(path: str, upload_id: str, owner_id: str, tables: list[str]) -> bool:
    with database(path) as conn:
        return bool(
            conn.execute(
                "UPDATE uploads SET selected_json = ? WHERE id = ? AND owner_id = ?",
                (json.dumps(tables), upload_id, owner_id),
            ).rowcount
        )


def delete_upload(path: str, upload_id: str, owner_id: str, remove_files: Callable[[], None] | None = None) -> bool:
    with database(path) as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM uploads WHERE id = ? AND owner_id = ?", (upload_id, owner_id)).fetchone():
            return False
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_reports'").fetchone():
            used = conn.execute(
                "SELECT 1 FROM run_reports r, json_each(r.report, '$.spec.sources') source"
                " WHERE (r.status = 'DEPLOYED' OR json_extract(r.report, '$.deployment.deployed_at') IS NOT NULL)"
                " AND (json_extract(source.value, '$.connection_ref') = ?"
                " OR json_extract(source.value, '$.source_ref') IN (?, ?, ?)) LIMIT 1",
                (f"upload:{upload_id}", *(f"f_{upload_id[:length]}" for length in (8, 12, len(upload_id)))),
            ).fetchone()
            if used:
                raise ConnectionInUseError("File đang được pipeline đã deploy sử dụng; chưa thể xóa.")
        if remove_files is not None:
            remove_files()
        return bool(conn.execute("DELETE FROM uploads WHERE id = ? AND owner_id = ?", (upload_id, owner_id)).rowcount)
