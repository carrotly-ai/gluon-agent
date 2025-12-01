"""SQLite persistence layer for Gluon Agent."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from gluon.models import ExecutionRun, Project, RunStatus, Session, SessionStatus, Workspace

DEFAULT_DB_PATH = Path.home() / ".gluon" / "gluon.db"

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
        project.updated_at = datetime.now()
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
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
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
        session.updated_at = datetime.now()
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
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
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
        workspace.updated_at = datetime.now()
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
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
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

    def create_run(self, project_id: str, prompt: str, initiator: str | None = None) -> ExecutionRun:
        """Create a new execution run."""
        run = ExecutionRun(project_id=project_id, prompt=prompt, initiator=initiator)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO execution_runs
                (id, session_id, project_id, pid, status, prompt, initiator, created_at,
                 started_at, completed_at, exit_code, log_path, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        limit: int = 50,
    ) -> list[ExecutionRun]:
        """List execution runs with optional filters."""
        with self._get_conn() as conn:
            query = "SELECT * FROM execution_runs WHERE 1=1"
            params: list[str | int] = []

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

    def list_active_runs(self) -> list[ExecutionRun]:
        """List all pending or running execution runs."""
        return self.list_runs(statuses=[RunStatus.PENDING, RunStatus.RUNNING])

    def update_run(self, run: ExecutionRun) -> None:
        """Update an existing execution run."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE execution_runs
                SET session_id = ?, pid = ?, status = ?, started_at = ?,
                    completed_at = ?, exit_code = ?, log_path = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    run.session_id,
                    run.pid,
                    run.status.value,
                    run.started_at.isoformat() if run.started_at else None,
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.exit_code,
                    str(run.log_path) if run.log_path else None,
                    run.error_message,
                    run.id,
                ),
            )

    def delete_run(self, run_id: str) -> bool:
        """Delete an execution run."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM execution_runs WHERE id = ?", (run_id,))
            return cursor.rowcount > 0

    def _row_to_run(self, row: sqlite3.Row) -> ExecutionRun:
        """Convert database row to ExecutionRun model."""
        return ExecutionRun(
            id=row["id"],
            session_id=row["session_id"],
            project_id=row["project_id"],
            pid=row["pid"],
            status=RunStatus(row["status"]),
            prompt=row["prompt"],
            initiator=row["initiator"] if "initiator" in row.keys() else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            exit_code=row["exit_code"],
            log_path=Path(row["log_path"]) if row["log_path"] else None,
            error_message=row["error_message"],
        )

    def get_run_with_project(self, run_id: str) -> tuple[ExecutionRun, Project] | None:
        """Get run and its associated project."""
        run = self.get_run(run_id)
        if run:
            project = self.get_project(run.project_id)
            if project:
                return run, project
        return None
