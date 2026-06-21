"""Notification routes (#162).

Extracted from create_app. These handlers depend only on the store (injected via
Depends) plus a pure response mapper, so they move cleanly to an APIRouter.
Paths are unchanged, so the app-wide fail-closed auth middleware still applies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from gluon.models import Notification
from gluon.store import GluonStore
from gluon.web.models import (
    NotificationResponse,
    NotificationsListResponse,
)
from gluon.web.routers._deps import get_store

router = APIRouter(tags=["notifications"])


def _notification_to_response(n: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=n.id,
        workspace_id=n.workspace_id,
        project_id=n.project_id,
        run_id=n.run_id,
        session_id=n.session_id,
        type=n.type.value,
        severity=n.severity.value,
        title=n.title,
        message=n.message,
        metadata=n.metadata,
        read=n.read,
        created_at=n.created_at.isoformat(),
        read_at=n.read_at.isoformat() if n.read_at else None,
    )


@router.get("/api/notifications", response_model=NotificationsListResponse)
async def list_notifications(
    store: Annotated[GluonStore, Depends(get_store)],
    workspace_id: str | None = None,
    unread_only: bool = False,
    limit: int = Query(default=50, le=200),
) -> NotificationsListResponse:
    """List notifications with optional filters."""
    notifications = store.list_notifications(
        workspace_id=workspace_id,
        unread_only=unread_only,
        limit=limit,
    )
    unread_count = store.get_unread_count(workspace_id=workspace_id)
    return NotificationsListResponse(
        notifications=[_notification_to_response(n) for n in notifications],
        unread_count=unread_count,
    )


@router.post("/api/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: str,
    store: Annotated[GluonStore, Depends(get_store)],
) -> NotificationResponse:
    """Mark a single notification as read."""
    notification = store.mark_notification_read(notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _notification_to_response(notification)


@router.post("/api/notifications/read-all")
async def mark_all_notifications_read(
    store: Annotated[GluonStore, Depends(get_store)],
    workspace_id: str | None = None,
) -> dict:
    """Mark all notifications as read."""
    count = store.mark_all_notifications_read(workspace_id=workspace_id)
    return {"marked_read": count}


@router.delete("/api/notifications")
async def delete_all_notifications(
    store: Annotated[GluonStore, Depends(get_store)],
) -> dict:
    """Delete all notifications."""
    count = store.delete_all_notifications()
    return {"deleted": count}
