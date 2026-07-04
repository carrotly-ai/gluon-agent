"""FastAPI application for Gluon Web Dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

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

from gluon.commands import get_slash_commands
from gluon.core import Orchestrator
from gluon.files import get_project_files
from gluon.models import (
    RunStatus,
    expand_path,
    run_readiness,
    utc_now,
)
from gluon.notifier import NotificationDispatcher
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web import background
from gluon.web.models import (
    # Phase 2: Gastown feature models
    AttachImageRequest,
    BranchListResponse,
    BranchOperationResponse,
    BranchResponse,
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
    CreateWorkspaceRequest,
    FileChangeResponse,
    FileDiffResponse,
    GitRefreshAllResponse,
    GitStatusResponse,
    GitSyncRequest,
    GitSyncResponse,
    ImageResponse,
    LinkCodeResponse,
    LinkStatusResponse,
    ProjectFileResponse,
    ProjectFilesResponse,
    ProjectResponse,
    QueuedMessageResponse,
    RebaseRequest,
    RebaseResponse,
    RecoverRunRequest,
    RecoverRunResponse,
    ResolveConflictRequest,
    ResolveConflictResponse,
    RunCommitsResponse,
    RunDetailResponse,
    RunFilesResponse,
    RunImagesResponse,
    RunResponse,
    ScanResultResponse,
    # SDK Session Browser models
    SlashCommandResponse,
    SlashCommandsResponse,
    TaskListResponse,
    VersionResponse,
    WorkspaceResponse,
    WorkspaceSettingsResponse,
)
from gluon.web.routers import (
    activity,
    approvals,
    auth,
    formulas,
    loops,
    merge_queue,
    notifications,
    projects,
    queued_messages,
    runs,
    schedules,
    sdk_sessions,
    settings,
    supervision,
    system,
    tasks,
    usage,
    users,
    webhooks,
    work_queue,
    workspaces,
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


class _SlidingWindowLimiter:
    """In-memory per-key sliding-window rate limiter.

    Used to blunt brute-force against unauthenticated auth endpoints. Per-app
    (not a global singleton) so test apps don't share state. Adequate for a
    single-process self-hosted dashboard; a multi-replica deployment behind a
    load balancer should rate-limit at the proxy.
    """

    def __init__(self, max_events: int, window_secs: float) -> None:
        self._max = max_events
        self._window = window_secs
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record an attempt for ``key``; return False if it exceeds the budget."""
        t = time.monotonic() if now is None else now
        dq = self._hits[key]
        cutoff = t - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self._max:
            return False
        dq.append(t)
        # Opportunistically drop the bucket if it's the only (now-stale) entry —
        # keeps the dict from growing unbounded across many one-off IPs.
        if not dq:
            self._hits.pop(key, None)
        return True


def create_app(
    store: GluonStore | None = None,
    notifier: NotificationDispatcher | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        store: Optional GluonStore instance. If not provided, creates a new one.
               Useful for testing with custom store configurations.
        notifier: Optional shared NotificationDispatcher. Pass the bot's notifier
               (which holds live transport instances) so web-submitted runs and
               event-bus question escalation reach Telegram/Discord. When omitted,
               a transport-less dispatcher is created and channel delivery no-ops.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Startup/shutdown run the same bodies previously registered via the
        # deprecated @app.on_event hooks (_run_startup / _run_shutdown, defined
        # later in this closure — bound by the time lifespan executes at app
        # startup). Migrated to the FastAPI lifespan API (#162).
        await _run_startup()
        try:
            yield
        finally:
            await _run_shutdown()

    app = FastAPI(
        title="Gluon Web Dashboard",
        description="Web interface for managing Gluon Agent task execution",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # Shared store — created early because the auth middleware below closes over it.
    if store is None:
        store = GluonStore()
    # Expose on app.state so extracted APIRouters can inject it via Depends (#162).
    app.state.store = store
    # Per-domain routers extracted from this closure (#162 STEP B). They stay
    # behind the same fail-closed auth middleware (paths unchanged).
    app.include_router(sdk_sessions.router)
    app.include_router(notifications.router)
    app.include_router(workspaces.router)
    app.include_router(queued_messages.router)
    app.include_router(tasks.router)
    app.include_router(formulas.router)
    app.include_router(schedules.router)
    app.include_router(usage.router)
    app.include_router(approvals.router)
    app.include_router(supervision.router)
    app.include_router(runs.router)
    app.include_router(projects.router)
    app.include_router(system.router)
    app.include_router(activity.router)
    app.include_router(work_queue.router)
    app.include_router(loops.router)
    app.include_router(merge_queue.router)
    app.include_router(settings.router)
    app.include_router(users.router)
    app.include_router(webhooks.router)
    app.include_router(auth.router)

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
        "/api/health",  # container liveness probe — no auth so the healthcheck works
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
        response = await call_next(request)
        # Audit trail: record authorized privileged mutations (operator+) so
        # multi-user actions are attributable. Viewer self-service writes
        # (notifications, own password) are intentionally excluded as noise.
        if request.method in _mutating_methods and _role_rank(required) >= _role_rank(_UserRole.OPERATOR):
            try:
                store.log_activity(
                    actor=user.username,
                    action=f"{request.method} {path}",
                    result=str(response.status_code),
                    metadata={"ip": request.client.host if request.client else None},
                )
            except Exception:
                logger.debug("audit log write failed", exc_info=True)
        return response

    @app.middleware("http")
    async def _security_headers(request, call_next):
        """Attach defensive response headers to every response.

        Declared after the auth gate so it wraps it — the headers land on the
        gate's 401/403 JSON responses too. HSTS is only emitted over HTTPS (it's
        meaningless and can lock out dev on plain HTTP).
        """
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response

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

    notifier = notifier or NotificationDispatcher(store=store)
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

    # Per-IP throttle on the unauthenticated/credential endpoints to blunt
    # password and link-code brute force. Per-app so test apps don't share it.
    _auth_limiter = _SlidingWindowLimiter(max_events=10, window_secs=60.0)
    # Exposed for the extracted auth router's rate_limit_auth dep — same instance,
    # so per-app semantics + per-path budgets are shared with the inline routes.
    app.state.auth_limiter = _auth_limiter

    def _rate_limit_auth(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        if not _auth_limiter.allow(f"{request.url.path}:{ip}"):
            raise HTTPException(status_code=429, detail="Too many attempts; wait a minute and try again.")

    # Build project lookup helper
    def get_project_lookup() -> dict[str, str]:
        """Build project_id → project_name lookup."""
        return {p.id: p.name for p in store.list_projects()}

    def _resolve_run_or_404(run_id: str):
        """Fetch a run by id (short-id aware) or raise 404."""
        run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return run

    def _resolve_project_or_404(project_id: str):
        """Fetch a project by id or name or raise 404."""
        project = store.get_project(project_id) or store.get_project_by_name(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return project

    def _workspace_to_response(ws, project_count: int) -> WorkspaceResponse:
        """Serialize a Workspace to WorkspaceResponse (rolling budgets + current spend)."""
        return WorkspaceResponse(
            id=ws.id,
            name=ws.name,
            path=str(ws.path),
            project_count=project_count,
            auto_discover=ws.auto_discover,
            daily_budget_usd=ws.daily_budget_usd,
            monthly_budget_usd=ws.monthly_budget_usd,
            daily_spend_usd=store.get_workspace_daily_spend(ws.id),
            monthly_spend_usd=store.get_workspace_monthly_spend(ws.id),
        )

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
            # Loop-engineering readiness (I4 warn-only)
            verify_cmd=run.verify_cmd,
            readiness=run_readiness(run.verify_cmd),
        )

    # ========== REST API Routes ==========

    # list_runs (GET /api/runs) moved to gluon.web.routers.runs (#162).
    # get_run stays inline (git/PR refresh + commit/file counts).

    @app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
    async def get_run(run_id: str, refresh_pr: bool = True) -> RunDetailResponse:
        """Get details for a specific run."""
        run = _resolve_run_or_404(run_id)

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
            verify_cmd=run.verify_cmd,
            readiness=run_readiness(run.verify_cmd),
        )

    # create_run (POST /api/runs) moved to gluon.web.routers.runs (#162).

    # cancel/resume run routes moved to gluon.web.routers.runs (#162).
    # recover stays inline (worktree-path + background recovery closure).

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

        # Get the run to recover
        run = _resolve_run_or_404(run_id)

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

                async for item in agent.resume_with_fresh_context(
                    recovery_state=recovery_state,
                    working_dir=working_dir,
                ):
                    item_count += 1
                    from gluon.agent import AgentResult

                    if isinstance(item, AgentResult):
                        result = item
                    else:
                        # Broadcast progress every 5 items
                        if item_count % 5 == 0:
                            target_run.recovery_item_count = item_count
                            store.update_run(target_run)
                            await ws_manager.broadcast_run_update(target_run, project_name)

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
                        logger.info(f"Recovery completed successfully for run {target_run.id}")
                    else:
                        target_run.status = RunStatus.FAILED
                        target_run.error_message = result.error
                        logger.warning(f"Recovery failed for run {target_run.id}: {result.error}")
                else:
                    target_run.status = RunStatus.FAILED
                    target_run.error_message = "Recovery produced no result"
                    logger.error(f"Recovery for run {target_run.id} produced no AgentResult")

                store.update_run(target_run)

                # Broadcast final update
                await ws_manager.broadcast_run_update(target_run, project_name)

            except Exception as e:
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

    # session-history run route moved to gluon.web.routers.runs (#162).

    # AskUserQuestion routes (run questions + answer) moved to gluon.web.routers.runs (#162).

    # Todo / ralph-loop run routes moved to gluon.web.routers.runs (#162).

    # Supervision routes moved to gluon.web.routers.supervision (#162).

    # ========== Git Commits and Files ==========

    @app.get("/api/runs/{run_id}/commits", response_model=RunCommitsResponse)
    async def get_run_commits(run_id: str) -> RunCommitsResponse:
        """
        Get commits on the run's branch since it diverged from the base branch.

        Falls back to snapshots if the branch has been merged or deleted.
        """
        from gluon.git_manager import GitManager

        run = _resolve_run_or_404(run_id)

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

        run = _resolve_run_or_404(run_id)

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

        run = _resolve_run_or_404(run_id)

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

        run = _resolve_run_or_404(run_id)

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

    # logs run route moved to gluon.web.routers.runs (#162).

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

    # status + provider routes moved to gluon.web.routers.system (#162).

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
        get_oidc_provider,
    )

    # auth login/logout/me moved to gluon.web.routers.auth (#162).

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

    # /api/auth/providers moved to gluon.web.routers.auth (#162).

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

    @app.post(
        "/api/auth/link-codes",
        response_model=LinkCodeResponse,
        dependencies=[Depends(_rate_limit_auth)],
    )
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

    # user-management routes moved to gluon.web.routers.users (#162).

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

    # health + global /api/commands moved to gluon.web.routers.system (#162).

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

    # status-transition run route moved to gluon.web.routers.runs (#162).

    # archive/unarchive/patch/snooze/fork run routes moved to gluon.web.routers.runs (#162).

    # attention-counts route moved to gluon.web.routers.system (#162).

    # pr-status run route moved to gluon.web.routers.runs (#162).

    # ========== Phase 7.3: Project Management ==========

    # get_project detail (GET /api/projects/{id}) moved to gluon.web.routers.projects (#162).
    # create_project stays inline (os.path.realpath taint-break — CodeQL recognises it only here).

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

    # delete_project (DELETE /api/projects/{id}) moved to gluon.web.routers.projects (#162).

    # ========== Workspace Management ==========
    #
    # NOTE: create_workspace stays inline (not in web/routers/workspaces.py).
    # Its path validation (os.path.realpath + home-directory containment) is
    # safe, but CodeQL only recognises the realpath taint-break in this module's
    # context; relocating the byte-identical code re-flags it as py/path-injection
    # (#162). Kept here where CodeQL already validates it. The other workspace
    # CRUD routes live in the router.

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

        return _workspace_to_response(workspace, len(projects_added))

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

    # workspace settings/env-vars routes moved to gluon.web.routers.workspaces (#162).

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

    # Usage-dashboard routes moved to gluon.web.routers.usage (#162).

    # settings + vercel-token routes moved to gluon.web.routers.settings (#162).

    # sandbox/status route moved to gluon.web.routers.system (#162).

    # webhook routes moved to gluon.web.routers.webhooks (#162).

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
        run = _resolve_run_or_404(run_id)

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
        run = _resolve_run_or_404(run_id)

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
        run = _resolve_run_or_404(run_id)

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
        run = _resolve_run_or_404(run_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

        result = await git_manager.rebase_abort(project.expanded_path)

        return RebaseResponse(
            success=result["success"],
            message=result["message"],
        )

    @app.get("/api/projects/{project_id}/branches", response_model=BranchListResponse)
    async def list_branches(project_id: str) -> BranchListResponse:
        """
        List all branches in the repository.
        """
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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
        project = _resolve_project_or_404(project_id)

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

    # ========== Activity Log Endpoints ==========

    # activity routes moved to gluon.web.routers.activity (#162).
    # work-queue routes moved to gluon.web.routers.work_queue (#162).

    # merge-queue routes moved to gluon.web.routers.merge_queue (#162).
    # witness route moved to gluon.web.routers.runs (#162).

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

        agent_tasks = store.get_agent_inbox(resolved_id, limit=limit)
        agent_name_cache: dict[str, str | None] = {}
        # task_to_response lives in the tasks router now (shared mapper); the
        # agents domain still resolves through it until it gets its own router.
        responses = [tasks.task_to_response(t, agent_name_cache, store) for t in agent_tasks]
        return TaskListResponse(tasks=responses, total=len(responses))

    # Task-schedule routes moved to gluon.web.routers.schedules (#162).

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

    async def _run_startup() -> None:
        """Start background tasks on app startup."""
        nonlocal _polling_task, _cleanup_task, _log_polling_task, _pr_polling_task
        nonlocal _worktree_cleanup_task, _auth_sweep_task, _health_monitor

        # Start event bus
        from gluon.events import event_bus
        from gluon.events.subscribers import register_subscribers

        await event_bus.start()
        register_subscribers(event_bus, store, app.state.notifier)

        # Start Redis event transport subscriber — receives events from runner subprocesses
        from gluon.events.redis_transport import RedisEventTransport

        redis_transport = RedisEventTransport()
        try:
            await redis_transport.start_subscriber(event_bus)
            app.state.redis_transport = redis_transport
        except Exception as e:
            logger.warning(f"Redis event transport unavailable (events from subprocesses won't reach UI): {e}")

        _polling_task = asyncio.create_task(
            background.poll_run_status_changes(store, runner, get_project_lookup, _last_run_states)
        )
        _cleanup_task = asyncio.create_task(
            background.cleanup_old_logs(store, cleanup_initial_delay_seconds, cleanup_interval_seconds)
        )
        _log_polling_task = asyncio.create_task(
            background.poll_log_updates(runner, _log_file_positions, _progress_file_mtimes, _tokens_file_mtimes)
        )
        _pr_polling_task = asyncio.create_task(background.poll_pr_status_changes(store, runner, get_project_lookup))
        _worktree_cleanup_task = asyncio.create_task(
            background.cleanup_old_worktrees(store, cleanup_initial_delay_seconds, cleanup_interval_seconds)
        )
        _auth_sweep_task = asyncio.create_task(background.sweep_auth_state(store))

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

    async def _run_shutdown() -> None:
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

    # Expose create_app's shared collaborators on app.state so per-domain
    # routers can inject them via Depends (web/routers/_deps.py) instead of
    # closing over these locals (#162). Set here, at the end, so every helper
    # is already defined. Read at request time, so order vs include_router
    # doesn't matter.
    app.state.runner = runner
    app.state.orchestrator = orchestrator
    app.state.ws_manager = ws_manager
    app.state.get_project_lookup = get_project_lookup
    app.state.run_to_response = run_to_response
    app.state.resolve_run_or_404 = _resolve_run_or_404
    app.state.resolve_project_or_404 = _resolve_project_or_404
    app.state.workspace_to_response = _workspace_to_response

    return app


# For uvicorn direct run: uvicorn gluon.web.api:app
app = create_app()
