"""FastAPI application for Gluon Web Dashboard."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from gluon.cleanup import LogCleanupService
from gluon.core import Orchestrator, ProjectNotFoundError
from gluon.models import RunStatus
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    AttachImageRequest,
    BranchListResponse,
    BranchOperationResponse,
    BranchResponse,
    ChangeBaseBranchRequest,
    CommitDetailResponse,
    CommitResponse,
    ConflictDetectionResponse,
    ConflictDiffResponse,
    ConflictFileResponse,
    CreateProjectRequest,
    CreateRunRequest,
    CreateWorkspaceRequest,
    DailyUsageResponse,
    FileChangeResponse,
    FileDiffResponse,
    ForcePushCheckResponse,
    ForcePushRequest,
    ForcePushResponse,
    ImageResponse,
    LogResponse,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectUsageResponse,
    RebaseRequest,
    RebaseResponse,
    RenameBranchRequest,
    ResolveConflictRequest,
    ResolveConflictResponse,
    ResumeRunRequest,
    ResumeRunResponse,
    RunCommitsResponse,
    RunDetailResponse,
    RunFilesResponse,
    RunImagesResponse,
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


def _get_git_branch(project_path: str | Path) -> str | None:
    """Get the current git branch for a project path."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _get_git_ahead_behind(project_path: str | Path) -> tuple[int | None, int | None]:
    """Get commits ahead/behind upstream for a project path."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None, None


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
            pr_mergeable=run.pr_mergeable,
            # Archive tracking
            archived=run.archived,
        )

    # ========== REST API Routes ==========

    @app.get("/api/runs", response_model=list[RunResponse])
    async def list_runs(
        project_id: str | None = None,
        status: Annotated[list[str] | None, Query()] = None,
        limit: int = 50,
        archived: bool = False,
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

        runs = store.list_runs(
            project_id=project_id,
            statuses=statuses,
            limit=limit,
            include_archived=archived,
        )

        # When viewing archived, only show archived runs; otherwise only show non-archived
        if archived:
            runs = [r for r in runs if r.archived]
        else:
            runs = [r for r in runs if not r.archived]

        project_lookup = get_project_lookup()

        return [run_to_response(run, project_lookup) for run in runs]

    @app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
    async def get_run(run_id: str, refresh_pr: bool = True) -> RunDetailResponse:
        """Get details for a specific run."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Refresh status if active
        if run.is_active:
            runner.refresh_run_status(run)

        # Refresh PR status from GitHub if run has an open PR
        # This catches when user merges PR on GitHub website
        if refresh_pr and run.pr_status == "open" and run.branch_name:
            try:
                project = store.get_project(run.project_id)
                if project:
                    pr_info = await runner.git_manager._get_pr_info(project.expanded_path, run.branch_name)
                    if pr_info:
                        # Check for any changes to pr_status or pr_mergeable
                        status_changed = pr_info.get("status") != run.pr_status
                        mergeable_changed = pr_info.get("mergeable") != run.pr_mergeable
                        if status_changed or mergeable_changed:
                            run.pr_status = pr_info.get("status")
                            run.pr_mergeable = pr_info.get("mergeable")
                            store.update_run(run)
                            # Broadcast update to WebSocket clients
                            project_lookup_temp = get_project_lookup()
                            project_name = project_lookup_temp.get(run.project_id, run.project_id[:8])
                            await ws_manager.broadcast_run_update(run, project_name)
            except Exception as e:
                logger.debug(f"Failed to refresh PR status: {e}")

        project_lookup = get_project_lookup()

        # Check if project has a git remote configured
        has_remote = True  # Default to true
        project = store.get_project(run.project_id)
        if project:
            git_status = store.get_git_status(project.id)
            if git_status:
                has_remote = git_status.remote is not None

        # Compute commit/file counts for tab badges (lightweight git operations)
        commit_count = None
        file_count = None
        if run.branch_name and project:
            from gluon.git_manager import GitManager
            git_manager_temp = GitManager(store)
            working_path = Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path
            base_branch = run.source_branch or "main"
            try:
                commit_count = await git_manager_temp.get_commit_count(working_path, run.branch_name, base_branch)
                file_count = await git_manager_temp.get_file_count(working_path, run.branch_name, base_branch)
            except Exception:
                pass  # Counts are optional, don't fail request

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
            pr_mergeable=run.pr_mergeable,
            has_remote=has_remote,
            # Resume tracking
            resume_count=run.resume_count,
            last_resumed_at=run.last_resumed_at.isoformat() if run.last_resumed_at else None,
            # Precomputed counts for tab badges
            commit_count=commit_count,
            file_count=file_count,
        )

    @app.post("/api/runs", response_model=RunResponse)
    async def create_run(body: CreateRunRequest) -> RunResponse:
        """Create and start a new execution run."""
        try:
            project = orchestrator.get_project(body.project_name)
        except ProjectNotFoundError:
            raise HTTPException(status_code=404, detail=f"Project not found: {body.project_name}")

        # Create the run
        run = await runner.submit(
            project_id=project.id,
            prompt=body.prompt,
            wait=False,
            use_worktree=body.use_worktree,
            initiator="web:dashboard",
            model=body.model,
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
        Resume a completed/failed run in-place (same run ID continues).

        The run continues with the same Claude session context, worktree,
        and branch. Logs are appended, costs accumulate.
        """
        # Get the run to resume
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Get the project for this run
        project_lookup = get_project_lookup()
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

    # ========== Git Commits and Files ==========

    @app.get("/api/runs/{run_id}/commits", response_model=RunCommitsResponse)
    async def get_run_commits(run_id: str) -> RunCommitsResponse:
        """
        Get commits on the run's branch since it diverged from the base branch.
        Only available for worktree runs with a branch.
        """
        from gluon.git_manager import GitManager

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.branch_name:
            return RunCommitsResponse(
                run_id=run.id,
                branch_name=None,
                base_branch="main",
                commit_count=0,
                commits=[],
            )

        # Get project to find repo path
        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Determine working path (worktree or project root)
        working_path = Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path

        # Get base branch (source_branch or default to main)
        base_branch = run.source_branch or "main"

        # Fetch commits
        git_manager = GitManager(store)
        commits_data = await git_manager.get_branch_commits(
            path=working_path,
            branch_name=run.branch_name,
            base_branch=base_branch,
        )

        return RunCommitsResponse(
            run_id=run.id,
            branch_name=run.branch_name,
            base_branch=base_branch,
            commit_count=len(commits_data),
            commits=[CommitResponse(**c) for c in commits_data],
        )

    @app.get("/api/runs/{run_id}/files", response_model=RunFilesResponse)
    async def get_run_files(run_id: str) -> RunFilesResponse:
        """
        Get files changed on the run's branch since it diverged from the base branch.
        Only available for worktree runs with a branch.
        """
        from gluon.git_manager import GitManager

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.branch_name:
            return RunFilesResponse(
                run_id=run.id,
                branch_name=None,
                base_branch="main",
                file_count=0,
                total_additions=0,
                total_deletions=0,
                files=[],
            )

        # Get project to find repo path
        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Determine working path (worktree or project root)
        working_path = Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path

        # Get base branch (source_branch or default to main)
        base_branch = run.source_branch or "main"

        # Fetch file changes
        git_manager = GitManager(store)
        files_data = await git_manager.get_changed_files(
            path=working_path,
            branch_name=run.branch_name,
            base_branch=base_branch,
        )

        # Calculate totals
        total_additions = sum(f["additions"] for f in files_data)
        total_deletions = sum(f["deletions"] for f in files_data)

        return RunFilesResponse(
            run_id=run.id,
            branch_name=run.branch_name,
            base_branch=base_branch,
            file_count=len(files_data),
            total_additions=total_additions,
            total_deletions=total_deletions,
            files=[FileChangeResponse(**f) for f in files_data],
        )

    @app.get("/api/runs/{run_id}/commits/{sha}", response_model=CommitDetailResponse)
    async def get_commit_detail(run_id: str, sha: str) -> CommitDetailResponse:
        """
        Get detailed information for a specific commit including files changed.
        This is lazy-loaded when a commit row is expanded.
        """
        from gluon.git_manager import GitManager

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.branch_name:
            raise HTTPException(status_code=400, detail="Run has no branch")

        # Get project to find repo path
        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Determine working path (worktree or project root)
        working_path = Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path

        # Fetch commit details
        git_manager = GitManager(store)
        commit_data = await git_manager.get_commit_detail(path=working_path, sha=sha)

        if not commit_data:
            raise HTTPException(status_code=404, detail=f"Commit not found: {sha}")

        return CommitDetailResponse(
            sha=commit_data["sha"],
            message=commit_data["message"],
            author=commit_data["author"],
            author_email=commit_data["author_email"],
            date=commit_data["date"],
            files=[FileChangeResponse(**f) for f in commit_data.get("files", [])],
        )

    @app.get("/api/runs/{run_id}/files/{file_path:path}/diff", response_model=FileDiffResponse)
    async def get_file_diff(run_id: str, file_path: str) -> FileDiffResponse:
        """
        Get unified diff for a specific file.
        This is lazy-loaded when a file row is expanded.
        """
        from gluon.git_manager import GitManager

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.branch_name:
            raise HTTPException(status_code=400, detail="Run has no branch")

        # Get project to find repo path
        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Determine working path (worktree or project root)
        working_path = Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path

        # Get base branch (source_branch or default to main)
        base_branch = run.source_branch or "main"

        # Fetch file diff
        git_manager = GitManager(store)
        diff_data = await git_manager.get_file_diff(
            path=working_path,
            file_path=file_path,
            branch_name=run.branch_name,
            base_branch=base_branch,
        )

        return FileDiffResponse(
            file_path=diff_data["file_path"],
            diff=diff_data["diff"],
            additions=diff_data["additions"],
            deletions=diff_data["deletions"],
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
            # Use expanded_path for git commands (resolves ${HOME}, ~, etc.)
            expanded = project.expanded_path
            git_branch = _get_git_branch(expanded)
            git_ahead, git_behind = _get_git_ahead_behind(expanded)
            result.append(
                ProjectResponse(
                    id=project.id,
                    name=project.name,
                    path=str(project.path),  # Keep original path for display
                    session_count=len(sessions),
                    workspace_id=project.workspace_id,
                    git_branch=git_branch,
                    git_ahead=git_ahead,
                    git_behind=git_behind,
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

    @app.post("/api/runs/{run_id}/pr-status", response_model=RunResponse)
    async def update_pr_status(run_id: str, pr_status: str = Query(..., description="New PR status")) -> RunResponse:
        """Update the PR status for a run (e.g., mark as merged to move from REVIEW to DONE)."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

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

    # ========== Phase 9: Settings API ==========

    @app.get("/api/settings")
    async def get_all_settings() -> dict[str, str]:
        """Get all settings as key-value pairs."""
        return store.get_all_settings()

    @app.put("/api/settings/{key}")
    async def update_setting(key: str, body: dict) -> dict[str, str]:
        """Update a single setting value."""
        value = body.get("value")
        if value is None:
            raise HTTPException(status_code=400, detail="Missing 'value' in request body")
        store.set_setting(key, str(value))
        return {"key": key, "value": str(value)}

    @app.post("/api/runs/{run_id}/create-pr")
    async def create_pr_for_run(run_id: str) -> dict:
        """Manually create a PR for a completed worktree run."""
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.use_worktree or not run.branch_name:
            raise HTTPException(
                status_code=400,
                detail="Run is not a worktree run or has no branch"
            )

        if run.pr_url:
            raise HTTPException(
                status_code=400,
                detail=f"PR already exists: {run.pr_url}"
            )

        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Use git manager to push branch and create PR
        from gluon.git_manager import GitManager
        git_manager = GitManager()

        # Determine working path (worktree if still exists, else project root)
        working_path = Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path

        try:
            pr_result = await git_manager.push_branch_and_create_pr(
                project_path=working_path,
                branch_name=run.branch_name,
                prompt=run.prompt,
                run_id=run.id,
            )

            if pr_result.get("pr_url"):
                run.pr_number = pr_result.get("pr_number")
                run.pr_url = pr_result.get("pr_url")
                run.pr_status = pr_result.get("pr_status")
                store.update_run(run)

                # Broadcast update
                project_lookup = get_project_lookup()
                project_name = project_lookup.get(run.project_id, run.project_id[:8])
                await ws_manager.broadcast_run_update(run, project_name)

                return {
                    "success": True,
                    "pr_url": run.pr_url,
                    "pr_number": run.pr_number,
                    "pr_status": run.pr_status,
                }
            else:
                return {
                    "success": False,
                    "error": pr_result.get("error", "Failed to create PR"),
                }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create PR: {e}")

    @app.post("/api/runs/{run_id}/merge")
    async def merge_run_branch(run_id: str) -> dict:
        """
        Merge a run's feature branch into the base branch locally and push (if remote exists).
        GitHub will automatically close the PR when the merge is pushed.

        Works for:
        - Runs with open PRs (merges and pushes, PR auto-closes)
        - Runs without PRs (local merge only)
        - Runs without remotes (local merge only)

        Returns conflict info if merge fails due to conflicts, allowing
        the user to resume the agent to resolve them.
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.use_worktree or not run.branch_name:
            raise HTTPException(
                status_code=400,
                detail="Run is not a worktree run or has no branch"
            )

        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Use git manager to merge branch locally and push
        from gluon.git_manager import GitManager
        git_manager = GitManager(store)

        # Use main project path (not worktree) for merging
        # Must use expanded_path to resolve ${HOME} and other variables
        project_path = project.expanded_path

        # Determine base branch (source_branch or default to main)
        base_branch = run.source_branch or "main"

        try:
            merge_result = await git_manager.merge_branch_locally(
                project_path=project_path,
                branch_name=run.branch_name,
                base_branch=base_branch,
                push_after_merge=True,  # Will only push if remote exists
            )

            if merge_result.get("success"):
                # Mark run as merged (works for both PRs and local-only merges)
                run.pr_status = "merged"
                store.update_run(run)

                # Broadcast update
                project_lookup = get_project_lookup()
                project_name = project_lookup.get(run.project_id, run.project_id[:8])
                await ws_manager.broadcast_run_update(run, project_name)

                return {
                    "success": True,
                    "message": merge_result.get("message"),
                    "merged_commit_sha": merge_result.get("merged_commit_sha"),
                }
            else:
                # Return conflict info if available
                return {
                    "success": False,
                    "error": merge_result.get("error", "Merge failed"),
                    "has_conflicts": merge_result.get("has_conflicts", False),
                    "conflicting_files": merge_result.get("conflicting_files", []),
                }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to merge: {e}")

    # ========== Image Attachments API (Phase 10.1) ==========

    from gluon.image_storage import (
        ImageStorageService,
        ImageNotFoundError,
        ImageTooLargeError,
        InvalidImageFormatError,
    )

    image_service = ImageStorageService(store)

    def image_to_response(image) -> ImageResponse:
        """Convert ImageAttachment to ImageResponse."""
        return ImageResponse(
            id=image.id,
            file_path=image.file_path,
            original_name=image.original_name,
            mime_type=image.mime_type,
            size_bytes=image.size_bytes,
            hash=image.hash,
            created_at=image.created_at.isoformat(),
        )

    @app.post("/api/images/upload", response_model=ImageResponse)
    async def upload_image(file: UploadFile) -> ImageResponse:
        """
        Upload an image file.

        Returns the image metadata. The image can then be attached to runs.
        Duplicate images (same content hash) return the existing image.
        """
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")

        try:
            data = await file.read()
            image = image_service.save_image(
                data=data,
                original_name=file.filename,
                mime_type=file.content_type,
            )
            return image_to_response(image)
        except ImageTooLargeError as e:
            raise HTTPException(status_code=413, detail=str(e))
        except InvalidImageFormatError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Image upload failed: {e}")
            raise HTTPException(status_code=500, detail="Image upload failed")

    @app.get("/api/images/{image_id}", response_model=ImageResponse)
    async def get_image(image_id: str) -> ImageResponse:
        """Get image metadata by ID."""
        try:
            image = image_service.get_image(image_id)
            return image_to_response(image)
        except ImageNotFoundError:
            raise HTTPException(status_code=404, detail=f"Image not found: {image_id}")

    @app.get("/api/images/{image_id}/file")
    async def serve_image(image_id: str) -> Response:
        """Serve the actual image file."""
        try:
            data, image = image_service.get_image_data(image_id)
            return Response(
                content=data,
                media_type=image.mime_type or "application/octet-stream",
                headers={
                    "Content-Disposition": f'inline; filename="{image.original_name}"',
                    "Cache-Control": "public, max-age=31536000",  # Cache for 1 year (content-addressed)
                },
            )
        except ImageNotFoundError:
            raise HTTPException(status_code=404, detail=f"Image not found: {image_id}")

    @app.delete("/api/images/{image_id}")
    async def delete_image(image_id: str) -> dict:
        """Delete an image (only if not attached to any runs)."""
        # Check if image exists
        try:
            image_service.get_image(image_id)
        except ImageNotFoundError:
            raise HTTPException(status_code=404, detail=f"Image not found: {image_id}")

        # Check if attached to any runs
        ref_count = store.count_image_references(image_id)
        if ref_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Image is attached to {ref_count} run(s). Detach first.",
            )

        success = image_service.delete_image(image_id)
        return {"deleted": success, "image_id": image_id}

    @app.get("/api/runs/{run_id}/attachments", response_model=RunImagesResponse)
    async def get_run_attachments(run_id: str) -> RunImagesResponse:
        """Get all images attached to a run."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        images = image_service.list_images_for_run(run.id)
        return RunImagesResponse(
            run_id=run.id,
            image_count=len(images),
            images=[image_to_response(img) for img in images],
        )

    @app.post("/api/runs/{run_id}/attachments", response_model=ImageResponse)
    async def attach_image_to_run(run_id: str, file: UploadFile | None = None, body: AttachImageRequest | None = None) -> ImageResponse:
        """
        Attach an image to a run.

        Either upload a new image (multipart form with 'file') or
        attach an existing image (JSON body with 'image_id').
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if file and file.filename:
            # Upload new image and attach
            try:
                data = await file.read()
                image = image_service.save_image(
                    data=data,
                    original_name=file.filename,
                    mime_type=file.content_type,
                )
                image_service.attach_to_run(run.id, image.id)
                return image_to_response(image)
            except ImageTooLargeError as e:
                raise HTTPException(status_code=413, detail=str(e))
            except InvalidImageFormatError as e:
                raise HTTPException(status_code=400, detail=str(e))
        elif body and body.image_id:
            # Attach existing image
            try:
                image_service.attach_to_run(run.id, body.image_id)
                image = image_service.get_image(body.image_id)
                return image_to_response(image)
            except ImageNotFoundError:
                raise HTTPException(status_code=404, detail=f"Image not found: {body.image_id}")
        else:
            raise HTTPException(status_code=400, detail="Provide either a file upload or image_id")

    @app.delete("/api/runs/{run_id}/attachments/{image_id}")
    async def detach_image_from_run(run_id: str, image_id: str) -> dict:
        """Detach an image from a run."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        success = image_service.detach_from_run(run.id, image_id)
        if not success:
            raise HTTPException(status_code=404, detail="Image not attached to this run")

        return {"detached": True, "run_id": run.id, "image_id": image_id}

    # ========== Advanced Git Operations (Phase 5) ==========

    from gluon.git_manager import GitManager

    git_manager = GitManager(store)

    @app.get("/api/projects/{project_id}/conflicts", response_model=ConflictDetectionResponse)
    async def detect_conflicts(project_id: str) -> ConflictDetectionResponse:
        """
        Detect if there are conflicts in the project (rebase/merge in progress).
        Returns conflict state and list of conflicted files.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        # Detect conflict state
        conflict_state = await git_manager._detect_conflict_state(project.expanded_path)

        # Get detailed conflict info
        conflicts = await git_manager.detect_conflicts(project.expanded_path)

        return ConflictDetectionResponse(
            has_conflicts=len(conflicts) > 0,
            is_rebase_in_progress=conflict_state.get("is_rebase_in_progress", False),
            is_merge_in_progress=conflict_state.get("is_merge_in_progress", False),
            conflict_operation=conflict_state.get("conflict_operation"),
            rebase_current_step=conflict_state.get("rebase_current_step"),
            rebase_total_steps=conflict_state.get("rebase_total_steps"),
            conflicted_files=[
                ConflictFileResponse(
                    file_path=c["file_path"],
                    conflict_markers_count=c["conflict_markers_count"],
                )
                for c in conflicts
            ],
        )

    @app.get("/api/projects/{project_id}/conflicts/{file_path:path}", response_model=ConflictDiffResponse)
    async def get_conflict_diff(project_id: str, file_path: str) -> ConflictDiffResponse:
        """
        Get 3-way diff for a conflicted file.
        Returns base (common ancestor), ours (HEAD), theirs (incoming), and merged (current with markers).
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        diff_data = await git_manager.get_conflict_diff(project.expanded_path, file_path)

        return ConflictDiffResponse(
            file_path=diff_data["file_path"],
            base=diff_data.get("base"),
            ours=diff_data.get("ours"),
            theirs=diff_data.get("theirs"),
            merged=diff_data.get("merged"),
        )

    @app.post("/api/projects/{project_id}/conflicts/resolve", response_model=ResolveConflictResponse)
    async def resolve_conflict(project_id: str, body: ResolveConflictRequest) -> ResolveConflictResponse:
        """
        Resolve a conflict by choosing ours, theirs, or marking as resolved.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        if body.resolution not in ("ours", "theirs", "resolved"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid resolution: {body.resolution}. Must be ours, theirs, or resolved.",
            )

        result = await git_manager.resolve_conflict(project.expanded_path, body.file_path, body.resolution)

        return ResolveConflictResponse(
            success=result["success"],
            message=result["message"],
        )

    @app.post("/api/projects/{project_id}/rebase", response_model=RebaseResponse)
    async def start_rebase(project_id: str, body: RebaseRequest) -> RebaseResponse:
        """
        Start a rebase onto another branch.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.rebase_branch(project.expanded_path, body.onto_branch)

        return RebaseResponse(
            success=result["success"],
            message=result["message"],
            conflicts=result.get("conflicts", []),
        )

    @app.post("/api/projects/{project_id}/rebase/continue", response_model=RebaseResponse)
    async def continue_rebase(project_id: str) -> RebaseResponse:
        """
        Continue a rebase after resolving conflicts.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.rebase_continue(project.expanded_path)

        return RebaseResponse(
            success=result["success"],
            message=result["message"],
            conflicts=result.get("conflicts", []),
        )

    @app.post("/api/projects/{project_id}/rebase/abort", response_model=RebaseResponse)
    async def abort_rebase(project_id: str) -> RebaseResponse:
        """
        Abort an in-progress rebase.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.rebase_abort(project.expanded_path)

        return RebaseResponse(
            success=result["success"],
            message=result["message"],
        )

    @app.post("/api/projects/{project_id}/rebase/skip", response_model=RebaseResponse)
    async def skip_rebase_commit(project_id: str) -> RebaseResponse:
        """
        Skip the current commit during rebase.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.rebase_skip(project.expanded_path)

        return RebaseResponse(
            success=result["success"],
            message=result["message"],
            conflicts=result.get("conflicts", []),
        )

    @app.get("/api/projects/{project_id}/force-push-check", response_model=ForcePushCheckResponse)
    async def check_force_push_needed(project_id: str, branch: str | None = None) -> ForcePushCheckResponse:
        """
        Check if a force push would be required for the current branch.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.check_force_push_needed(project.expanded_path, branch)

        return ForcePushCheckResponse(
            needed=result["needed"],
            commits_to_delete=result["commits_to_delete"],
            reason=result["reason"],
        )

    @app.post("/api/projects/{project_id}/force-push", response_model=ForcePushResponse)
    async def force_push(project_id: str, body: ForcePushRequest) -> ForcePushResponse:
        """
        Force push to remote. Use with caution!
        Defaults to --force-with-lease for safety.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.force_push(
            project.expanded_path,
            branch=body.branch,
            force_with_lease=body.force_with_lease,
        )

        return ForcePushResponse(
            success=result["success"],
            message=result["message"],
        )

    @app.get("/api/projects/{project_id}/branches", response_model=BranchListResponse)
    async def list_branches(project_id: str) -> BranchListResponse:
        """
        List all branches in the repository.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        branches = await git_manager.list_branches(project.expanded_path)

        current_branch = None
        for b in branches:
            if b.get("is_current"):
                current_branch = b["name"]
                break

        return BranchListResponse(
            branches=[
                BranchResponse(
                    name=b["name"],
                    is_current=b.get("is_current", False),
                    upstream=b.get("upstream"),
                    ahead=b.get("ahead", 0),
                    behind=b.get("behind", 0),
                )
                for b in branches
            ],
            current_branch=current_branch,
        )

    @app.post("/api/projects/{project_id}/branches/rename", response_model=BranchOperationResponse)
    async def rename_branch(project_id: str, body: RenameBranchRequest) -> BranchOperationResponse:
        """
        Rename a branch.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.rename_branch(project.expanded_path, body.old_name, body.new_name)

        return BranchOperationResponse(
            success=result["success"],
            message=result["message"],
        )

    @app.post("/api/projects/{project_id}/branches/change-base", response_model=BranchOperationResponse)
    async def change_branch_base(project_id: str, body: ChangeBaseBranchRequest) -> BranchOperationResponse:
        """
        Change the base of a feature branch by rebasing onto a new base.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.change_base_branch(project.expanded_path, body.feature_branch, body.new_base)

        return BranchOperationResponse(
            success=result["success"],
            message=result["message"],
            conflicts=result.get("conflicts", []),
        )

    @app.delete("/api/projects/{project_id}/branches/{branch_name}", response_model=BranchOperationResponse)
    async def delete_branch(
        project_id: str,
        branch_name: str,
        force: bool = False,
        remote: bool = False,
    ) -> BranchOperationResponse:
        """
        Delete a branch (local or remote).
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        result = await git_manager.delete_branch(project.expanded_path, branch_name, force=force, remote=remote)

        return BranchOperationResponse(
            success=result["success"],
            message=result["message"],
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

    # ========== Background Polling for Run Status Updates ==========

    # Track last known run states to detect changes
    _last_run_states: dict[str, str] = {}
    _polling_task: asyncio.Task | None = None
    _cleanup_task: asyncio.Task | None = None

    # Cleanup configuration
    cleanup_interval_seconds = 8 * 60 * 60  # 8 hours
    cleanup_initial_delay_seconds = 300  # 5 minutes after startup

    async def _poll_run_status_changes() -> None:
        """Background task to poll for run status changes and broadcast updates."""
        project_lookup = get_project_lookup()

        while True:
            try:
                # Refresh all running runs
                runner.refresh_all_runs()

                # Check all non-archived runs for status changes
                runs = store.list_runs(limit=100, include_archived=False)
                runs = [r for r in runs if not r.archived]

                for run in runs:
                    run_key = run.id
                    # Track status AND pr_status for Kanban column changes
                    current_state = f"{run.status.value}:{run.pr_status or 'none'}"

                    # Check if state changed
                    if run_key in _last_run_states:
                        if _last_run_states[run_key] != current_state:
                            # State changed - broadcast update
                            project_name = project_lookup.get(run.project_id, run.project_id[:8])
                            await ws_manager.broadcast_run_update(run, project_name)
                            logger.debug(f"Broadcast run update: {run.id[:8]} {_last_run_states[run_key]} -> {current_state}")

                    _last_run_states[run_key] = current_state

                # Clean up old run states (keep last 200)
                if len(_last_run_states) > 200:
                    # Keep only runs we just saw
                    current_ids = {r.id for r in runs}
                    _last_run_states.clear()
                    for run in runs:
                        _last_run_states[run.id] = f"{run.status.value}:{run.pr_status or 'none'}"

                # Refresh project lookup occasionally (new projects)
                project_lookup = get_project_lookup()

            except Exception as e:
                logger.error(f"Error in run status polling: {e}")

            # Poll every 2 seconds
            await asyncio.sleep(2)

    async def _cleanup_old_logs() -> None:
        """Background task to cleanup old log files based on retention policies.

        Runs after initial delay, then every 8 hours.
        - Archived runs: logs deleted 30 days after execution
        - Failed runs: logs deleted 7 days after execution
        - Orphan logs (no DB record): deleted immediately
        """
        cleanup_service = LogCleanupService(store=store)

        # Initial delay before first cleanup (300 seconds = 5 minutes)
        logger.info(
            f"Log cleanup scheduled: first run in {cleanup_initial_delay_seconds}s, "
            f"then every {cleanup_interval_seconds // 3600}h"
        )
        await asyncio.sleep(cleanup_initial_delay_seconds)

        while True:
            try:
                logger.info("Starting log cleanup...")
                stats = cleanup_service.cleanup()
                total = (
                    stats["orphan_deleted"]
                    + stats["archived_deleted"]
                    + stats["failed_deleted"]
                )
                if total > 0 or stats["errors"] > 0:
                    logger.info(
                        f"Log cleanup complete: {stats['orphan_deleted']} orphan, "
                        f"{stats['archived_deleted']} archived, "
                        f"{stats['failed_deleted']} failed deleted, "
                        f"{stats['errors']} errors"
                    )
                else:
                    logger.info("Log cleanup complete: no logs to delete")
            except Exception as e:
                logger.error(f"Error in log cleanup task: {e}")

            # Wait for next cleanup cycle
            await asyncio.sleep(cleanup_interval_seconds)

    @app.on_event("startup")
    async def start_background_tasks() -> None:
        """Start background tasks on app startup."""
        nonlocal _polling_task, _cleanup_task
        _polling_task = asyncio.create_task(_poll_run_status_changes())
        _cleanup_task = asyncio.create_task(_cleanup_old_logs())
        logger.info("Started background tasks: run status polling, log cleanup")

    @app.on_event("shutdown")
    async def stop_background_tasks() -> None:
        """Stop background tasks on app shutdown."""
        tasks_to_cancel = []
        if _polling_task:
            tasks_to_cancel.append(("run status polling", _polling_task))
        if _cleanup_task:
            tasks_to_cancel.append(("log cleanup", _cleanup_task))

        for name, task in tasks_to_cancel:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info(f"Stopped {name} background task")

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
