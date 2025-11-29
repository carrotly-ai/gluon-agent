# API Reference

Internal Python API for Gluon Agent components.

## Models (`gluon.models`)

### SessionStatus

```python
class SessionStatus(str, Enum):
    ACTIVE = "active"       # Currently running
    PAUSED = "paused"       # Can be resumed
    COMPLETED = "completed" # Finished successfully
    FAILED = "failed"       # Error occurred
```

### Workspace

```python
class Workspace(BaseModel):
    id: str                              # UUID, auto-generated
    name: str                            # Unique human-readable name
    path: Path                           # Absolute path to workspace
    created_at: datetime                 # Auto-set on creation
    updated_at: datetime                 # Updated on modifications
    scan_depth: int = 1                  # Depth to scan for projects
    auto_discover: bool = True           # Auto-discover on refresh
    ignore_patterns: list[str]           # Default: [".*", "node_modules", ...]

    def scan_for_projects(self) -> list[Path]:
        """Scan workspace directory for project directories."""
```

### Project

```python
class Project(BaseModel):
    id: str                          # UUID, auto-generated
    name: str                        # Unique human-readable name
    path: Path                       # Absolute path to project
    workspace_id: str | None = None  # FK to Workspace
    created_at: datetime             # Auto-set on creation
    updated_at: datetime             # Updated on modifications
    metadata: dict | None = None     # Optional extra data

    @property
    def is_workspace_managed(self) -> bool:
        """Check if project belongs to a workspace."""
```

### Session

```python
class Session(BaseModel):
    id: str                              # UUID, auto-generated
    project_id: str                      # FK to Project
    claude_session_id: str | None = None # Claude SDK session ID
    status: SessionStatus                # Current status
    created_at: datetime                 # Auto-set on creation
    updated_at: datetime                 # Updated on modifications
    last_prompt: str | None = None       # Last user prompt
    total_cost_usd: float = 0.0          # Accumulated cost
    total_turns: int = 0                 # Number of turns

    def mark_paused(self) -> None:
        """Mark session as paused (can be resumed)."""

    def mark_completed(self) -> None:
        """Mark session as completed."""

    def mark_failed(self) -> None:
        """Mark session as failed."""

    def add_cost(self, cost: float) -> None:
        """Add to total cost."""

    def increment_turns(self) -> None:
        """Increment turn count."""
```

### PROJECT_MARKERS

```python
PROJECT_MARKERS: list[str] = [
    "package.json",      # Node.js
    "pyproject.toml",    # Python (modern)
    "setup.py",          # Python (legacy)
    "Cargo.toml",        # Rust
    "go.mod",            # Go
    "pom.xml",           # Java/Maven
    "build.gradle",      # Java/Gradle
    "Gemfile",           # Ruby
    "composer.json",     # PHP
    "mix.exs",           # Elixir
    "pubspec.yaml",      # Dart/Flutter
    ".git",              # Any git repo
]
```

---

## Store (`gluon.store`)

### GluonStore

```python
class GluonStore:
    def __init__(self, db_path: Path | None = None):
        """
        Initialize SQLite store.

        Args:
            db_path: Path to database file. Default: ~/.gluon/gluon.db
        """

    # === Workspace Operations ===

    def create_workspace(self, name: str, path: Path) -> Workspace:
        """Create a new workspace."""

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        """Get workspace by ID."""

    def get_workspace_by_name(self, name: str) -> Workspace | None:
        """Get workspace by name."""

    def list_workspaces(self) -> list[Workspace]:
        """List all workspaces."""

    def update_workspace(self, workspace: Workspace) -> None:
        """Update an existing workspace."""

    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace. Returns True if deleted."""

    # === Project Operations ===

    def create_project(
        self,
        name: str,
        path: Path,
        metadata: dict | None = None,
        workspace_id: str | None = None,
    ) -> Project:
        """Create a new project."""

    def get_project(self, project_id: str) -> Project | None:
        """Get project by ID."""

    def get_project_by_name(self, name: str) -> Project | None:
        """Get project by name."""

    def get_project_by_path(self, path: Path) -> Project | None:
        """Get project by path."""

    def list_projects(self) -> list[Project]:
        """List all projects."""

    def list_projects_by_workspace(self, workspace_id: str) -> list[Project]:
        """List all projects in a workspace."""

    def update_project(self, project: Project) -> None:
        """Update an existing project."""

    def delete_project(self, project_id: str) -> bool:
        """Delete a project and its sessions. Returns True if deleted."""

    # === Session Operations ===

    def create_session(
        self,
        project_id: str,
        prompt: str | None = None,
    ) -> Session:
        """Create a new session for a project."""

    def get_session(self, session_id: str) -> Session | None:
        """Get session by ID."""

    def get_latest_session(
        self,
        project_id: str,
        statuses: list[SessionStatus] | None = None,
    ) -> Session | None:
        """Get most recent session, optionally filtered by status."""

    def list_sessions(self, project_id: str | None = None) -> list[Session]:
        """List sessions, optionally filtered by project."""

    def update_session(self, session: Session) -> None:
        """Update an existing session."""

    def delete_session(self, session_id: str) -> bool:
        """Delete a session. Returns True if deleted."""

    def get_active_sessions(self) -> list[Session]:
        """Get all active or paused sessions."""

    def get_session_with_project(
        self,
        session_id: str,
    ) -> tuple[Session, Project] | None:
        """Get session and its associated project."""
```

---

## Agent (`gluon.agent`)

### AgentResult

```python
@dataclass
class AgentResult:
    claude_session_id: str | None  # Session ID for resume
    total_cost_usd: float          # Total cost in USD
    total_turns: int               # Number of conversation turns
    success: bool                  # Whether execution succeeded
    error: str | None = None       # Error message if failed
```

### AgentMessage

```python
@dataclass
class AgentMessage:
    type: str                          # "text", "tool_use", "system", "error", "result"
    content: str                       # Message content
    metadata: dict[str, Any] | None    # Additional metadata
```

### GluonAgent

```python
class GluonAgent:
    def __init__(
        self,
        model: str = "sonnet",
        allowed_tools: list[str] | None = None,  # Default: DEFAULT_TOOLS
        permission_mode: str = "acceptEdits",
    ):
        """Initialize agent with configuration."""

    async def execute(
        self,
        working_dir: Path,
        prompt: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project.

        Yields AgentMessage objects during execution,
        then AgentResult as final yield.
        """

    async def execute_simple(
        self,
        working_dir: Path,
        prompt: str,
        resume_session_id: str | None = None,
    ) -> AgentResult:
        """Execute and return only final result (no streaming)."""
```

### DEFAULT_TOOLS

```python
DEFAULT_TOOLS: list[str] = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task", "TodoWrite"
]
```

### find_claude_cli

```python
def find_claude_cli() -> Path | None:
    """Find Claude CLI executable in PATH or common locations."""
```

---

## Core (`gluon.core`)

### Exceptions

```python
class ProjectNotFoundError(Exception):
    """Raised when a project is not found."""

class ProjectExistsError(Exception):
    """Raised when a project with the same name already exists."""

class WorkspaceNotFoundError(Exception):
    """Raised when a workspace is not found."""

class WorkspaceExistsError(Exception):
    """Raised when a workspace with the same name already exists."""
```

### Orchestrator

```python
class Orchestrator:
    def __init__(
        self,
        store: GluonStore | None = None,
        agent: GluonAgent | None = None,
    ):
        """Initialize orchestrator with optional custom store/agent."""

    # === Project Management ===

    def register_project(
        self,
        name: str,
        path: Path | str,
        metadata: dict | None = None,
    ) -> Project:
        """
        Register a new project.

        Raises:
            ProjectExistsError: If project with name exists
            ValueError: If path doesn't exist or isn't a directory
        """

    def get_project(self, name_or_id: str) -> Project:
        """
        Get project by name or ID.

        Raises:
            ProjectNotFoundError: If not found
        """

    def list_projects(self) -> list[Project]:
        """List all registered projects."""

    def remove_project(self, name_or_id: str) -> bool:
        """Remove a project and all its sessions."""

    # === Workspace Management ===

    def register_workspace(
        self,
        name: str,
        path: Path | str,
        auto_scan: bool = True,
    ) -> tuple[Workspace, list[Project]]:
        """
        Register workspace and optionally scan for projects.

        Returns:
            Tuple of (workspace, discovered_projects)

        Raises:
            WorkspaceExistsError: If workspace with name exists
            ValueError: If path doesn't exist or isn't a directory
        """

    def get_workspace(self, name_or_id: str) -> Workspace:
        """
        Get workspace by name or ID.

        Raises:
            WorkspaceNotFoundError: If not found
        """

    def list_workspaces(self) -> list[Workspace]:
        """List all registered workspaces."""

    def remove_workspace(
        self,
        name_or_id: str,
        remove_projects: bool = False,
    ) -> bool:
        """Remove workspace, optionally removing its projects."""

    def scan_workspace(self, name_or_id: str) -> list[Project]:
        """Scan workspace for new projects and register them."""

    def refresh_all_workspaces(self) -> dict[str, list[Project]]:
        """Refresh all workspaces. Returns map of workspace -> new projects."""

    def list_workspace_projects(self, name_or_id: str) -> list[Project]:
        """List all projects in a workspace."""

    # === Session Management ===

    def list_sessions(self, project_name: str | None = None) -> list[Session]:
        """List sessions, optionally filtered by project."""

    def get_session(self, session_id: str) -> Session | None:
        """Get a specific session by ID."""

    def get_active_sessions(self) -> list[Session]:
        """Get all active or paused sessions."""

    def get_resumable_session(self, project: Project) -> Session | None:
        """Get the latest resumable session for a project."""

    # === Execution ===

    async def execute(
        self,
        project_name: str,
        prompt: str,
        force_new_session: bool = False,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project.

        Automatically resumes last session unless force_new_session=True.
        """

    async def resume(
        self,
        project_name: str,
        prompt: str | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Resume the last session for a project.

        Raises:
            ValueError: If no resumable session exists
        """

    # === Status ===

    def status(self) -> dict:
        """
        Get overall status.

        Returns:
            {
                "total_projects": int,
                "active_sessions": int,
                "projects": [{"name": str, "path": str, "sessions": int}, ...]
            }
        """
```

---

## Chat Agent (`gluon.chat_agent`)

### ChatResponse

```python
@dataclass
class ChatResponse:
    text: str                           # Response text
    action_taken: str | None = None     # Tool that was called
    action_result: dict | None = None   # Result details
```

### GluonChatAgent

```python
class GluonChatAgent:
    def __init__(self, orchestrator: Orchestrator | None = None):
        """Initialize with optional orchestrator."""

    async def chat(self, message: str) -> ChatResponse:
        """
        Process natural language message.

        May set pending task for execution by caller.
        """

    def get_pending_task(self) -> dict | None:
        """Get pending task requiring execution."""

    def clear_pending_task(self) -> None:
        """Clear pending task."""
```

### MCP Tools

Available tools for natural language processing:

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_projects` | List all projects | None |
| `list_sessions` | List sessions | `project_name?` |
| `get_status` | Get overall status | None |
| `run_task` | Run task on project | `project_name`, `prompt` |
| `resume_session` | Resume last session | `project_name`, `prompt?` |
| `add_workspace` | Add workspace directory | `name`, `path` |
| `list_workspaces` | List all workspaces | None |
| `scan_workspace` | Scan workspace for projects | `name` |

---

## Bot (`gluon.bot`)

### GluonBot

```python
class GluonBot:
    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
    ):
        """
        Initialize Telegram bot.

        Args:
            token: Telegram bot token
            allowed_users: List of allowed user IDs (None = all allowed)
        """

    def build_application(self) -> Application:
        """Build Telegram application with handlers."""

    async def run_polling(self) -> None:
        """Run bot with polling."""
```

### run_bot

```python
def run_bot(
    token: str | None = None,
    allowed_users: list[int] | None = None,
) -> None:
    """
    Run the Gluon Telegram bot.

    Args:
        token: Bot token (or GLUON_TELEGRAM_TOKEN env var)
        allowed_users: User IDs (or GLUON_TELEGRAM_USERS env var)
    """
```
