"""Run routes (#162) — incremental extraction.

This router is being populated from create_app one clean batch at a time. It
currently holds the read-mostly run-introspection routes (session-history,
todos, iterations, stop-loop): each depends only on the store, the run
resolver, the project-name lookup, the run mapper, and the WebSocket manager —
all injected via Depends. The path/git/image run routes (commits, files, diff,
create-pr, merge, attachments) deliberately stay inline in create_app (CodeQL
path-validation context). Paths unchanged → same fail-closed auth posture.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.models import ExecutionRun, RunStatus
from gluon.store import GluonStore
from gluon.web.models import (
    RalphIterationResponse,
    RalphIterationsResponse,
    RunResponse,
    RunTodosResponse,
    SessionHistoryResponse,
    StopLoopResponse,
    TodoItemResponse,
)
from gluon.web.routers._deps import (
    get_project_lookup,
    get_resolve_run_or_404,
    get_run_to_response,
    get_store,
    get_ws_manager,
)
from gluon.web.websocket import WebSocketManager

router = APIRouter(tags=["runs"])

Store = Annotated[GluonStore, Depends(get_store)]
RunResolver = Annotated[Callable[[str], ExecutionRun], Depends(get_resolve_run_or_404)]
ProjectLookup = Annotated[Callable[[], dict[str, str]], Depends(get_project_lookup)]
RunToResponse = Annotated[Callable[..., RunResponse], Depends(get_run_to_response)]
WsManager = Annotated[WebSocketManager, Depends(get_ws_manager)]


@router.get("/api/runs/{run_id}/session-history", response_model=SessionHistoryResponse)
async def get_session_history(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
) -> SessionHistoryResponse:
    """
    Get the session history for a run - all runs that share the same Claude session.

    This is useful for viewing the full conversation history when a run has been
    resumed multiple times.
    """
    run = resolve_run_or_404(run_id)

    if not run.claude_session_id:
        raise HTTPException(
            status_code=400,
            detail="Run does not have a session",
        )

    # Get all runs in this session
    session_runs = store.list_runs_by_claude_session(run.claude_session_id)
    project_lookup = project_lookup_fn()

    return SessionHistoryResponse(
        session_id=run.claude_session_id,
        runs=[run_to_response(r, project_lookup) for r in session_runs],
    )


@router.get("/api/runs/{run_id}/todos", response_model=RunTodosResponse)
async def get_run_todos(run_id: str, store: Store, resolve_run_or_404: RunResolver) -> RunTodosResponse:
    """
    Get the latest todo tracking state for a run.

    Returns the most recent TodoWrite snapshot captured by the PostToolUse mirror hook.
    """
    run = resolve_run_or_404(run_id)

    snapshot = store.get_latest_todo_snapshot(run.id)
    if snapshot is None:
        return RunTodosResponse(run_id=run.id)

    return RunTodosResponse(
        run_id=run.id,
        todos=[
            TodoItemResponse(
                content=t.get("content", ""),
                status=t.get("status", "pending"),
                active_form=t.get("activeForm", ""),
            )
            for t in snapshot.todos
        ],
        todo_count=snapshot.todo_count,
        completed_count=snapshot.completed_count,
        in_progress_count=snapshot.in_progress_count,
        pending_count=snapshot.pending_count,
        captured_at=snapshot.captured_at.isoformat(),
    )


@router.get("/api/runs/{run_id}/iterations", response_model=RalphIterationsResponse)
async def get_ralph_iterations(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
    limit: int = 50,
) -> RalphIterationsResponse:
    """
    Get iteration history for a ralph-enabled run.

    Returns a list of all loop iterations with metrics for each.
    """
    run = resolve_run_or_404(run_id)

    if not run.ralph_enabled:
        raise HTTPException(
            status_code=400,
            detail="Run is not a ralph-enabled run",
        )

    iterations = store.list_ralph_iterations(run.id, limit=limit)

    return RalphIterationsResponse(
        run_id=run.id,
        iteration_count=len(iterations),
        iterations=[
            RalphIterationResponse(
                id=it.id,
                run_id=it.run_id,
                loop_number=it.loop_number,
                started_at=it.started_at.isoformat() if it.started_at else "",
                ended_at=it.ended_at.isoformat() if it.ended_at else None,
                duration_seconds=(
                    (it.ended_at - it.started_at).total_seconds() if it.started_at and it.ended_at else None
                ),
                files_changed=it.files_changed,
                progress_detected=it.progress_detected,
                has_errors=it.has_errors,
                error_message=it.error_summary,  # Model uses error_summary
                has_completion_signal=it.has_completion_signal,
                is_test_only=it.is_test_only,
                confidence_score=it.confidence_score,
                cost_usd=it.cost_usd,
                input_tokens=it.tokens_used,  # Model has tokens_used (combined)
                output_tokens=0,  # Not tracked separately in model
            )
            for it in iterations
        ],
    )


@router.post("/api/runs/{run_id}/stop-loop", response_model=StopLoopResponse)
async def stop_ralph_loop(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    ws_manager: WsManager,
) -> StopLoopResponse:
    """
    Stop a ralph loop early.

    This gracefully terminates the loop and moves the run to REVIEW status.
    Only works for ralph-enabled runs that are currently running.
    """
    run = resolve_run_or_404(run_id)

    if not run.ralph_enabled:
        raise HTTPException(
            status_code=400,
            detail="Run is not a ralph-enabled run",
        )

    if run.status != RunStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Run is not running (current status: {run.status.value})",
        )

    # Set completion reason and move to REVIEW
    run.completion_reason = "User requested stop"
    run.status = RunStatus.REVIEW
    store.update_run(run)

    # Broadcast update to WebSocket clients
    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(run.project_id, run.project_id[:8])
    await ws_manager.broadcast_run_update(run, project_name)

    return StopLoopResponse(
        success=True,
        run_id=run.id,
        message=f"Ralph loop stopped at iteration {run.loop_count}",
        final_loop_count=run.loop_count,
    )
