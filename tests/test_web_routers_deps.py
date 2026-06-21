"""Tests for the router Depends providers + app.state wiring (#162 STEP B-2).

create_app exposes its shared collaborators on app.state so extracted routers
can inject them via Depends. These tests pin that every expected dep is set and
that each provider reads it back — a drift-guard as more routers are extracted.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from gluon.store import GluonStore
from gluon.web.api import create_app
from gluon.web.routers import _deps

SHARED_DEPS = (
    "store",
    "runner",
    "ws_manager",
    "notifier",
    "get_project_lookup",
    "run_to_response",
    "resolve_run_or_404",
    "resolve_project_or_404",
    "workspace_to_response",
)


def _app():
    return create_app(GluonStore(db_path=Path(tempfile.mkdtemp()) / "deps.db"))


def test_app_state_has_all_shared_deps():
    app = _app()
    for attr in SHARED_DEPS:
        assert getattr(app.state, attr, None) is not None, f"app.state.{attr} not set"


def test_providers_read_app_state():
    app = _app()
    # Providers only touch request.app.state, so a duck-typed request suffices.
    req = SimpleNamespace(app=app)
    assert _deps.get_store(req) is app.state.store
    assert _deps.get_runner(req) is app.state.runner
    assert _deps.get_ws_manager(req) is app.state.ws_manager
    assert _deps.get_notifier(req) is app.state.notifier
    assert _deps.get_project_lookup(req) is app.state.get_project_lookup
    assert _deps.get_run_to_response(req) is app.state.run_to_response
    assert _deps.get_resolve_run_or_404(req) is app.state.resolve_run_or_404
    assert _deps.get_resolve_project_or_404(req) is app.state.resolve_project_or_404
    assert _deps.get_workspace_to_response(req) is app.state.workspace_to_response
