"""Activity-log routes (#162).

GET /api/activity (filterable event list) and POST /api/activity/cleanup (prune
old events). Store-only, injected via Depends. Paths unchanged → same
fail-closed auth posture. Behaviour locked by tests/test_api_activity_queue.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from gluon.store import GluonStore
from gluon.web.models import ActivityEventResponse, ActivityListResponse
from gluon.web.routers._deps import get_store

router = APIRouter(tags=["activity"])

Store = Annotated[GluonStore, Depends(get_store)]


@router.get("/api/activity", response_model=ActivityListResponse)
async def list_activity(
    store: Store,
    actor: str | None = None,
    action: str | None = None,
    since: str | None = None,
    limit: int = Query(default=50, le=200),
) -> ActivityListResponse:
    """List activity events with optional filters."""
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'since' datetime format")

    events = store.list_activities(actor=actor, action=action, since=since_dt, limit=limit)
    return ActivityListResponse(
        events=[
            ActivityEventResponse(
                id=e.id,
                timestamp=e.timestamp.isoformat(),
                actor=e.actor,
                action=e.action,
                result=e.result,
                message=e.message,
                metadata=e.metadata,
            )
            for e in events
        ],
        total=len(events),
    )


@router.post("/api/activity/cleanup")
async def cleanup_activity(store: Store, days: int = Query(default=90, ge=1)) -> dict[str, Any]:
    """Delete activity events older than N days."""
    deleted = store.cleanup_activities(days=days)
    return {"deleted": deleted}
