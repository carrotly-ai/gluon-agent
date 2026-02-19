# Gluon Agent SDK Integration Map

## SDK Dependency

- **Package**: `claude-agent-sdk` in `pyproject.toml` dependencies
- **Lockfile**: `uv.lock` (update via `uv lock` after changing pyproject.toml)

## Integration Layers (data flows top → bottom)

### 1. SDK Types & Imports (`src/gluon/agent.py`)

All SDK types are imported here. When the SDK adds new types, this is where they enter the codebase.

```
from claude_agent_sdk import (
    ClaudeAgentOptions,   # Main options dataclass — CHECK FOR NEW FIELDS
    ClaudeSDKClient,      # Client class
    ...type imports...
)
```

**Key method**: `GluonAgent._build_options()` — Constructs `ClaudeAgentOptions` from Gluon's internal state. This is the **SDK boundary** where Gluon's abstractions convert to SDK types.

**Key method**: `GluonAgent.__init__()` — Constructor accepting all configurable params. New SDK features get exposed here first.

### 2. Internal Models (`src/gluon/models.py`)

Gluon's own abstractions that map to SDK concepts:

- `ThinkingBudget` enum — Maps to `ClaudeAgentOptions.max_thinking_tokens`
- `TaskProfile` enum + `TASK_PROFILES` dict — Bundles of defaults (model, thinking, turns, budget, effort)
- `resolve_task_options()` — Resolves profile + overrides → flat dict consumed by `GluonAgent.__init__`

New SDK features that have per-task granularity should get:
1. A field in `TASK_PROFILES` (default per profile)
2. A parameter in `resolve_task_options()` (for overrides)
3. Returned in the options dict

### 3. Orchestrator (`src/gluon/core.py`)

`Orchestrator.execute()` — Foreground execution path (CLI interactive, bot).
- Calls `resolve_task_options()`
- Creates `GluonAgent()` with resolved options
- Streams `AgentMessage` / `AgentResult`

### 4. Task Runner (`src/gluon/runner.py`)

`TaskRunner.submit()` — Background execution path (CLI --background, web dashboard).
- Calls `resolve_task_options()`
- Stores resolved options in `run.metadata` dict
- `_run_task()` reads metadata and creates `GluonAgent()`
- `_run_ralph_loop()` creates its own `GluonAgent()` for autonomous loops

**Pattern**: Runner stores options in `run.metadata["key"]`, then reads them back in `_run_task()`.

### 5. CLI (`src/gluon/cli.py`)

`run` command — User-facing flags.
- Background path: calls `runner.submit()`
- Foreground path: calls `orchestrator.execute()`

New SDK features need a `typer.Option("--flag-name", ...)` added to the `run` function signature.

### 6. Bot Core (`src/gluon/bot_core.py`)

`GluonBotCore.execute_task()` — Bot execution (Telegram, Discord).
- Delegates to `orchestrator.execute()`
- New params need to be added to the signature and passed through.

### 7. Web API (`src/gluon/web/api.py` + `src/gluon/web/models.py`)

`CreateRunRequest` (Pydantic model) — Request body for `POST /api/runs`.
- New per-task options go here as optional fields
- `create_run()` endpoint passes fields to `runner.submit()`

### 8. Web UI (`web-ui/src/`)

- **Types**: `web-ui/src/lib/types.ts` — TypeScript types mirroring Python models
- **API client**: `web-ui/src/lib/api.ts` — `createRun()` function
- **New Task Dialog**: `web-ui/src/components/CreateTaskDialog.tsx` — Per-task overrides in "Advanced Options"
- **Settings Page**: `web-ui/src/components/SettingsPage.tsx` — Global preferences (stored via `GET/PUT /api/settings`)

### 9. Global Settings (`src/gluon/store.py`)

Settings stored in SQLite via `store.get_setting()` / `store.update_setting()`.
These are global (not per-task) and exposed in the Settings page.

Pattern: `self.store.get_setting("feature_enabled", "false") == "true"`

## SDK Feature Classification

When evaluating a new SDK feature, classify it:

| Category | Where it goes | Examples |
|----------|--------------|----------|
| **Global preference** | Settings page + `store.get_setting()` | sandbox_enabled, agent_teams_enabled |
| **Per-task option** | TASK_PROFILES + resolve_task_options + Advanced Options UI | effort, thinking, model |
| **SDK-only internal** | `agent.py` only (no user exposure) | hooks, permission_mode |
| **Informational** | README/docs only | New message types, deprecations |
