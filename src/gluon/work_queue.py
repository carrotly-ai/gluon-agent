"""Shared work queue for autonomous agent claiming (DYFJ self-propelling).

Idle agents claim tasks from the queue autonomously. Items are prioritized
and atomically claimed to prevent double-execution.
"""

import logging
from typing import TYPE_CHECKING

from gluon.models import WorkQueueItem, WorkQueueStatus, utc_now

if TYPE_CHECKING:
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


class WorkQueueManager:
    """Manages the shared work queue for autonomous agent claiming."""

    def __init__(self, store: "GluonStore"):
        self.store = store

    def enqueue(
        self,
        project_id: str,
        prompt: str,
        profile: str = "standard",
        priority: int = 10,
    ) -> WorkQueueItem:
        """Add a work item to the queue."""
        item = self.store.enqueue_work(
            project_id=project_id,
            prompt=prompt,
            profile=profile,
            priority=priority,
        )
        logger.info("Enqueued work item %s for project %s (priority=%d)", item.id, project_id, priority)
        return item

    def claim_next(self, project_id: str) -> WorkQueueItem | None:
        """Atomically claim highest-priority unclaimed item. Returns None if empty."""
        item = self.store.claim_work(project_id)
        if item:
            logger.info("Claimed work item %s for project %s", item.id, project_id)
        return item

    def mark_running(self, item_id: str, run_id: str) -> None:
        """Link claimed item to a run."""
        items = self.store.list_work_items()
        for item in items:
            if item.id == item_id:
                item.status = WorkQueueStatus.RUNNING
                item.claimed_by = run_id
                item.started_at = utc_now()
                self.store.update_work_item(item)
                logger.info("Work item %s now running as run %s", item_id, run_id)
                return

    def mark_completed(self, item_id: str) -> None:
        """Mark a work item as completed."""
        items = self.store.list_work_items()
        for item in items:
            if item.id == item_id:
                item.status = WorkQueueStatus.COMPLETED
                item.completed_at = utc_now()
                self.store.update_work_item(item)
                return

    def mark_failed(self, item_id: str, error: str) -> None:
        """Mark a work item as failed."""
        items = self.store.list_work_items()
        for item in items:
            if item.id == item_id:
                item.status = WorkQueueStatus.FAILED
                item.error_message = error
                item.completed_at = utc_now()
                self.store.update_work_item(item)
                return

    def release(self, item_id: str) -> None:
        """Release claimed item back to pending."""
        items = self.store.list_work_items()
        for item in items:
            if item.id == item_id:
                item.status = WorkQueueStatus.PENDING
                item.claimed_by = None
                item.claimed_at = None
                self.store.update_work_item(item)
                return

    def cancel(self, item_id: str) -> None:
        """Cancel a queued work item."""
        items = self.store.list_work_items()
        for item in items:
            if item.id == item_id:
                item.status = WorkQueueStatus.CANCELLED
                item.completed_at = utc_now()
                self.store.update_work_item(item)
                logger.info("Cancelled work item %s", item_id)
                return

    def release_stale_claims(self, threshold_secs: int = 1800) -> int:
        """Release items claimed >threshold ago with no heartbeat. Returns count."""
        released = self.store.release_stale_work_claims(threshold_secs)
        if released:
            logger.info("Released %d stale work queue claims", released)
        return released

    def list_items(
        self,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[WorkQueueItem]:
        """List work queue items."""
        return self.store.list_work_items(
            project_id=project_id,
            status=status,
            limit=limit,
        )
