"""Merge-queue history and cancellation routes.

GET /api/merge-queue (history) and cancellation remain supported. There is no
automatic merge processor, so the legacy retry endpoint refuses without
changing history and directs callers to the run's merge action.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from gluon.models import MergeQueueEntry, MergeQueueStatus, utc_now
from gluon.store import GluonStore
from gluon.web.models import MergeQueueEntryResponse, MergeQueueListResponse
from gluon.web.routers._deps import get_store

router = APIRouter(tags=["merge-queue"])

Store = Annotated[GluonStore, Depends(get_store)]


def merge_entry_to_response(entry: MergeQueueEntry) -> MergeQueueEntryResponse:
    return MergeQueueEntryResponse(
        id=entry.id,
        run_id=entry.run_id,
        project_id=entry.project_id,
        branch_name=entry.branch_name,
        pr_number=entry.pr_number,
        pr_url=entry.pr_url,
        status=entry.status.value,
        priority=entry.priority,
        conflict_count=entry.conflict_count,
        max_retries=entry.max_retries,
        last_error=entry.last_error,
        created_at=entry.created_at.isoformat(),
        completed_at=entry.completed_at.isoformat() if entry.completed_at else None,
    )


@router.get("/api/merge-queue", response_model=MergeQueueListResponse)
async def list_merge_queue(
    store: Store,
    status: str | None = None,
    limit: int = Query(default=20, le=100),
) -> MergeQueueListResponse:
    """List merge queue entries with optional filters."""
    entries = store.list_merge_entries(status=status, limit=limit)
    return MergeQueueListResponse(
        entries=[merge_entry_to_response(e) for e in entries],
        total=len(entries),
    )


@router.post("/api/merge-queue/{entry_id}/retry", response_model=MergeQueueEntryResponse)
async def retry_merge(entry_id: str, store: Store) -> MergeQueueEntryResponse:
    """Reject unsupported automatic retry while preserving the entry's history."""
    entry = store.get_merge_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Merge queue entry not found")
    if entry.status not in (MergeQueueStatus.CONFLICT, MergeQueueStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Cannot retry entry in {entry.status.value} status")

    raise HTTPException(
        status_code=409,
        detail=(
            "Merge queue retry is unavailable because there is no automatic merge processor. "
            f"Open run {entry.run_id} and use its Merge action, or merge the pull request on GitHub."
        ),
    )


@router.post("/api/merge-queue/{entry_id}/cancel", response_model=MergeQueueEntryResponse)
async def cancel_merge(entry_id: str, store: Store) -> MergeQueueEntryResponse:
    """Cancel a merge queue entry."""
    entry = store.get_merge_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Merge queue entry not found")
    if entry.status in (MergeQueueStatus.MERGED, MergeQueueStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Cannot cancel entry in {entry.status.value} status")

    entry.status = MergeQueueStatus.CANCELLED
    entry.completed_at = utc_now()
    store.update_merge_entry(entry)

    return merge_entry_to_response(entry)
