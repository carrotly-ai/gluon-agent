"""Work-queue routes (#162).

GET /api/queue (list), POST /api/queue (add), and the cancel/release actions.
Store-only, injected via Depends. The repeated WorkQueueItemResponse mapping is
unified into work_item_to_response — behaviour-identical to the four inline
constructions (fresh/released items have claimed_at/completed_at == None, which
the dynamic form reproduces) and locked by tests/test_api_activity_queue.py.
Paths unchanged → same fail-closed auth posture.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from gluon.models import WorkQueueItem, WorkQueueStatus, utc_now
from gluon.store import GluonStore
from gluon.web.models import WorkQueueAddRequest, WorkQueueItemResponse, WorkQueueListResponse
from gluon.web.routers._deps import get_store

router = APIRouter(tags=["work-queue"])

Store = Annotated[GluonStore, Depends(get_store)]


def work_item_to_response(item: WorkQueueItem) -> WorkQueueItemResponse:
    return WorkQueueItemResponse(
        id=item.id,
        project_id=item.project_id,
        prompt=item.prompt,
        profile=item.profile,
        priority=item.priority,
        status=item.status.value,
        claimed_by=item.claimed_by,
        created_at=item.created_at.isoformat(),
        claimed_at=item.claimed_at.isoformat() if item.claimed_at else None,
        completed_at=item.completed_at.isoformat() if item.completed_at else None,
        error_message=item.error_message,
    )


@router.get("/api/queue", response_model=WorkQueueListResponse)
async def list_queue(
    store: Store,
    project_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, le=100),
) -> WorkQueueListResponse:
    """List work queue items with optional filters."""
    items = store.list_work_items(project_id=project_id, status=status, limit=limit)
    return WorkQueueListResponse(
        items=[work_item_to_response(item) for item in items],
        total=len(items),
    )


@router.post("/api/queue", response_model=WorkQueueItemResponse)
async def add_to_queue(req: WorkQueueAddRequest, store: Store) -> WorkQueueItemResponse:
    """Add a new item to the work queue."""
    item = store.enqueue_work(
        project_id=req.project_id,
        prompt=req.prompt,
        profile=req.profile,
        priority=req.priority,
    )
    return work_item_to_response(item)


@router.post("/api/queue/{item_id}/cancel", response_model=WorkQueueItemResponse)
async def cancel_queue_item(item_id: str, store: Store) -> WorkQueueItemResponse:
    """Cancel a work queue item."""
    items = store.list_work_items()
    item = next((i for i in items if i.id == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Work queue item not found")
    if item.status not in (WorkQueueStatus.PENDING, WorkQueueStatus.CLAIMED):
        raise HTTPException(status_code=400, detail=f"Cannot cancel item in {item.status.value} status")

    item.status = WorkQueueStatus.CANCELLED
    item.completed_at = utc_now()
    store.update_work_item(item)

    return work_item_to_response(item)


@router.post("/api/queue/{item_id}/release", response_model=WorkQueueItemResponse)
async def release_queue_item(item_id: str, store: Store) -> WorkQueueItemResponse:
    """Release a claimed work queue item back to pending."""
    items = store.list_work_items()
    item = next((i for i in items if i.id == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Work queue item not found")
    if item.status != WorkQueueStatus.CLAIMED:
        raise HTTPException(status_code=400, detail="Can only release claimed items")

    item.status = WorkQueueStatus.PENDING
    item.claimed_by = None
    item.claimed_at = None
    store.update_work_item(item)

    return work_item_to_response(item)
