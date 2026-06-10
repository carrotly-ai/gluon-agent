"""Sequential merge processing for PRs from parallel agent runs.

Processes merges one at a time: test merge (dry-run), then apply merge.
Handles conflicts with exponential backoff retries.

NOTE (remediation WS-7): ``MergeQueueService`` is currently exercised only by
``tests/test_merge_queue.py`` — nothing in the running app instantiates it or
calls ``process_next``. The ``/api/merge-queue`` endpoints and the web-ui page
operate on the ``merge_queue`` store rows directly; this processor layer is not
yet wired into the runtime. Kept (not deleted) because it is the intended
processor for an automated merge queue; wiring it (instantiate + a processor
loop driven on merge-queue mutations) is tracked as follow-up work. Do not
assume the merge queue auto-processes entries until that wiring lands.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from gluon.models import ExecutionRun, MergeQueueEntry, MergeQueueStatus, utc_now

if TYPE_CHECKING:
    from gluon.git_manager import GitManager
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


class MergeQueueService:
    """Sequential merge queue processor."""

    def __init__(self, store: "GluonStore", git_manager: "GitManager"):
        self.store = store
        self.git_manager = git_manager

    def enqueue(self, run: ExecutionRun) -> MergeQueueEntry:
        """Add run's PR to merge queue."""
        entry = MergeQueueEntry(
            run_id=run.id,
            project_id=run.project_id,
            branch_name=run.branch_name or "",
            pr_number=run.pr_number,
            pr_url=run.pr_url,
        )
        entry = self.store.enqueue_merge(entry)
        logger.info(
            "Enqueued merge for run %s branch %s (PR #%s)",
            run.id[:8],
            entry.branch_name,
            entry.pr_number,
        )
        return entry

    async def process_next(self) -> MergeQueueEntry | None:
        """Process highest-priority pending entry. Returns processed entry or None."""
        entries = self.store.list_merge_entries(status=MergeQueueStatus.PENDING.value)

        # Filter out entries with future retry times
        now = utc_now()
        ready = [e for e in entries if not e.next_retry_at or e.next_retry_at <= now]
        if not ready:
            return None

        entry = ready[0]
        entry.status = MergeQueueStatus.TESTING
        entry.processing_started_at = utc_now()
        self.store.update_merge_entry(entry)

        # Test merge (dry-run)
        success, error = await self.test_merge(entry)
        if success:
            entry.status = MergeQueueStatus.MERGING
            self.store.update_merge_entry(entry)

            merged = await self.apply_merge(entry)
            if merged:
                entry.status = MergeQueueStatus.MERGED
                entry.completed_at = utc_now()
                self.store.update_merge_entry(entry)
                logger.info("Merged entry %s (branch %s)", entry.id, entry.branch_name)
            else:
                entry.status = MergeQueueStatus.FAILED
                entry.last_error = "Merge apply failed"
                entry.completed_at = utc_now()
                self.store.update_merge_entry(entry)
        else:
            entry.conflict_count += 1
            entry.last_error = error
            if self.should_retry(entry):
                entry.status = MergeQueueStatus.PENDING
                backoff = self.calculate_backoff(entry.conflict_count)
                entry.next_retry_at = utc_now() + timedelta(seconds=backoff)
                self.store.update_merge_entry(entry)
                logger.info(
                    "Merge conflict for %s (attempt %d), retry in %ds",
                    entry.id,
                    entry.conflict_count,
                    backoff,
                )
            else:
                entry.status = MergeQueueStatus.FAILED
                entry.completed_at = utc_now()
                self.store.update_merge_entry(entry)
                logger.warning(
                    "Merge failed for %s after %d retries",
                    entry.id,
                    entry.conflict_count,
                )

        return entry

    async def test_merge(self, entry: MergeQueueEntry) -> tuple[bool, str]:
        """Dry-run merge. Returns (success, error_message)."""
        project = self.store.get_project(entry.project_id)
        if not project:
            return False, f"Project not found: {entry.project_id}"

        try:
            rc, stdout, stderr = await self.git_manager._run_git(
                project.expanded_path,
                "merge",
                "--no-commit",
                "--no-ff",
                entry.branch_name,
            )
            if rc == 0:
                # Abort the test merge to clean up
                await self.git_manager._run_git(project.expanded_path, "merge", "--abort")
                return True, ""
            return False, stderr or "Merge conflict"
        except Exception as e:
            return False, str(e)

    async def apply_merge(self, entry: MergeQueueEntry) -> bool:
        """Execute actual merge. Returns success."""
        project = self.store.get_project(entry.project_id)
        if not project:
            return False

        try:
            rc, stdout, stderr = await self.git_manager._run_git(
                project.expanded_path,
                "merge",
                "--no-ff",
                entry.branch_name,
                "-m",
                f"Merge branch '{entry.branch_name}' (gluon merge queue)",
            )
            if rc == 0:
                # Push merged result
                await self.git_manager._run_git(project.expanded_path, "push")
                return True
            return False
        except Exception:
            logger.debug("Merge apply failed", exc_info=True)
            return False

    def should_retry(self, entry: MergeQueueEntry) -> bool:
        """Check if entry should be retried."""
        return entry.conflict_count < entry.max_retries

    def calculate_backoff(self, conflict_count: int) -> int:
        """Exponential backoff: 60 * 2^count seconds."""
        return int(60 * (2**conflict_count))
