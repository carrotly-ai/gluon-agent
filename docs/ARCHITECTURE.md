# Gluon Agent Architecture

## Overview

Gluon Agent is an AI orchestrator that manages multiple Claude Code agents across different software projects. It provides session persistence, resume capability, workspace-based project discovery, and multiple interfaces (CLI, Telegram bot).

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Gluon Agent                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐                                         │
│  │     CLI      │  │  Telegram    │    (User Interfaces)                    │
│  │   (cli.py)   │  │  Bot (bot.py)│                                         │
│  └──────┬───────┘  └──────┬───────┘                                         │
│         │                 │                                                  │
│         │    ┌────────────┴────────────┐                                    │
│         │    │    Chat Agent           │   (Natural Language Interpreter)   │
│         │    │  (chat_agent.py)        │                                    │
│         │    │  - Claude SDK + MCP     │                                    │
│         │    └────────────┬────────────┘                                    │
│         │                 │                                                  │
│         └────────┬────────┘                                                  │
│                  ▼                                                           │
│         ┌───────────────────┐                                               │
│         │    Orchestrator   │         (Business Logic)                      │
│         │     (core.py)     │                                               │
│         │  - Project mgmt   │                                               │
│         │  - Workspace mgmt │                                               │
│         │  - Session mgmt   │                                               │
│         │  - Execution flow │                                               │
│         └─────────┬─────────┘                                               │
│                   │                                                          │
│     ┌─────────────┼─────────────┐                                           │
│     ▼             ▼             ▼                                           │
│  ┌────────┐  ┌─────────┐  ┌──────────────┐                                  │
│  │ Store  │  │  Agent  │  │   Models     │                                  │
│  │(store) │  │(agent)  │  │  (models)    │                                  │
│  │        │  │         │  │              │                                  │
│  │ SQLite │  │ Claude  │  │ - Workspace  │                                  │
│  │  CRUD  │  │ Agent   │  │ - Project    │                                  │
│  │        │  │  SDK    │  │ - Session    │                                  │
│  └────────┘  └─────────┘  └──────────────┘                                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

External Dependencies:
  - Claude Code CLI (claude-agent-sdk)
  - python-telegram-bot (for bot interface)
```

## Component Details

### 1. Models (`models.py`)

Pydantic models defining the core data structures.

#### Workspace
```python
class Workspace(BaseModel):
    id: str                    # UUID
    name: str                  # Unique human-readable name
    path: Path                 # Absolute path to workspace directory
    scan_depth: int = 1        # How deep to scan for projects
    auto_discover: bool = True # Auto-discover new projects
    ignore_patterns: list[str] # Patterns to ignore (e.g., node_modules)
```

**Key Method:** `scan_for_projects()` - Scans immediate children for project markers.

#### Project
```python
class Project(BaseModel):
    id: str                      # UUID
    name: str                    # Unique human-readable name
    path: Path                   # Absolute path to project directory
    workspace_id: str | None     # FK to Workspace (None = standalone)
    metadata: dict | None        # Optional extra data
```

#### Session
```python
class Session(BaseModel):
    id: str                        # Internal UUID
    project_id: str                # FK to Project
    claude_session_id: str | None  # Claude SDK session ID (for resume)
    status: SessionStatus          # active, paused, completed, failed
    last_prompt: str | None        # Last user prompt
    total_cost_usd: float          # Accumulated cost
    total_turns: int               # Number of turns
```

**Status Lifecycle:**
```
ACTIVE ──▶ PAUSED ──▶ ACTIVE (resume)
   │          │
   ▼          ▼
COMPLETED  FAILED
```

### 2. Store (`store.py`)

SQLite persistence layer with CRUD operations.

**Tables:**
- `workspaces` - Workspace metadata
- `projects` - Project registry with optional workspace FK
- `sessions` - Session tracking with Claude session IDs

**Key Methods:**
```python
# Workspace operations
create_workspace(name, path) -> Workspace
get_workspace(id) -> Workspace | None
get_workspace_by_name(name) -> Workspace | None
list_workspaces() -> list[Workspace]
update_workspace(workspace) -> None
delete_workspace(id) -> bool

# Project operations
create_project(name, path, metadata?, workspace_id?) -> Project
get_project(id) -> Project | None
get_project_by_name(name) -> Project | None
get_project_by_path(path) -> Project | None
list_projects() -> list[Project]
list_projects_by_workspace(workspace_id) -> list[Project]
update_project(project) -> None
delete_project(id) -> bool

# Session operations
create_session(project_id, prompt?) -> Session
get_session(id) -> Session | None
get_latest_session(project_id, statuses?) -> Session | None
list_sessions(project_id?) -> list[Session]
update_session(session) -> None
delete_session(id) -> bool
get_active_sessions() -> list[Session]
```

### 3. Agent (`agent.py`)

Wrapper around Claude Agent SDK.

**Key Class: `GluonAgent`**

```python
class GluonAgent:
    def __init__(
        model: str = "sonnet",
        allowed_tools: list[str] = DEFAULT_TOOLS,
        permission_mode: str = "acceptEdits"
    )

    async def execute(
        working_dir: Path,
        prompt: str,
        resume_session_id: str | None = None
    ) -> AsyncIterator[AgentMessage | AgentResult]
```

**Message Types:**
- `AgentMessage` - Streaming messages (text, tool_use, system, error)
- `AgentResult` - Final result with session_id, cost, turns, success

**Claude SDK Integration:**
```python
options = ClaudeAgentOptions(
    cwd=working_dir,
    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task", "TodoWrite"],
    permission_mode="acceptEdits",
    model="sonnet",
    resume=previous_session_id,  # For session resume
)
```

### 4. Orchestrator (`core.py`)

Central coordinator connecting all components.

**Key Methods:**
```python
# Project Management
register_project(name, path, metadata?) -> Project
get_project(name_or_id) -> Project
list_projects() -> list[Project]
remove_project(name_or_id) -> bool

# Workspace Management
register_workspace(name, path, auto_scan?) -> tuple[Workspace, list[Project]]
get_workspace(name_or_id) -> Workspace
list_workspaces() -> list[Workspace]
remove_workspace(name_or_id, remove_projects?) -> bool
scan_workspace(name_or_id) -> list[Project]
refresh_all_workspaces() -> dict[str, list[Project]]
list_workspace_projects(name_or_id) -> list[Project]

# Session Management
list_sessions(project_name?) -> list[Session]
get_session(session_id) -> Session | None
get_active_sessions() -> list[Session]
get_resumable_session(project) -> Session | None

# Execution
async execute(project_name, prompt, force_new?) -> AsyncIterator[AgentMessage | AgentResult]
async resume(project_name, prompt?) -> AsyncIterator[AgentMessage | AgentResult]

# Status
status() -> dict
```

### 5. Chat Agent (`chat_agent.py`)

Natural language interface using Claude to interpret commands.

**MCP Tools Exposed:**
- `list_projects` - List all projects
- `list_sessions` - List sessions for a project
- `get_status` - Get overall status
- `run_task` - Run a task on a project
- `resume_session` - Resume last session

**Usage Flow:**
```
User Message ──▶ Claude (with MCP tools) ──▶ Tool Calls ──▶ Response
                        │
                        ▼
               Sets _pending_task for
               actual execution by caller
```

### 6. CLI (`cli.py`)

Typer-based command interface.

**Commands:**
```
gluon project add <name> <path>     # Register project
gluon project list                   # List projects
gluon project remove <name>          # Remove project

gluon workspace add <name> <path>    # Register workspace + auto-scan
gluon workspace list                 # List workspaces
gluon workspace remove <name>        # Remove workspace
gluon workspace scan [name]          # Scan for new projects
gluon workspace projects <name>      # List projects in workspace

gluon run <project> <prompt>         # Execute task
gluon resume <project> [prompt]      # Resume session
gluon sessions [project]             # List sessions
gluon status                         # Show status

gluon bot                            # Start Telegram bot
gluon version                        # Show version
```

### 7. Telegram Bot (`bot.py`)

Always-on daemon for remote interaction.

**Commands:**
- `/start`, `/help` - Welcome and help
- `/projects` - List projects
- `/sessions [project]` - List sessions
- `/run <project> <prompt>` - Run task
- `/resume <project> [prompt]` - Resume session
- `/status` - Show status
- `/cancel` - Cancel current task

**Natural Language:** Plain text messages are processed by `GluonChatAgent`.

## Data Flow

### Task Execution Flow

```
1. User Request (CLI/Telegram/NL)
         │
         ▼
2. Orchestrator.execute(project, prompt)
         │
         ├─▶ Find/create Session
         │
         ▼
3. GluonAgent.execute(working_dir, prompt, resume_id?)
         │
         ├─▶ Build ClaudeAgentOptions
         │
         ▼
4. ClaudeSDKClient
         │
         ├─▶ claude query <prompt>
         │
         ▼
5. Stream responses (AssistantMessage, SystemMessage, ResultMessage)
         │
         ├─▶ Capture session_id
         ├─▶ Track cost/turns
         │
         ▼
6. Update Session in store
         │
         ├─▶ Status: PAUSED (success) or FAILED
         │
         ▼
7. Return AgentResult to user
```

### Workspace Discovery Flow

```
1. User: gluon workspace add carrotly /path/to/workspaces/carrotly
         │
         ▼
2. Orchestrator.register_workspace("carrotly", path)
         │
         ├─▶ Create Workspace in store
         │
         ▼
3. Workspace.scan_for_projects()
         │
         ├─▶ Iterate immediate children
         ├─▶ Check for PROJECT_MARKERS
         │   (package.json, pyproject.toml, .git, etc.)
         │
         ▼
4. For each discovered project:
         │
         ├─▶ Check if already registered (by path)
         ├─▶ Generate unique name
         ├─▶ Create Project with workspace_id
         │
         ▼
5. Return (workspace, [discovered_projects])
```

## Database Schema

```sql
CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    scan_depth INTEGER DEFAULT 1,
    auto_discover INTEGER DEFAULT 1,
    ignore_patterns TEXT  -- JSON array
);

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT  -- JSON blob
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    claude_session_id TEXT,  -- From Claude SDK, for resume
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_prompt TEXT,
    total_cost_usd REAL DEFAULT 0.0,
    total_turns INTEGER DEFAULT 0
);

-- Indexes
CREATE INDEX idx_workspaces_name ON workspaces(name);
CREATE INDEX idx_projects_name ON projects(name);
CREATE INDEX idx_projects_workspace ON projects(workspace_id);
CREATE INDEX idx_sessions_project ON sessions(project_id);
CREATE INDEX idx_sessions_status ON sessions(status);
```

## Configuration

### Environment Variables
- `GLUON_TELEGRAM_TOKEN` - Telegram bot token
- `GLUON_TELEGRAM_USERS` - Comma-separated allowed user IDs

### Environment Files (loaded in order)
1. `~/.gluon/.env` - Global config
2. `.env` - Project config
3. `.env.local` - Local overrides (highest priority)

### Data Storage
- Default database: `~/.gluon/gluon.db`

## Error Handling

### Exception Hierarchy
```python
ProjectNotFoundError    # Project not found by name/ID
ProjectExistsError      # Duplicate project name
WorkspaceNotFoundError  # Workspace not found by name/ID
WorkspaceExistsError    # Duplicate workspace name
```

### Session Error States
- On agent execution error: Session marked as `FAILED`
- On success: Session marked as `PAUSED` (ready for resume)
- On explicit completion: Session marked as `COMPLETED`
