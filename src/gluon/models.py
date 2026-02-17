"""Pydantic models for Gluon Agent."""

import os
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


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
    REVIEW = "review"  # Awaiting action/approval
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Error occurred
    CANCELLED = "cancelled"  # Manually cancelled


class CircuitState(str, Enum):
    """Circuit breaker state for ralph loops."""

    CLOSED = "CLOSED"  # Normal operation, execution allowed
    HALF_OPEN = "HALF_OPEN"  # Monitoring mode, checking for recovery
    OPEN = "OPEN"  # Execution halted, requires intervention


class SupervisionPolicy(str, Enum):
    """Supervision policy for auto-resume decisions."""

    AGGRESSIVE = "aggressive"  # Resume if any chance of success
    CONSERVATIVE = "conservative"  # Resume only with high confidence of progress
    MANUAL = "manual"  # Never auto-resume (current behavior)


# ========== Task Profile Models ==========


class TaskProfile(str, Enum):
    """Pre-defined task profiles for common use cases."""

    QUICK = "quick"  # Fast, cheap, simple tasks (Haiku)
    STANDARD = "standard"  # Balanced performance (Sonnet, default)
    DEEP = "deep"  # Maximum reasoning (Opus)
    PLANNING = "planning"  # Force plan-first workflow (Opus)


class ThinkingBudget(str, Enum):
    """Thinking budget presets for extended thinking."""

    NONE = "none"  # 0 tokens - no thinking
    LOW = "low"  # 4,000 tokens - simple reasoning
    MEDIUM = "medium"  # 10,000 tokens - moderate complexity
    HIGH = "high"  # 16,000 tokens - complex analysis
    ULTRATHINK = "ultrathink"  # 32,000 tokens - maximum reasoning
    ADAPTIVE = "adaptive"  # Let the CLI decide (no max_thinking_tokens set)


# Thinking budget token values (-1 = sentinel for adaptive/unset)
THINKING_BUDGET_TOKENS: dict[ThinkingBudget, int] = {
    ThinkingBudget.NONE: 0,
    ThinkingBudget.LOW: 4000,
    ThinkingBudget.MEDIUM: 10000,
    ThinkingBudget.HIGH: 16000,
    ThinkingBudget.ULTRATHINK: 32000,
    ThinkingBudget.ADAPTIVE: -1,
}


# Base system prompt appended to ALL Gluon-launched tasks
GLUON_SYSTEM_PROMPT = """
## Gluon Runtime Environment

You are being run by **Gluon**, an orchestrator that manages concurrent coding agents.
Key context:

**Environment:**
- Runtime: Docker container (Ubuntu-based)
- Home directory: `/home/gluon` (use `~` or `$HOME` when possible)
- Available runtimes: Node.js, Python, Bun, Git, GitHub CLI (gh)
- MCP servers may be configured for additional tool access

**Important:**
- CRITICAL: NEVER hallucinate paths like `/home/testuser`, `/home/ralph`,
  `/Users/steve/Code/my-project` - these do not exist and tool calls will fail.
- The working directory in system context is AUTHORITATIVE - use it exactly as provided.
- Prefer relative paths when possible. When referencing home directory, prefer `~` or `$HOME` over hardcoded paths
- If you must use an absolute path, the home directory is `/home/gluon`

**Dev Server Port:**
- If you need to start a dev server, use: `PORT=$GLUON_DEV_PORT bun run dev`
"""


AGENT_BROWSER_SYSTEM_PROMPT = """
## Browser Automation (agent-browser)

`agent-browser` is pre-installed with Chromium for headless browser automation.
DO NOT use `npx playwright install` or any other browser installer — Chromium is already available.
DO NOT use `mcp__scraper` for localhost pages — it cannot reach the container's localhost.

**Commands:**
- `agent-browser open <url>` — Open a page
- `agent-browser screenshot <path.png>` — Capture full-page screenshot
- `agent-browser click <selector>` — Click element
- `agent-browser type <selector> <text>` — Type into element
- `agent-browser evaluate <js>` — Run JavaScript in page
- `agent-browser close` — Close browser session

**Dev server pattern:**
```bash
PORT=$GLUON_DEV_PORT bun run dev &
sleep 5
agent-browser open http://localhost:$GLUON_DEV_PORT
agent-browser screenshot homepage.png
```

Screenshots are automatically captured and attached to this run.
"""


# System prompt injected when force_planning is enabled (appended after GLUON_SYSTEM_PROMPT)
PLANNING_SYSTEM_PROMPT = """
## PLANNING MODE ACTIVE

Before executing ANY code changes or tool calls that modify files, you MUST:

1. **Analyze the Request**: Understand what the user is asking for
2. **Create a Plan**: Write a detailed plan using TodoWrite tool with specific tasks
3. **Present the Plan**: Output the plan for review BEFORE executing
4. **Wait for Confirmation**: Only proceed after presenting the plan

Structure your plan as:
- [ ] Task 1: Description
- [ ] Task 2: Description
- ...

DO NOT make any file modifications until you have presented your complete plan.
"""


# System prompt for autonomous planning mode (Ralph Loop with force_planning)
# This variant does NOT wait for human confirmation - proceeds directly to execution
PLANNING_AUTONOMOUS_PROMPT = """
## PLANNING MODE (Autonomous)

**Phase 1 - Planning:**
1. Analyze the request
2. Create a detailed plan using TodoWrite with checkbox format:
   - [ ] Task 1: Description
   - [ ] Task 2: Description
3. Output the plan

**Phase 2 - Execution (AUTOMATIC):**
Once your plan is written to a TODO file, IMMEDIATELY proceed to execute.
Do NOT wait for human confirmation - this is autonomous mode.
Work through tasks sequentially: execute -> verify -> check off -> next task.

**Critical**: After creating the plan, your VERY NEXT action must be executing Task 1.
"""


# System prompt for Ralph Loop runs - status reporting instructions
RALPH_SYSTEM_PROMPT = """
## RALPH Loop Status Reporting

At the end of EVERY response, you MUST include a RALPH_STATUS block:

```
---RALPH_STATUS---
STATUS: IN_PROGRESS | COMPLETE | BLOCKED
TASKS_COMPLETED_THIS_LOOP: <number of tasks you completed in this iteration>
FILES_MODIFIED: <number of files you modified>
TESTS_STATUS: PASSING | FAILING | NOT_RUN
WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING
EXIT_SIGNAL: false | true
RECOMMENDATION: <one line summary of what to do next>
---END_RALPH_STATUS---
```

**EXIT_SIGNAL is CRITICAL - it controls whether the Ralph Loop CONTINUES or STOPS:**
- `false` = There is MORE work remaining in the original prompt/PRD → loop continues
- `true` = The ENTIRE original task is 100% complete with NOTHING left → loop stops

**IMPORTANT RULES:**
1. If your RECOMMENDATION mentions "proceed", "continue", "next", or any future action → EXIT_SIGNAL MUST be `false`
2. STATUS=COMPLETE means THIS ITERATION is done, NOT the entire project
3. The most common pattern is `STATUS: COMPLETE` + `EXIT_SIGNAL: false` (iteration done, project continues)
4. Only set `EXIT_SIGNAL: true` when there is genuinely NO remaining work whatsoever
"""


# Default budget for all profiles (configurable via env var)
DEFAULT_BUDGET_USD = float(os.environ.get("DEFAULT_RALPH_COST_LIMIT", "1000.0"))

# Profile configurations - each profile bundles model + options
TASK_PROFILES: dict[TaskProfile, dict[str, Any]] = {
    TaskProfile.QUICK: {
        "model": "haiku",
        "max_thinking_tokens": 0,
        "max_turns": 10,
        "max_budget_usd": DEFAULT_BUDGET_USD,
        "force_planning": False,
        "effort": "low",
        "description": "Fast responses for simple tasks",
    },
    TaskProfile.STANDARD: {
        "model": "sonnet",
        "max_thinking_tokens": 10000,
        "max_turns": 30,
        "max_budget_usd": DEFAULT_BUDGET_USD,
        "force_planning": False,
        "effort": "medium",
        "description": "Balanced performance (default)",
    },
    TaskProfile.DEEP: {
        "model": "opus-4.6",
        "max_thinking_tokens": 32000,
        "max_turns": 50,
        "max_budget_usd": DEFAULT_BUDGET_USD,
        "force_planning": False,
        "effort": "high",
        "description": "Maximum reasoning for complex tasks",
    },
    TaskProfile.PLANNING: {
        "model": "opus-4.6",
        "max_thinking_tokens": 16000,
        "max_turns": 40,
        "max_budget_usd": DEFAULT_BUDGET_USD,
        "force_planning": True,
        "effort": "high",
        "description": "Plan before executing",
    },
}


def resolve_task_options(
    profile: TaskProfile | str | None = None,
    model: str | None = None,
    max_thinking_tokens: int | None = None,
    thinking_budget: ThinkingBudget | str | None = None,
    max_turns: int | None = None,
    max_budget_usd: float | None = None,
    force_planning: bool | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """
    Resolve task options from profile and overrides.

    Args:
        profile: Base profile to use (defaults to STANDARD)
        model: Override profile's model
        max_thinking_tokens: Override thinking tokens directly
        thinking_budget: Override via ThinkingBudget preset
        max_turns: Override profile's max turns
        max_budget_usd: Override profile's cost budget
        force_planning: Override profile's planning mode
        effort: Override reasoning effort (low/medium/high/max)

    Returns:
        Dict with resolved options:
        - model: str
        - max_thinking_tokens: int
        - max_turns: int | None
        - max_budget_usd: float | None
        - force_planning: bool
        - effort: str | None
    """
    # Resolve profile enum
    if profile is None:
        resolved_profile = TaskProfile.STANDARD
    elif isinstance(profile, str):
        try:
            resolved_profile = TaskProfile(profile.lower())
        except ValueError:
            resolved_profile = TaskProfile.STANDARD
    else:
        resolved_profile = profile

    # Get base config from profile
    config = TASK_PROFILES[resolved_profile].copy()

    # Apply overrides
    if model is not None:
        config["model"] = model

    # Thinking tokens: direct override takes precedence, then budget preset
    if max_thinking_tokens is not None:
        config["max_thinking_tokens"] = max_thinking_tokens
    elif thinking_budget is not None:
        if isinstance(thinking_budget, str):
            try:
                budget = ThinkingBudget(thinking_budget.lower())
            except ValueError:
                budget = ThinkingBudget.MEDIUM
        else:
            budget = thinking_budget
        config["max_thinking_tokens"] = THINKING_BUDGET_TOKENS[budget]

    if max_turns is not None:
        config["max_turns"] = max_turns

    if max_budget_usd is not None:
        config["max_budget_usd"] = max_budget_usd

    if force_planning is not None:
        config["force_planning"] = force_planning

    # Effort: explicit override wins, otherwise use profile default
    if effort is not None:
        config["effort"] = effort

    return {
        "model": config["model"],
        "max_thinking_tokens": config["max_thinking_tokens"],
        "max_turns": config.get("max_turns"),
        "max_budget_usd": config.get("max_budget_usd"),
        "force_planning": config["force_planning"],
        "effort": config.get("effort"),
    }


class SupervisionConfig(BaseModel):
    """Configuration for task supervision and auto-resume.

    Controls how the supervisor handles tasks that reach REVIEW status.
    """

    enabled: bool = True  # Whether supervision is enabled for this task
    policy: SupervisionPolicy = SupervisionPolicy.CONSERVATIVE  # Decision policy
    max_auto_resumes: int = 5  # Maximum auto-resume attempts
    min_time_between_resumes: int = 60  # Minimum seconds between resumes
    auto_resume_triggers: list[str] = Field(default_factory=lambda: ["incomplete_work", "test_only", "low_confidence"])


class QueuedMessage(BaseModel):
    """A queued follow-up message for a running task."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    message: str
    queued_at: datetime = Field(default_factory=utc_now)


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
    prompt: str  # The task prompt (may be updated on resume)
    original_prompt: str | None = None  # Original task prompt (preserved across resumes)
    model: str | None = None  # Requested model (e.g., "claude-haiku-4.5", "haiku")
    initiator: str | None = None  # Who started the run (e.g., "cli", "telegram:12345")
    thread_id: str | None = None  # Discord/Slack thread ID for resume detection
    metadata: dict[str, Any] | None = None  # Task profile options and other metadata
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

    # Context overflow recovery tracking
    recovery_count: int = 0  # Number of times recovered from context overflow
    last_recovery_at: datetime | None = None  # When last recovery happened
    recovery_from_run_id: str | None = None  # Parent run ID if this is a recovery run
    is_recovering: bool = False  # Currently in recovery process
    recovery_item_count: int = 0  # Progress counter during recovery

    # PR monitoring tracking
    last_comment_id: int | None = None  # Last processed PR comment ID
    last_check_sha: str | None = None  # Last checked commit SHA for CI
    auto_resume_enabled: bool = True  # Allow auto-resume for this run
    auto_resume_count: int = 0  # Number of auto-resumes (max 5)

    # Ralph mode fields (autonomous loop execution)
    ralph_enabled: bool = False  # Whether ralph loop mode is enabled
    loop_count: int = 0  # Current iteration count
    max_loops: int = 50  # Safety cap on iterations

    # Circuit breaker state
    circuit_state: CircuitState = CircuitState.CLOSED
    consecutive_no_progress: int = 0  # Loops without file changes
    consecutive_same_error: int = 0  # Loops with same error
    last_progress_loop: int = 0  # Last loop with progress
    last_error_hash: str | None = None  # Hash of last error for repetition detection
    half_open_iterations: int = 0  # Iterations spent in HALF_OPEN state

    # Completion detection
    completion_signals: int = 0  # Consecutive completion signals
    test_only_loops: int = 0  # Consecutive test-only loops
    completion_confidence: float = 0.0  # Confidence score 0-100
    completion_reason: str | None = None  # Why loop exited

    # Rate limiting
    calls_this_hour: int = 0  # API calls in current hour
    hour_start: datetime | None = None  # When current hour started
    max_calls_per_hour: int = 100  # Hourly API call limit
    max_cost_usd: float | None = None  # Optional cost cap

    # Supervision tracking (auto-resume until complete)
    supervision_config: SupervisionConfig | None = None  # Per-task supervision settings
    supervision_auto_resume_count: int = 0  # Number of supervisor-initiated resumes
    last_supervision_check_at: datetime | None = None  # When supervisor last evaluated
    last_supervision_resume_at: datetime | None = None  # When supervisor last resumed
    supervision_disabled_reason: str | None = None  # Why supervision was disabled

    # Queued follow-up messages (for sending messages while task is running)
    queued_messages: list[QueuedMessage] = Field(default_factory=list)

    # Commit/file snapshot tracking (persist changes after branch merge)
    changes_snapshotted: bool = False  # Whether commits/files have been snapshotted
    snapshot_at: datetime | None = None  # When snapshot was captured

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

    def mark_review(self) -> None:
        """Mark run as in review (awaiting action/approval)."""
        self.status = RunStatus.REVIEW

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
        """Check if run can be resumed (any non-active status with session)."""
        resumable_statuses = (RunStatus.REVIEW, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)
        return self.status in resumable_statuses and self.claude_session_id is not None

    @property
    def is_active(self) -> bool:
        """Check if run is still active (pending, running, or in review)."""
        return self.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.REVIEW)

    @property
    def duration_seconds(self) -> float | None:
        """Get duration in seconds if started."""
        if not self.started_at:
            return None
        end = self.completed_at or utc_now()
        return (end - self.started_at).total_seconds()


# ========== Ralph Loop Models ==========


class RalphLoopIteration(BaseModel):
    """Tracks individual loop iterations within a ralph-enabled run."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str  # FK to ExecutionRun
    loop_number: int  # 1-indexed iteration number
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None

    # Execution results
    files_changed: int = 0  # Number of git file changes
    has_errors: bool = False  # Whether errors occurred
    error_summary: str | None = None  # First ~200 chars of error
    output_length: int = 0  # Length of Claude output

    # Analysis results
    is_test_only: bool = False  # Only ran tests, no implementation
    has_completion_signal: bool = False  # Claude indicated "done"
    progress_detected: bool = False  # Files changed or meaningful work
    confidence_score: float = 0.0  # Completion confidence 0-100

    # Claude SDK info
    claude_session_id: str | None = None  # Session ID for this iteration
    cost_usd: float = 0.0  # Cost for this iteration
    tokens_used: int = 0  # Total tokens (input + output)

    @property
    def duration_seconds(self) -> float | None:
        """Get duration in seconds if completed."""
        if not self.ended_at:
            return None
        return (self.ended_at - self.started_at).total_seconds()


class QuestionStatus(str, Enum):
    """Status of a pending question."""

    PENDING = "pending"  # Waiting for user response
    ANSWERED = "answered"  # User provided answer
    AUTO_ANSWERED = "auto_answered"  # System auto-answered (timeout/Ralph)
    EXPIRED = "expired"  # Question timed out without answer


class PendingQuestion(BaseModel):
    """A question from Claude awaiting user response.

    When Claude uses the AskUserQuestion tool, we intercept it and store
    the question here for the user to answer via web UI or API.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str  # FK to ExecutionRun
    question_index: int = 0  # Index within the questions array (0-based)
    question_text: str  # The question being asked
    header: str  # Short label (e.g., "Database", "UI Style")
    options: list[dict[str, str]]  # [{label: str, description: str}, ...]
    multi_select: bool = False  # Whether multiple options can be selected
    status: QuestionStatus = QuestionStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    answered_at: datetime | None = None
    expires_at: datetime | None = None  # When auto-answer kicks in

    # Answer tracking
    selected_labels: list[str] = Field(default_factory=list)  # Selected option label(s)
    answer_source: str | None = None  # "user", "auto_recommended", "auto_first", "ralph"

    @property
    def is_pending(self) -> bool:
        """Check if question is still awaiting answer."""
        return self.status == QuestionStatus.PENDING

    @property
    def answer_string(self) -> str:
        """Get answer as comma-separated string for SDK."""
        return ", ".join(self.selected_labels)

    def get_recommended_option(self) -> str | None:
        """Find option marked as (Recommended), or None."""
        for opt in self.options:
            label = opt.get("label", "")
            if "(Recommended)" in label or "(recommended)" in label:
                return label
        return None

    def auto_answer(self, source: str = "auto_recommended") -> None:
        """Auto-answer with recommended option, or first if none recommended."""
        recommended = self.get_recommended_option()
        if recommended:
            self.selected_labels = [recommended]
        elif self.options:
            self.selected_labels = [self.options[0].get("label", "")]
        self.status = QuestionStatus.AUTO_ANSWERED
        self.answer_source = source
        self.answered_at = utc_now()

    def answer(self, labels: list[str], source: str = "user") -> None:
        """Record user's answer."""
        self.selected_labels = labels
        self.status = QuestionStatus.ANSWERED
        self.answer_source = source
        self.answered_at = utc_now()


class SupervisionDecision(BaseModel):
    """Audit trail for supervision decisions.

    Records every decision made by the supervisor for a run,
    including whether to resume, skip, or hold.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str  # FK to ExecutionRun
    timestamp: datetime = Field(default_factory=utc_now)

    # Decision details
    decision: str  # "resume", "skip", "hold", "disable"
    reason: str  # Human-readable explanation
    trigger: str | None = None  # What triggered this check (e.g., "scheduler", "manual", "pr_comment")

    # Context at time of decision
    circuit_state: CircuitState | None = None
    completion_confidence: float | None = None
    calls_this_hour: int | None = None
    cost_usd: float | None = None
    auto_resume_count: int | None = None

    # Policy that was applied
    policy: SupervisionPolicy | None = None


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
            not self.has_uncommitted and self.commits_ahead == 0 and self.commits_behind == 0 and not self.has_conflicts
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


# ========== Commit/File Snapshot Models ==========


class CommitSnapshot(BaseModel):
    """Persisted commit data captured before branch merge/deletion.

    Snapshots preserve commit history after the branch is merged into main
    or deleted, when git comparisons would otherwise return no results.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str  # FK to ExecutionRun
    sha: str  # Git commit SHA
    message: str  # Commit subject line
    full_message: str | None = None  # Full commit body (optional)
    author: str
    author_email: str | None = None
    date: datetime  # Commit timestamp
    ordinal: int  # 1-indexed order in commit list
    created_at: datetime = Field(default_factory=utc_now)


class FileChangeSnapshot(BaseModel):
    """Persisted file change data for a run.

    Stores aggregate file changes across the entire branch (not per-commit).
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str  # FK to ExecutionRun
    file_path: str  # Path relative to repo root
    change_type: str  # "added", "modified", "deleted", "renamed"
    additions: int = 0  # Lines added
    deletions: int = 0  # Lines deleted
    created_at: datetime = Field(default_factory=utc_now)


class CommitFileSnapshot(BaseModel):
    """Files changed in a specific commit (for detailed commit view).

    Links to CommitSnapshot to provide per-commit file breakdown.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    commit_snapshot_id: str  # FK to CommitSnapshot
    file_path: str
    change_type: str  # "added", "modified", "deleted", "renamed"
    additions: int = 0
    deletions: int = 0


class ChannelMapping(BaseModel):
    """Maps a chat channel to a project for multi-transport support."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    transport: str  # "discord", "slack", etc.
    channel_id: str  # Channel ID (platform-specific)
    project_id: str  # Project ID (FK)
    project_name: str  # Cached project name for convenience
    created_at: datetime = Field(default_factory=utc_now)


# ========== Chat Persistence Models ==========


# Default TTL values
MESSAGE_RUN_MAP_TTL_DAYS = 7  # Messages older than a week unlikely to be resumed
CHAT_HISTORY_TTL_HOURS = 48  # Conversational context is short-lived


class MessageRunMapping(BaseModel):
    """Maps a bot message to an execution run for reply-based resume.

    When a user replies to a completion message, we use this mapping
    to find the associated run and resume it with the new prompt.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    transport: str  # "discord", "telegram", etc.
    message_id: str  # Platform-specific message ID
    run_id: str  # FK to ExecutionRun
    chat_id: str  # Channel/chat ID for scoping
    user_id: str  # Who initiated the original run
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime  # TTL-based expiration


class ChatHistoryEntry(BaseModel):
    """Persisted chat history entry for natural language conversations.

    Stores user/assistant messages for maintaining conversation context
    across bot restarts.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str  # Universal user ID (e.g., 'telegram:123', 'discord:456')
    transport: str  # "telegram", "discord", etc.
    role: str  # "user" or "assistant"
    text: str  # Message content
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime  # TTL-based expiration


# ========== Image Attachment Models (Phase 10.1) ==========


class ImageAttachment(BaseModel):
    """Metadata for an uploaded image attachment."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str  # Relative path within storage (e.g., "ab/abcd1234.png")
    original_name: str  # User's original filename
    mime_type: str | None = None  # e.g., "image/png"
    size_bytes: int
    hash: str  # SHA256 hash for deduplication
    source: str = "user"  # "user" (uploaded) or "screenshot" (agent-browser)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def full_path(self) -> Path:
        """Get full path to image file in storage."""
        return Path.home() / ".gluon" / "images" / self.file_path

    def to_markdown(self) -> str:
        """Return markdown image reference."""
        return f"![{self.original_name}]({self.file_path})"


# ========== Distributed Worker Models ==========


class JobStatus(str, Enum):
    """Status of a job in the queue."""

    QUEUED = "queued"  # Waiting in queue
    ASSIGNED = "assigned"  # Assigned to worker, pending execution
    RUNNING = "running"  # Currently executing
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Error occurred


class WorkerType(str, Enum):
    """Type of worker for task execution."""

    LOCAL = "local"  # Local subprocess execution
    REMOTE = "remote"  # Remote worker via HTTP API


class WorkerStatus(str, Enum):
    """Health status of a worker."""

    HEALTHY = "healthy"  # Worker responding normally
    UNHEALTHY = "unhealthy"  # Worker missed heartbeats
    OFFLINE = "offline"  # Worker explicitly offline


class Worker(BaseModel):
    """A worker that can execute jobs."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str  # Human-readable name (unique)
    type: WorkerType = WorkerType.LOCAL
    base_url: str | None = None  # For remote workers (e.g., "http://worker1:8080")
    api_key: str  # API key for authentication
    max_concurrent: int = 4  # Maximum concurrent jobs
    status: WorkerStatus = WorkerStatus.HEALTHY
    last_heartbeat: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    # Runtime tracking (not persisted)
    active_jobs: int = 0  # Current number of running jobs

    @property
    def is_available(self) -> bool:
        """Check if worker can accept more jobs."""
        return self.status == WorkerStatus.HEALTHY and self.active_jobs < self.max_concurrent

    @property
    def available_slots(self) -> int:
        """Number of available job slots."""
        if self.status != WorkerStatus.HEALTHY:
            return 0
        return max(0, self.max_concurrent - self.active_jobs)

    def mark_healthy(self) -> None:
        """Mark worker as healthy with updated heartbeat."""
        self.status = WorkerStatus.HEALTHY
        self.last_heartbeat = utc_now()
        self.updated_at = utc_now()

    def mark_unhealthy(self) -> None:
        """Mark worker as unhealthy (missed heartbeats)."""
        self.status = WorkerStatus.UNHEALTHY
        self.updated_at = utc_now()

    def mark_offline(self) -> None:
        """Mark worker as explicitly offline."""
        self.status = WorkerStatus.OFFLINE
        self.updated_at = utc_now()


class Job(BaseModel):
    """A job in the execution queue."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str  # FK to ExecutionRun
    project_id: str  # FK to Project (denormalized for quick filtering)
    prompt: str  # Task prompt
    priority: int = 5  # 1 (highest) to 10 (lowest), default 5
    status: JobStatus = JobStatus.QUEUED
    worker_id: str | None = None  # FK to Worker (assigned worker)
    created_at: datetime = Field(default_factory=utc_now)
    assigned_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None

    # Job configuration
    model: str | None = None  # Requested model tier
    use_worktree: bool = False  # Whether to use git worktree
    session_id: str | None = None  # Session ID to resume (optional)

    # Lease tracking for fault tolerance
    lease_expires_at: datetime | None = None  # Worker lease expiration

    def assign_to_worker(self, worker_id: str, lease_seconds: int = 300) -> None:
        """Assign job to a worker with a lease."""
        self.worker_id = worker_id
        self.status = JobStatus.ASSIGNED
        self.assigned_at = utc_now()
        self.lease_expires_at = datetime.fromtimestamp(utc_now().timestamp() + lease_seconds, tz=UTC)

    def mark_running(self) -> None:
        """Mark job as running."""
        self.status = JobStatus.RUNNING
        self.started_at = utc_now()

    def mark_completed(self) -> None:
        """Mark job as completed."""
        self.status = JobStatus.COMPLETED
        self.completed_at = utc_now()
        self.lease_expires_at = None

    def mark_failed(self, error: str) -> None:
        """Mark job as failed."""
        self.status = JobStatus.FAILED
        self.error_message = error
        self.completed_at = utc_now()
        self.lease_expires_at = None

    def release_lease(self) -> None:
        """Release job back to queue (e.g., worker died)."""
        self.worker_id = None
        self.status = JobStatus.QUEUED
        self.assigned_at = None
        self.lease_expires_at = None

    @property
    def is_lease_expired(self) -> bool:
        """Check if worker lease has expired."""
        if not self.lease_expires_at:
            return False
        return utc_now() > self.lease_expires_at


# ========== Webhook Models ==========


class WebhookConfig(BaseModel):
    """Configuration for a webhook integration."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    handler: str  # "github", "gitlab", "bitbucket"
    project_id: str | None = None  # FK to Project (None = match by repo name)
    secret_key: str  # Webhook secret for signature verification
    events: list[str] = Field(default_factory=list)  # ["push", "pull_request", "issues"]
    prompt_template: str | None = None  # Custom prompt template (uses default if None)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    # Filtering options
    branches: list[str] | None = None  # Only trigger for these branches (None = all)
    ignore_branches: list[str] | None = None  # Ignore these branches
    labels: list[str] | None = None  # Only issues/PRs with these labels

    def matches_branch(self, branch: str) -> bool:
        """Check if webhook should trigger for this branch."""
        if self.ignore_branches and branch in self.ignore_branches:
            return False
        if self.branches is None:
            return True
        return branch in self.branches

    def matches_event(self, event_type: str) -> bool:
        """Check if webhook handles this event type."""
        if not self.events:
            return True  # Empty = all events
        return event_type in self.events
