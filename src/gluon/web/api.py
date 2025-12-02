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
    CreateRunRequest,
    LogResponse,
    ProjectResponse,
    ResumeRunRequest,
    ResumeRunResponse,
    RunDetailResponse,
    RunResponse,
    StatusResponse,
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
