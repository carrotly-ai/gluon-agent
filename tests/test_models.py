"""Tests for Gluon models."""

from datetime import datetime
from pathlib import Path

import pytest

from gluon.models import ExecutionRun, Project, RunStatus, Session, SessionStatus


class TestProject:
    """Tests for Project model."""

    def test_create_project(self, tmp_path: Path):
        """Test creating a project."""
        project = Project(name="test-project", path=tmp_path)

        assert project.name == "test-project"
        assert project.path == tmp_path
        assert project.id is not None
        assert isinstance(project.created_at, datetime)
        assert isinstance(project.updated_at, datetime)

    def test_project_path_resolved(self):
        """Test that expanded_path returns absolute path."""
        project = Project(name="test", path=Path("."))

        # path stores original value, expanded_path returns absolute
        assert project.expanded_path.is_absolute()

    def test_project_metadata(self, tmp_path: Path):
        """Test project with metadata."""
        metadata = {"language": "python", "framework": "fastapi"}
        project = Project(name="test", path=tmp_path, metadata=metadata)

        assert project.metadata == metadata


class TestSession:
    """Tests for Session model."""

    def test_create_session(self):
        """Test creating a session."""
        session = Session(project_id="test-project-id")

        assert session.project_id == "test-project-id"
        assert session.id is not None
        assert session.status == SessionStatus.ACTIVE
        assert session.claude_session_id is None
        assert session.total_cost_usd == 0.0
        assert session.total_turns == 0

    def test_session_mark_paused(self):
        """Test marking session as paused."""
        session = Session(project_id="test")
        original_updated = session.updated_at

        session.mark_paused()

        assert session.status == SessionStatus.PAUSED
        assert session.updated_at >= original_updated

    def test_session_mark_completed(self):
        """Test marking session as completed."""
        session = Session(project_id="test")
        session.mark_completed()

        assert session.status == SessionStatus.COMPLETED

    def test_session_mark_failed(self):
        """Test marking session as failed."""
        session = Session(project_id="test")
        session.mark_failed()

        assert session.status == SessionStatus.FAILED

    def test_session_add_cost(self):
        """Test adding cost to session."""
        session = Session(project_id="test")
        session.add_cost(0.05)
        session.add_cost(0.03)

        assert session.total_cost_usd == pytest.approx(0.08)

    def test_session_increment_turns(self):
        """Test incrementing turn count."""
        session = Session(project_id="test")
        session.increment_turns()
        session.increment_turns()

        assert session.total_turns == 2


class TestSessionStatus:
    """Tests for SessionStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert SessionStatus.ACTIVE.value == "active"
        assert SessionStatus.PAUSED.value == "paused"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.FAILED.value == "failed"

    def test_status_from_string(self):
        """Test creating status from string."""
        assert SessionStatus("active") == SessionStatus.ACTIVE
        assert SessionStatus("paused") == SessionStatus.PAUSED


class TestRunStatus:
    """Tests for RunStatus enum."""

    def test_status_values(self):
        """Test all status values exist including REVIEW."""
        assert RunStatus.PENDING.value == "pending"
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.REVIEW.value == "review"
        assert RunStatus.COMPLETED.value == "completed"
        assert RunStatus.FAILED.value == "failed"
        assert RunStatus.CANCELLED.value == "cancelled"

    def test_status_from_string(self):
        """Test creating status from string."""
        assert RunStatus("pending") == RunStatus.PENDING
        assert RunStatus("running") == RunStatus.RUNNING
        assert RunStatus("review") == RunStatus.REVIEW
        assert RunStatus("completed") == RunStatus.COMPLETED


class TestExecutionRun:
    """Tests for ExecutionRun model."""

    def test_create_run(self):
        """Test creating an execution run."""
        run = ExecutionRun(project_id="test-project", prompt="Fix the bug")

        assert run.project_id == "test-project"
        assert run.prompt == "Fix the bug"
        assert run.status == RunStatus.PENDING
        assert run.id is not None

    def test_mark_review(self):
        """Test marking run as in review."""
        run = ExecutionRun(project_id="test", prompt="test")
        run.status = RunStatus.RUNNING

        run.mark_review()

        assert run.status == RunStatus.REVIEW

    def test_is_active_includes_review(self):
        """Test that is_active includes REVIEW state."""
        run = ExecutionRun(project_id="test", prompt="test")

        # Test pending
        run.status = RunStatus.PENDING
        assert run.is_active is True

        # Test running
        run.status = RunStatus.RUNNING
        assert run.is_active is True

        # Test review - should be active
        run.status = RunStatus.REVIEW
        assert run.is_active is True

        # Test completed - not active
        run.status = RunStatus.COMPLETED
        assert run.is_active is False

        # Test failed - not active
        run.status = RunStatus.FAILED
        assert run.is_active is False

    def test_is_resumable_includes_review(self):
        """Test that is_resumable includes REVIEW state."""
        run = ExecutionRun(project_id="test", prompt="test")
        run.claude_session_id = "session-123"  # Required for resumability

        # Test review - should be resumable
        run.status = RunStatus.REVIEW
        assert run.is_resumable is True

        # Test completed - should be resumable
        run.status = RunStatus.COMPLETED
        assert run.is_resumable is True

        # Test failed - should be resumable
        run.status = RunStatus.FAILED
        assert run.is_resumable is True

        # Test running - not resumable
        run.status = RunStatus.RUNNING
        assert run.is_resumable is False

    def test_is_resumable_requires_session(self):
        """Test that is_resumable requires claude_session_id."""
        run = ExecutionRun(project_id="test", prompt="test")
        run.status = RunStatus.REVIEW

        # Without session ID - not resumable
        assert run.is_resumable is False

        # With session ID - resumable
        run.claude_session_id = "session-123"
        assert run.is_resumable is True
