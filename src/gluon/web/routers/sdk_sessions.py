"""SDK Session Browser routes (#162 STEP B — first extracted domain).

These two read-only handlers depend only on the store, so they are a clean
proof-of-concept for the per-domain APIRouter split: the store is injected via
``Depends(get_store)`` (reading ``request.app.state.store``) instead of closing
over create_app locals. Paths are unchanged, so the app-wide fail-closed auth
middleware still applies exactly as before.
"""

from __future__ import annotations

import logging
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from gluon.store import GluonStore
from gluon.web.models import (
    SDKSessionResponse,
    SessionDetailResponse,
    SessionMessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sdk-sessions"])


def get_store(request: Request) -> GluonStore:
    """Resolve the shared store from app state (set in create_app)."""
    # app.state is dynamically typed (Any); the store is assigned in create_app.
    return cast(GluonStore, request.app.state.store)


@router.get("/api/sdk-sessions", response_model=list[SDKSessionResponse])
async def list_sdk_sessions(
    store: Annotated[GluonStore, Depends(get_store)],
    directory: Annotated[str | None, Query(description="Project directory to filter sessions")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SDKSessionResponse]:
    """List all Claude SDK sessions from the local filesystem."""
    try:
        from claude_agent_sdk import list_sessions

        sessions = list_sessions(
            directory=directory,
            limit=limit,
            include_worktrees=True,
        )
    except Exception as e:
        logger.warning("Failed to list SDK sessions: %s", e)
        return []

    # Build lookup of claude_session_id -> run_ids for cross-referencing
    session_to_runs: dict[str, list[str]] = {}
    try:
        all_runs = store.list_runs(limit=500)
        for r in all_runs:
            if r.claude_session_id:
                session_to_runs.setdefault(r.claude_session_id, []).append(r.id)
    except Exception as e:
        logger.warning("Failed to load runs for session cross-reference: %s", e)

    result: list[SDKSessionResponse] = []
    for s in sessions:
        result.append(
            SDKSessionResponse(
                session_id=s.session_id,
                summary=s.summary,
                last_modified=s.last_modified,
                # SDK file_size is typed int|None; the model is int (always set at runtime).
                file_size=s.file_size,  # type: ignore[arg-type]
                custom_title=s.custom_title,
                first_prompt=s.first_prompt,
                git_branch=s.git_branch,
                cwd=s.cwd,
                linked_run_ids=session_to_runs.get(s.session_id, []),
            )
        )

    return result


@router.get("/api/sdk-sessions/{session_id}", response_model=SessionDetailResponse)
async def get_sdk_session(
    session_id: str,
    store: Annotated[GluonStore, Depends(get_store)],
    directory: Annotated[str | None, Query(description="Project directory")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionDetailResponse:
    """Get session detail with messages from Claude SDK."""
    try:
        from claude_agent_sdk import get_session_messages, list_sessions

        # Get session info
        sessions = list_sessions(directory=directory, limit=200, include_worktrees=True)
        session_info = next((s for s in sessions if s.session_id == session_id), None)
        if not session_info:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        # Get messages
        messages = get_session_messages(
            session_id=session_id,
            directory=directory,
            limit=limit,
            offset=offset,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to get SDK session %s: %s", session_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to read session: {e}")

    # Cross-reference linked runs
    linked_run_ids: list[str] = []
    try:
        all_runs = store.list_runs(limit=500)
        linked_run_ids = [r.id for r in all_runs if r.claude_session_id == session_id]
    except Exception as e:
        logger.warning("Failed to load runs for session cross-reference: %s", e)

    session_resp = SDKSessionResponse(
        session_id=session_info.session_id,
        summary=session_info.summary,
        last_modified=session_info.last_modified,
        # SDK file_size is typed int|None; the model is int (always set at runtime).
        file_size=session_info.file_size,  # type: ignore[arg-type]
        custom_title=session_info.custom_title,
        first_prompt=session_info.first_prompt,
        git_branch=session_info.git_branch,
        cwd=session_info.cwd,
        linked_run_ids=linked_run_ids,
    )

    message_responses = [
        SessionMessageResponse(
            type=m.type,
            uuid=m.uuid,
            session_id=m.session_id,
            message=m.message,
            parent_tool_use_id=m.parent_tool_use_id,
        )
        for m in messages
    ]

    return SessionDetailResponse(
        session=session_resp,
        messages=message_responses,
        total_messages=len(message_responses),
    )
