"""Core orchestrator for Gluon Agent."""

from collections.abc import AsyncIterator
from pathlib import Path

from gluon.agent import AgentMessage, AgentResult, GluonAgent
from gluon.models import Project, Session, SessionStatus, Workspace
from gluon.store import GluonStore


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


class Orchestrator:
    """
    Core orchestrator that coordinates project management,
    session tracking, and Claude agent execution.
    """

    def __init__(
        self,
        store: GluonStore | None = None,
        agent: GluonAgent | None = None,
    ):
        self.store = store or GluonStore()
        self.agent = agent or GluonAgent()

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
        # Validate path
        project_path = Path(path).resolve()
        if not project_path.exists():
            raise ValueError(f"Path does not exist: {project_path}")
        if not project_path.is_dir():
            raise ValueError(f"Path is not a directory: {project_path}")

        # Check for existing project
        existing = self.store.get_project_by_name(name)
        if existing:
            raise ProjectExistsError(f"Project '{name}' already exists")

        return self.store.create_project(name, project_path, metadata)

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
        workspace_path = Path(path).resolve()
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
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project.

        Automatically resumes the last session if available,
        unless force_new_session is True.

        Args:
            project_name: Name or ID of the project
            prompt: User prompt to execute
            force_new_session: Force creation of new session

        Yields:
            AgentMessage during execution
            AgentResult as final yield
        """
        project = self.get_project(project_name)

        # Find or create session
        session: Session | None = None
        resume_session_id: str | None = None

        if not force_new_session:
            session = self.get_resumable_session(project)
            if session and session.claude_session_id:
                resume_session_id = session.claude_session_id

        if not session:
            session = self.store.create_session(project.id, prompt)

        # Update session with new prompt
        session.last_prompt = prompt
        session.status = SessionStatus.ACTIVE
        self.store.update_session(session)

        # Execute via agent
        result: AgentResult | None = None

        async for item in self.agent.execute(
            working_dir=project.path,
            prompt=prompt,
            resume_session_id=resume_session_id,
        ):
            if isinstance(item, AgentMessage):
                # Capture session ID from system messages
                if item.type == "system" and item.metadata:
                    new_session_id = item.metadata.get("session_id")
                    if new_session_id and new_session_id != session.claude_session_id:
                        session.claude_session_id = new_session_id
                        self.store.update_session(session)

                yield item

            elif isinstance(item, AgentResult):
                result = item

        # Update session with result
        if result:
            session.claude_session_id = result.claude_session_id or session.claude_session_id
            session.total_cost_usd += result.total_cost_usd
            session.total_turns += result.total_turns

            if result.success:
                session.mark_paused()  # Ready for resume
            else:
                session.mark_failed()

            self.store.update_session(session)
            yield result

    async def resume(
        self,
        project_name: str,
        prompt: str | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Resume the last session for a project.

        Args:
            project_name: Name or ID of the project
            prompt: Optional new prompt (uses "Continue" if not provided)

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

        async for item in self.execute(project_name, actual_prompt, force_new_session=False):
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
