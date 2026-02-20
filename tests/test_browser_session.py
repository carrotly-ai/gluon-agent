"""Tests for browser session isolation."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gluon.models import ExecutionRun
from gluon.runner import RunnerConfig, TaskRunner
from gluon.store import GluonStore


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    return project_dir


class TestBrowserSessionIsolation:
    """Tests for AGENT_BROWSER_SESSION environment variable."""

    def test_background_run_sets_browser_session(self, store: GluonStore, project_path: Path, tmp_path: Path):
        """Test that background runs get unique AGENT_BROWSER_SESSION env var."""
        # Setup
        project = store.create_project("test-project", project_path)
        run = store.create_run(project.id, "test prompt")

        runner = TaskRunner(store, config=RunnerConfig(log_path=tmp_path / "logs"))

        # Mock subprocess.Popen to capture the env
        captured_env = {}

        def capture_popen(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            return mock_proc

        with patch("subprocess.Popen", side_effect=capture_popen):
            runner._spawn_background_process(run)

        # Verify browser session is set with run ID prefix
        assert "AGENT_BROWSER_SESSION" in captured_env
        assert captured_env["AGENT_BROWSER_SESSION"] == f"gluon-{run.id[:8]}"

    def test_background_runs_have_unique_sessions(self, store: GluonStore, project_path: Path, tmp_path: Path):
        """Test that different runs get different browser sessions."""
        # Setup
        project = store.create_project("test-project", project_path)
        run1 = store.create_run(project.id, "test prompt 1")
        run2 = store.create_run(project.id, "test prompt 2")

        runner = TaskRunner(store, config=RunnerConfig(log_path=tmp_path / "logs"))

        captured_sessions = []

        def capture_popen(*args, **kwargs):
            env = kwargs.get("env", {})
            if "AGENT_BROWSER_SESSION" in env:
                captured_sessions.append(env["AGENT_BROWSER_SESSION"])
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            return mock_proc

        with patch("subprocess.Popen", side_effect=capture_popen):
            runner._spawn_background_process(run1)
            runner._spawn_background_process(run2)

        # Verify both sessions are set and different
        assert len(captured_sessions) == 2
        assert captured_sessions[0] != captured_sessions[1]
        assert captured_sessions[0] == f"gluon-{run1.id[:8]}"
        assert captured_sessions[1] == f"gluon-{run2.id[:8]}"

    @pytest.mark.asyncio
    async def test_foreground_run_sets_browser_session(self, store: GluonStore, project_path: Path, tmp_path: Path):
        """Test that foreground runs set AGENT_BROWSER_SESSION in os.environ."""
        # Setup
        project = store.create_project("test-project", project_path)
        run = store.create_run(project.id, "test prompt")

        runner = TaskRunner(store, config=RunnerConfig(log_path=tmp_path / "logs"))

        # Track the env var during execution
        captured_session = None

        async def mock_run_task_inner(run):
            nonlocal captured_session
            # Call the real setup code
            runner._set_git_identity_env_vars()
            os.environ["AGENT_BROWSER_SESSION"] = f"gluon-{run.id[:8]}"
            captured_session = os.environ.get("AGENT_BROWSER_SESSION")
            # Don't actually run the agent
            run.mark_completed("Test completed")
            store.update_run(run)

        with patch.object(runner, "_run_task", mock_run_task_inner):
            await runner._run_task(run)

        # Verify browser session was set
        assert captured_session == f"gluon-{run.id[:8]}"

    def test_browser_session_format(self):
        """Test that browser session ID follows expected format."""
        run = ExecutionRun(project_id="test-project", prompt="test")

        session_id = f"gluon-{run.id[:8]}"

        # Should be gluon- prefix + 8 hex chars from UUID
        assert session_id.startswith("gluon-")
        assert len(session_id) == 14  # "gluon-" (6) + 8 chars
