"""Pydantic models for Web API request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class QueuedMessageResponse(BaseModel):
    """Response model for a queued message."""

    id: str
    message: str
    queued_at: str


class RunResponse(BaseModel):
    """Response model for execution runs."""

    id: str
    project_id: str
    project_name: str = Field(description="Denormalized project name for display")
    status: str
    prompt: str
    original_prompt: str | None = Field(default=None, description="Original task prompt (preserved across resumes)")
    initiator: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    # Cost tracking (available in list responses for RunCard display)
    cost_usd: float | None = Field(default=None, description="Total cost in USD for this run")
    # Git indicators (available in list responses for RunCard display)
    use_worktree: bool = Field(default=False, description="Whether worktree was used")
    branch_name: str | None = Field(default=None, description="Git branch name")
    # PR info (for Review column routing)
    pr_number: int | None = Field(default=None, description="GitHub PR number")
    pr_url: str | None = Field(default=None, description="GitHub PR URL")
    pr_status: str | None = Field(default=None, description="PR status: open, merged, closed, draft")
    pr_mergeable: str | None = Field(default=None, description="PR mergeable status: MERGEABLE, CONFLICTING, UNKNOWN")
    # Archive tracking
    archived: bool = Field(default=False, description="Whether run is archived")
    # Recovery progress UI
    is_recovering: bool = Field(default=False, description="Whether recovery is in progress")
    recovery_item_count: int = Field(default=0, description="Number of items processed during recovery")
    # Ralph Loop fields (autonomous execution mode)
    ralph_enabled: bool = Field(default=False, description="Whether ralph loop is enabled")
    loop_count: int = Field(default=0, description="Current loop iteration")
    max_loops: int = Field(default=50, description="Maximum loop iterations")
    circuit_state: str = Field(default="CLOSED", description="Circuit breaker state: CLOSED, HALF_OPEN, OPEN")
    completion_confidence: float = Field(default=0.0, description="Task completion confidence (0-100)")
    completion_reason: str | None = Field(default=None, description="Reason for loop completion/exit")
    calls_this_hour: int = Field(default=0, description="API calls made in current hour window")
    max_calls_per_hour: int = Field(default=100, description="Maximum API calls per hour")
    # Witness health (latest classification for running runs)
    health_classification: str | None = Field(default=None, description="Latest witness health classification")
    # Chain/formula step progress
    chain_id: str | None = Field(default=None, description="Chain ID if part of a formula")
    chain_step_name: str | None = Field(default=None, description="Current/last step name")
    chain_step_index: int | None = Field(default=None, description="Current step index (0-based)")
    chain_total_steps: int | None = Field(default=None, description="Total steps in chain")
    # SDK stop reason (surfaced to run lists/cards)
    stop_reason: str | None = Field(default=None, description="SDK stop reason (end_turn, max_turns, etc.)")

    class Config:
        from_attributes = True


class RunDetailResponse(RunResponse):
    """Detailed response model for a single run."""

    session_id: str | None = Field(default=None, description="Claude SDK session ID for resume")
    exit_code: int | None = None
    log_path: str | None = None
    # Cost tracking fields
    cost_usd: float | None = Field(default=None, description="Total cost in USD for this run")
    input_tokens: int | None = Field(default=None, description="Input tokens used")
    output_tokens: int | None = Field(default=None, description="Output tokens generated")
    model_used: str | None = Field(default=None, description="Model tier used (e.g., sonnet, opus)")
    # Git/worktree fields (Phase 7.1)
    branch_name: str | None = Field(default=None, description="Git branch name")
    source_branch: str | None = Field(default=None, description="Source branch (e.g., main)")
    use_worktree: bool = Field(default=False, description="Whether worktree was used")
    git_commit_sha: str | None = Field(default=None, description="Final commit SHA")
    pr_number: int | None = Field(default=None, description="GitHub PR number")
    pr_url: str | None = Field(default=None, description="GitHub PR URL")
    pr_status: str | None = Field(default=None, description="PR status: open, merged, closed, draft")
    pr_mergeable: str | None = Field(default=None, description="PR mergeable status: MERGEABLE, CONFLICTING, UNKNOWN")
    has_remote: bool = Field(default=True, description="Whether the project has a git remote configured")
    # Resume tracking fields
    resume_count: int = Field(default=0, description="Number of times this run has been resumed")
    last_resumed_at: str | None = Field(default=None, description="ISO timestamp of last resume")
    # Precomputed counts for tab badges (avoids lazy loading)
    commit_count: int | None = Field(default=None, description="Number of commits on branch")
    file_count: int | None = Field(default=None, description="Number of files changed on branch")
    # Ralph Loop detail fields (additional to RunResponse fields)
    consecutive_no_progress: int = Field(default=0, description="Consecutive loops without progress")
    consecutive_same_error: int = Field(default=0, description="Consecutive loops with same error")
    test_only_loops: int = Field(default=0, description="Number of test-only loop iterations")
    max_cost_usd: float | None = Field(default=None, description="Maximum cost limit for ralph loop")
    # SDK stop reason
    stop_reason: str | None = Field(default=None, description="SDK stop reason (end_turn, max_turns, etc.)")
    # Queued messages (for follow-up while task is running)
    queued_messages: list[QueuedMessageResponse] = Field(default_factory=list, description="Queued follow-up messages")


class CreateRunRequest(BaseModel):
    """Request model for creating a new run."""

    project_name: str = Field(description="Name of the project to run on")
    prompt: str = Field(description="Task prompt for Claude")
    model: str = Field(default="sonnet", description="Model tier: opus/sonnet/haiku")
    use_worktree: bool = Field(default=False, description="Execute in isolated Git worktree")
    # Task profile options
    profile: str = Field(default="standard", description="Task profile: quick/standard/deep/planning")
    thinking_override: str | None = Field(
        default=None,
        description="Override thinking budget: none/low/medium/high/ultrathink/adaptive",
    )
    effort_override: str | None = Field(
        default=None,
        description="Override reasoning effort: low/medium/high/max",
    )
    model_override: str | None = Field(
        default=None,
        description="Override profile's model: haiku/sonnet/opus",
    )
    max_budget_override: float | None = Field(
        default=None,
        description="Override profile's max budget in USD",
    )
    force_planning: bool | None = Field(
        default=None,
        description="Override planning mode (True = plan before executing)",
    )
    task_budget_override: int | None = Field(
        default=None,
        description="Override task token budget (model paces itself to finish within budget)",
    )
    # Ralph Loop options (autonomous execution mode)
    ralph_enabled: bool = Field(default=False, description="Enable ralph loop for autonomous execution")
    max_loops: int = Field(default=50, description="Maximum loop iterations (1-100)")
    max_cost_usd: float | None = Field(default=None, description="Optional cost limit in USD")
    # Per-task overrides
    agent_teams: bool | None = Field(default=None, description="Override global agent teams setting")
    dev_port: int | None = Field(default=None, description="Dev server port (auto-assigned if not set)")
    model_transition: str | None = Field(
        default=None,
        description="Model transition strategy: opus-to-sonnet, opus-to-haiku",
    )
    # Blueprint orchestration options (on by default)
    enable_prehydration: bool = Field(default=True, description="Pre-hydrate project context into prompt")
    blueprint_enabled: bool = Field(default=True, description="Run lint+test validation after completion")
    # Agent linkage (Theme B Phase 1+4)
    agent: str | None = Field(
        default=None,
        description=(
            "Agent name or ID prefix to link this run to. If unset and the "
            "project's workspace has exactly one active agent, that agent is "
            "auto-selected."
        ),
    )


class LogResponse(BaseModel):
    """Response model for log content."""

    run_id: str
    stream: str = Field(description="Log stream: stdout, stderr, or messages")
    content: str
    line_count: int


class ProjectResponse(BaseModel):
    """Response model for projects."""

    id: str
    name: str
    path: str
    session_count: int = 0
    workspace_id: str | None = None
    # Basic git status fields
    git_branch: str | None = Field(default=None, description="Current git branch")
    git_ahead: int | None = Field(default=None, description="Commits ahead of upstream")
    git_behind: int | None = Field(default=None, description="Commits behind upstream")
    # Extended git status fields for sync button
    git_uncommitted_count: int | None = Field(default=None, description="Number of uncommitted changes")
    git_has_remote: bool = Field(default=False, description="Whether a remote is configured")
    git_has_conflicts: bool = Field(default=False, description="Whether there are unresolved conflicts")
    git_has_operation_in_progress: bool = Field(default=False, description="Whether rebase/merge is in progress")
    # Computed sync state
    can_sync: bool = Field(default=False, description="Whether sync is available")
    sync_action: str | None = Field(default=None, description="Recommended action: pull, push, commit+push, diverged")

    class Config:
        from_attributes = True


class StatusResponse(BaseModel):
    """Response model for overall system status."""

    total_projects: int
    active_runs: int
    total_runs: int


class VersionResponse(BaseModel):
    """Response model for application version info."""

    version: str  # Git commit SHA (short)
    full_version: str  # Git commit SHA (full)
    build_time: str  # ISO timestamp of build
    environment: str  # "development" or "production"


# WebSocket message types


class WebSocketMessage(BaseModel):
    """Base WebSocket message."""

    type: str


class RunUpdatedMessage(WebSocketMessage):
    """WebSocket message for run status update."""

    type: str = "run_updated"
    run: RunResponse


class RunCreatedMessage(WebSocketMessage):
    """WebSocket message for new run created."""

    type: str = "run_created"
    run: RunResponse


class LogLineMessage(WebSocketMessage):
    """WebSocket message for log line."""

    type: str = "log_line"
    run_id: str
    stream: str
    line: str


class SubscribeLogsRequest(BaseModel):
    """WebSocket request to subscribe to run logs."""

    type: str = "subscribe_logs"
    run_id: str


class UnsubscribeLogsRequest(BaseModel):
    """WebSocket request to unsubscribe from run logs."""

    type: str = "unsubscribe_logs"
    run_id: str


# Resume API models


class ResumeRunRequest(BaseModel):
    """Request model for resuming a run."""

    prompt: str = Field(description="Follow-up prompt to continue the session")


class ResumeRunResponse(BaseModel):
    """Response model for resume operation (in-place resume)."""

    run_id: str = Field(description="ID of the run being resumed (same run continues)")
    status: str = Field(description="Current status of the run (should be 'running')")
    resume_count: int = Field(description="Number of times this run has been resumed")
    # Backward compatibility fields (deprecated, same as run_id)
    original_run_id: str | None = Field(default=None, description="Deprecated: Same as run_id")
    new_run_id: str | None = Field(default=None, description="Deprecated: Same as run_id")


class RecoverRunRequest(BaseModel):
    """Request model for recovering a run from context overflow."""

    fresh: bool = Field(
        default=False,
        description="If true, create a new run. If false, recover in-place.",
    )


class RecoverRunResponse(BaseModel):
    """Response model for recover operation."""

    run_id: str = Field(description="ID of the recovery run")
    status: str = Field(description="Current status (should be 'running')")
    recovery_count: int = Field(description="Number of recovery attempts")
    is_fresh: bool = Field(description="Whether this is a fresh run or in-place recovery")
    completed_work: list[str] = Field(
        default_factory=list,
        description="List of completed tasks from the original run",
    )


class SessionHistoryResponse(BaseModel):
    """Response model for session history (all runs in a session)."""

    session_id: str = Field(description="Claude session ID")
    runs: list[RunResponse] = Field(description="All runs in this session, chronologically ordered")


# Phase 7.2: Drag-and-Drop Status Transition Models


class UpdateStatusRequest(BaseModel):
    """Request model for updating run status (drag-and-drop)."""

    status: str = Field(description="Target RunStatus value")
    reason: str | None = Field(default=None, description="Optional note for audit")


class UpdateStatusResponse(BaseModel):
    """Response model for status update."""

    run: RunResponse
    previous_status: str
    new_status: str


# Phase 7.3: Project Management Models


class CreateProjectRequest(BaseModel):
    """Request model for creating a project."""

    name: str = Field(description="Project name (must be unique)")
    path: str = Field(description="Absolute path to project directory")
    workspace_id: str | None = Field(default=None, description="Optional workspace ID to associate with")


class ProjectDetailResponse(ProjectResponse):
    """Detailed response model for a single project."""

    workspace_id: str | None = None
    workspace_name: str | None = None
    run_count: int = 0
    last_run_at: datetime | None = None


class CreateWorkspaceRequest(BaseModel):
    """Request model for creating a workspace."""

    name: str = Field(description="Workspace name (must be unique)")
    path: str = Field(description="Absolute path to workspace directory")
    auto_scan: bool = Field(default=True, description="Auto-scan for projects on creation")


class WorkspaceResponse(BaseModel):
    """Response model for workspaces."""

    id: str
    name: str
    path: str
    project_count: int = 0
    auto_discover: bool = True

    class Config:
        from_attributes = True


class WorkspaceSettingsResponse(BaseModel):
    """Response model for workspace settings."""

    workspace_id: str
    settings: dict[str, str] = Field(description="Current workspace setting overrides")
    env_var_keys: list[str] = Field(description="Environment variable keys (values masked)")
    global_defaults: dict[str, str] = Field(description="Current global settings for comparison")


class ScanResultResponse(BaseModel):
    """Response model for workspace scan operation."""

    workspace_id: str
    projects_found: int
    projects_added: list[str] = Field(description="Names of newly added projects")
    projects_removed: list[str] = Field(
        default_factory=list,
        description="Names of projects removed (directory no longer exists)",
    )


class CloneRepositoryRequest(BaseModel):
    """Request model for cloning a GitHub repository into a workspace."""

    github_url: str = Field(description="GitHub repository URL (https://github.com/owner/repo)")


class CloneResultResponse(BaseModel):
    """Response model for clone operation."""

    workspace_id: str
    repo_name: str = Field(description="Name of the cloned repository directory")
    clone_path: str = Field(description="Absolute path where the repo was cloned")
    project_registered: bool = Field(description="Whether the project was auto-registered")
    project_name: str | None = Field(default=None, description="Registered project name (if auto-registered)")
    scan_result: ScanResultResponse = Field(description="Result of the workspace scan after cloning")


# Phase 8: Usage Dashboard Models


class UsageSummaryResponse(BaseModel):
    """Response model for usage summary (header display)."""

    today_cost_usd: float = Field(description="Cost in USD for today")
    today_runs: int = Field(description="Number of runs today")
    week_cost_usd: float = Field(description="Cost in USD for past 7 days")
    week_runs: int = Field(description="Number of runs past 7 days")
    month_cost_usd: float = Field(description="Cost in USD for current calendar month")
    month_runs: int = Field(description="Number of runs this calendar month")
    total_cost_usd: float = Field(description="All-time cost in USD")
    total_runs: int = Field(description="All-time run count")


class ProjectUsageResponse(BaseModel):
    """Response model for usage by project."""

    project_id: str
    project_name: str
    cost_usd: float
    run_count: int
    input_tokens: int
    output_tokens: int


class DailyUsageResponse(BaseModel):
    """Response model for daily usage."""

    date: str = Field(description="YYYY-MM-DD format")
    cost_usd: float
    run_count: int
    input_tokens: int
    output_tokens: int


class RunUsageItemResponse(BaseModel):
    """Response model for individual run in usage table."""

    id: str
    project_name: str
    prompt: str = Field(description="Truncated prompt")
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    model_used: str | None
    created_at: str
    status: str


# Commits and Files Response Models


class CommitResponse(BaseModel):
    """Response model for a single commit."""

    sha: str = Field(description="Full commit SHA")
    message: str = Field(description="Commit message (first line)")
    author: str = Field(description="Author name")
    author_email: str = Field(description="Author email")
    date: str = Field(description="Commit date in ISO format")


class RunCommitsResponse(BaseModel):
    """Response model for commits on a run's branch."""

    run_id: str
    branch_name: str | None
    base_branch: str
    commit_count: int
    commits: list[CommitResponse]
    from_snapshot: bool = Field(default=False, description="True if data from snapshot (branch may be deleted)")


class FileChangeResponse(BaseModel):
    """Response model for a single file change."""

    file_path: str = Field(description="Path to the file")
    additions: int = Field(description="Lines added")
    deletions: int = Field(description="Lines deleted")
    change_type: str = Field(description="Type: added, modified, deleted, renamed")


class RunFilesResponse(BaseModel):
    """Response model for files changed on a run's branch."""

    run_id: str
    branch_name: str | None
    base_branch: str
    file_count: int
    total_additions: int
    total_deletions: int
    files: list[FileChangeResponse]
    from_snapshot: bool = Field(default=False, description="True if data from snapshot (branch may be deleted)")


class CommitDetailResponse(BaseModel):
    """Detailed response model for a single commit with files."""

    sha: str = Field(description="Full commit SHA")
    message: str = Field(description="Full commit message (subject + body)")
    author: str = Field(description="Author name")
    author_email: str = Field(description="Author email")
    date: str = Field(description="Commit date in ISO format")
    files: list[FileChangeResponse] = Field(description="Files changed in this commit")
    from_snapshot: bool = Field(default=False, description="True if data from snapshot (branch may be deleted)")


class FileDiffResponse(BaseModel):
    """Response model for a file diff."""

    file_path: str = Field(description="Path to the file")
    diff: str = Field(description="Unified diff content")
    additions: int = Field(description="Lines added")
    deletions: int = Field(description="Lines deleted")


# ========== Image Attachment Models (Phase 10.1) ==========


class ImageResponse(BaseModel):
    """Response model for an image attachment."""

    id: str
    file_path: str = Field(description="Relative path within storage")
    original_name: str = Field(description="Original filename")
    mime_type: str | None = Field(default=None, description="MIME type")
    size_bytes: int = Field(description="File size in bytes")
    hash: str = Field(description="SHA256 content hash")
    source: str = Field(default="user", description="Origin: 'user' (uploaded) or 'screenshot' (agent-browser)")
    created_at: str = Field(description="Creation timestamp")

    class Config:
        from_attributes = True


class RunImagesResponse(BaseModel):
    """Response model for images attached to a run."""

    run_id: str
    image_count: int
    images: list[ImageResponse]


class AttachImageRequest(BaseModel):
    """Request model for attaching an existing image to a run."""

    image_id: str = Field(description="ID of the image to attach")


# ========== Advanced Git Operations Models (Phase 5) ==========


class ConflictFileResponse(BaseModel):
    """Response model for a conflicted file."""

    file_path: str = Field(description="Path to the conflicted file")
    conflict_markers_count: int = Field(description="Number of conflict markers found")


class ConflictDetectionResponse(BaseModel):
    """Response model for conflict detection."""

    has_conflicts: bool
    is_rebase_in_progress: bool
    is_merge_in_progress: bool
    conflict_operation: str | None = Field(default=None, description="Type of operation: rebase, merge, cherry_pick")
    rebase_current_step: int | None = None
    rebase_total_steps: int | None = None
    conflicted_files: list[ConflictFileResponse]


class ConflictDiffResponse(BaseModel):
    """Response model for 3-way diff of a conflicted file."""

    file_path: str
    base: str | None = Field(default=None, description="Common ancestor version")
    ours: str | None = Field(default=None, description="HEAD version (current branch)")
    theirs: str | None = Field(default=None, description="Incoming version")
    merged: str | None = Field(default=None, description="Current file content with conflict markers")


class ResolveConflictRequest(BaseModel):
    """Request model for resolving a conflict."""

    file_path: str = Field(description="Path to the conflicted file")
    resolution: str = Field(description="Resolution strategy: ours, theirs, or resolved")


class ResolveConflictResponse(BaseModel):
    """Response model for conflict resolution."""

    success: bool
    message: str


class RebaseRequest(BaseModel):
    """Request model for starting a rebase."""

    onto_branch: str = Field(description="Branch to rebase onto (e.g., main)")


class RebaseResponse(BaseModel):
    """Response model for rebase operations."""

    success: bool
    message: str
    conflicts: list[str] = Field(default_factory=list, description="List of conflicted files if any")


class ForcePushCheckResponse(BaseModel):
    """Response model for force push check."""

    needed: bool = Field(description="Whether force push is required")
    commits_to_delete: int = Field(default=0, description="Number of commits that would be deleted on remote")
    reason: str = Field(default="", description="Explanation")


class ForcePushRequest(BaseModel):
    """Request model for force push."""

    branch: str | None = Field(default=None, description="Branch to force push (default: current)")
    force_with_lease: bool = Field(default=True, description="Use --force-with-lease for safety")


class ForcePushResponse(BaseModel):
    """Response model for force push."""

    success: bool
    message: str


class BranchResponse(BaseModel):
    """Response model for a branch."""

    name: str
    is_current: bool
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0


class BranchListResponse(BaseModel):
    """Response model for branch list."""

    branches: list[BranchResponse]
    current_branch: str | None = None


class RenameBranchRequest(BaseModel):
    """Request model for renaming a branch."""

    old_name: str
    new_name: str


class ChangeBaseBranchRequest(BaseModel):
    """Request model for changing a branch's base."""

    feature_branch: str = Field(description="Branch to rebase")
    new_base: str = Field(description="New base branch")


class BranchOperationResponse(BaseModel):
    """Response model for branch operations."""

    success: bool
    message: str
    conflicts: list[str] = Field(default_factory=list)


# ========== Git Sync Models (Settings Page) ==========


class GitStatusResponse(BaseModel):
    """Response model for detailed git status of a project."""

    is_git_repo: bool = Field(description="Whether the path is a git repository")
    branch: str | None = Field(default=None, description="Current branch name")
    remote: str | None = Field(default=None, description="Remote name (e.g., origin)")
    remote_url: str | None = Field(default=None, description="Remote URL")
    has_uncommitted: bool = Field(default=False, description="Whether there are uncommitted changes")
    uncommitted_count: int = Field(default=0, description="Number of uncommitted changes")
    commits_ahead: int = Field(default=0, description="Commits ahead of upstream")
    commits_behind: int = Field(default=0, description="Commits behind upstream")
    is_diverged: bool = Field(default=False, description="Whether branch has diverged (both ahead and behind)")
    needs_pull: bool = Field(default=False, description="Whether project needs to pull (behind only)")
    needs_push: bool = Field(default=False, description="Whether project needs to push (ahead only)")
    has_conflicts: bool = Field(default=False, description="Whether there are unresolved conflicts")
    has_operation_in_progress: bool = Field(default=False, description="Whether rebase/merge is in progress")
    operation_type: str | None = Field(default=None, description="Type of operation: rebase, merge, cherry_pick")
    last_fetch_at: datetime | None = Field(default=None, description="Last fetch timestamp")


class GitSyncRequest(BaseModel):
    """Request model for syncing git state."""

    action: str = Field(
        default="auto",
        description="Sync action: auto (smart), pull, push, fetch",
    )
    force: bool = Field(default=False, description="Force operation (use with caution)")


class GitSyncResponse(BaseModel):
    """Response model for git sync operation."""

    success: bool
    action: str = Field(description="Action performed: none, pull, push, commit+push")
    message: str = Field(description="Human-readable result message")
    error: str | None = Field(default=None, description="Error message if failed")
    commits_pulled: int = Field(default=0, description="Number of commits pulled")
    commits_pushed: int = Field(default=0, description="Number of commits pushed")
    files_committed: int = Field(default=0, description="Number of files committed")
    updated_status: GitStatusResponse | None = Field(default=None, description="Updated git status after sync")


class GitRefreshAllResponse(BaseModel):
    """Response model for refreshing git status of all projects."""

    projects_refreshed: int = Field(description="Number of projects refreshed")
    errors: list[str] = Field(default_factory=list, description="Projects that failed to refresh")


# ========== Supervision Models ==========


class SupervisionDecisionResponse(BaseModel):
    """Response model for a supervision decision."""

    timestamp: str = Field(description="Decision timestamp in ISO format")
    decision: str = Field(description="Decision: resume, skip, hold, disable")
    reason: str = Field(description="Reason for the decision")
    trigger: str | None = Field(default=None, description="What triggered the evaluation")
    circuit_state: str | None = Field(default=None, description="Circuit breaker state at time of decision")
    completion_confidence: float | None = Field(default=None, description="Completion confidence score")
    auto_resume_count: int | None = Field(default=None, description="Auto-resume count at time of decision")


class SupervisionStatusResponse(BaseModel):
    """Response model for supervision status of a run."""

    run_id: str
    enabled: bool = Field(description="Whether supervision is enabled")
    policy: str = Field(description="Supervision policy: aggressive, conservative, manual")
    max_auto_resumes: int = Field(description="Maximum auto-resume attempts")
    auto_resume_count: int = Field(description="Current auto-resume count")
    min_time_between_resumes: int = Field(description="Minimum seconds between resumes")
    last_check_at: str | None = Field(default=None, description="Last supervision check timestamp")
    last_resume_at: str | None = Field(default=None, description="Last auto-resume timestamp")
    disabled_reason: str | None = Field(default=None, description="Reason if supervision disabled")
    recent_decisions: list[SupervisionDecisionResponse] = Field(
        default_factory=list, description="Recent supervision decisions"
    )


class SupervisionEvaluateResponse(BaseModel):
    """Response model for manual supervision evaluation."""

    run_id: str
    decision: str = Field(description="Decision: resume or skip")
    reason: str = Field(description="Reason for the decision")
    wait_seconds: int = Field(default=0, description="Seconds to wait before retrying")


class SupervisionDisableRequest(BaseModel):
    """Request model for disabling supervision."""

    reason: str = Field(default="Manual disable", description="Reason for disabling")


# ========== Ralph Loop Models ==========


class RalphIterationResponse(BaseModel):
    """Response model for a single ralph loop iteration."""

    id: str
    run_id: str
    loop_number: int = Field(description="1-indexed loop number")
    started_at: str = Field(description="ISO timestamp when iteration started")
    ended_at: str | None = Field(default=None, description="ISO timestamp when iteration ended")
    duration_seconds: float | None = Field(default=None, description="Iteration duration in seconds")
    files_changed: int = Field(default=0, description="Number of files modified")
    progress_detected: bool = Field(default=False, description="Whether progress was detected")
    has_errors: bool = Field(default=False, description="Whether errors occurred")
    error_message: str | None = Field(default=None, description="Error message if has_errors is true")
    has_completion_signal: bool = Field(default=False, description="Whether completion signal was detected")
    is_test_only: bool = Field(default=False, description="Whether iteration only ran tests")
    confidence_score: float = Field(default=0.0, description="Task completion confidence (0-100)")
    cost_usd: float = Field(default=0.0, description="Cost for this iteration in USD")
    input_tokens: int = Field(default=0, description="Input tokens used")
    output_tokens: int = Field(default=0, description="Output tokens generated")


class RalphIterationsResponse(BaseModel):
    """Response model for iteration history."""

    run_id: str
    iteration_count: int = Field(description="Total number of iterations")
    iterations: list[RalphIterationResponse] = Field(description="List of iterations, most recent first")


class StopLoopResponse(BaseModel):
    """Response model for stopping a ralph loop early."""

    success: bool
    run_id: str
    message: str = Field(description="Result message")
    final_loop_count: int = Field(description="Final loop count when stopped")


# ========== AskUserQuestion Models ==========


class PendingQuestionResponse(BaseModel):
    """Response model for a pending question."""

    id: str
    run_id: str
    question_index: int = Field(description="Index of question in batch (0-based)")
    question_text: str = Field(description="The question text")
    header: str = Field(description="Short label/header for the question")
    options: list[dict[str, str]] = Field(description="List of options with label and description")
    multi_select: bool = Field(default=False, description="Whether multiple options can be selected")
    status: str = Field(description="Status: pending, answered, auto_answered, expired")
    created_at: str = Field(description="Creation timestamp")
    expires_at: str | None = Field(default=None, description="Expiration timestamp")
    selected_labels: list[str] | None = Field(default=None, description="Selected option labels")
    answer_source: str | None = Field(default=None, description="Source of answer: user, timeout_auto, etc.")


class AnswerQuestionRequest(BaseModel):
    """Request model for answering a question."""

    selected_labels: list[str] = Field(description="List of selected option labels")


class PendingQuestionsResponse(BaseModel):
    """Response model for questions on a run."""

    run_id: str
    questions: list[PendingQuestionResponse]
    has_pending: bool = Field(description="Whether there are pending (unanswered) questions")


class QueueFollowupRequest(BaseModel):
    """Request model for queuing a follow-up message."""

    message: str = Field(description="The follow-up message to queue")


class QueueFollowupResponse(BaseModel):
    """Response model for queue-followup endpoint."""

    run_id: str
    action: str = Field(description="Action taken: 'queued' or 'resume_now'")
    message: str | None = Field(default=None, description="The queued message if action is 'queued'")
    message_id: str | None = Field(default=None, description="The ID of the queued message")


class EditQueuedMessageRequest(BaseModel):
    """Request model for editing a queued message."""

    message: str = Field(description="The updated message content")


# ========== Slash Command Models ==========


class SlashCommandResponse(BaseModel):
    """Response model for a slash command or skill."""

    name: str = Field(description="Command name (e.g., commit-push-pr)")
    type: str = Field(description="Type: command or skill")
    description: str = Field(description="Brief description")
    argument_hint: str = Field(default="", description="Argument syntax hint")


class SlashCommandsResponse(BaseModel):
    """Response model for list of slash commands."""

    commands: list[SlashCommandResponse] = Field(description="Available slash commands")


# ========== Todo Tracking Models ==========


class TodoItemResponse(BaseModel):
    """A single todo item from a TodoWrite snapshot."""

    content: str = Field(description="Task description (imperative form)")
    status: str = Field(description="Status: pending, in_progress, or completed")
    active_form: str = Field(description="Present continuous form of the task")


class RunTodosResponse(BaseModel):
    """Response model for todo tracking state of a run."""

    run_id: str
    todos: list[TodoItemResponse] = Field(default_factory=list)
    todo_count: int = Field(default=0, description="Total number of todos")
    completed_count: int = Field(default=0, description="Number completed")
    in_progress_count: int = Field(default=0, description="Number in progress")
    pending_count: int = Field(default=0, description="Number pending")
    captured_at: str | None = Field(default=None, description="ISO timestamp of latest snapshot")


# ========== Project File Autocomplete Models ==========


class ProjectFileResponse(BaseModel):
    """Response model for a file or directory in a project."""

    path: str = Field(description="Relative path from project root")
    type: str = Field(description="Type: file or directory")


class ProjectFilesResponse(BaseModel):
    """Response model for project files list (autocomplete)."""

    project_id: str = Field(description="Project ID")
    files: list[ProjectFileResponse] = Field(description="List of files and directories")
    truncated: bool = Field(default=False, description="Whether results were truncated")


# ========== Activity Log API Models ==========


class ActivityEventResponse(BaseModel):
    """Response model for a single activity event."""

    id: str
    timestamp: str
    actor: str
    action: str
    result: str | None = None
    message: str | None = None
    metadata: dict | None = None


class ActivityListResponse(BaseModel):
    """Response model for activity event list."""

    events: list[ActivityEventResponse]
    total: int


# ========== Work Queue API Models ==========


class WorkQueueItemResponse(BaseModel):
    """Response model for a work queue item."""

    id: str
    project_id: str
    prompt: str
    profile: str
    priority: int
    status: str
    claimed_by: str | None = None
    created_at: str
    claimed_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


class WorkQueueListResponse(BaseModel):
    """Response model for work queue item list."""

    items: list[WorkQueueItemResponse]
    total: int


class WorkQueueAddRequest(BaseModel):
    """Request model for adding a work queue item."""

    project_id: str = Field(description="Project ID to queue work for")
    prompt: str = Field(description="Task prompt")
    profile: str = Field(default="standard", description="Task profile")
    priority: int = Field(default=10, description="Priority (lower = higher)")


# ========== Merge Queue API Models ==========


class MergeQueueEntryResponse(BaseModel):
    """Response model for a merge queue entry."""

    id: str
    run_id: str
    project_id: str
    branch_name: str
    pr_number: int | None = None
    pr_url: str | None = None
    status: str
    priority: int
    conflict_count: int
    max_retries: int
    last_error: str | None = None
    created_at: str
    completed_at: str | None = None


class MergeQueueListResponse(BaseModel):
    """Response model for merge queue entry list."""

    entries: list[MergeQueueEntryResponse]
    total: int


# ========== Witness API Models ==========


class WitnessDecisionResponse(BaseModel):
    """Response model for a witness decision."""

    id: str
    run_id: str
    timestamp: str
    classification: str
    confidence: float
    reasoning: str | None = None
    action: str
    action_result: str | None = None


class WitnessDecisionListResponse(BaseModel):
    """Response model for witness decision list."""

    run_id: str
    decisions: list[WitnessDecisionResponse]


# ========== Formula API Models ==========


class FormulaStepResponse(BaseModel):
    """Response model for a formula step."""

    id: str
    name: str
    prompt: str
    depends_on: list[str] = Field(default_factory=list)
    profile: str = "standard"


class FormulaVariableResponse(BaseModel):
    """Response model for a formula variable."""

    name: str
    type: str = "string"
    required: bool = False
    default: str | None = None
    help: str | None = None


class FormulaTemplateResponse(BaseModel):
    """Response model for a formula template."""

    name: str
    description: str | None = None
    variables: list[FormulaVariableResponse]
    steps: list[FormulaStepResponse]
    use_worktree: bool = True


class FormulaListResponse(BaseModel):
    """Response model for formula template list."""

    formulas: list[FormulaTemplateResponse]


class FormulaRunRequest(BaseModel):
    """Request model for running a formula."""

    project_id: str = Field(description="Project ID to run formula on")
    variables: dict[str, str] = Field(default_factory=dict, description="Variable values")


class FormulaRunResponse(BaseModel):
    """Response model for formula execution."""

    chain_id: str
    step_count: int


# ========== Notification Models ==========


class NotificationResponse(BaseModel):
    """Response model for a persistent notification."""

    id: str
    workspace_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    type: str
    severity: str
    title: str
    message: str | None = None
    metadata: dict | None = None
    read: bool = False
    created_at: str
    read_at: str | None = None


class NotificationsListResponse(BaseModel):
    """Response model for notification list."""

    notifications: list[NotificationResponse]
    unread_count: int


# ========== SDK Session Browser Models ==========


class SDKSessionResponse(BaseModel):
    """Response model for a Claude SDK session."""

    session_id: str
    summary: str
    last_modified: int = Field(description="Unix timestamp")
    file_size: int
    custom_title: str | None = None
    first_prompt: str | None = None
    git_branch: str | None = None
    cwd: str | None = None
    linked_run_ids: list[str] = Field(default_factory=list)


class SessionMessageResponse(BaseModel):
    """Response model for a session message."""

    type: str = Field(description="user or assistant")
    uuid: str
    session_id: str
    message: object = None
    parent_tool_use_id: str | None = None


class SessionDetailResponse(BaseModel):
    """Response model for session detail with messages."""

    session: SDKSessionResponse
    messages: list[SessionMessageResponse]
    total_messages: int
