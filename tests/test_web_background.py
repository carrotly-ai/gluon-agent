"""Tests for the extracted web background coroutines (#162).

The 6 long-running poll/cleanup loops were moved out of `create_app`'s closure
into `gluon.web.background` as module-level functions taking explicit deps. These
tests lock the extracted contract (signatures) and smoke-test the three
immediate-tick pollers by running each as a task for a moment and cancelling —
exercising the real loop body, which the app's startup hook (not triggered in
the test suite) otherwise never covers.

The cleanup/sweep coroutines start with a multi-minute `asyncio.sleep`, so they
get signature coverage only.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.models import RunStatus
from gluon.store import GluonStore
from gluon.web import background as bg


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "bg.db")


def _make_run(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    project = store.create_project("proj", proj_path)
    run = store.create_run(project.id, "test prompt")
    run.status = RunStatus.REVIEW
    store.update_run(run)
    return project, run


async def _run_briefly(coro) -> None:
    """Start a coroutine as a task, let it tick once, then cancel cleanly."""
    task = asyncio.create_task(coro)
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ========== Signature lock (all 6) ==========


def test_all_six_coroutines_exported_with_expected_params():
    expected = {
        "poll_run_status_changes": ["store", "runner", "get_project_lookup", "last_run_states"],
        "poll_log_updates": ["runner", "log_file_positions", "progress_file_mtimes", "tokens_file_mtimes"],
        "poll_pr_status_changes": ["store", "runner", "get_project_lookup"],
        "cleanup_old_logs": ["store", "cleanup_initial_delay_seconds", "cleanup_interval_seconds"],
        "cleanup_old_worktrees": ["store", "cleanup_initial_delay_seconds", "cleanup_interval_seconds"],
        "sweep_auth_state": ["store"],
    }
    for name, params in expected.items():
        fn = getattr(bg, name)
        assert inspect.iscoroutinefunction(fn), f"{name} must be a coroutine function"
        assert list(inspect.signature(fn).parameters) == params, name


# ========== Behavioral smoke tests (immediate-tick pollers) ==========


@pytest.mark.anyio
async def test_poll_run_status_changes_broadcasts_on_state_change(tmp_path):
    store = _make_store(tmp_path)
    project, run = _make_run(store, tmp_path)

    runner = MagicMock()
    runner.refresh_all_runs = MagicMock()

    # Pre-seed an OLD state so the first tick sees a change and broadcasts.
    last_states = {run.id: "running:none"}

    with patch("gluon.web.background.ws_manager") as mock_ws:
        mock_ws.broadcast_run_update = AsyncMock()
        await _run_briefly(bg.poll_run_status_changes(store, runner, lambda: {project.id: project.name}, last_states))
        mock_ws.broadcast_run_update.assert_awaited()

    # State map was updated to the run's current state.
    assert last_states[run.id] == f"{run.status.value}:none"


@pytest.mark.anyio
async def test_poll_run_status_changes_no_broadcast_for_new_run(tmp_path):
    store = _make_store(tmp_path)
    project, run = _make_run(store, tmp_path)

    runner = MagicMock()
    runner.refresh_all_runs = MagicMock()

    last_states: dict[str, str] = {}  # run unseen -> record only, no broadcast

    with patch("gluon.web.background.ws_manager") as mock_ws:
        mock_ws.broadcast_run_update = AsyncMock()
        await _run_briefly(bg.poll_run_status_changes(store, runner, lambda: {project.id: project.name}, last_states))
        mock_ws.broadcast_run_update.assert_not_called()

    assert run.id in last_states  # first sighting recorded


@pytest.mark.anyio
async def test_poll_log_updates_noop_without_subscribers(tmp_path):
    runner = MagicMock()

    with patch("gluon.web.background.ws_manager") as mock_ws:
        mock_ws.log_subscriptions = {}
        mock_ws.stream_agent_message = AsyncMock()
        # No subscribers -> no file reads, no streaming, no crash.
        await _run_briefly(bg.poll_log_updates(runner, {}, {}, {}))
        mock_ws.stream_agent_message.assert_not_called()


@pytest.mark.anyio
async def test_poll_pr_status_changes_noop_on_empty_store(tmp_path):
    store = _make_store(tmp_path)
    runner = MagicMock()

    with patch("gluon.web.background.ws_manager") as mock_ws:
        mock_ws.broadcast_run_update = AsyncMock()
        # Empty store -> no runs with open PRs -> constructs services, no GitHub
        # calls, no broadcast, no crash.
        await _run_briefly(bg.poll_pr_status_changes(store, runner, lambda: {}))
        mock_ws.broadcast_run_update.assert_not_called()
