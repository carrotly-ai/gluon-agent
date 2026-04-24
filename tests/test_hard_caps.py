"""Tests for hard per-run safety caps (Theme D3).

Covers:
- Schema roundtrip (max_tool_calls, max_duration_minutes, tool_call_count)
- PreToolUse hook: allow when under cap, deny when at cap, increments counter
- Hook handles vanished run gracefully
- Hook handles missing cap (None) gracefully
- Duration watchdog invokes runner.cancel() after timeout
- CLI flags parse and reach submit()
- Web API roundtrip (POST /api/runs then GET /api/runs/{id})
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.hard_caps import _make_hard_caps_hook
from gluon.runner import TaskRunner, _duration_watchdog
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "hard_caps.db")


def _make_project(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    return store.create_project("proj", proj_path)


# ---------------------------------------------------------------------------
# Schema roundtrip
# ---------------------------------------------------------------------------


def test_create_run_persists_hard_caps(tmp_path):
    """Creating a run with both hard caps reads back the same values."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    run = store.create_run(
        project_id=project.id,
        prompt="test",
        max_tool_calls=10,
        max_duration_minutes=60,
    )

    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.max_tool_calls == 10
    assert fetched.max_duration_minutes == 60
    assert fetched.tool_call_count == 0


def test_create_run_hard_caps_default_none(tmp_path):
    """Omitting hard caps leaves them as None (unlimited)."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    run = store.create_run(project_id=project.id, prompt="test")
    fetched = store.get_run(run.id)

    assert fetched is not None
    assert fetched.max_tool_calls is None
    assert fetched.max_duration_minutes is None
    assert fetched.tool_call_count == 0


def test_update_run_persists_tool_call_count(tmp_path):
    """Mutating tool_call_count and calling update_run round-trips correctly."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t", max_tool_calls=5)

    run.tool_call_count = 3
    store.update_run(run)

    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.tool_call_count == 3
    assert fetched.max_tool_calls == 5


# ---------------------------------------------------------------------------
# PreToolUse hook
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hook_allows_when_cap_is_none(tmp_path):
    """Hook should be a no-op when the run has no max_tool_calls."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")  # cap=None

    hook = _make_hard_caps_hook(store, run.id)
    result = await hook(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        "toolu_a",
        MagicMock(),
    )

    # Allow = empty dict
    assert result == {}
    # Counter should not be touched when cap is None
    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.tool_call_count == 0


@pytest.mark.anyio
async def test_hook_allows_and_increments_under_cap(tmp_path):
    """Hook allows the call and bumps the counter when under cap."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t", max_tool_calls=3)

    hook = _make_hard_caps_hook(store, run.id)

    # Call #1: 0 -> 1, allow
    r1 = await hook({"tool_name": "Bash", "tool_input": {}}, "a", MagicMock())
    assert r1 == {}

    # Call #2: 1 -> 2, allow
    r2 = await hook({"tool_name": "Bash", "tool_input": {}}, "b", MagicMock())
    assert r2 == {}

    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.tool_call_count == 2


@pytest.mark.anyio
async def test_hook_denies_at_cap(tmp_path):
    """Hook returns deny when tool_call_count >= max_tool_calls."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t", max_tool_calls=2)

    hook = _make_hard_caps_hook(store, run.id)

    # Exhaust the cap (2 allowed calls)
    await hook({"tool_name": "Bash", "tool_input": {}}, "a", MagicMock())
    await hook({"tool_name": "Bash", "tool_input": {}}, "b", MagicMock())

    # Next call should be denied — we're at cap
    result = await hook({"tool_name": "Bash", "tool_input": {}}, "c", MagicMock())

    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    assert "hard-cap" in reason
    assert "max_tool_calls" in reason
    # Counter should not have been incremented on the deny
    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.tool_call_count == 2


@pytest.mark.anyio
async def test_hook_denies_immediately_when_count_already_above_cap(tmp_path):
    """If tool_call_count somehow exceeds the cap (e.g. cap lowered mid-run), deny."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t", max_tool_calls=5)

    # Manually seed a counter past the cap
    run.tool_call_count = 10
    store.update_run(run)

    hook = _make_hard_caps_hook(store, run.id)
    result = await hook({"tool_name": "Bash", "tool_input": {}}, "a", MagicMock())

    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    assert "10/5" in reason


@pytest.mark.anyio
async def test_hook_allows_when_run_vanishes(tmp_path):
    """If the run doesn't exist, hook should allow (not crash)."""
    store = _make_store(tmp_path)

    hook = _make_hard_caps_hook(store, run_id="nonexistent")
    result = await hook({"tool_name": "Bash", "tool_input": {}}, "a", MagicMock())

    assert result == {}


@pytest.mark.anyio
async def test_hook_soft_fails_on_store_exception(tmp_path):
    """Hook should swallow store errors and allow — never crash the run loop."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t", max_tool_calls=5)

    # Sabotage the store so it raises on get_run
    broken_store = MagicMock()
    broken_store.get_run = MagicMock(side_effect=RuntimeError("db broken"))

    hook = _make_hard_caps_hook(broken_store, run.id)
    result = await hook({"tool_name": "Bash", "tool_input": {}}, "a", MagicMock())

    # Allow despite the store failure
    assert result == {}


# ---------------------------------------------------------------------------
# Duration watchdog
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_duration_watchdog_cancels_after_timeout(tmp_path, monkeypatch):
    """The watchdog should call runner.cancel() after max_duration_minutes elapses.

    We subvert ``asyncio.sleep`` inside the watchdog by monkeypatching it to a
    fast no-op so the test runs quickly; the watchdog should still call cancel
    exactly once with the run's ID.
    """
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t", max_duration_minutes=1)

    # Build a mock runner with the store attached and a cancel AsyncMock
    fake_runner = MagicMock()
    fake_runner.store = store
    fake_runner.cancel = AsyncMock(return_value=True)

    # Replace asyncio.sleep inside the runner module so the watchdog returns fast
    import gluon.runner as runner_mod

    async def _fast_sleep(_secs):
        return None

    monkeypatch.setattr(runner_mod.asyncio, "sleep", _fast_sleep)

    await _duration_watchdog(fake_runner, run.id, max_duration_minutes=1)

    # cancel should have been called exactly once with our run ID
    fake_runner.cancel.assert_awaited_once_with(run.id)

    # The watchdog should have updated error_message before cancelling
    refreshed = store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.error_message is not None
    assert "max_duration_minutes" in refreshed.error_message
    assert "(1)" in refreshed.error_message


@pytest.mark.anyio
async def test_duration_watchdog_cancellable_before_fire():
    """Cancelling the watchdog before sleep returns should not call runner.cancel."""
    fake_runner = MagicMock()
    fake_runner.cancel = AsyncMock()

    # Use a real long sleep; we cancel immediately
    task = asyncio.create_task(_duration_watchdog(fake_runner, "run1", max_duration_minutes=60))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    fake_runner.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


def test_cli_run_flags_thread_through_submit(tmp_path, monkeypatch):
    """`gluon run ... --max-tool-calls N --max-duration M` reaches runner.submit."""
    from typer.testing import CliRunner

    from gluon.cli import app

    # Build a project on disk
    test_store = _make_store(tmp_path)
    proj_path = tmp_path / "proj"
    proj_path.mkdir()
    test_store.create_project("my-proj", proj_path)

    # Patch the orchestrator and runner constructors used inside cli.run
    import gluon.cli as cli_mod

    class _FakeProj:
        id = "proj-id-123"
        workspace_id = None

    _fake_proj = _FakeProj()
    _store_ref = test_store

    class _FakeOrch:
        store = _store_ref

        def get_project(self, _):
            return _fake_proj

        def resolve_agent(self, *_args, **_kw):
            return None

    captured_kwargs: dict = {}

    class _FakeRunner:
        async def submit(self, *args, **kwargs):
            captured_kwargs.update(kwargs)
            # Return a stub run object with the fields the CLI prints
            run_stub = MagicMock()
            run_stub.id = "abcd1234-ef"
            return run_stub

    monkeypatch.setattr(cli_mod, "get_orchestrator", lambda: _FakeOrch())
    monkeypatch.setattr(cli_mod, "TaskRunner", _FakeRunner)

    runner_cli = CliRunner()
    result = runner_cli.invoke(
        app,
        [
            "run",
            "my-proj",
            "do a thing",
            "--background",
            "--max-tool-calls",
            "7",
            "--max-duration",
            "30",
        ],
    )

    assert result.exit_code == 0, f"stdout={result.stdout}"
    assert captured_kwargs.get("max_tool_calls") == 7
    assert captured_kwargs.get("max_duration_minutes") == 30


def test_cli_run_without_hard_caps_passes_none(tmp_path, monkeypatch):
    """No hard-cap flags = None forwarded to submit (default behavior unchanged)."""
    from typer.testing import CliRunner

    from gluon.cli import app

    test_store = _make_store(tmp_path)
    proj_path = tmp_path / "proj"
    proj_path.mkdir()
    test_store.create_project("p2", proj_path)

    import gluon.cli as cli_mod

    class _FakeProj:
        id = "proj-id-2"
        workspace_id = None

    _fake_proj = _FakeProj()
    _store_ref = test_store

    class _FakeOrch:
        store = _store_ref

        def get_project(self, _):
            return _fake_proj

        def resolve_agent(self, *_args, **_kw):
            return None

    captured: dict = {}

    class _FakeRunner:
        async def submit(self, *args, **kwargs):
            captured.update(kwargs)
            r = MagicMock()
            r.id = "abcd1234"
            return r

    monkeypatch.setattr(cli_mod, "get_orchestrator", lambda: _FakeOrch())
    monkeypatch.setattr(cli_mod, "TaskRunner", _FakeRunner)

    runner_cli = CliRunner()
    result = runner_cli.invoke(
        app,
        ["run", "p2", "hello", "--background"],
    )

    assert result.exit_code == 0, f"stdout={result.stdout}"
    assert captured.get("max_tool_calls") is None
    assert captured.get("max_duration_minutes") is None


# ---------------------------------------------------------------------------
# Web API roundtrip
# ---------------------------------------------------------------------------


def test_api_create_run_persists_hard_caps(temp_store, project_with_path, api_client_with_mocks):
    """POST /api/runs with hard-cap fields → GET /api/runs/{id} reflects them."""
    project, _ = project_with_path
    client, mock_runner, _mock_ws = api_client_with_mocks

    # Seed a run that looks like what the real runner would produce, carrying
    # the hard caps we pass to the API.
    async def _fake_submit(**kwargs):
        run = temp_store.create_run(
            project_id=project.id,
            prompt=kwargs.get("prompt", ""),
            max_tool_calls=kwargs.get("max_tool_calls"),
            max_duration_minutes=kwargs.get("max_duration_minutes"),
        )
        return run

    mock_runner.submit = AsyncMock(side_effect=_fake_submit)

    resp = client.post(
        "/api/runs",
        json={
            "project_name": "test-project",
            "prompt": "capped run",
            "max_tool_calls": 42,
            "max_duration_minutes": 15,
        },
    )
    assert resp.status_code == 200, resp.text

    run_id = resp.json()["id"]

    # Fetch the detail; hard-cap fields should be present and equal
    detail = client.get(f"/api/runs/{run_id}?refresh_pr=false")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["max_tool_calls"] == 42
    assert body["max_duration_minutes"] == 15
    assert body["tool_call_count"] == 0


def test_api_create_run_without_hard_caps_defaults_to_none(temp_store, project_with_path, api_client_with_mocks):
    """Omitting hard caps in POST body leaves them as None on detail response."""
    project, _ = project_with_path
    client, mock_runner, _mock_ws = api_client_with_mocks

    async def _fake_submit(**kwargs):
        return temp_store.create_run(
            project_id=project.id,
            prompt=kwargs.get("prompt", ""),
            max_tool_calls=kwargs.get("max_tool_calls"),
            max_duration_minutes=kwargs.get("max_duration_minutes"),
        )

    mock_runner.submit = AsyncMock(side_effect=_fake_submit)

    resp = client.post(
        "/api/runs",
        json={"project_name": "test-project", "prompt": "uncapped"},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["id"]

    detail = client.get(f"/api/runs/{run_id}?refresh_pr=false")
    assert detail.status_code == 200
    body = detail.json()
    assert body["max_tool_calls"] is None
    assert body["max_duration_minutes"] is None
    assert body["tool_call_count"] == 0


# ---------------------------------------------------------------------------
# Runner.submit integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_runner_submit_threads_hard_caps_to_store(tmp_path):
    """TaskRunner.submit should forward max_tool_calls and max_duration_minutes
    to the store via create_run."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    runner = TaskRunner(store=store)

    # wait=False so we just create the run record and don't spawn anything
    # We monkey-patch _spawn_background_process to avoid actually launching.
    runner._spawn_background_process = MagicMock()  # type: ignore[assignment]

    run = await runner.submit(
        project_id=project.id,
        prompt="test",
        wait=False,
        max_tool_calls=8,
        max_duration_minutes=5,
    )

    # Refresh from DB
    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.max_tool_calls == 8
    assert fetched.max_duration_minutes == 5
    assert fetched.tool_call_count == 0
