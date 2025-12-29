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

### RunStatus

```python
class RunStatus(str, Enum):
    PENDING = "pending"       # Queued, not started
    RUNNING = "running"       # Currently executing
    COMPLETED = "completed"   # Finished successfully
    FAILED = "failed"         # Error occurred
    CANCELLED = "cancelled"   # User cancelled
```

### ExecutionRun

```python
class ExecutionRun(BaseModel):
    id: str                              # UUID, auto-generated
    project_id: str                      # FK to Project
    session_id: str | None = None        # FK to Session (linked after execution)
    pid: int | None = None               # Process ID when running
    status: RunStatus                    # Current status
    prompt: str                          # Task prompt
    initiator: str | None = None         # Who started: 'telegram:123', 'discord:456', 'cli'
    thread_id: str | None = None         # Discord/Slack thread ID for resume
    created_at: datetime                 # Auto-set on creation
    started_at: datetime | None          # When execution started
    completed_at: datetime | None        # When execution ended
    exit_code: int | None                # Exit code (0 = success)
    log_path: Path | None                # Path to log files
    error_message: str | None            # Error if failed

    @property
    def is_active(self) -> bool:
        """Check if run is pending or running."""

    @property
    def duration_seconds(self) -> float | None:
        """Calculate run duration if completed."""

    def mark_running(self, pid: int, log_path: Path) -> None:
        """Mark run as running with process info."""

    def mark_completed(self, exit_code: int = 0) -> None:
        """Mark run as completed."""

    def mark_failed(self, error: str, exit_code: int = 1) -> None:
        """Mark run as failed."""

    def mark_cancelled(self) -> None:
        """Mark run as cancelled."""
```

### ChannelMapping

```python
class ChannelMapping(BaseModel):
    id: str                              # UUID, auto-generated
    transport: str                       # 'telegram', 'discord'
    channel_id: str                      # Platform-specific channel ID
    project_id: str                      # FK to Project
    project_name: str                    # Cached project name
    created_at: datetime                 # Auto-set on creation
```

### GitStatus

```python
class GitStatus(BaseModel):
    is_git_repo: bool                    # Whether path is a git repo
    branch: str | None = None            # Current branch name
    remote: str | None = None            # Remote name (usually 'origin')
    remote_url: str | None = None        # Remote URL
    has_uncommitted: bool = False        # Has uncommitted changes
    uncommitted_count: int = 0           # Number of uncommitted files
    commits_ahead: int = 0               # Commits ahead of remote
    commits_behind: int = 0              # Commits behind remote
    last_fetch_at: datetime | None       # Last fetch time
    last_push_at: datetime | None        # Last push time
    last_commit_at: datetime | None      # Last commit time

    @property
    def is_diverged(self) -> bool:
        """Check if branch has diverged from remote."""
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

    # === Execution Run Operations ===

    def create_run(
        self,
        project_id: str,
        prompt: str,
        initiator: str | None = None,
    ) -> ExecutionRun:
        """Create a new execution run."""

    def get_run(self, run_id: str) -> ExecutionRun | None:
        """Get run by ID."""

    def get_run_by_short_id(self, short_id: str) -> ExecutionRun | None:
        """Get run by short ID prefix (at least 4 chars)."""

    def get_run_by_thread_id(self, thread_id: str) -> ExecutionRun | None:
        """Get the most recent run for a thread ID."""

    def list_runs(
        self,
        project_id: str | None = None,
        statuses: list[RunStatus] | None = None,
        initiator: str | None = None,
        limit: int = 50,
    ) -> list[ExecutionRun]:
        """List runs with optional filters."""

    def list_active_runs(self) -> list[ExecutionRun]:
        """List all pending or running runs."""

    def update_run(self, run: ExecutionRun) -> None:
        """Update an existing run."""

    def delete_run(self, run_id: str) -> bool:
        """Delete a run. Returns True if deleted."""

    # === Channel Mapping Operations ===

    def create_channel_mapping(
        self,
        transport: str,
        channel_id: str,
        project_id: str,
        project_name: str,
    ) -> ChannelMapping:
        """Create or update a channel-to-project mapping (upsert)."""

    def get_channel_mapping(
        self,
        transport: str,
        channel_id: str,
    ) -> ChannelMapping | None:
        """Get channel mapping by transport and channel ID."""

    def list_channel_mappings(
        self,
        transport: str | None = None,
    ) -> list[ChannelMapping]:
        """List channel mappings, optionally filtered by transport."""

    def delete_channel_mapping(
        self,
        transport: str,
        channel_id: str,
    ) -> bool:
        """Delete a channel mapping. Returns True if deleted."""

    # === Git Status Operations ===

    def get_git_status(self, project_id: str) -> GitStatus | None:
        """Get cached git status for a project."""

    def update_git_status(self, project_id: str, status: GitStatus) -> None:
        """Update cached git status for a project."""
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

    # === Run Management ===

    def list_runs(
        self,
        project_name: str | None = None,
        active_only: bool = False,
        limit: int = 10,
    ) -> list[ExecutionRun]:
        """List execution runs with optional filters."""

    def get_run(self, run_id: str) -> ExecutionRun | None:
        """Get a run by ID (supports short IDs)."""

    def cancel_run(self, run_id: str) -> tuple[bool, str]:
        """Cancel a running task. Returns (success, message)."""

    # === Execution ===

    async def execute(
        self,
        project_name: str,
        prompt: str,
        force_new_session: bool = False,
        model: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project.

        Args:
            project_name: Project name or ID
            prompt: User prompt
            force_new_session: Force new session vs auto-resume
            model: Model tier ('opus', 'sonnet', 'haiku')
            run_id: Optional run ID for git commit metadata
            session_id: Specific session ID to resume

        Automatically resumes last session unless force_new_session=True.
        """

    async def resume(
        self,
        project_name: str,
        prompt: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Resume the last session for a project.

        Raises:
            ValueError: If no resumable session exists
        """

    # === Git Operations ===

    async def get_git_status(self, project_name: str) -> GitStatus | None:
        """Get git status for a project."""

    async def git_sync(self, project_name: str) -> tuple[bool, str]:
        """Auto-commit, fetch, and fast-forward. Returns (success, message)."""

    async def git_push(
        self,
        project_name: str,
        commit_message: str,
    ) -> tuple[bool, str]:
        """Commit and push changes. Returns (success, message)."""

    async def git_fetch(self, project_name: str) -> tuple[bool, str]:
        """Fetch from remote. Returns (success, message with ahead/behind)."""

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

The chat agent exposes 40+ MCP tools for comprehensive Gluon operations:

#### Project & Session Management

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_projects` | List all registered projects | None |
| `add_project` | Register a new project | `name`, `path` |
| `remove_project` | Unregister a project | `name` |
| `list_sessions` | List sessions for a project | `project_name?` |
| `get_status` | Get overall Gluon status | None |

#### Workspace Management

| Tool | Description | Parameters |
|------|-------------|------------|
| `add_workspace` | Register a workspace directory | `name`, `path` |
| `list_workspaces` | List all workspaces | None |
| `scan_workspace` | Scan workspace for new projects | `name` |
| `remove_workspace` | Unregister a workspace | `name` |
| `list_workspace_projects` | List projects in a workspace | `name` |

#### Task Execution

| Tool | Description | Parameters |
|------|-------------|------------|
| `run_task` | Run a coding task on a project | `project_name`, `prompt`, `model?`, `use_worktree?` |
| `resume_session` | Resume the last session | `project_name`, `prompt?`, `model?` |

#### Run Management

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_runs` | List execution runs | `project_name?`, `active_only?` |
| `get_run` | Get run details | `run_id` |
| `get_logs` | Get logs for a run | `run_id`, `stream?`, `lines?` |
| `cancel_run` | Cancel a running task | `run_id` |
| `archive_run` | Archive a completed run | `run_id` |

#### Git Operations

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_git_status` | Get git status for a project | `project_name` |
| `git_sync` | Auto-commit, fetch, and fast-forward | `project_name` |
| `git_push` | Commit and push changes | `project_name`, `commit_message` |
| `git_fetch` | Fetch from remote | `project_name` |

#### Branch & PR Operations

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_branches` | List all branches | `project_name` |
| `delete_branch` | Delete a branch | `project_name`, `branch_name` |
| `create_pr` | Create a pull request | `run_id`, `title?`, `body?` |
| `merge_branch` | Merge a run's branch | `run_id` |
| `get_run_commits` | Get commits on a run's branch | `run_id` |
| `get_run_files` | Get files changed on branch | `run_id` |
| `get_file_diff` | Get diff for a specific file | `run_id`, `file_path` |

#### Conflict Resolution

| Tool | Description | Parameters |
|------|-------------|------------|
| `check_conflicts` | Check for merge conflicts | `project_name` |
| `get_conflict_diff` | Get 3-way diff for a conflict | `project_name`, `file_path` |
| `resolve_conflict` | Resolve a conflict (ours/theirs) | `project_name`, `file_path`, `resolution` |
| `rebase_branch` | Start a rebase operation | `run_id` |
| `rebase_continue` | Continue after resolving | `run_id` |
| `rebase_abort` | Abort a rebase | `run_id` |

#### Images & Attachments

| Tool | Description | Parameters |
|------|-------------|------------|
| `upload_image` | Upload an image | `run_id`, `url` or `base64` |
| `list_run_images` | List images attached to a run | `run_id` |

#### Usage & Settings

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_usage` | Get usage summary | None |
| `get_usage_by_project` | Get usage broken down by project | None |
| `get_setting` | Get a configuration setting | `key` |
| `set_setting` | Set a configuration setting | `key`, `value` |

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

---

## Transport Layer (`gluon.transport`)

### TransportContext

```python
@dataclass
class TransportContext:
    transport: str              # 'telegram', 'discord', etc.
    user_id: str                # Universal ID: '{transport}:{id}'
    chat_id: str                # Channel/conversation ID
    thread_id: str | None       # Thread ID if in thread
    project_hint: str | None    # Project name from context
    message_id: str | None      # Triggering message ID
    raw_data: dict              # Platform-specific metadata

    @property
    def platform_user_id(self) -> str:
        """Extract platform-specific user ID."""
```

### TransportResponse

```python
@dataclass
class TransportResponse:
    text: str                   # Message text
    thread_id: str | None       # Thread to send into
    reply_to_id: str | None     # Message to reply to
    parse_mode: str = "markdown" # 'plain', 'markdown', 'html'
    editable: bool = False      # May be edited later
```

### TransportCapabilities

```python
@dataclass
class TransportCapabilities:
    max_message_length: int     # Max chars per message
    supports_threads: bool      # Can create threads
    supports_editing: bool      # Can edit sent messages
    supports_typing: bool       # Can show typing indicator
    supports_formatting: bool   # Supports markdown/rich text
```

### Transport (ABC)

```python
class Transport(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Return transport name."""

    @property
    @abstractmethod
    def capabilities(self) -> TransportCapabilities:
        """Return transport capabilities."""

    @abstractmethod
    async def send(
        self,
        ctx: TransportContext,
        response: TransportResponse,
    ) -> str:
        """Send a message and return its ID."""

    @abstractmethod
    async def edit(
        self,
        ctx: TransportContext,
        message_id: str,
        response: TransportResponse,
    ) -> bool:
        """Edit an existing message. Returns success."""

    @abstractmethod
    async def send_typing(self, ctx: TransportContext) -> None:
        """Show typing indicator."""

    async def create_thread(
        self,
        ctx: TransportContext,
        name: str,
        message_id: str | None = None,
    ) -> str | None:
        """Create a thread (optional, returns None if unsupported)."""
        return None

    @abstractmethod
    async def start(self) -> None:
        """Start the transport."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport gracefully."""

    def is_authorized(self, user_id: str | int) -> bool:
        """Check if user is authorized (default: allow all)."""

    def truncate_text(self, text: str) -> str:
        """Truncate text to max message length."""
```

---

## GluonBotCore (`gluon.bot_core`)

Shared logic for all transports.

```python
class GluonBotCore:
    def __init__(
        self,
        store: GluonStore | None = None,
        orchestrator: Orchestrator | None = None,
        git_manager: GitManager | None = None,
        max_concurrent: int = 16,
    ):
        """Initialize bot core with shared state."""

    # === Task Execution ===

    async def execute_task(
        self,
        ctx: TransportContext,
        run: ExecutionRun,
        project_name: str,
        send_callback: SendCallback,
        model: str | None = None,
        force_new_session: bool = True,
        session_id: str | None = None,
    ) -> None:
        """Execute a task with streaming updates."""

    async def process_natural_language(
        self,
        ctx: TransportContext,
        text: str,
        send_callback: SendCallback,
        reply_context: str | None = None,
    ) -> dict | None:
        """Process NL message. Returns pending task if any."""

    # === Concurrency ===

    def is_at_capacity(self) -> bool:
        """Check if at concurrent task limit."""

    def register_task(self, run_id: str, task: asyncio.Task) -> None:
        """Register an active task."""

    async def cancel_task(self, run_id: str) -> bool:
        """Cancel a task by run ID."""

    # === Formatting ===

    def format_projects_list(self, filter_term: str | None = None) -> str:
        """Format projects for display."""

    def format_runs_list(self, initiator: str | None = None) -> str:
        """Format runs for display."""

    def format_status(self) -> str:
        """Format overall status."""

    # === Recovery ===

    def recover_stale_runs(self, transport_prefix: str) -> int:
        """Mark stale runs as failed. Returns count recovered."""
```

---

## GitManager (`gluon.git_manager`)

```python
class GitManager:
    def __init__(
        self,
        store: GluonStore,
        sync_interval: int = 300,
    ):
        """Initialize git manager."""

    async def refresh_status(self, project: Project) -> GitStatus:
        """Refresh and return git status for a project."""

    async def pre_task_sync(self, project: Project) -> GitSyncResult:
        """Pre-task sync: auto-commit, fetch, fast-forward."""

    async def post_task_sync(
        self,
        project: Project,
        commit_message: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> GitSyncResult:
        """Post-task sync: commit and push."""

    async def start_background_sync(self) -> None:
        """Start background fetch loop for all projects."""

    async def stop_background_sync(self) -> None:
        """Stop background sync."""
```

### GitSyncResult

```python
@dataclass
class GitSyncResult:
    success: bool
    action: str           # 'none', 'commit', 'fetch', 'pull', 'push'
    message: str
    error: str | None = None
```
