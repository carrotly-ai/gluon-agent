"""WS-2 regression: SQLite connections use WAL + busy_timeout and are closed.

WAL + a non-zero busy_timeout let the web server, detached workers and the
supervisor share the DB across processes without spurious "database is locked".
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from time import monotonic

import pytest

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


def test_readiness_preserves_settings_and_closes_connection(store: GluonStore):
    store.set_setting("__gluon_readiness__", "existing value")
    with store._get_conn() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM settings ORDER BY key")]

    store.check_readiness()

    with store._get_conn() as conn:
        after = [tuple(row) for row in conn.execute("SELECT * FROM settings ORDER BY key")]
        # A leaked probe transaction would prevent another writer acquiring this.
        conn.execute("BEGIN IMMEDIATE")
    assert after == before


def test_readiness_does_not_create_missing_database(store: GluonStore):
    store.db_path.rename(store.db_path.with_suffix(".offline"))

    with pytest.raises(sqlite3.OperationalError):
        store.check_readiness()

    assert not store.db_path.exists()


def test_readiness_escapes_database_uri(tmp_path: Path):
    store = GluonStore(tmp_path / "database?# space.db")
    store.check_readiness()


def test_readiness_requires_initialized_database(store: GluonStore, tmp_path: Path):
    store.db_path = tmp_path / "uninitialized.db"
    with closing(sqlite3.connect(store.db_path)):
        pass

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.check_readiness()


def test_readiness_requires_writable_database(store: GluonStore, monkeypatch):
    connect = sqlite3.connect

    def read_only_connection(*args, **kwargs):
        return connect(f"{store.db_path.as_uri()}?mode=ro", uri=True)

    monkeypatch.setattr("gluon.store.sqlite3.connect", read_only_connection)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store.check_readiness()


def test_readiness_bounds_lock_wait_and_recovers(store: GluonStore):
    with closing(sqlite3.connect(store.db_path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        started = monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.check_readiness()
        assert monotonic() - started < 1.0
        writer.rollback()

    store.check_readiness()
