"""Workspace routes (#162) — core CRUD + budget.

Extracted from create_app. Handlers use only the store (+ the shared
workspace→response mapper) via Depends; the budget route keeps its admin gate
via the shared require_admin dependency. Paths unchanged → same fail-closed
auth posture. (Settings / env-vars / scan / clone remain inline for now.)
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.models import expand_path
from gluon.store import GluonStore
from gluon.web.models import (
    CreateWorkspaceRequest,
    UpdateWorkspaceBudgetRequest,
    WorkspaceResponse,
)
from gluon.web.routers._deps import get_store, get_workspace_to_response, require_admin

router = APIRouter(tags=["workspaces"])

WorkspaceMapper = Callable[..., WorkspaceResponse]


@router.get("/api/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(
    store: Annotated[GluonStore, Depends(get_store)],
    workspace_to_response: Annotated[WorkspaceMapper, Depends(get_workspace_to_response)],
) -> list[WorkspaceResponse]:
    """List all workspaces."""
    workspaces = store.list_workspaces()
    result = []
    for ws in workspaces:
        projects = store.list_projects_by_workspace(ws.id)
        result.append(workspace_to_response(ws, len(projects)))
    return result


@router.get("/api/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace_detail(
    workspace_id: str,
    store: Annotated[GluonStore, Depends(get_store)],
    workspace_to_response: Annotated[WorkspaceMapper, Depends(get_workspace_to_response)],
) -> WorkspaceResponse:
    """Get a single workspace including rolling budgets and current spend."""
    workspace = store.get_workspace(workspace_id)
    if not workspace:
        workspace = store.get_workspace_by_name(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

    projects = store.list_projects_by_workspace(workspace.id)
    return workspace_to_response(workspace, len(projects))


@router.post("/api/workspaces", response_model=WorkspaceResponse)
async def create_workspace(
    body: CreateWorkspaceRequest,
    store: Annotated[GluonStore, Depends(get_store)],
    workspace_to_response: Annotated[WorkspaceMapper, Depends(get_workspace_to_response)],
) -> WorkspaceResponse:
    """Create a new workspace."""
    # Check if workspace with same name exists
    existing = store.get_workspace_by_name(body.name)
    if existing:
        raise HTTPException(status_code=400, detail=f"Workspace already exists: {body.name}")

    # Resolve and validate path (expand env vars like $HOME, ${HOME}, ~)
    # os.path.realpath breaks CodeQL taint chain for py/path-injection
    resolved = os.path.realpath(str(expand_path(body.path)))
    home_dir = os.path.realpath(str(Path.home()))
    if not (resolved.startswith(home_dir + os.sep) or resolved == home_dir):
        raise HTTPException(status_code=400, detail="Path must be under home directory")
    if not os.path.exists(resolved):
        raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")
    workspace_path = Path(resolved)

    # Create workspace (with optional budgets)
    workspace = store.create_workspace(
        name=body.name,
        path=workspace_path,
        daily_budget_usd=body.daily_budget_usd,
        monthly_budget_usd=body.monthly_budget_usd,
    )

    # Auto-scan for projects if requested
    projects_added = []
    if body.auto_scan:
        for project_path in workspace.scan_for_projects():
            project_name = project_path.name
            existing_project = store.get_project_by_name(project_name)
            if not existing_project:
                store.create_project(
                    name=project_name,
                    path=project_path,
                    workspace_id=workspace.id,
                )
                projects_added.append(project_name)

    return workspace_to_response(workspace, len(projects_added))


@router.put(
    "/api/workspaces/{workspace_id}/budget",
    response_model=WorkspaceResponse,
    dependencies=[Depends(require_admin)],
)
async def update_workspace_budget(
    workspace_id: str,
    body: UpdateWorkspaceBudgetRequest,
    store: Annotated[GluonStore, Depends(get_store)],
) -> WorkspaceResponse:
    """Set or clear workspace rolling budgets (Theme D2).

    Pass 0 for daily/monthly to clear that scope. Null/omitted fields are
    left unchanged.
    """
    workspace = store.get_workspace(workspace_id)
    if not workspace:
        workspace = store.get_workspace_by_name(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

    if body.daily_budget_usd is not None:
        workspace.daily_budget_usd = body.daily_budget_usd if body.daily_budget_usd > 0 else None
    if body.monthly_budget_usd is not None:
        workspace.monthly_budget_usd = body.monthly_budget_usd if body.monthly_budget_usd > 0 else None

    store.update_workspace(workspace)

    projects = store.list_projects_by_workspace(workspace.id)
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        path=str(workspace.path),
        project_count=len(projects),
        auto_discover=workspace.auto_discover,
        daily_budget_usd=workspace.daily_budget_usd,
        monthly_budget_usd=workspace.monthly_budget_usd,
        daily_spend_usd=store.get_workspace_daily_spend(workspace.id),
        monthly_spend_usd=store.get_workspace_monthly_spend(workspace.id),
    )


@router.delete("/api/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    store: Annotated[GluonStore, Depends(get_store)],
) -> dict:
    """Delete a workspace (projects are kept but unlinked)."""
    workspace = store.get_workspace(workspace_id)
    if not workspace:
        workspace = store.get_workspace_by_name(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

    success = store.delete_workspace(workspace.id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete workspace")

    return {"deleted": True, "workspace_id": workspace.id}
