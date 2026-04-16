"""Cleanup services for Gluon Agent.

Log retention policies:
- Orphan logs (no DB record): deleted immediately
- Archived runs: logs deleted 30 days after completion
- Failed runs: logs deleted 7 days after completion
- Completed runs (non-archived): logs deleted 30 days after completion

Worktree retention policies:
- Orphan worktrees (no DB record): deleted immediately
- Merged PRs: deleted immediately
- Completed/failed/cancelled runs: deleted after retention period (default 7 days)
- Active runs (pending/running/review): never deleted
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from gluon.models import ExecutionRun, RunStatus, utc_now
from gluon.store import GluonStore

if TYPE_CHECKING:
    pass


class DiskUsageStats(TypedDict):
    """Type definition for disk usage statistics."""

    total_bytes: int
    run_count: int
    top_runs: list[tuple[str, int]]


class WorktreeInfo(TypedDict):
    """Info about a worktree on disk."""

    path: Path
    run_id: str | None
    size_bytes: int
    reason: str


logger = logging.getLogger(__name__)

# Retention periods in days
ARCHIVED_RETENTION_DAYS = 30
FAILED_RETENTION_DAYS = 7
COMPLETED_RETENTION_DAYS = 30
WORKTREE_RETENTION_DAYS = 7

# Default directories
DEFAULT_LOG_DIR = Path.home() / ".gluon" / "logs"
DEFAULT_WORKTREE_DIR = Path("/tmp/gluon-worktrees")


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

    def get_disk_usage(self) -> DiskUsageStats:
        """Get disk usage statistics for log directory.

        Returns:
            DiskUsageStats with total_bytes, run_count, and top_runs.
        """
        if not self.log_dir.exists():
            return DiskUsageStats(total_bytes=0, run_count=0, top_runs=[])

        run_sizes: list[tuple[str, int]] = []
        for entry in self.log_dir.iterdir():
            if entry.is_dir():
                size = self.get_log_size(entry.name)
                run_sizes.append((entry.name, size))

        run_sizes.sort(key=lambda x: x[1], reverse=True)
        total = sum(size for _, size in run_sizes)

        return DiskUsageStats(
            total_bytes=total,
            run_count=len(run_sizes),
            top_runs=run_sizes[:10],
        )


class WorktreeCleanupService:
    """Service for cleaning up stale Git worktrees created by background runs.

    Worktrees are created in /tmp/gluon-worktrees/wt-{run_id} for isolated
    task execution. Background runs (via runner.py) intentionally leave
    worktrees alive so users can inspect branches and create PRs. This
    service garbage-collects them after the retention window expires.

    Retention rules (evaluated in order):
    - Active runs (pending/running/review): never deleted
    - Merged PRs: eligible immediately
    - Completed/failed/cancelled: eligible after retention_days
    - Orphan (no matching DB run): eligible immediately
    """

    def __init__(
        self,
        store: GluonStore,
        worktree_dir: Path | None = None,
        retention_days: int = WORKTREE_RETENTION_DAYS,
    ):
        self.store = store
        self.worktree_dir = worktree_dir or DEFAULT_WORKTREE_DIR
        self.retention_days = retention_days

    def cleanup(self) -> dict[str, int]:
        """Run worktree garbage collection.

        Returns:
            Dictionary with counts: {
                "orphan_deleted": N,
                "merged_deleted": N,
                "expired_deleted": N,
                "git_pruned": N,
                "errors": N,
                "bytes_freed": N,
            }
        """
        stats = {
            "orphan_deleted": 0,
            "merged_deleted": 0,
            "expired_deleted": 0,
            "git_pruned": 0,
            "errors": 0,
            "bytes_freed": 0,
        }

        if not self.worktree_dir.exists():
            logger.debug(f"Worktree directory does not exist: {self.worktree_dir}")
            return stats

        now = utc_now()
        cutoff = now - timedelta(days=self.retention_days)

        fs_worktrees = self._get_filesystem_worktrees()
        if not fs_worktrees:
            logger.debug("No worktree directories found")
            return stats

        logger.info(f"Found {len(fs_worktrees)} worktree directories to evaluate")

        db_runs = self._get_worktree_runs_map()
        repo_paths_to_prune: set[str] = set()

        for wt_dir_name, wt_path in fs_worktrees.items():
            try:
                run = self._find_run_for_worktree(wt_dir_name, wt_path, db_runs)

                if run is None:
                    # Orphan: no matching run in DB
                    size = self._get_dir_size(wt_path)
                    self._remove_worktree(wt_path, repo_paths_to_prune)
                    stats["orphan_deleted"] += 1
                    stats["bytes_freed"] += size
                    logger.info(f"Deleted orphan worktree: {wt_dir_name}")

                elif run.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.REVIEW):
                    # Active run — never touch
                    continue

                elif run.pr_status == "merged":
                    # PR merged — work is safely in the base branch
                    size = self._get_dir_size(wt_path)
                    self._remove_worktree(wt_path, repo_paths_to_prune)
                    stats["merged_deleted"] += 1
                    stats["bytes_freed"] += size
                    logger.info(f"Deleted merged worktree: {wt_dir_name} (PR #{run.pr_number})")

                elif run.completed_at and run.completed_at < cutoff:
                    # Expired: completed/failed/cancelled past retention
                    size = self._get_dir_size(wt_path)
                    self._remove_worktree(wt_path, repo_paths_to_prune)
                    stats["expired_deleted"] += 1
                    stats["bytes_freed"] += size
                    logger.info(
                        f"Deleted expired worktree: {wt_dir_name} "
                        f"(status={run.status}, completed {run.completed_at.date()})"
                    )

            except Exception as e:
                logger.error(f"Error processing worktree {wt_dir_name}: {e}")
                stats["errors"] += 1

        # Prune stale git worktree references in each affected repo
        for repo_path in repo_paths_to_prune:
            try:
                self._git_worktree_prune(Path(repo_path))
                stats["git_pruned"] += 1
            except Exception as e:
                logger.warning(f"Failed to prune git worktree refs in {repo_path}: {e}")

        total_deleted = stats["orphan_deleted"] + stats["merged_deleted"] + stats["expired_deleted"]
        if total_deleted > 0:
            freed_mb = stats["bytes_freed"] / (1024 * 1024)
            logger.info(
                f"Worktree cleanup complete: {stats['orphan_deleted']} orphan, "
                f"{stats['merged_deleted']} merged, {stats['expired_deleted']} expired deleted "
                f"({freed_mb:.1f} MB freed)"
            )
        else:
            logger.debug("Worktree cleanup complete: no worktrees deleted")

        return stats

    def preview(self) -> dict[str, list[WorktreeInfo]]:
        """Preview what would be cleaned without deleting.

        Returns:
            Dictionary with lists of WorktreeInfo by category.
        """
        result: dict[str, list[WorktreeInfo]] = {
            "orphan": [],
            "merged": [],
            "expired": [],
            "active": [],
            "retained": [],
        }

        if not self.worktree_dir.exists():
            return result

        now = utc_now()
        cutoff = now - timedelta(days=self.retention_days)

        fs_worktrees = self._get_filesystem_worktrees()
        if not fs_worktrees:
            return result

        db_runs = self._get_worktree_runs_map()

        for wt_dir_name, wt_path in fs_worktrees.items():
            run = self._find_run_for_worktree(wt_dir_name, wt_path, db_runs)
            size = self._get_dir_size(wt_path)
            run_id = run.id if run else None

            if run is None:
                result["orphan"].append(
                    WorktreeInfo(
                        path=wt_path,
                        run_id=None,
                        size_bytes=size,
                        reason="no matching run in DB",
                    )
                )

            elif run.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.REVIEW):
                result["active"].append(
                    WorktreeInfo(
                        path=wt_path,
                        run_id=run_id,
                        size_bytes=size,
                        reason=f"run is {run.status}",
                    )
                )

            elif run.pr_status == "merged":
                result["merged"].append(
                    WorktreeInfo(
                        path=wt_path,
                        run_id=run_id,
                        size_bytes=size,
                        reason=f"PR #{run.pr_number} merged",
                    )
                )

            elif run.completed_at and run.completed_at < cutoff:
                days_ago = (now - run.completed_at).days
                result["expired"].append(
                    WorktreeInfo(
                        path=wt_path,
                        run_id=run_id,
                        size_bytes=size,
                        reason=f"{run.status} {days_ago}d ago (retention: {self.retention_days}d)",
                    )
                )

            else:
                remaining = ""
                if run.completed_at:
                    days_left = self.retention_days - (now - run.completed_at).days
                    remaining = f", {days_left}d until eligible"
                result["retained"].append(
                    WorktreeInfo(
                        path=wt_path,
                        run_id=run_id,
                        size_bytes=size,
                        reason=f"{run.status}{remaining}",
                    )
                )

        return result

    def get_disk_usage(self) -> DiskUsageStats:
        """Get disk usage statistics for worktree directory."""
        if not self.worktree_dir.exists():
            return DiskUsageStats(total_bytes=0, run_count=0, top_runs=[])

        wt_sizes: list[tuple[str, int]] = []
        for entry in self.worktree_dir.iterdir():
            if entry.is_dir():
                size = self._get_dir_size(entry)
                wt_sizes.append((entry.name, size))

        wt_sizes.sort(key=lambda x: x[1], reverse=True)
        total = sum(size for _, size in wt_sizes)

        return DiskUsageStats(
            total_bytes=total,
            run_count=len(wt_sizes),
            top_runs=wt_sizes[:10],
        )

    def _get_filesystem_worktrees(self) -> dict[str, Path]:
        """Get all worktree directories from filesystem. Returns {dir_name: path}."""
        worktrees: dict[str, Path] = {}
        try:
            for entry in self.worktree_dir.iterdir():
                if entry.is_dir() and entry.name.startswith("wt-"):
                    worktrees[entry.name] = entry
        except PermissionError as e:
            logger.error(f"Permission error reading worktree directory: {e}")
        return worktrees

    def _get_worktree_runs_map(self) -> dict[str, ExecutionRun]:
        """Get all worktree runs from database, keyed by run ID."""
        runs = self.store.list_runs(limit=10000, include_archived=True)
        return {run.id: run for run in runs if run.use_worktree}

    def _find_run_for_worktree(
        self, wt_dir_name: str, wt_path: Path, db_runs: dict[str, ExecutionRun]
    ) -> ExecutionRun | None:
        """Match a worktree directory to its DB run.

        Worktree dirs are named wt-{run_id[:8]}. Match by:
        1. Exact worktree_path match in DB
        2. Run ID prefix from directory name
        """
        # Try matching by worktree_path stored in the run
        wt_path_str = str(wt_path)
        for run in db_runs.values():
            if run.worktree_path == wt_path_str:
                return run

        # Fall back to run ID prefix from dir name (wt-{run_id[:8]})
        if wt_dir_name.startswith("wt-"):
            short_id = wt_dir_name[3:]  # strip "wt-"
            for run_id, run in db_runs.items():
                if run_id.startswith(short_id):
                    return run

        return None

    def _remove_worktree(self, wt_path: Path, repo_paths_to_prune: set[str]) -> None:
        """Remove a worktree directory and track its repo for git pruning."""
        # Try to find the main repo path from the worktree's .git file
        git_file = wt_path / ".git"
        if git_file.is_file():
            try:
                content = git_file.read_text().strip()
                # Format: "gitdir: /path/to/repo/.git/worktrees/wt-xxx"
                if content.startswith("gitdir:"):
                    gitdir = content.split(":", 1)[1].strip()
                    # Walk up from .git/worktrees/wt-xxx to .git to repo root
                    git_dir = Path(gitdir)
                    if "worktrees" in git_dir.parts:
                        # .git/worktrees/wt-xxx -> .git -> repo
                        main_git_dir = git_dir.parent.parent
                        if main_git_dir.name == ".git":
                            repo_paths_to_prune.add(str(main_git_dir.parent))
            except (OSError, ValueError):
                pass

        shutil.rmtree(wt_path, ignore_errors=True)

    @staticmethod
    def _git_worktree_prune(repo_path: Path) -> None:
        """Run 'git worktree prune' to clean stale worktree references."""
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=repo_path,
            capture_output=True,
            timeout=30,
        )

    @staticmethod
    def _get_dir_size(path: Path) -> int:
        """Get total size in bytes of a directory."""
        total = 0
        try:
            for entry in path.rglob("*"):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
        except (PermissionError, OSError):
            pass
        return total
