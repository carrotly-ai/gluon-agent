"""Pydantic models for Web API request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class RunResponse(BaseModel):
    """Response model for execution runs."""

    id: str
    project_id: str
    project_name: str = Field(description="Denormalized project name for display")
    status: str
    prompt: str
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


class CreateRunRequest(BaseModel):
    """Request model for creating a new run."""

    project_name: str = Field(description="Name of the project to run on")
    prompt: str = Field(description="Task prompt for Claude")
    model: str = Field(default="sonnet", description="Model tier: opus/sonnet/haiku")
    use_worktree: bool = Field(default=False, description="Execute in isolated Git worktree")


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
    git_branch: str | None = Field(default=None, description="Current git branch")
    git_ahead: int | None = Field(default=None, description="Commits ahead of upstream")
    git_behind: int | None = Field(default=None, description="Commits behind upstream")

    class Config:
        from_attributes = True


class StatusResponse(BaseModel):
    """Response model for overall system status."""

    total_projects: int
    active_runs: int
    total_runs: int


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


class ScanResultResponse(BaseModel):
    """Response model for workspace scan operation."""

    workspace_id: str
    projects_found: int
    projects_added: list[str] = Field(description="Names of newly added projects")
    projects_removed: list[str] = Field(
        default_factory=list,
        description="Names of projects removed (directory no longer exists)",
    )


# Phase 8: Usage Dashboard Models


class UsageSummaryResponse(BaseModel):
    """Response model for usage summary (header display)."""

    today_cost_usd: float = Field(description="Cost in USD for today")
    today_runs: int = Field(description="Number of runs today")
    week_cost_usd: float = Field(description="Cost in USD for past 7 days")
    week_runs: int = Field(description="Number of runs past 7 days")
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


class CommitDetailResponse(BaseModel):
    """Detailed response model for a single commit with files."""

    sha: str = Field(description="Full commit SHA")
    message: str = Field(description="Full commit message (subject + body)")
    author: str = Field(description="Author name")
    author_email: str = Field(description="Author email")
    date: str = Field(description="Commit date in ISO format")
    files: list[FileChangeResponse] = Field(description="Files changed in this commit")


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
