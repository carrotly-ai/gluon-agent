"""Orchestrator-task routes (#162).

The /api/tasks* routes depend only on the store, the orchestrator (for agent
resolution), and the current-user dependency (for create attribution) — all
injected via Depends. The five mapper/resolver helpers that were closure-local
in ``create_app`` are lifted to module level here, parameterized by the
collaborators they need, so they can be shared (the still-inline
``/api/agents/{agent_id}/inbox`` route imports ``task_to_response``).

Paths unchanged → same fail-closed auth posture as the inline versions.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from gluon.auth import SYSTEM_USER
from gluon.core import AgentAmbiguousError, AgentNotFoundError, Orchestrator, TaskNotFoundError
from gluon.models import OrchestratorTask, TaskComment, TaskStatus, utc_now
from gluon.models import User as UserModel
from gluon.store import GluonStore
from gluon.web.models import (
    TaskAssignRequest,
    TaskCommentListResponse,
    TaskCommentRequest,
    TaskCommentResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskUpdateRequest,
)
from gluon.web.routers._deps import get_current_user, get_orchestrator, get_store

router = APIRouter(tags=["tasks"])

Store = Annotated[GluonStore, Depends(get_store)]
Orch = Annotated[Orchestrator, Depends(get_orchestrator)]
CurrentUser = Annotated[UserModel, Depends(get_current_user)]


def task_to_response(
    task: OrchestratorTask,
    agent_name_cache: dict[str, str | None],
    store: GluonStore,
) -> TaskResponse:
    """Convert an OrchestratorTask to its API response with hydrated agent name.

    agent_name_cache is mutated in place so repeated lookups across a result
    set only hit the store once per unique agent_id.
    """
    agent_name: str | None = None
    if task.assigned_agent_id:
        if task.assigned_agent_id in agent_name_cache:
            agent_name = agent_name_cache[task.assigned_agent_id]
        else:
            agent = store.get_agent(task.assigned_agent_id)
            agent_name = agent.name if agent else None
            agent_name_cache[task.assigned_agent_id] = agent_name

    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status.value,
        priority=task.priority,
        assigned_agent_id=task.assigned_agent_id,
        assigned_agent_name=agent_name,
        created_by=task.created_by,
        created_by_user_id=task.created_by_user_id,
        assigned_files=list(task.assigned_files),
        parent_task_id=task.parent_task_id,
        execution_locked_at=task.execution_locked_at.isoformat() if task.execution_locked_at else None,
        execution_run_id=task.execution_run_id,
        created_at=task.created_at.isoformat(),
        updated_at=task.updated_at.isoformat(),
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def comment_to_response(comment: TaskComment) -> TaskCommentResponse:
    return TaskCommentResponse(
        id=comment.id,
        task_id=comment.task_id,
        author_agent_id=comment.author_agent_id,
        author_label=comment.author_label,
        content=comment.content,
        created_at=comment.created_at.isoformat(),
    )


def validate_task_status(status: str) -> TaskStatus:
    """Convert a string to TaskStatus or raise 422."""
    try:
        return TaskStatus(status)
    except ValueError:
        valid = ", ".join(s.value for s in TaskStatus)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Must be one of: {valid}",
        ) from None


def resolve_task_ref_or_404(task_id: str, store: GluonStore) -> OrchestratorTask:
    """Load a task by ID or 8-char prefix; 404 if not found."""
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


def resolve_agent_for_task(
    task: OrchestratorTask,
    agent_ref: str | None,
    store: GluonStore,
    orchestrator: Orchestrator,
) -> str | None:
    """Resolve an agent reference in the context of a task's workspace.

    Returns the resolved agent_id, or None if agent_ref is None.
    Raises HTTPException on lookup failures.
    """
    if agent_ref is None:
        return None

    project = store.get_project(task.project_id)
    workspace_id = project.workspace_id if project else None
    try:
        return orchestrator.resolve_agent(agent_ref, workspace_id)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except AgentAmbiguousError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks_endpoint(
    store: Store,
    orchestrator: Orch,
    project_id: str | None = None,
    agent_id: str | None = Query(default=None, description="Agent name or ID for filtering"),
    status: str | None = None,
    limit: int = 100,
) -> TaskListResponse:
    """List tasks with optional filters."""
    resolved_agent_id: str | None = None
    if agent_id:
        # Allow name-or-id filtering; scope to project's workspace if provided
        workspace_id: str | None = None
        if project_id:
            project = store.get_project(project_id)
            if project:
                workspace_id = project.workspace_id
        try:
            resolved_agent_id = orchestrator.resolve_agent(agent_id, workspace_id)
        except AgentNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        except AgentAmbiguousError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    status_enum: TaskStatus | None = None
    if status is not None:
        status_enum = validate_task_status(status)

    tasks = store.list_tasks(
        project_id=project_id,
        agent_id=resolved_agent_id,
        status=status_enum,
        limit=limit,
    )
    agent_name_cache: dict[str, str | None] = {}
    responses = [task_to_response(t, agent_name_cache, store) for t in tasks]
    return TaskListResponse(tasks=responses, total=len(responses))


@router.post("/api/tasks", response_model=TaskResponse)
async def create_task_endpoint(
    body: TaskCreateRequest,
    store: Store,
    orchestrator: Orch,
    user: CurrentUser,
) -> TaskResponse:
    """Create a new orchestrator task.

    D5 Phase 2 attribution: the task's ``created_by_user_id`` is set to the
    current user's ID when auth is enabled.
    """
    # Only attribute when a real (non-SYSTEM) user is logged in.
    attribution_user_id = user.id if user.id != SYSTEM_USER.id else None

    project = store.get_project(body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {body.project_id}")

    assigned_agent_id: str | None = None
    if body.assigned_agent:
        try:
            assigned_agent_id = orchestrator.resolve_agent(body.assigned_agent, project.workspace_id)
        except AgentNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        except AgentAmbiguousError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    if body.parent_task_id:
        parent = store.get_task(body.parent_task_id)
        if parent is None:
            raise HTTPException(
                status_code=404,
                detail=f"Parent task not found: {body.parent_task_id}",
            )

    task = store.create_task(
        project_id=project.id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        assigned_agent_id=assigned_agent_id,
        created_by="web",
        created_by_user_id=attribution_user_id,
        assigned_files=body.assigned_files,
        parent_task_id=body.parent_task_id,
    )
    return task_to_response(task, {}, store)


@router.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task_endpoint(task_id: str, store: Store) -> TaskResponse:
    """Get a task by ID or 8-char prefix."""
    task = resolve_task_ref_or_404(task_id, store)
    return task_to_response(task, {}, store)


@router.patch("/api/tasks/{task_id}", response_model=TaskResponse)
async def update_task_endpoint(task_id: str, body: TaskUpdateRequest, store: Store) -> TaskResponse:
    """Update task fields (title, description, priority, status, assigned_files)."""
    task = resolve_task_ref_or_404(task_id, store)

    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=422, detail="Title cannot be empty")
        task.title = body.title
    if body.description is not None:
        task.description = body.description
    if body.priority is not None:
        task.priority = body.priority
    if body.status is not None:
        new_status = validate_task_status(body.status)
        task.status = new_status
        if new_status == TaskStatus.DONE and task.completed_at is None:
            task.completed_at = utc_now()
    if body.assigned_files is not None:
        task.assigned_files = body.assigned_files

    store.update_task(task)
    refreshed = store.get_task(task.id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail=f"Task vanished after update: {task_id}")
    return task_to_response(refreshed, {}, store)


@router.delete("/api/tasks/{task_id}")
async def delete_task_endpoint(task_id: str, store: Store) -> dict[str, Any]:
    """Delete a task and all its comments."""
    task = resolve_task_ref_or_404(task_id, store)
    deleted = store.delete_task(task.id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {"deleted": True, "task_id": task.id}


@router.post("/api/tasks/{task_id}/assign", response_model=TaskResponse)
async def assign_task_endpoint(
    task_id: str,
    body: TaskAssignRequest,
    store: Store,
    orchestrator: Orch,
) -> TaskResponse:
    """Assign a task to an agent (moves BACKLOG → ASSIGNED)."""
    task = resolve_task_ref_or_404(task_id, store)
    agent_id = resolve_agent_for_task(task, body.agent, store, orchestrator)
    if agent_id is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {body.agent}")

    task.assigned_agent_id = agent_id
    if task.status == TaskStatus.BACKLOG:
        task.status = TaskStatus.ASSIGNED
    store.update_task(task)

    refreshed = store.get_task(task.id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail=f"Task vanished after assign: {task_id}")
    return task_to_response(refreshed, {}, store)


@router.post("/api/tasks/{task_id}/done", response_model=TaskResponse)
async def done_task_endpoint(task_id: str, store: Store) -> TaskResponse:
    """Mark a task as DONE and release any execution lock."""
    task = resolve_task_ref_or_404(task_id, store)
    try:
        released = store.release_task(task.id, TaskStatus.DONE)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return task_to_response(released, {}, store)


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task_endpoint(task_id: str, store: Store) -> TaskResponse:
    """Mark a task as CANCELLED and release any execution lock."""
    task = resolve_task_ref_or_404(task_id, store)
    try:
        released = store.release_task(task.id, TaskStatus.CANCELLED)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return task_to_response(released, {}, store)


@router.post("/api/tasks/{task_id}/review", response_model=TaskResponse)
async def review_task_endpoint(task_id: str, store: Store) -> TaskResponse:
    """Mark a task as REVIEW and release any execution lock."""
    task = resolve_task_ref_or_404(task_id, store)
    try:
        released = store.release_task(task.id, TaskStatus.REVIEW)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return task_to_response(released, {}, store)


@router.get("/api/tasks/{task_id}/comments", response_model=TaskCommentListResponse)
async def list_task_comments_endpoint(task_id: str, store: Store) -> TaskCommentListResponse:
    """List comments on a task (oldest first)."""
    task = resolve_task_ref_or_404(task_id, store)
    comments = store.list_task_comments(task.id)
    responses = [comment_to_response(c) for c in comments]
    return TaskCommentListResponse(comments=responses, total=len(responses))


@router.post("/api/tasks/{task_id}/comments", response_model=TaskCommentResponse)
async def add_task_comment_endpoint(
    task_id: str,
    body: TaskCommentRequest,
    store: Store,
) -> TaskCommentResponse:
    """Append a comment to a task."""
    task = resolve_task_ref_or_404(task_id, store)
    comment = store.add_task_comment(
        task.id,
        content=body.content,
        author_label=body.author_label or "web",
    )
    return comment_to_response(comment)
