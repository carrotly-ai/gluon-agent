# Plan: Paperclip-Inspired Patterns for Gluon

**Status**: Draft
**Date**: 2026-03-08
**Reference**: [`docs/paperclip-patterns-analysis.md`](../paperclip-patterns-analysis.md)
**Branch**: `feat/agent-identity-heartbeats`

## Overview

Adopt three high-value patterns from Paperclip into Gluon, sequenced to build on each other. Each phase is independently shippable.

| Phase | Feature | Priority | Effort | Depends on |
|-------|---------|----------|--------|------------|
| 1 | Persistent Agent Identity | High | M | -- |
| 2 | Heartbeat / Scheduled Wakeups | High | L | Phase 1 |
| 3 | Orchestrator Task Tracking | Medium | M | Phase 1 |
| 4 | Cost Budget Enforcement | Medium | S | Phase 1 |

### What We Already Have

Gluon already has primitives that overlap with Paperclip concepts:

| Gluon Primitive | Paperclip Equivalent | Gap |
|----------------|---------------------|-----|
| `Worker` (models.py:1267) | Agent executor | No identity/role/budget; workers are execution targets, not persistent identities |
| `Job` (models.py:1313) | Issue/Task | No workflow states beyond queue status; no assignment or checkout |
| `WorkQueueItem` (models.py:1443) | Agent inbox | No agent-level filtering; global queue only |
| `ExecutionRun.cost_usd` | CostEvent | Tracked per-run but not aggregated or enforced at agent level |
| `SupervisionConfig` | Approval gates | Already exists for run-level supervision; no agent-level policy |

The plan builds *on top of* these primitives rather than replacing them.

---

## Phase 1: Persistent Agent Identity

### Problem

Gluon has no concept of a persistent agent that owns a workspace or project. Sessions are ephemeral, and there is no way to track which "agent" (identity) is responsible for ongoing work, budget, or scheduled activities.

### Design

Add an `Agent` model that sits between Workspace and ExecutionRun. An agent is a named, persistent identity with a role, budget cap, and concurrency limit. Each workspace can have zero or more agents.

### Implementation

#### Step 1: Add Agent model

**File**: `src/gluon/models.py`

```python
class Agent(BaseModel):
    """Persistent agent identity within a workspace."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    name: str
    description: str | None = None
    role: str = "worker"
    is_active: bool = True
    monthly_budget_usd: float | None = None
    max_concurrent_runs: int = 1
    last_active_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

Keep it minimal -- no org chart, no supervisor links, no approval gates. Those can be added later if needed.

#### Step 2: Add schema migration

**File**: `src/gluon/store.py` (append to `MIGRATIONS`)

```sql
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

CREATE INDEX IF NOT EXISTS idx_agents_workspace ON agents(workspace_id);
CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);
```

#### Step 3: Add store CRUD

**File**: `src/gluon/store.py`

Methods to add:
- `create_agent(agent: Agent) -> Agent`
- `get_agent(agent_id: str) -> Agent | None`
- `get_agent_by_name(workspace_id: str, name: str) -> Agent | None`
- `list_agents(workspace_id: str | None = None) -> list[Agent]`
- `update_agent(agent_id: str, **kwargs) -> Agent`
- `delete_agent(agent_id: str) -> bool`
- `_row_to_agent(row: sqlite3.Row) -> Agent`

Follow existing patterns (see `create_workspace`, `_row_to_workspace`).

#### Step 4: Link ExecutionRun to Agent

**File**: `src/gluon/models.py` -- add field to `ExecutionRun`:

```python
agent_id: str | None = None  # FK to agents table
```

**File**: `src/gluon/store.py` -- migration:

```sql
ALTER TABLE execution_runs ADD COLUMN agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_runs_agent ON execution_runs(agent_id);
```

#### Step 5: CLI commands

**File**: `src/gluon/cli.py`

```
gluon agent list [--workspace NAME]
gluon agent create <workspace> <name> [--role ROLE] [--budget USD]
gluon agent show <name>
gluon agent update <name> [--role ROLE] [--budget USD] [--active/--inactive]
gluon agent delete <name>
```

When `gluon run` is invoked for a project in a workspace with exactly one agent, auto-link the run to that agent. When multiple agents exist, require `--agent NAME`.

#### Step 6: Orchestrator integration

**File**: `src/gluon/core.py`

- `Orchestrator.execute()`: Accept optional `agent_id` parameter. If provided, validate agent exists, is active, and hasn't exceeded budget (Phase 4). Set `run.agent_id`.
- `Orchestrator.get_agent()` / `list_agents()`: Delegate to store.

### Acceptance Criteria

- [ ] `gluon agent create myworkspace researcher --role researcher --budget 50` creates agent
- [ ] `gluon agent list` shows agents with workspace, role, active status
- [ ] `gluon run myproject "fix bug" --agent researcher` links run to agent
- [ ] `gluon agent show researcher` displays agent details including run count and spend
- [ ] Agent name is unique within workspace
- [ ] Deleting an agent does not delete its historical runs (SET NULL)

### Tests

- `tests/test_agent.py`: CRUD operations, unique constraint, workspace FK cascade
- `tests/test_core.py`: Execute with agent_id, auto-linking behavior

---

## Phase 2: Heartbeat / Scheduled Wakeups

### Problem

Agents only act when a user explicitly triggers a run. There is no mechanism for an agent to periodically wake up, assess state, and decide whether to act.

### Design

A lightweight scheduler that fires agent wakeups on a cron schedule. Each heartbeat:
1. Checks if the agent is already running (coalesce)
2. Renders a prompt template with context
3. Spawns an ExecutionRun if work is needed

Use `asyncio` scheduling (no heavy dependency like APScheduler) since Gluon already runs an async event loop in the bot/server process. For CLI-only usage, a `gluon scheduler` command starts a long-running process.

### Implementation

#### Step 1: Add schedule and heartbeat models

**File**: `src/gluon/models.py`

```python
class AgentSchedule(BaseModel):
    """Periodic wakeup schedule for an agent."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str
    project_id: str | None = None  # None = all projects in workspace
    prompt_template: str
    schedule_cron: str  # Standard cron expression
    is_enabled: bool = True
    coalesce_ttl_seconds: int = 300  # 5 min default
    task_profile: str = "quick"  # Use cheap model for heartbeat checks
    last_fired_at: datetime | None = None
    next_fire_at: datetime | None = None
    consecutive_failures: int = 0
    created_at: datetime = Field(default_factory=utc_now)

class HeartbeatRun(BaseModel):
    """Record of a scheduled agent wakeup."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str
    schedule_id: str
    execution_run_id: str | None = None
    fired_at: datetime = Field(default_factory=utc_now)
    status: str = "pending"  # pending, running, completed, failed, coalesced
    result_summary: str | None = None
    error_message: str | None = None
    completed_at: datetime | None = None
```

#### Step 2: Schema migrations

**File**: `src/gluon/store.py`

```sql
CREATE TABLE IF NOT EXISTS agent_schedules (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    prompt_template TEXT NOT NULL,
    schedule_cron TEXT NOT NULL,
    is_enabled INTEGER DEFAULT 1,
    coalesce_ttl_seconds INTEGER DEFAULT 300,
    task_profile TEXT DEFAULT 'quick',
    last_fired_at TEXT,
    next_fire_at TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedules_agent ON agent_schedules(agent_id);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON agent_schedules(is_enabled);

CREATE TABLE IF NOT EXISTS heartbeat_runs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    schedule_id TEXT NOT NULL REFERENCES agent_schedules(id) ON DELETE CASCADE,
    execution_run_id TEXT REFERENCES execution_runs(id) ON DELETE SET NULL,
    fired_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result_summary TEXT,
    error_message TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_heartbeats_agent ON heartbeat_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_heartbeats_schedule ON heartbeat_runs(schedule_id);
```

#### Step 3: Scheduler engine

**File**: `src/gluon/scheduler.py` (new)

```python
class HeartbeatScheduler:
    """Manages periodic agent wakeups using asyncio."""

    def __init__(self, store: GluonStore, runner: TaskRunner):
        self.store = store
        self.runner = runner
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self, poll_interval: int = 60) -> None:
        """Start the scheduler loop."""
        ...

    async def stop(self) -> None:
        """Stop the scheduler."""
        ...

    async def _tick(self) -> None:
        """Check all enabled schedules, fire due heartbeats."""
        ...

    async def fire_heartbeat(self, schedule: AgentSchedule) -> HeartbeatRun:
        """Fire a single heartbeat for a schedule."""
        # 1. Check coalesce: skip if agent has running heartbeat within TTL
        # 2. Check concurrency: skip if agent at max_concurrent_runs
        # 3. Render prompt template with context (agent, workspace, project)
        # 4. Spawn ExecutionRun via runner.submit()
        # 5. Record HeartbeatRun
        # 6. On failure: increment consecutive_failures; disable if >= 3
        ...

    def _render_prompt(self, template: str, context: dict) -> str:
        """Render prompt template with Jinja2-style substitution."""
        # Simple str.format_map or string.Template (avoid Jinja2 dep)
        ...

    def _is_due(self, schedule: AgentSchedule) -> bool:
        """Check if schedule is due to fire based on cron expression."""
        # Use croniter (already lightweight) or manual parsing
        ...
```

Key design decisions:
- **Coalescing**: Before spawning, check `heartbeat_runs` for existing `running` status within `coalesce_ttl_seconds`. If found, record as `coalesced` and skip.
- **Circuit breaker**: 3 consecutive failures disables the schedule. Manual re-enable via CLI.
- **Cheap model**: Heartbeat runs use `TaskProfile.QUICK` (Haiku) to minimize cost. Only spawn full runs if the heartbeat decides work is needed.
- **Cron parsing**: Add `croniter` as optional dependency, or implement basic interval parsing.

#### Step 4: CLI commands

**File**: `src/gluon/cli.py`

```
gluon schedule create <agent> --cron "*/30 * * * *" --prompt "Check project status" [--project NAME]
gluon schedule list [--agent NAME]
gluon schedule enable/disable <schedule-id>
gluon schedule delete <schedule-id>
gluon heartbeat list <agent> [--limit 20]
gluon heartbeat fire <agent>  # Manual trigger for testing
gluon scheduler start  # Long-running scheduler process
```

#### Step 5: Integration with bot/serve

**File**: `src/gluon/bot_core.py` or `src/gluon/cli.py` (`serve` command)

When `gluon serve` or `gluon bot` starts, also start `HeartbeatScheduler` as a background task in the event loop.

### Acceptance Criteria

- [ ] `gluon schedule create researcher --cron "0 */6 * * *" --prompt "Review open PRs"` creates schedule
- [ ] Scheduler fires heartbeat at correct intervals
- [ ] Coalescing prevents duplicate runs within TTL
- [ ] Failed heartbeats increment failure counter; 3 failures disables schedule
- [ ] `gluon heartbeat fire researcher` triggers immediate wakeup
- [ ] `gluon heartbeat list researcher` shows history with status
- [ ] Heartbeat runs use `TaskProfile.QUICK` (cheap model)
- [ ] Scheduler starts automatically with `gluon serve`

### Tests

- `tests/test_scheduler.py`: Due calculation, coalescing, circuit breaker, prompt rendering
- `tests/test_heartbeat.py`: HeartbeatRun lifecycle, store CRUD

### New Dependency

- `croniter` (pure Python, ~30KB) for cron expression parsing. Add to `pyproject.toml` as optional: `gluon[scheduler]`.

---

## Phase 3: Orchestrator Task Tracking

### Problem

Gluon tracks work at the `ExecutionRun` level (subprocess lifecycle), but has no concept of a "task" that survives across runs. There is no way for an agent to pick up work from a backlog, and no mechanism to prevent two agents from working on the same task.

### Design

Add a lightweight `OrchestratorTask` model with workflow states and atomic checkout. Tasks live at the project level and can be assigned to agents. This is intentionally simpler than Paperclip's full issue tracker -- no labels, no hierarchical issues, no comments (initially).

### Implementation

#### Step 1: Add task model

**File**: `src/gluon/models.py`

```python
class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"

class OrchestratorTask(BaseModel):
    """Task tracked at the orchestrator layer."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.BACKLOG
    priority: int = 5  # 1-10, 10 = highest
    assigned_agent_id: str | None = None
    created_by: str = "cli"  # "cli", "heartbeat", "webhook", agent_id
    execution_run_id: str | None = None
    execution_locked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
```

#### Step 2: Schema migration

```sql
CREATE TABLE IF NOT EXISTS orchestrator_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'backlog',
    priority INTEGER DEFAULT 5,
    assigned_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    created_by TEXT DEFAULT 'cli',
    execution_run_id TEXT REFERENCES execution_runs(id) ON DELETE SET NULL,
    execution_locked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_project ON orchestrator_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON orchestrator_tasks(assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON orchestrator_tasks(status);
```

#### Step 3: Store methods with atomic checkout

**File**: `src/gluon/store.py`

```python
LOCK_TTL_SECONDS = 3600  # 1 hour

def checkout_task(self, task_id: str, agent_id: str, run_id: str) -> OrchestratorTask:
    """Atomically lock a task for execution."""
    with self._get_conn() as conn:
        row = conn.execute("SELECT * FROM orchestrator_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise ValueError(f"Task {task_id} not found")
        task = self._row_to_task(row)
        if task.execution_locked_at:
            age = (utc_now() - task.execution_locked_at).total_seconds()
            if age < LOCK_TTL_SECONDS:
                raise TaskLockedError(f"Task locked by run {task.execution_run_id}")
        conn.execute("""
            UPDATE orchestrator_tasks
            SET execution_locked_at = ?, execution_run_id = ?,
                assigned_agent_id = ?, status = 'in_progress', updated_at = ?
            WHERE id = ?
        """, (utc_now().isoformat(), run_id, agent_id, utc_now().isoformat(), task_id))
    return self.get_task(task_id)

def release_task(self, task_id: str, new_status: str) -> OrchestratorTask:
    """Release lock and update status."""
    completed_at = utc_now().isoformat() if new_status == "done" else None
    with self._get_conn() as conn:
        conn.execute("""
            UPDATE orchestrator_tasks
            SET status = ?, execution_locked_at = NULL, execution_run_id = NULL,
                completed_at = ?, updated_at = ?
            WHERE id = ?
        """, (new_status, completed_at, utc_now().isoformat(), task_id))
    return self.get_task(task_id)

def get_agent_inbox(self, agent_id: str) -> list[OrchestratorTask]:
    """Get tasks assigned to agent, ordered by priority."""
    ...
```

#### Step 4: CLI commands

```
gluon task create <project> "title" [--description TEXT] [--priority N] [--assign AGENT]
gluon task list [--project NAME] [--agent NAME] [--status STATUS]
gluon task show <task-id>
gluon task assign <task-id> <agent>
gluon task done <task-id>
gluon task cancel <task-id>
gluon task inbox <agent>  # Show assigned tasks sorted by priority
```

#### Step 5: Heartbeat integration

When a heartbeat fires, include task inbox in the rendered prompt context:

```
You are agent "{{agent_name}}" responsible for workspace "{{workspace_name}}".

Your inbox ({{inbox_count}} tasks):
{% for task in inbox %}
- [P{{task.priority}}] {{task.title}} ({{task.status}})
{% endfor %}

Decide what to work on next.
```

### Acceptance Criteria

- [ ] `gluon task create myproject "Fix auth bug" --priority 8 --assign researcher` creates and assigns task
- [ ] `gluon task inbox researcher` shows assigned tasks sorted by priority
- [ ] Atomic checkout prevents two runs from claiming the same task
- [ ] Lock expires after TTL (1 hour), allowing re-checkout
- [ ] Task completion via `gluon task done` releases lock and sets `completed_at`
- [ ] Heartbeat prompt includes inbox context

### Tests

- `tests/test_tasks.py`: CRUD, checkout/release, lock TTL, inbox query

---

## Phase 4: Cost Budget Enforcement

### Problem

Gluon tracks `cost_usd` per ExecutionRun but doesn't aggregate at the agent level or enforce spending limits.

### Design

Before spawning a run linked to an agent, check the agent's monthly spend against `monthly_budget_usd`. This is a lightweight enhancement on top of Phase 1's Agent model.

### Implementation

#### Step 1: Add budget query to store

**File**: `src/gluon/store.py`

```python
def get_agent_monthly_spend(self, agent_id: str, month_start: datetime) -> float:
    """Sum cost_usd of all runs for this agent since month_start."""
    with self._get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0.0) as total
            FROM execution_runs
            WHERE agent_id = ? AND created_at >= ?
        """, (agent_id, month_start.isoformat())).fetchone()
    return row[0] if row else 0.0
```

#### Step 2: Add enforcement to Orchestrator.execute()

**File**: `src/gluon/core.py`

```python
# In execute(), after resolving agent:
if agent and agent.monthly_budget_usd:
    month_start = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = self.store.get_agent_monthly_spend(agent.id, month_start)
    if spent >= agent.monthly_budget_usd:
        raise BudgetExceededError(
            f"Agent {agent.name} has spent ${spent:.2f} of ${agent.monthly_budget_usd:.2f} monthly budget"
        )
```

#### Step 3: CLI budget display

`gluon agent show <name>` includes:
```
Budget: $42.50 / $50.00 (85.0%) this month
```

### Acceptance Criteria

- [ ] Run is rejected if agent's monthly spend exceeds budget
- [ ] `gluon agent show` displays current month spend vs budget
- [ ] Budget is per-calendar-month (UTC)
- [ ] Runs without agent_id are not budget-restricted
- [ ] BudgetExceededError includes clear message with amounts

### Tests

- `tests/test_budget.py`: Enforcement, edge cases (no budget set, zero budget, exactly at limit)

---

## Sequencing and Dependencies

```
Phase 1: Agent Identity
    |
    +---> Phase 2: Heartbeats (needs agent_id on runs)
    |
    +---> Phase 3: Task Tracking (needs agent for assignment)
    |
    +---> Phase 4: Budget Enforcement (needs agent monthly_budget_usd)
```

Phase 1 is the prerequisite. Phases 2-4 are independent of each other and can be done in any order or in parallel.

## Out of Scope (Deferred)

Per the analysis doc, these Paperclip patterns are intentionally skipped:

- **Org chart / supervisor hierarchy** -- not needed for single-user orchestrator
- **Approval gates** -- existing `SupervisionConfig` covers run-level approval
- **Config versioning / rollback** -- low value until agent configs are complex
- **Live event pub/sub** -- existing WebSocket streaming is sufficient
- **Multi-tenancy** -- workspaces are sufficient isolation

## New Dependencies

| Package | Phase | Purpose | Size |
|---------|-------|---------|------|
| `croniter` | 2 | Cron expression parsing | ~30KB, pure Python |

No other new dependencies required.
