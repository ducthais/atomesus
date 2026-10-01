"""Persistent local session storage; independent of source Postgres.

RunReport nằm cùng file SQLite nhưng do `src.run_report.store` quản lý.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def database(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT, expires REAL)")
            yield conn
    finally:
        conn.close()
