"""Tests for context overflow recovery functionality."""

import json
from pathlib import Path

import pytest

from gluon.agent import (
    AgentMessage,
    ContextOverflowError,
    _classify_api_error,
)
from gluon.models import ExecutionRun
from gluon.store import GluonStore


class TestContextOverflowError:
    """Tests for ContextOverflowError classification."""

    def test_classify_400_too_long(self):
        """Test detection of 400 'too long' error."""
        error = Exception("API Error: 400 Input is too long for requested model")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_classify_input_too_long(self):
        """Test detection of 'input is too long' error."""
        error = Exception("input is too long for this model")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_classify_context_exceeded(self):
        """Test detection of 'context exceeded' error."""
        error = Exception("The context window has been exceeded")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_classify_token_limit_exceeded(self):
        """Test detection of 'token limit exceeded' error."""
        error = Exception("Token limit has been exceeded")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_classify_other_400_error(self):
        """Test that other 400 errors are not classified as overflow."""
        error = Exception("API Error: 400 Invalid request format")
        result = _classify_api_error(error)
        assert not isinstance(result, ContextOverflowError)
        assert result is error

    def test_classify_500_error(self):
        """Test that 500 errors are not classified as overflow."""
        error = Exception("API Error: 500 Internal server error")
        result = _classify_api_error(error)
        assert not isinstance(result, ContextOverflowError)
        assert result is error

    def test_classify_generic_error(self):
        """Test that generic errors pass through unchanged."""
        error = ValueError("Something went wrong")
        result = _classify_api_error(error)
        assert not isinstance(result, ContextOverflowError)
        assert result is error


class TestRecoveryTrackingFields:
    """Tests for recovery tracking fields in ExecutionRun."""

    def test_default_recovery_fields(self):
        """Test default values for recovery fields."""
        run = ExecutionRun(
            project_id="test-project",
            prompt="test prompt",
        )
        assert run.recovery_count == 0
        assert run.last_recovery_at is None
        assert run.recovery_from_run_id is None

    def test_increment_recovery_count(self):
        """Test incrementing recovery count."""
        run = ExecutionRun(
            project_id="test-project",
            prompt="test prompt",
        )
        run.recovery_count += 1
        assert run.recovery_count == 1

    def test_set_recovery_from_run_id(self):
        """Test setting recovery_from_run_id."""
        original_run = ExecutionRun(
            project_id="test-project",
            prompt="original prompt",
        )
        recovery_run = ExecutionRun(
            project_id="test-project",
            prompt="recovery prompt",
            recovery_from_run_id=original_run.id,
        )
        assert recovery_run.recovery_from_run_id == original_run.id


class TestRecoveryStoreFields:
    """Tests for recovery field persistence in store."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a test store."""
        store = GluonStore(db_path=tmp_path / "test.db")
        store._init_db()
        return store

    @pytest.fixture
    def project(self, store):
        """Create a test project."""
        return store.create_project(name="test-project", path=Path("/tmp/test"))

    def test_save_and_load_recovery_fields(self, store, project):
        """Test that recovery fields are persisted correctly."""
        from gluon.models import utc_now

        # Create run
        run = store.create_run(
            project_id=project.id,
            prompt="test prompt",
        )

        # Update recovery fields
        run.recovery_count = 2
        run.last_recovery_at = utc_now()
        run.recovery_from_run_id = "parent-run-id"
        store.update_run(run)

        # Reload and verify
        loaded = store.get_run(run.id)
        assert loaded is not None
        assert loaded.recovery_count == 2
        assert loaded.last_recovery_at is not None
        assert loaded.recovery_from_run_id == "parent-run-id"

    def test_default_recovery_fields_on_load(self, store, project):
        """Test that default recovery fields work on load."""
        run = store.create_run(
            project_id=project.id,
            prompt="test prompt",
        )

        loaded = store.get_run(run.id)
        assert loaded is not None
        assert loaded.recovery_count == 0
        assert loaded.last_recovery_at is None
        assert loaded.recovery_from_run_id is None


class TestRecoveryStateExtraction:
    """Tests for _extract_recovery_state and helpers."""

    @pytest.fixture
    def runner(self, tmp_path):
        """Create a TaskRunner with temp store."""
        from gluon.runner import TaskRunner

        store = GluonStore(db_path=tmp_path / "test.db")
        store._init_db()
        return TaskRunner(store=store)

    @pytest.fixture
    def project(self, runner):
        """Create a test project."""
        return runner.store.create_project(name="test-project", path=Path("/tmp/test"))

    @pytest.fixture
    def run_with_logs(self, runner, project, tmp_path):
        """Create a run with log files."""
        run = runner.store.create_run(
            project_id=project.id,
            prompt="Implement the feature",
        )
        run.log_path = tmp_path / "logs" / run.id
        run.log_path.mkdir(parents=True, exist_ok=True)
        run.cost_usd = 5.50
        runner.store.update_run(run)
        return run

    def test_extract_basic_recovery_state(self, runner, run_with_logs):
        """Test basic recovery state extraction."""
        state = runner._extract_recovery_state(run_with_logs)

        assert state["run_id"] == run_with_logs.id
        assert state["project_id"] == run_with_logs.project_id
        assert state["original_prompt"] == "Implement the feature"
        assert state["total_cost_usd"] == 5.50
        assert state["completed_work"] == []

    def test_extract_completed_tasks_from_logs(self, runner, run_with_logs):
        """Test extracting completed tasks from messages.jsonl."""
        # Write messages with TodoWrite tool use
        messages_path = run_with_logs.log_path / "messages.jsonl"
        messages = [
            {
                "type": "tool_use",
                "metadata": {
                    "tool": "TodoWrite",
                    "input": {
                        "todos": [
                            {"content": "Setup project structure", "status": "completed"},
                            {"content": "Write tests", "status": "completed"},
                            {"content": "Implement feature", "status": "in_progress"},
                        ]
                    },
                },
            }
        ]
        with open(messages_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

        state = runner._extract_recovery_state(run_with_logs)
        assert "Setup project structure" in state["completed_work"]
        assert "Write tests" in state["completed_work"]
        assert "Implement feature" not in state["completed_work"]

    def test_extract_last_tool_used(self, runner, run_with_logs):
        """Test extracting last tool used from logs."""
        messages_path = run_with_logs.log_path / "messages.jsonl"
        messages = [
            {"type": "tool_use", "metadata": {"tool": "Read"}},
            {"type": "text", "content": "Found the file"},
            {"type": "tool_use", "metadata": {"tool": "Edit"}},
            {"type": "text", "content": "Made changes"},
            {"type": "tool_use", "metadata": {"tool": "Bash"}},
        ]
        with open(messages_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

        state = runner._extract_recovery_state(run_with_logs)
        assert state["last_tool_used"] == "Bash"

    def test_extract_with_worktree_info(self, runner, project, tmp_path):
        """Test recovery state includes worktree info."""
        run = runner.store.create_run(
            project_id=project.id,
            prompt="Test prompt",
            use_worktree=True,
        )
        run.branch_name = "gluon-abc123"
        run.worktree_path = "/tmp/worktree"
        run.source_branch = "main"
        run.log_path = tmp_path / "logs" / run.id
        runner.store.update_run(run)

        state = runner._extract_recovery_state(run)
        assert state["branch_name"] == "gluon-abc123"
        assert state["worktree_path"] == "/tmp/worktree"
        assert state["source_branch"] == "main"


class TestAgentMessageContextOverflow:
    """Tests for context overflow metadata in AgentMessage."""

    def test_context_overflow_message_metadata(self):
        """Test AgentMessage with context overflow metadata."""
        msg = AgentMessage(
            type="error",
            content="Context overflow: API Error 400 Input too long",
            metadata={
                "exception": "ContextOverflowError",
                "recoverable": True,
                "session_id": "test-session-123",
            },
        )

        assert msg.type == "error"
        assert msg.metadata["exception"] == "ContextOverflowError"
        assert msg.metadata["recoverable"] is True
        assert msg.metadata["session_id"] == "test-session-123"

    def test_regular_error_message_metadata(self):
        """Test regular error AgentMessage has different metadata."""
        msg = AgentMessage(
            type="error",
            content="Error: Connection timeout",
            metadata={
                "exception": "TimeoutError",
            },
        )

        assert msg.type == "error"
        assert msg.metadata["exception"] == "TimeoutError"
        assert "recoverable" not in msg.metadata
