"""Core orchestrator for Gluon Agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from gluon.agent import AgentMessage, AgentResult, GluonAgent
from gluon.models import (
    ExecutionRun,
    GitStatus,
    Project,
    RunStatus,
    Session,
    SessionStatus,
    TaskProfile,
    Workspace,
    expand_path,
    resolve_task_options,
    utc_now,
)
from gluon.models_config import ModelTier, get_model_id
from gluon.store import GluonStore
from gluon.worktree import WorktreeError, WorktreeManager, is_git_repository

if TYPE_CHECKING:
    from gluon.git_manager import GitManager
    from gluon.notifier import NotificationDispatcher

logger = logging.getLogger(__name__)


async def _broadcast_run_event(event_type: str, run: ExecutionRun, project_name: str) -> None:
    """Broadcast run event via the event bus (falls back to direct ws_manager).

    Uses lazy import to avoid circular dependencies and gracefully handles
    cases where web module is not installed or no clients are connected.
    """
    try:
        from gluon.events import event_bus
        from gluon.events.types import EventCategory, GluonEvent

        # Map event_type to event bus type
        if event_type == "created":
            etype = "run.created"
        elif run.status:
            etype = f"run.{run.status.value}"
        else:
            etype = "run.updated"

        await event_bus.emit(
            GluonEvent(
                type=etype,
                category=EventCategory.LIFECYCLE,
                project_id=run.project_id,
                run_id=run.id,
                data={
                    "run": run,
                    "project_name": project_name,
                    "prompt": run.prompt,
                    "error_message": run.error_message,
                },
            )
        )
    except ImportError:
        # Event bus not available, fall back to direct ws_manager
        try:
            from gluon.web.websocket import ws_manager

            if event_type == "created":
                await ws_manager.broadcast_run_created(run, project_name)
            else:
                await ws_manager.broadcast_run_update(run, project_name)
        except ImportError:
            pass
    except Exception as e:
        logger.debug(f"Event broadcast failed (non-critical): {e}")


class ProjectNotFoundError(Exception):
    """Raised when a project is not found."""

    pass


class ProjectExistsError(Exception):
    """Raised when a project with the same name already exists."""

    pass


class WorkspaceNotFoundError(Exception):
    """Raised when a workspace is not found."""

    pass


class WorkspaceExistsError(Exception):
    """Raised when a workspace with the same name already exists."""

    pass


class GitSyncError(Exception):
    """Raised when git sync fails and cannot proceed."""

    pass


class AgentNotFoundError(Exception):
    """Raised when an agent name doesn't resolve within the expected workspace."""

    pass


class AgentAmbiguousError(Exception):
    """Raised when a workspace has multiple agents and none was explicitly selected."""

    pass


class BudgetExceededError(Exception):
    """Raised when an agent attempts to start a run that would exceed its monthly budget."""

    def __init__(self, agent_name: str, spent: float, budget: float):
        self.agent_name = agent_name
        self.spent = spent
        self.budget = budget
        super().__init__(f"Agent '{agent_name}' monthly budget exceeded: spent ${spent:.2f} of ${budget:.2f} cap")


class WorkspaceBudgetExceededError(Exception):
    """Raised when a workspace daily or monthly budget would be exceeded."""

    def __init__(self, workspace_name: str, scope: str, spent: float, budget: float):
        self.workspace_name = workspace_name
        self.scope = scope  # "daily" or "monthly"
        self.spent = spent
        self.budget = budget
        super().__init__(
            f"Workspace '{workspace_name}' {scope} budget exceeded: spent ${spent:.2f} of ${budget:.2f} cap"
        )


class TaskLockedError(Exception):
    """Raised when an OrchestratorTask checkout is attempted but the task is already locked.

    Lock TTL applies: after the TTL expires the task becomes re-checkoutable,
    but until then concurrent checkouts fail fast rather than silently
    overwriting the owner.
    """

    def __init__(self, task_id: str, locked_by_run_id: str | None, age_seconds: float):
        self.task_id = task_id
        self.locked_by_run_id = locked_by_run_id
        self.age_seconds = age_seconds
        owner = locked_by_run_id[:8] if locked_by_run_id else "unknown"
        super().__init__(f"Task {task_id[:8]} is locked by run {owner} ({age_seconds:.0f}s ago)")


class TaskNotFoundError(Exception):
    """Raised when a task ID/prefix doesn't resolve."""

    pass


# ========== Advanced Git Operation Exceptions ==========


class GitOperationError(Exception):
    """Base exception for git operations."""

    pass


class GitMergeConflictError(GitOperationError):
    """Raised when a merge or rebase results in conflicts."""

    def __init__(self, files: list[str], operation: str = "merge"):
        self.files = files
        self.operation = operation
        super().__init__(
            f"{operation.capitalize()} conflict in {len(files)} file(s): {', '.join(files[:3])}"
            + ("..." if len(files) > 3 else "")
        )


class GitRebaseInProgressError(GitOperationError):
    """Raised when a rebase is already in progress."""

    def __init__(self, current_step: int | None = None, total_steps: int | None = None):
        self.current_step = current_step
        self.total_steps = total_steps
        msg = "Rebase in progress"
        if current_step and total_steps:
            msg += f" (step {current_step}/{total_steps})"
        super().__init__(msg)


class GitForcePushRequiredError(GitOperationError):
    """Raised when a force push would be required."""

    def __init__(self, branch: str, commits_to_delete: int):
        self.branch = branch
        self.commits_to_delete = commits_to_delete
        super().__init__(f"Force push required on {branch}: would delete {commits_to_delete} remote commit(s)")


class GitBranchNotFoundError(GitOperationError):
    """Raised when a branch is not found."""

    def __init__(self, branch: str):
        self.branch = branch
        super().__init__(f"Branch not found: {branch}")


class Orchestrator:
    """
    Core orchestrator that coordinates project management,
    session tracking, and Claude agent execution.
    """

    def __init__(
        self,
        store: GluonStore | None = None,
        git_manager: GitManager | None = None,
        notifier: NotificationDispatcher | None = None,
    ):
        self.store = store or GluonStore()
        self.git_manager = git_manager
        self.notifier = notifier

    # ========== Project Management ==========

    def register_project(
        self,
        name: str,
        path: Path | str,
        metadata: dict | None = None,
    ) -> Project:
        """
        Register a new project.

        Args:
            name: Unique name for the project
            path: Path to project directory
            metadata: Optional metadata

        Returns:
            Created Project

        Raises:
            ProjectExistsError: If project with name already exists
            ValueError: If path doesn't exist or isn't a directory
        """
        # Create project with path (may contain ${VAR})
        # Path validation will happen with expanded_path
        project = Project(name=name, path=Path(path), metadata=metadata)

        # Validate expanded path exists
        expanded = project.expanded_path
        if not expanded.exists():
            raise ValueError(f"Path does not exist: {path} (expanded to {expanded})")
        if not expanded.is_dir():
            raise ValueError(f"Path is not a directory: {path} (expanded to {expanded})")

        # Check for existing project by name
        existing = self.store.get_project_by_name(name)
        if existing:
            raise ProjectExistsError(f"Project '{name}' already exists")

        return self.store.create_project(name, project.path, metadata)

    def get_project(self, name_or_id: str) -> Project:
        """
        Get project by name or ID.

        Raises:
            ProjectNotFoundError: If project not found
        """
        # Try by name first, then by ID
        project = self.store.get_project_by_name(name_or_id)
        if not project:
            project = self.store.get_project(name_or_id)
        if not project:
            raise ProjectNotFoundError(f"Project not found: {name_or_id}")
        return project

    def list_projects(self) -> list[Project]:
        """List all registered projects."""
        return self.store.list_projects()

    # ========== Agent Resolution & Budget Helpers (Theme B Phase 1+4) ==========

    def resolve_agent(
        self,
        name_or_id: str | None,
        workspace_id: str | None,
    ) -> str | None:
        """Resolve an agent reference to an agent_id.

        Accepts:
          - Explicit agent name (looked up within the given workspace)
          - Explicit agent ID (full UUID or 8-char prefix)
          - None → auto-link only if the workspace has exactly one active agent

        Raises:
          AgentNotFoundError: Name/id given but no match within the workspace
          AgentAmbiguousError: `name_or_id` matches multiple agents by prefix
        """
        if name_or_id is None:
            if workspace_id is None:
                return None
            active = self.store.list_agents(workspace_id=workspace_id, is_active=True)
            if len(active) == 1:
                return active[0].id
            return None

        # Try by exact ID first
        agent = self.store.get_agent(name_or_id)
        if agent is not None:
            return agent.id

        # Try by (workspace, name)
        if workspace_id is not None:
            by_name = self.store.get_agent_by_name(workspace_id, name_or_id)
            if by_name is not None:
                return by_name.id

        # Try as ID prefix within the workspace scope
        scope = self.store.list_agents(workspace_id=workspace_id) if workspace_id else self.store.list_agents()
        prefix_matches = [a for a in scope if a.id.startswith(name_or_id)]
        if len(prefix_matches) == 1:
            return prefix_matches[0].id
        if len(prefix_matches) > 1:
            raise AgentAmbiguousError(
                f"Agent prefix '{name_or_id}' matches multiple agents: " + ", ".join(a.name for a in prefix_matches[:5])
            )

        raise AgentNotFoundError(
            f"Agent not found: '{name_or_id}'" + (f" in workspace {workspace_id[:8]}" if workspace_id else "")
        )

    def _enforce_agent_budget(self, agent_id: str) -> None:
        """Raise BudgetExceededError if the agent has hit its monthly cap.

        No-op if the agent has no budget configured or doesn't exist.
        """
        agent = self.store.get_agent(agent_id)
        if agent is None or agent.monthly_budget_usd is None:
            return

        from datetime import UTC, datetime

        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = self.store.get_agent_monthly_spend(agent_id, month_start)
        if spent >= agent.monthly_budget_usd:
            raise BudgetExceededError(
                agent_name=agent.name,
                spent=spent,
                budget=agent.monthly_budget_usd,
            )

    def _enforce_workspace_budget(self, workspace_id: str) -> None:
        """Raise WorkspaceBudgetExceededError if a daily or monthly cap is hit.

        No-op when neither budget is set (or the workspace doesn't exist).
        Checks daily first, then monthly. Also logs a WARNING at 80% for each
        scope so operators see headroom before we hard-stop.
        """
        workspace = self.store.get_workspace(workspace_id)
        if workspace is None:
            return
        if workspace.daily_budget_usd is None and workspace.monthly_budget_usd is None:
            return

        from datetime import UTC, datetime

        now = datetime.now(UTC)

        # Daily scope
        if workspace.daily_budget_usd is not None:
            spent_today = self.store.get_workspace_daily_spend(workspace_id, now)
            budget = workspace.daily_budget_usd
            if spent_today >= budget:
                raise WorkspaceBudgetExceededError(
                    workspace_name=workspace.name,
                    scope="daily",
                    spent=spent_today,
                    budget=budget,
                )
            if budget > 0 and (spent_today / budget) >= 0.8:
                logger.warning(
                    "Workspace '%s' daily spend at %.1f%% of cap ($%.2f / $%.2f)",
                    workspace.name,
                    (spent_today / budget) * 100,
                    spent_today,
                    budget,
                )

        # Monthly scope
        if workspace.monthly_budget_usd is not None:
            spent_month = self.store.get_workspace_monthly_spend(workspace_id, now)
            budget = workspace.monthly_budget_usd
            if spent_month >= budget:
                raise WorkspaceBudgetExceededError(
                    workspace_name=workspace.name,
                    scope="monthly",
                    spent=spent_month,
                    budget=budget,
                )
            if budget > 0 and (spent_month / budget) >= 0.8:
                logger.warning(
                    "Workspace '%s' monthly spend at %.1f%% of cap ($%.2f / $%.2f)",
                    workspace.name,
                    (spent_month / budget) * 100,
                    spent_month,
                    budget,
                )

    def _touch_agent_last_active(self, agent_id: str) -> None:
        """Best-effort update of the agent's last_active_at timestamp."""
        try:
            agent = self.store.get_agent(agent_id)
            if agent is None:
                return
            from datetime import UTC, datetime

            agent.last_active_at = datetime.now(UTC)
            self.store.update_agent(agent)
        except Exception:
            logger.debug("Failed to update agent last_active_at", exc_info=True)

    def remove_project(self, name_or_id: str) -> bool:
        """
        Remove a project and all its sessions.

        Returns:
            True if project was deleted
        """
        project = self.get_project(name_or_id)
        return self.store.delete_project(project.id)

    # ========== Session Management ==========

    def list_sessions(self, project_name: str | None = None) -> list[Session]:
        """List sessions, optionally filtered by project."""
        if project_name:
            project = self.get_project(project_name)
            return self.store.list_sessions(project.id)
        return self.store.list_sessions()

    def get_session(self, session_id: str) -> Session | None:
        """Get a specific session by ID."""
        return self.store.get_session(session_id)

    def get_active_sessions(self) -> list[Session]:
        """Get all active or paused sessions."""
        return self.store.get_active_sessions()

    def get_resumable_session(self, project: Project) -> Session | None:
        """Get the latest resumable session for a project."""
        return self.store.get_latest_session(
            project.id,
            statuses=[SessionStatus.PAUSED, SessionStatus.ACTIVE],
        )

    # ========== Workspace Management ==========

    def register_workspace(
        self,
        name: str,
        path: Path | str,
        auto_scan: bool = True,
    ) -> tuple[Workspace, list[Project]]:
        """
        Register a new workspace and optionally scan for projects.

        Args:
            name: Unique name for the workspace
            path: Path to workspace directory
            auto_scan: Whether to automatically scan and register projects

        Returns:
            Tuple of (workspace, list of discovered projects)

        Raises:
            WorkspaceExistsError: If workspace with name already exists
            ValueError: If path doesn't exist or isn't a directory
        """
        workspace_path = expand_path(path).resolve()
        if not workspace_path.exists():
            raise ValueError(f"Path does not exist: {workspace_path}")
        if not workspace_path.is_dir():
            raise ValueError(f"Path is not a directory: {workspace_path}")

        existing = self.store.get_workspace_by_name(name)
        if existing:
            raise WorkspaceExistsError(f"Workspace '{name}' already exists")

        workspace = self.store.create_workspace(name, workspace_path)
        discovered_projects: list[Project] = []

        if auto_scan:
            discovered_projects = self._scan_and_register_projects(workspace)

        return workspace, discovered_projects

    def get_workspace(self, name_or_id: str) -> Workspace:
        """
        Get workspace by name or ID.

        Raises:
            WorkspaceNotFoundError: If workspace not found
        """
        workspace = self.store.get_workspace_by_name(name_or_id)
        if not workspace:
            workspace = self.store.get_workspace(name_or_id)
        if not workspace:
            raise WorkspaceNotFoundError(f"Workspace not found: {name_or_id}")
        return workspace

    def list_workspaces(self) -> list[Workspace]:
        """List all registered workspaces."""
        return self.store.list_workspaces()

    def remove_workspace(self, name_or_id: str, remove_projects: bool = False) -> bool:
        """
        Remove a workspace.

        Args:
            name_or_id: Workspace name or ID
            remove_projects: If True, also remove all projects in the workspace

        Returns:
            True if workspace was deleted
        """
        workspace = self.get_workspace(name_or_id)

        if remove_projects:
            projects = self.store.list_projects_by_workspace(workspace.id)
            for project in projects:
                self.store.delete_project(project.id)

        return self.store.delete_workspace(workspace.id)

    def scan_workspace(self, name_or_id: str) -> list[Project]:
        """
        Scan a workspace for new projects and register them.

        Args:
            name_or_id: Workspace name or ID

        Returns:
            List of newly discovered and registered projects
        """
        workspace = self.get_workspace(name_or_id)
        return self._scan_and_register_projects(workspace)

    def refresh_all_workspaces(self) -> dict[str, list[Project]]:
        """
        Refresh all workspaces to detect new projects.

        Returns:
            Dict mapping workspace name to list of newly discovered projects
        """
        results: dict[str, list[Project]] = {}
        for workspace in self.list_workspaces():
            if workspace.auto_discover:
                new_projects = self._scan_and_register_projects(workspace)
                if new_projects:
                    results[workspace.name] = new_projects
        return results

    def list_workspace_projects(self, name_or_id: str) -> list[Project]:
        """List all projects in a workspace."""
        workspace = self.get_workspace(name_or_id)
        return self.store.list_projects_by_workspace(workspace.id)

    def _scan_and_register_projects(self, workspace: Workspace) -> list[Project]:
        """
        Scan workspace and register any new projects found.

        Returns:
            List of newly registered projects
        """
        discovered: list[Project] = []
        project_paths = workspace.scan_for_projects()

        for project_path in project_paths:
            # Check if project already exists
            existing = self.store.get_project_by_path(project_path)
            if existing:
                continue

            # Generate name from directory name
            project_name = project_path.name

            # Handle name conflicts by appending workspace prefix
            if self.store.get_project_by_name(project_name):
                project_name = f"{workspace.name}/{project_name}"

            # Still conflict? Skip it
            if self.store.get_project_by_name(project_name):
                continue

            project = self.store.create_project(
                name=project_name,
                path=project_path,
                workspace_id=workspace.id,
            )
            discovered.append(project)

        return discovered

    # ========== Execution ==========

    async def execute(
        self,
        project_name: str,
        prompt: str,
        force_new_session: bool = False,
        model: ModelTier | str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        use_worktree: bool = False,
        initiator: str | None = None,
        profile: TaskProfile | str | None = None,
        max_thinking_tokens: int | None = None,
        thinking_budget: str | None = None,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        force_planning: bool | None = None,
        effort: str | None = None,
        task_budget: int | None = None,
        agent_id: str | None = None,
        approval_policy: Any = None,  # models.ApprovalPolicy, defaults to PERMISSIVE
        max_tool_calls: int | None = None,
        max_duration_minutes: int | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project.

        Automatically resumes the last session if available,
        unless force_new_session is True.

        All executions are tracked via ExecutionRun records, making them
        visible in the dashboard regardless of interface (CLI, bot, web).

        Args:
            project_name: Name or ID of the project
            prompt: User prompt to execute
            force_new_session: Force creation of new session
            model: Model tier to use (opus/sonnet/haiku). Overrides profile's model.
            run_id: Optional run ID to link to existing ExecutionRun
            session_id: Specific session ID to resume (overrides auto-detection)
            use_worktree: Execute in isolated Git worktree (default: False)
            initiator: Source of execution (e.g., "cli:foreground", "telegram:123")
            profile: Task profile (quick/standard/deep/planning). Defaults to standard.
            max_thinking_tokens: Override thinking token budget directly.
            thinking_budget: Override via preset (none/low/medium/high/ultrathink/adaptive).
            max_turns: Override max conversation turns.
            max_budget_usd: Override max cost budget.
            force_planning: Override planning mode (True = plan before executing).
            effort: Override reasoning effort (low/medium/high/xhigh/max).
            task_budget: Override task token budget (model paces itself).

        Yields:
            AgentMessage during execution
            AgentResult as final yield
        """
        project = self.get_project(project_name)

        # Resolve agent — explicit argument > auto-link (only if workspace has
        # exactly one active agent) > None. Only applies when we're creating a
        # new run; existing runs keep whatever agent_id they had.
        resolved_agent_id: str | None = agent_id
        if run_id is None and resolved_agent_id is None and project.workspace_id:
            active = self.store.list_agents(workspace_id=project.workspace_id, is_active=True)
            if len(active) == 1:
                resolved_agent_id = active[0].id

        # Enforce the resolved agent's monthly budget before spawning a run
        if resolved_agent_id is not None:
            self._enforce_agent_budget(resolved_agent_id)

        # Enforce workspace-scoped rolling budgets (daily + monthly, Theme D2).
        # Check this after the agent budget so individual agent caps fire first
        # when they're hit, but still catch workspace-wide overruns before a run
        # record gets written.
        if project.workspace_id:
            self._enforce_workspace_budget(project.workspace_id)

        # ========== ExecutionRun Management ==========
        # All executions are tracked via ExecutionRun for unified visibility
        run: ExecutionRun | None = None
        if run_id:
            # Link to existing ExecutionRun (from bots/web that pre-create)
            run = self.store.get_run(run_id)
            if not run:
                raise ValueError(f"Run not found: {run_id}")
        else:
            # Create new ExecutionRun (for CLI foreground, etc.)
            from gluon.models import ApprovalPolicy as _ApprovalPolicy

            run = self.store.create_run(
                project_id=project.id,
                prompt=prompt,
                initiator=initiator or "orchestrator",
                use_worktree=use_worktree,
                agent_id=resolved_agent_id,
                approval_policy=approval_policy or _ApprovalPolicy.PERMISSIVE,
                max_tool_calls=max_tool_calls,
                max_duration_minutes=max_duration_minutes,
            )
            # Broadcast new run to dashboard (only for newly created runs)
            await _broadcast_run_event("created", run, project.name)

            # Touch agent activity on fresh run creation
            if resolved_agent_id is not None:
                self._touch_agent_last_active(resolved_agent_id)

        # Create log directory for all runs
        log_dir = Path.home() / ".gluon" / "logs" / run.id
        log_dir.mkdir(parents=True, exist_ok=True)

        # Mark run as running
        run.mark_running(pid=os.getpid(), log_path=log_dir)
        self.store.update_run(run)
        await _broadcast_run_event("updated", run, project.name)

        # Determine working directory (main project or worktree)
        working_dir = project.expanded_path
        worktree_manager: WorktreeManager | None = None

        # Create worktree if requested and project is a git repo
        if use_worktree:
            if await is_git_repository(project.expanded_path):
                worktree_run_id = run_id or str(uuid4())[:8]
                worktree_manager = WorktreeManager(project.expanded_path)
                try:
                    working_dir = await worktree_manager.create(worktree_run_id)
                    run.source_branch = worktree_manager.source_branch
                    run.worktree_path = str(working_dir)
                    run.branch_name = f"gluon-{worktree_run_id}"
                    self.store.update_run(run)
                    yield AgentMessage(
                        type="system",
                        content=f"Created worktree at {working_dir}",
                        metadata={"worktree_path": str(working_dir)},
                    )
                except WorktreeError as e:
                    logger.warning(f"Failed to create worktree, using main directory: {e}")
                    worktree_manager = None
            else:
                logger.info(f"Worktree requested but {project.expanded_path} is not a git repo, using main directory")

        # Capture source branch for non-worktree git repos
        if not run.source_branch and await is_git_repository(project.expanded_path):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "rev-parse",
                    "--abbrev-ref",
                    "HEAD",
                    cwd=str(project.expanded_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_bytes, _ = await proc.communicate()
                if proc.returncode == 0:
                    run.source_branch = stdout_bytes.decode().strip() or None
                    self.store.update_run(run)
            except Exception:
                pass

        try:
            # Pre-task git sync (only for main directory, not worktree)
            if self.git_manager and not worktree_manager:
                sync_result = await self.git_manager.pre_task_sync(project)
                if not sync_result.success:
                    raise GitSyncError(sync_result.error or sync_result.message)
                if sync_result.action != "none":
                    yield AgentMessage(
                        type="system",
                        content=f"Git: {sync_result.message}",
                        metadata={"git_action": sync_result.action},
                    )

            # Find or create session
            session: Session | None = None
            resume_session_id: str | None = None

            # If specific session_id provided, use that
            if session_id:
                session = self.store.get_session(session_id)
                if session and session.claude_session_id:
                    resume_session_id = session.claude_session_id
            elif not force_new_session:
                session = self.get_resumable_session(project)
                if session and session.claude_session_id:
                    resume_session_id = session.claude_session_id

            if not session:
                session = self.store.create_session(project.id, prompt)

            # Update session with new prompt
            session.last_prompt = prompt
            session.status = SessionStatus.ACTIVE
            self.store.update_session(session)

            # Resolve task options from profile and overrides
            task_options = resolve_task_options(
                profile=profile,
                model=model,
                max_thinking_tokens=max_thinking_tokens,
                thinking_budget=thinking_budget,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                force_planning=force_planning,
                effort=effort,
                task_budget=task_budget,
            )

            # Pre-hydration: gather project context if enabled
            enable_prehydration = self.store.get_setting("prehydration_enabled", "true") == "true"
            if enable_prehydration:
                from gluon.pre_hydration import format_context, hydrate

                hydration = await hydrate(working_dir)
                prompt = format_context(hydration) + "\n\n" + prompt

            # Get model ID from resolved options
            model_id = get_model_id(task_options["model"])

            # Read experimental feature settings
            agent_teams_enabled = self.store.get_setting("agent_teams_enabled", "false") == "true"
            skills_enabled = self.store.get_setting("skills_enabled", "true") == "true"

            # Create agent with resolved options
            agent = GluonAgent(
                model=model_id,
                max_thinking_tokens=task_options["max_thinking_tokens"],
                max_turns=task_options["max_turns"],
                max_budget_usd=task_options["max_budget_usd"],
                force_planning=task_options["force_planning"],
                effort=task_options.get("effort"),
                agent_teams_enabled=agent_teams_enabled,
                skills_enabled=skills_enabled,
                task_budget=task_options.get("task_budget"),
            )

            # Execute via agent with log file writing
            result: AgentResult | None = None
            stdout_path = log_dir / "stdout.log"
            messages_path = log_dir / "messages.jsonl"

            with open(stdout_path, "w") as stdout_file, open(messages_path, "w") as messages_file:
                async for item in agent.execute(
                    working_dir=working_dir,
                    prompt=prompt,
                    resume_session_id=resume_session_id,
                ):
                    if isinstance(item, AgentMessage):
                        # Write to log files
                        msg_dict = {
                            "timestamp": utc_now().isoformat(),
                            "type": item.type,
                            "content": item.content,
                            "metadata": item.metadata,
                        }
                        messages_file.write(json.dumps(msg_dict) + "\n")
                        messages_file.flush()

                        if item.type == "text" and item.content:
                            stdout_file.write(item.content + "\n")
                            stdout_file.flush()

                        # Capture session ID from system messages
                        if item.type == "system" and item.metadata:
                            new_session_id = item.metadata.get("session_id")
                            if new_session_id and new_session_id != session.claude_session_id:
                                session.claude_session_id = new_session_id
                                self.store.update_session(session)

                        yield item

                    elif isinstance(item, AgentResult):
                        result = item
                        # AgentResult summary - don't write to messages.jsonl
                        # since AgentMessage type="result" already logged the completion

            # Update ExecutionRun with result
            if result:
                old_status = run.status
                run.cost_usd = result.total_cost_usd
                run.input_tokens = result.input_tokens
                run.output_tokens = result.output_tokens
                run.model_used = result.model_used
                if result.claude_session_id:
                    run.claude_session_id = result.claude_session_id

                if result.success:
                    run.mark_review()  # All tasks go to REVIEW first
                else:
                    run.mark_failed(result.error or "Unknown error", exit_code=1)

                self.store.update_run(run)
                await _broadcast_run_event("updated", run, project.name)

                if self.notifier and run.status != old_status:
                    try:
                        await self.notifier.notify(run, old_status, run.status)
                    except Exception:
                        logger.debug("Notification dispatch failed", exc_info=True)

            # Update session with result
            if result:
                session.claude_session_id = result.claude_session_id or session.claude_session_id
                session.total_cost_usd += result.total_cost_usd
                session.total_turns += result.total_turns

                if result.success:
                    session.mark_paused()  # Ready for resume

                    # Post-task git sync (only for main directory, not worktree)
                    if self.git_manager and not worktree_manager:
                        commit_msg = prompt[:50] + ("..." if len(prompt) > 50 else "")
                        sync_result = await self.git_manager.post_task_sync(
                            project,
                            commit_msg,
                            session_id=session.id,
                            run_id=run.id,  # Use run.id from ExecutionRun
                        )
                        if sync_result.action != "none":
                            yield AgentMessage(
                                type="system",
                                content=f"Git: {sync_result.message}",
                                metadata={"git_action": sync_result.action},
                            )
                        if not sync_result.success:
                            logger.warning(f"Post-task git sync failed: {sync_result.error}")
                else:
                    session.mark_failed()

                self.store.update_session(session)

                # Add Gluon session ID and run ID to result for linking
                result.session_id = session.id
                result.execution_run_id = run.id
                yield result

        except Exception as e:
            # Mark run as failed if exception occurs
            if run:
                exc_old_status = run.status
                run.mark_failed(str(e), exit_code=1)
                self.store.update_run(run)
                await _broadcast_run_event("updated", run, project.name)

                if self.notifier and run.status != exc_old_status:
                    try:
                        await self.notifier.notify(run, exc_old_status, run.status)
                    except Exception:
                        logger.debug("Notification dispatch failed", exc_info=True)
            raise

        finally:
            # Cleanup worktree if one was created
            if worktree_manager:
                try:
                    cleanup_result = await worktree_manager.cleanup(commit_changes=True)
                    if cleanup_result.success:
                        msg = "Cleaned up worktree"
                        if cleanup_result.message:
                            msg += f": {cleanup_result.message}"
                        yield AgentMessage(
                            type="system",
                            content=msg,
                            metadata={"worktree_branch": cleanup_result.branch},
                        )
                except Exception as e:
                    logger.error(f"Failed to cleanup worktree: {e}")

    async def resume(
        self,
        project_name: str,
        prompt: str | None = None,
        model: ModelTier | str | None = None,
        profile: TaskProfile | str | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Resume the last session for a project.

        Args:
            project_name: Name or ID of the project
            prompt: Optional new prompt (uses "Continue" if not provided)
            model: Model tier to use (opus/sonnet/haiku). Overrides profile's model.
            profile: Task profile (quick/standard/deep/planning).

        Yields:
            AgentMessage during execution
            AgentResult as final yield

        Raises:
            ValueError: If no resumable session exists
        """
        project = self.get_project(project_name)
        session = self.get_resumable_session(project)

        if not session or not session.claude_session_id:
            raise ValueError(f"No resumable session for project '{project_name}'")

        actual_prompt = prompt or "Continue from where you left off."

        async for item in self.execute(
            project_name, actual_prompt, force_new_session=False, model=model, profile=profile
        ):
            yield item

    # ========== Status ==========

    def status(self) -> dict:
        """Get overall status of the orchestrator."""
        projects = self.list_projects()
        active_sessions = self.get_active_sessions()

        return {
            "total_projects": len(projects),
            "active_sessions": len(active_sessions),
            "projects": [
                {
                    "name": p.name,
                    "path": str(p.path),
                    "sessions": len(self.store.list_sessions(p.id)),
                }
                for p in projects
            ],
        }

    # ========== Run Management ==========

    def list_runs(
        self,
        project_name: str | None = None,
        active_only: bool = False,
        limit: int = 10,
    ) -> list[ExecutionRun]:
        """
        List execution runs.

        Args:
            project_name: Filter by project name
            active_only: Only return active runs
            limit: Maximum number of runs to return

        Returns:
            List of ExecutionRun objects
        """
        project_id = None
        if project_name:
            project = self.get_project(project_name)
            project_id = project.id

        if active_only:
            runs = self.store.list_active_runs()
            if project_id:
                runs = [r for r in runs if r.project_id == project_id]
            return runs[:limit]

        statuses = None
        return self.store.list_runs(project_id=project_id, statuses=statuses, limit=limit)

    def get_run(self, run_id: str) -> ExecutionRun | None:
        """Get a run by ID (supports short IDs)."""
        return self.store.get_run_by_short_id(run_id) or self.store.get_run(run_id)

    def cancel_run(self, run_id: str) -> tuple[bool, str]:
        """
        Cancel a running task.

        Args:
            run_id: Run ID (can be short ID)

        Returns:
            Tuple of (success, message)
        """
        import os
        import signal

        run = self.get_run(run_id)
        if not run:
            return False, f"Run not found: {run_id}"

        if not run.is_active:
            return False, f"Run is not active (status: {run.status.value})"

        # Try to kill by PID if running
        if run.pid and run.status == RunStatus.RUNNING:
            try:
                os.kill(run.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass  # Process already gone or can't kill

        # Mark as cancelled
        run.mark_cancelled()
        self.store.update_run(run)
        return True, f"Cancelled run {run.id[:8]}"

    # ========== Git Operations ==========

    async def get_git_status(self, project_name: str) -> GitStatus | None:
        """
        Get git status for a project.

        Args:
            project_name: Name or ID of the project

        Returns:
            GitStatus or None if not a git repo or git_manager not configured
        """
        project = self.get_project(project_name)

        # First check cached status in DB
        cached = self.store.get_git_status(project.id)

        # If we have git_manager, refresh the status
        if self.git_manager:
            return await self.git_manager.refresh_status(project)

        return cached

    async def git_sync(self, project_name: str) -> tuple[bool, str]:
        """
        Sync a project: auto-commit uncommitted changes, fetch, and fast-forward.

        Args:
            project_name: Name or ID of the project

        Returns:
            Tuple of (success, message)
        """
        if not self.git_manager:
            return False, "Git manager not configured"

        project = self.get_project(project_name)
        result = await self.git_manager.pre_task_sync(project)

        if result.success:
            return True, result.message
        return False, result.error or result.message

    async def git_push(self, project_name: str, commit_message: str) -> tuple[bool, str]:
        """
        Commit all changes and push to remote.

        Args:
            project_name: Name or ID of the project
            commit_message: Message for the commit

        Returns:
            Tuple of (success, message)
        """
        if not self.git_manager:
            return False, "Git manager not configured"

        project = self.get_project(project_name)
        result = await self.git_manager.post_task_sync(project, commit_message)

        if result.success:
            return True, result.message
        return False, result.error or result.message

    async def git_fetch(self, project_name: str) -> tuple[bool, str]:
        """
        Fetch from remote without merging.

        Args:
            project_name: Name or ID of the project

        Returns:
            Tuple of (success, message) with ahead/behind info
        """
        if not self.git_manager:
            return False, "Git manager not configured"

        project = self.get_project(project_name)
        status = await self.git_manager.refresh_status(project)

        if not status.is_git_repo:
            return False, f"{project_name} is not a git repository"

        if not status.remote:
            return True, "No remote configured (local-only repository)"

        msg_parts = [f"Fetched from {status.remote}"]
        if status.commits_ahead or status.commits_behind:
            if status.is_diverged:
                msg_parts.append(f"⚠️ Diverged: {status.commits_ahead} ahead, {status.commits_behind} behind")
            elif status.commits_ahead:
                msg_parts.append(f"↑ {status.commits_ahead} commit(s) to push")
            elif status.commits_behind:
                msg_parts.append(f"↓ {status.commits_behind} commit(s) to pull")
        else:
            msg_parts.append("Up to date")

        return True, ". ".join(msg_parts)
