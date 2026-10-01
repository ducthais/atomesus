"""Persistent local run/session storage; independent of source Postgres."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def database(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, report TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT, expires REAL)")
            yield conn
    finally:
        conn.close()


def save_report(path: str, report: dict):
    with database(path) as conn:
        conn.execute("INSERT INTO runs VALUES (?, ?)", (report["run_id"], json.dumps(report)))


def reports(path: str) -> list[dict]:
    with database(path) as conn:
        rows = conn.execute("SELECT report FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
    return [json.loads(row[0]) for row in rows]


def report_by_id(path: str, run_id: str) -> dict | None:
    with database(path) as conn:
        row = conn.execute("SELECT report FROM runs WHERE id = ?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else None
