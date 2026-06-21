"""Task-schedule routes — user-defined recurring tasks (#162).

The /api/schedules* routes depend on the store, orchestrator (project lookup),
runner + ws_manager (manual fire), the project-name lookup, the run mapper, and
the current-user dependency (create attribution) — all injected via Depends.
The two closure-local helpers (schedule_to_response, resolve_cron) are lifted to
module level, parameterized by the collaborators they need.

Paths unchanged → same fail-closed auth posture as the inline versions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.auth import SYSTEM_USER
from gluon.core import Orchestrator, ProjectNotFoundError
from gluon.models import ConcurrencyPolicy, TaskSchedule, utc_now
from gluon.models import User as UserModel
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    CreateTaskScheduleRequest,
    RunResponse,
    TaskScheduleListResponse,
    TaskSchedulePreviewRequest,
    TaskSchedulePreviewResponse,
    TaskScheduleResponse,
    UpdateTaskScheduleRequest,
)
from gluon.web.routers._deps import (
    get_current_user,
    get_orchestrator,
    get_project_lookup,
    get_run_to_response,
    get_runner,
    get_store,
    get_ws_manager,
)
from gluon.web.websocket import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schedules"])

Store = Annotated[GluonStore, Depends(get_store)]
Orch = Annotated[Orchestrator, Depends(get_orchestrator)]
Runner = Annotated[TaskRunner, Depends(get_runner)]
WsManager = Annotated[WebSocketManager, Depends(get_ws_manager)]
CurrentUser = Annotated[UserModel, Depends(get_current_user)]
ProjectLookup = Annotated[Callable[[], dict[str, str]], Depends(get_project_lookup)]
RunToResponse = Annotated[Callable[..., RunResponse], Depends(get_run_to_response)]


def schedule_to_response(
    schedule: TaskSchedule,
    store: GluonStore,
    project_lookup_fn: Callable[[], dict[str, str]],
) -> TaskScheduleResponse:
    """Build the response payload for a TaskSchedule, denormalizing project_name
    and computing per-schedule run counts + summary."""
    from gluon.recurrence import human_summary

    project_lookup = project_lookup_fn()
    try:
        run_count = len(store.list_runs_for_schedule(schedule.id, limit=10000))
    except Exception:
        run_count = 0
    try:
        active_count = len(store.list_active_runs_for_schedule(schedule.id))
    except Exception:
        active_count = 0
    # Friendly summary; falls back to the raw cron string when the
    # schedule was authored via the Advanced (cron-only) path.
    if schedule.recurrence_days and schedule.recurrence_time:
        summary = human_summary(schedule.recurrence_days, schedule.recurrence_time, schedule.timezone)
    else:
        summary = f"Cron: {schedule.schedule_cron} ({schedule.timezone})"
    return TaskScheduleResponse(
        id=schedule.id,
        name=schedule.name,
        project_id=schedule.project_id,
        project_name=project_lookup.get(schedule.project_id, schedule.project_id[:8]),
        prompt=schedule.prompt,
        profile=schedule.profile,
        model=schedule.model,
        use_worktree=schedule.use_worktree,
        timezone=schedule.timezone,
        recurrence_days=schedule.recurrence_days,
        recurrence_time=schedule.recurrence_time,
        schedule_cron=schedule.schedule_cron,
        concurrency_policy=schedule.concurrency_policy.value,
        is_enabled=schedule.is_enabled,
        last_fired_at=schedule.last_fired_at,
        next_fire_at=schedule.next_fire_at,
        description=schedule.description,
        created_by_user_id=schedule.created_by_user_id,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
        summary=summary,
        run_count=run_count,
        active_run_count=active_count,
    )


def resolve_cron(
    schedule_cron: str | None,
    recurrence_days: list[int] | None,
    recurrence_time: str | None,
) -> str:
    """Pick the source of truth between raw cron and friendly editor."""
    from gluon.recurrence import recurrence_to_cron, validate_cron

    if schedule_cron:
        if not validate_cron(schedule_cron):
            raise HTTPException(status_code=400, detail=f"Invalid cron: {schedule_cron!r}")
        return schedule_cron
    if recurrence_days and recurrence_time:
        try:
            return recurrence_to_cron(recurrence_days, recurrence_time)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
    raise HTTPException(
        status_code=400,
        detail="Provide either schedule_cron or both recurrence_days+recurrence_time.",
    )


@router.get("/api/schedules", response_model=TaskScheduleListResponse)
async def list_schedules_endpoint(
    store: Store,
    project_lookup_fn: ProjectLookup,
    project_id: str | None = None,
    include_disabled: bool = True,
) -> TaskScheduleListResponse:
    schedules = store.list_task_schedules(project_id=project_id, include_disabled=include_disabled)
    return TaskScheduleListResponse(
        schedules=[schedule_to_response(s, store, project_lookup_fn) for s in schedules],
        total=len(schedules),
    )


@router.post("/api/schedules", response_model=TaskScheduleResponse)
async def create_schedule_endpoint(
    body: CreateTaskScheduleRequest,
    store: Store,
    orchestrator: Orch,
    project_lookup_fn: ProjectLookup,
    user: CurrentUser,
) -> TaskScheduleResponse:
    from gluon.recurrence import compute_next_fire_in_tz

    try:
        project = orchestrator.get_project(body.project_name)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project not found: {body.project_name}") from None

    cron = resolve_cron(body.schedule_cron, body.recurrence_days, body.recurrence_time)

    # Validate concurrency_policy
    try:
        policy = ConcurrencyPolicy(body.concurrency_policy)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="concurrency_policy must be one of: skip, cancel_replace, allow_overlap",
        ) from None

    schedule = TaskSchedule(
        name=body.name.strip(),
        project_id=project.id,
        prompt=body.prompt,
        profile=body.profile,
        model=body.model,
        use_worktree=body.use_worktree,
        timezone=body.timezone,
        recurrence_days=body.recurrence_days,
        recurrence_time=body.recurrence_time,
        schedule_cron=cron,
        concurrency_policy=policy,
        is_enabled=body.is_enabled,
        description=body.description,
        created_by_user_id=user.id if user.id != SYSTEM_USER.id else None,
    )
    try:
        schedule.next_fire_at = compute_next_fire_in_tz(cron, body.timezone)
    except Exception:
        logger.exception("Failed to compute next fire for new schedule")
    store.create_task_schedule(schedule)
    return schedule_to_response(schedule, store, project_lookup_fn)


@router.get("/api/schedules/{schedule_id}", response_model=TaskScheduleResponse)
async def get_schedule_endpoint(
    schedule_id: str,
    store: Store,
    project_lookup_fn: ProjectLookup,
) -> TaskScheduleResponse:
    s = store.get_task_schedule(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    return schedule_to_response(s, store, project_lookup_fn)


@router.patch("/api/schedules/{schedule_id}", response_model=TaskScheduleResponse)
async def update_schedule_endpoint(
    schedule_id: str,
    body: UpdateTaskScheduleRequest,
    store: Store,
    orchestrator: Orch,
    project_lookup_fn: ProjectLookup,
) -> TaskScheduleResponse:
    from gluon.recurrence import compute_next_fire_in_tz

    s = store.get_task_schedule(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")

    sent = body.model_fields_set
    if "name" in sent and body.name:
        s.name = body.name.strip()
    if "project_name" in sent and body.project_name:
        try:
            project = orchestrator.get_project(body.project_name)
        except ProjectNotFoundError:
            raise HTTPException(status_code=404, detail=f"Project not found: {body.project_name}") from None
        s.project_id = project.id
    if "prompt" in sent and body.prompt is not None:
        s.prompt = body.prompt
    if "profile" in sent and body.profile:
        s.profile = body.profile
    if "model" in sent:
        s.model = body.model
    if "use_worktree" in sent and body.use_worktree is not None:
        s.use_worktree = body.use_worktree
    if "timezone" in sent and body.timezone:
        s.timezone = body.timezone
    if "description" in sent:
        s.description = body.description
    if "is_enabled" in sent and body.is_enabled is not None:
        s.is_enabled = body.is_enabled
    if "concurrency_policy" in sent and body.concurrency_policy:
        try:
            s.concurrency_policy = ConcurrencyPolicy(body.concurrency_policy)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid concurrency_policy") from None

    # Recurrence: if any of the three triggers fired, recompute the cron.
    cron_changed = any(k in sent for k in ("schedule_cron", "recurrence_days", "recurrence_time"))
    if cron_changed:
        new_cron = resolve_cron(
            body.schedule_cron if "schedule_cron" in sent else None,
            body.recurrence_days if "recurrence_days" in sent else s.recurrence_days,
            body.recurrence_time if "recurrence_time" in sent else s.recurrence_time,
        )
        s.schedule_cron = new_cron
        # Track structured fields too — clear them when only cron was set
        if "schedule_cron" in sent and "recurrence_days" not in sent and "recurrence_time" not in sent:
            s.recurrence_days = None
            s.recurrence_time = None
        else:
            if "recurrence_days" in sent:
                s.recurrence_days = body.recurrence_days
            if "recurrence_time" in sent:
                s.recurrence_time = body.recurrence_time

    # Recompute next_fire_at any time cron OR timezone changed.
    if cron_changed or "timezone" in sent:
        try:
            s.next_fire_at = compute_next_fire_in_tz(s.schedule_cron, s.timezone)
        except Exception:
            logger.exception("Failed to recompute next fire on schedule update")

    store.update_task_schedule(s)
    return schedule_to_response(s, store, project_lookup_fn)


@router.delete("/api/schedules/{schedule_id}")
async def delete_schedule_endpoint(schedule_id: str, store: Store) -> dict[str, bool]:
    deleted = store.delete_task_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    return {"deleted": True}


@router.post("/api/schedules/{schedule_id}/enable", response_model=TaskScheduleResponse)
async def enable_schedule_endpoint(
    schedule_id: str,
    store: Store,
    project_lookup_fn: ProjectLookup,
) -> TaskScheduleResponse:
    from gluon.recurrence import compute_next_fire_in_tz

    s = store.get_task_schedule(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    s.is_enabled = True
    try:
        s.next_fire_at = compute_next_fire_in_tz(s.schedule_cron, s.timezone)
    except Exception:
        pass
    store.update_task_schedule(s)
    return schedule_to_response(s, store, project_lookup_fn)


@router.post("/api/schedules/{schedule_id}/disable", response_model=TaskScheduleResponse)
async def disable_schedule_endpoint(
    schedule_id: str,
    store: Store,
    project_lookup_fn: ProjectLookup,
) -> TaskScheduleResponse:
    s = store.get_task_schedule(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    s.is_enabled = False
    store.update_task_schedule(s)
    return schedule_to_response(s, store, project_lookup_fn)


@router.post("/api/schedules/{schedule_id}/fire", response_model=RunResponse)
async def fire_schedule_now_endpoint(
    schedule_id: str,
    store: Store,
    runner: Runner,
    ws_manager: WsManager,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
) -> RunResponse:
    """Manually fire a schedule once — useful for testing without waiting."""
    s = store.get_task_schedule(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    try:
        run = await runner.submit(
            project_id=s.project_id,
            prompt=s.prompt,
            wait=False,
            use_worktree=s.use_worktree,
            initiator=f"schedule:{s.id[:8]}:manual",
            model=s.model,
            profile=s.profile,
            schedule_id=s.id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to spawn run: {e!s}") from e
    s.last_fired_at = utc_now()
    store.update_task_schedule(s)
    project_lookup = project_lookup_fn()
    project_name = project_lookup.get(run.project_id, run.project_id[:8])
    await ws_manager.broadcast_run_update(run, project_name)
    return run_to_response(run, project_lookup)


@router.get("/api/schedules/{schedule_id}/runs", response_model=list[RunResponse])
async def list_schedule_runs_endpoint(
    schedule_id: str,
    store: Store,
    project_lookup_fn: ProjectLookup,
    run_to_response: RunToResponse,
    limit: int = 50,
) -> list[RunResponse]:
    s = store.get_task_schedule(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    runs = store.list_runs_for_schedule(schedule_id, limit=limit)
    project_lookup = project_lookup_fn()
    return [run_to_response(r, project_lookup) for r in runs]


@router.post("/api/schedules/preview", response_model=TaskSchedulePreviewResponse)
async def preview_schedule_endpoint(body: TaskSchedulePreviewRequest) -> TaskSchedulePreviewResponse:
    """Render the cron + the next 5 fire moments for live editor preview.

    Doesn't persist anything — pure projection.
    """
    from gluon.recurrence import human_summary, next_n_fires_in_tz

    cron = resolve_cron(body.schedule_cron, body.recurrence_days, body.recurrence_time)
    try:
        fires = next_n_fires_in_tz(cron, body.timezone, n=5)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to compute fires: {e!s}") from e
    if body.recurrence_days and body.recurrence_time:
        summary = human_summary(body.recurrence_days, body.recurrence_time, body.timezone)
    else:
        summary = f"Cron: {cron} ({body.timezone})"
    return TaskSchedulePreviewResponse(schedule_cron=cron, summary=summary, next_fires=fires)
