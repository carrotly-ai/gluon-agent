# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Gluon Agent?

AI orchestrator for managing multiple Claude Code agents across projects. Provides session persistence, resume capability, workspace-based project discovery, and multiple interfaces (CLI, Telegram, Discord).

## Commands

```bash
# Setup
uv venv && uv pip install -e '.[dev]'

# Run CLI
uv run gluon --help
uv run gluon status
uv run gluon project list
uv run gluon run <project> '<prompt>'

# Run Telegram bot
export GLUON_TELEGRAM_TOKEN="your-token"
uv run gluon bot

# Run Discord bot
export GLUON_DISCORD_TOKEN="your-token"
export GLUON_DISCORD_GUILD="your-guild-id"
uv run gluon discord

# Run both transports
uv run gluon serve --telegram --discord

# Tests
uv run pytest                           # all tests
uv run pytest tests/test_store.py       # single file
uv run pytest tests/test_store.py::test_name -v  # single test

# Linting & Formatting
uv run ruff format .
uv run ruff check .
uv run mypy src/gluon

# Debug database
sqlite3 ~/.gluon/gluon.db
```

## Architecture

```
CLI (cli.py) ───────────────────────────────────────────┐
                                                        │
                Transport Layer (transport/)            │
                ┌─────────────┬─────────────┐           │
                ▼             ▼             ▼           ▼
         TelegramTransport  DiscordTransport  ...    Orchestrator (core.py)
                └─────────────┴─────────────┘           [Business Logic]
                              │                              │
                              ▼                              │
                       GluonBotCore (bot_core.py) ◄──────────┤
                       [Shared bot logic]                    │
                              │                              │
                    ┌─────────┴─────────┐                    │
                    ▼                   ▼                    ▼
             Chat Agent           Orchestrator ───────► Agent (agent.py)
             (chat_agent.py)      (core.py)            [Claude SDK]
                    │                   │
                    └─────────┬─────────┘
                              ▼
                        Store (store.py)
                        [SQLite CRUD]
```

**Key Data Flow:**
1. User request → Orchestrator.execute(project, prompt)
2. Orchestrator finds/creates Session, calls GluonAgent
3. GluonAgent wraps Claude Agent SDK, streams responses
4. Session updated with claude_session_id, cost, status

## Key Files

| File | Purpose |
|------|---------|
| `src/gluon/models.py` | Pydantic models: Workspace, Project, Session, ExecutionRun, ChannelMapping |
| `src/gluon/store.py` | SQLite persistence with CRUD for all entities |
| `src/gluon/agent.py` | GluonAgent wrapping claude-agent-sdk |
| `src/gluon/core.py` | Orchestrator coordinating store + agent |
| `src/gluon/runner.py` | Background task execution with subprocess management |
| `src/gluon/chat_agent.py` | Natural language interface using Claude + MCP tools |
| `src/gluon/cli.py` | Typer CLI commands |
| `src/gluon/bot.py` | Telegram bot interface (legacy, see bot_core.py) |
| `src/gluon/bot_core.py` | Transport-agnostic bot business logic |
| `src/gluon/transport/base.py` | Transport ABC and dataclasses |
| `src/gluon/transport/telegram.py` | TelegramTransport implementation |
| `src/gluon/transport/discord.py` | DiscordTransport implementation (optional dep) |

## Session Resume

Sessions capture `claude_session_id` from Claude SDK. Resume uses `fork_session` option:

```python
# In agent.py
options = ClaudeAgentOptions(
    cwd=working_dir,
    resume=previous_session_id,  # Enables resume
)
```

Session lifecycle: `ACTIVE → PAUSED → ACTIVE (resume) → COMPLETED/FAILED`

## Background Execution

Run tasks in background with log persistence:

```bash
# Submit background task
gluon run myproject "fix the bug" --background
# Returns: Task submitted: abc12345

# List all runs
gluon runs
gluon runs --active           # Only running tasks
gluon runs -p myproject       # Filter by project

# View logs
gluon logs abc12345           # View stdout
gluon logs abc12345 -f        # Follow live
gluon logs abc12345 -s stderr # View stderr
gluon logs abc12345 -s messages  # View structured JSONL

# Cancel running task
gluon cancel abc12345
```

**Storage:**
- Runs tracked in `execution_runs` table
- Logs stored at `~/.gluon/logs/{run_id}/`
  - `stdout.log` - Standard output
  - `stderr.log` - Standard error
  - `messages.jsonl` - Structured AgentMessage stream

**Run lifecycle:** `PENDING → RUNNING → COMPLETED/FAILED/CANCELLED`

## Extension Patterns

### Adding CLI Commands
```python
# cli.py
@app.command("newcmd")
def new_command(arg: Annotated[str, typer.Argument(...)]):
    orchestrator = get_orchestrator()
    # implementation
```

### Adding MCP Tools to Chat Agent
```python
# chat_agent.py
@tool("new_tool", "Description", {"param": str})
async def new_tool(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "result"}]}

# Add to allowed_tools list: "mcp__gluon__new_tool"
```

### Adding Store Methods
```python
# store.py - follow pattern:
def create_thing(self, ...) -> Thing:
    thing = Thing(...)
    with self._get_conn() as conn:
        conn.execute("INSERT INTO things ...", (...))
    return thing

def _row_to_thing(self, row: sqlite3.Row) -> Thing:
    return Thing(id=row["id"], ...)
```

### Schema Migrations
Add to `MIGRATIONS` list in `store.py` - migrations auto-run with error handling.

## Custom Exceptions

```python
from gluon.core import ProjectNotFoundError, ProjectExistsError, WorkspaceNotFoundError, WorkspaceExistsError
```

## Environment Variables

**Telegram:**
- `GLUON_TELEGRAM_TOKEN` - Telegram bot token
- `GLUON_TELEGRAM_USERS` - Comma-separated allowed user IDs

**Discord:**
- `GLUON_DISCORD_TOKEN` - Discord bot token
- `GLUON_DISCORD_GUILD` - Discord guild (server) ID
- `GLUON_DISCORD_USERS` - Comma-separated allowed user IDs

Data stored at `~/.gluon/gluon.db`

## Adding a New Transport

1. Create `src/gluon/transport/myplatform.py`
2. Implement `Transport` ABC from `transport/base.py`
3. Add capabilities to `transport/capabilities.py`
4. Add CLI command in `cli.py`
5. Export in `transport/__init__.py`
