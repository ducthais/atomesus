"""SQLite runtime settings and schema initialization for the shared local store."""

import pytest

from src.pipeline import store as pipeline_store
from src.run_report import store as report_store


@pytest.mark.parametrize(
    ("module", "opener"),
    [(pipeline_store, pipeline_store.database), (report_store, report_store._database)],
    ids=["pipeline", "run-report"],
)
def test_sqlite_uses_wal_normal_and_initializes_schema_once(tmp_path, monkeypatch, module, opener):
    path = str(tmp_path / f"{module.__name__.replace('.', '-')}.sqlite3")
    initialized = []
    original = module._initialize_schema

    def initialize(conn):
        initialized.append(True)
        original(conn)

    monkeypatch.setattr(module, "_initialize_schema", initialize)

    for _ in range(2):
        with opener(path) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL

    assert initialized == [True], "DDL/migrations must not run again for every query"
