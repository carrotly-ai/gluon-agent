"""FastAPI application for Gluon Web Dashboard."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from gluon.core import Orchestrator, ProjectNotFoundError
from gluon.models import RunStatus
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    CreateProjectRequest,
    CreateRunRequest,
    CreateWorkspaceRequest,
    DailyUsageResponse,
    LogResponse,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectUsageResponse,
    ResumeRunRequest,
    ResumeRunResponse,
    RunDetailResponse,
    RunResponse,
    RunUsageItemResponse,
    ScanResultResponse,
    SessionHistoryResponse,
    StatusResponse,
    UpdateStatusRequest,
    UpdateStatusResponse,
    UsageSummaryResponse,
    WorkspaceResponse,
)
from gluon.web.websocket import ws_manager

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Gluon Web Dashboard",
        description="Web interface for managing Gluon Agent task execution",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Shared instances
    store = GluonStore()
    orchestrator = Orchestrator(store=store)
    runner = TaskRunner(store=store)

    # Build project lookup helper
    def get_project_lookup() -> dict[str, str]:
        """Build project_id → project_name lookup."""
        return {p.id: p.name for p in store.list_projects()}

    def run_to_response(run, project_lookup: dict[str, str]) -> RunResponse:
        """Convert ExecutionRun to RunResponse."""
        return RunResponse(
            id=run.id,
            project_id=run.project_id,
            project_name=project_lookup.get(run.project_id, run.project_id[:8]),
            status=run.status.value,
            prompt=run.prompt,
            initiator=run.initiator,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_seconds=run.duration_seconds,
            error_message=run.error_message,
            cost_usd=run.cost_usd,
            # Git/PR fields for Kanban routing
            use_worktree=run.use_worktree,
            branch_name=run.branch_name,
            pr_number=run.pr_number,
            pr_status=run.pr_status,
            # Archive tracking
            archived=run.archived,
        )

    # ========== REST API Routes ==========

    @app.get("/api/runs", response_model=list[RunResponse])
    async def list_runs(
        project_id: str | None = None,
        status: Annotated[list[str] | None, Query()] = None,
        limit: int = 50,
    ) -> list[RunResponse]:
        """List execution runs with optional filters."""
        # Refresh status of active runs
        runner.refresh_all_runs()

        # Convert status strings to enum
        statuses = None
        if status:
            try:
                statuses = [RunStatus(s) for s in status]
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid status: {e}")

        runs = store.list_runs(project_id=project_id, statuses=statuses, limit=limit)
        project_lookup = get_project_lookup()

        return [run_to_response(run, project_lookup) for run in runs]

    @app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
    async def get_run(run_id: str) -> RunDetailResponse:
        """Get details for a specific run."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Refresh status if active
        if run.is_active:
            runner.refresh_run_status(run)

        project_lookup = get_project_lookup()

        return RunDetailResponse(
            id=run.id,
            project_id=run.project_id,
            project_name=project_lookup.get(run.project_id, run.project_id[:8]),
            status=run.status.value,
            prompt=run.prompt,
            initiator=run.initiator,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_seconds=run.duration_seconds,
            error_message=run.error_message,
            session_id=run.claude_session_id,
            exit_code=run.exit_code,
            log_path=str(run.log_path) if run.log_path else None,
            # Cost tracking
            cost_usd=run.cost_usd,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            model_used=run.model_used,
            # Git/worktree tracking
            branch_name=run.branch_name,
            source_branch=run.source_branch,
            use_worktree=run.use_worktree,
            git_commit_sha=run.git_commit_sha,
            pr_number=run.pr_number,
            pr_url=run.pr_url,
            pr_status=run.pr_status,
        )

    @app.post("/api/runs", response_model=RunResponse)
    async def create_run(body: CreateRunRequest) -> RunResponse:
        """Create and start a new execution run."""
        try:
            project = orchestrator.get_project(body.project_name)
        except ProjectNotFoundError:
            raise HTTPException(status_code=404, detail=f"Project not found: {body.project_name}")

        # Create the run
        # TODO: Add model and use_worktree support to TaskRunner.submit()
        run = await runner.submit(
            project_id=project.id,
            prompt=body.prompt,
            wait=False,
        )

        project_lookup = get_project_lookup()
        response = run_to_response(run, project_lookup)

        # Broadcast to WebSocket clients
        await ws_manager.broadcast_run_created(run, project.name)

        return response

    @app.post("/api/runs/{run_id}/cancel", response_model=RunResponse)
    async def cancel_run(run_id: str) -> RunResponse:
        """Cancel a running task."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"Run is not active (status: {run.status.value})",
            )

        success = await runner.cancel(run.id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to cancel run")

        # Refresh and return updated run
        updated_run = store.get_run(run.id)
        if not updated_run:
            raise HTTPException(status_code=500, detail="Run disappeared after cancel")
        project_lookup = get_project_lookup()
        response = run_to_response(updated_run, project_lookup)

        # Broadcast update
        project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
        await ws_manager.broadcast_run_update(updated_run, project_name)

        return response

    @app.post("/api/runs/{run_id}/resume", response_model=ResumeRunResponse)
    async def resume_run(run_id: str, body: ResumeRunRequest) -> ResumeRunResponse:
        """
        Resume a completed/failed run by creating a new run that continues
        from the original session.

        The new run inherits the Claude session context from the original run,
        allowing the agent to continue where it left off.
        """
        # Get the original run
        original_run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not original_run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Check if the run has a Claude session ID that can be resumed
        if not original_run.claude_session_id:
            raise HTTPException(
                status_code=400,
                detail="Run does not have a session to resume",
            )

        # Only completed or failed runs can be resumed
        if original_run.status not in (RunStatus.COMPLETED, RunStatus.FAILED):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resume run with status: {original_run.status.value}. "
                "Only completed or failed runs can be resumed.",
            )

        # Get the project for this run
        project_lookup = get_project_lookup()
        project_name = project_lookup.get(original_run.project_id)
        if not project_name:
            raise HTTPException(
                status_code=400,
                detail="Could not find project for this run",
            )

        # Create a new run that continues from the same Claude session
        new_run = await runner.submit(
            project_id=original_run.project_id,
            prompt=body.prompt,
            claude_session_id=original_run.claude_session_id,
            wait=False,
        )

        # Broadcast to WebSocket clients
        await ws_manager.broadcast_run_created(new_run, project_name)

        return ResumeRunResponse(
            original_run_id=original_run.id,
            new_run_id=new_run.id,
            status=new_run.status.value,
        )

    @app.get("/api/runs/{run_id}/session-history", response_model=SessionHistoryResponse)
    async def get_session_history(run_id: str) -> SessionHistoryResponse:
        """
        Get the session history for a run - all runs that share the same Claude session.

        This is useful for viewing the full conversation history when a run has been
        resumed multiple times.
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.claude_session_id:
            raise HTTPException(
                status_code=400,
                detail="Run does not have a session",
            )

        # Get all runs in this session
        session_runs = store.list_runs_by_claude_session(run.claude_session_id)
        project_lookup = get_project_lookup()

        return SessionHistoryResponse(
            session_id=run.claude_session_id,
            runs=[run_to_response(r, project_lookup) for r in session_runs],
        )

    @app.get("/api/runs/{run_id}/logs", response_model=LogResponse)
    async def get_logs(
        run_id: str,
        stream: str = "stdout",
        tail: int | None = None,
    ) -> LogResponse:
        """Get log content for a run."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if stream not in ("stdout", "stderr", "messages"):
            raise HTTPException(status_code=400, detail=f"Invalid stream: {stream}")

        logs = runner.get_logs(run.id, tail=tail)
        content = logs.get(stream, "")
        line_count = len(content.splitlines()) if content else 0

        return LogResponse(
            run_id=run.id,
            stream=stream,
            content=content,
            line_count=line_count,
        )

    @app.get("/api/projects", response_model=list[ProjectResponse])
    async def list_projects() -> list[ProjectResponse]:
        """List all registered projects."""
        projects = orchestrator.list_projects()
        result = []

        for project in projects:
            sessions = orchestrator.list_sessions(project.name)
            result.append(
                ProjectResponse(
                    id=project.id,
                    name=project.name,
                    path=str(project.path),
                    session_count=len(sessions),
                )
            )

        return result

    @app.get("/api/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Get overall system status."""
        projects = orchestrator.list_projects()
        active_runs = store.list_active_runs()
        all_runs = store.list_runs(limit=1000)  # Get count

        return StatusResponse(
            total_projects=len(projects),
            active_runs=len(active_runs),
            total_runs=len(all_runs),
        )

    # ========== Phase 7.2: Status Transitions (Drag-and-Drop) ==========

    # Allowed status transitions for drag-and-drop
    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        "pending": {"cancelled"},
        "running": {"cancelled"},
        "completed": {"pending"},  # Re-queue for retry
        "failed": {"pending"},  # Retry
        "cancelled": {"pending"},  # Retry
    }

    @app.post("/api/runs/{run_id}/status", response_model=UpdateStatusResponse)
    async def update_run_status(run_id: str, body: UpdateStatusRequest) -> UpdateStatusResponse:
        """
        Manually transition a run's status (for drag-and-drop).

        Allowed transitions:
        - pending → cancelled (abort before start)
        - running → cancelled (manual abort - also kills process)
        - completed → pending (re-queue for retry)
        - failed → pending (re-queue for retry)
        - cancelled → pending (re-queue)
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Validate transition
        current_status = run.status.value
        new_status = body.status

        try:
            new_status_enum = RunStatus(new_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

        if new_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
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

        project_lookup = get_project_lookup()
        response_run = run_to_response(updated_run, project_lookup)

        # Broadcast update
        project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
        await ws_manager.broadcast_run_update(updated_run, project_name)

        return UpdateStatusResponse(
            run=response_run,
            previous_status=previous_status,
            new_status=new_status,
        )

    # ========== Archive Run ==========

    @app.post("/api/runs/{run_id}/archive", response_model=RunResponse)
    async def archive_run(run_id: str) -> RunResponse:
        """Archive a run to hide it from the board."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        updated_run = store.archive_run(run.id, archived=True)
        if not updated_run:
            raise HTTPException(status_code=500, detail="Failed to archive run")

        project_lookup = get_project_lookup()
        response = run_to_response(updated_run, project_lookup)

        # Broadcast update so UI reflects the change
        project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
        await ws_manager.broadcast_run_update(updated_run, project_name)

        return response

    # ========== Phase 7.3: Project Management ==========

    @app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
    async def get_project(project_id: str) -> ProjectDetailResponse:
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

    @app.post("/api/projects", response_model=ProjectResponse)
    async def create_project(body: CreateProjectRequest) -> ProjectResponse:
        """Register a new project."""
        from pathlib import Path

        # Check if project with same name exists
        existing = store.get_project_by_name(body.name)
        if existing:
            raise HTTPException(status_code=400, detail=f"Project already exists: {body.name}")

        # Check if path exists
        project_path = Path(body.path)
        if not project_path.exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")

        # Create project
        project = store.create_project(
            name=body.name,
            path=project_path,
            workspace_id=body.workspace_id,
        )

        return ProjectResponse(
            id=project.id,
            name=project.name,
            path=str(project.path),
            session_count=0,
        )

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict:
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

    # ========== Workspace Management ==========

    @app.get("/api/workspaces", response_model=list[WorkspaceResponse])
    async def list_workspaces() -> list[WorkspaceResponse]:
        """List all workspaces."""
        workspaces = store.list_workspaces()
        result = []
        for ws in workspaces:
            projects = store.list_projects_by_workspace(ws.id)
            result.append(
                WorkspaceResponse(
                    id=ws.id,
                    name=ws.name,
                    path=str(ws.path),
                    project_count=len(projects),
                    auto_discover=ws.auto_discover,
                )
            )
        return result

    @app.post("/api/workspaces", response_model=WorkspaceResponse)
    async def create_workspace(body: CreateWorkspaceRequest) -> WorkspaceResponse:
        """Create a new workspace."""
        from pathlib import Path

        # Check if workspace with same name exists
        existing = store.get_workspace_by_name(body.name)
        if existing:
            raise HTTPException(status_code=400, detail=f"Workspace already exists: {body.name}")

        # Check if path exists
        workspace_path = Path(body.path)
        if not workspace_path.exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")

        # Create workspace
        workspace = store.create_workspace(name=body.name, path=workspace_path)

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

        return WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            path=str(workspace.path),
            project_count=len(projects_added),
            auto_discover=workspace.auto_discover,
        )

    @app.delete("/api/workspaces/{workspace_id}")
    async def delete_workspace(workspace_id: str) -> dict:
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

    @app.post("/api/workspaces/{workspace_id}/scan", response_model=ScanResultResponse)
    async def scan_workspace(workspace_id: str) -> ScanResultResponse:
        """Rescan workspace for new projects."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            workspace = store.get_workspace_by_name(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        projects_added = []
        project_paths = workspace.scan_for_projects()

        for project_path in project_paths:
            project_name = project_path.name
            existing = store.get_project_by_name(project_name)
            if not existing:
                store.create_project(
                    name=project_name,
                    path=project_path,
                    workspace_id=workspace.id,
                )
                projects_added.append(project_name)

        return ScanResultResponse(
            workspace_id=workspace.id,
            projects_found=len(project_paths),
            projects_added=projects_added,
        )

    # ========== Phase 8: Usage Dashboard ==========

    @app.get("/api/usage/summary", response_model=UsageSummaryResponse)
    async def get_usage_summary() -> UsageSummaryResponse:
        """Get aggregated usage statistics for header display."""
        summary = store.get_usage_summary()
        return UsageSummaryResponse(**summary)

    @app.get("/api/usage/by-project", response_model=list[ProjectUsageResponse])
    async def get_usage_by_project(
        since: str | None = None,
        until: str | None = None,
    ) -> list[ProjectUsageResponse]:
        """Get usage breakdown by project."""
        from datetime import datetime

        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None

        data = store.get_usage_by_project(since=since_dt, until=until_dt)
        return [ProjectUsageResponse(**item) for item in data]

    @app.get("/api/usage/by-day", response_model=list[DailyUsageResponse])
    async def get_usage_by_day(
        since: str | None = None,
        until: str | None = None,
    ) -> list[DailyUsageResponse]:
        """Get daily usage for charts."""
        from datetime import datetime

        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None

        data = store.get_usage_by_day(since=since_dt, until=until_dt)
        return [DailyUsageResponse(**item) for item in data]

    @app.get("/api/usage/runs", response_model=list[RunUsageItemResponse])
    async def get_usage_runs(
        since: str | None = None,
        until: str | None = None,
        sort_by: str = "cost",
        sort_order: str = "desc",
        limit: int = 50,
    ) -> list[RunUsageItemResponse]:
        """Get runs with cost data for usage dashboard."""
        from datetime import datetime

        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None

        data = store.get_usage_runs(
            since=since_dt,
            until=until_dt,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
        )
        return [RunUsageItemResponse(**item) for item in data]

    # ========== WebSocket ==========

    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """WebSocket endpoint for real-time updates."""
        await ws_manager.connect(websocket)

        try:
            while True:
                data = await websocket.receive_text()
                await ws_manager.handle_client_message(websocket, data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            await ws_manager.disconnect(websocket)

    # ========== Static Files (SPA) ==========

    # Determine static files directory
    web_dir = Path(__file__).parent
    dist_dir = web_dir / "dist"

    if dist_dir.exists():
        # Serve static assets
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

        @app.get("/", response_class=HTMLResponse)
        async def serve_spa_root():
            """Serve the SPA index.html."""
            index_path = dist_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path)
            return HTMLResponse(
                content="<h1>Gluon Web Dashboard</h1><p>Frontend not built. Run: cd web-ui && npm run build</p>",
                status_code=200,
            )

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve SPA for client-side routing."""
            # Skip API routes
            if full_path.startswith("api"):
                raise HTTPException(status_code=404)

            # Try to serve static file first
            file_path = dist_dir / full_path
            if file_path.is_file():
                return FileResponse(file_path)

            # Fallback to index.html for SPA routing
            index_path = dist_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path)

            raise HTTPException(status_code=404)
    else:
        # Development mode - show placeholder
        @app.get("/", response_class=HTMLResponse)
        async def serve_placeholder():
            """Development placeholder."""
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Gluon Web Dashboard</title>
                    <style>
                        body { font-family: system-ui; max-width: 800px; margin: 50px auto; padding: 20px; }
                        code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }
                        pre { background: #f0f0f0; padding: 15px; border-radius: 5px; overflow-x: auto; }
                    </style>
                </head>
                <body>
                    <h1>Gluon Web Dashboard</h1>
                    <p>The React frontend is not built yet.</p>
                    <h2>Development Mode</h2>
                    <pre>
# Terminal 1: FastAPI (already running)
uvicorn gluon.web.api:create_app --factory --reload --port 45866

# Terminal 2: Vite dev server
cd web-ui && npm run dev
                    </pre>
                    <h2>API Documentation</h2>
                    <ul>
                        <li><a href="/api/docs">Swagger UI</a></li>
                        <li><a href="/api/redoc">ReDoc</a></li>
                        <li><a href="/api/openapi.json">OpenAPI Schema</a></li>
                    </ul>
                    <h2>Available Endpoints</h2>
                    <ul>
                        <li><code>GET /api/runs</code> - List runs</li>
                        <li><code>GET /api/runs/{id}</code> - Get run details</li>
                        <li><code>POST /api/runs</code> - Create new run</li>
                        <li><code>POST /api/runs/{id}/cancel</code> - Cancel run</li>
                        <li><code>GET /api/runs/{id}/logs</code> - Get logs</li>
                        <li><code>GET /api/projects</code> - List projects</li>
                        <li><code>GET /api/status</code> - System status</li>
                        <li><code>WS /api/ws</code> - WebSocket for real-time updates</li>
                    </ul>
                </body>
                </html>
                """,
                status_code=200,
            )

    return app


# For uvicorn direct run: uvicorn gluon.web.api:app
app = create_app()
