"""FastAPI application for Gluon Web Dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from gluon.cleanup import LogCleanupService, WorktreeCleanupService
from gluon.commands import get_slash_commands
from gluon.core import Orchestrator, ProjectNotFoundError
from gluon.files import get_project_files
from gluon.models import (
    ConcurrencyPolicy,
    Notification,
    OrchestratorTask,
    RunStatus,
    TaskComment,
    TaskSchedule,
    TaskStatus,
    expand_path,
    utc_now,
)
from gluon.notifier import NotificationDispatcher
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import (
    # Phase 2: Gastown feature models
    ActivityEventResponse,
    ActivityListResponse,
    AnswerQuestionRequest,
    AttachImageRequest,
    AttentionCountsResponse,
    AuthProvidersResponse,
    BranchListResponse,
    BranchOperationResponse,
    BranchResponse,
    ChangeBaseBranchRequest,
    ChangePasswordRequest,
    ClaudeSessionInfo,
    ClaudeSessionListResponse,
    ClaudeSessionMessageItem,
    ClaudeSessionMessagesResponse,
    CloneRepositoryRequest,
    CloneResultResponse,
    CommitDetailResponse,
    CommitResponse,
    ConflictDetectionResponse,
    ConflictDiffResponse,
    ConflictFileResponse,
    CreateLinkCodeRequest,
    CreateProjectRequest,
    CreateRunRequest,
    CreateTaskScheduleRequest,
    CreateUserRequest,
    CreateWorkspaceRequest,
    DailyUsageResponse,
    EditQueuedMessageRequest,
    FileChangeResponse,
    FileDiffResponse,
    ForcePushCheckResponse,
    ForcePushRequest,
    ForcePushResponse,
    ForkRunRequest,
    FormulaListResponse,
    FormulaRunRequest,
    FormulaRunResponse,
    FormulaStepResponse,
    FormulaTemplateResponse,
    FormulaVariableResponse,
    GitRefreshAllResponse,
    GitStatusResponse,
    GitSyncRequest,
    GitSyncResponse,
    ImageResponse,
    LinkCodeResponse,
    LinkStatusResponse,
    LoginRequest,
    LoginResponse,
    LogResponse,
    MeResponse,
    MergeQueueEntryResponse,
    MergeQueueListResponse,
    NotificationResponse,
    NotificationsListResponse,
    OIDCProviderInfo,
    PendingQuestionResponse,
    PendingQuestionsResponse,
    ProjectDetailResponse,
    ProjectFileResponse,
    ProjectFilesResponse,
    ProjectResponse,
    ProjectUsageResponse,
    ProviderResponse,
    QueuedMessageResponse,
    QueueFollowupRequest,
    QueueFollowupResponse,
    # Ralph Loop models
    RalphIterationResponse,
    RalphIterationsResponse,
    RebaseRequest,
    RebaseResponse,
    RecoverRunRequest,
    RecoverRunResponse,
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
    RunTodosResponse,
    RunUsageItemResponse,
    ScanResultResponse,
    # SDK Session Browser models
    SDKSessionResponse,
    SessionDetailResponse,
    SessionHistoryResponse,
    SessionMessageResponse,
    # Slash Command models
    SlashCommandResponse,
    SlashCommandsResponse,
    SnoozeRunRequest,
    StatusResponse,
    StopLoopResponse,
    SupervisionDecisionResponse,
    SupervisionDisableRequest,
    SupervisionEvaluateResponse,
    SupervisionStatusResponse,
    # Task tracking models (Theme B Phase 3)
    TaskAssignRequest,
    TaskCommentListResponse,
    TaskCommentRequest,
    TaskCommentResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskScheduleListResponse,
    TaskSchedulePreviewRequest,
    TaskSchedulePreviewResponse,
    TaskScheduleResponse,
    TaskUpdateRequest,
    TodoItemResponse,
    UpdateRunRequest,
    UpdateStatusRequest,
    UpdateStatusResponse,
    UpdateTaskScheduleRequest,
    UpdateUserRequest,
    UpdateWorkspaceBudgetRequest,
    UsageSummaryResponse,
    UserListResponse,
    UserResponse,
    VersionResponse,
    WitnessDecisionListResponse,
    WitnessDecisionResponse,
    WorkQueueAddRequest,
    WorkQueueItemResponse,
    WorkQueueListResponse,
    WorkspaceResponse,
    WorkspaceSettingsResponse,
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


# Serializes every workspace-scoped git operation. `os.environ` is process-global,
# so injecting one workspace's vars and yielding across an `await` would let a
# concurrent request for a different workspace observe (and run git with) those
# vars — a cross-workspace credential bleed. Holding this lock for the whole
# inject→run→restore window makes the mutation atomic with respect to other
# workspace-scoped operations.
_workspace_env_lock = asyncio.Lock()

# Holds references to fire-and-forget background tasks so the event loop's weak
# reference does not let them be garbage-collected mid-execution.
_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def _workspace_env(store: GluonStore, workspace_id: str | None):
    """Temporarily inject workspace env vars into os.environ for git operations.

    Lock-guarded so concurrent requests for different workspaces cannot interleave
    their mutations of the shared process environment.
    """
    if not workspace_id:
        yield
        return
    ws_env = store.get_workspace_env_vars(workspace_id)
    if not ws_env:
        yield
        return
    async with _workspace_env_lock:
        saved = {k: os.environ.get(k) for k in ws_env}
        os.environ.update(ws_env)
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


_SECRET_KEY_MARKERS = ("secret", "token", "password", "passwd", "api_key")


def _redact_setting(key: str, value: str) -> str:
    """Mask secret-looking setting values so they are never returned to clients."""
    low = key.lower()
    if value and (any(m in low for m in _SECRET_KEY_MARKERS) or low.endswith("_key")):
        return "********"
    return value


def create_app(store: GluonStore | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        store: Optional GluonStore instance. If not provided, creates a new one.
               Useful for testing with custom store configurations.
    """
    app = FastAPI(
        title="Gluon Web Dashboard",
        description="Web interface for managing Gluon Agent task execution",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Shared store — created early because the auth middleware below closes over it.
    if store is None:
        store = GluonStore()

    # ---- Middleware (added innermost-first; CORS ends up outermost) ----
    #
    # The auth gate is FAIL-CLOSED: with GLUON_AUTH_ENABLED=true, any request
    # outside the explicit anonymous allowlist must carry a valid session, and
    # mutations require at least the operator role. A newly-added route is
    # therefore protected by default. When auth is disabled the gate is a no-op,
    # so single-user mode is unchanged. RBAC granularity above "operator on
    # mutations" (e.g. admin-only settings/webhooks/env-vars) is layered on with
    # per-route `Depends(require_admin)`.
    import re as _re

    from fastapi.responses import JSONResponse

    from gluon.auth import (
        SESSION_COOKIE_NAME,
        _current_user_impl,
        _role_rank,
        is_auth_enabled,
    )
    from gluon.models import UserRole as _UserRole

    # Reachable without authentication. Everything outside /api/ is the SPA
    # (static assets + client-side routes) and is also anonymous.
    _anon_api_paths = {
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",  # identity/status probe — returns SYSTEM_USER when unauthenticated
        "/api/auth/providers",
        "/api/auth/oidc/login",
        "/api/auth/oidc/callback",
        "/api/version",
        "/api/webhooks/github",  # authenticated by HMAC signature, not session
    }
    # Mutations any authenticated user may perform on their own account / UI state.
    _viewer_mutation_patterns = [
        _re.compile(r"^/api/auth/"),  # link own chat account / unlink
        _re.compile(r"^/api/users/[^/]+/password$"),  # change own password
        _re.compile(r"^/api/notifications(/|$)"),  # own notification state
    ]
    _mutating_methods = {"POST", "PUT", "DELETE", "PATCH"}

    def _is_anonymous_path(path: str) -> bool:
        if not path.startswith("/api/"):
            return True
        return path in _anon_api_paths

    def _required_role(method: str, path: str) -> _UserRole:
        if any(p.match(path) for p in _viewer_mutation_patterns):
            return _UserRole.VIEWER
        if method in _mutating_methods:
            return _UserRole.OPERATOR
        return _UserRole.VIEWER

    @app.middleware("http")
    async def _auth_gate(request, call_next):
        # CORS preflight is handled by the outer CORS middleware; single-user
        # mode skips auth entirely.
        if request.method == "OPTIONS" or not is_auth_enabled():
            return await call_next(request)
        path = request.url.path
        if _is_anonymous_path(path):
            return await call_next(request)
        session = request.cookies.get(SESSION_COOKIE_NAME)
        try:
            user = _current_user_impl(store, session)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        required = _required_role(request.method, path)
        if _role_rank(user.role) < _role_rank(required):
            return JSONResponse(
                {"detail": f"role '{required.value}' required (you are '{user.role.value}')"},
                status_code=403,
            )
        request.state.current_user = user
        return await call_next(request)

    # Starlette SessionMiddleware — needed by Authlib's OIDC client to carry
    # state + nonce between the redirect and callback. Mounted unconditionally
    # because the cost is tiny (a single signed cookie) and it's harmless
    # when OIDC isn't configured. Secret is read from env so multi-replica
    # deployments share state; falls back to a per-process random secret in
    # dev (sessions don't survive restarts but that's fine for dev).
    from secrets import token_urlsafe

    from starlette.middleware.sessions import SessionMiddleware  # type: ignore[import-untyped]

    _oidc_session_secret = os.environ.get("GLUON_OIDC_SESSION_SECRET") or token_urlsafe(32)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_oidc_session_secret,
        max_age=600,
        same_site="lax",
        https_only=False,  # auto-elevated to true on https requests by Starlette
    )

    # CORS — added last so it is the OUTERMOST middleware, ensuring even the auth
    # gate's 401/403 responses carry CORS headers (so the browser can read them).
    # Origins are an explicit allowlist (default the local dashboard); credentials
    # stay enabled so a separately-hosted front-end origin can send the cookie.
    _allowed_origins = [
        o.strip() for o in os.environ.get("GLUON_ALLOWED_ORIGINS", "http://localhost:45866").split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    notifier = NotificationDispatcher(store=store)
    orchestrator = Orchestrator(store=store, notifier=notifier)
    runner = TaskRunner(store=store, notifier=notifier)

    # Store notifier on app.state so transports can register later
    app.state.notifier = notifier

    # Auth dependency factories (D5 Phase 2) — defined up here because
    # endpoints throughout the file reference them in `Depends(...)` at
    # registration time. The auth endpoints themselves live further down in
    # their own section; these are just the reusable injectors.
    from gluon.auth import (  # noqa: E402 — intentional late import to avoid cycles
        SYSTEM_USER,
        make_current_user_dependency,
        make_require_role,
    )
    from gluon.models import User as UserModel  # noqa: E402
    from gluon.models import UserRole  # noqa: E402

    current_user_dep = make_current_user_dependency(store)
    require_admin = make_require_role(store, UserRole.ADMIN)

    # Build project lookup helper
    def get_project_lookup() -> dict[str, str]:
        """Build project_id → project_name lookup."""
        return {p.id: p.name for p in store.list_projects()}

    def run_to_response(run, project_lookup: dict[str, str]) -> RunResponse:
        """Convert ExecutionRun to RunResponse."""
        # For ralph-enabled runs, compute cost from iterations (accurate total)
        cost = run.cost_usd
        if run.ralph_enabled and run.loop_count > 0:
            cost = store.get_ralph_total_cost(run.id)

        # Get latest witness health classification for running runs
        health_classification = None
        if run.status.value == "running":
            try:
                decision = store.get_latest_witness_decision(run.id)
                if decision:
                    health_classification = decision.classification
            except Exception:
                pass

        # Look up chain/step info for formula runs
        chain_id_val = run.chain_id
        chain_step_name = None
        chain_step_index = None
        chain_total_steps = None
        if chain_id_val:
            try:
                chain = store.get_chain(chain_id_val)
                if chain:
                    chain_total_steps = len(chain.steps)
                    for i, s in enumerate(chain.steps):
                        if s.run_id == run.id or s.status.value == "running":
                            chain_step_name = s.name
                            chain_step_index = i
                    if chain_step_name is None and run.metadata:
                        chain_step_name = run.metadata.get("step_name")
            except Exception:
                pass

        return RunResponse(
            id=run.id,
            project_id=run.project_id,
            project_name=project_lookup.get(run.project_id, run.project_id[:8]),
            status=run.status.value,
            prompt=run.prompt,
            original_prompt=run.original_prompt,
            initiator=run.initiator,
            user_id=run.user_id,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_seconds=run.duration_seconds,
            error_message=run.error_message,
            cost_usd=cost,
            # Git/PR fields for Kanban routing
            use_worktree=run.use_worktree,
            branch_name=run.branch_name,
            pr_number=run.pr_number,
            pr_url=run.pr_url,
            pr_status=run.pr_status,
            pr_mergeable=run.pr_mergeable,
            ci_status=run.ci_status,
            # Archive tracking
            archived=run.archived,
            # Recovery progress UI
            is_recovering=run.is_recovering,
            recovery_item_count=run.recovery_item_count,
            # Ralph Loop fields
            ralph_enabled=run.ralph_enabled,
            loop_count=run.loop_count,
            max_loops=run.max_loops,
            circuit_state=run.circuit_state.value if run.circuit_state else "CLOSED",
            completion_confidence=run.completion_confidence,
            completion_reason=run.completion_reason,
            calls_this_hour=run.calls_this_hour,
            max_calls_per_hour=run.max_calls_per_hour,
            health_classification=health_classification,
            # Chain/formula step progress
            chain_id=chain_id_val,
            chain_step_name=chain_step_name,
            chain_step_index=chain_step_index,
            chain_total_steps=chain_total_steps,
            stop_reason=run.metadata.get("stop_reason") if run.metadata else None,
            # List-view cockpit fields
            custom_title=run.custom_title,
            kind=run.kind,
            snoozed_until=run.snoozed_until,
            last_activity_at=run.last_activity_at,
            forked_from_run_id=run.forked_from_run_id,
            # Scheduled-task linkage
            schedule_id=run.schedule_id,
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
        await asyncio.to_thread(runner.refresh_all_runs)

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
            await asyncio.to_thread(runner.refresh_run_status, run)

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
            working_path = (
                Path(run.worktree_path)
                if run.worktree_path and Path(run.worktree_path).exists()
                else project.expanded_path
            )
            base_branch = run.source_branch or "main"
            try:
                commit_count = await git_manager_temp.get_commit_count(working_path, run.branch_name, base_branch)
                file_count = await git_manager_temp.get_file_count(working_path, run.branch_name, base_branch)
            except Exception:
                pass  # Counts are optional, don't fail request

        # For ralph-enabled runs, compute cost from iterations (accurate total)
        cost = run.cost_usd
        if run.ralph_enabled and run.loop_count > 0:
            cost = store.get_ralph_total_cost(run.id)

        return RunDetailResponse(
            id=run.id,
            project_id=run.project_id,
            project_name=project_lookup.get(run.project_id, run.project_id[:8]),
            status=run.status.value,
            prompt=run.prompt,
            original_prompt=run.original_prompt,
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
            cost_usd=cost,
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
            # Ralph Loop fields
            ralph_enabled=run.ralph_enabled,
            loop_count=run.loop_count,
            max_loops=run.max_loops,
            circuit_state=run.circuit_state.value if run.circuit_state else "CLOSED",
            completion_confidence=run.completion_confidence,
            completion_reason=run.completion_reason,
            calls_this_hour=run.calls_this_hour,
            max_calls_per_hour=run.max_calls_per_hour,
            # Ralph Loop detail fields
            consecutive_no_progress=run.consecutive_no_progress,
            consecutive_same_error=run.consecutive_same_error,
            test_only_loops=run.test_only_loops,
            max_cost_usd=run.max_cost_usd,
            # SDK stop reason
            stop_reason=run.metadata.get("stop_reason") if run.metadata else None,
            # Queued messages
            queued_messages=[
                QueuedMessageResponse(
                    id=m.id,
                    message=m.message,
                    queued_at=m.queued_at.isoformat(),
                )
                for m in run.queued_messages
            ],
            # Hard caps (Theme D3)
            max_tool_calls=run.max_tool_calls,
            max_duration_minutes=run.max_duration_minutes,
            tool_call_count=run.tool_call_count,
            # List-view cockpit fields
            custom_title=run.custom_title,
            kind=run.kind,
            snoozed_until=run.snoozed_until,
            last_activity_at=run.last_activity_at,
            forked_from_run_id=run.forked_from_run_id,
        )

    @app.post("/api/runs", response_model=RunResponse)
    async def create_run(
        body: CreateRunRequest,
        user: UserModel = Depends(current_user_dep),  # type: ignore[arg-type]
    ) -> RunResponse:
        """Create and start a new execution run.

        D5 Phase 2 attribution: the created run's ``user_id`` is set to the
        current user's ID when auth is enabled, or None for SYSTEM_USER.
        """
        from gluon.core import (
            AgentAmbiguousError,
            AgentNotFoundError,
            BudgetExceededError,
            WorkspaceBudgetExceededError,
        )

        # Only attribute when a real (non-SYSTEM) user is logged in. SYSTEM_USER
        # has a deterministic zero-UUID but we don't want to pollute rows with it.
        attribution_user_id = user.id if user.id != SYSTEM_USER.id else None

        try:
            project = orchestrator.get_project(body.project_name)
        except ProjectNotFoundError:
            raise HTTPException(status_code=404, detail=f"Project not found: {body.project_name}")

        # Resolve agent — explicit reference or auto-select
        try:
            resolved_agent_id = orchestrator.resolve_agent(body.agent, project.workspace_id)
        except AgentNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        except AgentAmbiguousError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

        # Create the run (budget enforcement happens inside runner.submit)
        try:
            run = await runner.submit(
                project_id=project.id,
                prompt=body.prompt,
                wait=False,
                use_worktree=body.use_worktree,
                initiator="web:dashboard",
                model=body.model_override or body.model,  # Override takes precedence
                # Task profile options
                profile=body.profile,
                thinking_budget=body.thinking_override,
                force_planning=body.force_planning,
                effort=body.effort_override,
                task_budget=body.task_budget_override,
                # Ralph Loop options
                ralph_enabled=body.ralph_enabled,
                max_loops=body.max_loops,
                max_cost_usd=body.max_budget_override or body.max_cost_usd,  # Override takes precedence
                # Per-task overrides
                agent_teams=body.agent_teams,
                model_transition=body.model_transition,
                enable_prehydration=body.enable_prehydration,
                blueprint_enabled=body.blueprint_enabled,
                agent_id=resolved_agent_id,
                # Hard caps (Theme D3)
                max_tool_calls=body.max_tool_calls,
                max_duration_minutes=body.max_duration_minutes,
                # D5 Phase 2 — attribution
                user_id=attribution_user_id,
            )
        except BudgetExceededError as e:
            raise HTTPException(status_code=402, detail=str(e)) from None
        except WorkspaceBudgetExceededError as e:
            raise HTTPException(status_code=402, detail=str(e)) from None

        # Store dev_port in metadata if provided
        if body.dev_port is not None:
            if run.metadata is None:
                run.metadata = {}
            run.metadata["dev_port"] = body.dev_port
            store.update_run(run)

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

        try:
            success = await runner.cancel(run.id)
        except Exception as e:
            logger.error("Error cancelling run %s: %s", run.id[:8], e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"An error occurred while cancelling run {run.id[:8]}; check server logs.",
            ) from e
        if not success:
            # cancel() returns False when the run is no longer cancellable (e.g.
            # it just finished or was never running in this process).
            raise HTTPException(
                status_code=409,
                detail=f"Run {run.id[:8]} could not be cancelled (it may have already finished).",
            )

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

    @app.post("/api/runs/{run_id}/queue-followup", response_model=QueueFollowupResponse)
    async def queue_followup(run_id: str, body: QueueFollowupRequest) -> QueueFollowupResponse:
        """
        Queue a follow-up message for a running task.

        If the task is running/pending, the message is appended to the queue
        and will auto-resume after the task completes.

        If the task is not running, returns action="resume_now" to indicate
        the caller should use the normal resume endpoint instead.
        """
        from gluon.models import QueuedMessage

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Check if task is currently running
        if run.status not in (RunStatus.RUNNING, RunStatus.PENDING):
            # Not running - caller should use normal resume
            return QueueFollowupResponse(
                run_id=run.id,
                action="resume_now",
                message=None,
            )

        # Append message to the queue
        try:
            queued_msg = QueuedMessage(message=body.message)
            run.queued_messages.append(queued_msg)
            store.update_run(run)
        except Exception as e:
            logger.exception(f"Failed to queue message for run {run_id}")
            raise HTTPException(status_code=500, detail=f"Failed to queue message: {e!s}")

        # Broadcast update to WebSocket clients
        try:
            project_lookup = get_project_lookup()
            project_name = project_lookup.get(run.project_id) or "Unknown"
            await ws_manager.broadcast_run_update(run, project_name)
        except Exception as e:
            # Don't fail the request - message was queued successfully
            logger.warning(f"Failed to broadcast queue update for run {run_id}: {e}")

        return QueueFollowupResponse(
            run_id=run.id,
            action="queued",
            message=body.message,
            message_id=queued_msg.id,
        )

    @app.put("/api/runs/{run_id}/queue/{message_id}")
    async def edit_queued_message(
        run_id: str, message_id: str, body: EditQueuedMessageRequest
    ) -> QueuedMessageResponse:
        """Edit a queued message by its ID."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Find and update the message
        for msg in run.queued_messages:
            if msg.id == message_id:
                msg.message = body.message
                store.update_run(run)

                # Broadcast update
                project_lookup = get_project_lookup()
                project_name = project_lookup.get(run.project_id) or "Unknown"
                await ws_manager.broadcast_run_update(run, project_name)

                return QueuedMessageResponse(
                    id=msg.id,
                    message=msg.message,
                    queued_at=msg.queued_at.isoformat(),
                )

        raise HTTPException(status_code=404, detail=f"Queued message not found: {message_id}")

    @app.delete("/api/runs/{run_id}/queue/{message_id}")
    async def delete_queued_message(run_id: str, message_id: str) -> dict:
        """Delete a queued message by its ID."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Find and remove the message
        original_len = len(run.queued_messages)
        run.queued_messages = [m for m in run.queued_messages if m.id != message_id]

        if len(run.queued_messages) == original_len:
            raise HTTPException(status_code=404, detail=f"Queued message not found: {message_id}")

        store.update_run(run)

        # Broadcast update
        project_lookup = get_project_lookup()
        project_name = project_lookup.get(run.project_id) or "Unknown"
        await ws_manager.broadcast_run_update(run, project_name)

        return {"deleted": True, "message_id": message_id}

    @app.delete("/api/runs/{run_id}/queue")
    async def clear_queue(run_id: str) -> dict:
        """Clear all queued messages for a run."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        cleared_count = len(run.queued_messages)
        run.queued_messages = []
        store.update_run(run)

        # Broadcast update
        project_lookup = get_project_lookup()
        project_name = project_lookup.get(run.project_id) or "Unknown"
        await ws_manager.broadcast_run_update(run, project_name)

        return {"cleared": True, "count": cleared_count}

    @app.post("/api/runs/{run_id}/recover", response_model=RecoverRunResponse)
    async def recover_run(run_id: str, body: RecoverRunRequest | None = None) -> RecoverRunResponse:
        """
        Recover a failed run (typically from context overflow).

        This extracts recovery state from the failed run and starts a fresh
        session with a summary of completed work. Unlike resume, this does not
        reuse the Claude session - it starts fresh with context about what was
        already done.

        Args:
            run_id: ID of the run to recover (supports short IDs)
            body: Optional request body with recovery options
        """
        from gluon.agent import GluonAgent
        from gluon.models import utc_now

        # Get the run to recover
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Get the project for this run
        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(
                status_code=400,
                detail="Could not find project for this run",
            )

        # Extract recovery state
        recovery_state = runner._extract_recovery_state(run)
        completed_work = recovery_state.get("completed_work", [])

        # Determine whether to create fresh run or recover in-place
        fresh = body.fresh if body else False

        if fresh:
            # Create a new run linked to the failed one
            new_run = store.create_run(
                project_id=run.project_id,
                prompt=f"[Recovery from {run.id[:8]}] {run.prompt}",
                initiator="web:recover",
                use_worktree=run.use_worktree,
                model=run.model,
            )
            new_run.recovery_from_run_id = run.id
            new_run.recovery_count = 1
            new_run.last_recovery_at = utc_now()
            store.update_run(new_run)
            target_run = new_run
        else:
            # Recover in-place
            run.recovery_count += 1
            run.last_recovery_at = utc_now()
            run.status = RunStatus.RUNNING
            store.update_run(run)
            target_run = run

        # Determine working directory
        if run.worktree_path and Path(run.worktree_path).exists():
            working_dir = Path(run.worktree_path)
        else:
            working_dir = project.expanded_path

        # Verify working directory exists
        if not working_dir.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Working directory does not exist: {working_dir}. The worktree may have been deleted.",
            )

        # Start recovery in background
        async def _run_recovery():
            try:
                print(f"[RECOVERY] Starting recovery for run {target_run.id} in {working_dir}", flush=True)
                logger.info(f"Starting recovery for run {target_run.id} in {working_dir}")

                # Set recovering flag at start
                target_run.is_recovering = True
                target_run.status = RunStatus.RUNNING
                target_run.recovery_item_count = 0
                store.update_run(target_run)

                # Broadcast initial recovery state
                project_lookup = get_project_lookup()
                project_name = project_lookup.get(target_run.project_id, target_run.project_id[:8])
                await ws_manager.broadcast_run_update(target_run, project_name)

                agent = GluonAgent(model=run.model) if run.model else GluonAgent()
                result = None
                item_count = 0

                print("[RECOVERY] About to iterate agent.resume_with_fresh_context", flush=True)
                async for item in agent.resume_with_fresh_context(
                    recovery_state=recovery_state,
                    working_dir=working_dir,
                ):
                    item_count += 1
                    print(f"[RECOVERY] Item {item_count}: {type(item).__name__}", flush=True)
                    from gluon.agent import AgentResult

                    if isinstance(item, AgentResult):
                        print(f"[RECOVERY] Got AgentResult: success={item.success}", flush=True)
                        result = item
                    else:
                        # Broadcast progress every 5 items
                        if item_count % 5 == 0:
                            target_run.recovery_item_count = item_count
                            store.update_run(target_run)
                            await ws_manager.broadcast_run_update(target_run, project_name)

                print(f"[RECOVERY] Finished iteration, got {item_count} items", flush=True)

                # Clear recovering flag at end
                target_run.is_recovering = False
                target_run.recovery_item_count = 0

                # Update run with result
                if result:
                    if result.claude_session_id:
                        target_run.claude_session_id = result.claude_session_id
                    target_run.cost_usd = (target_run.cost_usd or 0) + (result.total_cost_usd or 0)
                    target_run.input_tokens = (target_run.input_tokens or 0) + (result.input_tokens or 0)
                    target_run.output_tokens = (target_run.output_tokens or 0) + (result.output_tokens or 0)
                    target_run.model_used = result.model_used

                    if result.success:
                        target_run.status = RunStatus.REVIEW
                        print(f"[RECOVERY] Completed successfully for run {target_run.id}", flush=True)
                        logger.info(f"Recovery completed successfully for run {target_run.id}")
                    else:
                        target_run.status = RunStatus.FAILED
                        target_run.error_message = result.error
                        print(f"[RECOVERY] Failed for run {target_run.id}: {result.error}", flush=True)
                        logger.warning(f"Recovery failed for run {target_run.id}: {result.error}")
                else:
                    target_run.status = RunStatus.FAILED
                    target_run.error_message = "Recovery produced no result"
                    print(f"[RECOVERY] No result for run {target_run.id}", flush=True)
                    logger.error(f"Recovery for run {target_run.id} produced no AgentResult")

                store.update_run(target_run)

                # Broadcast final update
                await ws_manager.broadcast_run_update(target_run, project_name)

            except Exception as e:
                print(f"[RECOVERY] Exception for run {target_run.id}: {e}", flush=True)
                import traceback

                traceback.print_exc()
                logger.exception(f"Recovery failed for run {target_run.id}: {e}")
                target_run.is_recovering = False
                target_run.recovery_item_count = 0
                target_run.status = RunStatus.FAILED
                target_run.error_message = f"Recovery failed: {e}"
                store.update_run(target_run)

                # Broadcast failure
                project_lookup = get_project_lookup()
                project_name = project_lookup.get(target_run.project_id, target_run.project_id[:8])
                await ws_manager.broadcast_run_update(target_run, project_name)

        # Schedule recovery to run in background
        _recovery_task = asyncio.create_task(_run_recovery())
        _background_tasks.add(_recovery_task)
        _recovery_task.add_done_callback(_background_tasks.discard)

        # Broadcast initial update
        project_lookup = get_project_lookup()
        project_name = project_lookup.get(target_run.project_id, target_run.project_id[:8])
        if fresh:
            await ws_manager.broadcast_run_created(target_run, project_name)
        else:
            await ws_manager.broadcast_run_update(target_run, project_name)

        return RecoverRunResponse(
            run_id=target_run.id,
            status=target_run.status.value,
            recovery_count=target_run.recovery_count,
            is_fresh=fresh,
            completed_work=completed_work,
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

    # ========== AskUserQuestion Endpoints ==========

    def _question_to_response(q) -> PendingQuestionResponse:
        """Convert PendingQuestion to API response."""
        return PendingQuestionResponse(
            id=q.id,
            run_id=q.run_id,
            question_index=q.question_index,
            question_text=q.question_text,
            header=q.header,
            options=q.options,
            multi_select=q.multi_select,
            status=q.status.value,
            created_at=q.created_at.isoformat(),
            expires_at=q.expires_at.isoformat() if q.expires_at else None,
            selected_labels=q.selected_labels,
            answer_source=q.answer_source,
        )

    @app.get("/api/runs/{run_id}/questions", response_model=PendingQuestionsResponse)
    async def get_run_questions(run_id: str) -> PendingQuestionsResponse:
        """
        Get all questions for a run.

        Returns both pending and answered questions for the run.
        """
        from gluon.models import QuestionStatus

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        questions = store.list_pending_questions(run.id)
        has_pending = any(q.status == QuestionStatus.PENDING for q in questions)

        return PendingQuestionsResponse(
            run_id=run.id,
            questions=[_question_to_response(q) for q in questions],
            has_pending=has_pending,
        )

    @app.post("/api/questions/{question_id}/answer", response_model=PendingQuestionResponse)
    async def answer_question(question_id: str, body: AnswerQuestionRequest) -> PendingQuestionResponse:
        """
        Submit an answer to a pending question.

        The answer must contain at least one selected label from the question's options.
        """
        from gluon.models import QuestionStatus, utc_now

        question = store.get_pending_question(question_id)
        if not question:
            raise HTTPException(status_code=404, detail=f"Question not found: {question_id}")

        if question.status != QuestionStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Question already answered (status: {question.status.value})",
            )

        # Validate at least one selection
        if not body.selected_labels:
            raise HTTPException(status_code=400, detail="At least one option must be selected")

        # Update the question
        question.status = QuestionStatus.ANSWERED
        question.selected_labels = body.selected_labels
        question.answer_source = "user"
        question.answered_at = utc_now()
        store.update_pending_question(question)

        # Emit question.answered event (subscribers handle WebSocket broadcast)
        try:
            from gluon.events import event_bus
            from gluon.events.types import EventCategory, GluonEvent

            await event_bus.emit(
                GluonEvent(
                    type="question.answered",
                    category=EventCategory.INTERACTION,
                    run_id=question.run_id,
                    data={"question_id": question_id, "selected_labels": body.selected_labels},
                )
            )
        except ImportError:
            await ws_manager.broadcast_question_answered(question.run_id, question_id)

        return _question_to_response(question)

    # ========== Todo Tracking Endpoints ==========

    @app.get("/api/runs/{run_id}/todos", response_model=RunTodosResponse)
    async def get_run_todos(run_id: str) -> RunTodosResponse:
        """
        Get the latest todo tracking state for a run.

        Returns the most recent TodoWrite snapshot captured by the PostToolUse mirror hook.
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        snapshot = store.get_latest_todo_snapshot(run.id)
        if snapshot is None:
            return RunTodosResponse(run_id=run.id)

        return RunTodosResponse(
            run_id=run.id,
            todos=[
                TodoItemResponse(
                    content=t.get("content", ""),
                    status=t.get("status", "pending"),
                    active_form=t.get("activeForm", ""),
                )
                for t in snapshot.todos
            ],
            todo_count=snapshot.todo_count,
            completed_count=snapshot.completed_count,
            in_progress_count=snapshot.in_progress_count,
            pending_count=snapshot.pending_count,
            captured_at=snapshot.captured_at.isoformat(),
        )

    # ========== Ralph Loop Endpoints ==========

    @app.get("/api/runs/{run_id}/iterations", response_model=RalphIterationsResponse)
    async def get_ralph_iterations(run_id: str, limit: int = 50) -> RalphIterationsResponse:
        """
        Get iteration history for a ralph-enabled run.

        Returns a list of all loop iterations with metrics for each.
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.ralph_enabled:
            raise HTTPException(
                status_code=400,
                detail="Run is not a ralph-enabled run",
            )

        iterations = store.list_ralph_iterations(run.id, limit=limit)

        return RalphIterationsResponse(
            run_id=run.id,
            iteration_count=len(iterations),
            iterations=[
                RalphIterationResponse(
                    id=it.id,
                    run_id=it.run_id,
                    loop_number=it.loop_number,
                    started_at=it.started_at.isoformat() if it.started_at else "",
                    ended_at=it.ended_at.isoformat() if it.ended_at else None,
                    duration_seconds=(
                        (it.ended_at - it.started_at).total_seconds() if it.started_at and it.ended_at else None
                    ),
                    files_changed=it.files_changed,
                    progress_detected=it.progress_detected,
                    has_errors=it.has_errors,
                    error_message=it.error_summary,  # Model uses error_summary
                    has_completion_signal=it.has_completion_signal,
                    is_test_only=it.is_test_only,
                    confidence_score=it.confidence_score,
                    cost_usd=it.cost_usd,
                    input_tokens=it.tokens_used,  # Model has tokens_used (combined)
                    output_tokens=0,  # Not tracked separately in model
                )
                for it in iterations
            ],
        )

    @app.post("/api/runs/{run_id}/stop-loop", response_model=StopLoopResponse)
    async def stop_ralph_loop(run_id: str) -> StopLoopResponse:
        """
        Stop a ralph loop early.

        This gracefully terminates the loop and moves the run to REVIEW status.
        Only works for ralph-enabled runs that are currently running.
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.ralph_enabled:
            raise HTTPException(
                status_code=400,
                detail="Run is not a ralph-enabled run",
            )

        if run.status != RunStatus.RUNNING:
            raise HTTPException(
                status_code=400,
                detail=f"Run is not running (current status: {run.status.value})",
            )

        # Set completion reason and move to REVIEW
        run.completion_reason = "User requested stop"
        run.status = RunStatus.REVIEW
        store.update_run(run)

        # Broadcast update to WebSocket clients
        project_lookup = get_project_lookup()
        project_name = project_lookup.get(run.project_id, run.project_id[:8])
        await ws_manager.broadcast_run_update(run, project_name)

        return StopLoopResponse(
            success=True,
            run_id=run.id,
            message=f"Ralph loop stopped at iteration {run.loop_count}",
            final_loop_count=run.loop_count,
        )

    # ========== Supervision Endpoints ==========

    @app.get("/api/runs/{run_id}/supervision", response_model=SupervisionStatusResponse)
    async def get_supervision_status(run_id: str) -> SupervisionStatusResponse:
        """Get supervision status for a run."""
        from gluon.policies import get_supervision_config

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

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

    @app.post("/api/runs/{run_id}/supervision/evaluate", response_model=SupervisionEvaluateResponse)
    async def evaluate_supervision(run_id: str) -> SupervisionEvaluateResponse:
        """Manually trigger supervision evaluation for a run."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        result = await runner.evaluate_supervision(run.id)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to evaluate supervision")

        return SupervisionEvaluateResponse(
            run_id=run.id,
            decision=result["decision"],
            reason=result["reason"],
            wait_seconds=result.get("wait_seconds", 0),
        )

    @app.post("/api/runs/{run_id}/supervision/disable", response_model=SupervisionStatusResponse)
    async def disable_supervision(run_id: str, request: SupervisionDisableRequest) -> SupervisionStatusResponse:
        """Disable supervision for a run."""
        from gluon.policies import get_supervision_config
        from gluon.resume_coordinator import ResumeCoordinator

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        coordinator = ResumeCoordinator(store=store, runner=runner)
        success = await coordinator.disable_supervision(run.id, request.reason)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to disable supervision")

        # Refresh run and config
        run = store.get_run(run.id)
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

    # ========== Git Commits and Files ==========

    @app.get("/api/runs/{run_id}/commits", response_model=RunCommitsResponse)
    async def get_run_commits(run_id: str) -> RunCommitsResponse:
        """
        Get commits on the run's branch since it diverged from the base branch.

        Falls back to snapshots if the branch has been merged or deleted.
        """
        from gluon.git_manager import GitManager

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        base_branch = run.source_branch or "main"

        # Priority 1: Check for snapshots (work after branch merge/deletion)
        if store.has_commit_snapshots(run.id):
            snapshots = store.get_commit_snapshots(run.id)
            return RunCommitsResponse(
                run_id=run.id,
                branch_name=run.branch_name,
                base_branch=base_branch,
                commit_count=len(snapshots),
                commits=[
                    CommitResponse(
                        sha=s.sha,
                        message=s.message,
                        author=s.author,
                        author_email=s.author_email or "",
                        date=s.date.isoformat(),
                    )
                    for s in snapshots
                ],
                from_snapshot=True,
            )

        # Priority 2: Live git query (original behavior)
        if not run.branch_name:
            return RunCommitsResponse(
                run_id=run.id,
                branch_name=None,
                base_branch=base_branch,
                commit_count=0,
                commits=[],
            )

        # Get project to find repo path
        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Determine working path (worktree or project root)
        working_path = (
            Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path
        )

        # Fetch commits from git
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
            from_snapshot=False,
        )

    @app.get("/api/runs/{run_id}/files", response_model=RunFilesResponse)
    async def get_run_files(run_id: str) -> RunFilesResponse:
        """
        Get files changed on the run's branch since it diverged from the base branch.

        Falls back to snapshots if the branch has been merged or deleted.
        """
        from gluon.git_manager import GitManager

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        base_branch = run.source_branch or "main"

        # Priority 1: Check for snapshots (work after branch merge/deletion)
        if store.has_file_change_snapshots(run.id):
            snapshots = store.get_file_change_snapshots(run.id)
            total_additions = sum(s.additions for s in snapshots)
            total_deletions = sum(s.deletions for s in snapshots)
            return RunFilesResponse(
                run_id=run.id,
                branch_name=run.branch_name,
                base_branch=base_branch,
                file_count=len(snapshots),
                total_additions=total_additions,
                total_deletions=total_deletions,
                files=[
                    FileChangeResponse(
                        file_path=s.file_path,
                        additions=s.additions,
                        deletions=s.deletions,
                        change_type=s.change_type,
                    )
                    for s in snapshots
                ],
                from_snapshot=True,
            )

        # Priority 2: Live git query (original behavior)
        if not run.branch_name:
            return RunFilesResponse(
                run_id=run.id,
                branch_name=None,
                base_branch=base_branch,
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
        working_path = (
            Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path
        )

        # Fetch file changes from git
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
            from_snapshot=False,
        )

    @app.get("/api/runs/{run_id}/commits/{sha}", response_model=CommitDetailResponse)
    async def get_commit_detail(run_id: str, sha: str) -> CommitDetailResponse:
        """
        Get detailed information for a specific commit including files changed.

        Falls back to snapshots if the branch has been merged or deleted.
        """
        from gluon.git_manager import GitManager

        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Priority 1: Check for snapshots
        if store.has_commit_snapshots(run.id):
            commit_snapshots = store.get_commit_snapshots(run.id)
            # Find matching commit by SHA
            matching = [c for c in commit_snapshots if c.sha == sha or c.sha.startswith(sha)]
            if matching:
                commit_snap = matching[0]
                # Get per-commit file changes from commit_file_snapshots
                file_snapshots = store.get_commit_file_snapshots(commit_snap.id)
                return CommitDetailResponse(
                    sha=commit_snap.sha,
                    message=commit_snap.full_message or commit_snap.message,
                    author=commit_snap.author,
                    author_email=commit_snap.author_email or "",
                    date=commit_snap.date.isoformat(),
                    files=[
                        FileChangeResponse(
                            file_path=f.file_path,
                            additions=f.additions,
                            deletions=f.deletions,
                            change_type=f.change_type,
                        )
                        for f in file_snapshots
                    ],
                    from_snapshot=True,
                )

        # Priority 2: Live git query (original behavior)
        if not run.branch_name:
            raise HTTPException(status_code=400, detail="Run has no branch")

        # Get project to find repo path
        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Determine working path (worktree or project root)
        working_path = (
            Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path
        )

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
            from_snapshot=False,
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
        working_path = (
            Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path
        )

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
        """List all registered projects with git sync status."""
        from gluon.git_manager import GitManager

        projects = orchestrator.list_projects()
        result = []

        # Use git manager for cached status if available
        project_git_manager = GitManager(store)

        for project in projects:
            sessions = orchestrator.list_sessions(project.name)
            # Use expanded_path for git commands (resolves ${HOME}, ~, etc.)
            expanded = project.expanded_path

            # Get basic git info — off-loop so the per-project subprocesses don't
            # block the event loop while iterating every project.
            git_branch = await asyncio.to_thread(_get_git_branch, expanded)
            git_ahead, git_behind = await asyncio.to_thread(_get_git_ahead_behind, expanded)

            # Get extended git status from cache (no network operations)
            git_uncommitted_count = None
            git_has_remote = False
            git_has_conflicts = False
            git_has_operation_in_progress = False
            can_sync = False
            sync_action = None

            cached_status = project_git_manager.get_cached_status(project)
            if cached_status and cached_status.is_git_repo:
                git_uncommitted_count = cached_status.uncommitted_count
                git_has_remote = cached_status.remote is not None
                git_has_conflicts = cached_status.has_conflicts
                git_has_operation_in_progress = (
                    cached_status.is_rebase_in_progress or cached_status.is_merge_in_progress
                )

                # Compute sync action
                if git_has_conflicts or git_has_operation_in_progress:
                    can_sync = False
                    sync_action = None
                elif cached_status.commits_ahead > 0 and cached_status.commits_behind > 0:
                    can_sync = False
                    sync_action = "diverged"
                elif cached_status.commits_behind > 0:
                    can_sync = cached_status.uncommitted_count == 0
                    sync_action = "pull"
                elif cached_status.commits_ahead > 0:
                    can_sync = True
                    sync_action = "push"
                elif cached_status.uncommitted_count > 0:
                    can_sync = True
                    sync_action = "commit+push"
                else:
                    can_sync = False
                    sync_action = None  # Already synced

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
                    git_uncommitted_count=git_uncommitted_count,
                    git_has_remote=git_has_remote,
                    git_has_conflicts=git_has_conflicts,
                    git_has_operation_in_progress=git_has_operation_in_progress,
                    can_sync=can_sync,
                    sync_action=sync_action,
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

    # ========== LLM Provider ==========

    @app.get("/api/provider", response_model=ProviderResponse)
    async def get_provider_info() -> ProviderResponse:
        """Get current LLM provider configuration and model mappings."""
        from gluon.llm_provider import get_provider, get_provider_source

        provider = get_provider()
        return ProviderResponse(
            provider=provider.__class__.__name__.replace("Provider", "").lower(),
            name=provider.name,
            supports_cost_tracking=provider.supports_cost_tracking,
            source=get_provider_source(),
            models={tier.value: model_id for tier, model_id in provider.MODELS.items()},
        )

    # ========== Auth (D5 Phase 2) ==========
    #
    # Session-cookie based auth. When GLUON_AUTH_ENABLED=false (default), the
    # /api/auth/me endpoint still works and returns the SYSTEM_USER so clients
    # have a uniform shape. Login/logout are always defined but only do real
    # work when auth is enabled.
    #
    # Note: `current_user_dep` and `require_admin` were already constructed
    # at the top of `create_app` so endpoints defined before this section
    # (like POST /api/runs) can reference them in their Depends() defaults.

    from gluon.auth import (
        DEFAULT_SESSION_TTL_DAYS,
        InvalidCredentialsError,
        UserDisabledError,
        create_session_for_user,
        get_auth_provider,
        get_local_provider,
        get_oidc_provider,
    )

    def _user_to_response(u: UserModel) -> UserResponse:
        return UserResponse(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            email=u.email,
            role=u.role.value,
            auth_provider=u.auth_provider.value,
            disabled=u.disabled,
            telegram_user_id=u.telegram_user_id,
            discord_user_id=u.discord_user_id,
            created_at=u.created_at.isoformat(),
            last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
        )

    @app.post("/api/auth/login", response_model=LoginResponse)
    async def auth_login(
        body: LoginRequest,
        request: Request,
        response: Response,
    ) -> LoginResponse:
        """Authenticate a username + password and set the session cookie.

        Returns 400 if auth is disabled (the endpoint exists so clients can
        detect the mode, but using it is meaningless in single-user).
        Returns 401 on bad credentials or a disabled user — the two are
        deliberately indistinguishable to callers (prevents user enumeration).
        """
        if not is_auth_enabled():
            raise HTTPException(
                status_code=400,
                detail=("GLUON_AUTH_ENABLED is false — login is a no-op. Use the system user or enable auth."),
            )
        provider = get_auth_provider(store)
        if not hasattr(provider, "authenticate"):
            raise HTTPException(status_code=500, detail="auth provider misconfigured")
        try:
            user = provider.authenticate(body.username, body.password)  # type: ignore[attr-defined]
        except InvalidCredentialsError:
            raise HTTPException(status_code=401, detail="invalid credentials") from None
        except UserDisabledError:
            raise HTTPException(status_code=401, detail="invalid credentials") from None

        session = create_session_for_user(
            store,
            user,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        # Cookie settings: httpOnly + sameSite=lax. `secure` is left off because
        # the dev dashboard runs on http://localhost; operators deploying with
        # the `GLUON_SSL_*` envs should reverse-proxy and flip `secure` via
        # the proxy (we don't have enough context here to know).
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session.id,
            max_age=DEFAULT_SESSION_TTL_DAYS * 24 * 3600,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return LoginResponse(user=_user_to_response(user))

    @app.post("/api/auth/logout")
    async def auth_logout(
        response: Response,
        request: Request,
    ) -> dict[str, bool]:
        """Clear the session cookie and delete the session from the store.

        Always succeeds — even with no valid session, we clear the cookie so
        state-mismatch scenarios don't lock users in a broken state.
        """
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        if session_id:
            try:
                store.delete_user_session(session_id)
            except Exception:
                # Never fail logout — worst case the session expires naturally.
                pass
        response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/auth/me", response_model=MeResponse)
    async def auth_me(
        request: Request,
    ) -> MeResponse:
        """Return the current user.

        With auth disabled: always returns SYSTEM_USER.
        With auth enabled + no/invalid session: returns SYSTEM_USER with
        `auth_enabled=True` so the client knows to show a login prompt.
        With auth enabled + valid session: returns the real user.
        """
        auth_on = is_auth_enabled()
        if not auth_on:
            return MeResponse(user=_user_to_response(SYSTEM_USER), auth_enabled=False)

        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        if not session_id:
            # Tell client they're not logged in — use SYSTEM_USER payload as
            # a placeholder so the shape is uniform.
            return MeResponse(user=_user_to_response(SYSTEM_USER), auth_enabled=True)
        from gluon.auth import resolve_session

        result = resolve_session(store, session_id)
        if result is None:
            return MeResponse(user=_user_to_response(SYSTEM_USER), auth_enabled=True)
        user, _ = result
        return MeResponse(user=_user_to_response(user), auth_enabled=True)

    # ========== Auth provider feature-detection (D5 Phase 3) ==========

    def _get_oauth_client(provider):  # type: ignore[no-untyped-def]
        """Build (and cache on app.state) the Authlib OAuth client for OIDC.

        Authlib registers each provider once; subsequent calls hit the cache.
        Discovery hits ``{issuer}/.well-known/openid-configuration`` lazily on
        first request.

        Stashed on ``app.state`` so multiple endpoints share one instance and
        we don't re-register on every request.
        """
        from authlib.integrations.starlette_client import OAuth  # type: ignore[import-untyped]

        oauth = getattr(app.state, "oauth", None)
        if oauth is None:
            oauth = OAuth()
            app.state.oauth = oauth
        if not hasattr(oauth, "gluon_oidc"):
            oauth.register(
                name="gluon_oidc",
                client_id=provider.config.client_id,
                client_secret=provider.config.client_secret,
                server_metadata_url=(f"{provider.config.issuer}/.well-known/openid-configuration"),
                client_kwargs={"scope": provider.config.scopes},
            )
        return oauth

    @app.get("/api/auth/providers", response_model=AuthProvidersResponse)
    async def auth_providers_endpoint(request: Request) -> AuthProvidersResponse:
        """Tell the login page which auth methods are available.

        - ``auth_enabled=false`` → caller is in single-user mode; no login UI.
        - ``local=true`` → render the username/password form.
        - ``oidc != null`` → render a "Sign in with {oidc.name}" button
          that POSTs to ``oidc.login_url`` (which 302s to the IdP).

        Local + OIDC can both be enabled simultaneously (typical pattern:
        OIDC for humans, a few service-account local users for automation).
        """
        if not is_auth_enabled():
            return AuthProvidersResponse(auth_enabled=False, local=False, oidc=None)

        local_enabled = get_local_provider(store) is not None
        oidc_provider = get_oidc_provider(store)
        oidc_info: OIDCProviderInfo | None = None
        if oidc_provider is not None:
            # Build a same-origin URL the browser can navigate to.
            login_url = str(request.url_for("oidc_login_endpoint"))
            oidc_info = OIDCProviderInfo(
                name=oidc_provider.config.provider_name,
                login_url=login_url,
            )
        return AuthProvidersResponse(
            auth_enabled=True,
            local=local_enabled,
            oidc=oidc_info,
        )

    # ========== OIDC flow (D5 Phase 3) ==========
    #
    # The redirect/callback dance uses Authlib + Starlette's SessionMiddleware
    # to carry state+nonce between the two requests. SessionMiddleware is
    # registered conditionally (only when OIDC is configured) — see the
    # bottom of this function. Without it, the OIDC endpoints 503.

    @app.get("/api/auth/oidc/login", name="oidc_login_endpoint")
    async def oidc_login_endpoint(request: Request) -> Response:
        """Kick off the OIDC authorization-code flow.

        302s the browser to the provider's authorize URL. Authlib stores the
        state + nonce in ``request.session`` (cookie-backed) so the callback
        can verify them. Any next-page hint (``?next=/board``) is preserved
        through the same session for post-login redirect.
        """
        provider = get_oidc_provider(store)
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail="OIDC is not configured on this server",
            )
        oauth = _get_oauth_client(provider)
        next_url = request.query_params.get("next", "/")
        request.session["oidc_next_url"] = next_url
        return await oauth.gluon_oidc.authorize_redirect(  # type: ignore[no-any-return]
            request, provider.config.redirect_uri
        )

    @app.get("/api/auth/oidc/callback", name="oidc_callback_endpoint")
    async def oidc_callback_endpoint(request: Request) -> Response:
        """Handle the redirect back from the OIDC provider.

        Exchanges the auth code for an ID token, validates it (signature
        via JWKS + issuer + audience + nonce), and either resolves the
        user via :meth:`OIDCAuthProvider.resolve_or_provision` or refuses.

        On success: mints a Gluon ``UserSession``, drops the session cookie,
        and 302s back to ``next`` (or ``/`` if absent).

        On failure: redirects back to ``/`` with a query string
        (``?oidc_error=…``) so the SPA can render a friendly message
        without leaking provider-side detail.
        """
        from authlib.integrations.base_client.errors import OAuthError  # type: ignore[import-untyped]

        provider = get_oidc_provider(store)
        if provider is None:
            raise HTTPException(status_code=503, detail="OIDC is not configured")
        oauth = _get_oauth_client(provider)
        try:
            token = await oauth.gluon_oidc.authorize_access_token(request)
        except OAuthError as e:
            logger.warning("OIDC token exchange failed: %s", e)
            return RedirectResponse(url="/?oidc_error=token_exchange", status_code=302)
        # Authlib parses the id_token and exposes claims under `userinfo`
        # (or `id_token` claims if userinfo is not in the response).
        userinfo = token.get("userinfo") or {}
        sub = userinfo.get("sub")
        if not sub:
            logger.warning("OIDC callback returned no `sub` claim — userinfo=%r", userinfo)
            return RedirectResponse(url="/?oidc_error=missing_sub", status_code=302)
        email = userinfo.get("email")
        display_name = userinfo.get("name") or email

        try:
            user = provider.resolve_or_provision(sub=sub, email=email, display_name=display_name)
        except UserDisabledError:
            return RedirectResponse(url="/?oidc_error=disabled", status_code=302)
        except InvalidCredentialsError as e:
            logger.info("OIDC user rejected: %s", e)
            return RedirectResponse(url="/?oidc_error=not_authorized", status_code=302)

        # Mint a Gluon session — same DB-backed cookie used by the local
        # password endpoint, so the rest of the app doesn't have to know
        # how the user logged in.
        session = create_session_for_user(
            store,
            user,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        next_url = request.session.pop("oidc_next_url", "/")
        # Defense-in-depth: refuse open-redirects.
        if not next_url.startswith("/"):
            next_url = "/"

        resp = RedirectResponse(url=next_url, status_code=302)
        resp.set_cookie(
            SESSION_COOKIE_NAME,
            session.id,
            max_age=int(timedelta(days=DEFAULT_SESSION_TTL_DAYS).total_seconds()),
            httponly=True,
            samesite="lax",
            # `secure` only matters under HTTPS — auto-detect via scheme.
            secure=request.url.scheme == "https",
        )
        return resp

    # ========== Self-serve chat linking (D5 Phase 4) ==========

    _valid_link_transports = ("telegram", "discord")

    @app.post("/api/auth/link-codes", response_model=LinkCodeResponse)
    async def create_link_code_endpoint(
        body: CreateLinkCodeRequest,
        user: UserModel = Depends(current_user_dep),
    ) -> LinkCodeResponse:
        """Generate a one-time code that binds a chat identity to the
        calling user's Gluon account.

        The user redeems it by sending ``/link <code>`` to the bot. The
        code is short (~50 bits of entropy), case-insensitive on
        consumption, and expires after 10 minutes. Generating a new code
        for the same (user, transport) tears down any prior unconsumed
        codes — there's only one active code at a time.

        Refuses for SYSTEM_USER (single-user mode / no real user).
        """
        if user.id == SYSTEM_USER.id:
            raise HTTPException(
                status_code=400,
                detail="cannot generate a link code without a real user — sign in first",
            )
        transport = body.transport.lower().strip()
        if transport not in _valid_link_transports:
            raise HTTPException(
                status_code=400,
                detail=(f"unknown transport '{body.transport}'; valid: {list(_valid_link_transports)}"),
            )
        link_code = store.create_link_code(user_id=user.id, transport=transport)
        return LinkCodeResponse(
            code=link_code.code,
            transport=link_code.transport,
            expires_at=link_code.expires_at.isoformat(),
        )

    @app.get("/api/auth/links", response_model=LinkStatusResponse)
    async def get_my_links_endpoint(
        user: UserModel = Depends(current_user_dep),
    ) -> LinkStatusResponse:
        """Show which chat accounts are bound to the current user.

        Always succeeds — returns ``{telegram_user_id: null, discord_user_id: null}``
        for SYSTEM_USER / unlinked users.
        """
        return LinkStatusResponse(
            telegram_user_id=user.telegram_user_id,
            discord_user_id=user.discord_user_id,
        )

    @app.delete("/api/auth/links/{transport}", response_model=LinkStatusResponse)
    async def unlink_my_chat_endpoint(
        transport: str,
        user: UserModel = Depends(current_user_dep),
    ) -> LinkStatusResponse:
        """Unbind the calling user's chat account on ``transport``."""
        if user.id == SYSTEM_USER.id:
            raise HTTPException(status_code=400, detail="no real user to unlink")
        transport = transport.lower().strip()
        if transport not in _valid_link_transports:
            raise HTTPException(
                status_code=400,
                detail=f"unknown transport '{transport}'",
            )
        updated = store.unlink_chat(user_id=user.id, transport=transport)
        if updated is None:
            raise HTTPException(status_code=404, detail="user not found")
        return LinkStatusResponse(
            telegram_user_id=updated.telegram_user_id,
            discord_user_id=updated.discord_user_id,
        )

    # ========== User management (D5 Phase 2 — admin-only) ==========

    @app.get("/api/users", response_model=UserListResponse)
    async def list_users_endpoint(
        include_disabled: bool = False,
        _admin: UserModel = Depends(require_admin),
    ) -> UserListResponse:
        """List all users. Admin-only."""
        users = store.list_users(include_disabled=include_disabled)
        return UserListResponse(
            users=[_user_to_response(u) for u in users],
            total=len(users),
        )

    @app.post("/api/users", response_model=UserResponse)
    async def create_user_endpoint(
        body: CreateUserRequest,
        _admin: UserModel = Depends(require_admin),
    ) -> UserResponse:
        """Create a new user. Admin-only.

        Password must be at least 12 characters. Returns 409 if the username
        already exists.
        """
        provider = get_auth_provider(store)
        if not hasattr(provider, "create_user"):
            raise HTTPException(
                status_code=500,
                detail="current auth provider does not support user creation",
            )
        try:
            role_enum = UserRole(body.role.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"unknown role '{body.role}'; valid: {[r.value for r in UserRole]}",
            ) from None
        try:
            user = provider.create_user(  # type: ignore[attr-defined]
                username=body.username,
                password=body.password,
                display_name=body.display_name,
                email=body.email,
                role=role_enum,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(status_code=409, detail="username already exists") from None
            raise
        return _user_to_response(user)

    @app.patch("/api/users/{user_id}", response_model=UserResponse)
    async def update_user_endpoint(
        user_id: str,
        body: UpdateUserRequest,
        admin: UserModel = Depends(require_admin),  # noqa: ARG001
    ) -> UserResponse:
        """Update a user's profile fields. Admin-only.

        Any field left `None` in the request is unchanged. Role changes and
        `disabled=True` rotate the target user's active sessions.

        Chat-account binding (D5 Phase 4): `telegram_user_id` /
        `discord_user_id` accept either a positive integer to set the link,
        or `0` to clear it. We refuse to set a chat ID that is already
        bound to a different user (returns 409) — chat IDs must be unique
        per platform so the bot can resolve them unambiguously.
        """
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")

        needs_session_rotation = False
        if body.display_name is not None:
            user.display_name = body.display_name
        if body.email is not None:
            user.email = body.email
        if body.role is not None and body.role.lower() != user.role.value:
            try:
                user.role = UserRole(body.role.lower())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown role '{body.role}'",
                ) from None
            needs_session_rotation = True
        if body.disabled is not None and body.disabled != user.disabled:
            user.disabled = body.disabled
            if body.disabled:
                needs_session_rotation = True

        # D5 Phase 4 — chat-account binding (admin pre-registration).
        # 0 is the "clear" sentinel; positive integers set the link.
        if body.telegram_user_id is not None:
            new_tg: int | None = body.telegram_user_id or None
            if new_tg is not None and new_tg != user.telegram_user_id:
                conflict = store.get_user_by_telegram_id(new_tg)
                if conflict is not None and conflict.id != user.id:
                    raise HTTPException(
                        status_code=409,
                        detail=(f"telegram user {new_tg} is already bound to @{conflict.username}"),
                    )
            user.telegram_user_id = new_tg
        if body.discord_user_id is not None:
            new_dc: int | None = body.discord_user_id or None
            if new_dc is not None and new_dc != user.discord_user_id:
                conflict = store.get_user_by_discord_id(new_dc)
                if conflict is not None and conflict.id != user.id:
                    raise HTTPException(
                        status_code=409,
                        detail=(f"discord user {new_dc} is already bound to @{conflict.username}"),
                    )
            user.discord_user_id = new_dc

        store.update_user(user)
        if needs_session_rotation:
            store.delete_user_sessions_for_user(user.id)
        return _user_to_response(user)

    @app.delete("/api/users/{user_id}", response_model=UserResponse)
    async def disable_user_endpoint(
        user_id: str,
        _admin: UserModel = Depends(require_admin),
    ) -> UserResponse:
        """Disable a user (soft delete). Admin-only. Rotates their sessions.

        We don't hard-delete users because all the D5 Phase 2 attribution
        links (``execution_runs.user_id``, ``orchestrator_tasks.created_by_user_id``,
        ``pending_approvals.decided_by_user_id``) would lose their target.
        Disable-and-preserve keeps the audit trail intact.
        """
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if not user.disabled:
            user.disabled = True
            store.update_user(user)
            store.delete_user_sessions_for_user(user.id)
        return _user_to_response(user)

    @app.post("/api/users/{user_id}/password", response_model=UserResponse)
    async def change_password_endpoint(
        user_id: str,
        body: ChangePasswordRequest,
        current: UserModel = Depends(current_user_dep),
    ) -> UserResponse:
        """Change a user's password.

        - Admins may change anyone's password without providing `current_password`.
        - Any other user may change only their own password AND must provide
          `current_password` which is verified against the stored hash.

        All sessions for the target user are rotated on success.
        """
        target = store.get_user(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")

        provider = get_auth_provider(store)
        if not hasattr(provider, "set_password") or not hasattr(provider, "verify_password"):
            raise HTTPException(status_code=500, detail="auth provider misconfigured")

        is_admin = current.role == UserRole.ADMIN
        is_self = current.id == target.id

        if not is_admin:
            if not is_self:
                raise HTTPException(status_code=403, detail="can only change your own password")
            if not body.current_password:
                raise HTTPException(status_code=400, detail="current_password required")
            if not provider.verify_password(target.auth_subject, body.current_password):  # type: ignore[attr-defined]
                raise HTTPException(status_code=401, detail="current password is incorrect")

        try:
            provider.set_password(target, body.new_password)  # type: ignore[attr-defined]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

        return _user_to_response(target)

    # ========== Version Info ==========

    # Cache version info at startup (computed once)
    _version_info: dict[str, str] | None = None

    def _get_version_info() -> dict[str, str]:
        """Get version info from environment or git."""
        nonlocal _version_info
        if _version_info is not None:
            return _version_info

        # Try environment variables first (set during Docker build)
        version = os.environ.get("GLUON_VERSION", "")
        full_version = os.environ.get("GLUON_FULL_VERSION", "")
        build_time = os.environ.get("GLUON_BUILD_TIME", "")

        # Fallback to git for development mode
        if not version:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    version = result.stdout.strip()
            except Exception:
                version = "dev"

        if not full_version:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    full_version = result.stdout.strip()
            except Exception:
                full_version = "development"

        if not build_time:
            build_time = datetime.now().isoformat()

        environment = "production" if os.environ.get("GLUON_VERSION") else "development"

        from gluon import __version__ as semver

        _version_info = {
            "version": version,
            "full_version": full_version,
            "build_time": build_time,
            "environment": environment,
            "semver": semver,
        }
        return _version_info

    @app.get("/api/version", response_model=VersionResponse)
    async def get_version() -> VersionResponse:
        """Get application version info for update checking."""
        info = _get_version_info()
        return VersionResponse(**info)

    # ========== Slash Commands ==========

    @app.get("/api/commands", response_model=SlashCommandsResponse)
    async def get_commands() -> SlashCommandsResponse:
        """Get available slash commands and skills from ~/.claude directories."""
        commands = get_slash_commands()
        return SlashCommandsResponse(
            commands=[
                SlashCommandResponse(
                    name=cmd.name,
                    type=cmd.type,
                    description=cmd.description,
                    argument_hint=cmd.argument_hint,
                )
                for cmd in commands
            ]
        )

    @app.get("/api/projects/{project_id}/commands", response_model=SlashCommandsResponse)
    async def get_project_commands(project_id: str) -> SlashCommandsResponse:
        """Get slash commands including project-specific ones from <project>/.claude directories."""
        project = store.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        commands = get_slash_commands(project_path=project.expanded_path)
        return SlashCommandsResponse(
            commands=[
                SlashCommandResponse(
                    name=cmd.name,
                    type=cmd.type,
                    description=cmd.description,
                    argument_hint=cmd.argument_hint,
                )
                for cmd in commands
            ]
        )

    @app.get("/api/projects/{project_id}/files", response_model=ProjectFilesResponse)
    async def list_project_files(
        project_id: str,
        prefix: str = "",
        limit: int = Query(default=1000, ge=1, le=2000),
    ) -> ProjectFilesResponse:
        """List files in a project for autocomplete.

        Returns files and directories from whitelisted scan paths (src/, tests/, etc.)
        with common exclusions (node_modules, .git, __pycache__, etc.) filtered out.
        """
        project = store.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        project_path = Path(project.expanded_path)
        if not project_path.exists():
            raise HTTPException(status_code=404, detail=f"Project path not found: {project_path}")

        files, truncated = get_project_files(
            project_id=project_id,
            project_path=project_path,
            prefix=prefix,
            limit=limit,
        )

        return ProjectFilesResponse(
            project_id=project_id,
            files=[ProjectFileResponse(path=f.path, type=f.type) for f in files],
            truncated=truncated,
        )

    # ========== Phase 7.2: Status Transitions (Drag-and-Drop) ==========

    # Allowed status transitions for drag-and-drop
    allowed_transitions: dict[str, set[str]] = {
        "pending": {"cancelled"},
        "running": {"cancelled"},
        "review": {"completed", "pending", "failed", "cancelled"},  # Approve, retry, reject, or cancel
        "completed": {"pending", "review"},  # Re-queue for retry, or back to review if PR still open
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

        if new_status not in allowed_transitions.get(current_status, set()):
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

    @app.post("/api/runs/{run_id}/unarchive", response_model=RunResponse)
    async def unarchive_run(run_id: str) -> RunResponse:
        """Unarchive a run to show it on the board again."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        updated_run = store.archive_run(run.id, archived=False)
        if not updated_run:
            raise HTTPException(status_code=500, detail="Failed to unarchive run")

        project_lookup = get_project_lookup()
        response = run_to_response(updated_run, project_lookup)

        # Broadcast update so UI reflects the change
        project_name = project_lookup.get(updated_run.project_id, updated_run.project_id[:8])
        await ws_manager.broadcast_run_update(updated_run, project_name)

        return response

    # ========== List-view cockpit endpoints (see tmp/list-view-plan.md) ==========

    @app.patch("/api/runs/{run_id}", response_model=RunResponse)
    async def patch_run(run_id: str, body: UpdateRunRequest) -> RunResponse:
        """Partially update a run's user-editable fields (title, kind).

        Unspecified fields are left unchanged. Pass ``null`` explicitly to clear
        a field. Returns the updated ``RunResponse``.
        """
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Pydantic's `model_fields_set` tells us which fields the client actually
        # sent — distinguishes "set to null" (clear) from "omitted" (leave).
        sent = body.model_fields_set
        if "custom_title" in sent:
            title = body.custom_title
            if title is not None:
                title = title.strip()
                if len(title) > 200:
                    raise HTTPException(status_code=400, detail="custom_title must be ≤ 200 chars")
                run.custom_title = title or None
            else:
                run.custom_title = None
        if "kind" in sent:
            kind = body.kind
            if kind is not None:
                kind = kind.strip().lower()
                if kind and kind not in {"research", "build", "docs", "bug", "review", "chore"}:
                    raise HTTPException(
                        status_code=400,
                        detail="kind must be one of: research, build, docs, bug, review, chore",
                    )
                run.kind = kind or None
            else:
                run.kind = None

        run.bump_activity()
        store.update_run(run)

        project_lookup = get_project_lookup()
        project_name = project_lookup.get(run.project_id, run.project_id[:8])
        await ws_manager.broadcast_run_update(run, project_name)
        return run_to_response(run, project_lookup)

    @app.post("/api/runs/{run_id}/snooze", response_model=RunResponse)
    async def snooze_run(run_id: str, body: SnoozeRunRequest) -> RunResponse:
        """Set or clear a run's snooze deadline."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        run.snoozed_until = body.until
        run.bump_activity()
        store.update_run(run)

        project_lookup = get_project_lookup()
        project_name = project_lookup.get(run.project_id, run.project_id[:8])
        await ws_manager.broadcast_run_update(run, project_name)
        return run_to_response(run, project_lookup)

    @app.post("/api/runs/{run_id}/fork", response_model=RunResponse)
    async def fork_run_endpoint(
        run_id: str,
        body: ForkRunRequest,
        user: UserModel = Depends(current_user_dep),  # type: ignore[arg-type]
    ) -> RunResponse:
        """Fork an existing run's Claude session into a new child run.

        The child run inherits the parent's ``claude_session_id`` and gets its
        own subprocess. Parent must have started at least once (must have a
        session id). See ``TaskRunner.fork_run`` for behaviour details.
        """
        parent = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not parent:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        attribution_user_id = user.id if user.id != SYSTEM_USER.id else None
        try:
            child = await runner.fork_run(
                parent_run_id=parent.id,
                new_prompt=body.prompt,
                custom_title=body.custom_title,
                initiator="web:fork",
                user_id=attribution_user_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

        project_lookup = get_project_lookup()
        project_name = project_lookup.get(child.project_id, child.project_id[:8])
        await ws_manager.broadcast_run_update(child, project_name)
        return run_to_response(child, project_lookup)

    @app.get("/api/attention-counts", response_model=AttentionCountsResponse)
    async def get_attention_counts() -> AttentionCountsResponse:
        """Aggregate counts of runs that need user attention.

        A run "needs attention" if it is FAILED, has a CONFLICTING PR, or has a
        pending question (``pending_questions.status = 'pending'``). Snoozed and
        archived runs are excluded.
        """
        runs = store.list_runs(limit=1000)
        try:
            pending_q_run_ids = store.list_run_ids_with_pending_questions()
        except Exception:
            pending_q_run_ids = set()

        needs_input = 0
        failed = 0
        conflicts = 0
        by_project: dict[str, int] = {}
        for run in runs:
            if run.archived or run.is_snoozed:
                continue
            attention = False
            if run.id in pending_q_run_ids:
                needs_input += 1
                attention = True
            if run.status == RunStatus.FAILED:
                failed += 1
                attention = True
            if run.pr_mergeable == "CONFLICTING":
                conflicts += 1
                attention = True
            if attention:
                by_project[run.project_id] = by_project.get(run.project_id, 0) + 1

        return AttentionCountsResponse(
            total=needs_input + failed + conflicts,
            needs_input=needs_input,
            failed=failed,
            conflicts=conflicts,
            by_project=by_project,
        )

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

        # Resolve and validate path (os.path.realpath breaks CodeQL taint chain)
        resolved = os.path.realpath(body.path)
        home_dir = os.path.realpath(str(Path.home()))
        if not (resolved.startswith(home_dir + os.sep) or resolved == home_dir):
            raise HTTPException(status_code=400, detail="Path must be under home directory")
        if not os.path.exists(resolved):
            raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")
        project_path = Path(resolved)

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
                    daily_budget_usd=ws.daily_budget_usd,
                    monthly_budget_usd=ws.monthly_budget_usd,
                    daily_spend_usd=store.get_workspace_daily_spend(ws.id),
                    monthly_spend_usd=store.get_workspace_monthly_spend(ws.id),
                )
            )
        return result

    @app.get("/api/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    async def get_workspace_detail(workspace_id: str) -> WorkspaceResponse:
        """Get a single workspace including rolling budgets and current spend."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            workspace = store.get_workspace_by_name(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

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

    @app.post("/api/workspaces", response_model=WorkspaceResponse)
    async def create_workspace(body: CreateWorkspaceRequest) -> WorkspaceResponse:
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

        return WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            path=str(workspace.path),
            project_count=len(projects_added),
            auto_discover=workspace.auto_discover,
            daily_budget_usd=workspace.daily_budget_usd,
            monthly_budget_usd=workspace.monthly_budget_usd,
            daily_spend_usd=store.get_workspace_daily_spend(workspace.id),
            monthly_spend_usd=store.get_workspace_monthly_spend(workspace.id),
        )

    @app.put(
        "/api/workspaces/{workspace_id}/budget", response_model=WorkspaceResponse, dependencies=[Depends(require_admin)]
    )
    async def update_workspace_budget(workspace_id: str, body: UpdateWorkspaceBudgetRequest) -> WorkspaceResponse:
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

    # ========== Workspace Settings Endpoints ==========

    @app.get(
        "/api/workspaces/{workspace_id}/settings",
        response_model=WorkspaceSettingsResponse,
        dependencies=[Depends(require_admin)],
    )
    async def get_workspace_settings(workspace_id: str) -> WorkspaceSettingsResponse:
        """Get workspace settings with global defaults for comparison."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        all_ws_settings = store.get_workspace_settings(workspace.id)
        # Separate env vars from regular settings
        settings = {k: v for k, v in all_ws_settings.items() if not k.startswith("env.")}
        env_var_keys = [k[4:] for k in all_ws_settings if k.startswith("env.")]
        global_defaults = store.get_all_settings()

        return WorkspaceSettingsResponse(
            workspace_id=workspace.id,
            settings=settings,
            env_var_keys=env_var_keys,
            global_defaults=global_defaults,
        )

    @app.put("/api/workspaces/{workspace_id}/settings", dependencies=[Depends(require_admin)])
    async def update_workspace_settings(workspace_id: str, body: dict[str, str]) -> dict:
        """Set one or more workspace setting overrides."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        for key, value in body.items():
            if key.startswith("env."):
                raise HTTPException(status_code=400, detail="Use /env-vars endpoint for environment variables")
            store.set_workspace_setting(workspace.id, key, value)

        return {"updated": len(body), "workspace_id": workspace.id}

    @app.delete("/api/workspaces/{workspace_id}/settings/{key}", dependencies=[Depends(require_admin)])
    async def delete_workspace_setting(workspace_id: str, key: str) -> dict:
        """Remove a single setting override (reverts to global)."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        deleted = store.delete_workspace_setting(workspace.id, key)
        return {"deleted": deleted, "key": key, "workspace_id": workspace.id}

    @app.put("/api/workspaces/{workspace_id}/env-vars", dependencies=[Depends(require_admin)])
    async def update_workspace_env_vars(workspace_id: str, body: dict[str, str]) -> dict:
        """Set workspace environment variables (auto-prefixed with env.)."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        for key, value in body.items():
            store.set_workspace_setting(workspace.id, f"env.{key}", value)

        return {"updated": len(body), "workspace_id": workspace.id}

    @app.delete("/api/workspaces/{workspace_id}/env-vars/{key}", dependencies=[Depends(require_admin)])
    async def delete_workspace_env_var(workspace_id: str, key: str) -> dict:
        """Remove a workspace environment variable."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        deleted = store.delete_workspace_setting(workspace.id, f"env.{key}")
        return {"deleted": deleted, "key": key, "workspace_id": workspace.id}

    @app.post("/api/workspaces/{workspace_id}/scan", response_model=ScanResultResponse)
    async def scan_workspace(workspace_id: str) -> ScanResultResponse:
        """Rescan workspace for new projects and remove missing ones."""
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            workspace = store.get_workspace_by_name(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        projects_added = []
        projects_removed = []

        # Get current project paths on disk
        project_paths = workspace.scan_for_projects()

        # Check existing projects in this workspace for removed directories
        existing_projects = store.list_projects_by_workspace(workspace.id)
        for project in existing_projects:
            project_path = project.expanded_path
            if not project_path.exists():
                # Directory no longer exists - remove the project
                logger.info(f"Removing project '{project.name}' - directory no longer exists: {project_path}")
                store.delete_project(project.id)
                projects_removed.append(project.name)

        # Add new projects that don't exist in database
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
            projects_removed=projects_removed,
        )

    @app.post("/api/workspaces/{workspace_id}/clone", response_model=CloneResultResponse)
    async def clone_repository(workspace_id: str, body: CloneRepositoryRequest) -> CloneResultResponse:
        """Clone a GitHub repository into a workspace directory."""
        import re
        import shutil

        # 1. Find workspace
        workspace = store.get_workspace(workspace_id)
        if not workspace:
            workspace = store.get_workspace_by_name(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}")

        # 2. Validate GitHub URL (strict regex, no command injection)
        github_pattern = re.compile(r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:\.git)?$")
        url = body.github_url.strip()
        if not github_pattern.match(url):
            raise HTTPException(
                status_code=400,
                detail="Invalid GitHub URL. Expected format: https://github.com/owner/repo",
            )

        # 3. Extract repo name
        repo_name = url.rstrip("/").split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        # 4. Validate workspace path
        workspace_path = workspace.expanded_path
        if not workspace_path.exists() or not workspace_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Workspace path does not exist: {workspace_path}",
            )

        # 5. Check target directory doesn't already exist
        target_path = workspace_path / repo_name
        if target_path.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Directory already exists: {repo_name}. Delete it first or choose a different workspace.",
            )

        # 6. Run git clone using asyncio.create_subprocess_exec (safe from injection)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                url,
                str(target_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=300,  # 5 minute timeout
            )
        except TimeoutError:
            # Attempt cleanup of partial clone
            if target_path.exists():
                shutil.rmtree(target_path, ignore_errors=True)
            raise HTTPException(
                status_code=504,
                detail="Clone operation timed out after 5 minutes",
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="git command not found. Ensure git is installed.",
            )

        if proc.returncode != 0:
            stderr_text = stderr.decode().strip() if stderr else "Unknown error"
            # Cleanup partial clone
            if target_path.exists():
                shutil.rmtree(target_path, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"Clone failed: {stderr_text}",
            )

        # 7. Scan workspace to register the new project (reuse scan logic)
        projects_added: list[str] = []
        projects_removed: list[str] = []
        project_paths = workspace.scan_for_projects()

        existing_projects = store.list_projects_by_workspace(workspace.id)
        for project in existing_projects:
            project_path = project.expanded_path
            if not project_path.exists():
                store.delete_project(project.id)
                projects_removed.append(project.name)

        registered_name: str | None = None
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
                if project_path.resolve() == target_path.resolve():
                    registered_name = project_name

        scan_result = ScanResultResponse(
            workspace_id=workspace.id,
            projects_found=len(project_paths),
            projects_added=projects_added,
            projects_removed=projects_removed,
        )

        return CloneResultResponse(
            workspace_id=workspace.id,
            repo_name=repo_name,
            clone_path=str(target_path),
            project_registered=registered_name is not None,
            project_name=registered_name,
            scan_result=scan_result,
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

    @app.get("/api/settings", dependencies=[Depends(require_admin)])
    async def get_all_settings() -> dict[str, str]:
        """Get all settings as key-value pairs."""
        from gluon.llm_provider import get_provider

        settings = store.get_all_settings()
        # Never round-trip secret values to the client (even for admins) — show
        # only that a value is set. Covers e.g. github_webhook_secret, *_token.
        settings = {k: _redact_setting(k, v) for k, v in settings.items()}
        # Expose whether VERCEL_TOKEN is available from environment (without leaking the value)
        settings["_vercel_token_from_env"] = "true" if os.environ.get("VERCEL_TOKEN") else "false"

        # Expose resolved provider info (the actual provider may come from env var, not DB)
        provider = get_provider()
        settings["_llm_provider_name"] = provider.name
        settings["_llm_provider_supports_cost_tracking"] = str(provider.supports_cost_tracking).lower()
        return settings

    @app.put("/api/settings/{key}", dependencies=[Depends(require_admin)])
    async def update_setting(key: str, body: dict) -> dict[str, str]:
        """Update a single setting value."""
        value = body.get("value")
        if value is None:
            raise HTTPException(status_code=400, detail="Missing 'value' in request body")
        store.set_setting(key, str(value))
        return {"key": key, "value": str(value)}

    @app.post("/api/vercel/test", dependencies=[Depends(require_admin)])
    async def test_vercel_token(body: dict) -> dict:
        """Test a Vercel API token by calling `vercel whoami`."""
        token = (body.get("token") or "").strip() or os.environ.get("VERCEL_TOKEN", "")
        if not token:
            raise HTTPException(status_code=400, detail="No token provided and VERCEL_TOKEN not set")

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["vercel", "whoami", f"--token={token}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return {"valid": True, "account": result.stdout.strip()}
            else:
                return {"valid": False, "error": result.stderr.strip() or "Invalid token"}
        except FileNotFoundError:
            return {"valid": False, "error": "Vercel CLI not installed"}
        except subprocess.TimeoutExpired:
            return {"valid": False, "error": "Request timed out"}

    @app.get("/api/sandbox/status")
    async def get_sandbox_status() -> dict:
        """Get sandbox availability and configuration.

        Returns information about OS-level sandboxing:
        - Linux: bubblewrap (bwrap)
        - macOS: sandbox-exec with Seatbelt profiles
        """
        import platform
        import shutil

        system = platform.system()

        # Check if sandbox runtime is available
        if system == "Linux":
            available = shutil.which("bwrap") is not None
            runtime = "bubblewrap"
        elif system == "Darwin":
            available = shutil.which("sandbox-exec") is not None
            runtime = "sandbox-exec"
        else:
            available = False
            runtime = None

        return {
            "available": available,
            "runtime": runtime,
            "enabled": store.get_setting("sandbox_enabled", "true") == "true",
            "platform": system,
        }

    # ========== Webhooks API (Phase: Distributed Workers) ==========

    @app.post("/api/webhooks/github")
    async def handle_github_webhook(request: Request) -> dict:
        """
        Handle GitHub webhook events.

        Validates webhook signature and creates runs for supported events:
        - push: Review pushed commits
        - pull_request: Review PR (opened, synchronize, reopened)
        - issues: Analyze new issues
        - issue_comment: Handle /gluon commands in comments
        - pull_request_review: Address requested changes

        Requires X-Hub-Signature-256 header for signature validation.
        """
        import os

        from gluon.webhooks.github import GitHubWebhookHandler

        # Get webhook secret from environment or database
        webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
        if not webhook_secret:
            # Try to get from settings
            webhook_secret = store.get_setting("github_webhook_secret")

        if not webhook_secret:
            raise HTTPException(
                status_code=500,
                detail="GitHub webhook secret not configured. Set GITHUB_WEBHOOK_SECRET env var.",
            )

        # Get signature and event type from headers
        signature = request.headers.get("X-Hub-Signature-256", "")
        event_type = request.headers.get("X-GitHub-Event", "")

        if not signature:
            raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

        if not event_type:
            raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

        # Read raw body for signature validation
        payload_bytes = await request.body()

        # Validate signature
        handler = GitHubWebhookHandler(secret=webhook_secret)
        is_valid = await handler.validate_signature(payload_bytes, signature)

        if not is_valid:
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        # Parse payload
        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        # Parse event into WebhookEvent
        event = await handler.parse_event(event_type, payload)

        if event is None:
            # Event type not supported or filtered out
            return {
                "status": "ignored",
                "reason": f"Event type '{event_type}' not processed or filtered",
            }

        # Resolve project by repository name
        project = store.get_project_by_name(event.project_hint)
        if not project:
            # Try to find by partial match (e.g., 'my-app' matches 'my-app-backend')
            projects = store.list_projects()
            for p in projects:
                if event.project_hint.lower() in p.name.lower():
                    project = p
                    break

        if not project:
            return {
                "status": "skipped",
                "reason": f"No project found matching '{event.project_hint}'",
            }

        # Check webhook config for this project
        configs = store.get_webhook_configs_for_handler("github")
        matching_config = None
        for config in configs:
            if config.project_id == project.id or config.project_id is None:
                # Check event type filter
                if config.matches_event(event.event_type):
                    # Check branch filter
                    if event.source_ref and not config.matches_branch(event.source_ref):
                        continue
                    matching_config = config
                    break

        if not matching_config:
            return {
                "status": "skipped",
                "reason": f"No webhook config matches event for project '{project.name}'",
            }

        # Use custom prompt template if configured
        prompt = event.prompt
        if matching_config.prompt_template:
            prompt = handler.generate_prompt(event_type, payload, matching_config.prompt_template)

        # Create and queue the run
        run = await runner.submit(
            project_id=project.id,
            prompt=prompt,
            wait=False,
            use_worktree=True,  # Webhooks default to worktree isolation
            initiator=f"webhook:github:{event.event_type}",
            model=None,  # Use default model
        )

        # Broadcast to WebSocket clients
        await ws_manager.broadcast_run_created(run, project.name)

        return {
            "status": "queued",
            "run_id": run.id,
            "project": project.name,
            "event_type": event.event_type,
            "prompt": prompt[:200] + "..." if len(prompt) > 200 else prompt,
        }

    @app.get("/api/webhooks", dependencies=[Depends(require_admin)])
    async def list_webhooks() -> list[dict]:
        """List all configured webhooks."""
        configs = store.list_webhook_configs(enabled_only=False)
        return [
            {
                "id": c.id,
                "handler": c.handler,
                "project_id": c.project_id,
                "events": c.events,
                "enabled": c.enabled,
                "created_at": c.created_at.isoformat(),
            }
            for c in configs
        ]

    @app.post("/api/webhooks", dependencies=[Depends(require_admin)])
    async def create_webhook(body: dict) -> dict:
        """Create a new webhook configuration."""
        import secrets

        from gluon.models import WebhookConfig

        handler = body.get("handler", "github")
        project_id = body.get("project_id")
        events = body.get("events", [])
        prompt_template = body.get("prompt_template")
        branches = body.get("branches")
        ignore_branches = body.get("ignore_branches")

        # Generate a secret if not provided
        secret_key = body.get("secret_key") or secrets.token_hex(32)

        config = WebhookConfig(
            handler=handler,
            project_id=project_id,
            secret_key=secret_key,
            events=events,
            prompt_template=prompt_template,
            branches=branches,
            ignore_branches=ignore_branches,
        )

        store.create_webhook_config(config)

        return {
            "id": config.id,
            "handler": config.handler,
            "secret_key": secret_key,  # Return so user can configure in GitHub
            "message": "Webhook created. Configure this secret in GitHub webhook settings.",
        }

    @app.delete("/api/webhooks/{webhook_id}", dependencies=[Depends(require_admin)])
    async def delete_webhook(webhook_id: str) -> dict:
        """Delete a webhook configuration."""
        config = store.get_webhook_config(webhook_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Webhook not found: {webhook_id}")

        success = store.delete_webhook_config(webhook_id)
        return {"deleted": success, "webhook_id": webhook_id}

    @app.post("/api/runs/{run_id}/create-pr")
    async def create_pr_for_run(run_id: str) -> dict:
        """Manually create a PR for a completed worktree run."""
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if not run.use_worktree or not run.branch_name:
            raise HTTPException(status_code=400, detail="Run is not a worktree run or has no branch")

        if run.pr_url:
            raise HTTPException(status_code=400, detail=f"PR already exists: {run.pr_url}")

        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Use git manager to push branch and create PR
        from gluon.git_manager import GitManager

        git_manager = GitManager(store, workspace_id=project.workspace_id)

        # Determine working path (worktree if still exists, else project root)
        working_path = (
            Path(run.worktree_path) if run.worktree_path and Path(run.worktree_path).exists() else project.expanded_path
        )

        try:
            async with _workspace_env(store, project.workspace_id):
                pr_result = await git_manager.push_branch_and_create_pr(
                    project_path=working_path,
                    branch_name=run.branch_name,
                    prompt=run.prompt,
                    run_id=run.id,
                    base_branch=run.source_branch,
                )

            if pr_result.get("pr_url"):
                run.pr_number = pr_result.get("pr_number")
                run.pr_url = pr_result.get("pr_url")
                run.pr_status = pr_result.get("pr_status")
                run.ci_status = "pending"
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
            logger.error("Failed to create PR for run %s: %s", run.id[:8], e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create a PR for run {run.id[:8]}; check server logs.",
            ) from e

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
            raise HTTPException(status_code=400, detail="Run is not a worktree run or has no branch")

        project = store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {run.project_id}")

        # Use git manager to merge branch locally and push
        from gluon.git_manager import GitManager

        git_manager = GitManager(store, workspace_id=project.workspace_id)

        # Use main project path (not worktree) for merging
        # Must use expanded_path to resolve ${HOME} and other variables
        project_path = project.expanded_path

        # Determine base branch (source_branch or default to main)
        base_branch = run.source_branch or "main"

        try:
            async with _workspace_env(store, project.workspace_id):
                merge_result = await git_manager.merge_branch_locally(
                    project_path=project_path,
                    branch_name=run.branch_name,
                    base_branch=base_branch,
                    push_after_merge=True,  # Will only push if remote exists
                )

            if merge_result.get("success"):
                # Mark run as merged and completed (works for both PRs and local-only merges)
                run.pr_status = "merged"
                run.status = RunStatus.COMPLETED
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
            logger.error("Failed to merge run %s: %s", run.id[:8], e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to merge run {run.id[:8]}; check server logs.",
            ) from e

    # ========== Image Attachments API (Phase 10.1) ==========

    from gluon.image_storage import (
        ImageNotFoundError,
        ImageStorageService,
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
            source=getattr(image, "source", "user"),
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
    async def attach_image_to_run(
        run_id: str, file: UploadFile | None = None, body: AttachImageRequest | None = None
    ) -> ImageResponse:
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

        async with _workspace_env(store, project.workspace_id):
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

        async with _workspace_env(store, project.workspace_id):
            result = await git_manager.delete_branch(project.expanded_path, branch_name, force=force, remote=remote)

        return BranchOperationResponse(
            success=result["success"],
            message=result["message"],
        )

    # ========== Git Sync Operations (Settings Page) ==========

    @app.get("/api/projects/{project_id}/git/status", response_model=GitStatusResponse)
    async def get_project_git_status(project_id: str) -> GitStatusResponse:
        """
        Get cached git status for a project (no network operations).
        Returns the last known git state from the database.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        # Get cached status from store (no git operations)
        cached_status = git_manager.get_cached_status(project)

        if cached_status is None:
            # No cached status, return minimal response
            return GitStatusResponse(
                is_git_repo=False,
            )

        # Compute derived fields
        is_diverged = cached_status.commits_ahead > 0 and cached_status.commits_behind > 0
        needs_pull = cached_status.commits_behind > 0 and cached_status.commits_ahead == 0
        needs_push = cached_status.commits_ahead > 0 and cached_status.commits_behind == 0

        return GitStatusResponse(
            is_git_repo=cached_status.is_git_repo,
            branch=cached_status.branch,
            remote=cached_status.remote,
            remote_url=cached_status.remote_url,
            has_uncommitted=cached_status.has_uncommitted,
            uncommitted_count=cached_status.uncommitted_count,
            commits_ahead=cached_status.commits_ahead,
            commits_behind=cached_status.commits_behind,
            is_diverged=is_diverged,
            needs_pull=needs_pull,
            needs_push=needs_push,
            has_conflicts=cached_status.has_conflicts,
            has_operation_in_progress=(cached_status.is_rebase_in_progress or cached_status.is_merge_in_progress),
            operation_type=(
                "rebase"
                if cached_status.is_rebase_in_progress
                else ("merge" if cached_status.is_merge_in_progress else None)
            ),
            last_fetch_at=cached_status.last_fetch_at,
        )

    @app.post("/api/projects/{project_id}/git/refresh", response_model=GitStatusResponse)
    async def refresh_project_git_status(project_id: str) -> GitStatusResponse:
        """
        Refresh git status for a project by fetching from remote.
        Updates the cached status and returns the new state.
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        # Refresh status (performs git fetch)
        try:
            async with _workspace_env(store, project.workspace_id):
                status = await git_manager.refresh_status(project)
        except Exception as e:
            logger.error(f"Failed to refresh git status for {project.name}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to refresh git status for project '{project.name}'; check server logs.",
            ) from e

        # Compute derived fields
        is_diverged = status.commits_ahead > 0 and status.commits_behind > 0
        needs_pull = status.commits_behind > 0 and status.commits_ahead == 0
        needs_push = status.commits_ahead > 0 and status.commits_behind == 0

        return GitStatusResponse(
            is_git_repo=status.is_git_repo,
            branch=status.branch,
            remote=status.remote,
            remote_url=status.remote_url,
            has_uncommitted=status.has_uncommitted,
            uncommitted_count=status.uncommitted_count,
            commits_ahead=status.commits_ahead,
            commits_behind=status.commits_behind,
            is_diverged=is_diverged,
            needs_pull=needs_pull,
            needs_push=needs_push,
            has_conflicts=status.has_conflicts,
            has_operation_in_progress=(status.is_rebase_in_progress or status.is_merge_in_progress),
            operation_type=(
                "rebase" if status.is_rebase_in_progress else ("merge" if status.is_merge_in_progress else None)
            ),
            last_fetch_at=status.last_fetch_at,
        )

    @app.post("/api/projects/{project_id}/git/sync", response_model=GitSyncResponse)
    async def sync_project_git(project_id: str, body: GitSyncRequest | None = None) -> GitSyncResponse:
        """
        Perform git sync operation on a project.

        Actions:
        - auto: Smart sync (pull if behind, push if ahead, commit+push if uncommitted)
        - pull: Git pull --ff-only
        - push: Git push
        - fetch: Git fetch only (refresh status)
        """
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        action = body.action if body else "auto"
        path = project.expanded_path

        # Get current status first
        status = await git_manager.refresh_status(project)

        if not status.is_git_repo:
            return GitSyncResponse(
                success=False,
                action="none",
                message="Not a git repository",
                error="Not a git repository",
            )

        # Check for blocking conditions
        if status.has_conflicts or status.is_rebase_in_progress or status.is_merge_in_progress:
            return GitSyncResponse(
                success=False,
                action="none",
                message="Cannot sync: conflicts or operation in progress",
                error="Resolve conflicts or complete the in-progress operation first",
            )

        # Determine action for "auto" mode
        if action == "auto":
            is_diverged = status.commits_ahead > 0 and status.commits_behind > 0
            if is_diverged:
                return GitSyncResponse(
                    success=False,
                    action="diverged",
                    message=f"Branch diverged: {status.commits_ahead} ahead, {status.commits_behind} behind",
                    error="Manual rebase or merge required",
                )
            elif status.commits_behind > 0:
                action = "pull"
            elif status.commits_ahead > 0:
                action = "push"
            elif status.has_uncommitted:
                action = "commit+push"
            else:
                # Already synced
                updated_status = await _build_git_status_response(status)
                return GitSyncResponse(
                    success=True,
                    action="none",
                    message="Already up to date",
                    updated_status=updated_status,
                )

        # Execute the action
        try:
            async with _workspace_env(store, project.workspace_id):
                if action == "pull":
                    # Cannot pull with uncommitted changes
                    if status.has_uncommitted:
                        return GitSyncResponse(
                            success=False,
                            action="none",
                            message=f"Cannot pull: {status.uncommitted_count} uncommitted changes",
                            error="Commit or stash your changes first",
                        )

                    result = await git_manager.pre_task_sync(project)

                    if not result.success:
                        return GitSyncResponse(
                            success=False,
                            action="pull",
                            message=result.message,
                            error=result.message,
                        )

                    # Refresh status after pull
                    new_status = await git_manager.refresh_status(project)
                    updated_status = await _build_git_status_response(new_status)

                    pull_message = (
                        f"Pulled {result.commits_pulled} commits" if result.commits_pulled else "Already up to date"
                    )
                    return GitSyncResponse(
                        success=True,
                        action="pull",
                        message=pull_message,
                        commits_pulled=result.commits_pulled or 0,
                        updated_status=updated_status,
                    )

                elif action == "push":
                    # Push current commits
                    rc, stdout, stderr = await git_manager._run_git(path, "push")

                    if rc != 0:
                        return GitSyncResponse(
                            success=False,
                            action="push",
                            message="Push failed",
                            error=stderr or "Unknown error",
                        )

                    # Refresh status after push
                    new_status = await git_manager.refresh_status(project)
                    updated_status = await _build_git_status_response(new_status)
                    commits_pushed = status.commits_ahead

                    return GitSyncResponse(
                        success=True,
                        action="push",
                        message=f"Pushed {commits_pushed} commits",
                        commits_pushed=commits_pushed,
                        updated_status=updated_status,
                    )

                elif action == "commit+push":
                    # Stage, commit, and push
                    result = await git_manager.post_task_sync(
                        project,
                        commit_message="Manual sync from web UI",
                    )

                    if not result.success:
                        return GitSyncResponse(
                            success=False,
                            action="commit+push",
                            message=result.message,
                            error=result.message,
                        )

                    # Refresh status after commit+push
                    new_status = await git_manager.refresh_status(project)
                    updated_status = await _build_git_status_response(new_status)

                    return GitSyncResponse(
                        success=True,
                        action="commit+push",
                        message=f"Committed {result.files_committed} files and pushed",
                        files_committed=result.files_committed or 0,
                        commits_pushed=1,
                        updated_status=updated_status,
                    )

                elif action == "fetch":
                    # Just refresh status (which does a fetch)
                    new_status = await git_manager.refresh_status(project)
                    updated_status = await _build_git_status_response(new_status)

                    return GitSyncResponse(
                        success=True,
                        action="fetch",
                        message="Status refreshed",
                        updated_status=updated_status,
                    )

                else:
                    return GitSyncResponse(
                        success=False,
                        action="none",
                        message=f"Unknown action: {action}",
                        error=f"Unknown action: {action}",
                    )

        except Exception as e:
            logger.error(f"Git sync failed for {project.name}: {e}")
            return GitSyncResponse(
                success=False,
                action=action,
                message="Sync failed",
                error=str(e),
            )

    async def _build_git_status_response(status) -> GitStatusResponse:
        """Helper to build GitStatusResponse from GitStatus model."""
        is_diverged = status.commits_ahead > 0 and status.commits_behind > 0
        needs_pull = status.commits_behind > 0 and status.commits_ahead == 0
        needs_push = status.commits_ahead > 0 and status.commits_behind == 0

        return GitStatusResponse(
            is_git_repo=status.is_git_repo,
            branch=status.branch,
            remote=status.remote,
            remote_url=status.remote_url,
            has_uncommitted=status.has_uncommitted,
            uncommitted_count=status.uncommitted_count,
            commits_ahead=status.commits_ahead,
            commits_behind=status.commits_behind,
            is_diverged=is_diverged,
            needs_pull=needs_pull,
            needs_push=needs_push,
            has_conflicts=status.has_conflicts,
            has_operation_in_progress=(status.is_rebase_in_progress or status.is_merge_in_progress),
            operation_type=(
                "rebase" if status.is_rebase_in_progress else ("merge" if status.is_merge_in_progress else None)
            ),
            last_fetch_at=status.last_fetch_at,
        )

    @app.post("/api/git/refresh-all", response_model=GitRefreshAllResponse)
    async def refresh_all_git_statuses() -> GitRefreshAllResponse:
        """
        Refresh git status for all projects.
        Useful after manual git operations to update the UI state.
        """
        projects = store.list_projects()
        refreshed = 0
        errors: list[str] = []

        for project in projects:
            try:
                await git_manager.refresh_status(project)
                refreshed += 1
            except Exception as e:
                logger.error(f"Failed to refresh git status for {project.name}: {e}")
                errors.append(project.name)

        return GitRefreshAllResponse(
            projects_refreshed=refreshed,
            errors=errors,
        )

    # ========== Notification Endpoints ==========

    def _notification_to_response(n: Notification) -> NotificationResponse:
        return NotificationResponse(
            id=n.id,
            workspace_id=n.workspace_id,
            project_id=n.project_id,
            run_id=n.run_id,
            session_id=n.session_id,
            type=n.type.value,
            severity=n.severity.value,
            title=n.title,
            message=n.message,
            metadata=n.metadata,
            read=n.read,
            created_at=n.created_at.isoformat(),
            read_at=n.read_at.isoformat() if n.read_at else None,
        )

    @app.get("/api/notifications", response_model=NotificationsListResponse)
    async def list_notifications(
        workspace_id: str | None = None,
        unread_only: bool = False,
        limit: int = Query(default=50, le=200),
    ) -> NotificationsListResponse:
        """List notifications with optional filters."""
        notifications = store.list_notifications(
            workspace_id=workspace_id,
            unread_only=unread_only,
            limit=limit,
        )
        unread_count = store.get_unread_count(workspace_id=workspace_id)
        return NotificationsListResponse(
            notifications=[_notification_to_response(n) for n in notifications],
            unread_count=unread_count,
        )

    @app.post("/api/notifications/{notification_id}/read", response_model=NotificationResponse)
    async def mark_notification_read(notification_id: str) -> NotificationResponse:
        """Mark a single notification as read."""
        notification = store.mark_notification_read(notification_id)
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        return _notification_to_response(notification)

    @app.post("/api/notifications/read-all")
    async def mark_all_notifications_read(
        workspace_id: str | None = None,
    ) -> dict:
        """Mark all notifications as read."""
        count = store.mark_all_notifications_read(workspace_id=workspace_id)
        return {"marked_read": count}

    @app.delete("/api/notifications")
    async def delete_all_notifications() -> dict:
        """Delete all notifications."""
        count = store.delete_all_notifications()
        return {"deleted": count}

    # ========== Activity Log Endpoints ==========

    @app.get("/api/activity", response_model=ActivityListResponse)
    async def list_activity(
        actor: str | None = None,
        action: str | None = None,
        since: str | None = None,
        limit: int = Query(default=50, le=200),
    ) -> ActivityListResponse:
        """List activity events with optional filters."""
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid 'since' datetime format")

        events = store.list_activities(actor=actor, action=action, since=since_dt, limit=limit)
        return ActivityListResponse(
            events=[
                ActivityEventResponse(
                    id=e.id,
                    timestamp=e.timestamp.isoformat(),
                    actor=e.actor,
                    action=e.action,
                    result=e.result,
                    message=e.message,
                    metadata=e.metadata,
                )
                for e in events
            ],
            total=len(events),
        )

    @app.post("/api/activity/cleanup")
    async def cleanup_activity(days: int = Query(default=90, ge=1)) -> dict:
        """Delete activity events older than N days."""
        deleted = store.cleanup_activities(days=days)
        return {"deleted": deleted}

    # ========== Work Queue Endpoints ==========

    @app.get("/api/queue", response_model=WorkQueueListResponse)
    async def list_queue(
        project_id: str | None = None,
        status: str | None = None,
        limit: int = Query(default=20, le=100),
    ) -> WorkQueueListResponse:
        """List work queue items with optional filters."""
        items = store.list_work_items(project_id=project_id, status=status, limit=limit)
        return WorkQueueListResponse(
            items=[
                WorkQueueItemResponse(
                    id=item.id,
                    project_id=item.project_id,
                    prompt=item.prompt,
                    profile=item.profile,
                    priority=item.priority,
                    status=item.status.value,
                    claimed_by=item.claimed_by,
                    created_at=item.created_at.isoformat(),
                    claimed_at=item.claimed_at.isoformat() if item.claimed_at else None,
                    completed_at=item.completed_at.isoformat() if item.completed_at else None,
                    error_message=item.error_message,
                )
                for item in items
            ],
            total=len(items),
        )

    @app.post("/api/queue", response_model=WorkQueueItemResponse)
    async def add_to_queue(req: WorkQueueAddRequest) -> WorkQueueItemResponse:
        """Add a new item to the work queue."""
        item = store.enqueue_work(
            project_id=req.project_id,
            prompt=req.prompt,
            profile=req.profile,
            priority=req.priority,
        )
        return WorkQueueItemResponse(
            id=item.id,
            project_id=item.project_id,
            prompt=item.prompt,
            profile=item.profile,
            priority=item.priority,
            status=item.status.value,
            claimed_by=item.claimed_by,
            created_at=item.created_at.isoformat(),
            claimed_at=None,
            completed_at=None,
            error_message=None,
        )

    @app.post("/api/queue/{item_id}/cancel", response_model=WorkQueueItemResponse)
    async def cancel_queue_item(item_id: str) -> WorkQueueItemResponse:
        """Cancel a work queue item."""
        from gluon.models import WorkQueueStatus, utc_now

        items = store.list_work_items()
        item = next((i for i in items if i.id == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Work queue item not found")
        if item.status not in (WorkQueueStatus.PENDING, WorkQueueStatus.CLAIMED):
            raise HTTPException(status_code=400, detail=f"Cannot cancel item in {item.status.value} status")

        item.status = WorkQueueStatus.CANCELLED
        item.completed_at = utc_now()
        store.update_work_item(item)

        return WorkQueueItemResponse(
            id=item.id,
            project_id=item.project_id,
            prompt=item.prompt,
            profile=item.profile,
            priority=item.priority,
            status=item.status.value,
            claimed_by=item.claimed_by,
            created_at=item.created_at.isoformat(),
            claimed_at=item.claimed_at.isoformat() if item.claimed_at else None,
            completed_at=item.completed_at.isoformat() if item.completed_at else None,
            error_message=item.error_message,
        )

    @app.post("/api/queue/{item_id}/release", response_model=WorkQueueItemResponse)
    async def release_queue_item(item_id: str) -> WorkQueueItemResponse:
        """Release a claimed work queue item back to pending."""
        from gluon.models import WorkQueueStatus

        items = store.list_work_items()
        item = next((i for i in items if i.id == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Work queue item not found")
        if item.status != WorkQueueStatus.CLAIMED:
            raise HTTPException(status_code=400, detail="Can only release claimed items")

        item.status = WorkQueueStatus.PENDING
        item.claimed_by = None
        item.claimed_at = None
        store.update_work_item(item)

        return WorkQueueItemResponse(
            id=item.id,
            project_id=item.project_id,
            prompt=item.prompt,
            profile=item.profile,
            priority=item.priority,
            status=item.status.value,
            claimed_by=item.claimed_by,
            created_at=item.created_at.isoformat(),
            claimed_at=None,
            completed_at=None,
            error_message=item.error_message,
        )

    # ========== Merge Queue Endpoints ==========

    @app.get("/api/merge-queue", response_model=MergeQueueListResponse)
    async def list_merge_queue(
        status: str | None = None,
        limit: int = Query(default=20, le=100),
    ) -> MergeQueueListResponse:
        """List merge queue entries with optional filters."""
        entries = store.list_merge_entries(status=status, limit=limit)
        return MergeQueueListResponse(
            entries=[
                MergeQueueEntryResponse(
                    id=e.id,
                    run_id=e.run_id,
                    project_id=e.project_id,
                    branch_name=e.branch_name,
                    pr_number=e.pr_number,
                    pr_url=e.pr_url,
                    status=e.status.value,
                    priority=e.priority,
                    conflict_count=e.conflict_count,
                    max_retries=e.max_retries,
                    last_error=e.last_error,
                    created_at=e.created_at.isoformat(),
                    completed_at=e.completed_at.isoformat() if e.completed_at else None,
                )
                for e in entries
            ],
            total=len(entries),
        )

    @app.post("/api/merge-queue/{entry_id}/retry", response_model=MergeQueueEntryResponse)
    async def retry_merge(entry_id: str) -> MergeQueueEntryResponse:
        """Retry a failed/conflicted merge queue entry."""
        from gluon.models import MergeQueueStatus

        entry = store.get_merge_entry(entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Merge queue entry not found")
        if entry.status not in (MergeQueueStatus.CONFLICT, MergeQueueStatus.FAILED):
            raise HTTPException(status_code=400, detail=f"Cannot retry entry in {entry.status.value} status")

        entry.status = MergeQueueStatus.PENDING
        entry.conflict_count = 0
        entry.last_error = None
        entry.completed_at = None
        store.update_merge_entry(entry)

        return MergeQueueEntryResponse(
            id=entry.id,
            run_id=entry.run_id,
            project_id=entry.project_id,
            branch_name=entry.branch_name,
            pr_number=entry.pr_number,
            pr_url=entry.pr_url,
            status=entry.status.value,
            priority=entry.priority,
            conflict_count=entry.conflict_count,
            max_retries=entry.max_retries,
            last_error=entry.last_error,
            created_at=entry.created_at.isoformat(),
            completed_at=None,
        )

    @app.post("/api/merge-queue/{entry_id}/cancel", response_model=MergeQueueEntryResponse)
    async def cancel_merge(entry_id: str) -> MergeQueueEntryResponse:
        """Cancel a merge queue entry."""
        from gluon.models import MergeQueueStatus, utc_now

        entry = store.get_merge_entry(entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Merge queue entry not found")
        if entry.status in (MergeQueueStatus.MERGED, MergeQueueStatus.CANCELLED):
            raise HTTPException(status_code=400, detail=f"Cannot cancel entry in {entry.status.value} status")

        entry.status = MergeQueueStatus.CANCELLED
        entry.completed_at = utc_now()
        store.update_merge_entry(entry)

        return MergeQueueEntryResponse(
            id=entry.id,
            run_id=entry.run_id,
            project_id=entry.project_id,
            branch_name=entry.branch_name,
            pr_number=entry.pr_number,
            pr_url=entry.pr_url,
            status=entry.status.value,
            priority=entry.priority,
            conflict_count=entry.conflict_count,
            max_retries=entry.max_retries,
            last_error=entry.last_error,
            created_at=entry.created_at.isoformat(),
            completed_at=entry.completed_at.isoformat() if entry.completed_at else None,
        )

    # ========== Witness Endpoints ==========

    # ========== Approval Gate Endpoints (Theme D1) ==========

    def _approval_to_dict(approval) -> dict[str, Any]:
        """Serialize a PendingApproval for API responses."""
        return {
            "id": approval.id,
            "run_id": approval.run_id,
            "tool_name": approval.tool_name,
            "tool_input": approval.tool_input,
            "tool_use_id": approval.tool_use_id,
            "classification_reason": approval.classification_reason,
            "status": approval.status.value,
            "decision_reason": approval.decision_reason,
            "decided_by": approval.decided_by,
            "created_at": approval.created_at.isoformat(),
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
            "timeout_at": approval.timeout_at.isoformat() if approval.timeout_at else None,
        }

    @app.get("/api/approvals")
    async def list_approvals_endpoint(
        status: str | None = None,
        run_id: str | None = None,
        limit: int = Query(default=50, le=200),
    ) -> dict[str, Any]:
        """List pending approvals, optionally filtered by status or run_id.

        Common usage: GET /api/approvals?status=pending — shows what needs attention.
        """
        from gluon.models import ApprovalStatus

        resolved_status: ApprovalStatus | None = None
        if status:
            try:
                resolved_status = ApprovalStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status}. Must be one of {[s.value for s in ApprovalStatus]}",
                )
        approvals = store.list_approvals(run_id=run_id, status=resolved_status, limit=limit)
        return {
            "approvals": [_approval_to_dict(a) for a in approvals],
            "total": len(approvals),
        }

    @app.get("/api/approvals/{approval_id}")
    async def get_approval_endpoint(approval_id: str) -> dict[str, Any]:
        """Get detail for a single approval."""
        approval = store.get_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")
        return _approval_to_dict(approval)

    @app.post("/api/approvals/{approval_id}/grant")
    async def grant_approval_endpoint(
        approval_id: str,
        body: dict[str, Any] | None = None,
        user: UserModel = Depends(current_user_dep),  # type: ignore[arg-type]
    ) -> dict[str, Any]:
        """Grant an approval — the waiting hook will unblock and allow the tool call.

        D5 Phase 2 attribution: the approval's ``decided_by_user_id`` is set to
        the current user's ID when auth is enabled. ``decided_by`` remains a
        free-form string like "web" for cross-surface compatibility.
        """
        from gluon.models import ApprovalStatus

        approval = store.get_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")
        if approval.status != ApprovalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Approval already {approval.status.value}",
            )
        reason = (body or {}).get("reason") if body else None
        decided_by = (body or {}).get("decided_by", "web") if body else "web"
        attribution_user_id = user.id if user.id != SYSTEM_USER.id else None
        updated = store.decide_approval(
            approval_id,
            status=ApprovalStatus.GRANTED,
            decided_by=decided_by,
            decided_by_user_id=attribution_user_id,
            decision_reason=reason,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Approval vanished")
        return _approval_to_dict(updated)

    @app.post("/api/approvals/{approval_id}/deny")
    async def deny_approval_endpoint(
        approval_id: str,
        body: dict[str, Any] | None = None,
        user: UserModel = Depends(current_user_dep),  # type: ignore[arg-type]
    ) -> dict[str, Any]:
        """Deny an approval — the waiting hook will return `permissionDecision: deny`."""
        from gluon.models import ApprovalStatus

        approval = store.get_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")
        if approval.status != ApprovalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Approval already {approval.status.value}",
            )
        reason = (body or {}).get("reason") if body else None
        decided_by = (body or {}).get("decided_by", "web") if body else "web"
        attribution_user_id = user.id if user.id != SYSTEM_USER.id else None
        updated = store.decide_approval(
            approval_id,
            status=ApprovalStatus.DENIED,
            decided_by=decided_by,
            decided_by_user_id=attribution_user_id,
            decision_reason=reason,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Approval vanished")
        return _approval_to_dict(updated)

    @app.get("/api/runs/{run_id}/witness", response_model=WitnessDecisionListResponse)
    async def get_witness_decisions(run_id: str) -> WitnessDecisionListResponse:
        """Get witness health decisions for a run."""
        decisions = store.list_witness_decisions(run_id=run_id)
        return WitnessDecisionListResponse(
            run_id=run_id,
            decisions=[
                WitnessDecisionResponse(
                    id=d.id,
                    run_id=d.run_id,
                    timestamp=d.timestamp.isoformat(),
                    classification=d.classification.value,
                    confidence=d.confidence,
                    reasoning=d.reasoning,
                    action=d.action.value,
                    action_result=d.action_result,
                )
                for d in decisions
            ],
        )

    # ========== Formula Endpoints ==========

    @app.get("/api/formulas", response_model=FormulaListResponse)
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

    @app.get("/api/formulas/{name}", response_model=FormulaTemplateResponse)
    async def get_formula(name: str) -> FormulaTemplateResponse:
        """Get a specific formula template by name."""
        from gluon.formulas import FormulaLoader

        template = FormulaLoader.load(name)
        if not template:
            raise HTTPException(status_code=404, detail=f"Formula '{name}' not found")

        return FormulaTemplateResponse(
            name=template.name,
            description=template.description,
            variables=[
                FormulaVariableResponse(name=v.name, type=v.type, required=v.required, default=v.default, help=v.help)
                for v in template.variables
            ],
            steps=[
                FormulaStepResponse(id=s.id, name=s.name, prompt=s.prompt, depends_on=s.depends_on, profile=s.profile)
                for s in template.steps
            ],
            use_worktree=template.use_worktree,
        )

    @app.post("/api/formulas/{name}/run", response_model=FormulaRunResponse)
    async def run_formula(name: str, req: FormulaRunRequest) -> FormulaRunResponse:
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

    @app.post("/api/formulas/validate")
    async def validate_formula_endpoint(template: dict) -> dict:
        """Validate a formula template definition."""
        from gluon.formulas import FormulaStepDef, FormulaTemplate, FormulaVariable, validate_formula

        try:
            variables = [FormulaVariable(**v) for v in template.get("variables", [])]
            steps = [FormulaStepDef(**s) for s in template.get("steps", [])]
            ft = FormulaTemplate(
                name=template.get("name", "unnamed"),
                description=template.get("description"),
                variables=variables,
                steps=steps,
                use_worktree=template.get("use_worktree", True),
            )
            errors = validate_formula(ft)
            return {"valid": len(errors) == 0, "errors": errors}
        except Exception as e:
            return {"valid": False, "errors": [str(e)]}

    # ==============================================================
    # OrchestratorTask endpoints (Theme B Phase 3)
    # ==============================================================

    def _task_to_response(task: OrchestratorTask, agent_name_cache: dict[str, str | None]) -> TaskResponse:
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

    def _comment_to_response(comment: TaskComment) -> TaskCommentResponse:
        return TaskCommentResponse(
            id=comment.id,
            task_id=comment.task_id,
            author_agent_id=comment.author_agent_id,
            author_label=comment.author_label,
            content=comment.content,
            created_at=comment.created_at.isoformat(),
        )

    def _validate_task_status(status: str) -> TaskStatus:
        """Convert a string to TaskStatus or raise 422."""
        try:
            return TaskStatus(status)
        except ValueError:
            valid = ", ".join(s.value for s in TaskStatus)
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {valid}",
            ) from None

    def _resolve_task_ref_or_404(task_id: str) -> OrchestratorTask:
        """Load a task by ID or 8-char prefix; 404 if not found."""
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return task

    def _resolve_agent_for_task(task: OrchestratorTask, agent_ref: str | None) -> str | None:
        """Resolve an agent reference in the context of a task's workspace.

        Returns the resolved agent_id, or None if agent_ref is None.
        Raises HTTPException on lookup failures.
        """
        from gluon.core import AgentAmbiguousError, AgentNotFoundError

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

    @app.get("/api/tasks", response_model=TaskListResponse)
    async def list_tasks_endpoint(
        project_id: str | None = None,
        agent_id: str | None = Query(default=None, description="Agent name or ID for filtering"),
        status: str | None = None,
        limit: int = 100,
    ) -> TaskListResponse:
        """List tasks with optional filters."""
        resolved_agent_id: str | None = None
        if agent_id:
            # Allow name-or-id filtering; scope to project's workspace if provided
            from gluon.core import AgentAmbiguousError, AgentNotFoundError

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
            status_enum = _validate_task_status(status)

        tasks = store.list_tasks(
            project_id=project_id,
            agent_id=resolved_agent_id,
            status=status_enum,
            limit=limit,
        )
        agent_name_cache: dict[str, str | None] = {}
        responses = [_task_to_response(t, agent_name_cache) for t in tasks]
        return TaskListResponse(tasks=responses, total=len(responses))

    @app.post("/api/tasks", response_model=TaskResponse)
    async def create_task_endpoint(
        body: TaskCreateRequest,
        user: UserModel = Depends(current_user_dep),  # type: ignore[arg-type]
    ) -> TaskResponse:
        """Create a new orchestrator task.

        D5 Phase 2 attribution: the task's ``created_by_user_id`` is set to the
        current user's ID when auth is enabled.
        """
        from gluon.core import AgentAmbiguousError, AgentNotFoundError

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
        return _task_to_response(task, {})

    @app.get("/api/tasks/{task_id}", response_model=TaskResponse)
    async def get_task_endpoint(task_id: str) -> TaskResponse:
        """Get a task by ID or 8-char prefix."""
        task = _resolve_task_ref_or_404(task_id)
        return _task_to_response(task, {})

    @app.patch("/api/tasks/{task_id}", response_model=TaskResponse)
    async def update_task_endpoint(task_id: str, body: TaskUpdateRequest) -> TaskResponse:
        """Update task fields (title, description, priority, status, assigned_files)."""
        task = _resolve_task_ref_or_404(task_id)

        if body.title is not None:
            if not body.title.strip():
                raise HTTPException(status_code=422, detail="Title cannot be empty")
            task.title = body.title
        if body.description is not None:
            task.description = body.description
        if body.priority is not None:
            task.priority = body.priority
        if body.status is not None:
            new_status = _validate_task_status(body.status)
            task.status = new_status
            if new_status == TaskStatus.DONE and task.completed_at is None:
                from gluon.models import utc_now

                task.completed_at = utc_now()
        if body.assigned_files is not None:
            task.assigned_files = body.assigned_files

        store.update_task(task)
        refreshed = store.get_task(task.id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail=f"Task vanished after update: {task_id}")
        return _task_to_response(refreshed, {})

    @app.delete("/api/tasks/{task_id}")
    async def delete_task_endpoint(task_id: str) -> dict[str, Any]:
        """Delete a task and all its comments."""
        task = _resolve_task_ref_or_404(task_id)
        deleted = store.delete_task(task.id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return {"deleted": True, "task_id": task.id}

    @app.post("/api/tasks/{task_id}/assign", response_model=TaskResponse)
    async def assign_task_endpoint(task_id: str, body: TaskAssignRequest) -> TaskResponse:
        """Assign a task to an agent (moves BACKLOG → ASSIGNED)."""
        task = _resolve_task_ref_or_404(task_id)
        agent_id = _resolve_agent_for_task(task, body.agent)
        if agent_id is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {body.agent}")

        task.assigned_agent_id = agent_id
        if task.status == TaskStatus.BACKLOG:
            task.status = TaskStatus.ASSIGNED
        store.update_task(task)

        refreshed = store.get_task(task.id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail=f"Task vanished after assign: {task_id}")
        return _task_to_response(refreshed, {})

    @app.post("/api/tasks/{task_id}/done", response_model=TaskResponse)
    async def done_task_endpoint(task_id: str) -> TaskResponse:
        """Mark a task as DONE and release any execution lock."""
        from gluon.core import TaskNotFoundError

        task = _resolve_task_ref_or_404(task_id)
        try:
            released = store.release_task(task.id, TaskStatus.DONE)
        except TaskNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        return _task_to_response(released, {})

    @app.post("/api/tasks/{task_id}/cancel", response_model=TaskResponse)
    async def cancel_task_endpoint(task_id: str) -> TaskResponse:
        """Mark a task as CANCELLED and release any execution lock."""
        from gluon.core import TaskNotFoundError

        task = _resolve_task_ref_or_404(task_id)
        try:
            released = store.release_task(task.id, TaskStatus.CANCELLED)
        except TaskNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        return _task_to_response(released, {})

    @app.post("/api/tasks/{task_id}/review", response_model=TaskResponse)
    async def review_task_endpoint(task_id: str) -> TaskResponse:
        """Mark a task as REVIEW and release any execution lock."""
        from gluon.core import TaskNotFoundError

        task = _resolve_task_ref_or_404(task_id)
        try:
            released = store.release_task(task.id, TaskStatus.REVIEW)
        except TaskNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        return _task_to_response(released, {})

    @app.get("/api/tasks/{task_id}/comments", response_model=TaskCommentListResponse)
    async def list_task_comments_endpoint(task_id: str) -> TaskCommentListResponse:
        """List comments on a task (oldest first)."""
        task = _resolve_task_ref_or_404(task_id)
        comments = store.list_task_comments(task.id)
        responses = [_comment_to_response(c) for c in comments]
        return TaskCommentListResponse(comments=responses, total=len(responses))

    @app.post("/api/tasks/{task_id}/comments", response_model=TaskCommentResponse)
    async def add_task_comment_endpoint(task_id: str, body: TaskCommentRequest) -> TaskCommentResponse:
        """Append a comment to a task."""
        task = _resolve_task_ref_or_404(task_id)
        comment = store.add_task_comment(
            task.id,
            content=body.content,
            author_label=body.author_label or "web",
        )
        return _comment_to_response(comment)

    @app.get("/api/agents/{agent_id}/inbox", response_model=TaskListResponse)
    async def agent_inbox_endpoint(
        agent_id: str,
        limit: int = 20,
    ) -> TaskListResponse:
        """Return ASSIGNED and IN_PROGRESS tasks for an agent, priority-ordered."""
        from gluon.core import AgentAmbiguousError, AgentNotFoundError

        try:
            resolved_id = orchestrator.resolve_agent(agent_id, workspace_id=None)
        except AgentNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        except AgentAmbiguousError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

        if resolved_id is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        tasks = store.get_agent_inbox(resolved_id, limit=limit)
        agent_name_cache: dict[str, str | None] = {}
        responses = [_task_to_response(t, agent_name_cache) for t in tasks]
        return TaskListResponse(tasks=responses, total=len(responses))

    # ========== Task Schedules — user-defined recurring tasks ==========

    def _schedule_to_response(schedule: TaskSchedule) -> TaskScheduleResponse:
        """Build the response payload for a TaskSchedule, denormalizing project_name
        and computing per-schedule run counts + summary."""
        from gluon.recurrence import human_summary

        project_lookup = get_project_lookup()
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

    def _resolve_cron(
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

    @app.get("/api/schedules", response_model=TaskScheduleListResponse)
    async def list_schedules_endpoint(
        project_id: str | None = None,
        include_disabled: bool = True,
    ) -> TaskScheduleListResponse:
        schedules = store.list_task_schedules(project_id=project_id, include_disabled=include_disabled)
        return TaskScheduleListResponse(
            schedules=[_schedule_to_response(s) for s in schedules],
            total=len(schedules),
        )

    @app.post("/api/schedules", response_model=TaskScheduleResponse)
    async def create_schedule_endpoint(
        body: CreateTaskScheduleRequest,
        user: UserModel = Depends(current_user_dep),  # type: ignore[arg-type]
    ) -> TaskScheduleResponse:
        from gluon.recurrence import compute_next_fire_in_tz

        try:
            project = orchestrator.get_project(body.project_name)
        except ProjectNotFoundError:
            raise HTTPException(status_code=404, detail=f"Project not found: {body.project_name}") from None

        cron = _resolve_cron(body.schedule_cron, body.recurrence_days, body.recurrence_time)

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
        return _schedule_to_response(schedule)

    @app.get("/api/schedules/{schedule_id}", response_model=TaskScheduleResponse)
    async def get_schedule_endpoint(schedule_id: str) -> TaskScheduleResponse:
        s = store.get_task_schedule(schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
        return _schedule_to_response(s)

    @app.patch("/api/schedules/{schedule_id}", response_model=TaskScheduleResponse)
    async def update_schedule_endpoint(
        schedule_id: str,
        body: UpdateTaskScheduleRequest,
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
            new_cron = _resolve_cron(
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
        return _schedule_to_response(s)

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule_endpoint(schedule_id: str) -> dict[str, bool]:
        deleted = store.delete_task_schedule(schedule_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
        return {"deleted": True}

    @app.post("/api/schedules/{schedule_id}/enable", response_model=TaskScheduleResponse)
    async def enable_schedule_endpoint(schedule_id: str) -> TaskScheduleResponse:
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
        return _schedule_to_response(s)

    @app.post("/api/schedules/{schedule_id}/disable", response_model=TaskScheduleResponse)
    async def disable_schedule_endpoint(schedule_id: str) -> TaskScheduleResponse:
        s = store.get_task_schedule(schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
        s.is_enabled = False
        store.update_task_schedule(s)
        return _schedule_to_response(s)

    @app.post("/api/schedules/{schedule_id}/fire", response_model=RunResponse)
    async def fire_schedule_now_endpoint(schedule_id: str) -> RunResponse:
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
        project_lookup = get_project_lookup()
        project_name = project_lookup.get(run.project_id, run.project_id[:8])
        await ws_manager.broadcast_run_update(run, project_name)
        return run_to_response(run, project_lookup)

    @app.get("/api/schedules/{schedule_id}/runs", response_model=list[RunResponse])
    async def list_schedule_runs_endpoint(schedule_id: str, limit: int = 50) -> list[RunResponse]:
        s = store.get_task_schedule(schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
        runs = store.list_runs_for_schedule(schedule_id, limit=limit)
        project_lookup = get_project_lookup()
        return [run_to_response(r, project_lookup) for r in runs]

    @app.post("/api/schedules/preview", response_model=TaskSchedulePreviewResponse)
    async def preview_schedule_endpoint(body: TaskSchedulePreviewRequest) -> TaskSchedulePreviewResponse:
        """Render the cron + the next 5 fire moments for live editor preview.

        Doesn't persist anything — pure projection.
        """
        from gluon.recurrence import human_summary, next_n_fires_in_tz

        cron = _resolve_cron(body.schedule_cron, body.recurrence_days, body.recurrence_time)
        try:
            fires = next_n_fires_in_tz(cron, body.timezone, n=5)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to compute fires: {e!s}") from e
        if body.recurrence_days and body.recurrence_time:
            summary = human_summary(body.recurrence_days, body.recurrence_time, body.timezone)
        else:
            summary = f"Cron: {cron} ({body.timezone})"
        return TaskSchedulePreviewResponse(schedule_cron=cron, summary=summary, next_fires=fires)

    # ========== WebSocket ==========

    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """WebSocket endpoint for real-time updates."""
        # The HTTP auth middleware does not cover the WebSocket scope, so gate
        # the handshake here. No-op in single-user mode.
        if is_auth_enabled():
            from gluon.auth import resolve_session

            session = websocket.cookies.get(SESSION_COOKIE_NAME)
            if not session or resolve_session(store, session) is None:
                await websocket.close(code=1008)  # policy violation
                return

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
    _log_polling_task: asyncio.Task | None = None
    _pr_polling_task: asyncio.Task | None = None
    _worktree_cleanup_task: asyncio.Task | None = None
    _auth_sweep_task: asyncio.Task | None = None
    _health_monitor: object | None = None  # HealthMonitor, optional

    # Track file positions for incremental log reading
    _log_file_positions: dict[str, int] = {}  # run_id -> last byte position
    _progress_file_mtimes: dict[str, float] = {}  # run_id -> last mtime
    _tokens_file_mtimes: dict[str, float] = {}  # run_id -> last mtime

    # Cleanup configuration
    cleanup_interval_seconds = 8 * 60 * 60  # 8 hours
    cleanup_initial_delay_seconds = 300  # 5 minutes after startup

    async def _poll_run_status_changes() -> None:
        """Background task to poll for run status changes and broadcast updates."""
        project_lookup = get_project_lookup()

        while True:
            try:
                # Refresh all running runs
                await asyncio.to_thread(runner.refresh_all_runs)

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
                            logger.debug(
                                f"Broadcast run update: {run.id[:8]} {_last_run_states[run_key]} -> {current_state}"
                            )

                    _last_run_states[run_key] = current_state

                # Clean up old run states (keep last 200)
                if len(_last_run_states) > 200:
                    # Keep only runs we just saw
                    _last_run_states.clear()
                    for run in runs:
                        _last_run_states[run.id] = f"{run.status.value}:{run.pr_status or 'none'}"

                # Refresh project lookup occasionally (new projects)
                project_lookup = get_project_lookup()

            except Exception as e:
                logger.error(f"Error in run status polling: {e}")

            # Poll every 2 seconds
            await asyncio.sleep(2)

    async def _poll_log_updates() -> None:
        """Background task to poll log files for new content and stream to WebSocket subscribers.

        Only polls runs that have active WebSocket subscribers, minimizing I/O.
        Reads messages.jsonl incrementally and broadcasts new lines.
        Also checks progress.json and tokens.json for updates.
        """
        while True:
            try:
                # Only poll runs with active subscribers
                subscribed_runs = list(ws_manager.log_subscriptions.keys())

                for run_id in subscribed_runs:
                    log_dir = runner.get_log_path(run_id)
                    if not log_dir or not log_dir.exists():
                        continue

                    # 1. Poll messages.jsonl for new agent messages
                    messages_path = log_dir / "messages.jsonl"
                    if messages_path.exists():
                        current_size = messages_path.stat().st_size

                        # Initialize position to current size for NEW subscriptions
                        # This prevents re-streaming messages that were already fetched via HTTP
                        # (fixes duplicate messages bug when resuming runs)
                        if run_id not in _log_file_positions:
                            _log_file_positions[run_id] = current_size
                            logger.debug(f"Initialized log position for {run_id[:8]} at {current_size} bytes")

                        last_pos = _log_file_positions[run_id]

                        if current_size > last_pos:
                            try:
                                with open(messages_path) as f:
                                    f.seek(last_pos)
                                    for line in f:
                                        if line.strip():
                                            try:
                                                msg = json.loads(line)
                                                await ws_manager.stream_agent_message(run_id, msg)
                                            except json.JSONDecodeError:
                                                pass  # Skip malformed lines
                                    _log_file_positions[run_id] = f.tell()
                            except Exception as e:
                                logger.debug(f"Error reading messages.jsonl for {run_id[:8]}: {e}")

                    # 2. Poll progress.json for progress updates
                    progress_path = log_dir / "progress.json"
                    if progress_path.exists():
                        try:
                            current_mtime = progress_path.stat().st_mtime
                            last_mtime = _progress_file_mtimes.get(run_id, 0)

                            if current_mtime > last_mtime:
                                progress = json.loads(progress_path.read_text())
                                await ws_manager.stream_progress(
                                    run_id,
                                    turns=progress.get("turns", 0),
                                    tool_calls=progress.get("tool_calls", 0),
                                    elapsed_seconds=progress.get("elapsed_seconds", 0),
                                )
                                _progress_file_mtimes[run_id] = current_mtime
                        except Exception as e:
                            logger.debug(f"Error reading progress.json for {run_id[:8]}: {e}")

                    # 3. Poll tokens.json for token/cost updates
                    tokens_path = log_dir / "tokens.json"
                    if tokens_path.exists():
                        try:
                            current_mtime = tokens_path.stat().st_mtime
                            last_mtime = _tokens_file_mtimes.get(run_id, 0)

                            if current_mtime > last_mtime:
                                tokens = json.loads(tokens_path.read_text())
                                await ws_manager.stream_token_update(
                                    run_id,
                                    input_tokens=tokens.get("input_tokens", 0),
                                    output_tokens=tokens.get("output_tokens", 0),
                                    estimated_cost_usd=tokens.get("estimated_cost_usd", 0),
                                    context_used=tokens.get("context_used"),
                                    context_window=tokens.get("context_window"),
                                    cache_read=tokens.get("cache_read", 0),
                                    cache_create=tokens.get("cache_create", 0),
                                    model=tokens.get("model"),
                                )
                                _tokens_file_mtimes[run_id] = current_mtime
                        except Exception as e:
                            logger.debug(f"Error reading tokens.json for {run_id[:8]}: {e}")

                # Cleanup tracking for unsubscribed runs
                active_subs = set(ws_manager.log_subscriptions.keys())
                for run_id in list(_log_file_positions.keys()):
                    if run_id not in active_subs:
                        _log_file_positions.pop(run_id, None)
                        _progress_file_mtimes.pop(run_id, None)
                        _tokens_file_mtimes.pop(run_id, None)

            except Exception as e:
                logger.error(f"Error in log polling: {e}")

            # Poll every 100ms for responsive streaming
            await asyncio.sleep(0.1)

    async def _poll_pr_status_changes() -> None:
        """Background task to poll GitHub PR status, comments, and CI failures.

        Checks runs with open PRs every 60 seconds for:
        1. @gluon or /gluon comments -> auto-resume to address feedback
        2. CI failures (Vercel, build, deploy) -> auto-resume to fix issues
        3. Merged PRs -> transition to COMPLETED

        Supports both REVIEW and COMPLETED status runs with open PRs.
        """
        from gluon.git_manager import GitManager
        from gluon.models import RunStatus
        from gluon.pr_monitor import PRMonitorService

        pr_git_manager = GitManager(store)
        pr_monitor = PRMonitorService(store, runner, pr_git_manager)
        project_lookup = get_project_lookup()

        while True:
            try:
                # Find runs with open PRs (REVIEW or COMPLETED status)
                runs = store.list_runs(limit=100, include_archived=False)
                runs_with_open_prs = [r for r in runs if pr_monitor.should_monitor_run(r) and r.branch_name]

                for run in runs_with_open_prs:
                    try:
                        project = store.get_project(run.project_id)
                        if not project:
                            continue

                        project_name = project_lookup.get(run.project_id, run.project_id[:8])

                        # 1. Check for new @gluon comments
                        triggered_comment = await pr_monitor.check_pr_comments(run)
                        if triggered_comment:
                            # Post "Addressing feedback..." comment
                            author = triggered_comment.get("author", "reviewer")
                            await pr_monitor.post_pr_comment(run, f"Addressing feedback from @{author}...")
                            # Auto-resume to address the comment
                            updated_run = await pr_monitor.auto_resume_for_comment(run, triggered_comment)
                            if updated_run:
                                await ws_manager.broadcast_run_update(updated_run, project_name)
                            continue

                        # 2. Poll CI check status and persist it
                        if run.git_commit_sha:
                            all_checks = await pr_git_manager.get_check_runs(project.expanded_path, run.git_commit_sha)
                            if all_checks:
                                has_pending = any(c.get("status") != "completed" for c in all_checks)
                                has_failure = any(
                                    c.get("status") == "completed" and c.get("conclusion") in ("failure", "timed_out")
                                    for c in all_checks
                                )
                                new_ci = "failure" if has_failure else ("pending" if has_pending else "success")
                            else:
                                new_ci = None
                            if new_ci != run.ci_status:
                                run.ci_status = new_ci
                                store.update_run(run)
                                await ws_manager.broadcast_run_update(run, project_name)

                        # 2b. Auto-resume on CI failures (existing behavior)
                        ci_failures = await pr_monitor.check_ci_failures(run)
                        if ci_failures:
                            failure_names = ", ".join(f.get("name", "unknown") for f in ci_failures[:3])
                            await pr_monitor.post_pr_comment(
                                run, f"Detected CI failures ({failure_names}). Investigating..."
                            )
                            updated_run = await pr_monitor.auto_resume_for_ci_failure(run, ci_failures)
                            if updated_run:
                                await ws_manager.broadcast_run_update(updated_run, project_name)
                            continue

                        # 3. Check if PR was merged (existing logic)
                        pr_info = await pr_git_manager._get_pr_info(project.expanded_path, run.branch_name)

                        if pr_info and pr_info.get("status") == "merged":
                            # PR was merged - transition to COMPLETED
                            logger.info(f"PR #{run.pr_number} merged - transitioning run {run.id[:8]} to COMPLETED")
                            run.pr_status = "merged"
                            run.status = RunStatus.COMPLETED
                            store.update_run(run)
                            await ws_manager.broadcast_run_update(run, project_name)

                        elif pr_info and pr_info.get("status") == "closed":
                            # PR was closed without merge - just update pr_status
                            if run.pr_status != "closed":
                                run.pr_status = "closed"
                                store.update_run(run)
                                await ws_manager.broadcast_run_update(run, project_name)

                    except Exception as e:
                        logger.debug(f"Error checking PR for run {run.id[:8]}: {e}")

                # Refresh project lookup occasionally
                project_lookup = get_project_lookup()

            except Exception as e:
                logger.error(f"Error in PR status polling: {e}")

            # Poll every 60 seconds (GitHub API rate limiting consideration)
            await asyncio.sleep(60)

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
                    + stats["completed_deleted"]
                )
                if total > 0 or stats["errors"] > 0:
                    logger.info(
                        f"Log cleanup complete: {stats['orphan_deleted']} orphan, "
                        f"{stats['archived_deleted']} archived, "
                        f"{stats['failed_deleted']} failed, "
                        f"{stats['completed_deleted']} completed deleted, "
                        f"{stats['errors']} errors"
                    )
                else:
                    logger.info("Log cleanup complete: no logs to delete")
            except Exception as e:
                logger.error(f"Error in log cleanup task: {e}")

            # Wait for next cleanup cycle
            await asyncio.sleep(cleanup_interval_seconds)

    async def _cleanup_old_worktrees() -> None:
        """Background task to garbage-collect stale Git worktrees.

        Runs alongside log cleanup on the same schedule.
        - Orphan worktrees (no DB record): deleted immediately
        - Merged PRs: deleted immediately
        - Completed/failed/cancelled runs: deleted after retention period
        """
        retention_setting = store.resolve_setting("worktree_retention_days")
        retention_days = int(retention_setting) if retention_setting else 7
        wt_service = WorktreeCleanupService(store=store, retention_days=retention_days)

        # Use same initial delay as log cleanup
        await asyncio.sleep(cleanup_initial_delay_seconds)

        while True:
            try:
                stats = wt_service.cleanup()
                total = stats["orphan_deleted"] + stats["merged_deleted"] + stats["expired_deleted"]
                if total > 0 or stats["errors"] > 0:
                    freed_mb = stats["bytes_freed"] / (1024 * 1024)
                    logger.info(
                        f"Worktree cleanup: {stats['orphan_deleted']} orphan, "
                        f"{stats['merged_deleted']} merged, "
                        f"{stats['expired_deleted']} expired deleted "
                        f"({freed_mb:.1f} MB freed, {stats['errors']} errors)"
                    )
            except Exception as e:
                logger.error(f"Error in worktree cleanup task: {e}")

            await asyncio.sleep(cleanup_interval_seconds)

    async def _sweep_auth_state() -> None:
        """Sweep expired auth artifacts (D5).

        Two store-level helpers were added in Phases 1 and 4 but had no
        scheduler — without this task, expired ``user_sessions`` rows and
        unconsumed-but-expired ``link_codes`` rows accumulate forever.
        Both sweeps are cheap (single DELETE each), so we run them on a
        single shared cadence.

        Tunable via ``GLUON_AUTH_SWEEP_INTERVAL_SECS`` (default 1 hour).
        First pass runs after a short delay so we don't spike at startup
        alongside other heavy tasks.
        """
        sweep_interval = int(os.environ.get("GLUON_AUTH_SWEEP_INTERVAL_SECS", str(60 * 60)))
        await asyncio.sleep(60)  # short startup delay
        logger.info(f"Auth state sweep scheduled every {sweep_interval}s")
        while True:
            try:
                expired_sessions = store.delete_expired_user_sessions()
                expired_codes = store.delete_expired_link_codes()
                # TTL'd chat-bot tables had cleanup helpers but no scheduler —
                # without this they grow unbounded. Same cheap cadence.
                expired_chat = store.cleanup_expired_chat_history()
                expired_maps = store.cleanup_expired_message_run_maps()
                if expired_sessions or expired_codes or expired_chat or expired_maps:
                    logger.info(
                        "Auth/TTL sweep: %d sessions, %d link codes, %d chat-history, %d message-run maps deleted",
                        expired_sessions,
                        expired_codes,
                        expired_chat,
                        expired_maps,
                    )
            except Exception as e:
                logger.error(f"Error in auth/TTL sweep: {e}")
            await asyncio.sleep(sweep_interval)

    @app.on_event("startup")
    async def start_background_tasks() -> None:
        """Start background tasks on app startup."""
        nonlocal _polling_task, _cleanup_task, _log_polling_task, _pr_polling_task
        nonlocal _worktree_cleanup_task, _auth_sweep_task, _health_monitor

        # Start event bus
        from gluon.events import event_bus
        from gluon.events.subscribers import register_subscribers

        await event_bus.start()
        register_subscribers(event_bus, store)

        # Start Redis event transport subscriber — receives events from runner subprocesses
        from gluon.events.redis_transport import RedisEventTransport

        redis_transport = RedisEventTransport()
        try:
            await redis_transport.start_subscriber(event_bus)
            app.state.redis_transport = redis_transport
        except Exception as e:
            logger.warning(f"Redis event transport unavailable (events from subprocesses won't reach UI): {e}")

        _polling_task = asyncio.create_task(_poll_run_status_changes())
        _cleanup_task = asyncio.create_task(_cleanup_old_logs())
        _log_polling_task = asyncio.create_task(_poll_log_updates())
        _pr_polling_task = asyncio.create_task(_poll_pr_status_changes())
        _worktree_cleanup_task = asyncio.create_task(_cleanup_old_worktrees())
        _auth_sweep_task = asyncio.create_task(_sweep_auth_state())

        # Start supervisor for ralph mode auto-resume
        await runner.start_supervisor(poll_interval=30)

        # Start periodic queue drain so pending items dispatch even when
        # no run completes to trigger the self-propelling path
        queue_drain_secs = int(os.environ.get("GLUON_QUEUE_DRAIN_INTERVAL_SECS", "60"))
        await runner.start_queue_drain(interval_secs=queue_drain_secs)

        # Start heartbeat scheduler (Theme B Phase 2)
        # Opt-in via GLUON_HEARTBEAT_ENABLED; default off so fresh installs don't
        # start firing cron-scheduled runs without explicit configuration.
        if os.environ.get("GLUON_HEARTBEAT_ENABLED", "").lower() in ("1", "true", "yes"):
            from gluon.scheduler import HeartbeatScheduler

            heartbeat_poll_secs = int(os.environ.get("GLUON_HEARTBEAT_POLL_SECS", "60"))
            app.state.heartbeat_scheduler = HeartbeatScheduler(
                store=store,
                runner=runner,
                poll_interval_secs=heartbeat_poll_secs,
            )
            await app.state.heartbeat_scheduler.start()
            logger.info("Heartbeat scheduler enabled (poll=%ds)", heartbeat_poll_secs)

        # Start user-facing TaskScheduleManager. ON BY DEFAULT — the dashboard
        # has no other way to fire user schedules, and an empty schedule list
        # makes the loop a near-no-op. Opt out with GLUON_SCHEDULES_DISABLED=1.
        if os.environ.get("GLUON_SCHEDULES_DISABLED", "").lower() not in ("1", "true", "yes"):
            from gluon.task_scheduler import TaskScheduleManager

            sched_poll_secs = int(os.environ.get("GLUON_TASK_SCHEDULES_POLL_SECS", "30"))
            app.state.task_schedule_manager = TaskScheduleManager(
                store=store,
                runner=runner,
                poll_interval_secs=sched_poll_secs,
            )
            await app.state.task_schedule_manager.start()
            logger.info("Task schedule manager enabled (poll=%ds)", sched_poll_secs)

        # Start witness health monitor if enabled
        if os.environ.get("GLUON_WITNESS_ENABLED", "").lower() in ("1", "true", "yes"):
            from gluon.health_monitor import HealthMonitor

            _health_monitor = HealthMonitor(
                store=store,
                log_path=runner.config.log_path,
                notifier=notifier,
                ws_manager=ws_manager,
            )
            await _health_monitor.start()
            logger.info("Witness health monitor enabled")

        logger.info(
            "Started background tasks: run status polling, log streaming, log cleanup, "
            "worktree cleanup, PR status polling, supervision coordinator"
        )

    @app.on_event("shutdown")
    async def stop_background_tasks() -> None:
        """Stop background tasks on app shutdown."""
        # Stop Redis event transport
        redis_transport = getattr(app.state, "redis_transport", None)
        if redis_transport:
            await redis_transport.stop()

        # Stop event bus
        from gluon.events import event_bus

        await event_bus.stop()

        # Stop health monitor
        if _health_monitor and hasattr(_health_monitor, "stop"):
            await _health_monitor.stop()

        # Stop supervisor first
        await runner.stop_supervisor()

        # Stop queue drain
        await runner.stop_queue_drain()

        # Stop heartbeat scheduler if it was started
        heartbeat_scheduler = getattr(app.state, "heartbeat_scheduler", None)
        if heartbeat_scheduler is not None:
            try:
                await heartbeat_scheduler.stop()
            except Exception:
                logger.debug("Heartbeat scheduler stop failed", exc_info=True)

        # Stop user task schedule manager if it was started
        task_schedule_manager = getattr(app.state, "task_schedule_manager", None)
        if task_schedule_manager is not None:
            try:
                await task_schedule_manager.stop()
            except Exception:
                logger.debug("Task schedule manager stop failed", exc_info=True)

        tasks_to_cancel = []
        if _polling_task:
            tasks_to_cancel.append(("run status polling", _polling_task))
        if _log_polling_task:
            tasks_to_cancel.append(("log streaming", _log_polling_task))
        if _cleanup_task:
            tasks_to_cancel.append(("log cleanup", _cleanup_task))
        if _pr_polling_task:
            tasks_to_cancel.append(("PR status polling", _pr_polling_task))
        if _worktree_cleanup_task:
            tasks_to_cancel.append(("worktree cleanup", _worktree_cleanup_task))
        if _auth_sweep_task:
            tasks_to_cancel.append(("auth state sweep", _auth_sweep_task))

        for name, task in tasks_to_cancel:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info(f"Stopped {name} background task")

    # ========== SDK Session Browser ==========

    @app.get("/api/sdk-sessions", response_model=list[SDKSessionResponse])
    async def list_sdk_sessions(
        directory: Annotated[str | None, Query(description="Project directory to filter sessions")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[SDKSessionResponse]:
        """List all Claude SDK sessions from the local filesystem."""
        try:
            from claude_agent_sdk import list_sessions

            sessions = list_sessions(
                directory=directory,
                limit=limit,
                include_worktrees=True,
            )
        except Exception as e:
            logger.warning("Failed to list SDK sessions: %s", e)
            return []

        # Build lookup of claude_session_id -> run_ids for cross-referencing
        session_to_runs: dict[str, list[str]] = {}
        try:
            all_runs = store.list_runs(limit=500)
            for r in all_runs:
                if r.claude_session_id:
                    session_to_runs.setdefault(r.claude_session_id, []).append(r.id)
        except Exception as e:
            logger.warning("Failed to load runs for session cross-reference: %s", e)

        result: list[SDKSessionResponse] = []
        for s in sessions:
            result.append(
                SDKSessionResponse(
                    session_id=s.session_id,
                    summary=s.summary,
                    last_modified=s.last_modified,
                    file_size=s.file_size,
                    custom_title=s.custom_title,
                    first_prompt=s.first_prompt,
                    git_branch=s.git_branch,
                    cwd=s.cwd,
                    linked_run_ids=session_to_runs.get(s.session_id, []),
                )
            )

        return result

    @app.get("/api/sdk-sessions/{session_id}", response_model=SessionDetailResponse)
    async def get_sdk_session(
        session_id: str,
        directory: Annotated[str | None, Query(description="Project directory")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> SessionDetailResponse:
        """Get session detail with messages from Claude SDK."""
        try:
            from claude_agent_sdk import get_session_messages, list_sessions

            # Get session info
            sessions = list_sessions(directory=directory, limit=200, include_worktrees=True)
            session_info = next((s for s in sessions if s.session_id == session_id), None)
            if not session_info:
                raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

            # Get messages
            messages = get_session_messages(
                session_id=session_id,
                directory=directory,
                limit=limit,
                offset=offset,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Failed to get SDK session %s: %s", session_id, e)
            raise HTTPException(status_code=500, detail=f"Failed to read session: {e}")

        # Cross-reference linked runs
        linked_run_ids: list[str] = []
        try:
            all_runs = store.list_runs(limit=500)
            linked_run_ids = [r.id for r in all_runs if r.claude_session_id == session_id]
        except Exception as e:
            logger.warning("Failed to load runs for session cross-reference: %s", e)

        session_resp = SDKSessionResponse(
            session_id=session_info.session_id,
            summary=session_info.summary,
            last_modified=session_info.last_modified,
            file_size=session_info.file_size,
            custom_title=session_info.custom_title,
            first_prompt=session_info.first_prompt,
            git_branch=session_info.git_branch,
            cwd=session_info.cwd,
            linked_run_ids=linked_run_ids,
        )

        message_responses = [
            SessionMessageResponse(
                type=m.type,
                uuid=m.uuid,
                session_id=m.session_id,
                message=m.message,
                parent_tool_use_id=m.parent_tool_use_id,
            )
            for m in messages
        ]

        return SessionDetailResponse(
            session=session_resp,
            messages=message_responses,
            total_messages=len(message_responses),
        )

    # ========== Claude Session Explorer (C4) ==========
    #
    # Expose Claude Code sessions scoped to a registered Gluon project.
    # Read-only. Differs from /api/sdk-sessions (which is global) by always
    # resolving the directory from project.expanded_path.

    def _flatten_claude_message(raw: object) -> str:
        """Flatten a raw Anthropic API message dict into a display string.

        Messages from the SDK are the raw wire-protocol dicts: either a plain
        ``str`` content (older format) or a list of content blocks with
        ``type="text"``/``"tool_use"``/``"tool_result"`` entries. We stringify
        text blocks and leave tool calls out of the preview to keep the
        payload small and readable.
        """
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            content = raw.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_val = block.get("text")
                        if isinstance(text_val, str):
                            parts.append(text_val)
                    elif btype == "tool_use":
                        name = block.get("name") or "tool"
                        parts.append(f"[tool_use: {name}]")
                    elif btype == "tool_result":
                        parts.append("[tool_result]")
                return "\n".join(p for p in parts if p)
        # Unknown shape — stringify defensively.
        return str(raw)

    def _extract_message_timestamp(msg: object) -> str | None:
        """Best-effort timestamp extraction from a SessionMessage's payload."""
        if not msg:
            return None
        raw = getattr(msg, "message", None)
        if isinstance(raw, dict):
            ts = raw.get("timestamp") or raw.get("created_at")
            if isinstance(ts, str):
                return ts
        return None

    @app.get(
        "/api/projects/{project_id}/claude-sessions",
        response_model=ClaudeSessionListResponse,
    )
    async def list_claude_sessions(
        project_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ClaudeSessionListResponse:
        """List Claude Code sessions located under a project directory.

        The project's ``expanded_path`` is passed as the ``directory``
        argument to the SDK so worktree sessions are included.
        """
        project = store.get_project(project_id)
        if not project:
            project = store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        project_dir = str(project.expanded_path)

        try:
            from claude_agent_sdk import list_sessions
        except ImportError as e:
            logger.warning("claude_agent_sdk unavailable: %s", e)
            raise HTTPException(
                status_code=503,
                detail="claude_agent_sdk not installed — session browsing unavailable",
            )

        try:
            sessions = list_sessions(
                directory=project_dir,
                limit=limit,
                offset=offset,
                include_worktrees=True,
            )
        except Exception as e:
            logger.warning("Failed to list Claude sessions for %s: %s", project.name, e)
            return ClaudeSessionListResponse(sessions=[], project_dir=project_dir, total=0)

        items: list[ClaudeSessionInfo] = []
        for s in sessions:
            items.append(
                ClaudeSessionInfo(
                    session_id=s.session_id,
                    summary=s.summary,
                    last_modified_ms=s.last_modified,
                    file_size=s.file_size,
                    tag=getattr(s, "tag", None),
                    created_at_ms=getattr(s, "created_at", None),
                    git_branch=s.git_branch,
                    cwd=s.cwd,
                    first_prompt=s.first_prompt,
                    custom_title=s.custom_title,
                )
            )

        return ClaudeSessionListResponse(
            sessions=items,
            project_dir=project_dir,
            total=len(items),
        )

    @app.get(
        "/api/projects/{project_id}/claude-sessions/{session_id}",
        response_model=ClaudeSessionInfo,
    )
    async def get_claude_session(
        project_id: str,
        session_id: str,
    ) -> ClaudeSessionInfo:
        """Fetch a single Claude Code session's metadata."""
        project = store.get_project(project_id)
        if not project:
            project = store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        project_dir = str(project.expanded_path)

        try:
            from claude_agent_sdk import get_session_info
        except ImportError as e:
            logger.warning("claude_agent_sdk unavailable: %s", e)
            raise HTTPException(
                status_code=503,
                detail="claude_agent_sdk not installed — session browsing unavailable",
            )

        try:
            info = get_session_info(session_id=session_id, directory=project_dir)
        except Exception as e:
            logger.warning("Failed to look up Claude session %s: %s", session_id, e)
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        if info is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        return ClaudeSessionInfo(
            session_id=info.session_id,
            summary=info.summary,
            last_modified_ms=info.last_modified,
            file_size=info.file_size,
            tag=getattr(info, "tag", None),
            created_at_ms=getattr(info, "created_at", None),
            git_branch=info.git_branch,
            cwd=info.cwd,
            first_prompt=info.first_prompt,
            custom_title=info.custom_title,
        )

    @app.get(
        "/api/projects/{project_id}/claude-sessions/{session_id}/messages",
        response_model=ClaudeSessionMessagesResponse,
    )
    async def get_claude_session_messages(
        project_id: str,
        session_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ClaudeSessionMessagesResponse:
        """Return conversation messages for a Claude Code session."""
        project = store.get_project(project_id)
        if not project:
            project = store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        project_dir = str(project.expanded_path)

        try:
            from claude_agent_sdk import get_session_messages
        except ImportError as e:
            logger.warning("claude_agent_sdk unavailable: %s", e)
            raise HTTPException(
                status_code=503,
                detail="claude_agent_sdk not installed — session browsing unavailable",
            )

        try:
            # Pull one extra row to compute `has_more` cheaply.
            batch = get_session_messages(
                session_id=session_id,
                directory=project_dir,
                limit=limit + 1,
                offset=offset,
            )
        except Exception as e:
            logger.warning("Failed to read messages for session %s: %s", session_id, e)
            return ClaudeSessionMessagesResponse(
                session_id=session_id,
                messages=[],
                total=0,
                has_more=False,
            )

        has_more = len(batch) > limit
        visible = batch[:limit]

        items: list[ClaudeSessionMessageItem] = []
        for m in visible:
            items.append(
                ClaudeSessionMessageItem(
                    type=m.type,
                    message=_flatten_claude_message(m.message),
                    timestamp=_extract_message_timestamp(m),
                )
            )

        return ClaudeSessionMessagesResponse(
            session_id=session_id,
            messages=items,
            total=len(items),
            has_more=has_more,
        )

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
                content="<h1>Gluon Web Dashboard</h1><p>Frontend not built. Run: cd web-ui && bun run build</p>",
                status_code=200,
            )

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve SPA for client-side routing."""
            # Skip API routes
            if full_path.startswith("api"):
                raise HTTPException(status_code=404)

            # Sanitise user path to prevent traversal — uses the exact
            # os.path.normpath(os.path.join()) pattern that CodeQL recognises
            # as a safe-access check for py/path-injection.
            base_dir = str(dist_dir)
            file_path = os.path.normpath(os.path.join(base_dir, full_path))
            if not file_path.startswith(base_dir):
                raise HTTPException(status_code=404)
            if os.path.isfile(file_path):
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
cd web-ui && bun dev
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
