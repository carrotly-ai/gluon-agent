# Agent Loops — Loop Engineering, Phase 2 (STATE.md)

Living design + progress doc for the outer-loop / agent-loops work (branch
`feat/agent-loops`). Companion to `docs/design/loop-engineering.md` (Phase 1:
objective gates, metrics, gateless handoff — merged in #154).

## Why

Phase 1 fixed the **inner** loop: a ralph run iterates one static prompt until an
objective gate (`verify_cmd`) passes, with caps and a draft-PR handoff. That is
still the Ralph pattern — the harness re-sends the same prompt.

**Loop engineering** proper (Cherny/Steinberger/Osmani, June 2026) is the next
layer: *the agent authors the next iteration's work*. The unit of work becomes an
ongoing loop in which each iteration does a focused slice, then **enqueues the
next task(s)** — fanning out where work is parallelizable — while the harness
keeps authority over verification, budgets, and stopping.

Gluon has every substrate piece already: a prioritized work queue with a drain
loop + self-propelling dispatch, objective gates (`gate.py`), per-run budgets,
worktrees, and subprocess workers with store access. What is missing is exactly
three things:

1. **Agents cannot write to the future** — no tool for a worker to enqueue work.
2. **No loop entity** — nothing owns an *objective* across runs (budget,
   iteration history, stall detection, completion) — only per-run state exists.
3. **No no-progress/dedup control** — a self-enqueueing agent could loop itself
   forever today (the "open invoice" failure mode).

## Design (grounded against the real code)

### New concept: `AgentLoop`

A persistent outer loop owning: `objective`, optional `verify_cmd` (loop-level
gate), budgets (`max_iterations`, `max_cost_usd`), stall controls (`max_stalls`,
`max_fanout`), status (`running/paused/completed/failed/cancelled`), and
accounting (`iteration_count`, `total_cost_usd`, `stall_count`).

### Mechanism — ride the existing machinery, don't add an 8th engine

- Loop tasks are ordinary `work_queue` items with `loop_id` (+ `source` =
  `seed | agent | continuation`, + `prompt_hash` for dedup). The existing drain
  loop and self-propelling dispatch execute them (per-project serialization and
  the global concurrency cap apply unchanged).
- Runs carry `loop_id` (nullable, like `schedule_id`). Dispatch from a loop item
  inherits the loop's `profile/model/use_worktree/verify_cmd`.
- **Worker-side tools**: when a run has `loop_id`, `GluonAgent._build_options`
  injects an in-process SDK MCP server `gluon-loop` (workers are
  `python -m gluon.runner` subprocesses with store access, so in-process tools
  write straight to SQLite):
  - `loop_enqueue_task(prompt, priority)` — agent authors the next task(s);
    fan-out = multiple calls. Guards: loop must be RUNNING, per-loop pending cap
    (`max_fanout`), normalized-prompt-hash dedup (duplicates rejected).
  - `loop_complete(summary)` — requests completion; **the gate, not the agent,
    is authority** when `verify_cmd` is set.
  - `loop_status()` — objective, budgets, pending tasks, recent iterations.
  A `LOOP_SYSTEM_PROMPT` block (models.py, beside `RALPH_SYSTEM_PROMPT`) states
  the iteration contract: do the slice → enqueue next or complete; no dupes;
  ending with neither counts as a stall.
- **`LoopManager.on_run_completed(run)`** — called from the run-completion seam
  in `runner._run_task` (before self-propelling dispatch, after
  `_finalize_queue_item`):
  1. run FAILED → pause loop (fail-safe), cancel pending loop items.
  2. `completion_requested` → gated: `run_gate(loop.verify_cmd)` in the run's
     worktree (or project dir); pass → COMPLETED; fail → deny completion, feed
     gate output into a harness `continuation` task (evaluator-optimizer at loop
     level). Gateless → COMPLETED (graceful gateless: agent's word is authority,
     matching Phase 1 semantics).
  3. budgets: `iteration_count >= max_iterations` or `total_cost_usd >=
     max_cost_usd` → PAUSED (`status_reason` says why) + cancel pending items —
     pause-not-fail so a human can raise the budget and resume.
  4. stall detection: zero pending loop tasks and no completion → `stall_count`
     +1; first stalls inject one harness-authored `continuation` task ("assess
     objective vs state; enqueue or complete"); `stall_count > max_stalls` →
     PAUSED. Any agent-enqueued pending work resets `stall_count`.
- Dedup applies to the **agent** path only (the tool); harness seeds and
  continuations bypass it (continuation text embeds the iteration number).

### Stop conditions (the loop-engineering discipline, enforced)

hard iteration ceiling ✓ (`max_iterations`) · spend cap ✓ (`max_cost_usd`,
loop-level, on top of per-run budgets) · no-progress detection ✓ (stall counter +
dedup rejection) · verification authority ✓ (`verify_cmd` via `gate.py`) ·
graceful handoff ✓ (PAUSED + `status_reason`, never silent, never infinite).

### Surfaces

- API `web/routers/loops.py`: `POST /api/loops`, `GET /api/loops`,
  `GET /api/loops/{id}`, `POST /api/loops/{id}/pause|resume|cancel`.
  Create seeds iteration 1 and kicks the queue drain for immediate dispatch.
- CLI `gluon loop create|list|show|pause|resume|cancel` (store-level; the
  server's drain loop dispatches).
- Web-UI surface: deferred (follow-up) — API is sufficient for MVP.

## Progress

- [x] Design doc (this file)
- [x] Models: `LoopStatus`, `AgentLoop`, `LOOP_SYSTEM_PROMPT`,
  `ExecutionRun.loop_id`, `WorkQueueItem.loop_id/source/prompt_hash`,
  `normalize_prompt_hash()`
- [x] Store: migrations (agent_loops table; work_queue + execution_runs
  columns), CRUD, loop queries (pending count, dedup, cancel-pending,
  runs-for-loop)
- [x] `loop_manager.py`: create/seed, `on_run_completed` (gate/budget/stall),
  pause/resume/cancel
- [x] `loop_tools.py`: `gluon-loop` SDK MCP server (enqueue/complete/status)
- [x] `agent.py`: inject MCP server + system prompt for loop runs
- [x] `runner.py`: `submit(loop_id=…)`, `_submit_from_queue_item` (both dispatch
  sites), completion-seam hook, `kick_queue_drain()`
- [x] Web: models + `loops.py` router + registration
- [x] CLI: `gluon loop` sub-app
- [x] Tests: `tests/test_agent_loops.py` (store, manager, tools, API,
  non-regression)
- [x] Gate: ruff + mypy clean on touched files; full pytest suite green

## Future (documented, not built here)

- Web-UI Loops page (list + detail timeline of iterations)
- Loop-level effectiveness metrics (extend `get_loop_effectiveness` with a
  per-loop breakdown; cost-per-accepted-change per loop)
- Independent-verifier subagent as an alternative gate type (Phase 1's I2)
- Cross-project loops / loop templates ("formulas" integration)
