# Development Guide

This guide helps future Claude Code sessions understand and extend Gluon Agent.

## Quick Start

```bash
# Navigate to project
cd ~/workspaces/tao/research/ai-orchestrator/gluon-agent

# Activate virtual environment
source .venv/bin/activate

# Or use uv directly
uv run gluon --help
```

## Project Structure

```
gluon-agent/
├── src/gluon/
│   ├── __init__.py      # Package version
│   ├── models.py        # Pydantic models (Workspace, Project, Session)
│   ├── store.py         # SQLite persistence layer
│   ├── agent.py         # Claude Agent SDK wrapper
│   ├── core.py          # Orchestrator (business logic)
│   ├── chat_agent.py    # NL interpreter with MCP tools
│   ├── cli.py           # Typer CLI commands
│   └── bot.py           # Telegram bot
├── tests/
│   ├── test_models.py
│   └── test_store.py
├── docs/                 # This documentation
├── pyproject.toml        # Dependencies
├── PLAN.md               # Original implementation plan
└── README.md             # User documentation
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
import tempfile
from pathlib import Path
import pytest
from gluon.store import GluonStore
from gluon.core import Orchestrator

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

def test_feature(store):
    """Test description."""
    # Arrange
    # Act
    # Assert
```

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
```

### Reset Database

```bash
# Delete database to start fresh
rm ~/.gluon/gluon.db

# Re-run any gluon command to recreate
uv run gluon status
```

### Debug Claude Agent

```python
# Add logging in agent.py
import logging
logging.basicConfig(level=logging.DEBUG)

# Or inspect messages:
async for msg in client.receive_response():
    print(f"DEBUG: {type(msg).__name__}: {msg}")
```

### Test Telegram Bot Locally

```bash
# Set environment variables
export GLUON_TELEGRAM_TOKEN="your-test-token"
export GLUON_TELEGRAM_USERS="your-user-id"

# Run with debug output
uv run gluon bot
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

Key dependencies and their purposes:

| Package | Purpose |
|---------|---------|
| `claude-agent-sdk` | Claude Code integration |
| `typer` | CLI framework |
| `rich` | Beautiful terminal output |
| `pydantic` | Data validation |
| `python-telegram-bot` | Telegram bot |
| `python-dotenv` | Environment variables |
| `anyio` | Async runtime |

## Environment Setup

### Required
- Python 3.12+
- Claude Code CLI installed and authenticated
- `uv` package manager

### For Telegram Bot
- Telegram bot token from @BotFather
- User IDs for access control

### Files
- `~/.gluon/gluon.db` - SQLite database
- `~/.gluon/.env` - Global environment config
- `.env.local` - Local environment overrides
