"""Tests for the log cleanup service."""

from datetime import timedelta
from pathlib import Path

import pytest

from gluon.cleanup import LogCleanupService
from gluon.models import RunStatus, utc_now
from gluon.store import GluonStore


@pytest.fixture
def temp_log_dir(tmp_path: Path) -> Path:
    """Create a temporary log directory."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


@pytest.fixture
def store(tmp_path: Path) -> GluonStore:
    """Create a temporary store."""
    db_path = tmp_path / "test.db"
    return GluonStore(db_path=db_path)


def create_test_run(
    store: GluonStore,
    project_id: str,
    status: RunStatus = RunStatus.PENDING,
    completed_at_days_ago: int | None = None,
    archived: bool = False,
    error_message: str | None = None,
):
    """Helper to create a run with specific attributes."""
    run = store.create_run(
        project_id=project_id,
        prompt="test prompt",
        initiator="test",
    )
    run.status = status
    if completed_at_days_ago is not None:
        run.completed_at = utc_now() - timedelta(days=completed_at_days_ago)
    if error_message:
        run.error_message = error_message
    store.update_run(run)

    # Archive using the dedicated method (which updates the DB directly)
    if archived:
        store.archive_run(run.id, archived=True)
        # Re-fetch to get updated archived state
        run = store.get_run(run.id)
        # Manually set archived_at to the past for testing
        if run and completed_at_days_ago is not None:
            with store._get_conn() as conn:
                archived_at = (utc_now() - timedelta(days=completed_at_days_ago)).isoformat()
                conn.execute(
                    "UPDATE execution_runs SET archived_at = ? WHERE id = ?",
                    (archived_at, run.id),
                )
            run = store.get_run(run.id)

    return run


class TestLogCleanupService:
    """Tests for LogCleanupService."""

    def test_cleanup_orphan_logs(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test that orphan log directories (no DB record) are deleted immediately."""
        # Create an orphan log directory (no matching run in DB)
        orphan_id = "orphan-run-12345"
        orphan_dir = temp_log_dir / orphan_id
        orphan_dir.mkdir()
        (orphan_dir / "stdout.log").write_text("orphan logs")

        service = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service.cleanup()

        assert stats["orphan_deleted"] == 1
        assert not orphan_dir.exists()

    def test_cleanup_archived_run_logs(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test that archived run logs are deleted after 30 days."""
        # Create a project first
        project = store.create_project(name="test-project", path=Path("/tmp/test"))

        # Create an archived run completed 31 days ago
        run = create_test_run(
            store=store,
            project_id=project.id,
            status=RunStatus.COMPLETED,
            completed_at_days_ago=31,
            archived=True,
        )

        # Create log directory for this run
        log_dir = temp_log_dir / run.id
        log_dir.mkdir()
        (log_dir / "stdout.log").write_text("old archived logs")

        service = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service.cleanup()

        assert stats["archived_deleted"] == 1
        assert not log_dir.exists()

    def test_keep_recent_archived_run_logs(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test that archived run logs less than 30 days old are kept."""
        # Create a project first
        project = store.create_project(name="test-project", path=Path("/tmp/test"))

        # Create an archived run completed 10 days ago
        run = create_test_run(
            store=store,
            project_id=project.id,
            status=RunStatus.COMPLETED,
            completed_at_days_ago=10,
            archived=True,
        )

        # Create log directory for this run
        log_dir = temp_log_dir / run.id
        log_dir.mkdir()
        (log_dir / "stdout.log").write_text("recent archived logs")

        service = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service.cleanup()

        assert stats["archived_deleted"] == 0
        assert log_dir.exists()

    def test_cleanup_failed_run_logs(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test that failed run logs are deleted after 7 days."""
        # Create a project first
        project = store.create_project(name="test-project", path=Path("/tmp/test"))

        # Create a failed run completed 8 days ago
        run = create_test_run(
            store=store,
            project_id=project.id,
            status=RunStatus.FAILED,
            completed_at_days_ago=8,
            error_message="test error",
        )

        # Create log directory for this run
        log_dir = temp_log_dir / run.id
        log_dir.mkdir()
        (log_dir / "stdout.log").write_text("old failed logs")

        service = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service.cleanup()

        assert stats["failed_deleted"] == 1
        assert not log_dir.exists()

    def test_keep_recent_failed_run_logs(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test that failed run logs less than 7 days old are kept."""
        # Create a project first
        project = store.create_project(name="test-project", path=Path("/tmp/test"))

        # Create a failed run completed 3 days ago
        run = create_test_run(
            store=store,
            project_id=project.id,
            status=RunStatus.FAILED,
            completed_at_days_ago=3,
            error_message="test error",
        )

        # Create log directory for this run
        log_dir = temp_log_dir / run.id
        log_dir.mkdir()
        (log_dir / "stdout.log").write_text("recent failed logs")

        service = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service.cleanup()

        assert stats["failed_deleted"] == 0
        assert log_dir.exists()

    def test_keep_active_run_logs(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test that active (running/pending) run logs are never deleted."""
        # Create a project first
        project = store.create_project(name="test-project", path=Path("/tmp/test"))

        # Create a running run
        run = create_test_run(
            store=store,
            project_id=project.id,
            status=RunStatus.RUNNING,
        )

        # Create log directory for this run
        log_dir = temp_log_dir / run.id
        log_dir.mkdir()
        (log_dir / "stdout.log").write_text("running logs")

        service = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service.cleanup()

        assert stats["orphan_deleted"] == 0
        assert stats["archived_deleted"] == 0
        assert stats["failed_deleted"] == 0
        assert log_dir.exists()

    def test_keep_completed_not_archived_logs(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test that completed but not archived run logs are kept."""
        # Create a project first
        project = store.create_project(name="test-project", path=Path("/tmp/test"))

        # Create a completed run (not archived)
        run = create_test_run(
            store=store,
            project_id=project.id,
            status=RunStatus.COMPLETED,
            completed_at_days_ago=60,  # Old but not archived
        )

        # Create log directory for this run
        log_dir = temp_log_dir / run.id
        log_dir.mkdir()
        (log_dir / "stdout.log").write_text("completed logs")

        service = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service.cleanup()

        assert stats["archived_deleted"] == 0
        assert log_dir.exists()

    def test_no_log_directory(self, store: GluonStore, tmp_path: Path) -> None:
        """Test cleanup when log directory doesn't exist."""
        non_existent_dir = tmp_path / "nonexistent"
        service = LogCleanupService(store=store, log_dir=non_existent_dir)
        stats = service.cleanup()

        assert stats["orphan_deleted"] == 0
        assert stats["archived_deleted"] == 0
        assert stats["failed_deleted"] == 0
        assert stats["errors"] == 0

    def test_custom_retention_periods(self, store: GluonStore, temp_log_dir: Path) -> None:
        """Test cleanup with custom retention periods."""
        # Create a project first
        project = store.create_project(name="test-project", path=Path("/tmp/test"))

        # Create an archived run completed 5 days ago
        run = create_test_run(
            store=store,
            project_id=project.id,
            status=RunStatus.COMPLETED,
            completed_at_days_ago=5,
            archived=True,
        )

        # Create log directory
        log_dir = temp_log_dir / run.id
        log_dir.mkdir()
        (log_dir / "stdout.log").write_text("logs")

        # With default 30-day retention, logs should be kept
        service_default = LogCleanupService(store=store, log_dir=temp_log_dir)
        stats = service_default.cleanup()
        assert stats["archived_deleted"] == 0
        assert log_dir.exists()

        # With custom 3-day retention, logs should be deleted
        service_custom = LogCleanupService(store=store, log_dir=temp_log_dir, archived_retention_days=3)
        stats = service_custom.cleanup()
        assert stats["archived_deleted"] == 1
        assert not log_dir.exists()
