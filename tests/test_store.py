"""Tests for Gluon store."""

from pathlib import Path

import pytest

from gluon.models import SessionStatus
from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path: Path) -> GluonStore:
    """Create a store with a temporary database."""
    db_path = tmp_path / "test.db"
    return GluonStore(db_path)


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    return project_dir


class TestProjectCRUD:
    """Tests for project CRUD operations."""

    def test_create_project(self, store: GluonStore, project_path: Path):
        """Test creating a project."""
        project = store.create_project("test", project_path)

        assert project.name == "test"
        assert project.path == project_path
        assert project.id is not None

    def test_create_project_with_metadata(self, store: GluonStore, project_path: Path):
        """Test creating a project with metadata."""
        metadata = {"language": "python"}
        project = store.create_project("test", project_path, metadata)

        assert project.metadata == metadata

    def test_get_project_by_id(self, store: GluonStore, project_path: Path):
        """Test getting a project by ID."""
        created = store.create_project("test", project_path)
        retrieved = store.get_project(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == created.name

    def test_get_project_by_name(self, store: GluonStore, project_path: Path):
        """Test getting a project by name."""
        store.create_project("test", project_path)
        retrieved = store.get_project_by_name("test")

        assert retrieved is not None
        assert retrieved.name == "test"

    def test_get_nonexistent_project(self, store: GluonStore):
        """Test getting a nonexistent project."""
        result = store.get_project("nonexistent")
        assert result is None

    def test_list_projects(self, store: GluonStore, tmp_path: Path):
        """Test listing projects."""
        path1 = tmp_path / "project1"
        path2 = tmp_path / "project2"
        path1.mkdir()
        path2.mkdir()

        store.create_project("alpha", path1)
        store.create_project("beta", path2)

        projects = store.list_projects()
        assert len(projects) == 2
        # Should be sorted by name
        assert projects[0].name == "alpha"
        assert projects[1].name == "beta"

    def test_update_project(self, store: GluonStore, project_path: Path):
        """Test updating a project."""
        project = store.create_project("test", project_path)
        project.metadata = {"updated": True}

        store.update_project(project)
        retrieved = store.get_project(project.id)

        assert retrieved is not None
        assert retrieved.metadata == {"updated": True}

    def test_delete_project(self, store: GluonStore, project_path: Path):
        """Test deleting a project."""
        project = store.create_project("test", project_path)
        result = store.delete_project(project.id)

        assert result is True
        assert store.get_project(project.id) is None

    def test_delete_nonexistent_project(self, store: GluonStore):
        """Test deleting a nonexistent project."""
        result = store.delete_project("nonexistent")
        assert result is False


class TestSessionCRUD:
    """Tests for session CRUD operations."""

    def test_create_session(self, store: GluonStore, project_path: Path):
        """Test creating a session."""
        project = store.create_project("test", project_path)
        session = store.create_session(project.id, "Hello Claude")

        assert session.project_id == project.id
        assert session.last_prompt == "Hello Claude"
        assert session.status == SessionStatus.ACTIVE

    def test_get_session(self, store: GluonStore, project_path: Path):
        """Test getting a session by ID."""
        project = store.create_project("test", project_path)
        created = store.create_session(project.id)

        retrieved = store.get_session(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id

    def test_get_latest_session(self, store: GluonStore, project_path: Path):
        """Test getting the latest session."""
        project = store.create_project("test", project_path)

        store.create_session(project.id, "First")  # Create first session
        session2 = store.create_session(project.id, "Second")

        latest = store.get_latest_session(project.id)
        assert latest is not None
        assert latest.id == session2.id

    def test_get_latest_session_by_status(self, store: GluonStore, project_path: Path):
        """Test getting the latest session filtered by status."""
        project = store.create_project("test", project_path)

        session1 = store.create_session(project.id, "First")
        session1.mark_paused()
        store.update_session(session1)

        session2 = store.create_session(project.id, "Second")
        session2.mark_completed()
        store.update_session(session2)

        # Should get session1 (paused), not session2 (completed)
        latest = store.get_latest_session(project.id, [SessionStatus.PAUSED])
        assert latest is not None
        assert latest.id == session1.id

    def test_list_sessions_by_project(self, store: GluonStore, project_path: Path):
        """Test listing sessions for a project."""
        project = store.create_project("test", project_path)
        store.create_session(project.id, "One")
        store.create_session(project.id, "Two")

        sessions = store.list_sessions(project.id)
        assert len(sessions) == 2

    def test_update_session(self, store: GluonStore, project_path: Path):
        """Test updating a session."""
        project = store.create_project("test", project_path)
        session = store.create_session(project.id)

        session.claude_session_id = "claude-123"
        session.total_cost_usd = 0.05
        session.mark_paused()

        store.update_session(session)
        retrieved = store.get_session(session.id)

        assert retrieved is not None
        assert retrieved.claude_session_id == "claude-123"
        assert retrieved.total_cost_usd == pytest.approx(0.05)
        assert retrieved.status == SessionStatus.PAUSED

    def test_delete_session(self, store: GluonStore, project_path: Path):
        """Test deleting a session."""
        project = store.create_project("test", project_path)
        session = store.create_session(project.id)

        result = store.delete_session(session.id)
        assert result is True
        assert store.get_session(session.id) is None

    def test_cascade_delete_sessions(self, store: GluonStore, project_path: Path):
        """Test that sessions are deleted when project is deleted."""
        project = store.create_project("test", project_path)
        session = store.create_session(project.id)

        store.delete_project(project.id)

        assert store.get_session(session.id) is None

    def test_get_active_sessions(self, store: GluonStore, project_path: Path):
        """Test getting active sessions."""
        project = store.create_project("test", project_path)

        session1 = store.create_session(project.id)  # Active
        session2 = store.create_session(project.id)
        session2.mark_paused()
        store.update_session(session2)

        session3 = store.create_session(project.id)
        session3.mark_completed()
        store.update_session(session3)

        active = store.get_active_sessions()
        assert len(active) == 2  # Active and Paused
        ids = [s.id for s in active]
        assert session1.id in ids
        assert session2.id in ids
        assert session3.id not in ids
