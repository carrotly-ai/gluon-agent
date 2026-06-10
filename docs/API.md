# API Reference

Internal Python API and REST endpoints for Gluon Agent.

- [REST API](#rest-api-gluonwebapi) - HTTP endpoints served by the web dashboard
- [Python API](#models-gluonmodels) - Internal models, store, agent, and orchestrator

---

## REST API (`gluon.web.api`)

Base URL: `http://localhost:45866/api`

WebSocket: `ws://localhost:45866/api/ws` (real-time run updates, log streaming)

> The tables below cover the core endpoints but are not exhaustive — newer 0.12
> route groups (schedules, tasks, fork/snooze, approvals, formulas, queues,
> SDK/Claude sessions) may not all be listed. The **authoritative, always-current**
> reference is the auto-generated OpenAPI spec at `/api/docs` (Swagger) and
> `/api/openapi.json`.

### Runs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs` | List all runs (supports `?project_id=`, `?status=`, `?limit=`) |
| `GET` | `/runs/{run_id}` | Get run details |
| `POST` | `/runs` | Create and start a new run |
| `POST` | `/runs/{run_id}/cancel` | Cancel a running task |
| `POST` | `/runs/{run_id}/resume` | Resume a paused/review task |
| `POST` | `/runs/{run_id}/recover` | Recover from context overflow |
| `POST` | `/runs/{run_id}/stop-loop` | Stop a Ralph Loop |
| `POST` | `/runs/{run_id}/archive` | Archive a completed run |
| `POST` | `/runs/{run_id}/unarchive` | Unarchive a run |
| `POST` | `/runs/{run_id}/status` | Update run status |
| `POST` | `/runs/{run_id}/pr-status` | Update PR status |
| `POST` | `/runs/{run_id}/create-pr` | Create PR from run's branch |
| `POST` | `/runs/{run_id}/merge` | Merge run's branch to main |
| `POST` | `/runs/{run_id}/queue-followup` | Queue a follow-up message |
| `PUT` | `/runs/{run_id}/queue/{message_id}` | Edit a queued message |
| `DELETE` | `/runs/{run_id}/queue/{message_id}` | Delete a queued message |
| `DELETE` | `/runs/{run_id}/queue` | Clear all queued messages |

### Session & History

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs/{run_id}/session-history` | Get Claude session message history |
| `GET` | `/runs/{run_id}/iterations` | Get Ralph Loop iteration history |
| `GET` | `/runs/{run_id}/logs` | Get run logs (stdout/stderr/messages) |

### Questions (Interactive)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs/{run_id}/questions` | Get pending questions from agent |
| `POST` | `/questions/{question_id}/answer` | Answer a pending question |

### Supervision

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs/{run_id}/supervision` | Get supervision status |
| `POST` | `/runs/{run_id}/supervision/evaluate` | Evaluate run for auto-resume |
| `POST` | `/runs/{run_id}/supervision/disable` | Disable supervision for a run |

### Git & Commits

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs/{run_id}/commits` | Get commits from run's branch |
| `GET` | `/runs/{run_id}/commits/{sha}` | Get commit details |
| `GET` | `/runs/{run_id}/files` | Get files changed on branch |
| `GET` | `/runs/{run_id}/files/{file_path}/diff` | Get unified diff for a file |
| `GET` | `/projects/{project_id}/git/status` | Get git status |
| `POST` | `/projects/{project_id}/git/refresh` | Refresh git status |
| `POST` | `/projects/{project_id}/git/sync` | Auto-commit, fetch, and fast-forward |
| `GET` | `/projects/{project_id}/branches` | List branches |
| `POST` | `/projects/{project_id}/branches/rename` | Rename a branch |
| `POST` | `/projects/{project_id}/branches/change-base` | Change base branch |
| `DELETE` | `/projects/{project_id}/branches/{branch_name}` | Delete a branch |
| `POST` | `/git/refresh-all` | Refresh git status for all projects |

### Conflict & Rebase

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/projects/{project_id}/conflicts` | Detect merge conflicts |
| `GET` | `/projects/{project_id}/conflicts/{file_path}` | Get 3-way conflict diff |
| `POST` | `/projects/{project_id}/conflicts/resolve` | Resolve conflict (ours/theirs) |
| `POST` | `/projects/{project_id}/rebase` | Start rebase onto main |
| `POST` | `/projects/{project_id}/rebase/continue` | Continue after resolving conflicts |
| `POST` | `/projects/{project_id}/rebase/abort` | Abort rebase |
| `POST` | `/projects/{project_id}/rebase/skip` | Skip current commit in rebase |
| `GET` | `/projects/{project_id}/force-push-check` | Check if force-push is safe |
| `POST` | `/projects/{project_id}/force-push` | Perform force push (with checks) |

### Projects & Workspaces

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/projects` | List all projects |
| `GET` | `/projects/{project_id}` | Get project details |
| `POST` | `/projects` | Create a project |
| `DELETE` | `/projects/{project_id}` | Delete a project |
| `GET` | `/projects/{project_id}/files` | List files in project directory |
| `GET` | `/workspaces` | List all workspaces |
| `POST` | `/workspaces` | Create a workspace |
| `DELETE` | `/workspaces/{workspace_id}` | Delete a workspace |
| `POST` | `/workspaces/{workspace_id}/scan` | Scan workspace for new projects |

### Images & Attachments

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/images/upload` | Upload an image |
| `GET` | `/images/{id}` | Get image metadata |
| `GET` | `/images/{id}/file` | Get image file |
| `DELETE` | `/images/{id}` | Delete an image |
| `GET` | `/runs/{run_id}/attachments` | List images attached to a run |
| `POST` | `/runs/{run_id}/attachments` | Attach image to a run |
| `DELETE` | `/runs/{run_id}/attachments/{image_id}` | Remove image from a run |

### Usage & Analytics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/usage/summary` | Aggregate usage (cost, tokens, run count) |
| `GET` | `/usage/by-project` | Usage broken down by project |
| `GET` | `/usage/by-day` | Daily usage trends |
| `GET` | `/usage/runs` | Per-run usage breakdown |

### Settings

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/settings` | Get all settings |
| `PUT` | `/settings/{key}` | Update a setting |

### Webhooks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhooks/github` | GitHub webhook receiver |
| `GET` | `/webhooks` | List webhook configurations |
| `POST` | `/webhooks` | Create a webhook |
| `DELETE` | `/webhooks/{id}` | Delete a webhook |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | System status |
| `GET` | `/version` | Version info |
| `GET` | `/commands` | List available slash commands |
| `GET` | `/projects/{id}/commands` | Project-specific slash commands |
| `GET` | `/sandbox/status` | Sandbox environment status |

### Authentication & Users (D5 — opt-in via `GLUON_AUTH_ENABLED=true`)

When auth is disabled, every endpoint accepts unauthenticated requests and the auth endpoints below either no-op or return placeholders. See [AUTH.md](AUTH.md) for the full model.

#### Sessions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/auth/me` | Current user (returns `SYSTEM_USER` placeholder when no session). Always 200. |
| `GET` | `/auth/providers` | Feature-detection: `{auth_enabled, local: bool, oidc: {name, login_url} \| null}`. Drives the LoginPage UI. |
| `POST` | `/auth/login` | Local password login. Body: `{username, password}`. Sets `gluon_session` cookie. |
| `POST` | `/auth/logout` | Invalidate the current session and clear the cookie. |

#### OIDC flow (D5 Phase 3 — see [`AUTH-OIDC.md`](AUTH-OIDC.md))

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/auth/oidc/login` | 302 to the IdP authorize URL. Stashes state+nonce in a signed cookie. |
| `GET` | `/auth/oidc/callback` | Handles the redirect back, validates the ID token (signature/iss/aud/nonce), creates session. On error redirects to `/?oidc_error=…`. |

#### User management (admin-only — `require_admin` dependency)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/users` | List users. `?include_disabled=true` includes soft-deleted accounts. |
| `POST` | `/users` | Create a user. Body: `{username, password, display_name?, email?, role}`. |
| `PATCH` | `/users/{user_id}` | Update display_name / email / role / disabled / telegram_user_id / discord_user_id. Pass `0` for chat IDs to clear. Returns 409 if a chat ID is already bound to another user. |
| `DELETE` | `/users/{user_id}` | Soft-delete (preserves attribution links + invalidates sessions). |
| `POST` | `/users/{user_id}/password` | Reset password. Body: `{new_password, current_password?}`. Admins may omit `current_password` to reset others'; non-admins must provide it for self-service. |

#### Self-serve transport linking (D5 Phase 4)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/link-codes` | Generate a 10-min one-time code for the calling user. Body: `{transport: "telegram" \| "discord"}`. Refused (400) in single-user mode. |
| `GET` | `/auth/links` | Show the current user's bound chat IDs: `{telegram_user_id, discord_user_id}`. |
| `DELETE` | `/auth/links/{transport}` | Unbind the calling user's chat account on `transport`. |

### WebSocket

Connect to `ws://localhost:45866/api/ws` for real-time updates:

- Run status changes (pending → running → review → completed)
- Live log streaming during execution
- Pending questions from agents
- Cost and token usage updates

---

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
    REVIEW = "review"         # Awaiting action/approval
    COMPLETED = "completed"   # Finished successfully
    FAILED = "failed"         # Error occurred
    CANCELLED = "cancelled"   # User cancelled
```

### CircuitState

```python
class CircuitState(str, Enum):
    CLOSED = "CLOSED"         # Normal operation, execution allowed
    HALF_OPEN = "HALF_OPEN"   # Monitoring mode, checking for recovery
    OPEN = "OPEN"             # Execution halted, requires intervention
```

### TaskProfile

```python
class TaskProfile(str, Enum):
    QUICK = "quick"           # Haiku, no thinking
    STANDARD = "standard"     # Sonnet, 10K thinking tokens
    DEEP = "deep"             # Opus 4.8, 32K thinking tokens
    PLANNING = "planning"     # Opus 4.8, 16K thinking, force planning
```

### ThinkingBudget

```python
class ThinkingBudget(str, Enum):
    NONE = "none"             # 0 tokens - no thinking
    LOW = "low"               # 4,000 tokens - simple reasoning
    MEDIUM = "medium"         # 10,000 tokens - moderate complexity
    HIGH = "high"             # 16,000 tokens - complex analysis
    ULTRATHINK = "ultrathink" # 32,000 tokens - maximum reasoning
```

### SupervisionPolicy

```python
class SupervisionPolicy(str, Enum):
    AGGRESSIVE = "aggressive"     # Resume if any chance of success
    CONSERVATIVE = "conservative" # Resume only with high confidence of progress
    MANUAL = "manual"             # Never auto-resume (current behavior)
```

### QuestionStatus

```python
class QuestionStatus(str, Enum):
    PENDING = "pending"           # Waiting for user response
    ANSWERED = "answered"         # User provided answer
    AUTO_ANSWERED = "auto_answered"  # System auto-answered (timeout/Ralph)
    EXPIRED = "expired"           # Question timed out without answer
```

### ExecutionRun

```python
class ExecutionRun(BaseModel):
    # Core fields
    id: str                              # UUID, auto-generated
    project_id: str                      # FK to Project
    session_id: str | None = None        # FK to Session (created when run starts)
    claude_session_id: str | None = None # Claude SDK session ID for resume
    pid: int | None = None               # OS process ID for cancellation
    status: RunStatus                    # Current status (pending, running, review, completed, failed, cancelled)
    prompt: str                          # Task prompt (may be updated on resume)
    original_prompt: str | None = None   # Original task prompt (preserved across resumes)
    model: str | None = None             # Requested model (e.g., "claude-haiku-4.5", "haiku")
    initiator: str | None = None         # Who started: 'cli', 'telegram:123', 'discord:456'
    thread_id: str | None = None         # Discord/Slack thread ID for resume detection
    metadata: dict | None = None         # Task profile options and other metadata
    created_at: datetime                 # Auto-set on creation
    started_at: datetime | None          # When execution started
    completed_at: datetime | None        # When execution ended
    exit_code: int | None                # Exit code (0 = success)
    log_path: Path | None                # Path to log directory
    error_message: str | None            # Error message if failed

    # Cost & usage tracking
    cost_usd: float | None = None        # Total cost in USD
    input_tokens: int | None = None      # Input tokens used
    output_tokens: int | None = None     # Output tokens generated
    model_used: str | None = None        # Model tier (e.g., "sonnet", "opus")
    max_cost_usd: float | None = None    # Optional cost cap

    # Git worktree fields
    branch_name: str | None = None       # Worktree branch name (e.g., "gluon-task/abc123")
    source_branch: str | None = None     # Branch forked from (usually main)
    worktree_path: str | None = None     # Worktree directory path
    use_worktree: bool = False           # Whether worktree isolation is enabled
    git_commit_sha: str | None = None    # SHA of final commit

    # PR management
    pr_number: int | None = None         # GitHub PR number
    pr_url: str | None = None            # PR URL
    pr_status: str | None = None         # 'open', 'merged', 'closed', 'draft'
    pr_mergeable: str | None = None      # 'MERGEABLE', 'CONFLICTING', 'UNKNOWN'

    # Resume tracking
    resume_count: int = 0                # Number of times this run has been resumed
    last_resumed_at: datetime | None = None  # When last resumed

    # Context overflow recovery tracking
    recovery_count: int = 0              # Times recovered from context overflow
    last_recovery_at: datetime | None = None  # When last recovery happened
    recovery_from_run_id: str | None = None  # Parent run ID if this is a recovery run
    is_recovering: bool = False          # Currently in recovery process
    recovery_item_count: int = 0         # Progress counter during recovery

    # PR monitoring tracking
    last_comment_id: int | None = None   # Last processed PR comment ID
    last_check_sha: str | None = None    # Last checked commit SHA for CI
    auto_resume_enabled: bool = True     # Allow auto-resume for this run
    auto_resume_count: int = 0           # Number of auto-resumes (max 5)

    # Ralph Loop fields
    ralph_enabled: bool = False          # Whether Ralph Loop is active
    loop_count: int = 0                  # Current iteration count
    max_loops: int = 50                  # Max iterations allowed
    circuit_state: CircuitState = CircuitState.CLOSED
    consecutive_no_progress: int = 0     # Loops without file changes
    consecutive_same_error: int = 0      # Loops with same error
    last_progress_loop: int = 0          # Last loop with progress
    last_error_hash: str | None = None   # Hash of last error for repetition detection
    half_open_iterations: int = 0        # Iterations spent in HALF_OPEN state
    completion_signals: int = 0          # Consecutive completion signals
    test_only_loops: int = 0             # Consecutive test-only loops
    completion_confidence: float = 0.0   # Confidence score 0-100
    completion_reason: str | None = None # Why loop exited

    # Rate limiting
    calls_this_hour: int = 0             # API calls in current hour
    hour_start: datetime | None = None   # When current hour started
    max_calls_per_hour: int = 100        # Hourly API call limit

    # Supervision fields
    supervision_config: SupervisionConfig | None = None
    supervision_auto_resume_count: int = 0
    last_supervision_check_at: datetime | None = None
    last_supervision_resume_at: datetime | None = None
    supervision_disabled_reason: str | None = None

    # Archive & queue
    archived: bool = False               # Whether run is archived
    archived_at: datetime | None = None  # When archived
    queued_messages: list[QueuedMessage] = None  # Follow-up messages

    # Snapshot tracking
    changes_snapshotted: bool = False    # Whether commits/files have been snapshotted
    snapshot_at: datetime | None = None  # When snapshot was captured

    @property
    def is_active(self) -> bool:
        """Check if run is pending, running, or in review."""

    @property
    def is_resumable(self) -> bool:
        """Check if run can be resumed (has session and is in terminal state)."""

    @property
    def duration_seconds(self) -> float | None:
        """Calculate run duration if started."""
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
    is_git_repo: bool = False            # Whether path is a git repo
    branch: str | None = None            # Current branch name
    remote: str | None = None            # Remote name (usually 'origin')
    remote_url: str | None = None        # Remote URL
    has_uncommitted: bool = False        # Has uncommitted changes
    uncommitted_count: int = 0           # Number of uncommitted files
    commits_ahead: int = 0               # Commits ahead of remote
    commits_behind: int = 0              # Commits behind remote
    last_fetch_at: datetime | None = None  # Last fetch time
    last_push_at: datetime | None = None    # Last push time
    last_commit_at: datetime | None = None  # Last commit time
    is_rebase_in_progress: bool = False  # Whether rebase is in progress
    is_merge_in_progress: bool = False   # Whether merge is in progress
    conflict_operation: str | None = None  # "rebase", "merge", "cherry_pick"
    conflicted_files: list[str] = []     # Files with conflicts
    rebase_current_step: int | None = None  # Current rebase step
    rebase_total_steps: int | None = None   # Total rebase steps

    @property
    def is_diverged(self) -> bool:
        """True if local and remote have diverged (both ahead and behind)."""

    @property
    def is_clean(self) -> bool:
        """True if working tree is clean and in sync with remote."""

    @property
    def needs_pull(self) -> bool:
        """True if behind remote and can fast-forward."""

    @property
    def needs_push(self) -> bool:
        """True if ahead of remote."""

    @property
    def has_conflicts(self) -> bool:
        """True if there are unresolved conflicts."""

    @property
    def has_operation_in_progress(self) -> bool:
        """True if a rebase or merge is in progress."""
```

### GitSyncResult

```python
class GitSyncResult(BaseModel):
    success: bool                   # Whether operation succeeded
    action: str                     # "none", "commit", "pull", "push", "commit+push", etc.
    message: str                    # Human-readable status message
    error: str | None = None        # Error message if failed
    commits_pulled: int = 0         # Number of commits pulled
    commits_pushed: int = 0         # Number of commits pushed
    files_committed: int = 0        # Number of files committed
```

### RalphLoopIteration

```python
class RalphLoopIteration(BaseModel):
    id: str                          # UUID, auto-generated
    run_id: str                      # FK to ExecutionRun
    loop_number: int                 # 1-indexed iteration number
    started_at: datetime             # When iteration started
    ended_at: datetime | None = None # When iteration ended

    # Execution results
    files_changed: int = 0           # Number of git file changes
    has_errors: bool = False         # Whether errors occurred
    error_summary: str | None = None # First ~200 chars of error
    output_length: int = 0           # Length of Claude output

    # Analysis results
    is_test_only: bool = False       # Only ran tests, no implementation
    has_completion_signal: bool = False  # Claude indicated "done"
    progress_detected: bool = False  # Files changed or meaningful work
    confidence_score: float = 0.0    # Completion confidence 0-100

    # Claude SDK info
    claude_session_id: str | None = None
    cost_usd: float = 0.0
    tokens_used: int = 0

    @property
    def duration_seconds(self) -> float | None:
        """Get duration in seconds if completed."""
```

### PendingQuestion

```python
class PendingQuestion(BaseModel):
    id: str                          # UUID, auto-generated
    run_id: str                      # FK to ExecutionRun
    question_index: int = 0          # Index within questions array
    question_text: str               # The question being asked
    header: str                      # Short label (e.g., "Database", "UI Style")
    options: list[dict[str, str]]    # [{label: str, description: str}, ...]
    multi_select: bool = False       # Whether multiple options allowed
    status: QuestionStatus           # pending, answered, auto_answered, expired
    created_at: datetime             # When question was created
    answered_at: datetime | None = None  # When user answered
    expires_at: datetime | None = None   # When auto-answer kicks in
    selected_labels: list[str] = []  # Selected option label(s)
    answer_source: str | None = None # "user", "auto_recommended", "auto_first", "ralph"

    @property
    def is_pending(self) -> bool:
        """Check if question is still awaiting answer."""

    @property
    def answer_string(self) -> str:
        """Get answer as comma-separated string for SDK."""
```

### CommitSnapshot

```python
class CommitSnapshot(BaseModel):
    id: str                          # UUID, auto-generated
    run_id: str                      # FK to ExecutionRun
    sha: str                         # Git commit SHA
    message: str                     # Commit subject line
    full_message: str | None = None  # Full commit body
    author: str                      # Author name
    author_email: str | None = None  # Author email
    date: datetime                   # Commit timestamp
    ordinal: int                     # 1-indexed order in commit list
    created_at: datetime             # When snapshot was captured
```

### FileChangeSnapshot

```python
class FileChangeSnapshot(BaseModel):
    id: str                          # UUID, auto-generated
    run_id: str                      # FK to ExecutionRun
    file_path: str                   # Path relative to repo root
    change_type: str                 # "added", "modified", "deleted", "renamed"
    additions: int = 0               # Lines added
    deletions: int = 0               # Lines deleted
    created_at: datetime             # When snapshot was captured
```

### SupervisionConfig

```python
class SupervisionConfig(BaseModel):
    enabled: bool = True             # Whether supervision is enabled
    policy: SupervisionPolicy        # aggressive, conservative, manual
    max_auto_resumes: int = 5        # Maximum auto-resume attempts
    min_time_between_resumes: int = 60  # Minimum seconds between resumes
    auto_resume_triggers: list[str]  # ["incomplete_work", "test_only", "low_confidence"]
```

### SupervisionDecision

```python
class SupervisionDecision(BaseModel):
    id: str                          # UUID, auto-generated
    run_id: str                      # FK to ExecutionRun
    timestamp: datetime              # When decision was made
    decision: str                    # "resume", "skip", "hold", "disable"
    reason: str                      # Human-readable explanation
    trigger: str | None = None       # "scheduler", "manual", "pr_comment"
    circuit_state: CircuitState | None = None
    completion_confidence: float | None = None
    calls_this_hour: int | None = None
    cost_usd: float | None = None
    auto_resume_count: int | None = None
    policy: SupervisionPolicy | None = None
```

### QueuedMessage

```python
class QueuedMessage(BaseModel):
    id: str                          # UUID (8 chars)
    message: str                     # Message text
    queued_at: datetime              # When message was queued
```

### Worker

```python
class WorkerType(str, Enum):
    LOCAL = "local"               # Local subprocess execution
    REMOTE = "remote"             # Remote worker via HTTP API

class WorkerStatus(str, Enum):
    HEALTHY = "healthy"           # Worker responding normally
    UNHEALTHY = "unhealthy"       # Worker missed heartbeats
    OFFLINE = "offline"           # Worker explicitly offline

class Worker(BaseModel):
    id: str                        # UUID, auto-generated
    name: str                      # Human-readable name (unique)
    type: WorkerType = WorkerType.LOCAL
    base_url: str | None = None    # For remote workers (e.g., "http://worker1:8080")
    api_key: str                   # API key for authentication
    max_concurrent: int = 4        # Maximum concurrent jobs
    status: WorkerStatus = WorkerStatus.HEALTHY
    last_heartbeat: datetime | None = None
    created_at: datetime           # Auto-set on creation
    updated_at: datetime           # Updated on modifications
    active_jobs: int = 0           # Current number of running jobs (not persisted)

    @property
    def is_available(self) -> bool:
        """Check if worker can accept more jobs."""

    @property
    def available_slots(self) -> int:
        """Number of available job slots."""
```

### Job

```python
class JobStatus(str, Enum):
    QUEUED = "queued"             # Waiting in queue
    ASSIGNED = "assigned"         # Assigned to worker, pending execution
    RUNNING = "running"           # Currently executing
    COMPLETED = "completed"       # Finished successfully
    FAILED = "failed"             # Error occurred

class Job(BaseModel):
    id: str                        # UUID, auto-generated
    run_id: str                    # FK to ExecutionRun
    project_id: str                # FK to Project (denormalized for filtering)
    prompt: str                    # Task prompt
    priority: int = 5              # 1 (highest) to 10 (lowest)
    status: JobStatus = JobStatus.QUEUED
    worker_id: str | None = None   # FK to Worker (assigned worker)
    created_at: datetime           # When job was created
    assigned_at: datetime | None = None  # When assigned to worker
    started_at: datetime | None = None   # When execution started
    completed_at: datetime | None = None # When execution completed
    error_message: str | None = None     # Error message if failed
    model: str | None = None       # Requested model tier
    use_worktree: bool = False     # Whether to use git worktree
    session_id: str | None = None  # Session ID to resume
    lease_expires_at: datetime | None = None  # Worker lease expiration

    @property
    def is_lease_expired(self) -> bool:
        """Check if worker lease has expired."""
```

### ImageAttachment

```python
class ImageAttachment(BaseModel):
    id: str                        # UUID, auto-generated
    file_path: str                 # Relative path within storage
    original_name: str             # User's original filename
    mime_type: str | None = None   # e.g., "image/png"
    size_bytes: int                # File size in bytes
    hash: str                      # SHA256 hash for deduplication
    created_at: datetime           # When image was uploaded
    updated_at: datetime           # When image was updated

    @property
    def full_path(self) -> Path:
        """Get full path to image file in storage."""

    def to_markdown(self) -> str:
        """Return markdown image reference."""
```

### WebhookConfig

```python
class WebhookConfig(BaseModel):
    id: str                        # UUID, auto-generated
    handler: str                   # "github", "gitlab", "bitbucket"
    project_id: str | None = None  # FK to Project (None = match by repo name)
    secret_key: str                # Webhook secret for signature verification
    events: list[str] = []         # ["push", "pull_request", "issues"]
    prompt_template: str | None = None  # Custom prompt template
    enabled: bool = True           # Whether webhook is active
    created_at: datetime           # When webhook was created
    updated_at: datetime           # When webhook was updated
    branches: list[str] | None = None  # Trigger only for these branches (None = all)
    ignore_branches: list[str] | None = None  # Ignore these branches
    labels: list[str] | None = None  # Only issues/PRs with these labels

    def matches_branch(self, branch: str) -> bool:
        """Check if webhook should trigger for this branch."""

    def matches_event(self, event_type: str) -> bool:
        """Check if webhook handles this event type."""
```

### MessageRunMapping

```python
class MessageRunMapping(BaseModel):
    id: str                        # UUID, auto-generated
    transport: str                 # "discord", "telegram", etc.
    message_id: str                # Platform-specific message ID
    run_id: str                    # FK to ExecutionRun
    chat_id: str                   # Channel/chat ID for scoping
    user_id: str                   # Who initiated the original run
    created_at: datetime           # When mapping was created
    expires_at: datetime           # TTL-based expiration
```

### ChatHistoryEntry

```python
class ChatHistoryEntry(BaseModel):
    id: str                        # UUID, auto-generated
    user_id: str                   # Universal user ID (e.g., 'telegram:123')
    transport: str                 # "telegram", "discord", etc.
    role: str                      # "user" or "assistant"
    text: str                      # Message content
    created_at: datetime           # When message was sent
    expires_at: datetime           # TTL-based expiration
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
        permission_mode: str = "bypassPermissions",
        cli_path: Path | str | None = None,
        question_handler: QuestionHandler | None = None,
        run_id: str | None = None,
        max_thinking_tokens: int | None = None,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        force_planning: bool = False,
        sandbox_enabled: bool = True,
    ):
        """
        Initialize agent with configuration.

        Args:
            model: Model tier (opus/sonnet/haiku) or full Bedrock model ID
            allowed_tools: List of allowed tool names (default: Read, Write, Edit, Bash, Glob, Grep, Task, TodoWrite)
            permission_mode: "bypassPermissions" or "acceptEdits"
            cli_path: Path to Claude CLI executable
            question_handler: Callback for handling user questions
            run_id: Optional run ID for logging/tracking
            max_thinking_tokens: Maximum tokens for extended thinking
            max_turns: Maximum conversation turns
            max_budget_usd: Cost budget cap
            force_planning: Force plan-first workflow
            sandbox_enabled: Enable sandbox environment
        """

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

        Args:
            working_dir: Working directory for execution
            prompt: User prompt or task description
            resume_session_id: Optional session ID to resume from

        Yields:
            AgentMessage during execution
            AgentResult as final result
        """
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

### find_mcp_config

```python
def find_mcp_config(working_dir: Path | None = None) -> Path | None:
    """
    Find MCP configuration file with layered precedence.

    Priority:
    1. Project-level .mcp.json (if working_dir provided)
    2. Host's ~/.claude/.mcp.json

    Args:
        working_dir: Optional project directory to check for local .mcp.json

    Returns:
        Path to MCP config file, or None if not found
    """
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

class GitSyncError(Exception):
    """Raised when git sync fails and cannot proceed."""

class GitOperationError(Exception):
    """Base exception for git operations."""

class GitMergeConflictError(GitOperationError):
    """Raised when a merge or rebase results in conflicts."""

class GitRebaseInProgressError(GitOperationError):
    """Raised when a rebase is already in progress."""

class GitForcePushRequiredError(GitOperationError):
    """Raised when a force push would be required."""

class GitBranchNotFoundError(GitOperationError):
    """Raised when a branch is not found."""
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
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project.

        Automatically resumes the last session if available,
        unless force_new_session is True.

        Args:
            project_name: Name or ID of the project
            prompt: User prompt to execute
            force_new_session: Force creation of new session
            model: Model tier (opus/sonnet/haiku). Overrides profile's model.
            run_id: Optional run ID to link to existing ExecutionRun
            session_id: Specific session ID to resume (overrides auto-detection)
            use_worktree: Execute in isolated Git worktree (default: False)
            initiator: Source of execution (e.g., "cli:foreground", "telegram:123")
            profile: Task profile (quick/standard/deep/planning). Defaults to standard.
            max_thinking_tokens: Override thinking token budget directly.
            thinking_budget: Override via preset (none/low/medium/high/ultrathink).
            max_turns: Override max conversation turns.
            max_budget_usd: Override max cost budget.
            force_planning: Override planning mode (True = plan before executing).

        Yields:
            AgentMessage during execution
            AgentResult as final yield
        """

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
            prompt: Optional new prompt (uses "Continue from where you left off." if not provided)
            model: Model tier to use (opus/sonnet/haiku). Overrides profile's model.
            profile: Task profile (quick/standard/deep/planning).

        Yields:
            AgentMessage during execution
            AgentResult as final yield

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

### ChatMessage

```python
@dataclass
class ChatMessage:
    role: str  # "user" or "assistant"
    text: str
```

### ChatResponse

```python
@dataclass
class ChatResponse:
    text: str                           # Response text
    action_taken: str | None = None     # Tool/action that was executed
    action_result: dict | None = None   # Result details from action
```

### GluonChatAgent

```python
class GluonChatAgent:
    def __init__(self, orchestrator: Orchestrator | None = None):
        """
        Initialize chat agent with optional orchestrator.

        Args:
            orchestrator: Orchestrator instance (created if not provided)
        """

    async def chat(
        self,
        message: str,
        history: list[ChatMessage] | None = None,
        reply_context: str | None = None,
    ) -> ChatResponse:
        """
        Process natural language message and return response.

        Args:
            message: The user's message
            history: Recent conversation history (oldest first)
            reply_context: If user is replying to a specific message, that message's text

        Returns:
            ChatResponse with text and optional pending action

        May set self._pending_task if an action needs to be executed by caller.
        """

    def get_pending_task(self) -> dict | None:
        """Get pending task requiring execution by caller."""

    def clear_pending_task(self) -> None:
        """Clear pending task after execution."""
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
    def __init__(self, store: GluonStore):
        """
        Initialize git manager.

        Args:
            store: GluonStore instance for persisting git status
        """

    async def refresh_status(self, project: Project) -> GitStatus:
        """
        Refresh and return git status for a project.

        Queries the git repository and updates cached status in database.
        Returns the current GitStatus.
        """

    async def pre_task_sync(self, project: Project) -> GitSyncResult:
        """
        Pre-task sync: auto-commit uncommitted changes, fetch, and fast-forward.

        Called before task execution to ensure clean working state.
        """

    async def post_task_sync(
        self,
        project: Project,
        commit_message: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> GitSyncResult:
        """
        Post-task sync: commit all changes and push to remote.

        Called after task execution to persist changes.
        Captures commit and file snapshots for later reference.
        """

    async def start_background_sync(self) -> None:
        """Start background fetch loop for all projects."""

    async def stop_background_sync(self) -> None:
        """Stop background sync."""
```

Note: `GitSyncResult` is documented in the Models section above.
