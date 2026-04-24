# Paperclip Patterns Analysis for Gluon

> Comparative analysis of [Paperclip](https://github.com/paperclipai/paperclip) (enterprise AI agent orchestration, TypeScript/Node.js) and Gluon Agent (Python). Identifies patterns worth adopting with priority, effort, and design guidance.

---

## Executive Summary

**Paperclip** is an enterprise-grade AI orchestration platform modeling agents as "employees" within a virtual company hierarchy. It features sophisticated scheduling (heartbeats), organizational governance, budget enforcement, built-in issue tracking, and multi-agent coordination with approval gates. It's a full-featured management system for teams of LLM agents with complex workflows.

**Gluon Agent** is a lightweight Python orchestrator focused on managing Claude Code sessions across projects. It emphasizes subprocess isolation, session persistence/resume, background task execution, git operations, and autonomous problem-solving loops (Ralph). It's optimized for single-agent-per-project scenarios with deep integration into Claude Code's capabilities.

**Why Comparison Matters:** Both systems solve AI orchestration, but Paperclip addresses organizational/governance concerns while Gluon addresses technical execution concerns. Gluon can strategically adopt Paperclip's heartbeat and task-tracking patterns without becoming a full organizational platform, enabling:

1. Agent-per-workspace and agent-per-project persistent identities
2. Scheduled autonomous work (heartbeats waking agents to assess state)
3. Task/issue tracking at the orchestrator layer for coordination and audit
4. Multi-agent job queuing with prioritization
5. Cost attribution and budget enforcement

---

## Architecture Comparison

| Aspect | Paperclip | Gluon | Notes |
|--------|-----------|-------|-------|
| **Primary Model** | Company → Agents → Projects → Issues (hierarchical) | Workspaces → Projects → Sessions → ExecutionRuns (flat + hierarchical) | Paperclip is org-centric; Gluon is project-centric |
| **Agent Identity** | Persistent agent (CEO, CTO, engineer, etc.) with roles and org chart | No persistent agent identity; wraps Claude SDK sessions | Gluon agents are ephemeral; Paperclip agents are persistent entities |
| **Persistence** | PostgreSQL + Drizzle ORM (full relational) | SQLite + Python (lightweight, embedded) | Gluon prioritizes simplicity; Paperclip supports scaling |
| **Task Execution** | Heartbeats trigger periodic wakeups on schedule | Background task runner with subprocess isolation | Paperclip is event-driven scheduling; Gluon is imperative task spawning |
| **Session Management** | Per-agent task sessions with continuity | Claude SDK session forking with resume coordinator | Paperclip persists agent conversations; Gluon forks sessions for context recovery |
| **Task Tracking** | Full issue tracker (backlog→todo→in_progress→in_review→done) | ExecutionRun lifecycle only (pending→running→completed/failed) | Gluon lacks task granularity; Paperclip is a full project manager |
| **Cost Tracking** | Per-run CostEvent; monthly budget caps per agent | Total cost per run + cost_usd cap; aggregate costs per session | Gluon tracks costs but not enforcement; Paperclip enforces budgets |
| **Access Control** | Company memberships + agent API keys + JWT | Transport-level permissions (Telegram user IDs, Discord roles) | Gluon has minimal auth; Paperclip is enterprise-ready |
| **Live Events** | WebSocket pub/sub (heartbeat.run.*, agent.status, etc.) | WebSocket for streaming only; no pub/sub event system | Gluon lacks real-time multi-client coordination |
| **Governance** | Approval gates, board-only decisions, hiring workflows | No approval/governance layer | Paperclip enforces organizational policy; Gluon has none |
| **Concurrency Control** | maxConcurrentRuns per agent + withAgentStartLock serialization | max_concurrent=16 global cap for all runs | Gluon is simpler but less nuanced |

---

## Patterns Worth Adopting

### Pattern 1: Persistent Agent Identity (Priority: HIGH, Effort: MEDIUM)

**What It Is:**
Each agent has a unique, persistent identity (not just a session ID). Agents maintain context across sessions, have assigned roles/responsibilities, and can be queried for their status and availability.

**How Paperclip Implements It:**
```typescript
interface Agent {
  id: string;
  name: string;
  role: 'ceo' | 'cto' | 'engineer' | 'designer';
  reportsTo: string | null;  // org chart relationship
  apiKey: string;            // hashed at rest
  budget: {
    monthly: number;
    spent: number;
    status: 'active' | 'paused';
  };
  maxConcurrentRuns: number;
  lastActiveAt: timestamp;
}

// Agent wakeup lifecycle
interface AgentWakeupRequest {
  agentId: string;
  source: 'timer' | 'assignment' | 'on_demand' | 'automation';
  priority: number;
  coalesce: boolean;  // dedup rapid wakeups
}
```

**How It Maps to Gluon:**

Create an optional `Agent` model (if not already managing workspace-per-agent):

```python
# models.py addition
class Agent(BaseModel):
    """Persistent agent identity within a workspace."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str  # FK to workspace
    name: str
    description: str | None = None
    role: str = "worker"  # e.g., "researcher", "engineer", "reviewer"
    created_at: datetime = Field(default_factory=utc_now)
    last_active_at: datetime | None = None
    
    # Supervisor/delegation
    supervisor_agent_id: str | None = None  # org chart
    
    # Budget & concurrency
    monthly_budget_usd: float | None = None
    spent_this_month_usd: float = 0.0
    max_concurrent_runs: int = 1
    is_active: bool = True

# Add to store schema:
# - agents table (id, workspace_id, name, role, supervisor_agent_id, budget, etc.)
# - Indexes on (workspace_id, name), (workspace_id, is_active)
```

**Acceptance Criteria:**
- Each workspace can have multiple agents
- Agents have stable identities across sessions
- Agent budget enforcement prevents overspend
- Agent supervisor relationships enable delegation (phase 2)

**Priority Rationale:** Enables "agent-per-workspace" vision; prerequisite for heartbeats.

---

### Pattern 2: Heartbeat System (Priority: HIGH, Effort: MEDIUM-HIGH)

**What It Is:**
Agents are periodically "woken up" by the system on a schedule. The wakeup is not tied to user action; rather, the agent autonomously assesses the state of its assigned work and decides what to do. Wakeups can be coalesced (deduped) if multiple triggers fire rapidly.

**How Paperclip Implements It:**

```typescript
// Database
interface HeartbeatRun {
  id: string;
  agentId: string;
  scheduledFor: timestamp;
  firedAt: timestamp | null;
  source: 'timer' | 'assignment' | 'webhook';
  status: 'pending' | 'running' | 'completed' | 'failed';
  result: any;
}

// Coalescing: dedup rapid wakeups
interface AgentWakeupRequest {
  agentId: string;
  source: 'timer' | 'assignment' | 'automation';
  coalesce: boolean;
  coalesceTtlMs: number;
}

// In scheduler:
// 1. Timer fires every N minutes
// 2. Check for pending assignments or scheduled work
// 3. Emit AgentWakeupRequest with coalesce=true
// 4. Dedup handler: if agent already waking up within TTL, skip
// 5. Fire heartbeat run with full agent context
// 6. Agent processes task, updates state, posts result
```

**How It Maps to Gluon:**

Add a scheduling layer using APScheduler (lightweight, built-in):

```python
# models.py addition
class AgentSchedule(BaseModel):
    """Periodic work schedule for an agent."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str  # FK to agents
    project_id: str | None = None  # If None, agent checks all projects
    prompt_template: str  # e.g., "Check for new work in {{project}} and assess status"
    schedule_type: str = "cron"  # "cron", "interval", "once"
    schedule_spec: str  # e.g., "0 * * * *" (hourly), "*/15 * * * *" (every 15 min)
    is_enabled: bool = True
    last_fired_at: datetime | None = None
    next_fire_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

class HeartbeatRun(BaseModel):
    """Record of a scheduled agent wakeup."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str  # FK to agents
    schedule_id: str  # FK to agent_schedules
    execution_run_id: str | None = None  # FK to execution_runs (if spawned)
    fired_at: datetime
    status: str = "pending"  # pending, running, completed, failed
    result: dict[str, Any] | None = None
    error_message: str | None = None
    completed_at: datetime | None = None

# Scheduler daemon (new file: scheduler.py)
class HeartbeatScheduler:
    """Manages periodic agent wakeups."""
    
    def __init__(self, store: GluonStore, agent: GluonAgent):
        self.store = store
        self.agent = agent
        self.scheduler = BackgroundScheduler()
    
    def register_schedule(self, agent_schedule: AgentSchedule) -> None:
        """Register a new scheduled wakeup."""
        # Parse schedule_spec (cron or interval)
        # Register with APScheduler
        # On fire: emit heartbeat, check for coalesce, spawn ExecutionRun if needed
    
    async def fire_heartbeat(self, schedule_id: str) -> HeartbeatRun:
        """Fire a heartbeat: assess agent state, decide on work."""
        schedule = self.store.get_agent_schedule(schedule_id)
        heartbeat = HeartbeatRun(
            agent_id=schedule.agent_id,
            schedule_id=schedule_id,
            fired_at=utc_now()
        )
        
        # Render prompt template
        prompt = self._render_prompt(schedule.prompt_template, schedule)
        
        # Check for ongoing runs (coalesce: don't spawn if already running)
        ongoing = self.store.get_runs_by_agent(schedule.agent_id, status=RunStatus.RUNNING)
        if ongoing and len(ongoing) >= agent.max_concurrent_runs:
            heartbeat.status = "coalesced"
            return self.store.create_heartbeat_run(heartbeat)
        
        # Spawn execution run
        run = ExecutionRun(
            project_id=schedule.project_id or agent.workspace_projects[0].id,
            prompt=prompt,
            initiator=f"heartbeat:{schedule_id}"
        )
        heartbeat.execution_run_id = run.id
        heartbeat.status = "running"
        self.store.create_heartbeat_run(heartbeat)
        
        # Start run
        await self.runner.run_task(run, ...)
```

**Adding to Store:**

```python
# In store.py schema
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT DEFAULT 'worker',
    supervisor_agent_id TEXT,
    monthly_budget_usd REAL,
    spent_this_month_usd REAL DEFAULT 0.0,
    max_concurrent_runs INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    last_active_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_schedules (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    prompt_template TEXT NOT NULL,
    schedule_type TEXT DEFAULT 'cron',
    schedule_spec TEXT NOT NULL,
    is_enabled INTEGER DEFAULT 1,
    last_fired_at TEXT,
    next_fire_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeat_runs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    schedule_id TEXT NOT NULL REFERENCES agent_schedules(id),
    execution_run_id TEXT REFERENCES execution_runs(id),
    fired_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result TEXT,  -- JSON
    error_message TEXT,
    completed_at TEXT
);
```

**CLI Commands:**

```bash
# Create an agent
gluon agent create myworkspace myagent --role researcher

# Set up schedule (wakeup every 15 minutes)
gluon agent schedule myagent --project myproject \
  --schedule "*/15 * * * *" \
  --prompt "Review recent issues and pick next task"

# View heartbeat history
gluon agent heartbeats myagent --limit 20

# Manual wakeup (test)
gluon agent wakeup myagent
```

**Acceptance Criteria:**
- Agents wake on schedule without user intervention
- Heartbeats are coalesced (no duplicate runs within TTL)
- Heartbeat prompt can reference project/agent context
- Heartbeat spawn an ExecutionRun with full tracking
- Coalesce respects max_concurrent_runs limit
- Manual wakeup works for testing

**Priority Rationale:** Core enabler of autonomous agent behavior; aligns with user's directional vision of agents checking in periodically.

---

### Pattern 3: Task/Issue Tracking at Orchestrator Layer (Priority: MEDIUM, Effort: MEDIUM)

**What It Is:**
The orchestrator maintains its own task tracker (not GitHub-specific). Tasks/issues have:
- Workflow state: backlog → assigned → in_progress → review → done
- Assignment to agents (with inbox concept)
- Priority, labels, descriptions, comments
- Atomic checkout to prevent double-work
- Audit trail (who assigned, when, outcome)

**How Paperclip Implements It:**

```typescript
interface Issue {
  id: string;
  title: string;
  description: string;
  status: 'backlog' | 'todo' | 'in_progress' | 'in_review' | 'done';
  priority: 'critical' | 'high' | 'medium' | 'low';
  assigneeAgentId: string | null;
  reporterAgentId: string;  // who created it
  parentId: string | null;   // hierarchical issues
  labels: string[];
  
  // Atomic checkout
  executionLockedAt: timestamp | null;
  checkoutRunId: string | null;
  
  // Audit
  createdAt: timestamp;
  updatedAt: timestamp;
  closedAt: timestamp | null;
}

interface IssueComment {
  id: string;
  issueId: string;
  authorAgentId: string;
  text: string;
  createdAt: timestamp;
}

// Atomic checkout workflow
async function checkoutIssue(issue: Issue, agentId: string): Promise<Issue> {
  // BEGIN TRANSACTION
  const updated = await db.transaction(async (tx) => {
    // Verify not locked
    if (issue.executionLockedAt && 
        Date.now() - issue.executionLockedAt < LOCK_TTL) {
      throw new IssueLockedError();
    }
    
    // Atomic lock: set executionLockedAt + checkoutRunId
    return await tx.issues.update(issue.id, {
      executionLockedAt: Date.now(),
      checkoutRunId: runId,
      assigneeAgentId: agentId,
      status: 'in_progress'
    });
  });
  // END TRANSACTION
  return updated;
}

// Release lock
async function releaseIssue(issue: Issue, newStatus: string): Promise<void> {
  await db.issues.update(issue.id, {
    status: newStatus,
    executionLockedAt: null,
    checkoutRunId: null,
    closedAt: newStatus === 'done' ? Date.now() : null
  });
}
```

**How It Maps to Gluon:**

Add lightweight task tracking (no-frills):

```python
# models.py addition
class TaskStatus(str, Enum):
    """Status of a tracked task."""
    BACKLOG = "backlog"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"

class OrchestratorTask(BaseModel):
    """Task tracked at the orchestrator layer."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str  # FK to projects
    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.BACKLOG
    priority: int = 5  # 1-10, 10 = highest
    assigned_to_agent_id: str | None = None  # FK to agents
    created_by: str  # "system", agent_id, or "cli"
    
    # Atomic checkout
    execution_locked_at: datetime | None = None
    execution_run_id: str | None = None  # FK to execution_runs
    
    # Audit
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    parent_task_id: str | None = None  # Hierarchical

class TaskComment(BaseModel):
    """Comment on a task (agent-to-agent communication)."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str  # FK to tasks
    author_agent_id: str | None = None  # Can be agent or system
    content: str
    created_at: datetime = Field(default_factory=utc_now)

# Store methods
class GluonStore:
    def create_task(self, task: OrchestratorTask) -> OrchestratorTask: ...
    
    def checkout_task(self, task_id: str, agent_id: str, run_id: str) -> OrchestratorTask:
        """Atomically lock a task for execution. Raises TaskLockedError if already locked."""
        with self._get_conn() as conn:
            # Verify not locked by someone else
            task = self.get_task(task_id)
            if task.execution_locked_at:
                age = (utc_now() - task.execution_locked_at).total_seconds()
                if age < LOCK_TTL_SECONDS:
                    raise TaskLockedError(f"Task locked by {task.execution_run_id}")
            
            # Atomic update
            conn.execute("""
                UPDATE tasks
                SET execution_locked_at = ?, execution_run_id = ?,
                    assigned_to_agent_id = ?, status = ?, updated_at = ?
                WHERE id = ?
            """, (utc_now().isoformat(), run_id, agent_id, TaskStatus.IN_PROGRESS, utc_now().isoformat(), task_id))
        
        return self.get_task(task_id)
    
    def release_task(self, task_id: str, new_status: TaskStatus) -> OrchestratorTask:
        """Release lock and update status."""
        completed_at = utc_now() if new_status == TaskStatus.DONE else None
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE tasks
                SET status = ?, execution_locked_at = NULL, execution_run_id = NULL,
                    completed_at = ?, updated_at = ?
                WHERE id = ?
            """, (new_status, completed_at, utc_now().isoformat(), task_id))
        return self.get_task(task_id)
    
    def get_agent_inbox(self, agent_id: str) -> list[OrchestratorTask]:
        """Get all tasks assigned to agent (status = ASSIGNED)."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks
                WHERE assigned_to_agent_id = ? AND status = ?
                ORDER BY priority DESC, created_at ASC
            """, (agent_id, TaskStatus.ASSIGNED.value)).fetchall()
        return [self._row_to_task(row) for row in rows]

# CLI commands
@app.command("task")
def task_command(action: str, project: str, title: str = None, ...):
    """Manage orchestrator tasks."""
    orchestrator = get_orchestrator()
    
    if action == "create":
        task = OrchestratorTask(
            project_id=orchestrator.get_project(project).id,
            title=title,
            created_by="cli"
        )
        task = orchestrator.store.create_task(task)
        typer.echo(f"Task created: {task.id} - {task.title}")
    
    elif action == "assign":
        orchestrator.store.update_task(task_id, {
            'assigned_to_agent_id': agent_id,
            'status': TaskStatus.ASSIGNED
        })
        typer.echo(f"Task assigned to {agent_id}")
    
    elif action == "inbox":
        # Show inbox for an agent
        inbox = orchestrator.store.get_agent_inbox(agent_id)
        for task in inbox:
            typer.echo(f"  [{task.priority}] {task.title}")

# Integration with ExecutionRun: when starting a task-tracked run
async def execute_task(task_id: str, agent_id: str) -> ExecutionRun:
    """Checkout a task and start execution."""
    orchestrator = get_orchestrator()
    task = orchestrator.store.checkout_task(task_id, agent_id, run_id)
    
    # Create execution run linked to task
    run = ExecutionRun(
        project_id=task.project_id,
        prompt=f"Complete task: {task.title}\n\n{task.description}",
        initiator=f"task:{task_id}"
    )
    run = orchestrator.store.create_execution_run(run)
    task = orchestrator.store.update_task(task_id, {'execution_run_id': run.id})
    
    # On completion: release task
    result = await orchestrator.run_execution(run)
    orchestrator.store.release_task(task_id, TaskStatus.DONE)
    return run
```

**Acceptance Criteria:**
- Tasks can be created, assigned, and tracked
- Atomic checkout prevents double-execution
- Lock timeout releases stale locks
- Agent inbox shows assigned tasks
- Task comments enable agent-to-agent communication
- Task completion marks as DONE and releases lock

**Priority Rationale:** Enables task queuing and agent coordination; informs "task/issue tracking at orchestrator layer" vision. Medium priority because heartbeats are more critical for autonomy.

---

### Pattern 4: Cost Attribution & Budget Enforcement (Priority: MEDIUM, Effort: LOW)

**What It Is:**
Track costs at the agent level, aggregate by month, and enforce hard caps. Agents cannot execute if budget exceeded.

**How Paperclip Implements It:**

```typescript
interface CostEvent {
  id: string;
  agentId: string;
  runId: string;
  provider: 'claude' | 'gpt4' | 'palm';
  model: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  createdAt: timestamp;
}

// Budget enforcement
async function beforeAgentWakeup(agent: Agent): Promise<void> {
  const thisMonth = getCurrentMonth();
  const spent = await db.costEvents.sumByAgentAndMonth(agent.id, thisMonth);
  
  if (spent >= agent.budget.monthly) {
    agent.status = 'paused';
    await db.agents.update(agent.id, { status: 'paused' });
    throw new BudgetExceededError(`Agent ${agent.id} exceeded monthly budget`);
  }
}
```

**How It Maps to Gluon:**

Gluon already tracks `cost_usd` per ExecutionRun and per Session. Enhance with agent-level enforcement:

```python
# In core.py Orchestrator
async def execute(self, agent_id: str, project_id: str, prompt: str) -> AsyncIterator[AgentMessage]:
    """Execute with budget check."""
    agent = self.store.get_agent(agent_id)
    
    # Check monthly budget
    this_month_start = utc_now().replace(day=1, hour=0, minute=0, second=0)
    spent_this_month = self.store.sum_agent_costs_since(agent_id, this_month_start)
    
    if agent.monthly_budget_usd and spent_this_month >= agent.monthly_budget_usd:
        raise BudgetExceededError(f"Agent {agent.name} budget exceeded for this month")
    
    # Check if agent would exceed budget with this run
    if agent.monthly_budget_usd:
        remaining = agent.monthly_budget_usd - spent_this_month
        # Warn if run might exceed (e.g., if >80% of remaining budget estimated)
        if remaining < 10.0:
            logger.warning(f"Agent {agent.name} has only ${remaining:.2f} remaining this month")
    
    # Execute (cost is tracked on ExecutionRun)
    run = ExecutionRun(
        project_id=project_id,
        prompt=prompt,
        initiator=f"agent:{agent_id}"
    )
    
    async for message in self.run_execution(run):
        yield message
    
    # Update agent's spent this month
    if run.cost_usd:
        spent_this_month += run.cost_usd
        # Optionally set agent to inactive if budget exceeded
        if agent.monthly_budget_usd and spent_this_month > agent.monthly_budget_usd:
            self.store.update_agent(agent_id, {'is_active': False})
            logger.warning(f"Agent {agent.name} paused: budget exceeded")

# Store enhancement
def sum_agent_costs_since(self, agent_id: str, since: datetime) -> float:
    """Sum all execution costs for agent since timestamp (usually month start)."""
    with self._get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0.0) as total
            FROM execution_runs
            WHERE project_id IN (
                SELECT id FROM projects WHERE workspace_id IN (
                    SELECT workspace_id FROM agents WHERE id = ?
                )
            )
            AND created_at >= ?
        """, (agent_id, since.isoformat())).fetchone()
    return row[0] if row else 0.0
```

**Acceptance Criteria:**
- Agents have monthly_budget_usd cap
- Costs are summed at agent level
- Execution is prevented if budget exceeded
- Warnings issued at 80% budget
- Agent marked inactive if budget exceeded

**Priority Rationale:** Practical cost control; lower effort. Useful for shared workspaces.

---

### Pattern 5: Config Versioning & Rollback (Priority: LOW, Effort: MEDIUM)

**What It Is:**
When agent config changes (e.g., role, budget, supervisor), store before/after snapshots. Enable rollback to previous config.

**How Paperclip Implements It:**

```typescript
interface AgentConfigVersion {
  id: string;
  agentId: string;
  configSnapshot: any;  // Full before/after
  change: 'role' | 'budget' | 'supervisor' | etc;
  changedBy: string;  // userId or agentId
  changedAt: timestamp;
  reason: string;
}

async function updateAgentConfig(agent: Agent, changes: Partial<Agent>, reason: string): Promise<Agent> {
  const before = JSON.parse(JSON.stringify(agent));
  
  // Create version record
  await db.configVersions.create({
    agentId: agent.id,
    configSnapshot: { before, after: { ...agent, ...changes } },
    change: Object.keys(changes)[0],
    changedBy: currentUserId,
    reason
  });
  
  // Apply changes
  return await db.agents.update(agent.id, changes);
}
```

**How It Maps to Gluon:**

Add optional config versioning to Agent model:

```python
class AgentConfigChange(BaseModel):
    """Record of an agent config change."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str
    before_snapshot: dict[str, Any]  # Full agent state before change
    after_snapshot: dict[str, Any]   # Full agent state after change
    changed_fields: list[str]  # e.g., ["monthly_budget_usd", "is_active"]
    changed_by: str  # "cli", "system", or agent_id
    reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

# Minimal implementation (schema only, no enforcement yet)
def log_agent_config_change(self, agent_id: str, before: dict, after: dict, changed_by: str, reason: str = None) -> AgentConfigChange:
    """Log a config change for audit trail."""
    change = AgentConfigChange(
        agent_id=agent_id,
        before_snapshot=before,
        after_snapshot=after,
        changed_fields=[k for k in after if after.get(k) != before.get(k)],
        changed_by=changed_by,
        reason=reason
    )
    # Store in DB
    return change

# CLI: rollback would require reading change history and applying oldest snapshot
@app.command("agent-config-history")
def agent_config_history(agent_name: str, limit: int = 10):
    """View config change history for an agent."""
    orchestrator = get_orchestrator()
    agent = orchestrator.store.get_agent_by_name(agent_name)
    changes = orchestrator.store.get_agent_config_changes(agent.id, limit=limit)
    
    for change in changes:
        typer.echo(f"{change.created_at} | {change.changed_by} | {','.join(change.changed_fields)}")
        if change.reason:
            typer.echo(f"  Reason: {change.reason}")
```

**Priority Rationale:** Useful for audit/compliance but lower priority than core features. Paperclip's full rollback system is overkill for Gluon; lightweight versioning sufficient.

---

## Agent-per-Workspace/Project Vision

**Direction:** Enable each workspace (or project) to have its own persistent agent identity that:
1. Maintains context across sessions
2. Can be scheduled to wake up periodically
3. Tracks its own budget and workload
4. Maintains an inbox of assigned tasks
5. Communicates with other agents via task comments

**Design (Phase 1):**

```python
# Workspace model enhanced:
class Workspace(BaseModel):
    ...
    agent_id: str | None = None  # Optional: this workspace's dedicated agent

# When creating workspace
workspace = Workspace(name="ml-research", ...)
agent = Agent(
    workspace_id=workspace.id,
    name=f"Agent:{workspace.name}",
    role="researcher"
)
# Agent becomes the "owner" of this workspace's work

# Agent schedule example:
# Every day at 10am, the workspace agent wakes up, surveys open issues,
# and decides what to work on
schedule = AgentSchedule(
    agent_id=agent.id,
    project_id=None,  # All projects in workspace
    prompt_template="""
    You are the agent responsible for workspace: {{workspace_name}}
    
    Review the current state:
    - {{open_issue_count}} open issues
    - {{active_run_count}} active runs
    - {{inbox_count}} tasks assigned to you
    
    What should you work on next? Start a new task if needed, or resume an existing one.
    """,
    schedule_spec="0 10 * * *"  # Daily at 10am
)

# Outcome: Agent runs daily, checks state, self-assigns work
```

**Benefit:** Agents become first-class citizens with observable behavior, not just process spawners.

---

## Heartbeat System Design Notes

**From Paperclip, What's Worth Adopting:**

1. **Coalescing (Dedup):** If multiple wakeup triggers fire within TTL, coalesce into one run. Prevents thundering herd.
   - Implementation: Check for ongoing runs before spawning new one
   - TTL: 5-10 minutes recommended

2. **Prompt Templates:** Wakeup prompt is a template with context substitution (workspace name, issue count, etc.)
   - Implementation: Use Jinja2 templates in prompt_template field
   - Context: agent, workspace, project, recent activity

3. **Coarse-Grained Responses:** Heartbeat run produces a decision/action, not a detailed execution
   - Example: "Check status" → "Found 3 urgent issues, starting work on issue #1"
   - Not: "Fix code, run tests, deploy" — that's a separate execution task

4. **Asymmetric Costs:** Heartbeat "check" is cheap (Haiku model); only spawns full run if needed
   - Recommendation: Use TaskProfile.QUICK for heartbeat runs

5. **Error Handling:** Heartbeat failure doesn't kill the agent. Circuit breaker: 3 failures → disable schedule temporarily.
   - Implementation: Track consecutive_failures per schedule; disable if >3

---

## Task/Issue Tracking at Orchestrator Layer

**From Paperclip, What's Worth Adopting:**

1. **Atomic Checkout:** Prevents two agents from claiming the same task
   - DB-level lock: `execution_locked_at + TTL`
   - Useful if multiple agents in same workspace

2. **Workflow States:** Simple 5-state machine (backlog → assigned → in_progress → review → done)
   - Atomic transitions: prevents stale updates
   - Audit: timestamp each transition

3. **Comment Thread:** Agents can post comments on tasks for async coordination
   - Useful: "Blocked on deployment, can you check?", "Ready for review"
   - Low friction vs opening new tickets

4. **Inbox Concept:** Agent has personal inbox of assigned tasks
   - Query: `SELECT * FROM tasks WHERE assigned_to = agent_id AND status = ASSIGNED`
   - Heartbeat can poll inbox: "Do I have new work?"

5. **Priority Ordering:** Tasks have priority (1-10). Inbox sorted by priority descending.
   - Guidance: Tasks created by heartbeat (auto-detected problems) start as "high", user-created as "medium"

---

## Patterns to Skip (and Why)

| Pattern | Paperclip Approach | Why Skip in Gluon | Alternative |
|---------|-------------------|-------------------|-------------|
| **Full Org Chart** | reportsTo, nested orgs, CEO board | Gluon has no governance layer; overkill for single-workspace use case | Simple supervisor link (optional) |
| **Approval Gates** | Board votes on CEO strategy, hiring workflows | Not applicable; Gluon doesn't manage hiring or strategy | Can add manual approval on task transition (future) |
| **Role-Based Permissions** | Company membership + principal-based access control | Gluon is simpler; transport handles auth | Keep transport-level auth only |
| **Multi-Company Multi-Tenancy** | Companies as top-level org unit | Gluon is single-user; no SaaS layer | Workspaces sufficient |
| **Live Event Streaming** | Full WebSocket pub/sub for all events | Overkill; Gluon's WebSocket is for streaming only | Keep streaming-only; add polling for status |
| **Agent API Keys + JWT** | Agents authenticate to adapters via JWT | Single-process orchestrator; no authentication boundary | Skip; unnecessary complexity |
| **Adapter Pattern** | Swappable LLM providers (claude_local, codex, http) | Gluon is Claude-only; adapter layer adds indirection | Keep monolithic Claude SDK usage |

---

## Summary Table

| Pattern | Paperclip Approach | Gluon Applicability | Priority | Effort | Status |
|---------|-------------------|-------------------|----------|--------|--------|
| **Persistent Agent Identity** | Agent model with role, org chart, budget | HIGH: Enables agent-per-workspace vision | HIGH | MEDIUM | Not Started |
| **Heartbeat/Scheduled Wakeups** | Timer + coalesce + AgentWakeupRequest queue | HIGH: Core autonomy feature | HIGH | MEDIUM-HIGH | Not Started |
| **Task/Issue Tracking** | Full tracker with workflow states + atomic checkout | MEDIUM: Enables coordination | MEDIUM | MEDIUM | Not Started |
| **Config Versioning** | Before/after snapshots + rollback | LOW: Audit trail only | LOW | MEDIUM | Not Started |
| **Cost Attribution & Budget** | Per-run CostEvent + monthly enforced cap | MEDIUM-HIGH: Practical cost control | MEDIUM | LOW | Partial (tracking exists, enforcement needed) |
| **Org Chart** | reportsTo hierarchy + CEO roles | LOW: Not applicable to single-agent-per-workspace | LOW | - | Skip |
| **Approval Gates** | Board-level decision workflows | NONE: No governance layer in Gluon | - | - | Skip |
| **Role-Based Access** | Company membership + JWT + principal access | LOW: Simpler to keep transport-level auth | LOW | - | Skip |
| **Live Event Pub/Sub** | WebSocket pub/sub for multi-client real-time | LOW: Can use polling instead | LOW | - | Skip |

---

## Implementation Roadmap (Suggested Phases)

**Phase 1: Agent Identity & Heartbeats (3-4 weeks)**
- Add Agent model to schema
- Add AgentSchedule + HeartbeatRun models
- Implement HeartbeatScheduler (APScheduler-based)
- CLI commands: `gluon agent create/schedule/wakeup`
- Enable agent-per-workspace pattern

**Phase 2: Task Tracking (2-3 weeks)**
- Add OrchestratorTask + TaskComment models
- Implement atomic checkout + lock management
- CLI commands: `gluon task create/assign/inbox`
- Integrate with ExecutionRun (task-driven runs)

**Phase 3: Cost Enforcement (1 week)**
- Add enforcement logic to Orchestrator.execute()
- Agent pauses if budget exceeded
- CLI: `gluon agent budget <agent> <monthly_usd>`

**Phase 4: Polish & Config Versioning (1-2 weeks)**
- Add AgentConfigChange model + audit trail
- CLI: `gluon agent config-history`
- Rollback support (read-only, manual)
- Tests for all new patterns

---

## Critical Files for Implementation

1. **`src/gluon/models.py`** - Add Agent, AgentSchedule, HeartbeatRun, OrchestratorTask, TaskComment, AgentConfigChange models; update Workspace/Project schema
2. **`src/gluon/store.py`** - Add CRUD methods for all new models; implement atomic checkout logic; add budget queries; schema migrations for new tables
3. **`src/gluon/core.py`** - Integrate budget checks into Orchestrator.execute(); task checkout on run start; task release on completion
4. **`src/gluon/scheduler.py`** (new) - HeartbeatScheduler using APScheduler; coalesce logic; prompt template rendering
5. **`src/gluon/cli.py`** - Add commands: `gluon agent *`, `gluon task *`, `gluon schedule *`; heartbeat status/logs

---

This document provides a strategic, pattern-level comparison grounded in both systems' actual implementations. The patterns are surgical (not wholesale adoption), prioritized (HIGH/MEDIUM/LOW), and implementable in phases.
