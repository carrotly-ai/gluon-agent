"""Log cleanup service for Gluon Agent.

Retention policies:
- Orphan logs (no DB record): deleted immediately
- Archived runs: logs deleted 30 days after completion
- Failed runs: logs deleted 7 days after completion
- Completed runs (non-archived): logs deleted 30 days after completion
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
COMPLETED_RETENTION_DAYS = 30

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
        completed_retention_days: int = COMPLETED_RETENTION_DAYS,
    ):
        self.store = store
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.archived_retention_days = archived_retention_days
        self.failed_retention_days = failed_retention_days
        self.completed_retention_days = completed_retention_days

    def cleanup(self) -> dict[str, int]:
        """
        Run cleanup based on retention policies.

        Returns:
            Dictionary with counts: {
                "orphan_deleted": N,
                "archived_deleted": N,
                "failed_deleted": N,
                "completed_deleted": N,
                "errors": N
            }
        """
        stats = {
            "orphan_deleted": 0,
            "archived_deleted": 0,
            "failed_deleted": 0,
            "completed_deleted": 0,
            "errors": 0,
        }

        if not self.log_dir.exists():
            logger.debug(f"Log directory does not exist: {self.log_dir}")
            return stats

        now = utc_now()
        archived_cutoff = now - timedelta(days=self.archived_retention_days)
        failed_cutoff = now - timedelta(days=self.failed_retention_days)
        completed_cutoff = now - timedelta(days=self.completed_retention_days)

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

                elif run.status == RunStatus.COMPLETED and not run.archived and run.completed_at:
                    # Completed (non-archived) run: check if past retention period
                    if run.completed_at < completed_cutoff:
                        self._delete_log_dir(run_id)
                        stats["completed_deleted"] += 1
                        logger.info(f"Deleted completed run logs: {run_id} (completed {run.completed_at.date()})")

            except Exception as e:
                logger.error(f"Error processing run {run_id}: {e}")
                stats["errors"] += 1

        total_deleted = (
            stats["orphan_deleted"] + stats["archived_deleted"] + stats["failed_deleted"] + stats["completed_deleted"]
        )
        if total_deleted > 0:
            logger.info(
                f"Cleanup complete: {stats['orphan_deleted']} orphan, "
                f"{stats['archived_deleted']} archived, "
                f"{stats['failed_deleted']} failed, "
                f"{stats['completed_deleted']} completed logs deleted"
            )
        else:
            logger.debug("Cleanup complete: no logs deleted")

        return stats

    def preview(self) -> dict[str, list[str]]:
        """Preview what would be cleaned without deleting.

        Returns:
            Dictionary with lists of run_ids by category:
            {
                "orphan": [...],
                "archived": [...],
                "failed": [...],
                "completed": [...]
            }
        """
        result: dict[str, list[str]] = {
            "orphan": [],
            "archived": [],
            "failed": [],
            "completed": [],
        }

        if not self.log_dir.exists():
            return result

        now = utc_now()
        archived_cutoff = now - timedelta(days=self.archived_retention_days)
        failed_cutoff = now - timedelta(days=self.failed_retention_days)
        completed_cutoff = now - timedelta(days=self.completed_retention_days)

        fs_run_ids = self._get_filesystem_run_ids()
        if not fs_run_ids:
            return result

        db_runs = self._get_all_runs_map()

        for run_id in fs_run_ids:
            run = db_runs.get(run_id)

            if run is None:
                result["orphan"].append(run_id)

            elif run.archived and run.completed_at:
                if run.completed_at < archived_cutoff:
                    result["archived"].append(run_id)

            elif run.status == RunStatus.FAILED and run.completed_at:
                if run.completed_at < failed_cutoff:
                    result["failed"].append(run_id)

            elif run.status == RunStatus.COMPLETED and not run.archived and run.completed_at:
                if run.completed_at < completed_cutoff:
                    result["completed"].append(run_id)

        return result

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

    def get_log_size(self, run_id: str) -> int:
        """Get total size in bytes of a run's log directory."""
        log_path = self.log_dir / run_id
        if not log_path.exists():
            return 0
        total = 0
        for entry in log_path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
        return total

    def get_disk_usage(self) -> dict[str, int | list[tuple[str, int]]]:
        """Get disk usage statistics for log directory.

        Returns:
            Dictionary with:
            {
                "total_bytes": N,
                "run_count": N,
                "top_runs": [(run_id, bytes), ...]  # Top 10 by size
            }
        """
        if not self.log_dir.exists():
            return {"total_bytes": 0, "run_count": 0, "top_runs": []}

        run_sizes: list[tuple[str, int]] = []
        for entry in self.log_dir.iterdir():
            if entry.is_dir():
                size = self.get_log_size(entry.name)
                run_sizes.append((entry.name, size))

        run_sizes.sort(key=lambda x: x[1], reverse=True)
        total = sum(size for _, size in run_sizes)

        return {
            "total_bytes": total,
            "run_count": len(run_sizes),
            "top_runs": run_sizes[:10],
        }
