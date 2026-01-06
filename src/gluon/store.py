"""SQLite persistence layer for Gluon Agent."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gluon.models import (
    ChannelMapping,
    ExecutionRun,
    GitStatus,
    ImageAttachment,
    Job,
    JobStatus,
    Project,
    RunStatus,
    Session,
    SessionStatus,
    WebhookConfig,
    Worker,
    WorkerStatus,
    WorkerType,
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

    def create_workspace(self, name: str, path: Path) -> Workspace:
        """Create a new workspace."""
        workspace = Workspace(name=name, path=path)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO workspaces
                (id, name, path, created_at, updated_at, scan_depth, auto_discover, ignore_patterns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                SET name = ?, path = ?, updated_at = ?, scan_depth = ?, auto_discover = ?, ignore_patterns = ?
                WHERE id = ?
                """,
                (
                    workspace.name,
                    str(workspace.path),
                    workspace.updated_at.isoformat(),
                    workspace.scan_depth,
                    1 if workspace.auto_discover else 0,
                    json.dumps(workspace.ignore_patterns),
                    workspace.id,
                ),
            )

    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace (projects are kept but unlinked)."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            return cursor.rowcount > 0

    def _row_to_workspace(self, row: sqlite3.Row) -> Workspace:
        """Convert database row to Workspace model."""
        return Workspace(
            id=row["id"],
            name=row["name"],
            path=Path(row["path"]),
            created_at=_parse_datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_datetime(row["updated_at"]),  # type: ignore[arg-type]
            scan_depth=row["scan_depth"],
            auto_discover=bool(row["auto_discover"]),
            ignore_patterns=json.loads(row["ignore_patterns"]) if row["ignore_patterns"] else [],
        )

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
    ) -> ExecutionRun:
        """Create a new execution run."""
        run = ExecutionRun(
            project_id=project_id,
            prompt=prompt,
            initiator=initiator,
            session_id=session_id,
            use_worktree=use_worktree,
            model=model,
        )
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO execution_runs
                (id, session_id, project_id, pid, status, prompt, initiator, created_at,
                 started_at, completed_at, exit_code, log_path, error_message, model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.session_id,
                    run.project_id,
                    run.pid,
                    run.status.value,
                    run.prompt,
                    run.initiator,
                    run.created_at.isoformat(),
                    run.started_at.isoformat() if run.started_at else None,
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.exit_code,
                    str(run.log_path) if run.log_path else None,
                    run.error_message,
                    run.model,
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
                SET session_id = ?, claude_session_id = ?, pid = ?, status = ?, prompt = ?,
                    started_at = ?, completed_at = ?, exit_code = ?, log_path = ?, error_message = ?,
                    thread_id = ?, cost_usd = ?, input_tokens = ?, output_tokens = ?, model_used = ?,
                    branch_name = ?, source_branch = ?, worktree_path = ?, use_worktree = ?,
                    git_commit_sha = ?, pr_number = ?, pr_url = ?, pr_status = ?, pr_mergeable = ?,
                    archived = ?, archived_at = ?, resume_count = ?, last_resumed_at = ?,
                    recovery_count = ?, last_recovery_at = ?, recovery_from_run_id = ?,
                    is_recovering = ?, recovery_item_count = ?
                WHERE id = ?
                """,
                (
                    run.session_id,
                    run.claude_session_id,
                    run.pid,
                    run.status.value,
                    run.prompt,
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
                    1 if run.archived else 0,
                    run.archived_at.isoformat() if run.archived_at else None,
                    run.resume_count,
                    run.last_resumed_at.isoformat() if run.last_resumed_at else None,
                    run.recovery_count,
                    run.last_recovery_at.isoformat() if run.last_recovery_at else None,
                    run.recovery_from_run_id,
                    1 if run.is_recovering else 0,
                    run.recovery_item_count,
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
            pid=row["pid"],
            status=RunStatus(row["status"]),
            prompt=row["prompt"],
            initiator=row["initiator"] if "initiator" in keys else None,
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

    def attach_image_to_run(self, run_id: str, image_id: str) -> None:
        """Attach an image to a run."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO run_images (run_id, image_id, created_at)
                VALUES (?, ?, ?)
                """,
                (run_id, image_id, utc_now().isoformat()),
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
        """List all images attached to a run."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                JOIN run_images ri ON i.id = ri.image_id
                WHERE ri.run_id = ?
                ORDER BY ri.created_at ASC
                """,
                (run_id,),
            ).fetchall()
            return [self._row_to_image(row) for row in rows]

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
