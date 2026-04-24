"""Tests for the operator-configurable default per-run cost cap."""

import os
from pathlib import Path
from unittest.mock import patch

from gluon.runner import _resolve_default_run_cost_cap
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "settings.db")


def test_resolve_returns_none_when_unset(tmp_path):
    store = _make_store(tmp_path)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GLUON_DEFAULT_RUN_MAX_COST_USD", None)
        assert _resolve_default_run_cost_cap(store) is None


def test_resolve_reads_db_setting(tmp_path):
    store = _make_store(tmp_path)
    store.set_setting("default_run_max_cost_usd", "12.5")
    assert _resolve_default_run_cost_cap(store) == 12.5


def test_resolve_db_setting_takes_priority_over_env(tmp_path):
    store = _make_store(tmp_path)
    store.set_setting("default_run_max_cost_usd", "7.5")

    with patch.dict(os.environ, {"GLUON_DEFAULT_RUN_MAX_COST_USD": "99.0"}):
        assert _resolve_default_run_cost_cap(store) == 7.5


def test_resolve_falls_back_to_env_var(tmp_path):
    store = _make_store(tmp_path)
    # Ensure no DB setting
    assert store.get_setting("default_run_max_cost_usd") is None

    with patch.dict(os.environ, {"GLUON_DEFAULT_RUN_MAX_COST_USD": "25.0"}):
        assert _resolve_default_run_cost_cap(store) == 25.0


def test_resolve_ignores_invalid_value(tmp_path):
    store = _make_store(tmp_path)
    store.set_setting("default_run_max_cost_usd", "not-a-number")

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GLUON_DEFAULT_RUN_MAX_COST_USD", None)
        assert _resolve_default_run_cost_cap(store) is None


def test_resolve_ignores_invalid_env_var(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_setting("default_run_max_cost_usd") is None

    with patch.dict(os.environ, {"GLUON_DEFAULT_RUN_MAX_COST_USD": "garbage"}):
        assert _resolve_default_run_cost_cap(store) is None


def test_resolve_accepts_integer_string(tmp_path):
    store = _make_store(tmp_path)
    store.set_setting("default_run_max_cost_usd", "50")
    assert _resolve_default_run_cost_cap(store) == 50.0
