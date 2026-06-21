"""Merge-queue routes (#162).

GET /api/merge-queue (list) and the retry/cancel actions. Store-only — these
only mutate the entry's DB status; the actual git merge is performed by a worker
elsewhere. The repeated MergeQueueEntryResponse mapping is unified into
merge_entry_to_response (the dynamic completed_at form is identical to the three
inline constructions for the reset/cancelled cases). Behaviour locked by
tests/test_api_merge_queue_witness.py. Paths unchanged → same fail-closed auth.
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
    """Retry a failed/conflicted merge queue entry."""
    entry = store.get_merge_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Merge queue entry not found")
    if entry.status not in (MergeQueueStatus.CONFLICT, MergeQueueStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Cannot retry entry in {entry.status.value} status")

    entry.status = MergeQueueStatus.PENDING
    entry.conflict_count = 0
    entry.last_error = None
    entry.completed_at = None
    store.update_merge_entry(entry)

    return merge_entry_to_response(entry)


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
