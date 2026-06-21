"""Run-supervision routes (#162).

The /api/runs/{run_id}/supervision* routes depend on the store, runner, and the
run resolver — all injected via Depends. No path/git handling. The
type-ignore comments on disable_supervision are preserved byte-for-byte from the
inline version (store.get_run returns ExecutionRun | None). Paths unchanged →
same fail-closed auth posture.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.models import ExecutionRun
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    SupervisionDecisionResponse,
    SupervisionDisableRequest,
    SupervisionEvaluateResponse,
    SupervisionStatusResponse,
)
from gluon.web.routers._deps import get_resolve_run_or_404, get_runner, get_store

router = APIRouter(tags=["supervision"])

Store = Annotated[GluonStore, Depends(get_store)]
Runner = Annotated[TaskRunner, Depends(get_runner)]
RunResolver = Annotated[Callable[[str], ExecutionRun], Depends(get_resolve_run_or_404)]


@router.get("/api/runs/{run_id}/supervision", response_model=SupervisionStatusResponse)
async def get_supervision_status(
    run_id: str,
    store: Store,
    resolve_run_or_404: RunResolver,
) -> SupervisionStatusResponse:
    """Get supervision status for a run."""
    from gluon.policies import get_supervision_config

    run = resolve_run_or_404(run_id)

    config = get_supervision_config(run)
    decisions = store.list_supervision_decisions(run.id, limit=10)

    return SupervisionStatusResponse(
        run_id=run.id,
        enabled=config.enabled,
        policy=config.policy.value,
        max_auto_resumes=config.max_auto_resumes,
        auto_resume_count=run.supervision_auto_resume_count,
        min_time_between_resumes=config.min_time_between_resumes,
        last_check_at=run.last_supervision_check_at.isoformat() if run.last_supervision_check_at else None,
        last_resume_at=run.last_supervision_resume_at.isoformat() if run.last_supervision_resume_at else None,
        disabled_reason=run.supervision_disabled_reason,
        recent_decisions=[
            SupervisionDecisionResponse(
                timestamp=d.timestamp.isoformat(),
                decision=d.decision,
                reason=d.reason,
                trigger=d.trigger,
                circuit_state=d.circuit_state.value if d.circuit_state else None,
                completion_confidence=d.completion_confidence,
                auto_resume_count=d.auto_resume_count,
            )
            for d in decisions
        ],
    )


@router.post("/api/runs/{run_id}/supervision/evaluate", response_model=SupervisionEvaluateResponse)
async def evaluate_supervision(
    run_id: str,
    runner: Runner,
    resolve_run_or_404: RunResolver,
) -> SupervisionEvaluateResponse:
    """Manually trigger supervision evaluation for a run."""
    run = resolve_run_or_404(run_id)

    result = await runner.evaluate_supervision(run.id)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to evaluate supervision")

    return SupervisionEvaluateResponse(
        run_id=run.id,
        decision=result["decision"],
        reason=result["reason"],
        wait_seconds=result.get("wait_seconds", 0),
    )


@router.post("/api/runs/{run_id}/supervision/disable", response_model=SupervisionStatusResponse)
async def disable_supervision(
    run_id: str,
    request: SupervisionDisableRequest,
    store: Store,
    runner: Runner,
    resolve_run_or_404: RunResolver,
) -> SupervisionStatusResponse:
    """Disable supervision for a run."""
    from gluon.policies import get_supervision_config
    from gluon.resume_coordinator import ResumeCoordinator

    run = resolve_run_or_404(run_id)

    coordinator = ResumeCoordinator(store=store, runner=runner)
    success = await coordinator.disable_supervision(run.id, request.reason)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to disable supervision")

    # Refresh run and config. get_run is typed ExecutionRun | None; the inline
    # original lived in mypy-ignored create_app and tolerated the theoretical
    # None via the union-attr ignores below — preserved exactly here (a None
    # guard would change behavior), with the reassignment ignored to match.
    run = store.get_run(run.id)  # type: ignore[assignment]
    config = get_supervision_config(run)  # type: ignore[arg-type]
    decisions = store.list_supervision_decisions(run.id, limit=10)  # type: ignore[union-attr]

    return SupervisionStatusResponse(
        run_id=run.id,  # type: ignore[union-attr]
        enabled=config.enabled,
        policy=config.policy.value,
        max_auto_resumes=config.max_auto_resumes,
        auto_resume_count=run.supervision_auto_resume_count,  # type: ignore[union-attr]
        min_time_between_resumes=config.min_time_between_resumes,
        last_check_at=run.last_supervision_check_at.isoformat() if run.last_supervision_check_at else None,  # type: ignore[union-attr]
        last_resume_at=run.last_supervision_resume_at.isoformat() if run.last_supervision_resume_at else None,  # type: ignore[union-attr]
        disabled_reason=run.supervision_disabled_reason,  # type: ignore[union-attr]
        recent_decisions=[
            SupervisionDecisionResponse(
                timestamp=d.timestamp.isoformat(),
                decision=d.decision,
                reason=d.reason,
                trigger=d.trigger,
                circuit_state=d.circuit_state.value if d.circuit_state else None,
                completion_confidence=d.completion_confidence,
                auto_resume_count=d.auto_resume_count,
            )
            for d in decisions
        ],
    )
