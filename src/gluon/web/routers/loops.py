"""Agent-loop routes (loop-engineering Phase 2 — docs/design/agent-loops.md).

POST /api/loops (create + seed iteration 1), GET /api/loops (list),
GET /api/loops/{id} (detail), and the pause/resume/cancel actions. Creation
kicks one queue-drain cycle so the seed task dispatches immediately instead of
waiting for the next drain interval.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.auth import SYSTEM_USER
from gluon.loop_manager import LoopManager
from gluon.models import AgentLoop
from gluon.models import User as UserModel
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    AgentLoopListResponse,
    AgentLoopResponse,
    CreateAgentLoopRequest,
    GateabilityBucket,
    LoopRunSummary,
    LoopTaskNode,
)
from gluon.web.routers._deps import get_current_user, get_runner, get_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["loops"])

Store = Annotated[GluonStore, Depends(get_store)]
Runner = Annotated[TaskRunner, Depends(get_runner)]
CurrentUser = Annotated[UserModel, Depends(get_current_user)]


def loop_to_response(loop: AgentLoop, store: GluonStore, include_runs: bool = False) -> AgentLoopResponse:
    from gluon.loop_manager import VERIFICATION_MARKER

    project = store.get_project(loop.project_id)
    metrics = store.get_agent_loop_metrics(loop.id)
    recent_runs: list[LoopRunSummary] = []
    graph: list[LoopTaskNode] = []
    if include_runs:
        graph = [
            LoopTaskNode(
                id=it.id,
                status=it.status.value,
                source=it.source,
                prompt=it.prompt[:200],
                depends_on=it.depends_on or [],
                verify_cmd=it.verify_cmd,
            )
            for it in store.list_loop_work_items(loop.id)
        ]
        recent_runs = [
            LoopRunSummary(
                id=r.id,
                status=r.status.value,
                cost_usd=r.cost_usd,
                title=(r.custom_title or r.prompt)[:140],
                verifier=VERIFICATION_MARKER in (r.prompt or ""),
                created_at=r.created_at.isoformat(),
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
            )
            for r in store.list_runs_for_loop(loop.id, limit=25)
        ]
    return AgentLoopResponse(
        id=loop.id,
        project_id=loop.project_id,
        project_name=project.name if project else None,
        metrics=GateabilityBucket(**metrics) if metrics["runs"] else None,
        objective=loop.objective,
        verify_cmd=loop.verify_cmd,
        agent_verifier=loop.agent_verifier,
        readiness="gated" if loop.verify_cmd else "gateless",
        profile=loop.profile,
        model=loop.model,
        executor_model=loop.executor_model,
        watch_cmd=loop.watch_cmd,
        use_worktree=loop.use_worktree,
        autonomy=loop.autonomy,
        status=loop.status.value,
        status_reason=loop.status_reason,
        iteration_count=loop.iteration_count,
        max_iterations=loop.max_iterations,
        total_cost_usd=loop.total_cost_usd,
        max_cost_usd=loop.max_cost_usd,
        stall_count=loop.stall_count,
        max_stalls=loop.max_stalls,
        max_fanout=loop.max_fanout,
        completion_requested=loop.completion_requested,
        completion_summary=loop.completion_summary,
        pending_tasks=store.count_pending_loop_items(loop.id),
        recent_runs=recent_runs,
        graph=graph,
        initiator=loop.initiator,
        created_at=loop.created_at.isoformat(),
        updated_at=loop.updated_at.isoformat(),
        completed_at=loop.completed_at.isoformat() if loop.completed_at else None,
    )


def _get_loop_or_404(store: GluonStore, loop_id: str) -> AgentLoop:
    loop = store.get_agent_loop(loop_id)
    if loop is None:
        raise HTTPException(status_code=404, detail="Agent loop not found")
    return loop


@router.post("/api/loops", response_model=AgentLoopResponse)
async def create_loop(
    body: CreateAgentLoopRequest,
    store: Store,
    runner: Runner,
    user: CurrentUser,
) -> AgentLoopResponse:
    """Create an agent loop and seed its first iteration into the work queue."""
    projects = [p for p in store.list_projects() if p.name == body.project_name]
    if not projects:
        raise HTTPException(status_code=404, detail=f"Project '{body.project_name}' not found")
    project = projects[0]

    manager = LoopManager(store)
    loop = manager.create_loop(
        project_id=project.id,
        objective=body.objective,
        verify_cmd=body.verify_cmd,
        agent_verifier=body.agent_verifier,
        profile=body.profile,
        model=body.model,
        executor_model=body.executor_model,
        watch_cmd=body.watch_cmd,
        use_worktree=body.use_worktree,
        autonomy=body.autonomy,
        max_iterations=body.max_iterations,
        max_cost_usd=body.max_cost_usd,
        max_stalls=body.max_stalls,
        max_fanout=body.max_fanout,
        initiator="web:loops",
        created_by_user_id=None if user.id == SYSTEM_USER.id else user.id,
    )

    # Dispatch the seed immediately rather than waiting for the drain interval.
    try:
        await runner.kick_queue_drain()
    except Exception:
        logger.debug("Post-create queue kick failed; drain loop will pick up the seed", exc_info=True)

    return loop_to_response(loop, store)


@router.get("/api/loops", response_model=AgentLoopListResponse)
async def list_loops(
    store: Store,
    project_id: str | None = None,
    status: str | None = None,
) -> AgentLoopListResponse:
    """List agent loops, newest first."""
    loops = store.list_agent_loops(project_id=project_id, status=status)
    return AgentLoopListResponse(loops=[loop_to_response(lp, store) for lp in loops], total=len(loops))


@router.get("/api/loops/{loop_id}", response_model=AgentLoopResponse)
async def get_loop(loop_id: str, store: Store) -> AgentLoopResponse:
    """Get one agent loop, including its iteration timeline."""
    return loop_to_response(_get_loop_or_404(store, loop_id), store, include_runs=True)


@router.post("/api/loops/{loop_id}/pause", response_model=AgentLoopResponse)
async def pause_loop(loop_id: str, store: Store) -> AgentLoopResponse:
    """Pause a running loop. Pending tasks are preserved (inert until resume)."""
    loop = _get_loop_or_404(store, loop_id)
    updated = LoopManager(store).pause_loop(loop.id)
    return loop_to_response(updated or loop, store)


@router.post("/api/loops/{loop_id}/resume", response_model=AgentLoopResponse)
async def resume_loop(loop_id: str, store: Store, runner: Runner) -> AgentLoopResponse:
    """Resume a paused loop; re-seeds a continuation if nothing is pending."""
    loop = _get_loop_or_404(store, loop_id)
    if loop.status.value != "paused":
        raise HTTPException(status_code=400, detail=f"Cannot resume a {loop.status.value} loop")
    updated = LoopManager(store).resume_loop(loop.id)
    try:
        await runner.kick_queue_drain()
    except Exception:
        logger.debug("Post-resume queue kick failed; drain loop will pick up", exc_info=True)
    return loop_to_response(updated or loop, store)


@router.post("/api/loops/{loop_id}/cancel", response_model=AgentLoopResponse)
async def cancel_loop(loop_id: str, store: Store) -> AgentLoopResponse:
    """Cancel a loop and drop its pending tasks."""
    loop = _get_loop_or_404(store, loop_id)
    if loop.status.value not in ("running", "paused"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel a {loop.status.value} loop")
    updated = LoopManager(store).cancel_loop(loop.id)
    return loop_to_response(updated or loop, store)
