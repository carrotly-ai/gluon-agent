# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Gluon Agent?

AI orchestrator for managing multiple Claude Code agents across projects. Provides session persistence, resume capability, workspace-based project discovery, and multiple interfaces (CLI, Telegram, Discord).

## LLM Providers

Gluon supports **four** LLM providers — AWS Bedrock, direct Anthropic API, Google Vertex AI, and Microsoft Foundry (Azure AI Foundry). Selectable via `GLUON_LLM_PROVIDER` env var, the `gluon provider` CLI command, or the web dashboard Settings page. Default is `bedrock`. Provider abstraction lives in `src/gluon/llm_provider.py`; see `docs/LLM-PROVIDER.md` for the full design.

| Tier                | Bedrock Model ID                                     | Anthropic Model ID            | Vertex Model ID                | Foundry Model ID      |
|---------------------|------------------------------------------------------|-------------------------------|--------------------------------|-----------------------|
| claude-opus-4.8     | global.anthropic.claude-opus-4-8                     | claude-opus-4-8               | claude-opus-4-8                | claude-opus-4-8       |
| claude-opus-4.7     | global.anthropic.claude-opus-4-7                     | claude-opus-4-7               | claude-opus-4-7                | claude-opus-4-7       |
| claude-opus-4.6     | global.anthropic.claude-opus-4-6-v1                  | claude-opus-4-6               | claude-opus-4-6                | claude-opus-4-6       |
| claude-sonnet-4.6   | global.anthropic.claude-sonnet-4-6                   | claude-sonnet-4-6             | claude-sonnet-4-6              | claude-sonnet-4-6     |
| claude-haiku-4.5    | global.anthropic.claude-haiku-4-5-20251001-v1:0      | claude-haiku-4-5-20251001     | claude-haiku-4-5@20251001      | claude-haiku-4-5      |

**IMPORTANT:** We only support the five tiers listed above. The default is `claude-opus-4.8`. Provider resolution order: explicit argument → `GLUON_LLM_PROVIDER` env var → `llm_provider` setting in the DB → default `bedrock`.

**Subprocess env:** Each provider's `runner_env()` method contributes the `CLAUDE_CODE_USE_*` flag and any required credentials to the Claude Code subprocess. Never hardcode `CLAUDE_CODE_USE_BEDROCK=1` in compose or env files — let the provider emit it.

## Docker Operations

**IMPORTANT: Always use `docker compose` for container operations. NEVER use raw `docker run` commands.**

There are two compose files for different purposes:

| File | Purpose | Image | Container | Env file |
|------|---------|-------|-----------|----------|
| `docker-compose.dev.yml` | Local dev/test — builds from source | `gluon-agent:latest` (local) | `gluon-agent-dev` | `.env.local` |
| `docker-compose.yml` | Production — pulls from GHCR | `ghcr.io/carrotly-ai/gluon-agent:latest` | `gluon-agent` | `.env` |

Both compose files mount critical host directories:
- `~/.claude` - Claude CLI credentials (authentication)
- `~/.gluon` - Gluon database and logs
- `~/.aws` - AWS credentials for Bedrock
- `~/.config/gh` - GitHub CLI for PR operations

### Dev/Test (build from source)

```bash
# Rebuild and restart (after code changes)
docker compose -f docker-compose.dev.yml build && docker compose -f docker-compose.dev.yml up -d

# View logs
docker compose -f docker-compose.dev.yml logs -f

# Stop
docker compose -f docker-compose.dev.yml down

# Full rebuild (no cache)
docker compose -f docker-compose.dev.yml build --no-cache && docker compose -f docker-compose.dev.yml up -d

# Shell into container
docker exec -it gluon-agent-dev bash
```

**After any significant changes** (dependency upgrades, new features, bug fixes), rebuild and redeploy locally:
```bash
docker compose -f docker-compose.dev.yml build && docker compose -f docker-compose.dev.yml up -d
```

### Production (GHCR images)

```bash
# Pull latest and start
docker compose pull && docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down

# Shell into container
docker exec -it gluon-agent bash
```

**Port:** 45866 (both dev and production)

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
| `src/gluon/agent_hooks.py` | SDK hooks: tool logging, subagent tracking, screenshot interception |
| `src/gluon/core.py` | Orchestrator coordinating store + agent |
| `src/gluon/runner.py` | Background task execution with subprocess management |
| `src/gluon/chat_agent.py` | Natural language interface using Claude + MCP tools |
| `src/gluon/cli.py` | Typer CLI commands |
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

**Web Dashboard HTTPS (optional):**
- `GLUON_SSL_CERTFILE` - Path to SSL certificate file (e.g., `/home/gluon/.gluon/ssl/cert.crt`)
- `GLUON_SSL_KEYFILE` - Path to SSL private key file (e.g., `/home/gluon/.gluon/ssl/key.key`)
- Both must be set to enable HTTPS; if unset, serves HTTP

**Multi-user auth (D5 — optional, default off):**
- `GLUON_AUTH_ENABLED` - Set to `true` to require login. When unset/false, Gluon runs in single-user mode and the SYSTEM_USER placeholder is used for all actions. **Single feature flag for both local and OIDC.** When enabled, an app-wide fail-closed middleware enforces authentication + RBAC on every API route (see `docs/AUTH.md`).
- `GLUON_ALLOWED_ORIGINS` - Comma-separated CORS origin allowlist for the web dashboard (default `http://localhost:45866`). Only these origins may make credentialed cross-origin requests.
- `GLUON_AUTH_SWEEP_INTERVAL_SECS` - How often to sweep expired sessions and unconsumed link codes. Default `3600` (1 hour).
- `GLUON_LOCAL_AUTH_ENABLED` - Default `true`. Set `false` to disable the password endpoint entirely (OIDC-only mode). The CLI still works, so first-admin bootstrap remains available.

**OIDC / SSO** (D5 Phase 3 — opt-in; see `docs/AUTH-OIDC.md` for full setup):
- `GLUON_OIDC_ISSUER` - Discovery URL, e.g. `https://accounts.google.com`
- `GLUON_OIDC_CLIENT_ID` / `GLUON_OIDC_CLIENT_SECRET`
- `GLUON_OIDC_REDIRECT_URI` - Must match what's registered with the IdP, e.g. `https://gluon.example.com/api/auth/oidc/callback`
- `GLUON_OIDC_PROVIDER_NAME` - Display name on the login button (default "OIDC")
- `GLUON_OIDC_AUTO_PROVISION` - Default `false`. When `true`, **requires** `GLUON_OIDC_DOMAIN_ALLOWLIST` (comma-separated email domains) — the safety guardrail that prevents random Google/Auth0 users from creating accounts.
- `GLUON_OIDC_DEFAULT_ROLE` - Role for auto-provisioned users (default `viewer`)
- `GLUON_OIDC_SESSION_SECRET` - Signs the OAuth state cookie. Set in multi-replica deployments.

**Bootstrap the first admin** (CLI works regardless of `GLUON_AUTH_ENABLED`):
```bash
# Local (password) user
uv run gluon user add alice --role admin
# prompts for password (12+ chars)

# OIDC user — admin doesn't need to know the IdP's `sub` claim;
# Gluon uses the email as a placeholder until first login.
uv run gluon user add alice --auth-provider oidc --email alice@org.example --role admin
```
Other user-management commands: `gluon user list / show / disable / enable / set-role / set-password`. Web dashboard's admin user-management screen requires logging in as an existing admin.

**Operational tuning (optional):** these are read by the code but are not required for normal use.
- `GLUON_MAX_CONCURRENT_RUNS` — max simultaneously-executing runs per container (default `3`). Each run is a heavy subprocess tree, so keep this conservative; overshooting can saturate the host VM. Excess runs queue on the semaphore rather than being rejected.
- `GLUON_DEFAULT_RUN_MAX_COST_USD` — default per-run cost cap in USD (unset = no default cap).
- `GLUON_QUESTION_TIMEOUT` — seconds before an unanswered AskUserQuestion times out and pauses the run (default `300`).
- `GLUON_QUESTION_ESCALATE_AT` — seconds before an unanswered question is escalated (default `180`).
- `GLUON_QUEUE_DRAIN_INTERVAL_SECS` — work-queue drain cadence (default `60`).
- `GLUON_TASK_SCHEDULES_POLL_SECS` — task-schedule poll cadence (default `30`); `GLUON_SCHEDULES_DISABLED=1` turns the scheduler off.
- `GLUON_HEARTBEAT_ENABLED` / `GLUON_HEARTBEAT_POLL_SECS` — enable the health heartbeat and its cadence (default `60`).
- `GLUON_WITNESS_ENABLED` — enable the witness/decision subsystem.
- `GLUON_TELEGRAM_APPROVAL_CHAT` / `GLUON_DISCORD_APPROVAL_CHANNEL` — route approval prompts to a specific chat/channel.
- `GLUON_DEV_PORT` — dev-server port passed into the agent subprocess for app-launch tasks.

Data stored at `~/.gluon/gluon.db`

## Adding a New Transport

1. Create `src/gluon/transport/myplatform.py`
2. Implement `Transport` ABC from `transport/base.py`
3. Add capabilities to `transport/capabilities.py`
4. Add CLI command in `cli.py`
5. Export in `transport/__init__.py`
