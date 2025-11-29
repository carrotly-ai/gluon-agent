"""Tests for Gluon models."""

from datetime import datetime
from pathlib import Path

import pytest

from gluon.models import Project, Session, SessionStatus


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
        """Test that relative paths are resolved to absolute."""
        project = Project(name="test", path=Path("."))

        assert project.path.is_absolute()

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
