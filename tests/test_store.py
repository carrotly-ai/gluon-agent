"""Tests for Gluon store."""

import time
from pathlib import Path

import pytest

from gluon.models import RunStatus, SessionStatus, TodoSnapshot
from gluon.store import GluonStore


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


class TestExecutionRunCRUD:
    """Tests for execution run CRUD operations."""

    def test_create_run(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "fix the bug")

        assert run.id is not None
        assert run.project_id == project.id
        assert run.prompt == "fix the bug"
        assert run.status == RunStatus.PENDING
        assert run.created_at is not None

    def test_create_run_with_optional_fields(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(
            project.id,
            "add tests",
            initiator="telegram:123",
            model="claude-sonnet-4.6",
        )

        assert run.initiator == "telegram:123"
        assert run.model == "claude-sonnet-4.6"

    def test_get_run(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        created = store.create_run(project.id, "test prompt")

        retrieved = store.get_run(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.prompt == "test prompt"

    def test_get_run_nonexistent(self, store: GluonStore):
        assert store.get_run("nonexistent-id") is None

    def test_get_run_by_short_id(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test")
        short_id = run.id[:8]

        retrieved = store.get_run_by_short_id(short_id)
        assert retrieved is not None
        assert retrieved.id == run.id

    def test_get_run_by_short_id_too_short(self, store: GluonStore):
        result = store.get_run_by_short_id("ab")
        assert result is None

    def test_update_run(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test")
        run.status = RunStatus.RUNNING
        run.cost_usd = 0.05
        store.update_run(run)

        retrieved = store.get_run(run.id)
        assert retrieved is not None
        assert retrieved.status == RunStatus.RUNNING
        assert retrieved.cost_usd == pytest.approx(0.05)

    def test_list_runs(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        store.create_run(project.id, "task 1")
        store.create_run(project.id, "task 2")
        store.create_run(project.id, "task 3")

        runs = store.list_runs()
        assert len(runs) == 3

    def test_list_runs_by_project(self, store: GluonStore, tmp_path: Path):
        path1 = tmp_path / "p1"
        path2 = tmp_path / "p2"
        path1.mkdir()
        path2.mkdir()

        p1 = store.create_project("proj1", path1)
        p2 = store.create_project("proj2", path2)
        store.create_run(p1.id, "task for p1")
        store.create_run(p2.id, "task for p2")

        runs = store.list_runs(project_id=p1.id)
        assert len(runs) == 1
        assert runs[0].project_id == p1.id

    def test_list_runs_by_status(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run1 = store.create_run(project.id, "task 1")
        store.create_run(project.id, "task 2")

        run1.status = RunStatus.RUNNING
        store.update_run(run1)

        running_runs = store.list_runs(statuses=[RunStatus.RUNNING])
        assert len(running_runs) == 1
        assert running_runs[0].id == run1.id

    def test_list_runs_with_limit(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        for i in range(10):
            store.create_run(project.id, f"task {i}")

        runs = store.list_runs(limit=5)
        assert len(runs) == 5

    def test_list_active_runs(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run1 = store.create_run(project.id, "pending task")  # PENDING
        run2 = store.create_run(project.id, "running task")
        run2.status = RunStatus.RUNNING
        store.update_run(run2)
        run3 = store.create_run(project.id, "done task")
        run3.status = RunStatus.COMPLETED
        store.update_run(run3)

        active = store.list_active_runs()
        active_ids = [r.id for r in active]
        assert run1.id in active_ids
        assert run2.id in active_ids
        assert run3.id not in active_ids

    def test_update_run_status(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test")

        updated = store.update_run_status(run.id, RunStatus.CANCELLED)
        assert updated is not None
        assert updated.status == RunStatus.CANCELLED
        assert updated.completed_at is not None

    def test_delete_run(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test")

        result = store.delete_run(run.id)
        assert result is True
        assert store.get_run(run.id) is None

    def test_delete_run_nonexistent(self, store: GluonStore):
        result = store.delete_run("nonexistent")
        assert result is False


class TestTodoSnapshotCRUD:
    """Tests for todo snapshot CRUD operations."""

    def test_save_and_get_latest_snapshot(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")

        todos = [
            {"content": "Fix bug", "status": "completed", "activeForm": "Fixing bug"},
            {"content": "Add tests", "status": "in_progress", "activeForm": "Adding tests"},
            {"content": "Update docs", "status": "pending", "activeForm": "Updating docs"},
        ]
        snapshot1 = TodoSnapshot.from_tool_input(run.id, todos)
        store.save_todo_snapshot(snapshot1)

        # Save a second snapshot (all completed)
        time.sleep(0.01)  # Ensure different timestamp
        todos2 = [
            {"content": "Fix bug", "status": "completed", "activeForm": "Fixing bug"},
            {"content": "Add tests", "status": "completed", "activeForm": "Adding tests"},
            {"content": "Update docs", "status": "completed", "activeForm": "Updating docs"},
        ]
        snapshot2 = TodoSnapshot.from_tool_input(run.id, todos2)
        store.save_todo_snapshot(snapshot2)

        # get_latest should return the second snapshot
        latest = store.get_latest_todo_snapshot(run.id)
        assert latest is not None
        assert latest.id == snapshot2.id
        assert latest.completed_count == 3
        assert latest.todo_count == 3

    def test_get_latest_snapshot_nonexistent_run(self, store: GluonStore):
        result = store.get_latest_todo_snapshot("nonexistent-id")
        assert result is None

    def test_list_snapshots(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")

        # Save 3 snapshots
        for i in range(3):
            time.sleep(0.01)
            todos = [{"content": f"Task {i}", "status": "pending", "activeForm": f"Doing {i}"}]
            snapshot = TodoSnapshot.from_tool_input(run.id, todos)
            store.save_todo_snapshot(snapshot)

        snapshots = store.list_todo_snapshots(run.id)
        assert len(snapshots) == 3
        # Should be newest-first
        assert snapshots[0].todos[0]["content"] == "Task 2"
        assert snapshots[2].todos[0]["content"] == "Task 0"

    def test_snapshot_counts(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")

        todos = [
            {"content": "A", "status": "completed", "activeForm": "A"},
            {"content": "B", "status": "completed", "activeForm": "B"},
            {"content": "C", "status": "in_progress", "activeForm": "C"},
            {"content": "D", "status": "pending", "activeForm": "D"},
            {"content": "E", "status": "pending", "activeForm": "E"},
        ]
        snapshot = TodoSnapshot.from_tool_input(run.id, todos)
        store.save_todo_snapshot(snapshot)

        retrieved = store.get_latest_todo_snapshot(run.id)
        assert retrieved is not None
        assert retrieved.todo_count == 5
        assert retrieved.completed_count == 2
        assert retrieved.in_progress_count == 1
        assert retrieved.pending_count == 2

    def test_list_snapshots_respects_limit(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        run = store.create_run(project.id, "test task")

        for i in range(5):
            time.sleep(0.01)
            todos = [{"content": f"Task {i}", "status": "pending", "activeForm": f"Doing {i}"}]
            store.save_todo_snapshot(TodoSnapshot.from_tool_input(run.id, todos))

        snapshots = store.list_todo_snapshots(run.id, limit=2)
        assert len(snapshots) == 2
