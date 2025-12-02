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
