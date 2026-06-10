"""WS-2 regression: SQLite connections use WAL + busy_timeout and are closed.

WAL + a non-zero busy_timeout let the web server, detached workers and the
supervisor share the DB across processes without spurious "database is locked".
"""

from __future__ import annotations

from pathlib import Path

from gluon.store import GluonStore


def test_connections_use_wal_and_busy_timeout(tmp_path: Path):
    store = GluonStore(db_path=tmp_path / "gluon.db")
    with store._get_conn() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000


def test_get_conn_commits_and_closes(tmp_path: Path):
    store = GluonStore(db_path=tmp_path / "gluon.db")
    # Write inside the context manager; it should be committed on exit.
    with store._get_conn() as conn:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO probe (v) VALUES ('x')")
    # A fresh connection sees the committed row, and the previous one is closed.
    with store._get_conn() as conn2:
        rows = conn2.execute("SELECT v FROM probe").fetchall()
    assert [r[0] for r in rows] == ["x"]


def test_get_conn_rolls_back_on_error(tmp_path: Path):
    store = GluonStore(db_path=tmp_path / "gluon.db")
    with store._get_conn() as conn:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, v TEXT)")
    try:
        with store._get_conn() as conn:
            conn.execute("INSERT INTO probe (v) VALUES ('y')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with store._get_conn() as conn:
        rows = conn.execute("SELECT v FROM probe").fetchall()
    assert rows == []  # the failed transaction was rolled back
