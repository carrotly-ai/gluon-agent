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

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from gluon.auth import SYSTEM_USER
from gluon.models import ExecutionRun, PendingQuestion, QuestionStatus, RunStatus, utc_now
from gluon.models import User as UserModel
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    AnswerQuestionRequest,
    ForkRunRequest,
    PendingQuestionResponse,
    PendingQuestionsResponse,
    RalphIterationResponse,
    RalphIterationsResponse,
    ResumeRunRequest,
    ResumeRunResponse,
    RunResponse,
    RunTodosResponse,
    SessionHistoryResponse,
    SnoozeRunRequest,
    StopLoopResponse,
    TodoItemResponse,
    UpdateRunRequest,
    UpdateStatusRequest,
    UpdateStatusResponse,
)
from gluon.web.routers._deps import (
    get_current_user,
    get_project_lookup,
    get_resolve_run_or_404,
    get_run_to_response,
    get_runner,
    get_store,
    get_ws_manager,
)
from gluon.web.websocket import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])

Store = Annotated[GluonStore, Depends(get_store)]
Runner = Annotated[TaskRunner, Depends(get_runner)]
RunResolver = Annotated[Callable[[str], ExecutionRun], Depends(get_resolve_run_or_404)]
ProjectLookup = Annotated[Callable[[], dict[str, str]], Depends(get_project_lookup)]
RunToResponse = Annotated[Callable[..., RunResponse], Depends(get_run_to_response)]
WsManager = Annotated[WebSocketManager, Depends(get_ws_manager)]
CurrentUser = Annotated[UserModel, Depends(get_current_user)]


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


@router.post("/api/runs/{run_id}/archive", response_model=RunResponse)
async def archive_run(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
) -> RunResponse:
    """Archive a run to hide it from the board."""
    run = resolve_run_or_404(run_id)

    updated_run = store.archive_run(run.id, archived=True)
    if not updated_run:
        raise HTTPException(status_code=500, detail="Failed to archive run")

    project_lookup = project_lookup_fn()
    response = run_to_response(updated_run, project_lookup)

    # Broadcast update so UI reflects the change
    project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
    await ws_manager.broadcast_run_update(updated_run, project_name)

    return response


@router.post("/api/runs/{run_id}/unarchive", response_model=RunResponse)
async def unarchive_run(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
) -> RunResponse:
    """Unarchive a run to show it on the board again."""
    run = resolve_run_or_404(run_id)

    updated_run = store.archive_run(run.id, archived=False)
    if not updated_run:
        raise HTTPException(status_code=500, detail="Failed to unarchive run")

    project_lookup = project_lookup_fn()
    response = run_to_response(updated_run, project_lookup)

    # Broadcast update so UI reflects the change
    project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
    await ws_manager.broadcast_run_update(updated_run, project_name)

    return response


@router.patch("/api/runs/{run_id}", response_model=RunResponse)
async def patch_run(
    run_id: str,
    body: UpdateRunRequest,
    store: Store,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
) -> RunResponse:
    """Partially update a run's user-editable fields (title, kind).

    Unspecified fields are left unchanged. Pass ``null`` explicitly to clear
    a field. Returns the updated ``RunResponse``.
    """
    run = resolve_run_or_404(run_id)

    # Pydantic's `model_fields_set` tells us which fields the client actually
    # sent — distinguishes "set to null" (clear) from "omitted" (leave).
    sent = body.model_fields_set
    if "custom_title" in sent:
        title = body.custom_title
        if title is not None:
            title = title.strip()
            if len(title) > 200:
                raise HTTPException(status_code=400, detail="custom_title must be ≤ 200 chars")
            run.custom_title = title or None
        else:
            run.custom_title = None
    if "kind" in sent:
        kind = body.kind
        if kind is not None:
            kind = kind.strip().lower()
            if kind and kind not in {"research", "build", "docs", "bug", "review", "chore"}:
                raise HTTPException(
                    status_code=400,
                    detail="kind must be one of: research, build, docs, bug, review, chore",
                )
            run.kind = kind or None
        else:
            run.kind = None

    run.bump_activity()
    store.update_run(run)

    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(run.project_id, run.project_id[:8])
    await ws_manager.broadcast_run_update(run, project_name)
    return run_to_response(run, project_lookup)


@router.post("/api/runs/{run_id}/snooze", response_model=RunResponse)
async def snooze_run(
    run_id: str,
    body: SnoozeRunRequest,
    store: Store,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
) -> RunResponse:
    """Set or clear a run's snooze deadline."""
    run = resolve_run_or_404(run_id)

    run.snoozed_until = body.until
    run.bump_activity()
    store.update_run(run)

    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(run.project_id, run.project_id[:8])
    await ws_manager.broadcast_run_update(run, project_name)
    return run_to_response(run, project_lookup)


@router.post("/api/runs/{run_id}/fork", response_model=RunResponse)
async def fork_run_endpoint(
    run_id: str,
    body: ForkRunRequest,
    store: Store,
    runner: Runner,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
    user: CurrentUser,
) -> RunResponse:
    """Fork an existing run's Claude session into a new child run.

    The child run inherits the parent's ``claude_session_id`` and gets its
    own subprocess. Parent must have started at least once (must have a
    session id). See ``TaskRunner.fork_run`` for behaviour details.
    """
    parent = store.get_run_by_short_id(run_id) or store.get_run(run_id)
    if not parent:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    attribution_user_id = user.id if user.id != SYSTEM_USER.id else None
    try:
        child = await runner.fork_run(
            parent_run_id=parent.id,
            new_prompt=body.prompt,
            custom_title=body.custom_title,
            initiator="web:fork",
            user_id=attribution_user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(child.project_id, child.project_id[:8])
    await ws_manager.broadcast_run_update(child, project_name)
    return run_to_response(child, project_lookup)


def question_to_response(q: PendingQuestion) -> PendingQuestionResponse:
    """Convert PendingQuestion to API response."""
    return PendingQuestionResponse(
        id=q.id,
        run_id=q.run_id,
        question_index=q.question_index,
        question_text=q.question_text,
        header=q.header,
        options=q.options,
        multi_select=q.multi_select,
        status=q.status.value,
        created_at=q.created_at.isoformat(),
        expires_at=q.expires_at.isoformat() if q.expires_at else None,
        selected_labels=q.selected_labels,
        answer_source=q.answer_source,
    )


@router.get("/api/runs/{run_id}/questions", response_model=PendingQuestionsResponse)
async def get_run_questions(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
) -> PendingQuestionsResponse:
    """
    Get all questions for a run.

    Returns both pending and answered questions for the run.
    """
    run = resolve_run_or_404(run_id)

    questions = store.list_pending_questions(run.id)
    has_pending = any(q.status == QuestionStatus.PENDING for q in questions)

    return PendingQuestionsResponse(
        run_id=run.id,
        questions=[question_to_response(q) for q in questions],
        has_pending=has_pending,
    )


@router.post("/api/questions/{question_id}/answer", response_model=PendingQuestionResponse)
async def answer_question(
    question_id: str,
    body: AnswerQuestionRequest,
    store: Store,
    ws_manager: WsManager,
) -> PendingQuestionResponse:
    """
    Submit an answer to a pending question.

    The answer must contain at least one selected label from the question's options.
    """
    question = store.get_pending_question(question_id)
    if not question:
        raise HTTPException(status_code=404, detail=f"Question not found: {question_id}")

    if question.status != QuestionStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Question already answered (status: {question.status.value})",
        )

    # Validate at least one selection
    if not body.selected_labels:
        raise HTTPException(status_code=400, detail="At least one option must be selected")

    # Update the question
    question.status = QuestionStatus.ANSWERED
    question.selected_labels = body.selected_labels
    question.answer_source = "user"
    question.answered_at = utc_now()
    store.update_pending_question(question)

    # Emit question.answered event (subscribers handle WebSocket broadcast)
    try:
        from gluon.events import event_bus
        from gluon.events.types import EventCategory, GluonEvent

        await event_bus.emit(
            GluonEvent(
                type="question.answered",
                category=EventCategory.INTERACTION,
                run_id=question.run_id,
                data={"question_id": question_id, "selected_labels": body.selected_labels},
            )
        )
    except ImportError:
        await ws_manager.broadcast_question_answered(question.run_id, question_id)

    return question_to_response(question)


# Allowed status transitions for drag-and-drop
ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"cancelled"},
    "running": {"cancelled"},
    "review": {"completed", "pending", "failed", "cancelled"},  # Approve, retry, reject, or cancel
    "completed": {"pending", "review"},  # Re-queue for retry, or back to review if PR still open
    "failed": {"pending"},  # Retry
    "cancelled": {"pending"},  # Retry
}


@router.post("/api/runs/{run_id}/status", response_model=UpdateStatusResponse)
async def update_run_status(
    run_id: str,
    body: UpdateStatusRequest,
    store: Store,
    runner: Runner,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
) -> UpdateStatusResponse:
    """
    Manually transition a run's status (for drag-and-drop).

    Allowed transitions:
    - pending → cancelled (abort before start)
    - running → cancelled (manual abort - also kills process)
    - completed → pending (re-queue for retry)
    - failed → pending (re-queue for retry)
    - cancelled → pending (re-queue)
    """
    run = resolve_run_or_404(run_id)

    # Validate transition
    current_status = run.status.value
    new_status = body.status

    try:
        new_status_enum = RunStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

    if new_status not in ALLOWED_STATUS_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from {current_status} to {new_status}",
        )

    # Handle special case: cancelling a running process
    if current_status == "running" and new_status == "cancelled":
        await runner.cancel(run.id)

    # Update status
    previous_status = current_status
    updated_run = store.update_run_status(run.id, new_status_enum)
    if not updated_run:
        raise HTTPException(status_code=500, detail="Failed to update run status")

    project_lookup = project_lookup_fn()
    response_run = run_to_response(updated_run, project_lookup)

    # Broadcast update
    project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
    await ws_manager.broadcast_run_update(updated_run, project_name)

    return UpdateStatusResponse(
        run=response_run,
        previous_status=previous_status,
        new_status=new_status,
    )


@router.post("/api/runs/{run_id}/pr-status", response_model=RunResponse)
async def update_pr_status(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
    pr_status: str = Query(..., description="New PR status"),
) -> RunResponse:
    """Update the PR status for a run (e.g., mark as merged to move from REVIEW to DONE)."""
    run = resolve_run_or_404(run_id)

    # Validate pr_status
    valid_statuses = {"open", "merged", "closed", "draft"}
    if pr_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid PR status: {pr_status}. Must be one of: {valid_statuses}",
        )

    updated_run = store.update_pr_status(run.id, pr_status)
    if not updated_run:
        raise HTTPException(status_code=500, detail="Failed to update PR status")

    project_lookup = project_lookup_fn()
    response = run_to_response(updated_run, project_lookup)

    # Broadcast update so UI reflects the change
    project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
    await ws_manager.broadcast_run_update(updated_run, project_name)

    return response


@router.post("/api/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    store: Store,
    runner: Runner,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    ws_manager: WsManager,
) -> RunResponse:
    """Cancel a running task."""
    run = resolve_run_or_404(run_id)

    if not run.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"Run is not active (status: {run.status.value})",
        )

    try:
        success = await runner.cancel(run.id)
    except Exception as e:
        logger.error("Error cancelling run %s: %s", run.id[:8], e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while cancelling run {run.id[:8]}; check server logs.",
        ) from e
    if not success:
        # cancel() returns False when the run is no longer cancellable (e.g.
        # it just finished or was never running in this process).
        raise HTTPException(
            status_code=409,
            detail=f"Run {run.id[:8]} could not be cancelled (it may have already finished).",
        )

    # Refresh and return updated run
    updated_run = store.get_run(run.id)
    if not updated_run:
        raise HTTPException(status_code=500, detail="Run disappeared after cancel")
    project_lookup = project_lookup_fn()
    response = run_to_response(updated_run, project_lookup)

    # Broadcast update
    project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
    await ws_manager.broadcast_run_update(updated_run, project_name)

    return response


@router.post("/api/runs/{run_id}/resume", response_model=ResumeRunResponse)
async def resume_run(
    run_id: str,
    body: ResumeRunRequest,
    runner: Runner,
    resolve_run_or_404: RunResolver,
    project_lookup_fn: ProjectLookup,
    ws_manager: WsManager,
) -> ResumeRunResponse:
    """
    Resume a completed/failed run in-place (same run ID continues).

    The run continues with the same Claude session context, worktree,
    and branch. Logs are appended, costs accumulate.
    """
    # Get the run to resume
    run = resolve_run_or_404(run_id)

    # Get the project for this run
    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(run.project_id)
    if not project_name:
        raise HTTPException(
            status_code=400,
            detail="Could not find project for this run",
        )

    # Use resume_in_place which handles all validation
    try:
        resumed_run = await runner.resume_in_place(
            run_id=run.id,
            new_prompt=body.prompt,
            wait=False,
            initiator="web:resume",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Broadcast to WebSocket clients (run_updated since same run continues)
    await ws_manager.broadcast_run_update(resumed_run, project_name)

    return ResumeRunResponse(
        run_id=resumed_run.id,
        status=resumed_run.status.value,
        resume_count=resumed_run.resume_count,
        # Backward compatibility
        original_run_id=resumed_run.id,
        new_run_id=resumed_run.id,
    )
