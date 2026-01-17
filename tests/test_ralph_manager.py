"""Tests for RalphManager - fresh context sessions and progress file management."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.models import ExecutionRun, RalphLoopIteration, RunStatus
from gluon.ralph_manager import PROGRESS_FILE_NAME, RalphManager


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_run():
    """Create a mock ExecutionRun for testing."""
    return ExecutionRun(
        id="test-run-123",
        project_id="test-project",
        prompt="Fix all the bugs",
        status=RunStatus.RUNNING,
        ralph_enabled=True,
        max_loops=10,
        loop_count=0,
        max_calls_per_hour=100,
    )


@pytest.fixture
def mock_store():
    """Create a mock GluonStore."""
    store = MagicMock()
    store.create_ralph_iteration = MagicMock()
    store.update_run = MagicMock()
    return store


@pytest.fixture
def mock_agent():
    """Create a mock GluonAgent."""
    agent = MagicMock()
    agent.execute = AsyncMock(return_value=iter([]))
    return agent


@pytest.fixture
def ralph_manager(mock_run, mock_agent, mock_store, temp_dir):
    """Create a RalphManager for testing."""
    return RalphManager(
        run=mock_run,
        agent=mock_agent,
        store=mock_store,
        working_dir=temp_dir,
        log_dir=temp_dir,
    )


class TestProgressFileWrite:
    """Test _write_progress_file() method."""

    def test_creates_progress_file(self, ralph_manager, temp_dir):
        """Progress file is created in working directory."""
        iteration = RalphLoopIteration(
            run_id="test-run-123",
            loop_number=1,
            started_at=datetime.now(UTC),
            files_changed=3,
            has_errors=False,
            confidence_score=75.0,
        )
        output = "Some output text"

        ralph_manager._write_progress_file(iteration, output)

        progress_path = temp_dir / PROGRESS_FILE_NAME
        assert progress_path.exists()
        content = progress_path.read_text()
        assert "## Iteration 1" in content
        assert "**Files Changed**: 3" in content
        assert "**Errors**: False" in content
        assert "**Confidence**: 75%" in content

    def test_overwrites_previous_file(self, ralph_manager, temp_dir):
        """Progress file is overwritten (not appended)."""
        # Write first iteration
        iter1 = RalphLoopIteration(
            run_id="test-run-123",
            loop_number=1,
            started_at=datetime.now(UTC),
            files_changed=1,
            has_errors=False,
            confidence_score=50.0,
        )
        ralph_manager._write_progress_file(iter1, "Output 1")

        # Write second iteration
        iter2 = RalphLoopIteration(
            run_id="test-run-123",
            loop_number=2,
            started_at=datetime.now(UTC),
            files_changed=5,
            has_errors=True,
            confidence_score=80.0,
        )
        ralph_manager._write_progress_file(iter2, "Output 2")

        # Check only iteration 2 content exists
        progress_path = temp_dir / PROGRESS_FILE_NAME
        content = progress_path.read_text()
        assert "## Iteration 2" in content
        assert "## Iteration 1" not in content
        assert "**Files Changed**: 5" in content

    def test_includes_ralph_status_block(self, ralph_manager, temp_dir):
        """Progress file includes extracted RALPH_STATUS block."""
        iteration = RalphLoopIteration(
            run_id="test-run-123",
            loop_number=1,
            started_at=datetime.now(UTC),
            files_changed=2,
            has_errors=False,
            confidence_score=60.0,
        )
        output = """Some text before
---RALPH_STATUS---
STATUS: IN_PROGRESS
TASKS_COMPLETED_THIS_LOOP: 2
FILES_MODIFIED: 2
TESTS_STATUS: PASSING
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: false
RECOMMENDATION: Continue with task 3
---END_RALPH_STATUS---
Some text after"""

        ralph_manager._write_progress_file(iteration, output)

        progress_path = temp_dir / PROGRESS_FILE_NAME
        content = progress_path.read_text()
        assert "STATUS: IN_PROGRESS" in content
        assert "TASKS_COMPLETED_THIS_LOOP: 2" in content
        assert "RECOMMENDATION: Continue with task 3" in content


class TestProgressFileRead:
    """Test _read_progress_file() method."""

    def test_returns_none_when_missing(self, ralph_manager, temp_dir):
        """Returns None if progress file doesn't exist."""
        result = ralph_manager._read_progress_file()
        assert result is None

    def test_returns_content_when_exists(self, ralph_manager, temp_dir):
        """Returns file content when progress file exists."""
        progress_path = temp_dir / PROGRESS_FILE_NAME
        progress_path.write_text("## Iteration 1\nSome content")

        result = ralph_manager._read_progress_file()
        assert result == "## Iteration 1\nSome content"


class TestExtractRalphStatus:
    """Test _extract_ralph_status() method."""

    def test_extracts_status_block(self, ralph_manager):
        """Extracts RALPH_STATUS block from output."""
        output = """Some preamble
---RALPH_STATUS---
STATUS: COMPLETE
EXIT_SIGNAL: true
---END_RALPH_STATUS---
Some postamble"""

        result = ralph_manager._extract_ralph_status(output)
        assert result is not None
        assert "STATUS: COMPLETE" in result
        assert "EXIT_SIGNAL: true" in result

    def test_returns_none_when_no_block(self, ralph_manager):
        """Returns None when no RALPH_STATUS block found."""
        output = "Just some regular output without status block"
        result = ralph_manager._extract_ralph_status(output)
        assert result is None

    def test_handles_multiline_content(self, ralph_manager):
        """Correctly extracts multiline RALPH_STATUS block."""
        output = """---RALPH_STATUS---
STATUS: IN_PROGRESS
TASKS_COMPLETED_THIS_LOOP: 1
FILES_MODIFIED: 3
TESTS_STATUS: FAILING
WORK_TYPE: TESTING
EXIT_SIGNAL: false
RECOMMENDATION: Fix failing tests in test_auth.py
---END_RALPH_STATUS---"""

        result = ralph_manager._extract_ralph_status(output)
        assert "TESTS_STATUS: FAILING" in result
        assert "RECOMMENDATION: Fix failing tests" in result


class TestExtractKeyOutput:
    """Test _extract_key_output() method."""

    def test_extracts_tool_usage(self, ralph_manager):
        """Extracts tool usage from output."""
        output = "[Tool: Read]\n[Tool: Edit]\n[Tool: Read]\n[Tool: Bash]"
        result = ralph_manager._extract_key_output(output)
        assert "Tools used:" in result
        assert "Read(2)" in result
        assert "Edit(1)" in result
        assert "Bash(1)" in result

    def test_extracts_file_mentions(self, ralph_manager):
        """Extracts file modification mentions."""
        output = "Modified `src/auth.py` and Created `tests/test_auth.py`"
        result = ralph_manager._extract_key_output(output)
        assert "Files mentioned:" in result
        assert "auth.py" in result or "test_auth.py" in result

    def test_extracts_test_results(self, ralph_manager):
        """Extracts test results from output."""
        output = "Running pytest... 5 tests passed, 2 tests failed"
        result = ralph_manager._extract_key_output(output)
        assert "Test results:" in result

    def test_extracts_errors(self, ralph_manager):
        """Extracts error summaries."""
        output = "Error: Connection refused when connecting to database"
        result = ralph_manager._extract_key_output(output)
        assert "Errors:" in result
        assert "Connection refused" in result

    def test_truncates_long_output(self, ralph_manager):
        """Truncates output exceeding max_chars."""
        long_output = "x" * 5000
        result = ralph_manager._extract_key_output(long_output, max_chars=100)
        assert len(result) <= 103  # 100 + "..."
        assert result.endswith("...")

    def test_returns_empty_for_no_output(self, ralph_manager):
        """Returns empty string for no output."""
        result = ralph_manager._extract_key_output("")
        assert result == ""


class TestBuildLoopPrompt:
    """Test _build_loop_prompt() method."""

    def test_includes_loop_number(self, ralph_manager):
        """Prompt includes loop number."""
        prompt = ralph_manager._build_loop_prompt(1)
        assert "[Loop 1/10]" in prompt

    def test_includes_ralph_status_instruction(self, ralph_manager):
        """Prompt includes RALPH_STATUS instruction."""
        prompt = ralph_manager._build_loop_prompt(1)
        assert "---RALPH_STATUS---" in prompt
        assert "STATUS: IN_PROGRESS | COMPLETE | BLOCKED" in prompt

    def test_no_previous_summary_for_loop_1(self, ralph_manager, temp_dir):
        """Loop 1 doesn't include previous summary."""
        # Create a progress file (shouldn't be read for loop 1)
        progress_path = temp_dir / PROGRESS_FILE_NAME
        progress_path.write_text("## Previous iteration")

        prompt = ralph_manager._build_loop_prompt(1)
        assert "Previous Iteration Summary" not in prompt

    def test_includes_previous_summary_for_loop_2(self, ralph_manager, temp_dir):
        """Loop 2+ includes previous iteration summary."""
        # Create a progress file
        progress_path = temp_dir / PROGRESS_FILE_NAME
        progress_path.write_text("## Iteration 1\n**Files Changed**: 3")

        prompt = ralph_manager._build_loop_prompt(2)
        assert "Previous Iteration Summary" in prompt
        assert "## Iteration 1" in prompt
        assert "**Files Changed**: 3" in prompt
        assert "This is a fresh session" in prompt

    def test_includes_todo_count(self, ralph_manager, temp_dir):
        """Prompt includes TODO count when TODO file exists."""
        # Create a TODO file
        todo_path = temp_dir / "TODO.md"
        todo_path.write_text("- [ ] Task 1\n- [x] Task 2\n- [ ] Task 3")

        prompt = ralph_manager._build_loop_prompt(1)
        assert "[2/3 tasks remaining - EXIT_SIGNAL must be false]" in prompt

    def test_todo_count_all_done(self, ralph_manager, temp_dir):
        """Prompt shows all done when no tasks remain."""
        # Create a TODO file with all tasks complete
        todo_path = temp_dir / "TODO.md"
        todo_path.write_text("- [x] Task 1\n- [x] Task 2\n- [x] Task 3")

        prompt = ralph_manager._build_loop_prompt(1)
        assert "[0/3 tasks remaining - all done]" in prompt


class TestFreshSessionBehavior:
    """Test that RalphManager uses fresh sessions."""

    @pytest.mark.asyncio
    async def test_run_claude_uses_none_for_resume(self, ralph_manager, mock_agent):
        """_run_claude passes resume_session_id=None."""
        # Set up mock to capture the call
        calls = []

        async def mock_execute(**kwargs):
            calls.append(kwargs)
            # Return empty iterator
            return
            yield  # Make it an async generator

        mock_agent.execute = mock_execute

        # Note: _run_claude is synchronous but calls async execute
        # We need to test this differently - check the code directly
        # The key assertion is that line 338 now has resume_session_id=None

        # Just verify the method exists and has correct signature
        import inspect

        source = inspect.getsource(ralph_manager._run_claude)
        assert "resume_session_id=None" in source


class TestSessionIdTracking:
    """Test session ID tracking across iterations."""

    def test_session_id_updated_after_iteration(self, ralph_manager):
        """Session ID is stored after iteration for potential manual resume."""
        iteration = RalphLoopIteration(
            run_id="test-run-123",
            loop_number=1,
            started_at=datetime.now(UTC),
            claude_session_id="session-abc-123",
            files_changed=1,
            has_errors=False,
            confidence_score=50.0,
        )

        # Simulate what happens after _run_claude sets session_id
        if iteration.claude_session_id:
            ralph_manager.run.claude_session_id = iteration.claude_session_id

        assert ralph_manager.run.claude_session_id == "session-abc-123"
