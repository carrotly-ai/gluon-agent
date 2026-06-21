"""Queued-message routes for runs (#162).

The /api/runs/{run_id}/queue* routes depend only on the store, the run resolver,
the project-name lookup, and the WebSocket manager — all injected via Depends or
the module-level ws_manager singleton. Paths unchanged → same fail-closed auth.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.models import ExecutionRun, QueuedMessage, RunStatus
from gluon.store import GluonStore
from gluon.web.models import (
    EditQueuedMessageRequest,
    QueuedMessageResponse,
    QueueFollowupRequest,
    QueueFollowupResponse,
)
from gluon.web.routers._deps import (
    get_project_lookup,
    get_resolve_run_or_404,
    get_store,
    get_ws_manager,
)
from gluon.web.websocket import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["queued-messages"])

RunResolver = Callable[[str], ExecutionRun]
ProjectLookup = Callable[[], dict[str, str]]
WsManager = Annotated[WebSocketManager, Depends(get_ws_manager)]


@router.post("/api/runs/{run_id}/queue-followup", response_model=QueueFollowupResponse)
async def queue_followup(
    run_id: str,
    body: QueueFollowupRequest,
    store: Annotated[GluonStore, Depends(get_store)],
    resolve_run_or_404: Annotated[RunResolver, Depends(get_resolve_run_or_404)],
    project_lookup_fn: Annotated[ProjectLookup, Depends(get_project_lookup)],
    ws_manager: WsManager,
) -> QueueFollowupResponse:
    """
    Queue a follow-up message for a running task.

    If the task is running/pending, the message is appended to the queue
    and will auto-resume after the task completes.

    If the task is not running, returns action="resume_now" to indicate
    the caller should use the normal resume endpoint instead.
    """
    run = resolve_run_or_404(run_id)

    # Check if task is currently running
    if run.status not in (RunStatus.RUNNING, RunStatus.PENDING):
        # Not running - caller should use normal resume
        return QueueFollowupResponse(
            run_id=run.id,
            action="resume_now",
            message=None,
        )

    # Append message to the queue
    try:
        queued_msg = QueuedMessage(message=body.message)
        run.queued_messages.append(queued_msg)
        store.update_run(run)
    except Exception as e:
        logger.exception(f"Failed to queue message for run {run_id}")
        raise HTTPException(status_code=500, detail=f"Failed to queue message: {e!s}")

    # Broadcast update to WebSocket clients
    try:
        project_lookup = project_lookup_fn()
        project_name = project_lookup.get(run.project_id) or "Unknown"
        await ws_manager.broadcast_run_update(run, project_name)
    except Exception as e:
        # Don't fail the request - message was queued successfully
        logger.warning(f"Failed to broadcast queue update for run {run_id}: {e}")

    return QueueFollowupResponse(
        run_id=run.id,
        action="queued",
        message=body.message,
        message_id=queued_msg.id,
    )


@router.put("/api/runs/{run_id}/queue/{message_id}")
async def edit_queued_message(
    run_id: str,
    message_id: str,
    body: EditQueuedMessageRequest,
    store: Annotated[GluonStore, Depends(get_store)],
    resolve_run_or_404: Annotated[RunResolver, Depends(get_resolve_run_or_404)],
    project_lookup_fn: Annotated[ProjectLookup, Depends(get_project_lookup)],
    ws_manager: WsManager,
) -> QueuedMessageResponse:
    """Edit a queued message by its ID."""
    run = resolve_run_or_404(run_id)

    # Find and update the message
    for msg in run.queued_messages:
        if msg.id == message_id:
            msg.message = body.message
            store.update_run(run)

            # Broadcast update
            project_lookup = project_lookup_fn()
            project_name = project_lookup.get(run.project_id) or "Unknown"
            await ws_manager.broadcast_run_update(run, project_name)

            return QueuedMessageResponse(
                id=msg.id,
                message=msg.message,
                queued_at=msg.queued_at.isoformat(),
            )

    raise HTTPException(status_code=404, detail=f"Queued message not found: {message_id}")


@router.delete("/api/runs/{run_id}/queue/{message_id}")
async def delete_queued_message(
    run_id: str,
    message_id: str,
    store: Annotated[GluonStore, Depends(get_store)],
    resolve_run_or_404: Annotated[RunResolver, Depends(get_resolve_run_or_404)],
    project_lookup_fn: Annotated[ProjectLookup, Depends(get_project_lookup)],
    ws_manager: WsManager,
) -> dict:
    """Delete a queued message by its ID."""
    run = resolve_run_or_404(run_id)

    # Find and remove the message
    original_len = len(run.queued_messages)
    run.queued_messages = [m for m in run.queued_messages if m.id != message_id]

    if len(run.queued_messages) == original_len:
        raise HTTPException(status_code=404, detail=f"Queued message not found: {message_id}")

    store.update_run(run)

    # Broadcast update
    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(run.project_id) or "Unknown"
    await ws_manager.broadcast_run_update(run, project_name)

    return {"deleted": True, "message_id": message_id}


@router.delete("/api/runs/{run_id}/queue")
async def clear_queue(
    run_id: str,
    store: Annotated[GluonStore, Depends(get_store)],
    resolve_run_or_404: Annotated[RunResolver, Depends(get_resolve_run_or_404)],
    project_lookup_fn: Annotated[ProjectLookup, Depends(get_project_lookup)],
    ws_manager: WsManager,
) -> dict:
    """Clear all queued messages for a run."""
    run = resolve_run_or_404(run_id)

    cleared_count = len(run.queued_messages)
    run.queued_messages = []
    store.update_run(run)

    # Broadcast update
    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(run.project_id) or "Unknown"
    await ws_manager.broadcast_run_update(run, project_name)

    return {"cleared": True, "count": cleared_count}
