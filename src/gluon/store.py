"""SQLite persistence layer for Gluon Agent."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gluon.models import (
    CHAT_HISTORY_TTL_HOURS,
    MESSAGE_RUN_MAP_TTL_DAYS,
    TASK_LOCK_TTL_SECS,
    ActivityEvent,
    Agent,
    AgentSchedule,
    ApprovalPolicy,
    ApprovalStatus,
    ChainStatus,
    ChannelMapping,
    ChatHistoryEntry,
    CircuitState,
    CommitFileSnapshot,
    CommitSnapshot,
    ExecutionRun,
    FileChangeSnapshot,
    GitStatus,
    HealthClassification,
    HeartbeatRun,
    HeartbeatStatus,
    ImageAttachment,
    Job,
    JobStatus,
    LinkCode,
    MergeQueueEntry,
    MergeQueueStatus,
    MessageRunMapping,
    Notification,
    NotificationSeverity,
    NotificationType,
    OrchestratorTask,
    PendingApproval,
    PendingQuestion,
    Project,
    QuestionStatus,
    QueuedMessage,
    RalphLoopIteration,
    RecoveryAction,
    RunStatus,
    Session,
    SessionStatus,
    StepStatus,
    SupervisionConfig,
    SupervisionDecision,
    SupervisionPolicy,
    TaskChain,
    TaskComment,
    TaskProfile,
    TaskStatus,
    TaskStep,
    TodoSnapshot,
    User,
    UserRole,
    UserSession,
    WebhookConfig,
    WitnessDecision,
    Worker,
    WorkerStatus,
    WorkerType,
    WorkQueueItem,
    WorkQueueStatus,
    Workspace,
    utc_now,
)

DEFAULT_DB_PATH = Path.home() / ".gluon" / "gluon.db"


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse datetime from database, ensuring timezone awareness.

    Older records may be stored without timezone info. This function
    ensures all returned datetimes are UTC-aware.
    """
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    # If naive (no timezone), assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


SCHEMA = """
-- Workspaces table
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    scan_depth INTEGER DEFAULT 1,
    auto_discover INTEGER DEFAULT 1,
    ignore_patterns TEXT
);

-- Projects table
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT
);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    claude_session_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_prompt TEXT,
    total_cost_usd REAL DEFAULT 0.0,
    total_turns INTEGER DEFAULT 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);
CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_name ON workspaces(name);
"""

# Migration to add workspace_id column if it doesn't exist
MIGRATIONS = [
    """
    -- Add workspace_id to projects if not exists
    ALTER TABLE projects ADD COLUMN workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL;
    """,
    """
    -- Add initiator to execution_runs if not exists
    ALTER TABLE execution_runs ADD COLUMN initiator TEXT;
    """,
    # Git status columns
    "ALTER TABLE projects ADD COLUMN git_is_repo INTEGER DEFAULT 0;",
    "ALTER TABLE projects ADD COLUMN git_branch TEXT;",
    "ALTER TABLE projects ADD COLUMN git_remote TEXT;",
    "ALTER TABLE projects ADD COLUMN git_remote_url TEXT;",
    "ALTER TABLE projects ADD COLUMN git_uncommitted_count INTEGER DEFAULT 0;",
    "ALTER TABLE projects ADD COLUMN git_commits_ahead INTEGER DEFAULT 0;",
    "ALTER TABLE projects ADD COLUMN git_commits_behind INTEGER DEFAULT 0;",
    "ALTER TABLE projects ADD COLUMN git_last_fetch_at TEXT;",
    "ALTER TABLE projects ADD COLUMN git_last_push_at TEXT;",
    "ALTER TABLE projects ADD COLUMN git_last_commit_at TEXT;",
    # Thread tracking for session resume
    "ALTER TABLE execution_runs ADD COLUMN thread_id TEXT;",
    # Claude SDK session ID for resume (separate from internal session FK)
    "ALTER TABLE execution_runs ADD COLUMN claude_session_id TEXT;",
    # Cost tracking for execution runs
    "ALTER TABLE execution_runs ADD COLUMN cost_usd REAL;",
    "ALTER TABLE execution_runs ADD COLUMN input_tokens INTEGER;",
    "ALTER TABLE execution_runs ADD COLUMN output_tokens INTEGER;",
    "ALTER TABLE execution_runs ADD COLUMN model_used TEXT;",
    # Git/worktree tracking for execution runs (Phase 7.1)
    "ALTER TABLE execution_runs ADD COLUMN branch_name TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN source_branch TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN worktree_path TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN use_worktree INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN git_commit_sha TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN pr_number INTEGER;",
    "ALTER TABLE execution_runs ADD COLUMN pr_url TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN pr_status TEXT;",
    # Archive tracking
    "ALTER TABLE execution_runs ADD COLUMN archived INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN archived_at TEXT;",
    # Image attachments (Phase 10.1)
    """
    CREATE TABLE IF NOT EXISTS images (
        id TEXT PRIMARY KEY,
        file_path TEXT NOT NULL,
        original_name TEXT NOT NULL,
        mime_type TEXT,
        size_bytes INTEGER NOT NULL,
        hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS run_images (
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, image_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_run_images_run ON run_images(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_run_images_image ON run_images(image_id);",
    "CREATE INDEX IF NOT EXISTS idx_images_hash ON images(hash);",
    # PR mergeable status for conflict detection
    "ALTER TABLE execution_runs ADD COLUMN pr_mergeable TEXT;",
    # Resume tracking for in-place resume (Phase: Resume Refactor)
    "ALTER TABLE execution_runs ADD COLUMN resume_count INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN last_resumed_at TEXT;",
    # Model selection (Phase: Model Parameter)
    "ALTER TABLE execution_runs ADD COLUMN model TEXT;",
    # Workers table (Distributed Workers)
    """
    CREATE TABLE IF NOT EXISTS workers (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        type TEXT NOT NULL DEFAULT 'local',
        base_url TEXT,
        api_key TEXT NOT NULL,
        max_concurrent INTEGER DEFAULT 4,
        status TEXT DEFAULT 'healthy',
        last_heartbeat TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_workers_name ON workers(name);",
    "CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);",
    # Jobs table (Distributed Queue)
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        prompt TEXT NOT NULL,
        priority INTEGER DEFAULT 5,
        status TEXT NOT NULL DEFAULT 'queued',
        worker_id TEXT REFERENCES workers(id) ON DELETE SET NULL,
        model TEXT,
        use_worktree INTEGER DEFAULT 0,
        session_id TEXT,
        created_at TEXT NOT NULL,
        assigned_at TEXT,
        started_at TEXT,
        completed_at TEXT,
        error_message TEXT,
        lease_expires_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_worker ON jobs(worker_id);",
    # Webhook configs table
    """
    CREATE TABLE IF NOT EXISTS webhook_configs (
        id TEXT PRIMARY KEY,
        handler TEXT NOT NULL,
        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
        secret_key TEXT NOT NULL,
        events TEXT,
        prompt_template TEXT,
        enabled INTEGER DEFAULT 1,
        branches TEXT,
        ignore_branches TEXT,
        labels TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_webhooks_handler ON webhook_configs(handler);",
    "CREATE INDEX IF NOT EXISTS idx_webhooks_project ON webhook_configs(project_id);",
    # Context overflow recovery tracking
    "ALTER TABLE execution_runs ADD COLUMN recovery_count INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN last_recovery_at TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN recovery_from_run_id TEXT;",
    # Recovery progress UI (Phase: Recovery Progress)
    "ALTER TABLE execution_runs ADD COLUMN is_recovering INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN recovery_item_count INTEGER DEFAULT 0;",
    # PR monitoring tracking (Phase: PR Auto-Resume)
    "ALTER TABLE execution_runs ADD COLUMN last_comment_id INTEGER;",
    "ALTER TABLE execution_runs ADD COLUMN last_check_sha TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN auto_resume_enabled INTEGER DEFAULT 1;",
    "ALTER TABLE execution_runs ADD COLUMN auto_resume_count INTEGER DEFAULT 0;",
    # Ralph mode fields (autonomous loop execution)
    "ALTER TABLE execution_runs ADD COLUMN ralph_enabled INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN loop_count INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN max_loops INTEGER DEFAULT 50;",
    "ALTER TABLE execution_runs ADD COLUMN circuit_state TEXT DEFAULT 'CLOSED';",
    "ALTER TABLE execution_runs ADD COLUMN consecutive_no_progress INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN consecutive_same_error INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN last_progress_loop INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN last_error_hash TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN completion_signals INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN test_only_loops INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN completion_confidence REAL DEFAULT 0.0;",
    "ALTER TABLE execution_runs ADD COLUMN completion_reason TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN calls_this_hour INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN hour_start TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN max_calls_per_hour INTEGER DEFAULT 100;",
    "ALTER TABLE execution_runs ADD COLUMN max_cost_usd REAL;",
    # Ralph loop iterations table
    """
    CREATE TABLE IF NOT EXISTS ralph_loop_iterations (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        loop_number INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        files_changed INTEGER DEFAULT 0,
        has_errors INTEGER DEFAULT 0,
        error_summary TEXT,
        output_length INTEGER DEFAULT 0,
        is_test_only INTEGER DEFAULT 0,
        has_completion_signal INTEGER DEFAULT 0,
        progress_detected INTEGER DEFAULT 0,
        confidence_score REAL DEFAULT 0.0,
        claude_session_id TEXT,
        cost_usd REAL DEFAULT 0.0,
        tokens_used INTEGER DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_ralph_iterations_run ON ralph_loop_iterations(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_ralph_iterations_loop ON ralph_loop_iterations(run_id, loop_number);",
    # Supervision fields for ExecutionRun
    "ALTER TABLE execution_runs ADD COLUMN supervision_config TEXT;",  # JSON blob
    "ALTER TABLE execution_runs ADD COLUMN supervision_auto_resume_count INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN last_supervision_check_at TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN last_supervision_resume_at TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN supervision_disabled_reason TEXT;",
    # Supervision decisions audit trail table
    """
    CREATE TABLE IF NOT EXISTS supervision_decisions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        timestamp TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT NOT NULL,
        trigger TEXT,
        circuit_state TEXT,
        completion_confidence REAL,
        calls_this_hour INTEGER,
        cost_usd REAL,
        auto_resume_count INTEGER,
        policy TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_supervision_decisions_run ON supervision_decisions(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_supervision_decisions_timestamp ON supervision_decisions(timestamp);",
    # Circuit breaker HALF_OPEN tracking (missing from original ralph fields)
    "ALTER TABLE execution_runs ADD COLUMN half_open_iterations INTEGER DEFAULT 0;",
    # Original prompt preservation (for auto-resume to reference original task)
    "ALTER TABLE execution_runs ADD COLUMN original_prompt TEXT;",
    # Pending questions table for AskUserQuestion support
    """
    CREATE TABLE IF NOT EXISTS pending_questions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        question_index INTEGER DEFAULT 0,
        question_text TEXT NOT NULL,
        header TEXT NOT NULL,
        options TEXT NOT NULL,
        multi_select INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        answered_at TEXT,
        expires_at TEXT,
        selected_labels TEXT,
        answer_source TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_questions_run ON pending_questions(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_pending_questions_status ON pending_questions(status);",
    # Queued follow-up for sending messages while task is running
    "ALTER TABLE execution_runs ADD COLUMN queued_followup TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN queued_followup_at TEXT;",
    # Migration: queued_followup -> queued_messages (JSON array)
    "ALTER TABLE execution_runs ADD COLUMN queued_messages TEXT;",
    # Commit/file snapshot tables for persisting changes after branch merge/deletion
    """
    CREATE TABLE IF NOT EXISTS commit_snapshots (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        sha TEXT NOT NULL,
        message TEXT NOT NULL,
        full_message TEXT,
        author TEXT NOT NULL,
        author_email TEXT,
        date TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_commit_snapshots_run ON commit_snapshots(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_commit_snapshots_run_ordinal ON commit_snapshots(run_id, ordinal);",
    """
    CREATE TABLE IF NOT EXISTS file_change_snapshots (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        file_path TEXT NOT NULL,
        change_type TEXT NOT NULL,
        additions INTEGER DEFAULT 0,
        deletions INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_file_change_snapshots_run ON file_change_snapshots(run_id);",
    """
    CREATE TABLE IF NOT EXISTS commit_file_snapshots (
        id TEXT PRIMARY KEY,
        commit_snapshot_id TEXT NOT NULL REFERENCES commit_snapshots(id) ON DELETE CASCADE,
        file_path TEXT NOT NULL,
        change_type TEXT NOT NULL,
        additions INTEGER DEFAULT 0,
        deletions INTEGER DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_commit_file_snapshots_commit ON commit_file_snapshots(commit_snapshot_id);",
    # Flags on execution_runs to track snapshot status
    "ALTER TABLE execution_runs ADD COLUMN changes_snapshotted INTEGER DEFAULT 0;",
    "ALTER TABLE execution_runs ADD COLUMN snapshot_at TEXT;",
    # Task profile metadata (JSON blob)
    "ALTER TABLE execution_runs ADD COLUMN metadata TEXT;",
    # Message-to-run mapping for reply-based resume (Chat Integration Consolidation)
    """
    CREATE TABLE IF NOT EXISTS message_run_map (
        id TEXT PRIMARY KEY,
        transport TEXT NOT NULL,
        message_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        UNIQUE(transport, message_id, chat_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_message_run_map_transport ON message_run_map(transport);",
    "CREATE INDEX IF NOT EXISTS idx_message_run_map_lookup ON message_run_map(transport, message_id, chat_id);",
    "CREATE INDEX IF NOT EXISTS idx_message_run_map_expires ON message_run_map(expires_at);",
    # Chat history persistence (Chat Integration Consolidation)
    """
    CREATE TABLE IF NOT EXISTS chat_history (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        transport TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_chat_history_expires ON chat_history(expires_at);",
    # Screenshot interception: add source column to run_images
    "ALTER TABLE run_images ADD COLUMN source TEXT DEFAULT 'user';",
    # Todo tracking: mirror TodoWrite tool calls for dashboard visibility
    """
    CREATE TABLE IF NOT EXISTS todo_snapshots (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        todos TEXT NOT NULL,
        todo_count INTEGER NOT NULL DEFAULT 0,
        completed_count INTEGER NOT NULL DEFAULT 0,
        in_progress_count INTEGER NOT NULL DEFAULT 0,
        pending_count INTEGER NOT NULL DEFAULT 0,
        captured_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_todo_snapshots_run ON todo_snapshots(run_id);",
    # Health monitoring: track last output time for stuck detection
    "ALTER TABLE execution_runs ADD COLUMN last_output_at TEXT;",
    # Task chains: multi-step DAG-based task execution
    """
    CREATE TABLE IF NOT EXISTS task_chains (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        use_worktree INTEGER NOT NULL DEFAULT 0,
        initiator TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_chains_project ON task_chains(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_task_chains_status ON task_chains(status);",
    """
    CREATE TABLE IF NOT EXISTS task_steps (
        id TEXT PRIMARY KEY,
        chain_id TEXT NOT NULL REFERENCES task_chains(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        depends_on TEXT NOT NULL DEFAULT '[]',
        profile TEXT NOT NULL DEFAULT 'standard',
        status TEXT NOT NULL DEFAULT 'pending',
        run_id TEXT REFERENCES execution_runs(id),
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        error_message TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_steps_chain ON task_steps(chain_id);",
    "CREATE INDEX IF NOT EXISTS idx_task_steps_status ON task_steps(status);",
    # Link runs back to chains
    "ALTER TABLE execution_runs ADD COLUMN chain_id TEXT;",
    "ALTER TABLE execution_runs ADD COLUMN step_id TEXT;",
    # Activity Log (F11): cross-agent queryable event stream
    """
    CREATE TABLE IF NOT EXISTS activity_events (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        result TEXT,
        message TEXT,
        metadata TEXT,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_events(timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_activity_actor ON activity_events(actor);",
    "CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_events(action);",
    # Work Queue (F12): shared work queue for autonomous agent claiming
    """
    CREATE TABLE IF NOT EXISTS work_queue (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        prompt TEXT NOT NULL,
        profile TEXT DEFAULT 'standard',
        priority INTEGER DEFAULT 10,
        status TEXT NOT NULL DEFAULT 'pending',
        claimed_by TEXT,
        created_at TEXT NOT NULL,
        claimed_at TEXT,
        started_at TEXT,
        completed_at TEXT,
        last_heartbeat_at TEXT,
        error_message TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_work_queue_status ON work_queue(status);",
    "CREATE INDEX IF NOT EXISTS idx_work_queue_priority ON work_queue(priority, created_at);",
    # Merge Queue (F8): sequential merge processing for PRs
    """
    CREATE TABLE IF NOT EXISTS merge_queue (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        branch_name TEXT NOT NULL,
        pr_number INTEGER,
        pr_url TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        priority INTEGER DEFAULT 10,
        conflict_count INTEGER DEFAULT 0,
        max_retries INTEGER DEFAULT 3,
        last_error TEXT,
        created_at TEXT NOT NULL,
        processing_started_at TEXT,
        completed_at TEXT,
        next_retry_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_merge_queue_status ON merge_queue(status);",
    "CREATE INDEX IF NOT EXISTS idx_merge_queue_priority ON merge_queue(priority, created_at);",
    # Witness Pattern (F9): LLM-based health classification
    """
    CREATE TABLE IF NOT EXISTS witness_decisions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        classification TEXT NOT NULL,
        confidence REAL NOT NULL,
        reasoning TEXT,
        action TEXT NOT NULL DEFAULT 'none',
        action_result TEXT,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_witness_run ON witness_decisions(run_id);",
    # Track shared run_id on task chains (unified formula execution)
    "ALTER TABLE task_chains ADD COLUMN run_id TEXT;",
    # Persistent notifications table
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY,
        workspace_id TEXT,
        project_id TEXT,
        run_id TEXT,
        session_id TEXT,
        type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'info',
        title TEXT NOT NULL,
        message TEXT,
        metadata TEXT,
        read INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        read_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_notifications_run ON notifications(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_notifications_workspace ON notifications(workspace_id);",
    # Workspace-specific settings and environment variables
    """
    CREATE TABLE IF NOT EXISTS workspace_settings (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, key)
    );
    """,
    # Agents: persistent per-workspace identities (Theme B Phase 1)
    """
    CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT,
        role TEXT DEFAULT 'worker',
        is_active INTEGER DEFAULT 1,
        monthly_budget_usd REAL,
        max_concurrent_runs INTEGER DEFAULT 1,
        last_active_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(workspace_id, name)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_agents_workspace ON agents(workspace_id);",
    "CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);",
    # Link ExecutionRun to Agent (nullable — pre-existing runs remain unlinked)
    "ALTER TABLE execution_runs ADD COLUMN agent_id TEXT;",
    "CREATE INDEX IF NOT EXISTS idx_runs_agent ON execution_runs(agent_id);",
    # OrchestratorTask: tracked unit of work above the run layer (Theme B Phase 3)
    """
    CREATE TABLE IF NOT EXISTS orchestrator_tasks (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'backlog',
        priority INTEGER DEFAULT 5,
        assigned_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
        created_by TEXT DEFAULT 'cli',
        assigned_files TEXT,  -- JSON array of file globs this task touches
        parent_task_id TEXT REFERENCES orchestrator_tasks(id) ON DELETE SET NULL,
        execution_locked_at TEXT,
        execution_run_id TEXT REFERENCES execution_runs(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_project ON orchestrator_tasks(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_agent ON orchestrator_tasks(assigned_agent_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON orchestrator_tasks(status);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON orchestrator_tasks(priority DESC, created_at ASC);",
    # TaskComment: agent-to-agent / human coordination messages on a task
    """
    CREATE TABLE IF NOT EXISTS task_comments (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES orchestrator_tasks(id) ON DELETE CASCADE,
        author_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
        author_label TEXT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id, created_at);",
    # AgentSchedule: cron-based wakeup rules (Theme B Phase 2)
    """
    CREATE TABLE IF NOT EXISTS agent_schedules (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        prompt_template TEXT NOT NULL,
        schedule_cron TEXT NOT NULL,
        is_enabled INTEGER DEFAULT 1,
        coalesce_ttl_seconds INTEGER DEFAULT 300,
        task_profile TEXT DEFAULT 'quick',
        consecutive_failures INTEGER DEFAULT 0,
        last_fired_at TEXT,
        next_fire_at TEXT,
        description TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_schedules_agent ON agent_schedules(agent_id);",
    "CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON agent_schedules(is_enabled);",
    "CREATE INDEX IF NOT EXISTS idx_schedules_next_fire ON agent_schedules(next_fire_at);",
    # HeartbeatRun: record of each scheduled firing (Theme B Phase 2)
    """
    CREATE TABLE IF NOT EXISTS heartbeat_runs (
        id TEXT PRIMARY KEY,
        schedule_id TEXT NOT NULL REFERENCES agent_schedules(id) ON DELETE CASCADE,
        agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        execution_run_id TEXT REFERENCES execution_runs(id) ON DELETE SET NULL,
        fired_at TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        result_summary TEXT,
        error_message TEXT,
        completed_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_heartbeats_agent ON heartbeat_runs(agent_id, fired_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_heartbeats_schedule ON heartbeat_runs(schedule_id, fired_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_heartbeats_run ON heartbeat_runs(execution_run_id);",
    # Approval gates (Theme D1)
    "ALTER TABLE execution_runs ADD COLUMN approval_policy TEXT DEFAULT 'permissive';",
    """
    CREATE TABLE IF NOT EXISTS pending_approvals (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
        tool_name TEXT NOT NULL,
        tool_input TEXT,
        tool_use_id TEXT,
        classification_reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        decision_reason TEXT,
        decided_by TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        timeout_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_approvals_run ON pending_approvals(run_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_approvals_status ON pending_approvals(status, created_at);",
    # Transport delivery tracking (approval watcher uses this to find
    # PENDING approvals not yet posted to async transports like Telegram).
    # NULL = needs delivery; non-NULL = already posted at that time.
    "ALTER TABLE pending_approvals ADD COLUMN notified_at TEXT;",
    "CREATE INDEX IF NOT EXISTS idx_approvals_notified ON pending_approvals(notified_at, status);",
    # Workspace rolling budgets (Theme D2) — daily/monthly caps across the workspace
    "ALTER TABLE workspaces ADD COLUMN daily_budget_usd REAL;",
    "ALTER TABLE workspaces ADD COLUMN monthly_budget_usd REAL;",
    # Hard per-run caps (Theme D3). Both limits default NULL = no enforcement.
    "ALTER TABLE execution_runs ADD COLUMN max_tool_calls INTEGER;",
    "ALTER TABLE execution_runs ADD COLUMN max_duration_minutes INTEGER;",
    "ALTER TABLE execution_runs ADD COLUMN tool_call_count INTEGER DEFAULT 0;",
    # Multi-user auth (D5 Phase 1) — users + sessions. Rows only exist when
    # GLUON_AUTH_ENABLED=true. Single-user mode never touches these tables.
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        email TEXT,
        auth_provider TEXT NOT NULL,
        auth_subject TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator',
        disabled INTEGER NOT NULL DEFAULT 0,
        telegram_user_id INTEGER,
        discord_user_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_login_at TEXT,
        UNIQUE(auth_provider, auth_subject)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);",
    "CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_user_id) WHERE telegram_user_id IS NOT NULL;",
    "CREATE INDEX IF NOT EXISTS idx_users_discord ON users(discord_user_id) WHERE discord_user_id IS NOT NULL;",
    """
    CREATE TABLE IF NOT EXISTS user_sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_expires ON user_sessions(expires_at);",
    # D5 Phase 2 attribution — nullable FKs to users(id) on action-attributable
    # tables. NULL means "pre-auth era" or "SYSTEM_USER" (no persistence).
    # No FK constraint because the users table is only populated when
    # GLUON_AUTH_ENABLED=true, but the column must exist always.
    "ALTER TABLE execution_runs ADD COLUMN user_id TEXT;",
    "ALTER TABLE orchestrator_tasks ADD COLUMN created_by_user_id TEXT;",
    "ALTER TABLE pending_approvals ADD COLUMN decided_by_user_id TEXT;",
    "CREATE INDEX IF NOT EXISTS idx_runs_user ON execution_runs(user_id) WHERE user_id IS NOT NULL;",
    (
        "CREATE INDEX IF NOT EXISTS idx_tasks_creator ON orchestrator_tasks(created_by_user_id) "
        "WHERE created_by_user_id IS NOT NULL;"
    ),
    # D5 Phase 4 self-serve linking — short-lived one-time codes a logged-in
    # user generates in the dashboard, then redeems by sending `/link <code>`
    # to the bot. ``code`` is the URL-safe random token (PRIMARY KEY for
    # constant-time uniqueness checks); ``transport`` is "telegram" /
    # "discord". A row is consumed by setting ``consumed_at`` (so we keep
    # an audit trail of past links) — once consumed, the same code can't
    # be reused.
    """
    CREATE TABLE IF NOT EXISTS link_codes (
        code TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        transport TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_link_codes_user ON link_codes(user_id);",
    (
        "CREATE INDEX IF NOT EXISTS idx_link_codes_active "
        "ON link_codes(transport, expires_at) WHERE consumed_at IS NULL;"
    ),
    "ALTER TABLE execution_runs ADD COLUMN ci_status TEXT;",
    # Discord/Telegram question escalation — track whether a pending question
    # has been posted to an async transport so the QuestionWatcher doesn't
    # double-post. Mirrors `pending_approvals.notified_at`.
    "ALTER TABLE pending_questions ADD COLUMN notified_at TEXT;",
    ("CREATE INDEX IF NOT EXISTS idx_pending_questions_notified ON pending_questions(status, notified_at);"),
    # Lookup index: find the most-recent message_run_map row for a given
    # run, used by approval/question watchers to route notifications back
    # to the channel that originated the run.
    "CREATE INDEX IF NOT EXISTS idx_message_run_map_run ON message_run_map(run_id, created_at);",
]

DEFAULT_LOG_PATH = Path.home() / ".gluon" / "logs"


class GluonStore:
    """SQLite-based storage for projects and sessions."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        """Initialize database schema and run migrations."""
        with self._get_conn() as conn:
            # Check which tables exist
            existing_tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }

            # Create tables that don't exist
            if "workspaces" not in existing_tables:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS workspaces (
                        id TEXT PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        scan_depth INTEGER DEFAULT 1,
                        auto_discover INTEGER DEFAULT 1,
                        ignore_patterns TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_name ON workspaces(name)")

            if "projects" not in existing_tables:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        path TEXT NOT NULL,
                        workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        metadata TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id)")

            if "sessions" not in existing_tables:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        claude_session_id TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_prompt TEXT,
                        total_cost_usd REAL DEFAULT 0.0,
                        total_turns INTEGER DEFAULT 0
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")

            if "execution_runs" not in existing_tables:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS execution_runs (
                        id TEXT PRIMARY KEY,
                        session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        pid INTEGER,
                        status TEXT NOT NULL DEFAULT 'pending',
                        prompt TEXT NOT NULL,
                        initiator TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        exit_code INTEGER,
                        log_path TEXT,
                        error_message TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project ON execution_runs(project_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON execution_runs(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_initiator ON execution_runs(initiator)")

            if "channel_mappings" not in existing_tables:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS channel_mappings (
                        id TEXT PRIMARY KEY,
                        transport TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        project_name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(transport, channel_id)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_mappings_transport ON channel_mappings(transport)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mappings_channel ON channel_mappings(transport, channel_id)"
                )

            # Settings table for key-value configuration
            if "settings" not in existing_tables:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                # Set default settings
                now = utc_now().isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("auto_create_pr", "true", now),
                )
                # Git author identity settings
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("git_user_name", "", now),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("git_user_email", "", now),
                )
                # Security sandbox setting (default enabled)
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("sandbox_enabled", "true", now),
                )

            # Run migrations for existing tables
            for migration in MIGRATIONS:
                try:
                    conn.executescript(migration)
                except sqlite3.OperationalError:
                    pass  # Column/table already exists

    # ========== Project CRUD ==========

    def create_project(
        self, name: str, path: Path, metadata: dict | None = None, workspace_id: str | None = None
    ) -> Project:
        """Create a new project."""
        project = Project(name=name, path=path, metadata=metadata, workspace_id=workspace_id)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO projects (id, name, path, workspace_id, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.id,
                    project.name,
                    str(project.path),
                    project.workspace_id,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    json.dumps(project.metadata) if project.metadata else None,
                ),
            )
        return project

    def get_project(self, project_id: str) -> Project | None:
        """Get project by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row:
                return self._row_to_project(row)
        return None

    def get_project_by_name(self, name: str) -> Project | None:
        """Get project by name."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
            if row:
                return self._row_to_project(row)
        return None

    def list_projects(self) -> list[Project]:
        """List all projects."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
            return [self._row_to_project(row) for row in rows]

    def update_project(self, project: Project) -> None:
        """Update an existing project."""
        project.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE projects
                SET name = ?, path = ?, updated_at = ?, metadata = ?
                WHERE id = ?
                """,
                (
                    project.name,
                    str(project.path),
                    project.updated_at.isoformat(),
                    json.dumps(project.metadata) if project.metadata else None,
                    project.id,
                ),
            )

    def delete_project(self, project_id: str) -> bool:
        """Delete a project and its sessions."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cursor.rowcount > 0

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        """Convert database row to Project model."""
        return Project(
            id=row["id"],
            name=row["name"],
            path=Path(row["path"]),
            workspace_id=row["workspace_id"] if "workspace_id" in row.keys() else None,
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def get_project_by_path(self, path: Path) -> Project | None:
        """Get project by path."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE path = ?", (str(path.resolve()),)).fetchone()
            if row:
                return self._row_to_project(row)
        return None

    def list_projects_by_workspace(self, workspace_id: str) -> list[Project]:
        """List all projects in a workspace."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM projects WHERE workspace_id = ? ORDER BY name",
                (workspace_id,),
            ).fetchall()
            return [self._row_to_project(row) for row in rows]

    # ========== Git Status CRUD ==========

    def get_git_status(self, project_id: str) -> GitStatus | None:
        """Get cached git status for a project."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT git_is_repo, git_branch, git_remote, git_remote_url,
                       git_uncommitted_count, git_commits_ahead, git_commits_behind,
                       git_last_fetch_at, git_last_push_at, git_last_commit_at
                FROM projects WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
            if row and row["git_is_repo"] is not None:
                return GitStatus(
                    is_git_repo=bool(row["git_is_repo"]),
                    branch=row["git_branch"],
                    remote=row["git_remote"],
                    remote_url=row["git_remote_url"],
                    has_uncommitted=row["git_uncommitted_count"] > 0,
                    uncommitted_count=row["git_uncommitted_count"] or 0,
                    commits_ahead=row["git_commits_ahead"] or 0,
                    commits_behind=row["git_commits_behind"] or 0,
                    last_fetch_at=_parse_datetime(row["git_last_fetch_at"]),
                    last_push_at=_parse_datetime(row["git_last_push_at"]),
                    last_commit_at=_parse_datetime(row["git_last_commit_at"]),
                )
        return None

    def update_git_status(self, project_id: str, status: GitStatus) -> None:
        """Update cached git status for a project."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE projects SET
                    git_is_repo = ?,
                    git_branch = ?,
                    git_remote = ?,
                    git_remote_url = ?,
                    git_uncommitted_count = ?,
                    git_commits_ahead = ?,
                    git_commits_behind = ?,
                    git_last_fetch_at = ?,
                    git_last_push_at = ?,
                    git_last_commit_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if status.is_git_repo else 0,
                    status.branch,
                    status.remote,
                    status.remote_url,
                    status.uncommitted_count,
                    status.commits_ahead,
                    status.commits_behind,
                    status.last_fetch_at.isoformat() if status.last_fetch_at else None,
                    status.last_push_at.isoformat() if status.last_push_at else None,
                    status.last_commit_at.isoformat() if status.last_commit_at else None,
                    utc_now().isoformat(),
                    project_id,
                ),
            )

    # ========== Session CRUD ==========

    def create_session(self, project_id: str, prompt: str | None = None) -> Session:
        """Create a new session for a project."""
        session = Session(project_id=project_id, last_prompt=prompt)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO sessions
                (id, project_id, claude_session_id, status, created_at, updated_at,
                 last_prompt, total_cost_usd, total_turns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.project_id,
                    session.claude_session_id,
                    session.status.value,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    session.last_prompt,
                    session.total_cost_usd,
                    session.total_turns,
                ),
            )
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get session by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                return self._row_to_session(row)
        return None

    def get_session_by_short_id(self, short_id: str, project_id: str | None = None) -> Session | None:
        """Get session by short ID prefix (at least 4 chars), optionally filtered by project."""
        if len(short_id) < 4:
            return None
        with self._get_conn() as conn:
            if project_id:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE id LIKE ? AND project_id = ? LIMIT 1",
                    (f"{short_id}%", project_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE id LIKE ? LIMIT 1",
                    (f"{short_id}%",),
                ).fetchone()
            if row:
                return self._row_to_session(row)
        return None

    def get_latest_session(
        self,
        project_id: str,
        statuses: list[SessionStatus] | None = None,
    ) -> Session | None:
        """Get the most recent session for a project, optionally filtered by status."""
        with self._get_conn() as conn:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                status_values = [s.value for s in statuses]
                row = conn.execute(
                    f"""
                    SELECT * FROM sessions
                    WHERE project_id = ? AND status IN ({placeholders})
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    [project_id, *status_values],
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE project_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
            if row:
                return self._row_to_session(row)
        return None

    def list_sessions(self, project_id: str | None = None) -> list[Session]:
        """List sessions, optionally filtered by project."""
        with self._get_conn() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE project_id = ? ORDER BY updated_at DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
            return [self._row_to_session(row) for row in rows]

    def update_session(self, session: Session) -> None:
        """Update an existing session."""
        session.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET claude_session_id = ?, status = ?, updated_at = ?,
                    last_prompt = ?, total_cost_usd = ?, total_turns = ?
                WHERE id = ?
                """,
                (
                    session.claude_session_id,
                    session.status.value,
                    session.updated_at.isoformat(),
                    session.last_prompt,
                    session.total_cost_usd,
                    session.total_turns,
                    session.id,
                ),
            )

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Convert database row to Session model."""
        return Session(
            id=row["id"],
            project_id=row["project_id"],
            claude_session_id=row["claude_session_id"],
            status=SessionStatus(row["status"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
            last_prompt=row["last_prompt"],
            total_cost_usd=row["total_cost_usd"],
            total_turns=row["total_turns"],
        )

    # ========== Workspace CRUD ==========

    def create_workspace(
        self,
        name: str,
        path: Path,
        *,
        daily_budget_usd: float | None = None,
        monthly_budget_usd: float | None = None,
    ) -> Workspace:
        """Create a new workspace."""
        workspace = Workspace(
            name=name,
            path=path,
            daily_budget_usd=daily_budget_usd,
            monthly_budget_usd=monthly_budget_usd,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO workspaces
                (id, name, path, created_at, updated_at, scan_depth, auto_discover,
                 ignore_patterns, daily_budget_usd, monthly_budget_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace.id,
                    workspace.name,
                    str(workspace.path),
                    workspace.created_at.isoformat(),
                    workspace.updated_at.isoformat(),
                    workspace.scan_depth,
                    1 if workspace.auto_discover else 0,
                    json.dumps(workspace.ignore_patterns),
                    workspace.daily_budget_usd,
                    workspace.monthly_budget_usd,
                ),
            )
        return workspace

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        """Get workspace by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
            if row:
                return self._row_to_workspace(row)
        return None

    def get_workspace_by_name(self, name: str) -> Workspace | None:
        """Get workspace by name."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE name = ?", (name,)).fetchone()
            if row:
                return self._row_to_workspace(row)
        return None

    def list_workspaces(self) -> list[Workspace]:
        """List all workspaces."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM workspaces ORDER BY name").fetchall()
            return [self._row_to_workspace(row) for row in rows]

    def update_workspace(self, workspace: Workspace) -> None:
        """Update an existing workspace."""
        workspace.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE workspaces
                SET name = ?, path = ?, updated_at = ?, scan_depth = ?, auto_discover = ?,
                    ignore_patterns = ?, daily_budget_usd = ?, monthly_budget_usd = ?
                WHERE id = ?
                """,
                (
                    workspace.name,
                    str(workspace.path),
                    workspace.updated_at.isoformat(),
                    workspace.scan_depth,
                    1 if workspace.auto_discover else 0,
                    json.dumps(workspace.ignore_patterns),
                    workspace.daily_budget_usd,
                    workspace.monthly_budget_usd,
                    workspace.id,
                ),
            )

    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace (projects are kept but unlinked)."""
        with self._get_conn() as conn:
            # Clean up workspace settings before deleting
            conn.execute("DELETE FROM workspace_settings WHERE workspace_id = ?", (workspace_id,))
            cursor = conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            return cursor.rowcount > 0

    def _row_to_workspace(self, row: sqlite3.Row) -> Workspace:
        """Convert database row to Workspace model."""
        # Guard budget columns so older DBs (pre-migration) still hydrate cleanly
        keys = row.keys()
        daily_budget = row["daily_budget_usd"] if "daily_budget_usd" in keys else None
        monthly_budget = row["monthly_budget_usd"] if "monthly_budget_usd" in keys else None
        return Workspace(
            id=row["id"],
            name=row["name"],
            path=Path(row["path"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
            scan_depth=row["scan_depth"],
            auto_discover=bool(row["auto_discover"]),
            ignore_patterns=json.loads(row["ignore_patterns"]) if row["ignore_patterns"] else [],
            daily_budget_usd=daily_budget,
            monthly_budget_usd=monthly_budget,
        )

    # ========== Workspace Rolling Budgets (Theme D2) ==========

    def get_workspace_spend_since(self, workspace_id: str, since: datetime) -> float:
        """Sum cost_usd of runs across all projects in the workspace since timestamp.

        Runs with NULL cost_usd are treated as zero. Only runs whose projects
        currently belong to the given workspace contribute; detached runs (whose
        project has been moved or removed) are ignored.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0) AS total
                FROM execution_runs
                WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = ?)
                  AND created_at >= ?
                """,
                (workspace_id, since.isoformat()),
            ).fetchone()
            return float(row["total"]) if row else 0.0

    def get_workspace_daily_spend(self, workspace_id: str, now: datetime | None = None) -> float:
        """Spend since today's UTC midnight across the workspace."""
        from datetime import UTC

        reference = now if now is not None else datetime.now(UTC)
        day_start = reference.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.get_workspace_spend_since(workspace_id, day_start)

    def get_workspace_monthly_spend(self, workspace_id: str, now: datetime | None = None) -> float:
        """Spend since the first of the current UTC month across the workspace."""
        from datetime import UTC

        reference = now if now is not None else datetime.now(UTC)
        month_start = reference.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.get_workspace_spend_since(workspace_id, month_start)

    # ========== Agent CRUD (Theme B Phase 1) ==========

    def create_agent(
        self,
        workspace_id: str,
        name: str,
        *,
        description: str | None = None,
        role: str = "worker",
        monthly_budget_usd: float | None = None,
        max_concurrent_runs: int = 1,
    ) -> Agent:
        """Create a new agent within a workspace.

        Raises sqlite3.IntegrityError if (workspace_id, name) already exists.
        """
        agent = Agent(
            workspace_id=workspace_id,
            name=name,
            description=description,
            role=role,
            monthly_budget_usd=monthly_budget_usd,
            max_concurrent_runs=max_concurrent_runs,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agents
                (id, workspace_id, name, description, role, is_active,
                 monthly_budget_usd, max_concurrent_runs, last_active_at,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent.id,
                    agent.workspace_id,
                    agent.name,
                    agent.description,
                    agent.role,
                    1 if agent.is_active else 0,
                    agent.monthly_budget_usd,
                    agent.max_concurrent_runs,
                    agent.last_active_at.isoformat() if agent.last_active_at else None,
                    agent.created_at.isoformat(),
                    agent.updated_at.isoformat(),
                ),
            )
        return agent

    def get_agent(self, agent_id: str) -> Agent | None:
        """Get an agent by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            return self._row_to_agent(row) if row else None

    def get_agent_by_name(self, workspace_id: str, name: str) -> Agent | None:
        """Get an agent by (workspace_id, name)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agents WHERE workspace_id = ? AND name = ?",
                (workspace_id, name),
            ).fetchone()
            return self._row_to_agent(row) if row else None

    def list_agents(
        self,
        workspace_id: str | None = None,
        *,
        is_active: bool | None = None,
    ) -> list[Agent]:
        """List agents, optionally filtered by workspace and active status."""
        query = "SELECT * FROM agents"
        params: list[Any] = []
        conditions: list[str] = []
        if workspace_id is not None:
            conditions.append("workspace_id = ?")
            params.append(workspace_id)
        if is_active is not None:
            conditions.append("is_active = ?")
            params.append(1 if is_active else 0)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at ASC"

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_agent(row) for row in rows]

    def update_agent(self, agent: Agent) -> None:
        """Persist modifications to an agent record."""
        agent.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE agents SET
                    name = ?, description = ?, role = ?, is_active = ?,
                    monthly_budget_usd = ?, max_concurrent_runs = ?,
                    last_active_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    agent.name,
                    agent.description,
                    agent.role,
                    1 if agent.is_active else 0,
                    agent.monthly_budget_usd,
                    agent.max_concurrent_runs,
                    agent.last_active_at.isoformat() if agent.last_active_at else None,
                    agent.updated_at.isoformat(),
                    agent.id,
                ),
            )

    def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent. Historical runs are preserved; their agent_id is set to NULL.

        Returns True if the agent was found and deleted.
        """
        with self._get_conn() as conn:
            conn.execute("UPDATE execution_runs SET agent_id = NULL WHERE agent_id = ?", (agent_id,))
            cursor = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            return cursor.rowcount > 0

    def get_agent_monthly_spend(self, agent_id: str, since: datetime) -> float:
        """Sum cost_usd of runs for this agent since the given timestamp.

        Callers typically pass the current month's start (UTC). Runs with NULL
        cost_usd are treated as zero. Only runs linked to this agent contribute.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0) AS total
                FROM execution_runs
                WHERE agent_id = ? AND created_at >= ?
                """,
                (agent_id, since.isoformat()),
            ).fetchone()
            return float(row["total"]) if row else 0.0

    def count_agent_active_runs(self, agent_id: str) -> int:
        """Count currently-running or pending runs for an agent."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM execution_runs
                WHERE agent_id = ? AND status IN ('pending', 'running')
                """,
                (agent_id,),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def _row_to_agent(self, row: sqlite3.Row) -> Agent:
        """Convert database row to Agent model."""
        return Agent(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            description=row["description"],
            role=row["role"] or "worker",
            is_active=bool(row["is_active"]),
            monthly_budget_usd=row["monthly_budget_usd"],
            max_concurrent_runs=row["max_concurrent_runs"] or 1,
            last_active_at=_parse_datetime(row["last_active_at"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
        )

    # ========== User CRUD (D5 Phase 1 — multi-user auth) ==========
    #
    # These tables are only populated when GLUON_AUTH_ENABLED=true. In
    # single-user mode the SYSTEM_USER singleton from `auth.py` stands in and
    # nothing is ever written here.

    def create_user(
        self,
        username: str,
        display_name: str,
        *,
        auth_subject: str,
        auth_provider: str = "local",
        email: str | None = None,
        role: UserRole = UserRole.OPERATOR,
    ) -> User:
        """Create a new user.

        Raises sqlite3.IntegrityError on duplicate username or
        (auth_provider, auth_subject).
        """
        user = User(
            username=username,
            display_name=display_name,
            auth_subject=auth_subject,
            auth_provider=auth_provider,  # type: ignore[arg-type]
            email=email,
            role=role,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO users
                (id, username, display_name, email, auth_provider, auth_subject,
                 role, disabled, telegram_user_id, discord_user_id,
                 created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.username,
                    user.display_name,
                    user.email,
                    user.auth_provider.value,
                    user.auth_subject,
                    user.role.value,
                    1 if user.disabled else 0,
                    user.telegram_user_id,
                    user.discord_user_id,
                    user.created_at.isoformat(),
                    user.updated_at.isoformat(),
                    user.last_login_at.isoformat() if user.last_login_at else None,
                ),
            )
        return user

    def get_user(self, user_id: str) -> User | None:
        """Get a user by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> User | None:
        """Get a user by username. Case-insensitive via COLLATE NOCASE."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def get_user_by_auth_subject(self, auth_provider: str, auth_subject: str) -> User | None:
        """Get a user by (provider, subject) — used during login/OIDC callback."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE auth_provider = ? AND auth_subject = ?",
                (auth_provider, auth_subject),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def get_user_by_telegram_id(self, telegram_user_id: int) -> User | None:
        """Look up the user linked to a Telegram account (Phase 4)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def get_user_by_discord_id(self, discord_user_id: int) -> User | None:
        """Look up the user linked to a Discord account (Phase 4)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE discord_user_id = ?",
                (discord_user_id,),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def list_users(self, *, include_disabled: bool = False) -> list[User]:
        """List all users, ordered by username."""
        query = "SELECT * FROM users"
        if not include_disabled:
            query += " WHERE disabled = 0"
        query += " ORDER BY username COLLATE NOCASE"
        with self._get_conn() as conn:
            return [self._row_to_user(r) for r in conn.execute(query).fetchall()]

    def update_user(self, user: User) -> User:
        """Update a user's mutable fields. `updated_at` is bumped automatically."""
        user.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE users
                SET display_name = ?, email = ?, role = ?, disabled = ?,
                    auth_subject = ?,
                    telegram_user_id = ?, discord_user_id = ?,
                    last_login_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    user.display_name,
                    user.email,
                    user.role.value,
                    1 if user.disabled else 0,
                    user.auth_subject,
                    user.telegram_user_id,
                    user.discord_user_id,
                    user.last_login_at.isoformat() if user.last_login_at else None,
                    user.updated_at.isoformat(),
                    user.id,
                ),
            )
        return user

    def delete_user(self, user_id: str) -> None:
        """Hard-delete a user. Usually you want `user.disabled = True` instead —
        this removes all audit attribution and is only meant for test cleanup
        or mistaken bootstraps."""
        with self._get_conn() as conn:
            # ON DELETE CASCADE handles user_sessions
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            email=row["email"],
            auth_provider=row["auth_provider"],
            auth_subject=row["auth_subject"],
            role=UserRole(row["role"]),
            disabled=bool(row["disabled"]),
            telegram_user_id=row["telegram_user_id"],
            discord_user_id=row["discord_user_id"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
            last_login_at=_parse_datetime(row["last_login_at"]),
        )

    # ========== UserSession CRUD (D5 Phase 1) ==========

    def create_user_session(
        self,
        user_id: str,
        expires_at: datetime,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> UserSession:
        """Create a new auth session for an authenticated user."""
        session = UserSession(
            user_id=user_id,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO user_sessions
                (id, user_id, created_at, expires_at, last_seen_at, ip, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.user_id,
                    session.created_at.isoformat(),
                    session.expires_at.isoformat(),
                    session.last_seen_at.isoformat(),
                    session.ip,
                    session.user_agent,
                ),
            )
        return session

    def get_user_session(self, session_id: str) -> UserSession | None:
        """Get an auth session by ID. Returns None for unknown or expired sessions."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM user_sessions WHERE id = ?", (session_id,)).fetchone()
            if not row:
                return None
            session = self._row_to_user_session(row)
            # Expired sessions are returned-but-filterable: the auth layer
            # decides what to do (renew, reject, etc.). We don't auto-delete.
            return session

    def touch_user_session(self, session_id: str, *, new_expires_at: datetime | None = None) -> None:
        """Update `last_seen_at` (and optionally roll the expiry forward)."""
        now = utc_now()
        with self._get_conn() as conn:
            if new_expires_at:
                conn.execute(
                    "UPDATE user_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                    (now.isoformat(), new_expires_at.isoformat(), session_id),
                )
            else:
                conn.execute(
                    "UPDATE user_sessions SET last_seen_at = ? WHERE id = ?",
                    (now.isoformat(), session_id),
                )

    def delete_user_session(self, session_id: str) -> None:
        """Explicit logout — delete a single session."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM user_sessions WHERE id = ?", (session_id,))

    def delete_user_sessions_for_user(self, user_id: str) -> int:
        """Delete all sessions for a user. Returns count deleted.

        Called on password change, role change, or disable — per the design
        doc's "rotation" policy (§4.4).
        """
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            return cur.rowcount

    def delete_expired_user_sessions(self) -> int:
        """Sweep expired sessions. Suitable for a periodic cleanup job."""
        now = utc_now().isoformat()
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (now,))
            return cur.rowcount

    def _row_to_user_session(self, row: sqlite3.Row) -> UserSession:
        return UserSession(
            id=row["id"],
            user_id=row["user_id"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            expires_at=_parse_datetime(row["expires_at"]),  # type: ignore[arg-type]
            last_seen_at=_parse_datetime(row["last_seen_at"]),  # type: ignore[arg-type]
            ip=row["ip"],
            user_agent=row["user_agent"],
        )

    # ========== LinkCode CRUD (D5 Phase 4 self-serve) ==========

    def create_link_code(
        self,
        *,
        user_id: str,
        transport: str,
        ttl_minutes: int = 10,
    ) -> LinkCode:
        """Create a one-time code that binds a chat identity to ``user_id``
        when consumed by the bot. Default TTL is 10 minutes.

        The code is short and case-insensitive at consumption time so users
        can re-type it on a phone without fighting autocorrect — but it has
        ~50 bits of entropy, well past brute-force range for a 10-minute
        window. We also tear down any prior unconsumed codes for the same
        ``(user_id, transport)`` so an active "Link Telegram" panel
        always shows the most recent code.
        """
        from datetime import timedelta
        from secrets import choice

        # 10 chars from a 32-char alphabet (no 0/1/I/O for readability)
        # → ~50 bits, plenty for short-lived single-use tokens.
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "".join(choice(alphabet) for _ in range(10))

        now = utc_now()
        expires = now + timedelta(minutes=ttl_minutes)
        link_code = LinkCode(
            code=code,
            user_id=user_id,
            transport=transport,
            created_at=now,
            expires_at=expires,
            consumed_at=None,
        )
        with self._get_conn() as conn:
            # Clear any previous unconsumed codes for this user+transport.
            conn.execute(
                """
                DELETE FROM link_codes
                WHERE user_id = ? AND transport = ? AND consumed_at IS NULL
                """,
                (user_id, transport),
            )
            conn.execute(
                """
                INSERT INTO link_codes
                    (code, user_id, transport, created_at, expires_at, consumed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    link_code.code,
                    link_code.user_id,
                    link_code.transport,
                    link_code.created_at.isoformat(),
                    link_code.expires_at.isoformat(),
                    None,
                ),
            )
        return link_code

    def get_link_code(self, code: str) -> LinkCode | None:
        """Look up a link code (consumed or not). Used by tests + admins."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM link_codes WHERE code = ?",
                (code.upper(),),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_link_code(row)

    def consume_link_code(
        self,
        *,
        code: str,
        transport: str,
        chat_id: int,
    ) -> User:
        """Atomically redeem a link code: bind ``chat_id`` to the code's user.

        On success, the user's ``telegram_user_id`` (or ``discord_user_id``)
        is updated and the code's ``consumed_at`` is stamped. Returns the
        updated User.

        Raises ``LinkCodeError`` (subclass of ValueError) with a stable
        ``reason`` attribute the bot can branch on:

        - ``"unknown"`` — code doesn't exist
        - ``"expired"`` — code is past its TTL
        - ``"consumed"`` — code was already used
        - ``"transport_mismatch"`` — user generated a Telegram code but
          tried to redeem it from Discord (or vice versa)
        - ``"chat_taken"`` — another Gluon user already has this chat ID
          bound. We refuse to silently take it over.
        """
        from gluon.auth import LinkCodeError  # local import to avoid cycle

        norm_code = code.upper().strip()
        now = utc_now()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM link_codes WHERE code = ?",
                (norm_code,),
            ).fetchone()
            if row is None:
                raise LinkCodeError("unknown")
            link = self._row_to_link_code(row)
            if link.transport != transport:
                raise LinkCodeError("transport_mismatch")
            if link.consumed_at is not None:
                raise LinkCodeError("consumed")
            if link.expires_at < now:
                raise LinkCodeError("expired")

            # Refuse if the chat ID is already bound to a different user —
            # we never silently take over another account's chat binding.
            existing_owner = None
            if transport == "telegram":
                existing_owner = self.get_user_by_telegram_id(chat_id)
            elif transport == "discord":
                existing_owner = self.get_user_by_discord_id(chat_id)
            if existing_owner is not None and existing_owner.id != link.user_id:
                raise LinkCodeError("chat_taken")

            # Bind the chat ID and stamp the code consumed in one transaction.
            target = self.get_user(link.user_id)
            if target is None:
                # Code refers to a deleted user — treat as unknown.
                raise LinkCodeError("unknown")
            if transport == "telegram":
                target.telegram_user_id = chat_id
            elif transport == "discord":
                target.discord_user_id = chat_id
            else:
                raise LinkCodeError("unknown")
            target.updated_at = now
            conn.execute(
                """
                UPDATE users
                SET telegram_user_id = ?, discord_user_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    target.telegram_user_id,
                    target.discord_user_id,
                    now.isoformat(),
                    target.id,
                ),
            )
            conn.execute(
                "UPDATE link_codes SET consumed_at = ? WHERE code = ?",
                (now.isoformat(), norm_code),
            )
        return target

    def unlink_chat(self, *, user_id: str, transport: str) -> User | None:
        """Clear the user's binding for ``transport``. Returns the updated
        User, or ``None`` if no such user exists. No-op if already unbound.
        """
        user = self.get_user(user_id)
        if user is None:
            return None
        if transport == "telegram":
            user.telegram_user_id = None
        elif transport == "discord":
            user.discord_user_id = None
        else:
            return user  # unknown transport → no-op
        user.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE users
                SET telegram_user_id = ?, discord_user_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    user.telegram_user_id,
                    user.discord_user_id,
                    user.updated_at.isoformat(),
                    user.id,
                ),
            )
        return user

    def delete_expired_link_codes(self) -> int:
        """Sweep codes whose TTL has passed AND haven't been consumed.

        Consumed codes are kept as an audit trail — they're cheap (one row
        per successful link) and let an admin investigate "who linked what,
        when".
        """
        now = utc_now().isoformat()
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM link_codes WHERE consumed_at IS NULL AND expires_at < ?",
                (now,),
            )
            return cur.rowcount

    def _row_to_link_code(self, row: sqlite3.Row) -> LinkCode:
        return LinkCode(
            code=row["code"],
            user_id=row["user_id"],
            transport=row["transport"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            expires_at=_parse_datetime(row["expires_at"]),  # type: ignore[arg-type]
            consumed_at=_parse_datetime(row["consumed_at"]) if row["consumed_at"] else None,
        )

    # ========== Task CRUD (Theme B Phase 3) ==========

    def create_task(
        self,
        project_id: str,
        title: str,
        *,
        description: str | None = None,
        priority: int = 5,
        assigned_agent_id: str | None = None,
        created_by: str = "cli",
        created_by_user_id: str | None = None,
        assigned_files: list[str] | None = None,
        parent_task_id: str | None = None,
    ) -> OrchestratorTask:
        """Create a new orchestrator task.

        ``created_by_user_id`` (D5 Phase 2): attribution to a logged-in
        Gluon user. Pass ``None`` for single-user / SYSTEM_USER context —
        the row will have NULL in that column and audit views will show
        ``system``.
        """
        status = TaskStatus.ASSIGNED if assigned_agent_id else TaskStatus.BACKLOG
        task = OrchestratorTask(
            project_id=project_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            assigned_agent_id=assigned_agent_id,
            created_by=created_by,
            created_by_user_id=created_by_user_id,
            assigned_files=assigned_files or [],
            parent_task_id=parent_task_id,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO orchestrator_tasks
                (id, project_id, title, description, status, priority,
                 assigned_agent_id, created_by, created_by_user_id,
                 assigned_files, parent_task_id,
                 execution_locked_at, execution_run_id,
                 created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.project_id,
                    task.title,
                    task.description,
                    task.status.value,
                    task.priority,
                    task.assigned_agent_id,
                    task.created_by,
                    task.created_by_user_id,
                    json.dumps(task.assigned_files) if task.assigned_files else None,
                    task.parent_task_id,
                    None,
                    None,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    None,
                ),
            )
        return task

    def get_task(self, task_id: str) -> OrchestratorTask | None:
        """Get a task by ID. Also accepts 8-char prefixes when unique."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM orchestrator_tasks WHERE id = ?", (task_id,)).fetchone()
            if row:
                return self._row_to_task(row)
            # Try prefix match if ID didn't resolve exactly
            rows = conn.execute(
                "SELECT * FROM orchestrator_tasks WHERE id LIKE ? LIMIT 2",
                (task_id + "%",),
            ).fetchall()
            if len(rows) == 1:
                return self._row_to_task(rows[0])
        return None

    def list_tasks(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        status: str | TaskStatus | None = None,
        limit: int = 100,
    ) -> list[OrchestratorTask]:
        """List tasks with optional filters. Ordered by priority DESC, created_at ASC."""
        query = "SELECT * FROM orchestrator_tasks"
        params: list[Any] = []
        conditions: list[str] = []
        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)
        if agent_id is not None:
            conditions.append("assigned_agent_id = ?")
            params.append(agent_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value if isinstance(status, TaskStatus) else status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY priority DESC, created_at ASC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_task(row) for row in rows]

    def update_task(self, task: OrchestratorTask) -> None:
        """Persist modifications to a task. Does NOT modify execution lock fields —
        use checkout_task / release_task for those.
        """
        task.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE orchestrator_tasks SET
                    title = ?, description = ?, status = ?, priority = ?,
                    assigned_agent_id = ?, assigned_files = ?, parent_task_id = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    task.title,
                    task.description,
                    task.status.value,
                    task.priority,
                    task.assigned_agent_id,
                    json.dumps(task.assigned_files) if task.assigned_files else None,
                    task.parent_task_id,
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.id,
                ),
            )

    def checkout_task(
        self,
        task_id: str,
        agent_id: str | None,
        run_id: str,
    ) -> OrchestratorTask:
        """Atomically lock a task for execution.

        Uses a transaction + explicit state check to prevent two concurrent
        checkouts from claiming the same task. If the task is already locked
        and the lock is younger than TASK_LOCK_TTL_SECS, raises TaskLockedError.
        Stale locks (older than TTL) are overridden silently.

        Side effects:
          - Sets execution_locked_at = now
          - Sets execution_run_id = run_id
          - Sets assigned_agent_id = agent_id (if provided)
          - Sets status = IN_PROGRESS
          - Bumps updated_at
        """
        from gluon.core import TaskLockedError, TaskNotFoundError

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # Acquire write lock for the transaction
            try:
                row = conn.execute("SELECT * FROM orchestrator_tasks WHERE id = ?", (task_id,)).fetchone()
                if not row:
                    raise TaskNotFoundError(f"Task not found: {task_id}")

                existing_lock_at = _parse_datetime(row["execution_locked_at"])
                if existing_lock_at is not None:
                    age = (utc_now() - existing_lock_at).total_seconds()
                    if age < TASK_LOCK_TTL_SECS:
                        raise TaskLockedError(
                            task_id=row["id"],
                            locked_by_run_id=row["execution_run_id"],
                            age_seconds=age,
                        )
                    # Lock is stale — we're allowed to take it

                now_iso = utc_now().isoformat()
                conn.execute(
                    """
                    UPDATE orchestrator_tasks
                    SET execution_locked_at = ?,
                        execution_run_id = ?,
                        assigned_agent_id = COALESCE(?, assigned_agent_id),
                        status = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        now_iso,
                        run_id,
                        agent_id,
                        TaskStatus.IN_PROGRESS.value,
                        now_iso,
                        row["id"],
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        result = self.get_task(task_id)
        if result is None:
            # Should never happen — we just verified existence
            raise TaskNotFoundError(f"Task vanished after checkout: {task_id}")
        return result

    def release_task(self, task_id: str, new_status: str | TaskStatus) -> OrchestratorTask:
        """Release a task's execution lock and update its status.

        Sets execution_locked_at/execution_run_id to NULL. If new_status is DONE,
        stamps completed_at.
        """
        from gluon.core import TaskNotFoundError

        status_value = new_status.value if isinstance(new_status, TaskStatus) else new_status
        completed_at = utc_now().isoformat() if status_value == TaskStatus.DONE.value else None

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE orchestrator_tasks SET
                    status = ?,
                    execution_locked_at = NULL,
                    execution_run_id = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status_value, completed_at, utc_now().isoformat(), task_id),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"Task not found: {task_id}")

        result = self.get_task(task_id)
        if result is None:
            raise TaskNotFoundError(f"Task vanished after release: {task_id}")
        return result

    def get_agent_inbox(self, agent_id: str, limit: int = 20) -> list[OrchestratorTask]:
        """Return tasks assigned to this agent, ordered for inbox display.

        Only returns ASSIGNED and IN_PROGRESS tasks — not BACKLOG (unclaimed),
        REVIEW (waiting on review), DONE, or CANCELLED.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM orchestrator_tasks
                WHERE assigned_agent_id = ?
                  AND status IN (?, ?)
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (
                    agent_id,
                    TaskStatus.ASSIGNED.value,
                    TaskStatus.IN_PROGRESS.value,
                    limit,
                ),
            ).fetchall()
            return [self._row_to_task(row) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and all its comments. Returns True if deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM orchestrator_tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0

    def _row_to_task(self, row: sqlite3.Row) -> OrchestratorTask:
        """Convert database row to OrchestratorTask model."""
        assigned_files = []
        try:
            if row["assigned_files"]:
                assigned_files = json.loads(row["assigned_files"])
        except (json.JSONDecodeError, TypeError):
            pass

        # `created_by_user_id` is a D5 Phase 2 column — guard the lookup so
        # tests running against schemas built from older snapshots don't KeyError.
        try:
            created_by_user_id = row["created_by_user_id"]
        except (KeyError, IndexError):
            created_by_user_id = None

        return OrchestratorTask(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"],
            status=TaskStatus(row["status"]),
            priority=row["priority"] or 5,
            assigned_agent_id=row["assigned_agent_id"],
            created_by=row["created_by"] or "cli",
            created_by_user_id=created_by_user_id,
            assigned_files=assigned_files,
            parent_task_id=row["parent_task_id"],
            execution_locked_at=_parse_datetime(row["execution_locked_at"]),
            execution_run_id=row["execution_run_id"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
            completed_at=_parse_datetime(row["completed_at"]),
        )

    # ========== Task Comments CRUD ==========

    def add_task_comment(
        self,
        task_id: str,
        content: str,
        *,
        author_agent_id: str | None = None,
        author_label: str | None = None,
    ) -> TaskComment:
        """Append a comment to a task."""
        comment = TaskComment(
            task_id=task_id,
            author_agent_id=author_agent_id,
            author_label=author_label,
            content=content,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO task_comments
                (id, task_id, author_agent_id, author_label, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    comment.id,
                    comment.task_id,
                    comment.author_agent_id,
                    comment.author_label,
                    comment.content,
                    comment.created_at.isoformat(),
                ),
            )
        return comment

    def list_task_comments(self, task_id: str) -> list[TaskComment]:
        """List all comments on a task, oldest first."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            return [self._row_to_task_comment(row) for row in rows]

    def _row_to_task_comment(self, row: sqlite3.Row) -> TaskComment:
        """Convert database row to TaskComment model."""
        return TaskComment(
            id=row["id"],
            task_id=row["task_id"],
            author_agent_id=row["author_agent_id"],
            author_label=row["author_label"],
            content=row["content"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
        )

    # ========== AgentSchedule CRUD (Theme B Phase 2) ==========

    def create_schedule(
        self,
        agent_id: str,
        prompt_template: str,
        schedule_cron: str,
        *,
        project_id: str | None = None,
        coalesce_ttl_seconds: int = 300,
        task_profile: str = "quick",
        description: str | None = None,
        next_fire_at: datetime | None = None,
    ) -> AgentSchedule:
        """Create a new schedule for an agent."""
        schedule = AgentSchedule(
            agent_id=agent_id,
            project_id=project_id,
            prompt_template=prompt_template,
            schedule_cron=schedule_cron,
            coalesce_ttl_seconds=coalesce_ttl_seconds,
            task_profile=task_profile,
            description=description,
            next_fire_at=next_fire_at,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_schedules
                (id, agent_id, project_id, prompt_template, schedule_cron,
                 is_enabled, coalesce_ttl_seconds, task_profile, consecutive_failures,
                 last_fired_at, next_fire_at, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.id,
                    schedule.agent_id,
                    schedule.project_id,
                    schedule.prompt_template,
                    schedule.schedule_cron,
                    1 if schedule.is_enabled else 0,
                    schedule.coalesce_ttl_seconds,
                    schedule.task_profile,
                    schedule.consecutive_failures,
                    schedule.last_fired_at.isoformat() if schedule.last_fired_at else None,
                    schedule.next_fire_at.isoformat() if schedule.next_fire_at else None,
                    schedule.description,
                    schedule.created_at.isoformat(),
                    schedule.updated_at.isoformat(),
                ),
            )
        return schedule

    def get_schedule(self, schedule_id: str) -> AgentSchedule | None:
        """Get a schedule by ID. Supports 8-char prefix when unique."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM agent_schedules WHERE id = ?", (schedule_id,)).fetchone()
            if row:
                return self._row_to_schedule(row)
            rows = conn.execute(
                "SELECT * FROM agent_schedules WHERE id LIKE ? LIMIT 2",
                (schedule_id + "%",),
            ).fetchall()
            if len(rows) == 1:
                return self._row_to_schedule(rows[0])
        return None

    def list_schedules(
        self,
        *,
        agent_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[AgentSchedule]:
        """List schedules with optional filters."""
        query = "SELECT * FROM agent_schedules"
        params: list[Any] = []
        conditions: list[str] = []
        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if enabled_only:
            conditions.append("is_enabled = 1")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at ASC"

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_schedule(row) for row in rows]

    def list_due_schedules(self, now: datetime | None = None) -> list[AgentSchedule]:
        """List enabled schedules whose next_fire_at is <= now (or null).

        A schedule with null next_fire_at has never been fired yet; it should
        be considered due so the scheduler can compute its first fire time.
        """
        now = now or utc_now()
        now_iso = now.isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_schedules
                WHERE is_enabled = 1
                  AND (next_fire_at IS NULL OR next_fire_at <= ?)
                ORDER BY next_fire_at ASC NULLS FIRST
                """,
                (now_iso,),
            ).fetchall()
            return [self._row_to_schedule(row) for row in rows]

    def update_schedule(self, schedule: AgentSchedule) -> None:
        """Persist modifications to a schedule."""
        schedule.updated_at = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE agent_schedules SET
                    project_id = ?, prompt_template = ?, schedule_cron = ?,
                    is_enabled = ?, coalesce_ttl_seconds = ?, task_profile = ?,
                    consecutive_failures = ?, last_fired_at = ?, next_fire_at = ?,
                    description = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    schedule.project_id,
                    schedule.prompt_template,
                    schedule.schedule_cron,
                    1 if schedule.is_enabled else 0,
                    schedule.coalesce_ttl_seconds,
                    schedule.task_profile,
                    schedule.consecutive_failures,
                    schedule.last_fired_at.isoformat() if schedule.last_fired_at else None,
                    schedule.next_fire_at.isoformat() if schedule.next_fire_at else None,
                    schedule.description,
                    schedule.updated_at.isoformat(),
                    schedule.id,
                ),
            )

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule (cascades to its heartbeat_runs). Returns True if deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM agent_schedules WHERE id = ?", (schedule_id,))
            return cursor.rowcount > 0

    def _row_to_schedule(self, row: sqlite3.Row) -> AgentSchedule:
        """Convert database row to AgentSchedule model."""
        return AgentSchedule(
            id=row["id"],
            agent_id=row["agent_id"],
            project_id=row["project_id"],
            prompt_template=row["prompt_template"],
            schedule_cron=row["schedule_cron"],
            is_enabled=bool(row["is_enabled"]),
            coalesce_ttl_seconds=row["coalesce_ttl_seconds"] or 300,
            task_profile=row["task_profile"] or "quick",
            consecutive_failures=row["consecutive_failures"] or 0,
            last_fired_at=_parse_datetime(row["last_fired_at"]),
            next_fire_at=_parse_datetime(row["next_fire_at"]),
            description=row["description"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
        )

    # ========== HeartbeatRun CRUD (Theme B Phase 2) ==========

    def record_heartbeat(self, heartbeat: HeartbeatRun) -> HeartbeatRun:
        """Insert a new heartbeat run record."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO heartbeat_runs
                (id, schedule_id, agent_id, execution_run_id, fired_at, status,
                 result_summary, error_message, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    heartbeat.id,
                    heartbeat.schedule_id,
                    heartbeat.agent_id,
                    heartbeat.execution_run_id,
                    heartbeat.fired_at.isoformat(),
                    heartbeat.status.value,
                    heartbeat.result_summary,
                    heartbeat.error_message,
                    heartbeat.completed_at.isoformat() if heartbeat.completed_at else None,
                ),
            )
        return heartbeat

    def update_heartbeat(self, heartbeat: HeartbeatRun) -> None:
        """Update an existing heartbeat record."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE heartbeat_runs SET
                    execution_run_id = ?, status = ?,
                    result_summary = ?, error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    heartbeat.execution_run_id,
                    heartbeat.status.value,
                    heartbeat.result_summary,
                    heartbeat.error_message,
                    heartbeat.completed_at.isoformat() if heartbeat.completed_at else None,
                    heartbeat.id,
                ),
            )

    def list_heartbeats(
        self,
        *,
        schedule_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[HeartbeatRun]:
        """List heartbeat runs, newest first."""
        query = "SELECT * FROM heartbeat_runs"
        params: list[Any] = []
        conditions: list[str] = []
        if schedule_id is not None:
            conditions.append("schedule_id = ?")
            params.append(schedule_id)
        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY fired_at DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_heartbeat(row) for row in rows]

    def get_last_active_heartbeat(self, schedule_id: str, within_seconds: int) -> HeartbeatRun | None:
        """Return the most recent RUNNING or PENDING heartbeat within the window.

        Used by the coalescer — if a heartbeat is still live from a recent
        firing, a new fire is suppressed.
        """
        from datetime import timedelta

        cutoff = utc_now() - timedelta(seconds=within_seconds)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM heartbeat_runs
                WHERE schedule_id = ?
                  AND status IN (?, ?)
                  AND fired_at >= ?
                ORDER BY fired_at DESC
                LIMIT 1
                """,
                (
                    schedule_id,
                    HeartbeatStatus.PENDING.value,
                    HeartbeatStatus.RUNNING.value,
                    cutoff.isoformat(),
                ),
            ).fetchone()
            return self._row_to_heartbeat(row) if row else None

    def _row_to_heartbeat(self, row: sqlite3.Row) -> HeartbeatRun:
        """Convert database row to HeartbeatRun model."""
        return HeartbeatRun(
            id=row["id"],
            schedule_id=row["schedule_id"],
            agent_id=row["agent_id"],
            execution_run_id=row["execution_run_id"],
            fired_at=_parse_datetime(row["fired_at"]),  # type: ignore[arg-type]
            status=HeartbeatStatus(row["status"]),
            result_summary=row["result_summary"],
            error_message=row["error_message"],
            completed_at=_parse_datetime(row["completed_at"]),
        )

    # ========== PendingApproval CRUD (Theme D1) ==========

    def create_approval(
        self,
        run_id: str,
        tool_name: str,
        classification_reason: str,
        *,
        tool_input: dict[str, Any] | None = None,
        tool_use_id: str | None = None,
        timeout_at: datetime | None = None,
    ) -> PendingApproval:
        """Create a pending approval record for a risky tool call."""
        approval = PendingApproval(
            run_id=run_id,
            tool_name=tool_name,
            tool_input=tool_input or {},
            tool_use_id=tool_use_id,
            classification_reason=classification_reason,
            timeout_at=timeout_at,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO pending_approvals
                (id, run_id, tool_name, tool_input, tool_use_id, classification_reason,
                 status, decision_reason, decided_by, created_at, decided_at, timeout_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.run_id,
                    approval.tool_name,
                    json.dumps(approval.tool_input) if approval.tool_input else None,
                    approval.tool_use_id,
                    approval.classification_reason,
                    approval.status.value,
                    approval.decision_reason,
                    approval.decided_by,
                    approval.created_at.isoformat(),
                    approval.decided_at.isoformat() if approval.decided_at else None,
                    approval.timeout_at.isoformat() if approval.timeout_at else None,
                ),
            )
        return approval

    def get_approval(self, approval_id: str) -> PendingApproval | None:
        """Get an approval by ID. Supports 8-char prefix when unique."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
            if row:
                return self._row_to_approval(row)
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE id LIKE ? LIMIT 2",
                (approval_id + "%",),
            ).fetchall()
            if len(rows) == 1:
                return self._row_to_approval(rows[0])
        return None

    def list_approvals(
        self,
        *,
        run_id: str | None = None,
        status: str | ApprovalStatus | None = None,
        limit: int = 100,
    ) -> list[PendingApproval]:
        """List approvals with optional filters, newest first."""
        query = "SELECT * FROM pending_approvals"
        params: list[Any] = []
        conditions: list[str] = []
        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value if isinstance(status, ApprovalStatus) else status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_approval(row) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        decided_by: str,
        decision_reason: str | None = None,
        decided_by_user_id: str | None = None,
    ) -> PendingApproval | None:
        """Record a grant/deny decision on a PENDING approval.

        Only mutates approvals in PENDING status — idempotent if already decided.
        Returns the updated approval, or None if not found.

        ``decided_by`` (legacy) keeps its existing semantics — a transport/source
        tag like ``"web"``, ``"telegram:12345"``, ``"system:timeout"``.
        ``decided_by_user_id`` (D5 Phase 2) is the FK to the logged-in Gluon user
        who made the decision, when known. The two are complementary — both get
        populated for post-D5 decisions so audit trails can render the human name
        while still telling you which surface the decision came from.
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_approvals SET
                    status = ?, decided_by = ?, decided_by_user_id = ?,
                    decision_reason = ?, decided_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    decided_by,
                    decided_by_user_id,
                    decision_reason,
                    utc_now().isoformat(),
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            if cursor.rowcount == 0:
                # Either doesn't exist or already decided — return current state
                return self.get_approval(approval_id)
        return self.get_approval(approval_id)

    def expire_stale_approvals(self, now: datetime | None = None) -> int:
        """Mark all PENDING approvals past their timeout_at as EXPIRED. Returns count."""
        now = now or utc_now()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_approvals
                SET status = ?, decided_at = ?, decided_by = 'system:timeout',
                    decision_reason = 'Approval request timed out'
                WHERE status = ? AND timeout_at IS NOT NULL AND timeout_at <= ?
                """,
                (
                    ApprovalStatus.EXPIRED.value,
                    now.isoformat(),
                    ApprovalStatus.PENDING.value,
                    now.isoformat(),
                ),
            )
            return cursor.rowcount

    def _row_to_approval(self, row: sqlite3.Row) -> PendingApproval:
        """Convert database row to PendingApproval model."""
        try:
            tool_input = json.loads(row["tool_input"]) if row["tool_input"] else {}
        except (json.JSONDecodeError, TypeError):
            tool_input = {}
        keys = row.keys()
        return PendingApproval(
            id=row["id"],
            run_id=row["run_id"],
            tool_name=row["tool_name"],
            tool_input=tool_input,
            tool_use_id=row["tool_use_id"],
            classification_reason=row["classification_reason"],
            status=ApprovalStatus(row["status"]),
            decision_reason=row["decision_reason"],
            decided_by=row["decided_by"],
            decided_by_user_id=row["decided_by_user_id"] if "decided_by_user_id" in keys else None,
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            decided_at=_parse_datetime(row["decided_at"]),
            timeout_at=_parse_datetime(row["timeout_at"]),
            notified_at=_parse_datetime(row["notified_at"]) if "notified_at" in keys else None,
        )

    def list_pending_undelivered_approvals(self, limit: int = 50) -> list[PendingApproval]:
        """Return PENDING approvals that have not yet been notified to an async transport.

        Used by the ApprovalWatcher to find approvals needing a Telegram/Discord
        notification. Once the watcher posts an approval, it calls
        `mark_approval_notified` so subsequent polls skip it.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_approvals
                WHERE status = ? AND notified_at IS NULL
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (ApprovalStatus.PENDING.value, limit),
            ).fetchall()
            return [self._row_to_approval(row) for row in rows]

    def mark_approval_notified(self, approval_id: str) -> bool:
        """Record that an approval has been posted to an async transport.

        Atomic — uses WHERE notified_at IS NULL so two watchers don't
        double-notify. Returns True if this caller won the race.
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_approvals
                SET notified_at = ?
                WHERE id = ? AND notified_at IS NULL
                """,
                (utc_now().isoformat(), approval_id),
            )
            return cursor.rowcount > 0

    # ========== Utility Methods ==========

    def get_active_sessions(self) -> list[Session]:
        """Get all active or paused sessions."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE status IN (?, ?)
                ORDER BY updated_at DESC
                """,
                (SessionStatus.ACTIVE.value, SessionStatus.PAUSED.value),
            ).fetchall()
            return [self._row_to_session(row) for row in rows]

    def get_session_with_project(self, session_id: str) -> tuple[Session, Project] | None:
        """Get session and its associated project."""
        session = self.get_session(session_id)
        if session:
            project = self.get_project(session.project_id)
            if project:
                return session, project
        return None

    # ========== Execution Run CRUD ==========

    def create_run(
        self,
        project_id: str,
        prompt: str,
        initiator: str | None = None,
        session_id: str | None = None,
        use_worktree: bool = False,
        model: str | None = None,
        ralph_enabled: bool = False,
        max_loops: int = 50,
        max_calls_per_hour: int = 100,
        max_cost_usd: float | None = None,
        agent_id: str | None = None,
        approval_policy: ApprovalPolicy = ApprovalPolicy.PERMISSIVE,
        max_tool_calls: int | None = None,
        max_duration_minutes: int | None = None,
        user_id: str | None = None,
    ) -> ExecutionRun:
        """Create a new execution run.

        ``user_id`` (D5 Phase 2): attribution to a logged-in Gluon user.
        Pass ``None`` for single-user / SYSTEM_USER context — the row will
        have NULL in that column and audit views will show ``system``.
        """
        run = ExecutionRun(
            project_id=project_id,
            prompt=prompt,
            original_prompt=prompt,  # Preserve original prompt for auto-resume
            initiator=initiator,
            session_id=session_id,
            use_worktree=use_worktree,
            model=model,
            ralph_enabled=ralph_enabled,
            max_loops=max_loops,
            max_calls_per_hour=max_calls_per_hour,
            max_cost_usd=max_cost_usd,
            agent_id=agent_id,
            approval_policy=approval_policy,
            max_tool_calls=max_tool_calls,
            max_duration_minutes=max_duration_minutes,
            user_id=user_id,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO execution_runs
                (id, session_id, project_id, pid, status, prompt, original_prompt, initiator, created_at,
                 started_at, completed_at, exit_code, log_path, error_message, model,
                 ralph_enabled, max_loops, max_calls_per_hour, max_cost_usd, agent_id, approval_policy,
                 max_tool_calls, max_duration_minutes, tool_call_count, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.session_id,
                    run.project_id,
                    run.pid,
                    run.status.value,
                    run.prompt,
                    run.original_prompt,
                    run.initiator,
                    run.created_at.isoformat(),
                    run.started_at.isoformat() if run.started_at else None,
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.exit_code,
                    str(run.log_path) if run.log_path else None,
                    run.error_message,
                    run.model,
                    1 if run.ralph_enabled else 0,
                    run.max_loops,
                    run.max_calls_per_hour,
                    run.max_cost_usd,
                    run.agent_id,
                    run.approval_policy.value,
                    run.max_tool_calls,
                    run.max_duration_minutes,
                    run.tool_call_count,
                    run.user_id,
                ),
            )
        return run

    def get_run(self, run_id: str) -> ExecutionRun | None:
        """Get execution run by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM execution_runs WHERE id = ?", (run_id,)).fetchone()
            if row:
                return self._row_to_run(row)
        return None

    def get_run_by_short_id(self, short_id: str) -> ExecutionRun | None:
        """Get execution run by short ID prefix (at least 4 chars)."""
        if len(short_id) < 4:
            return None
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM execution_runs WHERE id LIKE ? LIMIT 1",
                (f"{short_id}%",),
            ).fetchone()
            if row:
                return self._row_to_run(row)
        return None

    def list_runs(
        self,
        project_id: str | None = None,
        statuses: list[RunStatus] | None = None,
        initiator: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[ExecutionRun]:
        """List execution runs with optional filters."""
        with self._get_conn() as conn:
            query = "SELECT * FROM execution_runs WHERE 1=1"
            params: list[str | int] = []

            # Exclude archived by default
            if not include_archived:
                query += " AND (archived IS NULL OR archived = 0)"

            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)

            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                query += f" AND status IN ({placeholders})"
                params.extend(s.value for s in statuses)

            if initiator:
                query += " AND initiator = ?"
                params.append(initiator)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_run(row) for row in rows]

    def list_runs_by_claude_session(self, claude_session_id: str) -> list[ExecutionRun]:
        """List all runs that share the same Claude session (for session history)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_runs WHERE claude_session_id = ? ORDER BY created_at ASC",
                (claude_session_id,),
            ).fetchall()
            return [self._row_to_run(row) for row in rows]

    def list_active_runs(self) -> list[ExecutionRun]:
        """List all pending or running execution runs."""
        return self.list_runs(statuses=[RunStatus.PENDING, RunStatus.RUNNING])

    def update_run(self, run: ExecutionRun) -> None:
        """Update an existing execution run."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE execution_runs
                SET session_id = ?, claude_session_id = ?, pid = ?, status = ?, prompt = ?, original_prompt = ?,
                    started_at = ?, completed_at = ?, exit_code = ?, log_path = ?, error_message = ?,
                    thread_id = ?, cost_usd = ?, input_tokens = ?, output_tokens = ?, model_used = ?,
                    branch_name = ?, source_branch = ?, worktree_path = ?, use_worktree = ?,
                    git_commit_sha = ?, pr_number = ?, pr_url = ?, pr_status = ?, pr_mergeable = ?, ci_status = ?,
                    archived = ?, archived_at = ?, resume_count = ?, last_resumed_at = ?,
                    recovery_count = ?, last_recovery_at = ?, recovery_from_run_id = ?,
                    is_recovering = ?, recovery_item_count = ?,
                    last_comment_id = ?, last_check_sha = ?, auto_resume_enabled = ?, auto_resume_count = ?,
                    loop_count = ?, circuit_state = ?, consecutive_no_progress = ?,
                    consecutive_same_error = ?, last_progress_loop = ?, last_error_hash = ?,
                    half_open_iterations = ?, completion_signals = ?, test_only_loops = ?,
                    completion_confidence = ?, completion_reason = ?, calls_this_hour = ?,
                    hour_start = ?, supervision_config = ?,
                    supervision_auto_resume_count = ?, last_supervision_check_at = ?,
                    last_supervision_resume_at = ?, supervision_disabled_reason = ?,
                    queued_messages = ?, changes_snapshotted = ?, snapshot_at = ?,
                    metadata = ?, last_output_at = ?, chain_id = ?, step_id = ?,
                    agent_id = ?,
                    approval_policy = ?,
                    max_tool_calls = ?, max_duration_minutes = ?, tool_call_count = ?
                WHERE id = ?
                """,
                (
                    run.session_id,
                    run.claude_session_id,
                    run.pid,
                    run.status.value,
                    run.prompt,
                    run.original_prompt,
                    run.started_at.isoformat() if run.started_at else None,
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.exit_code,
                    str(run.log_path) if run.log_path else None,
                    run.error_message,
                    run.thread_id,
                    run.cost_usd,
                    run.input_tokens,
                    run.output_tokens,
                    run.model_used,
                    run.branch_name,
                    run.source_branch,
                    run.worktree_path,
                    1 if run.use_worktree else 0,
                    run.git_commit_sha,
                    run.pr_number,
                    run.pr_url,
                    run.pr_status,
                    run.pr_mergeable,
                    run.ci_status,
                    1 if run.archived else 0,
                    run.archived_at.isoformat() if run.archived_at else None,
                    run.resume_count,
                    run.last_resumed_at.isoformat() if run.last_resumed_at else None,
                    run.recovery_count,
                    run.last_recovery_at.isoformat() if run.last_recovery_at else None,
                    run.recovery_from_run_id,
                    1 if run.is_recovering else 0,
                    run.recovery_item_count,
                    run.last_comment_id,
                    run.last_check_sha,
                    1 if run.auto_resume_enabled else 0,
                    run.auto_resume_count,
                    # Ralph fields
                    run.loop_count,
                    run.circuit_state.value,
                    run.consecutive_no_progress,
                    run.consecutive_same_error,
                    run.last_progress_loop,
                    run.last_error_hash,
                    run.half_open_iterations,
                    run.completion_signals,
                    run.test_only_loops,
                    run.completion_confidence,
                    run.completion_reason,
                    run.calls_this_hour,
                    run.hour_start.isoformat() if run.hour_start else None,
                    # Supervision fields
                    json.dumps(run.supervision_config.model_dump()) if run.supervision_config else None,
                    run.supervision_auto_resume_count,
                    run.last_supervision_check_at.isoformat() if run.last_supervision_check_at else None,
                    run.last_supervision_resume_at.isoformat() if run.last_supervision_resume_at else None,
                    run.supervision_disabled_reason,
                    # Queued messages (JSON array)
                    json.dumps([m.model_dump(mode="json") for m in run.queued_messages])
                    if run.queued_messages
                    else None,
                    # Snapshot tracking
                    1 if run.changes_snapshotted else 0,
                    run.snapshot_at.isoformat() if run.snapshot_at else None,
                    # Task profile metadata
                    json.dumps(run.metadata) if run.metadata else None,
                    # Health monitoring + chain linking
                    run.last_output_at.isoformat() if run.last_output_at else None,
                    run.chain_id,
                    run.step_id,
                    # Agent linkage (Theme B Phase 1)
                    run.agent_id,
                    # Approval gates (Theme D1)
                    run.approval_policy.value,
                    # Hard caps (Theme D3)
                    run.max_tool_calls,
                    run.max_duration_minutes,
                    run.tool_call_count,
                    run.id,
                ),
            )

    def update_run_status(self, run_id: str, new_status: RunStatus) -> ExecutionRun | None:
        """Update run status (for manual transitions via drag-and-drop)."""
        run = self.get_run(run_id)
        if not run:
            return None
        run.status = new_status
        if new_status == RunStatus.CANCELLED:
            run.completed_at = utc_now()
        self.update_run(run)
        return run

    def delete_run(self, run_id: str) -> bool:
        """Delete an execution run."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM execution_runs WHERE id = ?", (run_id,))
            return cursor.rowcount > 0

    def archive_run(self, run_id: str, archived: bool = True) -> ExecutionRun | None:
        """Archive or unarchive an execution run."""
        run = self.get_run(run_id)
        if not run:
            return None

        archived_at = utc_now().isoformat() if archived else None

        with self._get_conn() as conn:
            conn.execute(
                "UPDATE execution_runs SET archived = ?, archived_at = ? WHERE id = ?",
                (1 if archived else 0, archived_at, run_id),
            )

        # Return updated run
        return self.get_run(run_id)

    def update_pr_status(self, run_id: str, pr_status: str) -> ExecutionRun | None:
        """Update the PR status for an execution run."""
        run = self.get_run(run_id)
        if not run:
            return None

        with self._get_conn() as conn:
            conn.execute(
                "UPDATE execution_runs SET pr_status = ? WHERE id = ?",
                (pr_status, run_id),
            )

        # Return updated run
        return self.get_run(run_id)

    def _row_to_run(self, row: sqlite3.Row) -> ExecutionRun:
        """Convert database row to ExecutionRun model."""
        keys = row.keys()
        return ExecutionRun(
            id=row["id"],
            session_id=row["session_id"],
            claude_session_id=row["claude_session_id"] if "claude_session_id" in keys else None,
            project_id=row["project_id"],
            agent_id=row["agent_id"] if "agent_id" in keys else None,
            pid=row["pid"],
            status=RunStatus(row["status"]),
            prompt=row["prompt"],
            original_prompt=row["original_prompt"] if "original_prompt" in keys else None,
            initiator=row["initiator"] if "initiator" in keys else None,
            user_id=row["user_id"] if "user_id" in keys else None,
            thread_id=row["thread_id"] if "thread_id" in keys else None,
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            started_at=_parse_datetime(row["started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
            exit_code=row["exit_code"],
            log_path=Path(row["log_path"]) if row["log_path"] else None,
            error_message=row["error_message"],
            # Cost tracking
            cost_usd=row["cost_usd"] if "cost_usd" in keys else None,
            input_tokens=row["input_tokens"] if "input_tokens" in keys else None,
            output_tokens=row["output_tokens"] if "output_tokens" in keys else None,
            model_used=row["model_used"] if "model_used" in keys else None,
            # Git/worktree tracking
            branch_name=row["branch_name"] if "branch_name" in keys else None,
            source_branch=row["source_branch"] if "source_branch" in keys else None,
            worktree_path=row["worktree_path"] if "worktree_path" in keys else None,
            use_worktree=bool(row["use_worktree"])
            if "use_worktree" in keys and row["use_worktree"] is not None
            else False,
            git_commit_sha=row["git_commit_sha"] if "git_commit_sha" in keys else None,
            pr_number=row["pr_number"] if "pr_number" in keys else None,
            pr_url=row["pr_url"] if "pr_url" in keys else None,
            pr_status=row["pr_status"] if "pr_status" in keys else None,
            pr_mergeable=row["pr_mergeable"] if "pr_mergeable" in keys else None,
            ci_status=row["ci_status"] if "ci_status" in keys else None,
            # Archive tracking
            archived=bool(row["archived"]) if "archived" in keys and row["archived"] is not None else False,
            archived_at=_parse_datetime(row["archived_at"]) if "archived_at" in keys else None,
            # Resume tracking
            resume_count=row["resume_count"] if "resume_count" in keys and row["resume_count"] is not None else 0,
            last_resumed_at=_parse_datetime(row["last_resumed_at"]) if "last_resumed_at" in keys else None,
            # Model selection
            model=row["model"] if "model" in keys else None,
            # Context overflow recovery tracking
            recovery_count=row["recovery_count"]
            if "recovery_count" in keys and row["recovery_count"] is not None
            else 0,
            last_recovery_at=_parse_datetime(row["last_recovery_at"]) if "last_recovery_at" in keys else None,
            recovery_from_run_id=row["recovery_from_run_id"] if "recovery_from_run_id" in keys else None,
            # Recovery progress UI
            is_recovering=bool(row["is_recovering"])
            if "is_recovering" in keys and row["is_recovering"] is not None
            else False,
            recovery_item_count=row["recovery_item_count"]
            if "recovery_item_count" in keys and row["recovery_item_count"] is not None
            else 0,
            # PR monitoring tracking
            last_comment_id=row["last_comment_id"] if "last_comment_id" in keys else None,
            last_check_sha=row["last_check_sha"] if "last_check_sha" in keys else None,
            auto_resume_enabled=bool(row["auto_resume_enabled"])
            if "auto_resume_enabled" in keys and row["auto_resume_enabled"] is not None
            else True,
            auto_resume_count=row["auto_resume_count"]
            if "auto_resume_count" in keys and row["auto_resume_count"] is not None
            else 0,
            # Ralph mode fields
            ralph_enabled=bool(row["ralph_enabled"])
            if "ralph_enabled" in keys and row["ralph_enabled"] is not None
            else False,
            loop_count=row["loop_count"] if "loop_count" in keys and row["loop_count"] is not None else 0,
            max_loops=row["max_loops"] if "max_loops" in keys and row["max_loops"] is not None else 50,
            circuit_state=CircuitState(row["circuit_state"])
            if "circuit_state" in keys and row["circuit_state"]
            else CircuitState.CLOSED,
            consecutive_no_progress=row["consecutive_no_progress"]
            if "consecutive_no_progress" in keys and row["consecutive_no_progress"] is not None
            else 0,
            consecutive_same_error=row["consecutive_same_error"]
            if "consecutive_same_error" in keys and row["consecutive_same_error"] is not None
            else 0,
            last_progress_loop=row["last_progress_loop"]
            if "last_progress_loop" in keys and row["last_progress_loop"] is not None
            else 0,
            last_error_hash=row["last_error_hash"] if "last_error_hash" in keys else None,
            half_open_iterations=row["half_open_iterations"]
            if "half_open_iterations" in keys and row["half_open_iterations"] is not None
            else 0,
            completion_signals=row["completion_signals"]
            if "completion_signals" in keys and row["completion_signals"] is not None
            else 0,
            test_only_loops=row["test_only_loops"]
            if "test_only_loops" in keys and row["test_only_loops"] is not None
            else 0,
            completion_confidence=row["completion_confidence"]
            if "completion_confidence" in keys and row["completion_confidence"] is not None
            else 0.0,
            completion_reason=row["completion_reason"] if "completion_reason" in keys else None,
            calls_this_hour=row["calls_this_hour"]
            if "calls_this_hour" in keys and row["calls_this_hour"] is not None
            else 0,
            hour_start=_parse_datetime(row["hour_start"]) if "hour_start" in keys else None,
            max_calls_per_hour=row["max_calls_per_hour"]
            if "max_calls_per_hour" in keys and row["max_calls_per_hour"] is not None
            else 100,
            max_cost_usd=row["max_cost_usd"] if "max_cost_usd" in keys else None,
            # Approval gates (Theme D1)
            approval_policy=(
                ApprovalPolicy(row["approval_policy"])
                if "approval_policy" in keys and row["approval_policy"]
                else ApprovalPolicy.PERMISSIVE
            ),
            # Hard caps (Theme D3)
            max_tool_calls=row["max_tool_calls"] if "max_tool_calls" in keys else None,
            max_duration_minutes=row["max_duration_minutes"] if "max_duration_minutes" in keys else None,
            tool_call_count=(
                row["tool_call_count"] if "tool_call_count" in keys and row["tool_call_count"] is not None else 0
            ),
            # Supervision fields
            supervision_config=SupervisionConfig(**json.loads(row["supervision_config"]))
            if "supervision_config" in keys and row["supervision_config"]
            else None,
            supervision_auto_resume_count=row["supervision_auto_resume_count"]
            if "supervision_auto_resume_count" in keys and row["supervision_auto_resume_count"] is not None
            else 0,
            last_supervision_check_at=_parse_datetime(row["last_supervision_check_at"])
            if "last_supervision_check_at" in keys
            else None,
            last_supervision_resume_at=_parse_datetime(row["last_supervision_resume_at"])
            if "last_supervision_resume_at" in keys
            else None,
            supervision_disabled_reason=row["supervision_disabled_reason"]
            if "supervision_disabled_reason" in keys
            else None,
            # Queued messages (JSON array)
            queued_messages=[QueuedMessage(**m) for m in json.loads(row["queued_messages"])]
            if "queued_messages" in keys and row["queued_messages"]
            else [],
            # Commit/file snapshot tracking
            changes_snapshotted=bool(row["changes_snapshotted"])
            if "changes_snapshotted" in keys and row["changes_snapshotted"] is not None
            else False,
            snapshot_at=_parse_datetime(row["snapshot_at"]) if "snapshot_at" in keys else None,
            # Task profile metadata
            metadata=json.loads(row["metadata"]) if "metadata" in keys and row["metadata"] else None,
            # Health monitoring
            last_output_at=_parse_datetime(row["last_output_at"]) if "last_output_at" in keys else None,
            # Task chain linking
            chain_id=row["chain_id"] if "chain_id" in keys else None,
            step_id=row["step_id"] if "step_id" in keys else None,
        )

    def get_run_by_thread_id(self, thread_id: str) -> ExecutionRun | None:
        """Get the most recent execution run for a thread ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM execution_runs WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if row:
                return self._row_to_run(row)
        return None

    def get_run_with_project(self, run_id: str) -> tuple[ExecutionRun, Project] | None:
        """Get run and its associated project."""
        run = self.get_run(run_id)
        if run:
            project = self.get_project(run.project_id)
            if project:
                return run, project
        return None

    # ========== Ralph Loop Iteration CRUD ==========

    def create_ralph_iteration(self, iteration: RalphLoopIteration) -> RalphLoopIteration:
        """Create a new ralph loop iteration record."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO ralph_loop_iterations (
                    id, run_id, loop_number, started_at, ended_at,
                    files_changed, has_errors, error_summary, output_length,
                    is_test_only, has_completion_signal, progress_detected,
                    confidence_score, claude_session_id, cost_usd, tokens_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iteration.id,
                    iteration.run_id,
                    iteration.loop_number,
                    iteration.started_at.isoformat(),
                    iteration.ended_at.isoformat() if iteration.ended_at else None,
                    iteration.files_changed,
                    1 if iteration.has_errors else 0,
                    iteration.error_summary,
                    iteration.output_length,
                    1 if iteration.is_test_only else 0,
                    1 if iteration.has_completion_signal else 0,
                    1 if iteration.progress_detected else 0,
                    iteration.confidence_score,
                    iteration.claude_session_id,
                    iteration.cost_usd,
                    iteration.tokens_used,
                ),
            )
        return iteration

    def update_ralph_iteration(self, iteration: RalphLoopIteration) -> RalphLoopIteration:
        """Update an existing ralph loop iteration."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE ralph_loop_iterations SET
                    ended_at = ?,
                    files_changed = ?,
                    has_errors = ?,
                    error_summary = ?,
                    output_length = ?,
                    is_test_only = ?,
                    has_completion_signal = ?,
                    progress_detected = ?,
                    confidence_score = ?,
                    claude_session_id = ?,
                    cost_usd = ?,
                    tokens_used = ?
                WHERE id = ?
                """,
                (
                    iteration.ended_at.isoformat() if iteration.ended_at else None,
                    iteration.files_changed,
                    1 if iteration.has_errors else 0,
                    iteration.error_summary,
                    iteration.output_length,
                    1 if iteration.is_test_only else 0,
                    1 if iteration.has_completion_signal else 0,
                    1 if iteration.progress_detected else 0,
                    iteration.confidence_score,
                    iteration.claude_session_id,
                    iteration.cost_usd,
                    iteration.tokens_used,
                    iteration.id,
                ),
            )
        return iteration

    def list_ralph_iterations(self, run_id: str, limit: int | None = None) -> list[RalphLoopIteration]:
        """List iterations for a ralph-enabled run."""
        with self._get_conn() as conn:
            query = "SELECT * FROM ralph_loop_iterations WHERE run_id = ? ORDER BY loop_number"
            params: list[str | int] = [run_id]
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_ralph_iteration(row) for row in rows]

    def get_ralph_iteration(self, iteration_id: str) -> RalphLoopIteration | None:
        """Get a specific ralph iteration by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ralph_loop_iterations WHERE id = ?",
                (iteration_id,),
            ).fetchone()
        return self._row_to_ralph_iteration(row) if row else None

    def get_latest_ralph_iteration(self, run_id: str) -> RalphLoopIteration | None:
        """Get the most recent iteration for a run."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ralph_loop_iterations WHERE run_id = ? ORDER BY loop_number DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return self._row_to_ralph_iteration(row) if row else None

    def get_ralph_total_cost(self, run_id: str) -> float:
        """Get the total cost summed from all iterations for a ralph run."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total FROM ralph_loop_iterations WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return float(row["total"]) if row else 0.0

    def _row_to_ralph_iteration(self, row: sqlite3.Row) -> RalphLoopIteration:
        """Convert database row to RalphLoopIteration model."""
        return RalphLoopIteration(
            id=row["id"],
            run_id=row["run_id"],
            loop_number=row["loop_number"],
            started_at=_parse_datetime(row["started_at"]),  # type: ignore[arg-type]
            ended_at=_parse_datetime(row["ended_at"]),
            files_changed=row["files_changed"] or 0,
            has_errors=bool(row["has_errors"]),
            error_summary=row["error_summary"],
            output_length=row["output_length"] or 0,
            is_test_only=bool(row["is_test_only"]),
            has_completion_signal=bool(row["has_completion_signal"]),
            progress_detected=bool(row["progress_detected"]),
            confidence_score=row["confidence_score"] or 0.0,
            claude_session_id=row["claude_session_id"],
            cost_usd=row["cost_usd"] or 0.0,
            tokens_used=row["tokens_used"] or 0,
        )

    # ========== Commit/File Snapshot CRUD ==========

    def has_commit_snapshots(self, run_id: str) -> bool:
        """Check if a run has commit snapshots."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM commit_snapshots WHERE run_id = ?", (run_id,)).fetchone()
            return row[0] > 0 if row else False

    def has_file_change_snapshots(self, run_id: str) -> bool:
        """Check if a run has file change snapshots."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM file_change_snapshots WHERE run_id = ?", (run_id,)).fetchone()
            return row[0] > 0 if row else False

    def get_commit_snapshots(self, run_id: str) -> list[CommitSnapshot]:
        """Get commit snapshots for a run, ordered by ordinal."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM commit_snapshots WHERE run_id = ? ORDER BY ordinal",
                (run_id,),
            ).fetchall()
        return [self._row_to_commit_snapshot(row) for row in rows]

    def get_file_change_snapshots(self, run_id: str) -> list[FileChangeSnapshot]:
        """Get file change snapshots for a run."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM file_change_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        return [self._row_to_file_change_snapshot(row) for row in rows]

    def get_commit_file_snapshots(self, commit_snapshot_id: str) -> list[CommitFileSnapshot]:
        """Get file snapshots for a specific commit."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM commit_file_snapshots WHERE commit_snapshot_id = ?",
                (commit_snapshot_id,),
            ).fetchall()
        return [self._row_to_commit_file_snapshot(row) for row in rows]

    def save_run_snapshots(
        self,
        run_id: str,
        commits: list[CommitSnapshot],
        files: list[FileChangeSnapshot],
        commit_files: list[CommitFileSnapshot],
    ) -> None:
        """Atomically save all snapshots for a run and mark it as snapshotted."""
        with self._get_conn() as conn:
            # Insert commits
            for commit in commits:
                conn.execute(
                    """
                    INSERT INTO commit_snapshots (
                        id, run_id, sha, message, full_message, author, author_email,
                        date, ordinal, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        commit.id,
                        commit.run_id,
                        commit.sha,
                        commit.message,
                        commit.full_message,
                        commit.author,
                        commit.author_email,
                        commit.date.isoformat(),
                        commit.ordinal,
                        commit.created_at.isoformat(),
                    ),
                )

            # Insert file changes
            for file_change in files:
                conn.execute(
                    """
                    INSERT INTO file_change_snapshots (
                        id, run_id, file_path, change_type, additions, deletions, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_change.id,
                        file_change.run_id,
                        file_change.file_path,
                        file_change.change_type,
                        file_change.additions,
                        file_change.deletions,
                        file_change.created_at.isoformat(),
                    ),
                )

            # Insert commit files
            for commit_file in commit_files:
                conn.execute(
                    """
                    INSERT INTO commit_file_snapshots (
                        id, commit_snapshot_id, file_path, change_type, additions, deletions
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        commit_file.id,
                        commit_file.commit_snapshot_id,
                        commit_file.file_path,
                        commit_file.change_type,
                        commit_file.additions,
                        commit_file.deletions,
                    ),
                )

            # Mark run as snapshotted
            now = utc_now()
            conn.execute(
                "UPDATE execution_runs SET changes_snapshotted = 1, snapshot_at = ? WHERE id = ?",
                (now.isoformat(), run_id),
            )

    def mark_run_snapshotted(self, run_id: str) -> None:
        """Mark a run as having snapshots captured."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE execution_runs SET changes_snapshotted = 1, snapshot_at = ? WHERE id = ?",
                (utc_now().isoformat(), run_id),
            )

    def delete_run_snapshots(self, run_id: str) -> int:
        """Delete all snapshots for a run. Returns count deleted."""
        with self._get_conn() as conn:
            # Commit file snapshots deleted via CASCADE
            result1 = conn.execute("DELETE FROM commit_snapshots WHERE run_id = ?", (run_id,))
            result2 = conn.execute("DELETE FROM file_change_snapshots WHERE run_id = ?", (run_id,))
            # Reset flag
            conn.execute(
                "UPDATE execution_runs SET changes_snapshotted = 0, snapshot_at = NULL WHERE id = ?",
                (run_id,),
            )
            return result1.rowcount + result2.rowcount

    def _row_to_commit_snapshot(self, row: sqlite3.Row) -> CommitSnapshot:
        """Convert database row to CommitSnapshot model."""
        return CommitSnapshot(
            id=row["id"],
            run_id=row["run_id"],
            sha=row["sha"],
            message=row["message"],
            full_message=row["full_message"],
            author=row["author"],
            author_email=row["author_email"],
            date=_parse_datetime(row["date"]),  # type: ignore[arg-type]
            ordinal=row["ordinal"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
        )

    def _row_to_file_change_snapshot(self, row: sqlite3.Row) -> FileChangeSnapshot:
        """Convert database row to FileChangeSnapshot model."""
        return FileChangeSnapshot(
            id=row["id"],
            run_id=row["run_id"],
            file_path=row["file_path"],
            change_type=row["change_type"],
            additions=row["additions"] or 0,
            deletions=row["deletions"] or 0,
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
        )

    def _row_to_commit_file_snapshot(self, row: sqlite3.Row) -> CommitFileSnapshot:
        """Convert database row to CommitFileSnapshot model."""
        return CommitFileSnapshot(
            id=row["id"],
            commit_snapshot_id=row["commit_snapshot_id"],
            file_path=row["file_path"],
            change_type=row["change_type"],
            additions=row["additions"] or 0,
            deletions=row["deletions"] or 0,
        )

    # ========== Supervision Decision CRUD ==========

    def create_supervision_decision(self, decision: "SupervisionDecision") -> "SupervisionDecision":
        """Create a new supervision decision record."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO supervision_decisions (
                    id, run_id, timestamp, decision, reason, trigger,
                    circuit_state, completion_confidence, calls_this_hour,
                    cost_usd, auto_resume_count, policy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.run_id,
                    decision.timestamp.isoformat(),
                    decision.decision,
                    decision.reason,
                    decision.trigger,
                    decision.circuit_state.value if decision.circuit_state else None,
                    decision.completion_confidence,
                    decision.calls_this_hour,
                    decision.cost_usd,
                    decision.auto_resume_count,
                    decision.policy.value if decision.policy else None,
                ),
            )
        return decision

    def list_supervision_decisions(self, run_id: str, limit: int | None = None) -> list["SupervisionDecision"]:
        """List supervision decisions for a run, most recent first."""
        with self._get_conn() as conn:
            query = "SELECT * FROM supervision_decisions WHERE run_id = ? ORDER BY timestamp DESC"
            params: list[str | int] = [run_id]
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_supervision_decision(row) for row in rows]

    def get_latest_supervision_decision(self, run_id: str) -> "SupervisionDecision | None":
        """Get the most recent supervision decision for a run."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM supervision_decisions WHERE run_id = ? ORDER BY timestamp DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return self._row_to_supervision_decision(row) if row else None

    def count_supervision_decisions(self, run_id: str, decision_type: str | None = None) -> int:
        """Count supervision decisions for a run, optionally filtered by type."""
        with self._get_conn() as conn:
            if decision_type:
                row = conn.execute(
                    "SELECT COUNT(*) FROM supervision_decisions WHERE run_id = ? AND decision = ?",
                    (run_id, decision_type),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM supervision_decisions WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        return row[0] if row else 0

    def _row_to_supervision_decision(self, row: sqlite3.Row) -> SupervisionDecision:
        """Convert database row to SupervisionDecision model."""
        return SupervisionDecision(
            id=row["id"],
            run_id=row["run_id"],
            timestamp=_parse_datetime(row["timestamp"]),  # type: ignore[arg-type]
            decision=row["decision"],
            reason=row["reason"],
            trigger=row["trigger"],
            circuit_state=CircuitState(row["circuit_state"]) if row["circuit_state"] else None,
            completion_confidence=row["completion_confidence"],
            calls_this_hour=row["calls_this_hour"],
            cost_usd=row["cost_usd"],
            auto_resume_count=row["auto_resume_count"],
            policy=SupervisionPolicy(row["policy"]) if row["policy"] else None,
        )

    # ========== Pending Questions CRUD ==========

    def create_pending_question(self, question: PendingQuestion) -> PendingQuestion:
        """Create a new pending question."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO pending_questions (
                    id, run_id, question_index, question_text, header, options,
                    multi_select, status, created_at, answered_at, expires_at,
                    selected_labels, answer_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question.id,
                    question.run_id,
                    question.question_index,
                    question.question_text,
                    question.header,
                    json.dumps(question.options),
                    1 if question.multi_select else 0,
                    question.status.value,
                    question.created_at.isoformat(),
                    question.answered_at.isoformat() if question.answered_at else None,
                    question.expires_at.isoformat() if question.expires_at else None,
                    json.dumps(question.selected_labels),
                    question.answer_source,
                ),
            )
        return question

    def get_pending_question(self, question_id: str) -> PendingQuestion | None:
        """Get a pending question by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_questions WHERE id = ?",
                (question_id,),
            ).fetchone()
        return self._row_to_pending_question(row) if row else None

    def list_pending_questions(self, run_id: str, status: QuestionStatus | None = None) -> list[PendingQuestion]:
        """List pending questions for a run, optionally filtered by status."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM pending_questions WHERE run_id = ? AND status = ? ORDER BY question_index",
                    (run_id, status.value),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pending_questions WHERE run_id = ? ORDER BY question_index",
                    (run_id,),
                ).fetchall()
        return [self._row_to_pending_question(row) for row in rows]

    def get_pending_questions_for_run(self, run_id: str) -> list[PendingQuestion]:
        """Get all pending (unanswered) questions for a run."""
        return self.list_pending_questions(run_id, status=QuestionStatus.PENDING)

    def update_pending_question(self, question: PendingQuestion) -> PendingQuestion:
        """Update a pending question (e.g., after answering)."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE pending_questions SET
                    status = ?,
                    answered_at = ?,
                    selected_labels = ?,
                    answer_source = ?
                WHERE id = ?
                """,
                (
                    question.status.value,
                    question.answered_at.isoformat() if question.answered_at else None,
                    json.dumps(question.selected_labels),
                    question.answer_source,
                    question.id,
                ),
            )
        return question

    def delete_pending_questions(self, run_id: str) -> int:
        """Delete all pending questions for a run. Returns count deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM pending_questions WHERE run_id = ?",
                (run_id,),
            )
        return cursor.rowcount

    def _row_to_pending_question(self, row: sqlite3.Row) -> PendingQuestion:
        """Convert database row to PendingQuestion model."""
        keys = row.keys()
        return PendingQuestion(
            id=row["id"],
            run_id=row["run_id"],
            question_index=row["question_index"],
            question_text=row["question_text"],
            header=row["header"],
            options=json.loads(row["options"]),
            multi_select=bool(row["multi_select"]),
            status=QuestionStatus(row["status"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            answered_at=_parse_datetime(row["answered_at"]),
            expires_at=_parse_datetime(row["expires_at"]),
            selected_labels=json.loads(row["selected_labels"]) if row["selected_labels"] else [],
            answer_source=row["answer_source"],
            notified_at=_parse_datetime(row["notified_at"]) if "notified_at" in keys else None,
        )

    def list_pending_undelivered_questions(self, limit: int = 50) -> list[PendingQuestion]:
        """Return PENDING questions that have not yet been notified to an async transport.

        Used by the QuestionWatcher to find questions needing a Telegram/Discord
        notification. Once posted, the watcher calls `mark_question_notified`
        so subsequent polls skip it. Mirrors `list_pending_undelivered_approvals`.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_questions
                WHERE status = ? AND notified_at IS NULL
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (QuestionStatus.PENDING.value, limit),
            ).fetchall()
            return [self._row_to_pending_question(row) for row in rows]

    def mark_question_notified(self, question_id: str) -> bool:
        """Record that a question has been posted to an async transport.

        Atomic — uses WHERE notified_at IS NULL so two watchers don't
        double-notify. Returns True if this caller won the race.
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_questions
                SET notified_at = ?
                WHERE id = ? AND notified_at IS NULL
                """,
                (utc_now().isoformat(), question_id),
            )
            return cursor.rowcount > 0

    # ========== Todo Snapshot CRUD ==========

    def save_todo_snapshot(self, snapshot: TodoSnapshot) -> TodoSnapshot:
        """Save a todo snapshot captured from a TodoWrite PostToolUse hook."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO todo_snapshots (
                    id, run_id, todos, todo_count, completed_count,
                    in_progress_count, pending_count, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.id,
                    snapshot.run_id,
                    json.dumps(snapshot.todos),
                    snapshot.todo_count,
                    snapshot.completed_count,
                    snapshot.in_progress_count,
                    snapshot.pending_count,
                    snapshot.captured_at.isoformat(),
                ),
            )
        return snapshot

    def get_latest_todo_snapshot(self, run_id: str) -> TodoSnapshot | None:
        """Get the most recent todo snapshot for a run."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM todo_snapshots WHERE run_id = ? ORDER BY captured_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_todo_snapshot(row)

    def list_todo_snapshots(self, run_id: str, limit: int = 50) -> list[TodoSnapshot]:
        """List todo snapshots for a run, newest first."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM todo_snapshots WHERE run_id = ? ORDER BY captured_at DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return [self._row_to_todo_snapshot(row) for row in rows]

    def _row_to_todo_snapshot(self, row: sqlite3.Row) -> TodoSnapshot:
        """Convert database row to TodoSnapshot model."""
        return TodoSnapshot(
            id=row["id"],
            run_id=row["run_id"],
            todos=json.loads(row["todos"]),
            todo_count=row["todo_count"],
            completed_count=row["completed_count"],
            in_progress_count=row["in_progress_count"],
            pending_count=row["pending_count"],
            captured_at=_parse_datetime(row["captured_at"]),  # type: ignore[arg-type]
        )

    # ========== Channel Mapping CRUD ==========

    def create_channel_mapping(
        self,
        transport: str,
        channel_id: str,
        project_id: str,
        project_name: str,
    ) -> ChannelMapping:
        """Create or update a channel-to-project mapping."""
        mapping = ChannelMapping(
            transport=transport,
            channel_id=channel_id,
            project_id=project_id,
            project_name=project_name,
        )
        with self._get_conn() as conn:
            # Upsert: replace if exists
            conn.execute(
                """
                INSERT INTO channel_mappings (id, transport, channel_id, project_id, project_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(transport, channel_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    project_name = excluded.project_name
                """,
                (
                    mapping.id,
                    mapping.transport,
                    mapping.channel_id,
                    mapping.project_id,
                    mapping.project_name,
                    mapping.created_at.isoformat(),
                ),
            )
        return mapping

    def get_channel_mapping(self, transport: str, channel_id: str) -> ChannelMapping | None:
        """Get channel mapping by transport and channel ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM channel_mappings WHERE transport = ? AND channel_id = ?",
                (transport, channel_id),
            ).fetchone()
            if row:
                return self._row_to_channel_mapping(row)
        return None

    def list_channel_mappings(self, transport: str | None = None) -> list[ChannelMapping]:
        """List channel mappings, optionally filtered by transport."""
        with self._get_conn() as conn:
            if transport:
                rows = conn.execute(
                    "SELECT * FROM channel_mappings WHERE transport = ? ORDER BY created_at DESC",
                    (transport,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM channel_mappings ORDER BY transport, created_at DESC").fetchall()
            return [self._row_to_channel_mapping(row) for row in rows]

    def list_channel_mappings_for_project(self, project_id: str) -> list[ChannelMapping]:
        """List channel mappings for a specific project."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM channel_mappings WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            return [self._row_to_channel_mapping(row) for row in rows]

    def delete_channel_mapping(self, transport: str, channel_id: str) -> bool:
        """Delete a channel mapping."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM channel_mappings WHERE transport = ? AND channel_id = ?",
                (transport, channel_id),
            )
            return cursor.rowcount > 0

    def _row_to_channel_mapping(self, row: sqlite3.Row) -> ChannelMapping:
        """Convert database row to ChannelMapping model."""
        return ChannelMapping(
            id=row["id"],
            transport=row["transport"],
            channel_id=row["channel_id"],
            project_id=row["project_id"],
            project_name=row["project_name"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
        )

    # ========== Usage Statistics ==========

    def get_usage_summary(self) -> dict:
        """Get aggregated usage statistics for header display."""
        from datetime import timedelta

        now = utc_now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = today - timedelta(days=7)
        # First day of current month (for billing alignment)
        month_start = today.replace(day=1)

        with self._get_conn() as conn:
            # Today's stats
            today_row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0) as cost, COUNT(*) as runs
                FROM execution_runs
                WHERE created_at >= ?
                """,
                (today.isoformat(),),
            ).fetchone()

            # Week stats
            week_row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0) as cost, COUNT(*) as runs
                FROM execution_runs
                WHERE created_at >= ?
                """,
                (week_ago.isoformat(),),
            ).fetchone()

            # This month stats (for API billing alignment)
            month_row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0) as cost, COUNT(*) as runs
                FROM execution_runs
                WHERE created_at >= ?
                """,
                (month_start.isoformat(),),
            ).fetchone()

            # All time
            total_row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0) as cost, COUNT(*) as runs
                FROM execution_runs
                """
            ).fetchone()

        return {
            "today_cost_usd": today_row["cost"],
            "today_runs": today_row["runs"],
            "week_cost_usd": week_row["cost"],
            "week_runs": week_row["runs"],
            "month_cost_usd": month_row["cost"],
            "month_runs": month_row["runs"],
            "total_cost_usd": total_row["cost"],
            "total_runs": total_row["runs"],
        }

    def get_usage_by_project(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """Aggregate usage by project."""
        query = """
            SELECT
                r.project_id,
                p.name as project_name,
                COALESCE(SUM(r.cost_usd), 0) as cost_usd,
                COUNT(*) as run_count,
                COALESCE(SUM(r.input_tokens), 0) as input_tokens,
                COALESCE(SUM(r.output_tokens), 0) as output_tokens
            FROM execution_runs r
            JOIN projects p ON r.project_id = p.id
            WHERE 1=1
        """
        params: list[str] = []
        if since:
            query += " AND r.created_at >= ?"
            params.append(since.isoformat())
        if until:
            query += " AND r.created_at <= ?"
            params.append(until.isoformat())
        query += " GROUP BY r.project_id ORDER BY cost_usd DESC"

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "project_id": row["project_id"],
                    "project_name": row["project_name"],
                    "cost_usd": row["cost_usd"],
                    "run_count": row["run_count"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                }
                for row in rows
            ]

    def get_usage_by_day(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """Aggregate usage by day."""
        query = """
            SELECT
                DATE(created_at) as date,
                COALESCE(SUM(cost_usd), 0) as cost_usd,
                COUNT(*) as run_count,
                COALESCE(SUM(input_tokens), 0) as input_tokens,
                COALESCE(SUM(output_tokens), 0) as output_tokens
            FROM execution_runs
            WHERE 1=1
        """
        params: list[str] = []
        if since:
            query += " AND created_at >= ?"
            params.append(since.isoformat())
        if until:
            query += " AND created_at <= ?"
            params.append(until.isoformat())
        query += " GROUP BY DATE(created_at) ORDER BY date DESC"

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "date": row["date"],
                    "cost_usd": row["cost_usd"],
                    "run_count": row["run_count"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                }
                for row in rows
            ]

    def get_usage_runs(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        sort_by: str = "cost",
        sort_order: str = "desc",
        limit: int = 50,
    ) -> list[dict]:
        """Get runs with cost data for usage dashboard."""
        order_column = {
            "cost": "r.cost_usd",
            "date": "r.created_at",
            "tokens": "(COALESCE(r.input_tokens, 0) + COALESCE(r.output_tokens, 0))",
        }.get(sort_by, "r.cost_usd")
        order_dir = "DESC" if sort_order == "desc" else "ASC"

        query = """
            SELECT
                r.id,
                p.name as project_name,
                r.prompt,
                r.cost_usd,
                r.input_tokens,
                r.output_tokens,
                r.model_used,
                r.created_at,
                r.status
            FROM execution_runs r
            JOIN projects p ON r.project_id = p.id
            WHERE 1=1
        """
        params: list[str | int] = []
        if since:
            query += " AND r.created_at >= ?"
            params.append(since.isoformat())
        if until:
            query += " AND r.created_at <= ?"
            params.append(until.isoformat())
        query += f" ORDER BY {order_column} {order_dir} NULLS LAST LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "id": row["id"],
                    "project_name": row["project_name"],
                    "prompt": row["prompt"][:100] if row["prompt"] else "",  # Truncate
                    "cost_usd": row["cost_usd"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "model_used": row["model_used"],
                    "created_at": row["created_at"],
                    "status": row["status"],
                }
                for row in rows
            ]

    # ========== Settings CRUD ==========

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Get a setting value by key."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting value."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, utc_now().isoformat()),
            )

    def get_all_settings(self) -> dict[str, str]:
        """Get all settings as a dictionary."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: row["value"] for row in rows}

    # ========== Workspace Settings CRUD ==========

    def get_workspace_settings(self, workspace_id: str) -> dict[str, str]:
        """Get all settings for a workspace (both overrides and env vars)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM workspace_settings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
            return {row["key"]: row["value"] for row in rows}

    def get_workspace_setting(self, workspace_id: str, key: str, default: str | None = None) -> str | None:
        """Get a single workspace setting."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = ?",
                (workspace_id, key),
            ).fetchone()
            return row["value"] if row else default

    def set_workspace_setting(self, workspace_id: str, key: str, value: str) -> None:
        """Set/update a workspace setting using UPSERT."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO workspace_settings (workspace_id, key, value, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(workspace_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (workspace_id, key, value, utc_now().isoformat()),
            )

    def delete_workspace_setting(self, workspace_id: str, key: str) -> bool:
        """Remove a workspace setting override (reverts to global)."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM workspace_settings WHERE workspace_id = ? AND key = ?",
                (workspace_id, key),
            )
            return cursor.rowcount > 0

    def delete_all_workspace_settings(self, workspace_id: str) -> int:
        """Remove all settings for a workspace."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM workspace_settings WHERE workspace_id = ?",
                (workspace_id,),
            )
            return cursor.rowcount

    def resolve_setting(self, key: str, default: str | None = None, workspace_id: str | None = None) -> str | None:
        """Resolve a setting: workspace override > global default > hardcoded default."""
        if workspace_id:
            ws_value = self.get_workspace_setting(workspace_id, key)
            if ws_value is not None:
                return ws_value
        return self.get_setting(key, default)

    def get_workspace_env_vars(self, workspace_id: str) -> dict[str, str]:
        """Get env vars for a workspace (keys starting with 'env.')."""
        all_settings = self.get_workspace_settings(workspace_id)
        return {k[4:]: v for k, v in all_settings.items() if k.startswith("env.")}

    # ========== Image Attachment CRUD (Phase 10.1) ==========

    def create_image(self, image: ImageAttachment) -> ImageAttachment:
        """Create a new image record."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO images (id, file_path, original_name, mime_type,
                                   size_bytes, hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image.id,
                    image.file_path,
                    image.original_name,
                    image.mime_type,
                    image.size_bytes,
                    image.hash,
                    image.created_at.isoformat(),
                    image.updated_at.isoformat(),
                ),
            )
        return image

    def get_image(self, image_id: str) -> ImageAttachment | None:
        """Get image by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
            if row:
                return self._row_to_image(row)
        return None

    def get_image_by_hash(self, hash_value: str) -> ImageAttachment | None:
        """Get image by content hash (for deduplication)."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM images WHERE hash = ?", (hash_value,)).fetchone()
            if row:
                return self._row_to_image(row)
        return None

    def delete_image(self, image_id: str) -> bool:
        """Delete an image record."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
            return cursor.rowcount > 0

    def _row_to_image(self, row: sqlite3.Row) -> ImageAttachment:
        """Convert database row to ImageAttachment model."""
        return ImageAttachment(
            id=row["id"],
            file_path=row["file_path"],
            original_name=row["original_name"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            hash=row["hash"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
        )

    # ========== Run-Image Association ==========

    def attach_image_to_run(self, run_id: str, image_id: str, source: str = "user") -> None:
        """Attach an image to a run.

        Args:
            run_id: Run UUID
            image_id: Image UUID
            source: Origin of the image — "user" (uploaded) or "screenshot" (agent-browser)
        """
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO run_images (run_id, image_id, created_at, source)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, image_id, utc_now().isoformat(), source),
            )

    def detach_image_from_run(self, run_id: str, image_id: str) -> bool:
        """Detach an image from a run."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM run_images WHERE run_id = ? AND image_id = ?",
                (run_id, image_id),
            )
            return cursor.rowcount > 0

    def list_images_for_run(self, run_id: str) -> list[ImageAttachment]:
        """List all images attached to a run, including source."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT i.*, ri.source FROM images i
                JOIN run_images ri ON i.id = ri.image_id
                WHERE ri.run_id = ?
                ORDER BY ri.created_at ASC
                """,
                (run_id,),
            ).fetchall()
            results = []
            for row in rows:
                img = self._row_to_image(row)
                # Attach source from the join (may be None for old rows)
                img.source = row["source"] or "user"
                results.append(img)
            return results

    def count_image_references(self, image_id: str) -> int:
        """Count how many runs reference an image."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as count FROM run_images WHERE image_id = ?",
                (image_id,),
            ).fetchone()
            return row["count"] if row else 0

    def list_orphan_images(self) -> list[ImageAttachment]:
        """List images that are not attached to any run."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                LEFT JOIN run_images ri ON i.id = ri.image_id
                WHERE ri.run_id IS NULL
                """,
            ).fetchall()
            return [self._row_to_image(row) for row in rows]

    # ========== Worker CRUD (Distributed Workers) ==========

    def create_worker(self, worker: Worker) -> Worker:
        """Create a new worker."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO workers (id, name, type, base_url, api_key, max_concurrent,
                                    status, last_heartbeat, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worker.id,
                    worker.name,
                    worker.type.value,
                    worker.base_url,
                    worker.api_key,
                    worker.max_concurrent,
                    worker.status.value,
                    worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
                    worker.created_at.isoformat(),
                    worker.updated_at.isoformat(),
                ),
            )
        return worker

    def get_worker(self, worker_id: str) -> Worker | None:
        """Get worker by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)).fetchone()
            if row:
                return self._row_to_worker(row)
        return None

    def get_worker_by_name(self, name: str) -> Worker | None:
        """Get worker by name."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM workers WHERE name = ?", (name,)).fetchone()
            if row:
                return self._row_to_worker(row)
        return None

    def list_workers(self, status: WorkerStatus | None = None) -> list[Worker]:
        """List all workers, optionally filtered by status."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM workers WHERE status = ? ORDER BY name",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM workers ORDER BY name").fetchall()
            return [self._row_to_worker(row) for row in rows]

    def get_healthy_workers(self) -> list[Worker]:
        """Get all healthy workers."""
        return self.list_workers(status=WorkerStatus.HEALTHY)

    def update_worker(self, worker: Worker) -> Worker | None:
        """Update an existing worker."""
        worker.updated_at = utc_now()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE workers
                SET name = ?, type = ?, base_url = ?, api_key = ?, max_concurrent = ?,
                    status = ?, last_heartbeat = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    worker.name,
                    worker.type.value,
                    worker.base_url,
                    worker.api_key,
                    worker.max_concurrent,
                    worker.status.value,
                    worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
                    worker.updated_at.isoformat(),
                    worker.id,
                ),
            )
            if cursor.rowcount > 0:
                return worker
            return None

    def delete_worker(self, worker_id: str) -> bool:
        """Delete a worker."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
            return cursor.rowcount > 0

    def update_worker_heartbeat(self, worker_id: str) -> Worker | None:
        """Update worker heartbeat timestamp and mark healthy."""
        worker = self.get_worker(worker_id)
        if not worker:
            return None
        worker.mark_healthy()
        self.update_worker(worker)
        return worker

    def _row_to_worker(self, row: sqlite3.Row) -> Worker:
        """Convert database row to Worker model."""
        return Worker(
            id=row["id"],
            name=row["name"],
            type=WorkerType(row["type"]),
            base_url=row["base_url"],
            api_key=row["api_key"],
            max_concurrent=row["max_concurrent"],
            status=WorkerStatus(row["status"]),
            last_heartbeat=_parse_datetime(row["last_heartbeat"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
        )

    # ========== Job CRUD (Distributed Queue) ==========

    def create_job(self, job: Job) -> Job:
        """Create a new job."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, run_id, project_id, prompt, priority, status,
                                 worker_id, model, use_worktree, session_id,
                                 created_at, assigned_at, started_at, completed_at,
                                 error_message, lease_expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.run_id,
                    job.project_id,
                    job.prompt,
                    job.priority,
                    job.status.value,
                    job.worker_id,
                    job.model,
                    1 if job.use_worktree else 0,
                    job.session_id,
                    job.created_at.isoformat(),
                    job.assigned_at.isoformat() if job.assigned_at else None,
                    job.started_at.isoformat() if job.started_at else None,
                    job.completed_at.isoformat() if job.completed_at else None,
                    job.error_message,
                    job.lease_expires_at.isoformat() if job.lease_expires_at else None,
                ),
            )
        return job

    def get_job(self, job_id: str) -> Job | None:
        """Get job by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                return self._row_to_job(row)
        return None

    def get_job_by_run_id(self, run_id: str) -> Job | None:
        """Get job by run ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE run_id = ?", (run_id,)).fetchone()
            if row:
                return self._row_to_job(row)
        return None

    def list_jobs(
        self,
        status: JobStatus | None = None,
        worker_id: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs with optional filters."""
        with self._get_conn() as conn:
            query = "SELECT * FROM jobs WHERE 1=1"
            params: list[str | int] = []

            if status:
                query += " AND status = ?"
                params.append(status.value)

            if worker_id:
                query += " AND worker_id = ?"
                params.append(worker_id)

            query += " ORDER BY priority ASC, created_at ASC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_job(row) for row in rows]

    def list_queued_jobs(self, limit: int = 100) -> list[Job]:
        """List jobs waiting in queue."""
        return self.list_jobs(status=JobStatus.QUEUED, limit=limit)

    def update_job(self, job: Job) -> None:
        """Update an existing job."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = ?, assigned_at = ?, started_at = ?,
                    completed_at = ?, error_message = ?, lease_expires_at = ?
                WHERE id = ?
                """,
                (
                    job.status.value,
                    job.worker_id,
                    job.assigned_at.isoformat() if job.assigned_at else None,
                    job.started_at.isoformat() if job.started_at else None,
                    job.completed_at.isoformat() if job.completed_at else None,
                    job.error_message,
                    job.lease_expires_at.isoformat() if job.lease_expires_at else None,
                    job.id,
                ),
            )

    def delete_job(self, job_id: str) -> bool:
        """Delete a job."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    def get_expired_lease_jobs(self) -> list[Job]:
        """Get jobs with expired leases (for recovery)."""
        now = utc_now().isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                  AND status IN (?, ?)
                """,
                (now, JobStatus.ASSIGNED.value, JobStatus.RUNNING.value),
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        """Convert database row to Job model."""
        return Job(
            id=row["id"],
            run_id=row["run_id"],
            project_id=row["project_id"],
            prompt=row["prompt"],
            priority=row["priority"],
            status=JobStatus(row["status"]),
            worker_id=row["worker_id"],
            model=row["model"],
            use_worktree=bool(row["use_worktree"]),
            session_id=row["session_id"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            assigned_at=_parse_datetime(row["assigned_at"]),
            started_at=_parse_datetime(row["started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
            error_message=row["error_message"],
            lease_expires_at=_parse_datetime(row["lease_expires_at"]),
        )

    # ========== Webhook Config CRUD ==========

    def create_webhook_config(self, config: WebhookConfig) -> WebhookConfig:
        """Create a new webhook configuration."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO webhook_configs (id, handler, project_id, secret_key, events,
                                            prompt_template, enabled, branches,
                                            ignore_branches, labels, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config.id,
                    config.handler,
                    config.project_id,
                    config.secret_key,
                    json.dumps(config.events) if config.events else None,
                    config.prompt_template,
                    1 if config.enabled else 0,
                    json.dumps(config.branches) if config.branches else None,
                    json.dumps(config.ignore_branches) if config.ignore_branches else None,
                    json.dumps(config.labels) if config.labels else None,
                    config.created_at.isoformat(),
                    config.updated_at.isoformat(),
                ),
            )
        return config

    def get_webhook_config(self, config_id: str) -> WebhookConfig | None:
        """Get webhook config by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM webhook_configs WHERE id = ?", (config_id,)).fetchone()
            if row:
                return self._row_to_webhook_config(row)
        return None

    def list_webhook_configs(
        self,
        handler: str | None = None,
        project_id: str | None = None,
        enabled_only: bool = True,
    ) -> list[WebhookConfig]:
        """List webhook configs with optional filters."""
        with self._get_conn() as conn:
            query = "SELECT * FROM webhook_configs WHERE 1=1"
            params: list[str | int] = []

            if handler:
                query += " AND handler = ?"
                params.append(handler)

            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)

            if enabled_only:
                query += " AND enabled = 1"

            query += " ORDER BY created_at DESC"

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_webhook_config(row) for row in rows]

    def get_webhook_configs_for_handler(self, handler: str) -> list[WebhookConfig]:
        """Get all enabled webhook configs for a handler (e.g., 'github')."""
        return self.list_webhook_configs(handler=handler, enabled_only=True)

    def update_webhook_config(self, config: WebhookConfig) -> WebhookConfig | None:
        """Update an existing webhook config."""
        config.updated_at = utc_now()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE webhook_configs
                SET handler = ?, project_id = ?, secret_key = ?, events = ?,
                    prompt_template = ?, enabled = ?, branches = ?,
                    ignore_branches = ?, labels = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    config.handler,
                    config.project_id,
                    config.secret_key,
                    json.dumps(config.events) if config.events else None,
                    config.prompt_template,
                    1 if config.enabled else 0,
                    json.dumps(config.branches) if config.branches else None,
                    json.dumps(config.ignore_branches) if config.ignore_branches else None,
                    json.dumps(config.labels) if config.labels else None,
                    config.updated_at.isoformat(),
                    config.id,
                ),
            )
            if cursor.rowcount > 0:
                return config
            return None

    def delete_webhook_config(self, config_id: str) -> bool:
        """Delete a webhook config."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM webhook_configs WHERE id = ?", (config_id,))
            return cursor.rowcount > 0

    def _row_to_webhook_config(self, row: sqlite3.Row) -> WebhookConfig:
        """Convert database row to WebhookConfig model."""
        return WebhookConfig(
            id=row["id"],
            handler=row["handler"],
            project_id=row["project_id"],
            secret_key=row["secret_key"],
            events=json.loads(row["events"]) if row["events"] else [],
            prompt_template=row["prompt_template"],
            enabled=bool(row["enabled"]),
            branches=json.loads(row["branches"]) if row["branches"] else None,
            ignore_branches=json.loads(row["ignore_branches"]) if row["ignore_branches"] else None,
            labels=json.loads(row["labels"]) if row["labels"] else None,
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
        )

    # ========== Message Run Mapping CRUD ==========

    def create_message_run_map(
        self,
        transport: str,
        message_id: str,
        run_id: str,
        chat_id: str,
        user_id: str,
        ttl_days: int = MESSAGE_RUN_MAP_TTL_DAYS,
    ) -> MessageRunMapping:
        """Create a message-to-run mapping for reply-based resume.

        Args:
            transport: Transport name (e.g., "discord", "telegram")
            message_id: Platform-specific message ID
            run_id: ExecutionRun ID
            chat_id: Channel/chat ID
            user_id: User who initiated the run
            ttl_days: Days until expiration (default: 7)

        Returns:
            Created MessageRunMapping
        """
        from datetime import timedelta

        now = utc_now()
        expires_at = now + timedelta(days=ttl_days)

        mapping = MessageRunMapping(
            transport=transport,
            message_id=message_id,
            run_id=run_id,
            chat_id=chat_id,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
        )

        with self._get_conn() as conn:
            # Upsert: replace if exists
            conn.execute(
                """
                INSERT INTO message_run_map
                (id, transport, message_id, run_id, chat_id, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transport, message_id, chat_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    user_id = excluded.user_id,
                    expires_at = excluded.expires_at
                """,
                (
                    mapping.id,
                    mapping.transport,
                    mapping.message_id,
                    mapping.run_id,
                    mapping.chat_id,
                    mapping.user_id,
                    mapping.created_at.isoformat(),
                    mapping.expires_at.isoformat(),
                ),
            )
        return mapping

    def get_message_run_map(
        self,
        transport: str,
        message_id: str,
        chat_id: str,
    ) -> MessageRunMapping | None:
        """Get message-to-run mapping by transport, message ID, and chat ID.

        Only returns non-expired mappings.

        Args:
            transport: Transport name
            message_id: Platform-specific message ID
            chat_id: Channel/chat ID

        Returns:
            MessageRunMapping if found and not expired, None otherwise
        """
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM message_run_map
                WHERE transport = ? AND message_id = ? AND chat_id = ?
                AND expires_at > ?
                """,
                (transport, message_id, chat_id, utc_now().isoformat()),
            ).fetchone()
            if row:
                return self._row_to_message_run_map(row)
        return None

    def find_message_run_map_by_run(
        self,
        run_id: str,
        transport: str | None = None,
    ) -> MessageRunMapping | None:
        """Find the most-recent (non-expired) message-to-run mapping for a run.

        Used by transports to discover the originating channel/message for a
        run so out-of-band notifications (approvals, questions, completions)
        can be posted back into the same Discord channel — replying to the
        originating status message for thread continuity.

        Args:
            run_id: ExecutionRun ID to look up
            transport: Optional transport filter (e.g., "discord")

        Returns:
            Most-recent MessageRunMapping, or None if none exists / all expired
        """
        with self._get_conn() as conn:
            if transport:
                row = conn.execute(
                    """
                    SELECT * FROM message_run_map
                    WHERE run_id = ? AND transport = ? AND expires_at > ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (run_id, transport, utc_now().isoformat()),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM message_run_map
                    WHERE run_id = ? AND expires_at > ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (run_id, utc_now().isoformat()),
                ).fetchone()
            if row:
                return self._row_to_message_run_map(row)
        return None

    def cleanup_expired_message_run_maps(self) -> int:
        """Delete expired message-to-run mappings.

        Returns:
            Number of deleted mappings
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM message_run_map WHERE expires_at <= ?",
                (utc_now().isoformat(),),
            )
            return cursor.rowcount

    def _row_to_message_run_map(self, row: sqlite3.Row) -> MessageRunMapping:
        """Convert database row to MessageRunMapping model."""
        return MessageRunMapping(
            id=row["id"],
            transport=row["transport"],
            message_id=row["message_id"],
            run_id=row["run_id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            expires_at=_parse_datetime(row["expires_at"]),  # type: ignore[arg-type]
        )

    # ========== Chat History CRUD ==========

    def create_chat_history(
        self,
        user_id: str,
        transport: str,
        role: str,
        text: str,
        ttl_hours: int = CHAT_HISTORY_TTL_HOURS,
    ) -> ChatHistoryEntry:
        """Create a chat history entry.

        Args:
            user_id: Universal user ID (e.g., 'telegram:123')
            transport: Transport name
            role: 'user' or 'assistant'
            text: Message content
            ttl_hours: Hours until expiration (default: 48)

        Returns:
            Created ChatHistoryEntry
        """
        from datetime import timedelta

        now = utc_now()
        expires_at = now + timedelta(hours=ttl_hours)

        entry = ChatHistoryEntry(
            user_id=user_id,
            transport=transport,
            role=role,
            text=text,
            created_at=now,
            expires_at=expires_at,
        )

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_history
                (id, user_id, transport, role, text, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.user_id,
                    entry.transport,
                    entry.role,
                    entry.text,
                    entry.created_at.isoformat(),
                    entry.expires_at.isoformat(),
                ),
            )
        return entry

    def get_chat_history(
        self,
        user_id: str,
        limit: int = 10,
    ) -> list[ChatHistoryEntry]:
        """Get chat history for a user.

        Returns most recent non-expired entries in chronological order
        (oldest first, suitable for conversation context).

        Args:
            user_id: Universal user ID
            limit: Maximum entries to return (default: 10)

        Returns:
            List of ChatHistoryEntry in chronological order
        """
        with self._get_conn() as conn:
            # Get most recent entries, then reverse for chronological order
            rows = conn.execute(
                """
                SELECT * FROM chat_history
                WHERE user_id = ? AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, utc_now().isoformat(), limit),
            ).fetchall()
            # Reverse to get chronological order (oldest first)
            return [self._row_to_chat_history(row) for row in reversed(rows)]

    def clear_chat_history(self, user_id: str) -> int:
        """Clear all chat history for a user.

        Args:
            user_id: Universal user ID

        Returns:
            Number of deleted entries
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_history WHERE user_id = ?",
                (user_id,),
            )
            return cursor.rowcount

    def cleanup_expired_chat_history(self) -> int:
        """Delete expired chat history entries.

        Returns:
            Number of deleted entries
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_history WHERE expires_at <= ?",
                (utc_now().isoformat(),),
            )
            return cursor.rowcount

    def _row_to_chat_history(self, row: sqlite3.Row) -> ChatHistoryEntry:
        """Convert database row to ChatHistoryEntry model."""
        return ChatHistoryEntry(
            id=row["id"],
            user_id=row["user_id"],
            transport=row["transport"],
            role=row["role"],
            text=row["text"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            expires_at=_parse_datetime(row["expires_at"]),  # type: ignore[arg-type]
        )

    # ========== Task Chain CRUD ==========

    def create_chain(self, chain: TaskChain) -> TaskChain:
        """Create a new task chain."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO task_chains (id, project_id, name, description, status,
                    created_at, started_at, completed_at, use_worktree, initiator)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chain.id,
                    chain.project_id,
                    chain.name,
                    chain.description,
                    chain.status.value,
                    chain.created_at.isoformat(),
                    chain.started_at.isoformat() if chain.started_at else None,
                    chain.completed_at.isoformat() if chain.completed_at else None,
                    1 if chain.use_worktree else 0,
                    chain.initiator,
                ),
            )
        return chain

    def get_chain(self, chain_id: str) -> TaskChain | None:
        """Get a task chain by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM task_chains WHERE id = ?", (chain_id,)).fetchone()
            if row:
                chain = self._row_to_chain(row)
                chain.steps = self.list_steps(chain_id)
                return chain
        return None

    def list_chains(
        self,
        project_id: str | None = None,
        status: ChainStatus | None = None,
    ) -> list[TaskChain]:
        """List task chains with optional filters."""
        with self._get_conn() as conn:
            query = "SELECT * FROM task_chains WHERE 1=1"
            params: list[str] = []
            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)
            if status:
                query += " AND status = ?"
                params.append(status.value)
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            chains = [self._row_to_chain(row) for row in rows]
            for chain in chains:
                chain.steps = self.list_steps(chain.id)
            return chains

    def update_chain(self, chain: TaskChain) -> None:
        """Update a task chain."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE task_chains
                SET status = ?, started_at = ?, completed_at = ?, run_id = ?
                WHERE id = ?
                """,
                (
                    chain.status.value,
                    chain.started_at.isoformat() if chain.started_at else None,
                    chain.completed_at.isoformat() if chain.completed_at else None,
                    chain.run_id,
                    chain.id,
                ),
            )

    def _row_to_chain(self, row: sqlite3.Row) -> TaskChain:
        """Convert database row to TaskChain model."""
        # run_id column may not exist in older databases
        run_id = None
        try:
            run_id = row["run_id"]
        except (IndexError, KeyError):
            pass
        return TaskChain(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"],
            status=ChainStatus(row["status"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            started_at=_parse_datetime(row["started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
            use_worktree=bool(row["use_worktree"]),
            initiator=row["initiator"],
            run_id=run_id,
        )

    # ========== Task Step CRUD ==========

    def create_step(self, step: TaskStep) -> TaskStep:
        """Create a new task step."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO task_steps (id, chain_id, name, prompt, depends_on, profile,
                    status, run_id, created_at, started_at, completed_at, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.id,
                    step.chain_id,
                    step.name,
                    step.prompt,
                    json.dumps(step.depends_on),
                    step.profile.value,
                    step.status.value,
                    step.run_id,
                    step.created_at.isoformat(),
                    step.started_at.isoformat() if step.started_at else None,
                    step.completed_at.isoformat() if step.completed_at else None,
                    step.error_message,
                ),
            )
        return step

    def get_step(self, step_id: str) -> TaskStep | None:
        """Get a task step by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM task_steps WHERE id = ?", (step_id,)).fetchone()
            if row:
                return self._row_to_step(row)
        return None

    def list_steps(self, chain_id: str) -> list[TaskStep]:
        """List all steps in a chain."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM task_steps WHERE chain_id = ? ORDER BY created_at ASC",
                (chain_id,),
            ).fetchall()
            return [self._row_to_step(row) for row in rows]

    def update_step(self, step: TaskStep) -> None:
        """Update a task step."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE task_steps
                SET status = ?, run_id = ?, started_at = ?, completed_at = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    step.status.value,
                    step.run_id,
                    step.started_at.isoformat() if step.started_at else None,
                    step.completed_at.isoformat() if step.completed_at else None,
                    step.error_message,
                    step.id,
                ),
            )

    def get_ready_steps(self, chain_id: str) -> list[TaskStep]:
        """Get steps whose dependencies are all completed and are pending/ready."""
        all_steps = self.list_steps(chain_id)
        completed_ids = {s.id for s in all_steps if s.status == StepStatus.COMPLETED}

        ready = []
        for step in all_steps:
            if step.status not in (StepStatus.PENDING, StepStatus.READY):
                continue
            if all(dep_id in completed_ids for dep_id in step.depends_on):
                ready.append(step)
        return ready

    def _row_to_step(self, row: sqlite3.Row) -> TaskStep:
        """Convert database row to TaskStep model."""
        return TaskStep(
            id=row["id"],
            chain_id=row["chain_id"],
            name=row["name"],
            prompt=row["prompt"],
            depends_on=json.loads(row["depends_on"]) if row["depends_on"] else [],
            profile=TaskProfile(row["profile"]) if row["profile"] else TaskProfile.STANDARD,
            status=StepStatus(row["status"]),
            run_id=row["run_id"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            started_at=_parse_datetime(row["started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
            error_message=row["error_message"],
        )

    # ========== Activity Log CRUD (F11) ==========

    def log_activity(
        self,
        actor: str,
        action: str,
        result: str | None = None,
        message: str | None = None,
        metadata: dict | None = None,
    ) -> ActivityEvent:
        """Log an activity event."""
        event = ActivityEvent(
            actor=actor,
            action=action,
            result=result,
            message=message,
            metadata=metadata,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO activity_events (id, timestamp, actor, action, result, message, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.timestamp.isoformat(),
                    event.actor,
                    event.action,
                    event.result,
                    event.message,
                    json.dumps(event.metadata) if event.metadata else None,
                    event.created_at.isoformat(),
                ),
            )
        return event

    def list_activities(
        self,
        actor: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[ActivityEvent]:
        """Query activity events with optional filters."""
        conditions: list[str] = []
        params: list[str | int] = []
        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM activity_events{where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_activity_event(row) for row in rows]

    def cleanup_activities(self, days: int = 90) -> int:
        """Delete events older than N days. Returns count deleted."""
        from datetime import timedelta

        cutoff = (utc_now() - timedelta(days=days)).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM activity_events WHERE timestamp < ?", (cutoff,))
            return cursor.rowcount

    def _row_to_activity_event(self, row: sqlite3.Row) -> ActivityEvent:
        """Convert database row to ActivityEvent model."""
        return ActivityEvent(
            id=row["id"],
            timestamp=_parse_datetime(row["timestamp"]),  # type: ignore[arg-type]
            actor=row["actor"],
            action=row["action"],
            result=row["result"],
            message=row["message"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
        )

    # ========== Work Queue CRUD (F12) ==========

    def enqueue_work(
        self,
        project_id: str,
        prompt: str,
        profile: str = "standard",
        priority: int = 10,
    ) -> WorkQueueItem:
        """Add an item to the work queue."""
        item = WorkQueueItem(
            project_id=project_id,
            prompt=prompt,
            profile=profile,
            priority=priority,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO work_queue (id, project_id, prompt, profile, priority, status,
                    claimed_by, created_at, claimed_at, started_at, completed_at,
                    last_heartbeat_at, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.project_id,
                    item.prompt,
                    item.profile,
                    item.priority,
                    item.status.value,
                    item.claimed_by,
                    item.created_at.isoformat(),
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
        return item

    def claim_work(self, project_id: str) -> WorkQueueItem | None:
        """Atomically claim highest-priority unclaimed item. Returns None if empty."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM work_queue
                WHERE project_id = ? AND status = ?
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (project_id, WorkQueueStatus.PENDING.value),
            ).fetchone()
            if not row:
                return None

            now = utc_now().isoformat()
            conn.execute(
                "UPDATE work_queue SET status = ?, claimed_at = ? WHERE id = ?",
                (WorkQueueStatus.CLAIMED.value, now, row["id"]),
            )
            item = self._row_to_work_queue_item(row)
            item.status = WorkQueueStatus.CLAIMED
            item.claimed_at = _parse_datetime(now)
            return item

    def update_work_item(self, item: WorkQueueItem) -> None:
        """Update a work queue item."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE work_queue
                SET status = ?, claimed_by = ?, started_at = ?, completed_at = ?,
                    last_heartbeat_at = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    item.status.value,
                    item.claimed_by,
                    item.started_at.isoformat() if item.started_at else None,
                    item.completed_at.isoformat() if item.completed_at else None,
                    item.last_heartbeat_at.isoformat() if item.last_heartbeat_at else None,
                    item.error_message,
                    item.id,
                ),
            )

    def list_work_items(
        self,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[WorkQueueItem]:
        """List work queue items with optional filters."""
        conditions: list[str] = []
        params: list[str | int] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM work_queue{where} ORDER BY priority ASC, created_at ASC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_work_queue_item(row) for row in rows]

    def release_stale_work_claims(self, threshold_secs: int = 1800) -> int:
        """Release items claimed >threshold ago with no heartbeat. Returns count."""
        from datetime import timedelta

        cutoff = (utc_now() - timedelta(seconds=threshold_secs)).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE work_queue SET status = ?, claimed_by = NULL, claimed_at = NULL
                WHERE status = ? AND claimed_at < ?
                AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
                """,
                (
                    WorkQueueStatus.PENDING.value,
                    WorkQueueStatus.CLAIMED.value,
                    cutoff,
                    cutoff,
                ),
            )
            return cursor.rowcount

    def _row_to_work_queue_item(self, row: sqlite3.Row) -> WorkQueueItem:
        """Convert database row to WorkQueueItem model."""
        return WorkQueueItem(
            id=row["id"],
            project_id=row["project_id"],
            prompt=row["prompt"],
            profile=row["profile"] or "standard",
            priority=row["priority"] or 10,
            status=WorkQueueStatus(row["status"]),
            claimed_by=row["claimed_by"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            claimed_at=_parse_datetime(row["claimed_at"]),
            started_at=_parse_datetime(row["started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
            last_heartbeat_at=_parse_datetime(row["last_heartbeat_at"]),
            error_message=row["error_message"],
        )

    # ========== Merge Queue CRUD (F8) ==========

    def enqueue_merge(self, entry: MergeQueueEntry) -> MergeQueueEntry:
        """Add an entry to the merge queue."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO merge_queue (id, run_id, project_id, branch_name, pr_number,
                    pr_url, status, priority, conflict_count, max_retries, last_error,
                    created_at, processing_started_at, completed_at, next_retry_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.run_id,
                    entry.project_id,
                    entry.branch_name,
                    entry.pr_number,
                    entry.pr_url,
                    entry.status.value,
                    entry.priority,
                    entry.conflict_count,
                    entry.max_retries,
                    entry.last_error,
                    entry.created_at.isoformat(),
                    None,
                    None,
                    None,
                ),
            )
        return entry

    def get_merge_entry(self, entry_id: str) -> MergeQueueEntry | None:
        """Get a merge queue entry by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM merge_queue WHERE id = ?", (entry_id,)).fetchone()
            if row:
                return self._row_to_merge_entry(row)
        return None

    def update_merge_entry(self, entry: MergeQueueEntry) -> None:
        """Update a merge queue entry."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE merge_queue
                SET status = ?, priority = ?, conflict_count = ?, last_error = ?,
                    processing_started_at = ?, completed_at = ?, next_retry_at = ?
                WHERE id = ?
                """,
                (
                    entry.status.value,
                    entry.priority,
                    entry.conflict_count,
                    entry.last_error,
                    entry.processing_started_at.isoformat() if entry.processing_started_at else None,
                    entry.completed_at.isoformat() if entry.completed_at else None,
                    entry.next_retry_at.isoformat() if entry.next_retry_at else None,
                    entry.id,
                ),
            )

    def list_merge_entries(
        self,
        status: str | None = None,
        limit: int = 20,
    ) -> list[MergeQueueEntry]:
        """List merge queue entries with optional status filter."""
        if status:
            query = "SELECT * FROM merge_queue WHERE status = ? ORDER BY priority ASC, created_at ASC LIMIT ?"
            params: tuple = (status, limit)
        else:
            query = "SELECT * FROM merge_queue ORDER BY priority ASC, created_at ASC LIMIT ?"
            params = (limit,)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_merge_entry(row) for row in rows]

    def _row_to_merge_entry(self, row: sqlite3.Row) -> MergeQueueEntry:
        """Convert database row to MergeQueueEntry model."""
        return MergeQueueEntry(
            id=row["id"],
            run_id=row["run_id"],
            project_id=row["project_id"],
            branch_name=row["branch_name"],
            pr_number=row["pr_number"],
            pr_url=row["pr_url"],
            status=MergeQueueStatus(row["status"]),
            priority=row["priority"] or 10,
            conflict_count=row["conflict_count"] or 0,
            max_retries=row["max_retries"] or 3,
            last_error=row["last_error"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            processing_started_at=_parse_datetime(row["processing_started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
            next_retry_at=_parse_datetime(row["next_retry_at"]),
        )

    # ========== Witness Decision CRUD (F9) ==========

    def record_witness_decision(self, decision: WitnessDecision) -> WitnessDecision:
        """Record a witness classification decision."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO witness_decisions (id, run_id, timestamp, classification,
                    confidence, reasoning, action, action_result, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.run_id,
                    decision.timestamp.isoformat(),
                    decision.classification.value,
                    decision.confidence,
                    decision.reasoning,
                    decision.action.value,
                    decision.action_result,
                    decision.created_at.isoformat(),
                ),
            )
        return decision

    def list_witness_decisions(
        self,
        run_id: str,
        limit: int = 20,
    ) -> list[WitnessDecision]:
        """List witness decisions for a run."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM witness_decisions WHERE run_id = ? ORDER BY timestamp DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
            return [self._row_to_witness_decision(row) for row in rows]

    def get_latest_witness_decision(self, run_id: str) -> WitnessDecision | None:
        """Get the most recent witness decision for a run."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM witness_decisions WHERE run_id = ? ORDER BY timestamp DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if row:
                return self._row_to_witness_decision(row)
        return None

    def _row_to_witness_decision(self, row: sqlite3.Row) -> WitnessDecision:
        """Convert database row to WitnessDecision model."""
        return WitnessDecision(
            id=row["id"],
            run_id=row["run_id"],
            timestamp=_parse_datetime(row["timestamp"]),  # type: ignore[arg-type]
            classification=HealthClassification(row["classification"]),
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            action=RecoveryAction(row["action"]),
            action_result=row["action_result"],
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
        )

    # ========== Notification CRUD ==========

    def create_notification(self, notification: Notification) -> Notification:
        """Create a new persistent notification."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO notifications (
                    id, workspace_id, project_id, run_id, session_id,
                    type, severity, title, message, metadata,
                    read, created_at, read_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.id,
                    notification.workspace_id,
                    notification.project_id,
                    notification.run_id,
                    notification.session_id,
                    notification.type.value,
                    notification.severity.value,
                    notification.title,
                    notification.message,
                    json.dumps(notification.metadata),
                    1 if notification.read else 0,
                    notification.created_at.isoformat(),
                    notification.read_at.isoformat() if notification.read_at else None,
                ),
            )
        return notification

    def list_notifications(
        self,
        workspace_id: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Notification]:
        """List notifications, optionally filtered by workspace and read status."""
        conditions: list[str] = []
        params: list[str | int] = []
        if workspace_id:
            conditions.append("workspace_id = ?")
            params.append(workspace_id)
        if unread_only:
            conditions.append("read = 0")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM notifications {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def get_unread_count(self, workspace_id: str | None = None) -> int:
        """Get count of unread notifications."""
        if workspace_id:
            query = "SELECT COUNT(*) FROM notifications WHERE read = 0 AND workspace_id = ?"
            params: tuple[str, ...] = (workspace_id,)
        else:
            query = "SELECT COUNT(*) FROM notifications WHERE read = 0"
            params = ()
        with self._get_conn() as conn:
            row = conn.execute(query, params).fetchone()
        return row[0] if row else 0

    def mark_notification_read(self, notification_id: str) -> Notification | None:
        """Mark a single notification as read."""
        now = utc_now()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE notifications SET read = 1, read_at = ? WHERE id = ?",
                (now.isoformat(), notification_id),
            )
            row = conn.execute(
                "SELECT * FROM notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()
        return self._row_to_notification(row) if row else None

    def mark_all_notifications_read(self, workspace_id: str | None = None) -> int:
        """Mark all notifications as read. Returns count updated."""
        now = utc_now()
        if workspace_id:
            query = "UPDATE notifications SET read = 1, read_at = ? WHERE read = 0 AND workspace_id = ?"
            params: tuple[str, ...] = (now.isoformat(), workspace_id)
        else:
            query = "UPDATE notifications SET read = 1, read_at = ? WHERE read = 0"
            params = (now.isoformat(),)
        with self._get_conn() as conn:
            cursor = conn.execute(query, params)
        return cursor.rowcount

    def delete_all_notifications(self) -> int:
        """Delete all notifications. Returns count deleted."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM notifications")
        return cursor.rowcount

    def delete_old_notifications(self, days: int = 30) -> int:
        """Delete notifications older than N days. Returns count deleted."""
        from datetime import timedelta

        cutoff = (utc_now() - timedelta(days=days)).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM notifications WHERE created_at < ?",
                (cutoff,),
            )
        return cursor.rowcount

    def _row_to_notification(self, row: sqlite3.Row) -> Notification:
        """Convert database row to Notification model."""
        return Notification(
            id=row["id"],
            workspace_id=row["workspace_id"],
            project_id=row["project_id"],
            run_id=row["run_id"],
            session_id=row["session_id"],
            type=NotificationType(row["type"]),
            severity=NotificationSeverity(row["severity"]),
            title=row["title"],
            message=row["message"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            read=bool(row["read"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            read_at=_parse_datetime(row["read_at"]),
        )
