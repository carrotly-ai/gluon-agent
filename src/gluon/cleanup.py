"""Log cleanup service for Gluon Agent.

Retention policies:
- Archived runs: logs deleted 30 days after execution
- Failed runs: logs deleted 7 days after execution
- Orphan logs (no DB record): deleted immediately
"""

from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from gluon.models import ExecutionRun, RunStatus, utc_now
from gluon.store import GluonStore

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Retention periods in days
ARCHIVED_RETENTION_DAYS = 30
FAILED_RETENTION_DAYS = 7

# Default log directory
DEFAULT_LOG_DIR = Path.home() / ".gluon" / "logs"


class LogCleanupService:
    """Service for cleaning up old log files based on retention policies."""

    def __init__(
        self,
        store: GluonStore,
        log_dir: Path | None = None,
        archived_retention_days: int = ARCHIVED_RETENTION_DAYS,
        failed_retention_days: int = FAILED_RETENTION_DAYS,
    ):
        self.store = store
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.archived_retention_days = archived_retention_days
        self.failed_retention_days = failed_retention_days

    def cleanup(self) -> dict[str, int]:
        """
        Run cleanup based on retention policies.

        Returns:
            Dictionary with counts: {
                "orphan_deleted": N,
                "archived_deleted": N,
                "failed_deleted": N,
                "errors": N
            }
        """
        stats = {
            "orphan_deleted": 0,
            "archived_deleted": 0,
            "failed_deleted": 0,
            "errors": 0,
        }

        if not self.log_dir.exists():
            logger.debug(f"Log directory does not exist: {self.log_dir}")
            return stats

        now = utc_now()
        archived_cutoff = now - timedelta(days=self.archived_retention_days)
        failed_cutoff = now - timedelta(days=self.failed_retention_days)

        # Get all run IDs from filesystem
        fs_run_ids = self._get_filesystem_run_ids()
        if not fs_run_ids:
            logger.debug("No log directories found")
            return stats

        logger.info(f"Found {len(fs_run_ids)} log directories to evaluate")

        # Get all runs from database for comparison
        db_runs = self._get_all_runs_map()

        for run_id in fs_run_ids:
            try:
                run = db_runs.get(run_id)

                if run is None:
                    # Orphan: exists in filesystem but not in database
                    self._delete_log_dir(run_id)
                    stats["orphan_deleted"] += 1
                    logger.info(f"Deleted orphan log directory: {run_id}")

                elif run.archived and run.completed_at:
                    # Archived run: check if past retention period
                    if run.completed_at < archived_cutoff:
                        self._delete_log_dir(run_id)
                        stats["archived_deleted"] += 1
                        logger.info(f"Deleted archived run logs: {run_id} (completed {run.completed_at.date()})")

                elif run.status == RunStatus.FAILED and run.completed_at:
                    # Failed run: check if past retention period
                    if run.completed_at < failed_cutoff:
                        self._delete_log_dir(run_id)
                        stats["failed_deleted"] += 1
                        logger.info(f"Deleted failed run logs: {run_id} (failed {run.completed_at.date()})")

            except Exception as e:
                logger.error(f"Error processing run {run_id}: {e}")
                stats["errors"] += 1

        total_deleted = stats["orphan_deleted"] + stats["archived_deleted"] + stats["failed_deleted"]
        if total_deleted > 0:
            logger.info(
                f"Cleanup complete: {stats['orphan_deleted']} orphan, "
                f"{stats['archived_deleted']} archived, "
                f"{stats['failed_deleted']} failed logs deleted"
            )
        else:
            logger.debug("Cleanup complete: no logs deleted")

        return stats

    def _get_filesystem_run_ids(self) -> set[str]:
        """Get all run IDs from log directory filesystem."""
        run_ids = set()
        try:
            for entry in self.log_dir.iterdir():
                if entry.is_dir():
                    run_ids.add(entry.name)
        except PermissionError as e:
            logger.error(f"Permission error reading log directory: {e}")
        return run_ids

    def _get_all_runs_map(self) -> dict[str, ExecutionRun]:
        """Get all runs from database as a map by ID."""
        # Query with a large limit to get all runs
        # Include archived to check retention
        runs = self.store.list_runs(limit=10000, include_archived=True)
        return {run.id: run for run in runs}

    def _delete_log_dir(self, run_id: str) -> None:
        """Delete log directory for a run."""
        log_path = self.log_dir / run_id
        if log_path.exists():
            shutil.rmtree(log_path, ignore_errors=True)
