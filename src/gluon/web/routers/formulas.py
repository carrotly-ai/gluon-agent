"""Formula-template routes (#162).

``GET /api/formulas`` is dependency-free (it just discovers templates on disk);
``POST /api/formulas/{name}/run`` needs the store, runner, notifier, and
WebSocket manager to build a ChainExecutor — all injected via Depends. The
heavy executor imports stay inside the handler body (as in the original) to
avoid pulling them in at module load. Paths unchanged → same fail-closed auth.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.notifier import NotificationDispatcher
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    FormulaListResponse,
    FormulaRunRequest,
    FormulaRunResponse,
    FormulaStepResponse,
    FormulaTemplateResponse,
    FormulaVariableResponse,
)
from gluon.web.routers._deps import get_notifier, get_runner, get_store, get_ws_manager
from gluon.web.websocket import WebSocketManager

router = APIRouter(tags=["formulas"])


@router.get("/api/formulas", response_model=FormulaListResponse)
async def list_formulas() -> FormulaListResponse:
    """List all available formula templates."""
    from gluon.formulas import FormulaLoader

    templates = FormulaLoader.discover()
    return FormulaListResponse(
        formulas=[
            FormulaTemplateResponse(
                name=t.name,
                description=t.description,
                variables=[
                    FormulaVariableResponse(
                        name=v.name, type=v.type, required=v.required, default=v.default, help=v.help
                    )
                    for v in t.variables
                ],
                steps=[
                    FormulaStepResponse(
                        id=s.id, name=s.name, prompt=s.prompt, depends_on=s.depends_on, profile=s.profile
                    )
                    for s in t.steps
                ],
                use_worktree=t.use_worktree,
            )
            for t in templates
        ]
    )


@router.post("/api/formulas/{name}/run", response_model=FormulaRunResponse)
async def run_formula(
    name: str,
    req: FormulaRunRequest,
    store: Annotated[GluonStore, Depends(get_store)],
    runner: Annotated[TaskRunner, Depends(get_runner)],
    notifier: Annotated[NotificationDispatcher, Depends(get_notifier)],
    ws_manager: Annotated[WebSocketManager, Depends(get_ws_manager)],
) -> FormulaRunResponse:
    """Execute a formula template for a project."""
    from gluon.chain_executor import ChainExecutor
    from gluon.formulas import FormulaLoader

    template = FormulaLoader.load(name)
    if not template:
        raise HTTPException(status_code=404, detail=f"Formula '{name}' not found")

    chain_executor = ChainExecutor(store=store, runner=runner, notifier=notifier, ws_manager=ws_manager)
    from gluon.formula_executor import FormulaExecutor

    executor = FormulaExecutor(store=store, chain_executor=chain_executor)
    try:
        chain_id = await executor.execute(
            template=template,
            project_id=req.project_id,
            variables=req.variables,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return FormulaRunResponse(
        chain_id=chain_id,
        step_count=len(template.steps),
    )
