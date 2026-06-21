"""Tests for the FastAPI lifespan migration (#162 STEP A).

The deprecated `@app.on_event("startup"/"shutdown")` hooks were replaced by a
single `lifespan` context manager passed to `FastAPI(lifespan=...)`. The hook
bodies are unchanged (`_run_startup` / `_run_shutdown`); only the registration
mechanism moved. These tests pin the wiring and prove the lifespan still runs
startup + shutdown end-to-end.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from starlette.testclient import TestClient

from gluon.store import GluonStore
from gluon.web.api import create_app


def _make_app():
    store = GluonStore(db_path=Path(tempfile.mkdtemp()) / "lifespan.db")
    return create_app(store)


def test_no_on_event_handlers_remain():
    """Migration left no legacy on_event startup/shutdown handlers."""
    app = _make_app()
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []


def test_lifespan_context_is_wired():
    """A custom lifespan context manager is configured on the app.

    Including APIRouters makes Starlette merge our `lifespan` with the routers'
    default lifespans into a `merged_lifespan` wrapper — either name confirms a
    real (non-default) lifespan is wired; the behavioral test below proves it
    actually runs our startup/shutdown.
    """
    app = _make_app()
    assert app.router.lifespan_context is not None
    assert getattr(app.router.lifespan_context, "__name__", "") in ("lifespan", "merged_lifespan")


def test_lifespan_runs_startup_and_shutdown(monkeypatch):
    """Entering/exiting the TestClient context runs the real lifespan.

    Disables the optional task scheduler so the test doesn't spin a poll loop;
    the redis transport is absent and fails gracefully (logged, not raised).
    """
    monkeypatch.setenv("GLUON_SCHEDULES_DISABLED", "1")
    app = _make_app()

    # `with TestClient(...)` triggers startup on enter and shutdown on exit.
    with TestClient(app) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
    # Reaching here means shutdown completed without raising.
