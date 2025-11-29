# Gluon Agent - MVP Implementation Plan

## Overview

**Goal:** Build a minimal viable product (MVP) that can manage one or more Claude Code instances operating on different projects, with the ability to resume sessions to continue working later.

**Tech Stack:**
- Python 3.12+
- `claude-agent-sdk` - Official Anthropic SDK for Claude Code
- `typer` + `rich` - Beautiful CLI interface
- `sqlite3` - Lightweight persistence for sessions (stdlib, no external deps)
- `pydantic` - Data validation and serialization
- `anyio` - Async runtime (required by claude-agent-sdk)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Gluon Agent                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐ │
│  │    CLI      │───▶│   Core      │───▶│   Claude Agent SDK      │ │
│  │  (typer)    │    │  Orchestrator│    │   (claude-agent-sdk)    │ │
│  └─────────────┘    └─────────────┘    └─────────────────────────┘ │
│         │                  │                                        │
│         │                  ▼                                        │
│         │           ┌─────────────┐                                 │
│         │           │   Session   │                                 │
│         └──────────▶│   Store     │                                 │
│                     │  (SQLite)   │                                 │
│                     └─────────────┘                                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
gluon-agent/
├── pyproject.toml              # Project metadata & dependencies
├── README.md                   # Project documentation
├── PLAN.md                     # This file
│
├── src/
│   └── gluon/
│       ├── __init__.py         # Package init, version
│       ├── cli.py              # Typer CLI commands
│       ├── core.py             # Orchestrator business logic
│       ├── models.py           # Pydantic models (Project, Session, Task)
│       ├── store.py            # SQLite session/project persistence
│       └── agent.py            # Claude Agent SDK wrapper
│
├── tests/
│   ├── __init__.py
│   ├── test_models.py          # Model validation tests
│   ├── test_store.py           # Storage layer tests
│   └── test_core.py            # Orchestrator tests (mocked SDK)
│
└── data/                       # Default data directory (gitignored)
    └── gluon.db                # SQLite database
```

---

## Data Models

### Project
```python
class Project(BaseModel):
    id: str                     # UUID
    name: str                   # Human-readable name
    path: Path                  # Absolute path to project directory
    created_at: datetime
    updated_at: datetime
    metadata: dict | None = None  # Optional extra data
```

### Session
```python
class Session(BaseModel):
    id: str                     # UUID (our internal ID)
    project_id: str             # FK to Project
    claude_session_id: str | None  # Session ID from Claude SDK (for resume)
    status: SessionStatus       # active, completed, failed, paused
    created_at: datetime
    updated_at: datetime
    last_prompt: str | None     # Last user prompt
    total_cost_usd: float = 0.0
    total_turns: int = 0
```

### SessionStatus (Enum)
```python
class SessionStatus(str, Enum):
    ACTIVE = "active"           # Currently running
    PAUSED = "paused"           # Can be resumed
    COMPLETED = "completed"     # Finished successfully
    FAILED = "failed"           # Error occurred
```

### Task (for queuing - stretch goal)
```python
class Task(BaseModel):
    id: str
    project_id: str
    prompt: str
    status: TaskStatus          # pending, running, completed, failed
    session_id: str | None      # Assigned session
    created_at: datetime
```

---

## Implementation Phases

### Phase 1: Foundation (Core Infrastructure)
**Files:** `models.py`, `store.py`, `__init__.py`

- [ ] 1.1 Create package structure with `src/gluon/`
- [ ] 1.2 Define Pydantic models for Project, Session, SessionStatus
- [ ] 1.3 Implement SQLite store with tables for projects and sessions
- [ ] 1.4 Add CRUD operations: create/get/update/list/delete for both entities
- [ ] 1.5 Add database initialization and migration logic
- [ ] 1.6 Write unit tests for models and store

**Deliverable:** Working persistence layer with tests

---

### Phase 2: Agent Wrapper (Claude SDK Integration)
**Files:** `agent.py`

- [ ] 2.1 Create `GluonAgent` class wrapping `ClaudeSDKClient`
- [ ] 2.2 Implement `start_session(project_path, prompt)` - starts new session
- [ ] 2.3 Implement `resume_session(claude_session_id, prompt)` - resumes existing
- [ ] 2.4 Implement message streaming with callbacks for real-time output
- [ ] 2.5 Extract and return session metadata (session_id, cost, turns)
- [ ] 2.6 Handle errors gracefully with proper status updates
- [ ] 2.7 Add configurable options (model, tools, permissions)

**Key SDK Integration Points:**
```python
# New session
options = ClaudeAgentOptions(
    cwd=project_path,
    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    permission_mode="acceptEdits",
)
async with ClaudeSDKClient(options=options) as client:
    await client.query(prompt)
    async for msg in client.receive_response():
        # Extract session_id from SystemMessage
        # Stream output
        # Capture ResultMessage for cost/usage

# Resume session
options = ClaudeAgentOptions(
    cwd=project_path,
    fork_session=previous_claude_session_id,  # KEY: This resumes!
    allowed_tools=[...],
)
```

**Deliverable:** Agent wrapper that can start and resume Claude sessions

---

### Phase 3: Orchestrator (Business Logic)
**Files:** `core.py`

- [ ] 3.1 Create `Orchestrator` class coordinating store + agent
- [ ] 3.2 Implement `register_project(name, path)` - adds project to registry
- [ ] 3.3 Implement `execute(project_id, prompt)` - runs task on project
  - Creates or resumes session automatically
  - Streams output to callback
  - Updates session state in store
- [ ] 3.4 Implement `resume(project_id, prompt)` - explicitly resume last session
- [ ] 3.5 Implement `list_projects()` - shows all registered projects
- [ ] 3.6 Implement `list_sessions(project_id)` - shows sessions for project
- [ ] 3.7 Implement `get_session_history(session_id)` - session details
- [ ] 3.8 Add cleanup/archive methods

**Deliverable:** Complete orchestration logic connecting all pieces

---

### Phase 4: CLI Interface
**Files:** `cli.py`

- [ ] 4.1 Setup Typer app with rich console output
- [ ] 4.2 Implement `gluon project add <name> <path>` - register project
- [ ] 4.3 Implement `gluon project list` - show all projects
- [ ] 4.4 Implement `gluon project remove <name>` - unregister project
- [ ] 4.5 Implement `gluon run <project> <prompt>` - execute task
  - Real-time streaming output with rich formatting
  - Show cost/usage summary at end
- [ ] 4.6 Implement `gluon resume <project> [prompt]` - continue session
- [ ] 4.7 Implement `gluon sessions <project>` - list sessions
- [ ] 4.8 Implement `gluon status` - show all active sessions
- [ ] 4.9 Add `--verbose` and `--quiet` flags
- [ ] 4.10 Add `--model` override option

**CLI Examples:**
```bash
# Register projects
gluon project add myapp /path/to/myapp
gluon project add backend /path/to/backend-api

# Run tasks
gluon run myapp "Fix the authentication bug in auth.py"
gluon run backend "Add rate limiting to all endpoints"

# Resume previous session
gluon resume myapp "Actually, also add logging"

# View status
gluon status
gluon sessions myapp
```

**Deliverable:** Fully functional CLI for MVP use cases

---

### Phase 5: Polish & Testing
**Files:** `tests/`, `README.md`

- [ ] 5.1 Write integration tests (with mocked Claude SDK)
- [ ] 5.2 Add error handling tests
- [ ] 5.3 Test session resume flow end-to-end
- [ ] 5.4 Write comprehensive README with examples
- [ ] 5.5 Add `--help` documentation for all commands
- [ ] 5.6 Test on real projects with actual Claude SDK

**Deliverable:** Production-ready MVP with documentation

---

## Dependencies

```toml
[project]
dependencies = [
    "claude-agent-sdk>=0.1.0",   # Anthropic's official SDK
    "typer>=0.12.0",             # CLI framework
    "rich>=13.0.0",              # Rich terminal output
    "pydantic>=2.0.0",           # Data validation
    "anyio>=4.0.0",              # Async runtime
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "ruff>=0.4.0",
    "mypy>=1.10.0",
]
```

---

## Database Schema

```sql
-- Projects table
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT  -- JSON blob
);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    claude_session_id TEXT,  -- From Claude SDK, used for resume
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_prompt TEXT,
    total_cost_usd REAL DEFAULT 0.0,
    total_turns INTEGER DEFAULT 0
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
```

---

## Key Implementation Details

### Session Resume Logic

```python
async def execute(self, project_id: str, prompt: str, force_new: bool = False):
    project = self.store.get_project(project_id)

    # Find resumable session (unless force_new)
    session = None
    if not force_new:
        session = self.store.get_latest_session(
            project_id,
            status=[SessionStatus.PAUSED, SessionStatus.ACTIVE]
        )

    # Build options
    options = ClaudeAgentOptions(
        cwd=project.path,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        permission_mode="acceptEdits",
    )

    # Add resume option if we have a previous session
    if session and session.claude_session_id:
        options.fork_session = session.claude_session_id
    else:
        # Create new session record
        session = self.store.create_session(project_id)

    # Execute with Claude SDK
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)

        async for msg in client.receive_response():
            # Capture claude_session_id from SystemMessage
            if isinstance(msg, SystemMessage):
                if hasattr(msg, 'session_id'):
                    session.claude_session_id = msg.session_id
                    self.store.update_session(session)

            # Stream to callback
            yield msg

            # Update cost from ResultMessage
            if isinstance(msg, ResultMessage):
                session.total_cost_usd += msg.total_cost_usd
                session.status = SessionStatus.PAUSED
                self.store.update_session(session)
```

### CLI Output Streaming

```python
@app.command()
def run(project: str, prompt: str):
    """Execute a task on a project."""
    console = Console()

    async def _run():
        orchestrator = Orchestrator()

        with console.status("[bold green]Working..."):
            async for msg in orchestrator.execute(project, prompt):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            console.print(block.text)
                        elif isinstance(block, ToolUseBlock):
                            console.print(f"[dim]Using tool: {block.name}[/dim]")
                elif isinstance(msg, ResultMessage):
                    console.print(f"\n[green]✓ Complete[/green]")
                    console.print(f"[dim]Cost: ${msg.total_cost_usd:.4f}[/dim]")

    anyio.run(_run)
```

---

## Success Criteria for MVP

1. **Project Management**
   - [x] Can register multiple projects with names and paths
   - [x] Can list all registered projects
   - [x] Can remove projects

2. **Task Execution**
   - [x] Can run a prompt against any registered project
   - [x] Claude agent operates in the correct working directory
   - [x] Real-time streaming output to terminal
   - [x] Cost tracking per session

3. **Session Resume**
   - [x] Session ID captured and persisted from Claude SDK
   - [x] Can resume a previous session with new prompt
   - [x] Context from previous conversation is maintained
   - [x] Can list sessions per project

4. **Error Handling**
   - [x] Graceful handling of SDK errors
   - [x] Session marked as failed on errors
   - [x] Clear error messages to user

---

## Out of Scope for MVP (Future)

- Web UI / API server
- Concurrent execution (multiple agents at once)
- Task queue system
- Custom tools / MCP servers
- Subagent orchestration
- Cost budgets / limits
- Session compaction / summarization
- Authentication / multi-user

---

## Estimated Implementation Order

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1: Foundation | 2-3 hours | None |
| Phase 2: Agent Wrapper | 2-3 hours | Phase 1, claude-agent-sdk |
| Phase 3: Orchestrator | 2-3 hours | Phases 1 & 2 |
| Phase 4: CLI | 2-3 hours | Phase 3 |
| Phase 5: Polish | 1-2 hours | Phase 4 |

**Total: ~10-14 hours**

---

## Next Steps

1. Approve this plan
2. Update `pyproject.toml` with dependencies
3. Create package structure
4. Implement Phase 1 (Foundation)
5. Iterate through remaining phases
