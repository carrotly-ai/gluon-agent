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
    """Response model for resume operation."""

    original_run_id: str = Field(description="ID of the run being resumed")
    new_run_id: str = Field(description="ID of the new continuation run")
    status: str = Field(description="Status of the new run")


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
