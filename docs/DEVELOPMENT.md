# Development Guide

This guide helps future Claude Code sessions understand and extend Gluon Agent.

## Quick Start

```bash
# Clone and enter project
git clone https://github.com/carrotly-ai/gluon-agent.git
cd gluon-agent

# Create virtual environment and install
uv venv
uv pip install -e '.[dev]'

# Or use uv directly
uv run gluon --help
```

## Project Structure

```
gluon-agent/
├── src/gluon/
│   ├── __init__.py          # Package version
│   ├── models.py            # Pydantic models (Workspace, Project, Session, ExecutionRun, etc.)
│   ├── models_config.py     # Model tier configuration (Opus 4.6/4.5, Sonnet, Haiku)
│   ├── store.py             # SQLite persistence layer with auto-migrations
│   ├── agent.py             # Claude Agent SDK wrapper
│   ├── core.py              # Orchestrator (business logic)
│   ├── runner.py            # Background task execution & subprocess management
│   ├── cli.py               # Typer CLI commands
│   ├── commands.py          # Additional CLI command groups
│   ├── bot_core.py          # Transport-agnostic bot logic
│   ├── chat_agent.py        # NL interpreter with MCP tools
│   ├── git_manager.py       # Git synchronization & worktree operations
│   ├── worktree.py          # Git worktree lifecycle management
│   ├── image_storage.py     # Content-addressed image storage (SHA256 dedup)
│   ├── ralph_manager.py     # Ralph loop autonomous execution orchestrator
│   ├── circuit_breaker.py   # 3-state circuit breaker (CLOSED/HALF_OPEN/OPEN)
│   ├── completion_detector.py # RALPH_STATUS parsing & confidence scoring
│   ├── rate_limiter.py      # Hourly API call limits & cost caps
│   ├── resume_coordinator.py # Auto-resume polling for REVIEW tasks
│   ├── supervisor_daemon.py # Background supervision daemon
│   ├── policies.py          # Supervision policies (AGGRESSIVE/CONSERVATIVE/MANUAL)
│   ├── pr_monitor.py        # PR status monitoring
│   ├── cleanup.py           # Resource cleanup utilities
│   ├── files.py             # File operation helpers
│   ├── transport/           # Transport layer
│   │   ├── __init__.py      # Exports base classes
│   │   ├── base.py          # Transport ABC, Context, Response
│   │   ├── capabilities.py  # Platform capabilities
│   │   ├── telegram.py      # Telegram transport
│   │   └── discord.py       # Discord transport
│   ├── web/                 # Web dashboard backend
│   │   ├── __init__.py
│   │   ├── api.py           # FastAPI REST + WebSocket endpoints
│   │   ├── models.py        # Request/response Pydantic models
│   │   ├── websocket.py     # WebSocket connection manager
│   │   └── dist/            # Built React frontend (served by FastAPI)
│   ├── webhooks/            # External webhook handlers
│   │   ├── __init__.py
│   │   ├── base.py          # Webhook handler base
│   │   └── github.py        # GitHub webhook handler
│   └── queue/               # Task queue system
│       ├── __init__.py
│       └── redis_queue.py   # Redis-backed queue
├── web-ui/                  # React frontend (Vite + TypeScript)
│   ├── src/
│   │   ├── App.tsx          # Main app with routing
│   │   ├── main.tsx         # Entry point with PWA registration
│   │   ├── components/      # React components (Kanban, RunDetail, Settings, etc.)
│   │   ├── hooks/           # Custom hooks (useWebSocket, useOnline, etc.)
│   │   └── lib/             # API client, types, utilities
│   └── package.json
├── tests/
├── docs/                    # This documentation
├── pyproject.toml           # Dependencies
└── README.md                # User documentation
```

## Key Patterns

### 1. Adding New CLI Commands

Commands are organized in Typer apps. To add a new command:

```python
# In cli.py

# For a new subcommand group:
new_app = typer.Typer(help="Description")
app.add_typer(new_app, name="newcmd")

@new_app.command("action")
def new_action(
    arg: Annotated[str, typer.Argument(help="Argument description")],
    option: Annotated[bool, typer.Option("--flag", "-f", help="Option")] = False,
):
    """Command description."""
    orchestrator = get_orchestrator()
    # ... implementation
```

### 2. Adding New Models

Models use Pydantic for validation:

```python
# In models.py
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4

class NewModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    created_at: datetime = Field(default_factory=datetime.now)

    def model_post_init(self, __context: Any) -> None:
        """Post-initialization hook for validation."""
        pass
```

### 3. Adding New Store Methods

The store uses SQLite with row factories:

```python
# In store.py

def create_new_thing(self, name: str, ...) -> NewThing:
    """Create a new thing."""
    thing = NewThing(name=name, ...)
    with self._get_conn() as conn:
        conn.execute(
            """
            INSERT INTO new_things (id, name, created_at, ...)
            VALUES (?, ?, ?, ...)
            """,
            (thing.id, thing.name, thing.created_at.isoformat(), ...),
        )
    return thing

def _row_to_new_thing(self, row: sqlite3.Row) -> NewThing:
    """Convert database row to model."""
    return NewThing(
        id=row["id"],
        name=row["name"],
        created_at=datetime.fromisoformat(row["created_at"]),
        ...
    )
```

### 4. Adding Schema Migrations

Migrations are handled in `_init_db()`:

```python
# In store.py - add to MIGRATIONS list
MIGRATIONS = [
    """
    ALTER TABLE existing_table ADD COLUMN new_column TEXT;
    """,
]

# Migrations are run with error handling for already-applied changes
```

### 5. Adding MCP Tools to Chat Agent

Chat agent uses custom MCP tools for NL processing:

```python
# In chat_agent.py

@tool("new_tool", "Description of what it does", {
    "param1": str,  # Required parameters
    "param2": int,  # With types
})
async def new_tool(args: dict[str, Any]) -> dict[str, Any]:
    param1 = args.get("param1", "")
    param2 = args.get("param2", 0)

    # ... implementation

    return {"content": [{"type": "text", "text": "Result"}]}

# Add to allowed_tools list:
allowed_tools=[
    "mcp__gluon__new_tool",
    ...
]
```

### 6. Adding Bot Commands

Telegram bot commands follow this pattern:

```python
# In bot.py

async def new_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /newcmd command."""
    if not update.effective_user or not update.message:
        return

    if not self._is_authorized(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return

    # ... implementation
    await update.message.reply_text("Response", parse_mode="Markdown")

# Register in build_application():
self.app.add_handler(CommandHandler("newcmd", self.new_command))
```

### 7. Adding a New Transport

To add support for a new chat platform (e.g., Slack, Matrix):

```python
# In transport/slack.py (new file)

from gluon.transport.base import Transport, TransportContext, TransportResponse
from gluon.transport.capabilities import TransportCapabilities

# Define platform capabilities
SLACK_CAPS = TransportCapabilities(
    max_message_length=4000,
    supports_threads=True,
    supports_editing=True,
    supports_typing=True,
    supports_formatting=True,
)

class SlackTransport(Transport):
    def __init__(
        self,
        token: str,
        bot_core: GluonBotCore,
        allowed_users: list[str] | None = None,
    ):
        self.token = token
        self.bot_core = bot_core
        self._allowed_users = set(allowed_users) if allowed_users else None
        # Initialize Slack client...

    @property
    def name(self) -> str:
        return "slack"

    @property
    def capabilities(self) -> TransportCapabilities:
        return SLACK_CAPS

    async def send(self, ctx: TransportContext, response: TransportResponse) -> str:
        """Send message via Slack API."""
        text = self.truncate_text(response.text)
        # ... send via Slack client
        return message_ts  # Return message ID

    async def edit(self, ctx: TransportContext, message_id: str, response: TransportResponse) -> bool:
        """Edit message via Slack API."""
        # ... edit via Slack client
        return True

    async def send_typing(self, ctx: TransportContext) -> None:
        """Show typing indicator (if supported)."""
        pass

    async def start(self) -> None:
        """Start the Slack bot (websocket or events API)."""
        self.bot_core.recover_stale_runs("slack")
        await self.bot_core.git_manager.start_background_sync()
        # ... start Slack client

    async def stop(self) -> None:
        """Stop gracefully."""
        await self.bot_core.git_manager.stop_background_sync()
        # ... close Slack client

    def _make_context(self, event: SlackEvent) -> TransportContext:
        """Create TransportContext from Slack event."""
        return TransportContext(
            transport="slack",
            user_id=f"slack:{event.user}",
            chat_id=event.channel,
            thread_id=event.thread_ts,
            project_hint=self._resolve_project(event.channel),
            message_id=event.ts,
            raw_data={"event": event},
        )

    async def _handle_message(self, event: SlackEvent) -> None:
        """Handle incoming Slack messages."""
        ctx = self._make_context(event)

        # Check authorization
        if not self.is_authorized(ctx.user_id):
            return

        # Delegate to bot_core for task execution
        async def send_callback(ctx, response):
            return await self.send(ctx, response)

        # Use bot_core.execute_task() or process_natural_language()
        await self.bot_core.process_natural_language(ctx, event.text, send_callback)
```

**Key steps:**
1. Create `transport/slack.py` with `SlackTransport` class
2. Define `SLACK_CAPS` with platform capabilities
3. Implement all abstract methods from `Transport` ABC
4. Use `GluonBotCore` for shared logic (task execution, concurrency, NL processing)
5. Add CLI command in `cli.py` (e.g., `gluon slack`)
6. Add optional dependency in `pyproject.toml`
7. Export in `transport/__init__.py`

### 8. Adding Webhook Handlers

Webhook handlers process external events (e.g., GitHub PR events) and trigger tasks:

```python
# In webhooks/github.py

from gluon.webhooks.base import WebhookHandler, WebhookEvent

class GitHubWebhookHandler(WebhookHandler):
    def __init__(self, secret: str | None = None):
        self.secret = secret  # For signature validation

    @property
    def name(self) -> str:
        return "github"

    async def validate_signature(self, payload: bytes, signature: str) -> bool:
        """Validate GitHub webhook signature (HMAC-SHA256)."""
        if not self.secret:
            return False
        import hmac
        import hashlib
        expected = "sha256=" + hmac.new(
            self.secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def parse_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookEvent | None:
        """Parse GitHub webhook event."""
        # Return None to skip this event
        if event_type not in ("pull_request", "push"):
            return None

        repo = payload.get("repository", {}).get("name")
        if not repo:
            return None

        prompt = self.generate_prompt(event_type, payload)
        return WebhookEvent(
            handler="github",
            event_type=event_type,
            project_hint=repo,
            prompt=prompt,
            source_ref=payload.get("pull_request", {}).get("head", {}).get("ref"),
            author=payload.get("sender", {}).get("login"),
            url=payload.get("pull_request", {}).get("html_url")
                or payload.get("head_commit", {}).get("url"),
        )

    def _default_prompt(self, event_type: str, payload: dict[str, Any]) -> str:
        """Generate default prompt based on event type."""
        if event_type == "pull_request":
            action = payload.get("action")
            return f"GitHub PR {action}: {payload.get('pull_request', {}).get('title', 'Untitled')}"
        elif event_type == "push":
            return f"New commits on {payload.get('ref', 'main')}: {payload.get('head_commit', {}).get('message', '')}"
        return "GitHub webhook event"

    def _get_repo_name(self, payload: dict[str, Any]) -> str | None:
        return payload.get("repository", {}).get("name")

    def _get_branch(self, payload: dict[str, Any]) -> str | None:
        return payload.get("pull_request", {}).get("head", {}).get("ref")

    def _get_author(self, payload: dict[str, Any]) -> str | None:
        return payload.get("sender", {}).get("login")

    def _get_title(self, payload: dict[str, Any]) -> str | None:
        return payload.get("pull_request", {}).get("title")

    def _get_body(self, payload: dict[str, Any]) -> str | None:
        return payload.get("pull_request", {}).get("body")

    def _get_url(self, payload: dict[str, Any]) -> str | None:
        return payload.get("pull_request", {}).get("html_url") \
            or payload.get("head_commit", {}).get("url")
```

**Key steps:**
1. Create handler subclass from `WebhookHandler` in `webhooks/`
2. Implement all abstract methods (validation, parsing, extraction)
3. Use `generate_prompt()` for flexible prompt templates
4. Return `WebhookEvent | None` (None to skip)
5. Register in web API at `/webhooks/{handler_name}`

### 9. Using Model Configuration

Select models for different task complexities using `models_config.py`:

```python
from gluon.models_config import ModelTier, get_model_id, describe_models

# Get model ID for a specific tier
model_id = get_model_id("opus-4.6")  # or ModelTier.OPUS_46
model_id = get_model_id("sonnet")    # Sonnet 4.5
model_id = get_model_id("haiku")     # Haiku 4.5

# Handle UI names (from web dashboard)
model_id = get_model_id("claude-opus-4.6")  # Resolves to opus-4.6 tier
model_id = get_model_id("claude-haiku-4.5")

# Full model IDs passed through unchanged
model_id = get_model_id("global.anthropic.claude-opus-4-6-v1")

# Show available models
print(describe_models())
```

Available tiers:
- `opus-4.6` - Latest Opus (most capable, default for complex tasks)
- `opus-4.5` - Previous Opus generation
- `sonnet` - Balanced performance (default for general tasks)
- `haiku` - Fast and lightweight (for simple tasks)

### 10. Managing Log Cleanup and Retention

Configure log retention policies via `LogCleanupService`:

```python
from gluon.cleanup import LogCleanupService

# Initialize service
cleanup = LogCleanupService(store, log_dir=Path.home() / ".gluon" / "logs")

# Run cleanup (removes logs based on run status and retention policy)
removed_count, freed_bytes = cleanup.run()

# Analyze disk usage
stats = cleanup.get_disk_usage()
print(f"Total: {stats['total_bytes']} bytes across {stats['run_count']} runs")
for run_id, bytes_used in stats['top_runs'][:5]:
    print(f"  {run_id}: {bytes_used} bytes")
```

**Retention policies:**
- **Orphan logs** (no DB record): Deleted immediately
- **Archived runs**: Logs deleted 30 days after completion
- **Failed runs**: Logs deleted 7 days after completion
- **Completed runs** (non-archived): Logs deleted 30 days after completion

### 11. Implementing Supervision Policies

Control auto-resume behavior with supervision policies:

```python
from gluon.policies import evaluate_policy, PolicyContext

# Create context with run state
ctx = PolicyContext(
    run=execution_run,
    circuit_state=current_circuit_state,
    calls_this_hour=42,
    max_calls_per_hour=100,
    total_cost_usd=15.50,
    max_cost_usd=50.0,
    completion_confidence=0.75,
    now=datetime.now(UTC),
)

# Evaluate policy
decision = evaluate_policy(ctx)
if decision.should_resume:
    print(f"Resume: {decision.reason}")
    if decision.wait_seconds > 0:
        await asyncio.sleep(decision.wait_seconds)
else:
    print(f"Block: {decision.reason}")
```

**Policies** (in `models.py` `SupervisionPolicy` enum):
- `AGGRESSIVE` - Resume on any significant progress
- `CONSERVATIVE` - Resume only if strong confidence and safe cost
- `MANUAL` - Require explicit user confirmation

### 12. Using the Job Queue (Redis)

Distribute tasks across workers with Redis queue:

```python
from gluon.queue.redis_queue import RedisJobQueue

# Initialize and connect
queue = RedisJobQueue(redis_url="redis://localhost:6379/0")
await queue.connect()

# Enqueue a job
job_id = await queue.enqueue(
    prompt="Analyze this codebase",
    project_id="my-project",
    priority=10,
    metadata={"user_id": "user123"},
)

# Dequeue and execute (worker-side)
job = await queue.dequeue(worker_id="worker1", timeout=5)
if job:
    # Process job...
    await queue.mark_complete(job.id, result={"status": "done"})

# Listen for job updates
def on_update(update: dict):
    print(f"Job {update['job_id']}: {update['status']}")

await queue.subscribe_updates(on_update)
```

## Testing

### Running Tests

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_store.py

# With coverage
uv run pytest --cov=src/gluon

# Verbose output
uv run pytest -v
```

### Testing Patterns

```python
# tests/test_new_feature.py
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
import pytest
from gluon.store import GluonStore
from gluon.core import Orchestrator
from gluon.models import Workspace, Project, Session

@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path

@pytest.fixture
def store(temp_db):
    """Create store with temp database."""
    return GluonStore(db_path=temp_db)

@pytest.fixture
def orchestrator(store):
    """Create orchestrator with temp store."""
    return Orchestrator(store)

# Synchronous test
def test_create_project(store):
    """Test project creation."""
    # Arrange
    workspace = store.create_workspace(path=Path("/tmp/workspace"))

    # Act
    project = store.create_project(
        workspace_id=workspace.id,
        name="test-project",
        path=Path("/tmp/workspace/test-project"),
    )

    # Assert
    assert project.name == "test-project"
    assert project.workspace_id == workspace.id
    retrieved = store.get_project(project.id)
    assert retrieved.id == project.id

# Async test
@pytest.mark.asyncio
async def test_execute_task(orchestrator):
    """Test async task execution."""
    # Arrange
    workspace = orchestrator.store.create_workspace(path=Path("/tmp/ws"))
    project = orchestrator.store.create_project(
        workspace_id=workspace.id,
        name="async-test",
        path=Path("/tmp/ws/async-test"),
    )

    # Act
    run = orchestrator.create_session(
        project_name="async-test",
        prompt="Echo: hello",
    )

    # Assert
    assert run.status.value in ("pending", "active")

# Parametrized tests
@pytest.mark.parametrize("model,expected", [
    ("opus-4.6", "global.anthropic.claude-opus-4-6-v1"),
    ("sonnet", "global.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    ("haiku", "global.anthropic.claude-haiku-4-5-20251001-v1:0"),
])
def test_model_resolution(model, expected):
    """Test model ID resolution."""
    from gluon.models_config import get_model_id
    assert get_model_id(model) == expected
```

**Testing best practices:**
- Use `pytest` fixtures for setup/teardown
- Create fresh database per test with `temp_db`
- Mark async tests with `@pytest.mark.asyncio`
- Use parametrize for testing multiple inputs
- Test error paths, not just happy paths
- Mock external dependencies (Claude SDK, Redis, etc.)

## Common Development Tasks

### Adding a New Entity (e.g., "Task Queue")

1. **Define Model** (`models.py`):
   ```python
   class Task(BaseModel):
       id: str = Field(default_factory=lambda: str(uuid4()))
       project_id: str
       prompt: str
       status: TaskStatus
       ...
   ```

2. **Add Store Methods** (`store.py`):
   - Update schema in `_init_db()`
   - Add `create_task()`, `get_task()`, `list_tasks()`, etc.
   - Add `_row_to_task()` converter

3. **Add Orchestrator Methods** (`core.py`):
   ```python
   def queue_task(self, project_name: str, prompt: str) -> Task:
       ...
   def process_queue(self) -> AsyncIterator[...]:
       ...
   ```

4. **Add CLI Commands** (`cli.py`):
   ```python
   @app.command("queue")
   def queue_task(...):
       ...
   ```

5. **Add Bot Commands** (`bot.py`):
   ```python
   async def queue(self, update, context):
       ...
   ```

6. **Add MCP Tools** (`chat_agent.py`):
   ```python
   @tool("queue_task", ..., {...})
   async def queue_task(args):
       ...
   ```

7. **Write Tests** (`tests/test_task.py`)

### Modifying Agent Behavior

To change how Claude Code is invoked:

```python
# In agent.py - modify GluonAgent class

def __init__(self, ...):
    self.model = model  # e.g., "sonnet", "opus"
    self.allowed_tools = allowed_tools  # Tool permissions
    self.permission_mode = permission_mode  # "acceptEdits", etc.

def _build_options(self, ...):
    # Modify ClaudeAgentOptions here
    options = ClaudeAgentOptions(
        cwd=working_dir,
        allowed_tools=self.allowed_tools,
        permission_mode=self.permission_mode,
        model=self.model,
        # Add new options:
        max_turns=10,
        system_prompt="Custom instructions...",
    )
```

### Adding New Workspace Features

To enhance workspace scanning:

```python
# In models.py - Workspace class

def scan_for_projects(self) -> list[Path]:
    """Customize project detection logic."""
    projects = []

    # Modify scan depth
    for depth in range(self.scan_depth):
        # Scan at each depth level
        ...

    # Add custom markers
    custom_markers = ["custom.config", "myproject.yaml"]

    return projects
```

### Integrating with Git Manager

For operations that involve git workflows:

```python
from gluon.git_manager import GitManager
from gluon.worktree import WorktreeManager

# Initialize managers
git_mgr = GitManager(project_path)
wt_mgr = WorktreeManager(project_path)

# Create isolated worktree for a run
worktree_path = await wt_mgr.create_worktree(
    base_branch="main",
    worktree_id="run-123"
)

# Sync from remote
await git_mgr.start_background_sync()
status = await git_mgr.fetch_all()

# Cleanup on completion
await wt_mgr.cleanup_worktree(worktree_id="run-123")
```

### Working with Images and Attachments

Handle image storage and retrieval:

```python
from gluon.image_storage import ImageStorage

# Initialize storage
storage = ImageStorage(base_dir=Path.home() / ".gluon" / "images")

# Store image (auto SHA256 deduplication)
image_id = storage.store_image(image_bytes)

# Retrieve image
image_bytes = storage.get_image(image_id)

# Attach to run
run.add_image_attachment(image_id, label="screenshot")
```

### Using Circuit Breaker for Safety

Protect against runaway tasks with circuit breaker:

```python
from gluon.circuit_breaker import CircuitBreaker, CircuitState

breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout_seconds=300,
    half_open_max_calls=1,
)

# Check state before executing
if breaker.state == CircuitState.OPEN:
    raise Exception("Circuit breaker is open - task blocked")

# Record result
if task_failed:
    breaker.record_failure()
else:
    breaker.record_success()
```

## Debugging Tips

### Check Database Contents

```bash
# Open SQLite database
sqlite3 ~/.gluon/gluon.db

# Useful queries:
.tables
.schema projects
SELECT * FROM projects;
SELECT * FROM sessions WHERE status = 'active';
SELECT * FROM workspaces;
SELECT * FROM execution_runs WHERE status = 'REVIEW' OR status = 'FAILED';
SELECT * FROM ralph_iterations WHERE run_id = 'run-abc123';

# Check disk usage
SELECT COUNT(*) as log_count FROM execution_runs;
SELECT AVG(cost_usd) as avg_cost FROM execution_runs WHERE status = 'COMPLETED';
```

### Reset Database

```bash
# Delete database to start fresh
rm ~/.gluon/gluon.db

# Re-run any gluon command to recreate
uv run gluon status
```

### Debug Claude Agent Execution

```python
# Add logging in agent.py or chat_agent.py
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Or inspect messages via CLI:
uv run gluon logs <run-id> -s messages  # View structured JSON messages
uv run gluon logs <run-id> -f           # Follow in real-time

# Check run details
uv run gluon runs --verbose
uv run gluon runs <run-id> --detail
```

### Inspect Worktrees

```bash
# Check created worktrees
cd ~/.gluon/worktrees
git worktree list

# Manual cleanup if needed
git worktree prune
```

### Redis Queue Debugging

```bash
# Connect to Redis
redis-cli -u redis://localhost:6379/0

# Inspect queue state
ZRANGE gluon:jobs:queue 0 -1 WITHSCORES  # View job queue
SMEMBERS gluon:worker:worker1:jobs        # Jobs assigned to worker
HGETALL gluon:job:job-abc123              # Job details

# Monitor updates
SUBSCRIBE gluon:jobs:updates
```

### Test Telegram Bot Locally

```bash
# Set environment variables
export GLUON_TELEGRAM_TOKEN="your-test-token"
export GLUON_TELEGRAM_USERS="your-user-id"

# Run with debug output
PYTHONASYNCDEBUG=1 uv run gluon bot

# Check bot logs
tail -f ~/.gluon/logs/*/stdout.log
```

### Monitor Web Dashboard

```bash
# Start web server
uv run gluon web

# View API logs
tail -f ~/.gluon/supervisor.log

# Test endpoints
curl http://localhost:8000/api/projects
curl http://localhost:8000/api/runs?status=ACTIVE
```

### Check Supervisor Daemon

```bash
# Check if running
uv run gluon supervisor status

# View daemon logs
tail -f ~/.gluon/supervisor.log

# Kill and restart
uv run gluon supervisor stop
uv run gluon supervisor start --foreground  # Run in foreground for debugging
```

### Inspect Completion Detection

```python
from gluon.completion_detector import parse_ralph_status

# Test status parsing
status = parse_ralph_status("RALPH_STATUS: COMPLETE with high confidence")
print(f"Complete: {status.is_complete}, Confidence: {status.confidence}")
```

## Advanced Patterns

### RALPH Loop (Autonomous Execution)

For autonomous multi-turn task execution:

```python
from gluon.ralph_manager import RalphLoopManager

# Initialize RALPH manager
ralph = RalphLoopManager(
    orchestrator=orchestrator,
    max_iterations=10,
    max_cost_per_loop=10.0,
)

# Run autonomous loop
iterations = await ralph.run(
    project_id="my-project",
    initial_prompt="Implement feature X",
    supervision_config=SupervisionConfig(
        policy=SupervisionPolicy.AGGRESSIVE,
        auto_resume=True,
    ),
)

# Access iteration results
for iteration in iterations:
    print(f"Iteration {iteration.number}:")
    print(f"  Status: {iteration.status}")
    print(f"  Cost: ${iteration.cost_usd}")
    print(f"  Output: {iteration.output}")
```

### Rate Limiting and Cost Controls

Prevent runaway costs with rate limiting:

```python
from gluon.rate_limiter import RateLimiter

limiter = RateLimiter(
    max_calls_per_hour=100,
    max_cost_per_hour=50.0,
)

# Check before executing
remaining = limiter.calls_remaining_this_hour()
if remaining <= 0:
    raise Exception("Rate limit exceeded")

# Record cost after execution
limiter.record_call(cost_usd=1.50)

# Get usage stats
stats = limiter.get_hourly_stats()
print(f"Used: {stats['calls']} calls, ${stats['cost']:.2f}")
```

### Resume Coordination

Auto-resume REVIEW tasks with smart polling:

```python
from gluon.resume_coordinator import ResumeCoordinator

coordinator = ResumeCoordinator(
    store=store,
    check_interval_seconds=10,
    policy_engine=policy_engine,
)

# Start background polling
coordinator.start()

# Manually trigger review resolution (after user approves in UI)
await coordinator.resolve_review(
    run_id="run-abc123",
    action="RESUME",  # or "CANCEL"
    user_notes="Looks good, proceed",
)

# Stop polling
coordinator.stop()
```

### PR Monitoring and Status Updates

Track PR status for run completion:

```python
from gluon.pr_monitor import PRMonitor

monitor = PRMonitor(orchestrator=orchestrator)

# Register PR for monitoring
await monitor.watch_pr(
    run_id="run-abc123",
    repo_owner="carrotly-ai",
    repo_name="gluon-agent",
    pr_number=42,
)

# Check status periodically
status = await monitor.check_pr_status(run_id="run-abc123")
print(f"PR status: {status.state}")  # OPEN, CLOSED, MERGED

# Webhook will auto-update run status on PR events
```

### Distributed Task Execution with Supervisor

Run tasks in background with automatic supervision:

```bash
# Start supervisor daemon (runs in background)
uv run gluon supervisor start

# Submit task for background execution
uv run gluon run myproject "fix the bug" --background

# Supervisor auto-resumes REVIEW tasks based on policy
# View status in UI or CLI
uv run gluon runs --active

# Supervisor logs
tail -f ~/.gluon/supervisor.log
```

## Code Style

- **Formatting**: Uses `ruff format`
- **Linting**: Uses `ruff check`
- **Type Checking**: Uses `mypy`
- **Python Version**: 3.12+

```bash
# Format code
uv run ruff format .

# Check linting
uv run ruff check .

# Type check
uv run mypy src/gluon
```

## Dependencies

Core dependencies and their purposes:

| Package | Purpose |
|---------|---------|
| `claude-agent-sdk` | Claude Code integration |
| `typer` | CLI framework |
| `rich` | Beautiful terminal output |
| `pydantic` | Data validation |
| `python-telegram-bot` | Telegram bot transport |
| `python-dotenv` | Environment variables |
| `anyio` | Async runtime |
| `redis[hiredis]` | Job queue and task distribution |

**Optional Dependencies:**
```bash
# Install with Discord support
pip install 'gluon-agent[discord]'

# Install with web dashboard
pip install 'gluon-agent[web]'

# Install with all features
pip install 'gluon-agent[all]'
```

**Web dashboard** requires:
- `fastapi>=0.115.0` - REST API and WebSocket
- `uvicorn[standard]>=0.32.0` - ASGI server

## Environment Setup

### Required
- Python 3.12+
- Claude Code CLI installed and authenticated
- `uv` package manager
- AWS credentials for Bedrock (for Claude models)
- Git with configured user.name and user.email

### For Telegram Bot
- Telegram bot token from @BotFather
- User IDs for access control
- Environment variables:
  - `GLUON_TELEGRAM_TOKEN` - Bot token
  - `GLUON_TELEGRAM_USERS` - Comma-separated allowed user IDs

### For Discord Bot
- Discord bot token from Discord Developer Portal
- Guild (server) ID
- Enable MESSAGE CONTENT INTENT in bot settings
- Environment variables:
  - `GLUON_DISCORD_TOKEN` - Bot token
  - `GLUON_DISCORD_GUILD` - Guild (server) ID
  - `GLUON_DISCORD_USERS` - Comma-separated allowed user IDs

### For Web Dashboard (FastAPI)
- Redis connection (for job queue)
- Environment variables:
  - `GLUON_REDIS_URL` - Redis connection URL (default: redis://localhost:6379/0)
  - `GLUON_UVICORN_HOST` - Server host (default: 0.0.0.0)
  - `GLUON_UVICORN_PORT` - Server port (default: 8000)

### For Webhooks
- Webhook secret for signature validation
- Environment variables:
  - `GLUON_GITHUB_WEBHOOK_SECRET` - GitHub webhook signing key

### Storage & Logging
- `~/.gluon/gluon.db` - SQLite database (auto-initialized)
- `~/.gluon/logs/` - Background run logs (organized by run_id)
- `~/.gluon/supervisor.pid` - Supervisor daemon PID file
- `~/.gluon/supervisor.log` - Supervisor daemon logs
- `~/.claude/` - Claude CLI credentials (auto-mounted in Docker)
- `~/.aws/` - AWS credentials for Bedrock access
