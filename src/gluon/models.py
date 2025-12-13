"""Pydantic models for Gluon Agent."""

import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


def expand_path(path_str: str | Path) -> Path:
    """Expand environment variables and user home directory in path.

    Supports:
    - $VAR or ${VAR} for environment variables
    - ~ for home directory
    - Relative paths

    Args:
        path_str: Path string potentially containing variables or ~

    Returns:
        Expanded Path object

    Example:
        expand_path("${HOME}/workspaces/project") → /Users/mcutler/workspaces/project
        expand_path("~/workspaces/project") → /Users/mcutler/workspaces/project
    """
    path_obj = Path(path_str)

    # Expand ~ to home directory
    if str(path_obj).startswith("~"):
        path_obj = path_obj.expanduser()

    # Expand environment variables
    expanded_str = os.path.expandvars(str(path_obj))
    path_obj = Path(expanded_str)

    return path_obj


class SessionStatus(str, Enum):
    """Status of a Claude Code session."""

    ACTIVE = "active"  # Currently running
    PAUSED = "paused"  # Can be resumed
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Error occurred


class RunStatus(str, Enum):
    """Status of an execution run."""

    PENDING = "pending"  # Queued but not started
    RUNNING = "running"  # Currently executing
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Error occurred
    CANCELLED = "cancelled"  # Manually cancelled


# Project markers - files that indicate a directory is a project
PROJECT_MARKERS = [
    "package.json",  # Node.js/JavaScript
    "pyproject.toml",  # Python (modern)
    "setup.py",  # Python (legacy)
    "Cargo.toml",  # Rust
    "go.mod",  # Go
    "pom.xml",  # Java/Maven
    "build.gradle",  # Java/Gradle
    "Gemfile",  # Ruby
    "composer.json",  # PHP
    "mix.exs",  # Elixir
    "pubspec.yaml",  # Dart/Flutter
    ".git",  # Any git repo
]


class Workspace(BaseModel):
    """A workspace directory containing multiple projects."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str  # Human-readable name (unique)
    path: Path  # Path (may contain ${VAR} or ~, expanded at runtime)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    scan_depth: int = 1  # How deep to scan for projects (1 = immediate children)
    auto_discover: bool = True  # Whether to auto-discover projects
    ignore_patterns: list[str] = Field(default_factory=lambda: [".*", "node_modules", "__pycache__", "venv", ".venv"])

    def model_post_init(self, __context: Any) -> None:
        """Convert path to Path object if needed."""
        if isinstance(self.path, str):
            self.path = Path(self.path)

    @property
    def expanded_path(self) -> Path:
        """Get the expanded path with environment variables substituted.

        Returns an absolute path with all variables expanded.
        Call this when you need the actual filesystem path.
        """
        expanded = expand_path(self.path)
        if not expanded.is_absolute():
            expanded = expanded.resolve()
        return expanded

    def scan_for_projects(self) -> list[Path]:
        """Scan workspace for project directories."""
        projects: list[Path] = []

        path = self.expanded_path
        if not path.exists() or not path.is_dir():
            return projects

        for item in path.iterdir():
            # Skip ignored patterns
            if any(item.match(pattern) for pattern in self.ignore_patterns):
                continue

            if not item.is_dir():
                continue

            # Check if it looks like a project
            if self._is_project(item):
                projects.append(item)

        return sorted(projects)

    def _is_project(self, path: Path) -> bool:
        """Check if a directory looks like a project."""
        for marker in PROJECT_MARKERS:
            if (path / marker).exists():
                return True
        return False


class Project(BaseModel):
    """A registered project that can be managed by Gluon."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str  # Human-readable name (unique)
    path: Path  # Path (may contain ${VAR} or ~, expanded at runtime)
    workspace_id: str | None = None  # FK to Workspace (None = standalone project)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] | None = None  # Optional extra data

    def model_post_init(self, __context: Any) -> None:
        """Convert path to Path object if needed."""
        if isinstance(self.path, str):
            self.path = Path(self.path)

    @property
    def expanded_path(self) -> Path:
        """Get the expanded path with environment variables substituted.

        Returns an absolute path with all variables expanded.
        Call this when you need the actual filesystem path.
        """
        expanded = expand_path(self.path)
        if not expanded.is_absolute():
            expanded = expanded.resolve()
        return expanded

    @property
    def is_workspace_managed(self) -> bool:
        """Check if this project is managed by a workspace."""
        return self.workspace_id is not None


class Session(BaseModel):
    """A Claude Code session associated with a project."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str  # FK to Project
    claude_session_id: str | None = None  # Session ID from Claude SDK (for resume)
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_prompt: str | None = None  # Last user prompt
    total_cost_usd: float = 0.0
    total_turns: int = 0

    def mark_paused(self) -> None:
        """Mark session as paused (can be resumed)."""
        self.status = SessionStatus.PAUSED
        self.updated_at = utc_now()

    def mark_completed(self) -> None:
        """Mark session as completed."""
        self.status = SessionStatus.COMPLETED
        self.updated_at = utc_now()

    def mark_failed(self) -> None:
        """Mark session as failed."""
        self.status = SessionStatus.FAILED
        self.updated_at = utc_now()

    def add_cost(self, cost: float) -> None:
        """Add to total cost."""
        self.total_cost_usd += cost
        self.updated_at = utc_now()

    def increment_turns(self) -> None:
        """Increment turn count."""
        self.total_turns += 1
        self.updated_at = utc_now()


class ExecutionRun(BaseModel):
    """A background execution run of a Claude Code task."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None  # FK to Session (created when run starts)
    claude_session_id: str | None = None  # Claude SDK session ID for resume (NOT a FK)
    project_id: str  # FK to Project
    pid: int | None = None  # OS process ID for cancellation
    status: RunStatus = RunStatus.PENDING
    prompt: str  # The task prompt
    model: str | None = None  # Requested model (e.g., "claude-haiku-4.5", "haiku")
    initiator: str | None = None  # Who started the run (e.g., "cli", "telegram:12345")
    thread_id: str | None = None  # Discord/Slack thread ID for resume detection
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    log_path: Path | None = None  # Path to log directory
    error_message: str | None = None

    # Cost tracking
    cost_usd: float | None = None  # Total cost for this run
    input_tokens: int | None = None  # Input tokens used
    output_tokens: int | None = None  # Output tokens generated
    model_used: str | None = None  # Model tier (e.g., "sonnet", "opus")

    # Git/worktree tracking (Phase 7.1)
    branch_name: str | None = None  # "gluon-task/abc123"
    source_branch: str | None = None  # "main", "develop"
    worktree_path: str | None = None  # Path used for execution
    use_worktree: bool = False  # Whether worktree was used
    git_commit_sha: str | None = None  # SHA of final commit
    pr_number: int | None = None  # GitHub PR number
    pr_url: str | None = None  # GitHub PR URL
    pr_status: str | None = None  # 'open', 'merged', 'closed', 'draft'
    pr_mergeable: str | None = None  # 'MERGEABLE', 'CONFLICTING', 'UNKNOWN'

    # Archive tracking
    archived: bool = False  # Whether the run is archived (hidden from board)
    archived_at: datetime | None = None  # When it was archived

    # Resume tracking (in-place resume)
    resume_count: int = 0  # Number of times this run has been resumed
    last_resumed_at: datetime | None = None  # When last resumed

    def mark_running(self, pid: int, log_path: Path) -> None:
        """Mark run as started."""
        self.status = RunStatus.RUNNING
        self.pid = pid
        self.log_path = log_path
        self.started_at = utc_now()

    def mark_completed(self, exit_code: int = 0) -> None:
        """Mark run as completed."""
        self.status = RunStatus.COMPLETED
        self.exit_code = exit_code
        self.completed_at = utc_now()

    def mark_failed(self, error: str, exit_code: int = 1) -> None:
        """Mark run as failed."""
        self.status = RunStatus.FAILED
        self.error_message = error
        self.exit_code = exit_code
        self.completed_at = utc_now()

    def mark_cancelled(self) -> None:
        """Mark run as cancelled."""
        self.status = RunStatus.CANCELLED
        self.completed_at = utc_now()

    def prepare_for_resume(self, new_prompt: str) -> None:
        """
        Prepare run for in-place resume.

        Resets status and timing fields while preserving:
        - run ID, project_id, claude_session_id
        - worktree info (branch_name, worktree_path, source_branch)
        - log_path (logs will be appended)
        - cost tracking (will accumulate)
        """
        self.prompt = new_prompt
        self.status = RunStatus.RUNNING
        self.started_at = utc_now()
        self.completed_at = None
        self.exit_code = None
        self.error_message = None
        self.resume_count += 1
        self.last_resumed_at = utc_now()
        # Reset PID - will be set by mark_running or subprocess

    @property
    def is_resumable(self) -> bool:
        """Check if run can be resumed (completed or failed with session)."""
        return (
            self.status in (RunStatus.COMPLETED, RunStatus.FAILED)
            and self.claude_session_id is not None
        )

    @property
    def is_active(self) -> bool:
        """Check if run is still active (pending or running)."""
        return self.status in (RunStatus.PENDING, RunStatus.RUNNING)

    @property
    def duration_seconds(self) -> float | None:
        """Get duration in seconds if started."""
        if not self.started_at:
            return None
        end = self.completed_at or utc_now()
        return (end - self.started_at).total_seconds()


# ========== Git Models ==========


class GitStatus(BaseModel):
    """Git repository status for a project (cached in database)."""

    is_git_repo: bool = False
    branch: str | None = None
    remote: str | None = None  # e.g., "origin"
    remote_url: str | None = None

    # Working tree status
    has_uncommitted: bool = False
    uncommitted_count: int = 0

    # Sync status relative to remote
    commits_ahead: int = 0  # Local commits not pushed
    commits_behind: int = 0  # Remote commits not pulled

    # Timestamps
    last_fetch_at: datetime | None = None
    last_push_at: datetime | None = None
    last_commit_at: datetime | None = None

    # Conflict and rebase state (Advanced Git Operations)
    is_rebase_in_progress: bool = False
    is_merge_in_progress: bool = False
    conflict_operation: str | None = None  # "rebase", "merge", "cherry_pick"
    conflicted_files: list[str] = Field(default_factory=list)
    rebase_current_step: int | None = None
    rebase_total_steps: int | None = None

    @property
    def is_diverged(self) -> bool:
        """True if local and remote have diverged (both ahead and behind)."""
        return self.commits_ahead > 0 and self.commits_behind > 0

    @property
    def is_clean(self) -> bool:
        """True if working tree is clean and in sync with remote."""
        return (
            not self.has_uncommitted
            and self.commits_ahead == 0
            and self.commits_behind == 0
            and not self.has_conflicts
        )

    @property
    def needs_pull(self) -> bool:
        """True if behind remote and can fast-forward."""
        return self.commits_behind > 0 and self.commits_ahead == 0

    @property
    def needs_push(self) -> bool:
        """True if ahead of remote."""
        return self.commits_ahead > 0 and self.commits_behind == 0

    @property
    def has_conflicts(self) -> bool:
        """True if there are unresolved conflicts."""
        return len(self.conflicted_files) > 0

    @property
    def has_operation_in_progress(self) -> bool:
        """True if a rebase or merge is in progress."""
        return self.is_rebase_in_progress or self.is_merge_in_progress


class GitSyncResult(BaseModel):
    """Result of a git sync operation."""

    success: bool
    action: str  # "none", "commit", "pull", "push", "commit+push", "commit+pull+push"
    message: str
    error: str | None = None

    # Operation details
    commits_pulled: int = 0
    commits_pushed: int = 0
    files_committed: int = 0

    @classmethod
    def ok(cls, action: str, message: str, **kwargs: Any) -> "GitSyncResult":
        """Create a successful result."""
        return cls(success=True, action=action, message=message, **kwargs)

    @classmethod
    def fail(cls, error: str, action: str = "none") -> "GitSyncResult":
        """Create a failed result."""
        return cls(success=False, action=action, message=f"Failed: {error}", error=error)

    @classmethod
    def skip(cls, reason: str) -> "GitSyncResult":
        """Create a skipped result (not a git repo, etc.)."""
        return cls(success=True, action="none", message=reason)


class ChannelMapping(BaseModel):
    """Maps a chat channel to a project for multi-transport support."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    transport: str  # "discord", "slack", etc.
    channel_id: str  # Channel ID (platform-specific)
    project_id: str  # Project ID (FK)
    project_name: str  # Cached project name for convenience
    created_at: datetime = Field(default_factory=utc_now)


# ========== Image Attachment Models (Phase 10.1) ==========


class ImageAttachment(BaseModel):
    """Metadata for an uploaded image attachment."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str  # Relative path within storage (e.g., "ab/abcd1234.png")
    original_name: str  # User's original filename
    mime_type: str | None = None  # e.g., "image/png"
    size_bytes: int
    hash: str  # SHA256 hash for deduplication
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def full_path(self) -> Path:
        """Get full path to image file in storage."""
        return Path.home() / ".gluon" / "images" / self.file_path

    def to_markdown(self) -> str:
        """Return markdown image reference."""
        return f"![{self.original_name}]({self.file_path})"
