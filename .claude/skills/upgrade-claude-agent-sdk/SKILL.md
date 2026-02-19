---
name: upgrade-claude-agent-sdk
description: Upgrade the claude-agent-sdk dependency, review SDK changes for integration impact, expose new features to users via Settings or New Task dialog, update documentation, and ship on a feature branch. This skill should be used when the user mentions upgrading, updating, or bumping the claude-agent-sdk, or when reviewing SDK release notes for Gluon integration.
---

# Upgrade Claude Agent SDK

## Overview

Perform a structured upgrade of the `claude-agent-sdk` Python package in this project. Review upstream changes, assess integration impact, expose new features to users where appropriate, update documentation, and ship the result on a clean feature branch.

## Workflow

Execute these phases in order. Do not skip phases — each validates the previous.

### Phase 1: Discover SDK Changes

1. **Check current version**

   ```bash
   uv run python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"
   grep 'claude-agent-sdk' pyproject.toml
   ```

2. **Fetch latest SDK release info** from `https://github.com/anthropics/claude-agent-sdk-python`

   Use `gh` CLI or web tools to review:
   - Release notes / changelog
   - New/changed exports in `claude_agent_sdk` and `claude_agent_sdk.types`
   - New fields on `ClaudeAgentOptions` (the primary integration surface)

3. **Inspect SDK types programmatically**

   ```bash
   # After installing the new version
   uv run python -c "
   import dataclasses, claude_agent_sdk
   for f in dataclasses.fields(claude_agent_sdk.ClaudeAgentOptions):
       print(f'{f.name}: {f.type}')
   "
   ```

4. **Produce a change summary** listing:
   - New `ClaudeAgentOptions` fields (type, default, purpose)
   - Deprecated or removed fields
   - New exported types/classes
   - Breaking changes to existing APIs
   - CLI flag changes (run `CLAUDECODE= claude --help` to check)

### Phase 2: Assess Integration Impact

Read the integration map at `references/integration-map.md` for the full data flow.

For each SDK change, determine:

1. **Does it affect existing integration?** Check if any current code in `agent.py` uses deprecated/changed APIs. Fix these first.

2. **Is it a new feature?** Classify it:
   - **Global preference** → Settings page + `store.get_setting()`
   - **Per-task option** → TASK_PROFILES + `resolve_task_options()` + Advanced Options UI
   - **SDK-only internal** → `agent.py` only
   - **Informational** → README/docs only

3. **Priority**: Fix breaking changes first, then deprecations, then new features.

### Phase 3: Implement Changes

Apply changes following the integration layer order (top → bottom):

#### 3a. Bump SDK Version

Update `pyproject.toml` dependency and run `uv lock`.

#### 3b. Update SDK Boundary (`src/gluon/agent.py`)

- Update imports for new/changed types
- Update `GluonAgent.__init__()` with new parameters
- Update `_build_options()` to set new `ClaudeAgentOptions` fields
- Remove usage of deprecated fields

#### 3c. Update Internal Models (`src/gluon/models.py`)

For per-task features:
- Add new enum values if needed (e.g., new thinking modes)
- Add defaults to `TASK_PROFILES` dict entries
- Add parameter to `resolve_task_options()` and include in return dict

#### 3d. Thread Through Execution Stack

For each new parameter, add it to (in order):
1. `Orchestrator.execute()` in `src/gluon/core.py`
2. `TaskRunner.submit()` in `src/gluon/runner.py` (+ metadata storage + `_run_task()` read-back)
3. `run` command in `src/gluon/cli.py` (as a `typer.Option`)
4. `GluonBotCore.execute_task()` in `src/gluon/bot_core.py`
5. `CreateRunRequest` in `src/gluon/web/models.py`
6. `create_run()` endpoint in `src/gluon/web/api.py`

#### 3e. Update Web UI

For per-task features:
- Add TypeScript types to `web-ui/src/lib/types.ts`
- Add UI control to `web-ui/src/components/CreateTaskDialog.tsx` (in Advanced Options section)
- Pass new field in `createRun()` API call

For global preferences:
- Add toggle/dropdown to `web-ui/src/components/SettingsPage.tsx`

#### 3f. Update Global Settings (if applicable)

For features that should apply globally:
- Add to Settings page UI
- Read via `store.get_setting()` in runner/core before creating GluonAgent

### Phase 4: Update Documentation

1. **README.md** — Update relevant sections:
   - Model Selection table (if models changed)
   - Features lists (if significant new capabilities)
   - CLI examples (if new flags added)
   - Any command syntax changes

2. **New docs** — If a feature is significant enough to warrant its own documentation (new execution mode, new integration pattern), create a file in `docs/` following the style of existing docs.

3. **CLAUDE.md** — Update the "LLM models supported" table if model IDs changed, or the "Key Files" table if new files were added.

### Phase 5: Quality Checks & Ship

1. **Run full test suite**

   ```bash
   uv run pytest tests/ -x -q
   ```

2. **Lint and format**

   ```bash
   uv run ruff check . --fix
   uv run ruff format .
   ```

3. **Type check**

   ```bash
   uv run mypy src/gluon
   ```

4. **Create feature branch, commit, and push**

   ```bash
   git checkout -b feat/upgrade-claude-agent-sdk-vX.Y.Z
   git add -A
   git commit -m "feat: upgrade claude-agent-sdk to vX.Y.Z

   - <summary of changes>

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
   git push -u origin feat/upgrade-claude-agent-sdk-vX.Y.Z
   ```

5. **Create PR** with summary of SDK changes and their Gluon integration impact.

## Decision Guide: Where to Expose New Features

```
Is the feature a per-invocation setting?
├── YES: Does it have sensible per-profile defaults?
│   ├── YES → Add to TASK_PROFILES + resolve_task_options + CLI flag + Advanced Options UI
│   └── NO  → Add to CLI flag + Advanced Options UI only (no profile default)
└── NO: Is it a preference that applies to all runs?
    ├── YES → Add to Settings page + store.get_setting()
    └── NO  → Keep internal to agent.py (SDK plumbing)
```

## Resources

### references/

- `integration-map.md` — Complete map of how SDK types flow through the Gluon codebase, from `agent.py` imports through to the web UI. Read this before making changes to understand the full data flow.
