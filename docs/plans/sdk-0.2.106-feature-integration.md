# Plan: SDK 0.2.106 Feature Integration

**Status**: Implemented
**Date**: 2026-06-22
**SDK Version**: claude-agent-sdk 0.2.106 (upgraded from 0.2.87)
**Branch**: `feat/upgrade-claude-agent-sdk-v0.2.106`

## Overview

The 0.2.88–0.2.106 range is overwhelmingly bundled-CLI maintenance (Claude CLI
`2.1.150 → 2.1.185`, transparent to Gluon). There are **no breaking changes** and
**no new `ClaudeAgentOptions` fields** — every option Gluon already sets
(`effort`, `task_budget`, `enable_file_checkpointing`, `thinking`, …) is unchanged.

One change is directly relevant to Gluon's agent message loop:

| # | Source | Feature | Priority | Effort |
|---|--------|---------|----------|--------|
| 1 | 0.2.101 (#1016) | Typed `TaskUpdatedMessage` terminal lifecycle event | Medium | S |

Two dependency/robustness changes apply automatically with no code:

- **0.2.96** — `mcp` pinned `< 2.0.0`, avoiding an incompatible transitive bump.
- **0.2.88** — `session_store` paths ported to `anyio` for trio compatibility
  (Gluon runs on asyncio, so this is latent insurance only).

---

## Feature 1: Typed `TaskUpdatedMessage` handling

### Problem

`TaskUpdatedMessage` (SDK ≥ 0.2.101) is the typed terminal lifecycle event for
background tasks (`status ∈ {completed, failed, killed, stopped}`, plus a `patch`
of changed fields). It subclasses `SystemMessage`, so before this change it fell
through Gluon's `SystemMessage` catch-all in `agent.py` and was emitted as a
generic `type="system", content="task_updated"` message. The web UI already
filtered that string form as noise, so nothing broke — but the terminal status
was untyped and invisible to any future consumer, and the handling was
inconsistent with the sibling task messages (`TaskStarted`/`TaskProgress`/
`TaskNotification`), all of which are matched explicitly before `SystemMessage`.

The upstream fix (#1016) exists so consumers tracking active background tasks
don't hang when a task finishes via `task_updated` without a paired
`TaskNotificationMessage`.

### Approach

Match `TaskUpdatedMessage` explicitly in the receive loop, before the
`SystemMessage` branch, and emit a typed `task_updated` `AgentMessage` carrying
`task_id`, `status`, `patch`, and `session_id`. The web UI continues to filter it
as low-signal (terminal status is surfaced via `task_notification`), but now on
the typed `msg.type === 'task_updated'` field, with backward-compat retained for
the old `system`/`task_updated` string form in historical logs.

### Implementation

- `src/gluon/agent.py` — import `TaskUpdatedMessage`; add an `elif` branch
  emitting `type="task_updated"` with structured metadata, placed before the
  `SystemMessage` catch-all (mirrors the existing task-message handlers).
- `web-ui/src/components/StreamingLogViewer.tsx` — filter the typed
  `task_updated` form (keep the legacy `system`/`task_updated` filter).
- `web-ui/src/lib/agentMessage.ts`, `web-ui/src/lib/types.ts` — add the
  `'task_updated'` literal to the `AgentMessage` type unions.

### Validation

- `ruff check` / `ruff format`: clean
- `mypy src/gluon/agent.py`: clean
- `pytest`: 2307 passed
- web-ui `tsc --noEmit`, `biome check`: clean; `vitest`: 34 passed
