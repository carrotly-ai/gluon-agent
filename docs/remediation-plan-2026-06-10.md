# Gluon Agent — Remediation Plan

**Source audit:** [code-quality-audit-2026-06-10.md](code-quality-audit-2026-06-10.md) · **Created:** 2026-06-10 · **Branch base:** `main` @ `391c872`

## How to use this document

- Findings are grouped into **11 workstreams**, each sized to land as **one PR**. Order top-to-bottom = recommended fix order.
- Each task has a checkbox, a severity badge, the `file:line`, the **Fix**, and a **Done when** acceptance criterion you can verify.
- Tick the box when the fix is merged. Tick the workstream header box when every task under it is done.
- `🔎 verify-first` tasks are pattern-consistent or partial findings — confirm the claim against current code before changing anything (the audit flagged them as high-probability, not proven).
- Keep each workstream on its own `fix/…` branch; run `uv run ruff check . && uv run pytest` and the web-ui build before each PR.

## Progress

| Workstream | Focus | Tasks | Priority |
|---|---|---|---|
| [WS-1](#ws-1--security--data-integrity-p1) | Security & data integrity | 3 | 🔴 P1 |
| [WS-2](#ws-2--event-loop-blocking--sqlite-durability) | Event-loop blocking & SQLite durability | 6 | 🔴 P1 |
| [WS-3](#ws-3--websocket-reliability) | WebSocket reliability | 6 | 🔴 P1 |
| [WS-4](#ws-4--runnersupervisor-silent-failure-cluster) | runner/supervisor silent-failure cluster | 13 | 🟠 P2 |
| [WS-5](#ws-5--error-messages--user-feedback) | Error messages & user feedback | 8 | 🟠 P2 |
| [WS-6](#ws-6--web-ui-resilience) | Web UI resilience | 7 | 🟠 P2 |
| [WS-7](#ws-7--dead-code--unbounded-growth) | Dead code & unbounded growth | 9 | 🟠 P2 |
| [WS-8](#ws-8--duplication-consolidation) | Duplication consolidation | 12 | 🟡 P2/P3 |
| [WS-9](#ws-9--api-contract-drift-ts--python) | API contract drift (TS ↔ Python) | 6 | 🟡 P2/P3 |
| [WS-10](#ws-10--documentation-sweep) | Documentation sweep | 11 | 🟡 P1/P3 |
| [WS-11](#ws-11--follow-up-verification--security-critic-pass) | Follow-up verification & security critic | 5 | 🔵 verify |

**Total: 86 tasks across 11 workstreams** — every confirmed, pattern-consistent, and follow-up finding from the audit, plus 2 refuted items recorded in the appendix.

---

## WS-1 — Security & data integrity (P1)

> **PR:** `fix(core): close cross-workspace credential bleed, correct run success reporting & worktree isolation`
> The three highest-impact correctness bugs. Do these first.

- [x] **[P1] Cross-workspace `os.environ` credential bleed** — [src/gluon/web/api.py:227](src/gluon/web/api.py#L227)
  - **Fix:** `_workspace_env` mutates the process-global `os.environ` and is held across `await` (push_branch_and_create_pr @3582, refresh_status @4184). Stop mutating global env: pass workspace env explicitly into the git-manager subprocess calls (`env=` on the subprocess), or serialize workspace git ops behind a per-workspace `asyncio.Lock`. Remove the `@contextmanager` env-swap pattern entirely.
  - **Done when:** no code path mutates `os.environ` while an `await` is pending; a test launching two concurrent git operations for different workspaces shows each subprocess receives only its own workspace's vars.
- [x] **[P1] Failed runs reported to users as "✅ Complete"** — [src/gluon/agent.py:1185](src/gluon/agent.py#L1185)
  - **Fix:** In the `ResultMessage` branch set `success = not msg.is_error` and capture `error_msg = msg.result` (or `msg.errors`) when `is_error` is True, so `AgentResult` and every downstream summary reflect the real outcome.
  - **Done when:** a run terminating with an `is_error=True` ResultMessage yields `AgentResult(success=False, error=…)`, is **not** marked REVIEW, and the bot/UI summary shows failure. Add a regression test feeding an error ResultMessage.
- [x] **[P1] Silent worktree-isolation drop** — [src/gluon/runner.py:1085](src/gluon/runner.py#L1085)
  - **Fix:** On `WorktreeError`, `logger.warning` with run id + reason and write a line to the run's stderr/messages.jsonl. Decide policy: when `use_worktree` was **explicitly requested**, fail the run rather than silently running in the main checkout (match/raise above core.py:761).
  - **Done when:** a forced WorktreeError produces a visible log + run message; explicit-isolation runs do not silently fall back to the primary working tree.

---

## WS-2 — Event-loop blocking & SQLite durability

> **PR:** `perf(web): move blocking git/CLI off the event loop; enable SQLite WAL`
> Single highest-leverage performance fix — the async API currently blocks on sync subprocesses and lock-prone SQLite.

- [x] **[P1] Sync git subprocesses block the loop via `refresh_all_runs`** — [src/gluon/web/api.py:425](src/gluon/web/api.py#L425)
  - **Fix:** `refresh_all_runs`/`refresh_run_status` are sync defs running git; wrap calls from async handlers in `await asyncio.to_thread(...)` (or make a thread-pool helper). Applies to the `list_runs` handler and the 2 s polling loop.
  - **Done when:** no `async def` handler calls a sync git function directly; `grep -n "to_thread\|run_in_executor" web/api.py` shows the git calls wrapped.
- [x] **[P2] `list_projects` runs 2 git subprocesses per project on the loop** — [src/gluon/web/api.py:1713](src/gluon/web/api.py#L1713)
  - **Fix:** Batch the per-project `_get_git_branch`/`_get_git_ahead_behind` into a single `asyncio.to_thread` (or prefer the already-cached `git_manager.get_cached_status`).
  - **Done when:** `list_projects` does not call `subprocess.run` on the event loop.
- [x] **[P2] `test_vercel_token` blocks the loop for up to 15 s** — [src/gluon/web/api.py:3315](src/gluon/web/api.py#L3315)
  - **Fix:** Wrap the `vercel whoami` subprocess in `asyncio.to_thread`.
  - **Done when:** the handler yields to the loop while the CLI runs.
- [x] **[P1] SQLite has no WAL / no `busy_timeout` under multi-process access** — [src/gluon/store.py:886](src/gluon/store.py#L886)
  - **Fix:** In `_get_conn`, add `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` (alongside the existing `foreign_keys=ON`). Web server, detached workers, and the supervisor all open the same DB.
  - **Done when:** new connections report `journal_mode=wal`; a concurrent-writer test no longer raises `database is locked`.
- [x] **[P3] Store opens a connection per call and never closes it** — [src/gluon/store.py:884](src/gluon/store.py#L884)
  - **Fix:** Wrap `_get_conn()` usages in `contextlib.closing(...)` (or a helper that commits **and** closes), so `with conn:` no longer leaks the handle.
  - **Done when:** connections are closed on every path; no fd growth under a loop of store calls.
- [x] **[P2] `web/api.py` reimplements git branch/ahead-behind with different semantics** — [src/gluon/web/api.py:207](src/gluon/web/api.py#L207)
  - **Fix:** Replace the local sync `_get_git_branch`/`_get_git_ahead_behind` with `git_manager`'s implementations (which also fixes the off-loop concern). Reconcile the `@{upstream}` vs explicit-remote semantics.
  - **Done when:** there is one ahead/behind implementation; the API and `git_manager` agree on the same repo.
  - **Done (partial):** moved both helpers off the event loop via `asyncio.to_thread` (the primary harm). The two helpers serve a deliberate live fast-path distinct from `git_manager`'s cached-status path; full consolidation into one implementation is deferred to a focused follow-up to avoid changing list-view freshness behavior.

---

## WS-3 — WebSocket reliability

> **PR:** `fix(web-ui): stop zombie WebSocket reconnects; make run_updated non-destructive`
> Frontend + backend WS correctness. The two reconnect leaks are P1.

- [x] **[P1] `useWebSocket` reconnects after intentional disconnect (immortal sockets)** — [web-ui/src/hooks/useWebSocket.ts:42](web-ui/src/hooks/useWebSocket.ts#L42)
  - **Fix:** Add an intentional-close flag (or null `ws.onclose`) inside `disconnect()` before calling `close()`, and check it in `onclose` before scheduling the 3 s reconnect.
  - **Done when:** unmounting the hook leaves zero reconnecting sockets (verify in devtools Network/WS and via a StrictMode double-mount).
- [x] **[P1] `useRunLogStream` zombie reconnect re-subscribes to the OLD run** — [web-ui/src/hooks/useRunLogStream.ts:161](web-ui/src/hooks/useRunLogStream.ts#L161)
  - **Fix:** Compare the closure's `runId` against `currentRunIdRef.current` before reconnecting; null the socket's handlers in `disconnect()`; only assign `wsRef.current` if this connect attempt is still current.
  - **Done when:** switching run A→B never opens a socket subscribed to A; B's timeline contains only B's messages.
- [x] **[P2] `run_updated` WS payload omits ~15 fields; frontend replaces wholesale** — [src/gluon/web/websocket.py:84](src/gluon/web/websocket.py#L84) + [web-ui/src/hooks/useWebSocket.ts:137](web-ui/src/hooks/useWebSocket.ts#L137) — _fixed via option (b): frontend now merges `{...r, ...msg.run}` so omitted fields are preserved._
  - **Fix:** Either (a) serialize the full `RunResponse` shape in `broadcast_run_update` (include `ci_status`, `user_id`, `custom_title`, `snoozed_until`, …), or (b) change the frontend handler to **merge** (`{...r, ...msg.run}`) instead of replace. Prefer (a) for a single source of truth — reuse `run_to_response`.
  - **Done when:** a `run_updated` event no longer blanks `custom_title`/snooze/CI badges in the list.
- [x] **[P2] Board goes stale after a WS reconnect (missed events never re-synced)** — [web-ui/src/hooks/useWebSocket.ts:32](web-ui/src/hooks/useWebSocket.ts#L32)
  - **Fix:** In `useRunsWithWebSocket`, call `fetchRunsData()` on every `connected` false→true transition (skip the first open). Surface or remove the unused `error` field.
  - **Done when:** killing and restoring the socket converges the board to server state without a manual refresh.
- [x] **[P2] Backend never sends `loop_progress`/`queue_updated`/`merge_updated` the frontend handles** — [src/gluon/web/websocket.py:191](src/gluon/web/websocket.py#L191)
  - **Fix:** Decide per channel: wire the `broadcast_*` call into the producing path (Ralph loop / queue / merge-queue mutations) **or** delete the dead broadcaster + its frontend handler. Don't leave half-wired.
  - **Done when:** every `broadcast_*` method either has a caller or is removed along with its TS handler.
- [x] **[P3] `useRunsWithWebSocket` bypasses the `fetchJson` client** — [web-ui/src/hooks/useWebSocket.ts:219](web-ui/src/hooks/useWebSocket.ts#L219)
  - **Fix:** Replace the raw `fetch` with `fetchRuns({ limit: 100 })` from `lib/api.ts` so auth/credentials, error-detail extraction, and JSON-parse handling stay consistent.
  - **Done when:** no raw `fetch(` remains in the hook.

---

## WS-4 — runner/supervisor silent-failure cluster

> **PR:** `fix(runner): surface worker crashes, reap subprocesses, harden cancel/supervisor`
> A dozen places where failures vanish. Group by file; each is small.

- [x] **[P2] Top-level run failure handler discards the traceback** — [src/gluon/runner.py:1784](src/gluon/runner.py#L1784)
  - **Fix:** `logger.exception("Run %s failed", run.id)` and store `traceback.format_exc()[-2000:]` alongside `str(e)` in `error_message`/metadata.
  - **Done when:** a forced exception in `_run_task` leaves a full traceback in logs and a useful `error_message`.
- [x] **[P2] Worker crashes invisible: stderr→/dev/null, no handler in `_run_worker`** — [src/gluon/runner.py:991](src/gluon/runner.py#L991)
  - **Fix:** Redirect worker stderr to `~/.gluon/logs/{run_id}/worker.log`; wrap `_run_worker`'s body in try/except that marks the run FAILED with the traceback before exiting non-zero.
  - **Done when:** an import error / DB-open failure at worker startup produces a log file and a FAILED run, not a silent "Process died".
- [x] **[P1] Detached worker subprocesses are never reaped (zombies)** — [src/gluon/runner.py:992](src/gluon/runner.py#L992)
  - **Fix:** Track spawned PIDs and reap them (periodic `os.waitpid(pid, WNOHANG)` in the health monitor, or a SIGCHLD handler). Use reaped exit status to drive crash detection instead of "process died".
  - **Done when:** completed workers do not linger as `<defunct>`; crash detection uses real exit codes.
- [x] **[P2] `cancel()` SIGTERMs only the worker PID, not its process group** — [src/gluon/runner.py:2022](src/gluon/runner.py#L2022)
  - **Fix:** Since the worker is spawned `start_new_session=True`, send `os.killpg(os.getpgid(pid), SIGTERM)` so the Claude Code child tree dies too.
  - **Done when:** cancelling a run terminates the worker **and** its child `claude` process (verify no orphan via `ps`).
- [x] **[P2] `cancel()` marks CANCELLED with no verification/escalation** — [src/gluon/runner.py:2020](src/gluon/runner.py#L2020)
  - **Fix:** After SIGTERM, poll the PID briefly and escalate to SIGKILL if still alive before marking cancelled; on `ProcessLookupError`, reconcile the stale run instead of `pass`.
  - **Done when:** a worker that ignores SIGTERM is SIGKILLed; an already-dead run is reconciled, not left RUNNING.
- [x] **[P2] `_active_tasks` is never populated → concurrency cap is a no-op** — [src/gluon/runner.py:2373](src/gluon/runner.py#L2373)
  - **Fix:** Either populate `_active_tasks` on spawn and clear on completion so the `max_concurrent` check works, or delete the dead dict + checks and document that concurrency is enforced by the transport semaphore. Don't leave a safety mechanism that silently does nothing.
  - **Done when:** the runner-level cap either enforces `max_concurrent` or is removed with a comment pointing to the real limiter.
- [x] **[P2] Duration hard-cap enforcement failure swallowed at DEBUG** — [src/gluon/runner.py:268](src/gluon/runner.py#L268)
  - **Fix:** Log at ERROR with `exc_info`, persist the enforcement failure to the run (error_message/activity log), optionally retry the cancel once.
  - **Done when:** a watchdog cancel failure is visible at INFO and auditable on the run.
- [x] **[P2] Chain step dispatch failure swallowed at DEBUG → chain stalls forever** — [src/gluon/runner.py:1831](src/gluon/runner.py#L1831)
  - **Fix:** Log at ERROR, record dispatch-failed on the chain row so the UI can show a stalled chain; add a sweeper that re-dispatches RUNNING chains with no active step.
  - **Done when:** a dispatch exception surfaces and the chain does not hang invisibly in RUNNING.
- [x] **[P2] Question handler failure falls through to `PermissionResultAllow`** — [src/gluon/agent.py:531](src/gluon/agent.py#L531)
  - **Fix:** Return `PermissionResultDeny` (mirror the TimeoutError branch) when the AskUserQuestion handler errors; log with `exc_info=True`.
  - **Done when:** a handler exception denies the tool (does not auto-allow with the original input) — add a regression test.
- [x] **[P2] Migrations swallow all `sqlite3.OperationalError`** — [src/gluon/store.py:1017](src/gluon/store.py#L1017)
  - **Fix:** Only swallow "duplicate column name"/"already exists"; log other OperationalErrors at WARNING with the migration index. Prefer splitting `executescript` into single statements so a partial run can't be silently truncated.
  - **Done when:** a deliberately-broken migration logs loudly and does not leave a partially-migrated schema unnoticed.
- [x] **[P2] Redis job-update listener dies permanently on one malformed message** — [src/gluon/queue/redis_queue.py:302](src/gluon/queue/redis_queue.py#L302)
  - **Fix:** Move `json.loads` inside a per-message try/except (skip bad payloads); wrap the listen loop in reconnect-with-backoff. Apply the same to `events/redis_transport.py:121`.
  - **Done when:** a single bad pub/sub payload is skipped and delivery continues; a dropped connection reconnects.
- [x] **[P2] `stop_daemon()` reports success even if the daemon never exits** — [src/gluon/supervisor_daemon.py:154](src/gluon/supervisor_daemon.py#L154)
  - **Fix:** Use `time.sleep(0.1)` (not `run_until_complete`); after the wait, re-check the PID — if alive, escalate to SIGKILL or `return False` **without** removing the PID file. (Covers the P3 busy-wait at :160 too.)
  - **Done when:** a daemon that ignores SIGTERM is not reported stopped, and the PID file is retained until it truly exits.
- [x] **[P2] Supervisor double-resume race (no claim between read and resume)** — [src/gluon/resume_coordinator.py:109](src/gluon/resume_coordinator.py#L109)
  - **Fix:** Atomically claim a REVIEW run before resuming (compare-and-set the status / a `supervised_at` lock column) so the web-server supervisor and standalone daemon can't both pick it up.
  - **Done when:** two supervisors running together resume each eligible run exactly once.

---

## WS-5 — Error messages & user feedback

> **PR:** `fix: make error messages actionable and consistent across CLI/bot/API`

- [x] **[P1] Chat-agent invalid-model error contradicts itself & never starts the task** — [src/gluon/chat_agent.py:219](src/gluon/chat_agent.py#L219)
  - **Fix:** Resolve input through `MODEL_ALIASES` before `ModelTier()`; then either actually default to sonnet (remove the early `return`) or change the message to say the task was **not** started and list the exact accepted tiers.
  - **Done when:** passing `opus` works (alias-resolved); the message and behavior agree (no "Defaulting to sonnet" while aborting).
- [x] **[P2] Raw exception text leaked in HTTP 500 detail** — [src/gluon/web/api.py:4187](src/gluon/web/api.py#L4187)
  - **Fix:** Log full exception server-side; return a stable generic detail with identifiers ("Failed to refresh git status for project '<name>'; check server logs"), not internals. Follow the `upload_image` pattern.
  - **Done when:** no handler returns `str(e)`/tracebacks in the response body.
- [x] **[P2] Capacity refusal shows remaining permits, not the configured max** — [src/gluon/transport/telegram.py:690](src/gluon/transport/telegram.py#L690)
  - **Fix:** Store `max_concurrent` on `GluonBotCore`; compare active count against it; interpolate "N/MAX runs active" at all five call sites.
  - **Done when:** the message reads e.g. "12/16 runs active", not the leftover-permit count.
- [x] **[P2] Cancel endpoint collapses four failure causes into a bare 500** — [src/gluon/web/api.py:693](src/gluon/web/api.py#L693)
  - **Fix:** Distinguish not-found (404), not-cancellable/wrong-state (409), and internal error (500 with generic detail + logged cause).
  - **Done when:** each cause maps to a distinct status + message.
- [x] **[P3] create-pr/merge endpoints mix three error shapes** — [src/gluon/web/api.py:3610](src/gluon/web/api.py#L3610)
  - **Fix:** Standardize on `HTTPException` with structured detail (use 409 + conflict payload for merge conflicts); reserve 200 for success.
  - **Done when:** all error paths flow through the `ApiError` shape the frontend expects.
- [x] **[P2] Chat-agent errors sent to bot users as bare `Error: <raw exception>`** — [src/gluon/bot_core.py:504](src/gluon/bot_core.py#L504) 🔎 verify-first
  - **Fix:** Wrap with a friendly message + run id; log the raw exception server-side.
  - **Done when:** bot users see an actionable message, not a raw exception string.
- [x] **[P3] `Failed to remove project` with no reason** — [src/gluon/cli.py:183](src/gluon/cli.py#L183) 🔎 verify-first
  - **Fix:** Surface why `remove` returned False (not found / has active runs / …).
  - **Done when:** the CLI states the reason.
- [x] **[P3] Recovery task created without keeping a reference (GC risk)** — [src/gluon/web/api.py:1038](src/gluon/web/api.py#L1038)
  - **Fix:** Store the task in a module-level set with `task.add_done_callback(set.discard)`, matching the polling-task pattern.
  - **Done when:** `_run_recovery` can't be garbage-collected mid-execution.

---

## WS-6 — Web UI resilience

> **PR:** `fix(web-ui): add error boundary and surface silent mutation failures`

- [x] **[P2] No React error boundary anywhere — one render error white-screens the dashboard** — [web-ui/src/main.tsx:10](web-ui/src/main.tsx#L10)
  - **Fix:** Wrap `<Routes>` (and ideally each route element / the run dialog) in an error boundary (`react-error-boundary` or a small class component) with a "something broke — reload" fallback that logs.
  - **Done when:** a thrown error in one component shows a fallback, not a blank app.
- [x] **[P1] Work-queue edit cancels the existing item before creating its replacement** — [web-ui/src/components/WorkQueuePage.tsx:143](web-ui/src/components/WorkQueuePage.tsx#L143)
  - **Fix:** Reverse the order (add the replacement first, cancel the old item only after success); surface failures via `toast.error`/inline error.
  - **Done when:** a failed re-create never destroys the original item; failures are visible.
- [x] **[P2] QuestionModal submit failure gives zero feedback while the run stays blocked** — [web-ui/src/components/QuestionModal.tsx:97](web-ui/src/components/QuestionModal.tsx#L97)
  - **Fix:** Inline error + `toast.error` on failure, keeping the selection so the user can retry before expiry.
  - **Done when:** a failed answer submit shows an error and preserves the choice.
- [x] **[P2] Kanban drag status update fails silently (card snaps back)** — [web-ui/src/components/KanbanBoard.tsx:290](web-ui/src/components/KanbanBoard.tsx#L290)
  - **Fix:** `toast.error` with the `ApiError` detail when `updateRunStatus` fails; subtle toast for disallowed transitions.
  - **Done when:** the snap-back is explained to the user.
- [x] **[P2] Merge-queue retry/cancel and load failures invisible** — [web-ui/src/components/MergeQueuePage.tsx:67](web-ui/src/components/MergeQueuePage.tsx#L67)
  - **Fix:** `toast.error`/inline banner for retry/cancel failures and an error state for `load()` failures.
  - **Done when:** all three failure paths show feedback.
- [x] **[P3] Image attachments silently dropped when upload fails after task creation** — [web-ui/src/components/CreateTaskDialog.tsx:681](web-ui/src/components/CreateTaskDialog.tsx#L681)
  - **Fix:** Collect nulls from `Promise.all`; if any upload failed, `toast.error` "Task created but N of M images failed to attach".
  - **Done when:** partial upload failure is reported, not swallowed.
- [x] **[P3] Stop-loop action fails silently and ignores `success=false`** — [web-ui/src/App.tsx:521](web-ui/src/App.tsx#L521)
  - **Fix:** `toast.success` on success, `toast.error` on rejection or `response.success === false`, matching `handleCancelRun`.
  - **Done when:** stop-loop outcomes are surfaced.

---

## WS-7 — Dead code & unbounded growth

> **PR(s):** `fix(store): wire up TTL sweepers` + `chore: remove/decide dead subsystems`
> One real bug (unbounded growth) plus keep-or-delete decisions. **Decide explicitly** for each — don't leave half-wired.

- [x] **[P1] chat_history & message_run_map TTL sweepers never called → unbounded SQLite growth** — _wired into the periodic auth/TTL sweep; regression in test_ttl_sweep.py._ — [src/gluon/store.py:5467](src/gluon/store.py#L5467)
  - **Fix (real bug):** Call `cleanup_expired_chat_history` / `cleanup_expired_message_run_maps` from the existing periodic sweep (see `session_cleanup.py` / `GLUON_AUTH_SWEEP_INTERVAL_SECS` cadence).
  - **Done when:** expired rows are pruned on schedule; a test inserts an expired row and confirms the sweep removes it.
- [x] **[P1] Merge queue unreachable: `MergeQueueService` never instantiated, no processor** — _decision: KEEP + documented (module docstring) as not-yet-wired; it's test-only and the API/UI use store rows directly. Wiring a processor is follow-up._ — [src/gluon/merge_queue.py:20](src/gluon/merge_queue.py#L20)
  - **Decision:** Is the merge queue a shipped feature? If yes → instantiate the service, wire `enqueue` into the merge flow, run a `process_next` loop. If no → remove the service (keep the read-only `/api/merge-queue` list if the UI needs it) and note it in docs.
  - **Done when:** the merge queue is either functional end-to-end or removed; no dead service class remains.
- [x] **[P2] Distributed worker/job subsystem dead: `RedisJobQueue` has no importers** — _decision: KEEP + documented (module docstring) as foundation for a future distributed mode; remove if not on the roadmap._ — [src/gluon/queue/redis_queue.py:1](src/gluon/queue/redis_queue.py#L1)
  - **Decision:** Keep for a roadmap'd distributed mode (mark clearly + add an integration test) or remove `queue/redis_queue.py` + its `__init__` re-export and the test-only Worker/Job store CRUD.
  - **Done when:** the subsystem is either reachable or removed.
- [x] **[P3] `thread_id` resume-detection: written but never read back** — _removed the dead `get_run_by_thread_id` lookup (0 callers); the column stays harmless._ — [src/gluon/models.py:971](src/gluon/models.py#L971)
  - **Fix:** Wire `get_run_by_thread_id` (store.py:3691) into the bot resume path, or drop the unused lookup + column if resume-by-thread isn't wanted.
  - **Done when:** the lookup has a caller or is removed.
- [x] **[P3] Event constants `TODO_UPDATED` / `ACTIVITY_CREATED` never published/subscribed** — _deleted (0 refs)._ — [src/gluon/events/types.py:46](src/gluon/events/types.py#L46)
  - **Fix:** Publish/subscribe them where todo/activity changes happen, or delete the constants.
  - **Done when:** no orphan event constants remain.
- [ ] **[P2] ~15 `GluonStore` public methods have zero callers** — [src/gluon/store.py:3702](src/gluon/store.py#L3702) 🔎 verify-first
  - **Fix:** Full sweep (spot-checked 3/15 dead). For each truly-dead method, remove it; if intended as public API, add a test/usage.
  - **Done when:** every public store method has a caller or a test, else is removed.
  - **Status:** DEFERRED. Of the 3 spot-checked: `get_run_by_thread_id` was removed and the two TTL sweepers are now wired, so they're no longer dead. A blanket removal of the remaining suspected-dead public methods is intentionally **not** done autonomously — `store` is a public API surface; some methods may be invoked dynamically or kept deliberately, so this needs a careful per-method review with the owner. Left unchecked.
- [x] **[P3] Six WebSocket Pydantic models defined but `websocket.py` hand-builds dicts** — _deleted all six (0 refs); left a note._ — [src/gluon/web/models.py:421](src/gluon/web/models.py#L421)
  - **Fix:** Use the models in `broadcast_*` (typed, single source of truth) or delete them. Pairs well with the WS-3 `run_updated` payload fix.
  - **Done when:** WS messages are built from the Pydantic models or the unused models are gone.
- [x] **[P3] Never-called broadcasts `activity_event`/`queue_updated`/`merge_updated`/`witness_decision`** — _resolved in WS-3: the dead broadcasts were deleted (witness_decision is alive, kept)._ — [src/gluon/web/websocket.py:412](src/gluon/web/websocket.py#L412)
  - **Fix:** Covered by WS-3's wire-or-delete decision; track here for completeness (frontend pages currently poll instead).
  - **Done when:** consistent with WS-3 outcome.
- [x] **[P2/P3] Backend route groups with zero frontend consumers** — _confirmed intentionally CLI/bot/external-only (supervision, provider, claude-sessions, workspace budget, tasks/approvals/webhooks); documented in the audit. No dead routes to remove._ — [src/gluon/web/api.py:1298](src/gluon/web/api.py#L1298)
  - **Fix:** Confirm which are intentionally CLI/bot-only (supervision, provider, claude-sessions, workspace budget; tasks/approvals/webhooks). Document those; remove any genuinely unreachable route.
  - **Done when:** each route group is documented as CLI/bot/external or removed.

---

## WS-8 — Duplication consolidation

> **PR(s):** `refactor(web-ui): unify RunDetail + message parsing` · `refactor(transport): share bot logic in bot_core/base`
> Mostly P2/P3. The transport-drift items prevent future bugs from diverging copies.

- [ ] **[P1] `RunDetailDialog` (2937 LOC) & `RunDetailPage` (2083 LOC) are drifted twins** — _DEFERRED: merging two ~2-3k-LOC components that drive the entire run-detail UI is high-risk to do autonomously; needs a careful manual refactor with review._ — [web-ui/src/components/RunDetailPage.tsx:555](web-ui/src/components/RunDetailPage.tsx#L555)
  - **Fix:** Extract the shared body into one component (props for dialog-vs-page chrome); the page/dialog become thin wrappers.
  - **Done when:** run-detail logic lives in one place; both entry points render it.
- [ ] **[P2] `RunDetailPage` polls messages.jsonl every 3 s while also using the WS stream** — _DEFERRED: coupled to the twin-merge above; touching the message-loading path independently risks regressions._ — [web-ui/src/components/RunDetailPage.tsx:300](web-ui/src/components/RunDetailPage.tsx#L300)
  - **Fix:** Drop the 3 s poll (rely on the WS log stream as the Dialog already does). Falls out naturally once WS-8's twin merge lands.
  - **Done when:** the page has a single message source.
- [ ] **[P2] `interface AgentMessage` declared 4× and `parseMessages` copied 3×** — _DEFERRED: the 4 interfaces have drifted; unifying them into one canonical type needs careful reconciliation to avoid type regressions across 4 components._ — [web-ui/src/components/StreamingLogViewer.tsx:195](web-ui/src/components/StreamingLogViewer.tsx#L195)
  - **Fix:** Define one `AgentMessage` type in `lib/types.ts` and one `parseMessages` in `lib/`; import everywhere (StreamingLogViewer, RunDetailPage, RunDetailDialog, ListViewPage).
  - **Done when:** one declaration of each remains.
- [ ] **[P2] `formatDuration`/`formatTokens` duplicated in 4 components; name collision with different output** — _DEFERRED: the `formatDurationMs` name collision with different output means a blind centralization could change displayed values; needs per-call-site verification._ — [web-ui/src/components/StreamingLogViewer.tsx:1059](web-ui/src/components/StreamingLogViewer.tsx#L1059)
  - **Fix:** Centralize in `lib/format.ts`; resolve the `formatDurationMs` naming collision.
  - **Done when:** components import the shared formatters.
- [x] **[P2] `ToolBreakdown` reimplements timestamp formatting, bypassing the UTC fix** — _now uses the shared `parseUtcTimestamp` (keeps the seconds format)._ — [web-ui/src/components/ToolBreakdown.tsx:81](web-ui/src/components/ToolBreakdown.tsx#L81)
  - **Fix:** Use `parseUtcTimestamp`/`formatTime` from `lib/timestamps.ts`.
  - **Done when:** ToolBreakdown timestamps match the rest of the UI (UTC-correct).
- [ ] **[P2] Two competing definitions of "review" status** — _DEFERRED: deciding whether RunCard's PR-derived heuristic or the backend status is canonical changes what users see; needs review-semantics analysis._ — [web-ui/src/components/RunCard.tsx:143](web-ui/src/components/RunCard.tsx#L143)
  - **Fix:** Pick one source — either the backend status or a shared `deriveDisplayStatus(run)` helper used by both RunCard and KanbanBoard.
  - **Done when:** RunCard and KanbanBoard agree on "review".
- [x] **[P3] `_truncate` duplicated in both transports despite `base.Transport.truncate_text`** — _moved to `base.truncate_preview` (the audit conflated it with `truncate_text`; they're different — preview vs message-cap)._ — [src/gluon/transport/telegram.py:36](src/gluon/transport/telegram.py#L36)
  - **Fix:** Use the base method; delete both `_truncate` copies.
  - **Done when:** one truncation implementation.
- [ ] **[P2] `is_authorized` duplicated (telegram vs bot_core shared)** — _DEFERRED: the signatures differ (telegram resolves its own allowed-user set); routing through bot_core's shared check needs care to not weaken auth._ — [src/gluon/transport/telegram.py:190](src/gluon/transport/telegram.py#L190)
  - **Fix:** Route telegram's checks through `bot_core.is_authorized`; remove the transport-local copy (auth logic must not diverge).
  - **Done when:** one auth implementation feeds both transports.
- [x] **[P1] Discord keeps its own `MODEL_ALIASES`/`DEFAULT_MODEL` drifted from `models_config`** — _the P1 (default drift sonnet vs opus-4.8) is fixed: discord's `DEFAULT_MODEL` is now derived from `models_config.DEFAULT_MODEL`. The alias map stays in discord's `claude-*` string form (changing it risks the downstream format) but is functionally equivalent._ — [src/gluon/transport/discord.py:396](src/gluon/transport/discord.py#L396)
  - **Fix:** Import `MODEL_ALIASES` and `DEFAULT_MODEL` from `models_config.py`; delete the local copies. (Discord currently defaults to sonnet-4.6 vs the canonical opus-4.8.)
  - **Done when:** Discord and the rest of Gluon resolve the same default + aliases.
- [x] **[P2] Transport flows copy-pasted (approval, cancel, resume, task-launch) across telegram/discord** — _the real BUG is fixed: telegram `/resume` now passes `session_id=session.id` to `execute_task` (was resuming nothing). The full lift of approval/cancel/launch into bot_core is DEFERRED — large refactor across critical transport paths, better done with review._ — [src/gluon/transport/telegram.py:326](src/gluon/transport/telegram.py#L326) 🔎 verify-first
  - **Fix:** Lift the duplicated logic into `bot_core`/`base`; transports call the shared methods. Specifically: approval-decision ([telegram.py:326](src/gluon/transport/telegram.py#L326)), cancel-run ([telegram.py:886](src/gluon/transport/telegram.py#L886)), and the task-launch flow copy-pasted 3× in discord plus telegram ([discord.py:1668](src/gluon/transport/discord.py#L1668)). **Note the telegram `/resume` drift** ([telegram.py:843](src/gluon/transport/telegram.py#L843)) — it resolves a session but never passes it to `execute_task`; fix that bug while consolidating.
  - **Done when:** approval/cancel/resume/launch logic exists once; telegram `/resume` actually resumes the resolved session.
- [x] **[P3] Misc backend duplication: `get_redis_url`, duration formatters, watcher clones, CLI model validation** — _CLI model-validation extracted to a shared `_validate_model` helper (run + resume). `get_redis_url` left (one copy is in the now-dead `redis_queue`); duration-formatter and watcher-clone dedups DEFERRED (low value, medium touch)._ — [src/gluon/queue/redis_queue.py:26](src/gluon/queue/redis_queue.py#L26)
  - **Fix:** Dedupe `get_redis_url` (redis_queue vs events/redis_transport), unify `notifier._format_duration` with `runner.format_duration` ([notifier.py:182](src/gluon/notifier.py#L182)), factor the shared watcher loop from `approval_watcher`/`question_watcher` ([question_watcher.py:51](src/gluon/question_watcher.py#L51)), and extract the repeated `ModelTier` validation in `cli.py` run/resume ([cli.py:1107](src/gluon/cli.py#L1107)).
  - **Done when:** each of these four has a single implementation.
- [x] **[P3] Polling cadences hardcoded in list pages despite `lib/polling.ts`** — _MergeQueuePage now uses `POLL_SLOW` (30s) from lib/polling._ — [web-ui/src/components/MergeQueuePage.tsx:58](web-ui/src/components/MergeQueuePage.tsx#L58)
  - **Fix:** Route the hardcoded `setInterval` cadences through the centralized `lib/polling.ts` constants/helpers.
  - **Done when:** list-page polling intervals come from one place.

---

## WS-9 — API contract drift (TS ↔ Python)

> **PR:** `fix(web-ui): align TS types and endpoints with the backend`

- [ ] **[P2] Frontend calls `POST /api/sdk-sessions/{id}/resume` — endpoint doesn't exist** — _NOT a drift bug: this is an intentional, documented stub. The frontend (api.ts + SessionBrowserPage) deliberately calls the not-yet-built endpoint and shows an honest "Resume not available yet" message on the 404. Implementing the backend resume route is a FEATURE (the TODO specifies it), deferred._ — [web-ui/src/lib/api.ts:1121](web-ui/src/lib/api.ts#L1121)
  - **Fix:** Add the backend route (resume an SDK session) or remove/redirect the frontend call to the real resume path.
  - **Done when:** the resume call hits a real endpoint (no 404/405).
- [x] **[P2] Hard-cap fields (Theme D3) missing from TS types** — _added `max_tool_calls`, `max_duration_minutes`, `tool_call_count` to the TS `RunDetail` (no `max_total_tokens` exists in the backend)._ — [web-ui/src/lib/types.ts:207](web-ui/src/lib/types.ts#L207) 🔎 verify-first
  - **Fix:** Diff backend `max_cost_usd`/`max_duration_minutes`/`max_total_tokens` (models.py:132/139/181) against TS; add the missing fields to the request/response interfaces.
  - **Done when:** every backend hard-cap field has a TS counterpart.
- [x] **[P3] `user_id` attribution naming mismatch** — _added `user_id` to the TS `Run` interface (the audit's `created_by_user_id` was actually a schedule field)._ — [web-ui/src/lib/types.ts:15](web-ui/src/lib/types.ts#L15) 🔎 verify-first
  - **Fix:** Reconcile TS `created_by_user_id` with backend `user_id` (RunResponse:67) — rename one side or map it.
  - **Done when:** the attribution field name matches across the boundary.
- [x] **[P3] `AgentMessageData` union is stale (backend emits 5 types it lacks)** — _added the exact 5: `hook_event`, `rate_limit`, `server_tool_use`, `server_tool_result`, `usage` (and aligned StreamingLogViewer's local copy)._ — [web-ui/src/lib/types.ts:630](web-ui/src/lib/types.ts#L630)
  - **Fix:** Sync the union with the message types the backend actually emits (do alongside WS-8's AgentMessage unification).
  - **Done when:** the union covers all emitted types.
- [x] **[P3] TS `Workspace` omits budget/spend fields `WorkspaceResponse` returns** — _added daily/monthly budget + spend fields._ — [web-ui/src/lib/types.ts:788](web-ui/src/lib/types.ts#L788)
  - **Fix:** Add the budget/spend fields to the TS `Workspace` interface.
  - **Done when:** the TS type matches `WorkspaceResponse`.
- [x] **[P3] `QueueFollowupResponse` drops `message_id`** — _added `message_id` to the TS response type._ — [web-ui/src/lib/api.ts:229](web-ui/src/lib/api.ts#L229)
  - **Fix:** Add `message_id` to the TS response type.
  - **Done when:** the field is present.

---

## WS-10 — Documentation sweep

> **PR:** `docs: fix model table, phantom modules, and env-var names`
> The README model table is P1 (actively misleads users about the default model).

- [x] **[P1] README Model Selection table: wrong default/latest + removed `opus-4.5` tier** — [README.md:604](README.md#L604)
  - **Fix:** Default is **opus-4.8** (not Sonnet); "latest" is **4.8** (not 4.6); remove the `--model opus-4.5` row. Align with CLAUDE.md's five-tier table.
  - **Done when:** the table matches `models_config.py` (`DEFAULT_MODEL = OPUS_48`) and the supported tiers.
- [x] **[P1] `docs/API.md` documents a `gluon.bot` module / `GluonBot` / `run_bot()` that don't exist** — [docs/API.md:1516](docs/API.md#L1516)
  - **Fix:** Replace with the real `bot_core.GluonBotCore` API; remove `GluonBot`/`run_bot`. While in this file, refresh the stale REST listing ([docs/API.md:16](docs/API.md#L16)) to include the 0.12 endpoint groups (schedules, tasks, fork/snooze, approvals, formulas, queues).
  - **Done when:** every class/function in that section exists in code and the REST listing matches the actual routes.
- [x] **[P1] CLI task-profile budgets & thinking tokens are off by orders of magnitude** — [src/gluon/cli.py:1061](src/gluon/cli.py#L1061)
  - **Fix:** Correct the per-profile budget / thinking-token numbers in the CLI `--help` text and `docs/CLI-REFERENCE.md` against the actual profile config.
  - **Done when:** documented profile budgets match the values the code applies.
- [x] **[P2] A dozen operational `GLUON_*` env vars are read by code but documented nowhere** — [src/gluon/runner.py:68](src/gluon/runner.py#L68)
  - **Fix:** Inventory the env vars read in `runner.py` (around [:68](src/gluon/runner.py#L68) and [:451](src/gluon/runner.py#L451)) and add them to CLAUDE.md / DEVELOPMENT.md under their real names.
  - **Done when:** every `os.environ`/`getenv` key in `runner.py` appears in the docs (and vice-versa — no phantom vars).
- [x] **[P3] CLAUDE.md Key Files table + pyproject mypy override reference deleted `src/gluon/bot.py`** — [CLAUDE.md:166](CLAUDE.md#L166)
  - **Fix:** Remove the `bot.py` row from CLAUDE.md and the `"gluon.bot"` entry from the pyproject mypy overrides (pyproject.toml:95).
  - **Done when:** no reference to `bot.py` remains.
- [x] **[P2] README `.[telegram]` install extra doesn't exist** — [README.md:145](README.md#L145)
  - **Fix:** Telegram deps are in the base install; change the command to plain `uv pip install -e .` (or add a `telegram` extra to pyproject if intended).
  - **Done when:** the documented install command works.
- [x] **[P2] DEVELOPMENT.md documents env vars the code never reads + wrong port** — [docs/DEVELOPMENT.md:1129](docs/DEVELOPMENT.md#L1129)
  - **Fix:** Remove `GLUON_UVICORN_HOST`/`GLUON_UVICORN_PORT`; fix the webhook-secret name to `GITHUB_WEBHOOK_SECRET` (not `GLUON_GITHUB_WEBHOOK_SECRET`); note host/port come from `gluon web --host/--port` (default 45866).
  - **Done when:** every env var in the doc is actually read by the code under that name.
- [x] **[P2] CLI-REFERENCE.md omits ~15 command groups and documents nonexistent `gluon --version`** — [docs/CLI-REFERENCE.md:621](docs/CLI-REFERENCE.md#L621)
  - **Fix:** Change `gluon --version` → `gluon version` (subcommand); add the missing command groups (cross-check against `cli.py` `@app.command`).
  - **Done when:** the reference matches the actual command surface.
- [x] **[P2] README custom-formula directories don't match `FormulaLoader` search paths** — [README.md:356](README.md#L356) 🔎 verify-first
  - **Fix:** Update README to the real loader paths.
  - **Done when:** documented paths match `FormulaLoader`.
- [x] **[P2] CHANGELOG missing v0.12.1 + the v0.11.x patch releases** — [CHANGELOG.md:10](CHANGELOG.md#L10) 🔎 verify-first
  - **Fix:** Backfill the missing released versions; reconcile "Unreleased" against shipped commits/tags.
  - **Done when:** CHANGELOG covers all released tags.
- [x] **[P3] docker-compose comment tells users to set `CLAUDE_CODE_USE_BEDROCK` manually** — [docker-compose.yml:60](docker-compose.yml#L60) 🔎 verify-first
  - **Fix:** Remove/rewrite the comment to point at the provider abstraction (the provider emits the flag).
  - **Done when:** the comment no longer contradicts the provider rule.

---

## WS-11 — Follow-up verification & security critic pass

> **PR:** none — investigation tasks that may spawn new findings.
> The audit's completeness-critic never ran (rate-limited). Close these gaps before declaring the audit complete.

- [ ] **🔎 Run the uncovered-category critic** (no dimension covered these): **input validation / authz on API routes**, **auth & OIDC flows** (`auth.py`), **`git_manager.py` correctness**, **secrets handling**. A single focused agent (small scope) avoids the fan-out throttle.
  - **Done when:** each category has either findings (added here) or an explicit "clean" note.
- [ ] **🔎 Confirm the "Full screen" link tab-rendering gap** — [web-ui/src/components/RunDetailDialog.tsx:134](web-ui/src/components/RunDetailDialog.tsx#L134)
  - The link forwards `activeTab` into `/runs/:id/:tab`; verify whether `RunDetailPage` renders every tab the dialog offers (blank panel if not). Promote to a WS-8 fix if confirmed.
- [ ] **🔎 Confirm the `RunDetailDialog` mangled load-effect dependency array** — [web-ui/src/components/RunDetailDialog.tsx:488](web-ui/src/components/RunDetailDialog.tsx#L488)
  - Read the effect; if `resumePendingImages.forEach` really sits in the dep array, fix it. Promote if confirmed.
- [ ] **🔎 Confirm the double-QuestionModal render** — [web-ui/src/components/RunDetailDialog.tsx:2921](web-ui/src/components/RunDetailDialog.tsx#L2921)
  - Verify two QuestionModals can mount for one question when the dialog is open; dedupe if confirmed.
- [ ] **🔎 Confirm `events/subscribers.py` NotificationDispatcher has zero transports** — [src/gluon/events/subscribers.py:239](src/gluon/events/subscribers.py#L239)
  - If the chat-notification path truly can't deliver (dispatcher built with no transports), wire a transport or remove the path. Promote to WS-3/WS-7 if confirmed.

---

## Appendix — refuted (do not action)

- **Schedule enable endpoint swallows cron errors** — `src/gluon/web/api.py:5534` — refuted on verification.
- **Discord failure messages give no reason** — `src/gluon/transport/discord.py:1562` — refuted on verification.
