"""Project routes (#162) — clean (store-only) subset.

This router holds the project routes that depend only on the store:
``GET /api/projects/{id}`` (detail) and ``DELETE /api/projects/{id}``. The
git/path-heavy project routes stay inline in create_app by design —
``create_project`` (os.path.realpath taint-break that CodeQL only recognises in
api.py's module context), ``list_projects`` (per-project git subprocesses),
``/files``, ``/commands``, and the whole conflicts/rebase/branches/git cluster.
Paths unchanged → same fail-closed auth posture.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from gluon.store import GluonStore
from gluon.web.models import ProjectDetailResponse
from gluon.web.routers._deps import get_store

router = APIRouter(tags=["projects"])

Store = Annotated[GluonStore, Depends(get_store)]


@router.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: str, store: Store) -> ProjectDetailResponse:
    """Get detailed info for a specific project."""
    project = store.get_project(project_id)
    if not project:
        # Try by name
        project = store.get_project_by_name(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    # Get workspace info if applicable
    workspace_name = None
    if project.workspace_id:
        workspace = store.get_workspace(project.workspace_id)
        if workspace:
            workspace_name = workspace.name

    # Get run stats
    runs = store.list_runs(project_id=project.id, limit=1000)
    last_run_at = runs[0].created_at if runs else None

    return ProjectDetailResponse(
        id=project.id,
        name=project.name,
        path=str(project.path),
        session_count=len(store.list_sessions(project.id)),
        workspace_id=project.workspace_id,
        workspace_name=workspace_name,
        run_count=len(runs),
        last_run_at=last_run_at,
    )


@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, store: Store) -> dict[str, Any]:
    """Delete a project (cascades to sessions/runs)."""
    project = store.get_project(project_id)
    if not project:
        project = store.get_project_by_name(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    success = store.delete_project(project.id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete project")

    return {"deleted": True, "project_id": project.id}
