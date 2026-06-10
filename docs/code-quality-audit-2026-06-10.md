# Gluon Agent — Code Quality Audit

**Date:** 2026-06-10 · **Branch:** `main` @ `391c872` · **Status:** verification complete

## Methodology

Multi-agent workflow: 9 parallel research agents (one per quality dimension) each citing file:line + verbatim evidence, followed by adversarial verifier agents instructed to refute claims against current source. The verifier fan-out was twice throttled by API limits, so the remaining findings were **verified inline this session** by directly reading the cited code (greps for callers/definitions, reading the actual control flow). Each finding below carries its verification provenance.

**Verification tiers:**
- **Confirmed** — independently re-read and verified (by a verifier agent or inline this session).
- **Corrected** — claim was partly wrong; the accurate version is recorded.
- **Needs focused follow-up** — the core is verified but one sub-claim needs a targeted diff.
- **Pattern-consistent** — belongs to a cluster whose representatives were confirmed, but this specific line was not individually re-read. Treat as high-probability, not proven.

## Scoreboard

| Tier | P0 | P1 | P2 | P3 | Total |
|---|---|---|---|---|---|
| **Confirmed** | 0 | 16 | 38 | 16 | **70** |
| **Pattern-consistent** | 0 | 3 | 12 | 8 | **23** |
| **Needs follow-up** | | | | | 3 |
| **Corrected / refuted** | | | | | 3 |

Of 99 distinct findings (one cross-dimension duplicate merged): **70 confirmed**, 23 pattern-consistent, 3 need a focused follow-up, 1 corrected, 2 refuted.

## Confirmed findings

### P1 — Real bugs & significant hazards

#### 1. README Model Selection table documents removed `opus-4.5` tier and wrong default/latest model

**[README.md:604](README.md#L604)** · Documentation drift · _verified: inline this session_

The Model Selection table says Sonnet is the default, calls Opus 4.6 'latest', and tells users to run `--model opus-4.5`. Commit 94b1b65 ('feat: add Opus 4.8 (new default) and 4.7 tiers, remove Opus 4.5 (#124)') changed all three facts: ModelTier has no 4.5 member, MODEL_ALIASES has no opus-4.5 entry, and DEFAULT_MODEL is OPUS_48. A user following this table gets `ValueError: Invalid model: opus-4.5` from LLMProviderConfig.get_model_id (src/gluon/llm_provider.py:124-129). It also contradicts CLAUDE.md and docs/CLI-REFERENCE.md, which both correctly say opus-4.8 is the default.

<details><summary>Verification note</summary>

README Model Selection table (607) says Sonnet is '(default)' but code default is ModelTier.OPUS_48; labels Opus 4.6 '(latest)' when latest is 4.8; documents `--model opus-4.5` which is not a supported tier. Three errors.

</details>

#### 2. docs/API.md documents a `gluon.bot` module, GluonBot class, and run_bot() that no longer exist

**[docs/API.md:1516](docs/API.md#L1516)** · Documentation drift · _verified: inline this session_

API.md has a full reference section for `gluon.bot` with class GluonBot (build_application, run_polling) and a run_bot() function. The module src/gluon/bot.py was deleted; grep for 'GluonBot|run_bot' across src/gluon returns nothing. The Telegram bot is now implemented by TelegramTransport (src/gluon/transport/telegram.py) driven by the `gluon bot` CLI command. Anyone importing per this doc gets ModuleNotFoundError.

<details><summary>Verification note</summary>

API.md documents gluon.bot module, GluonBot class (1521), run_bot() (1545) — none exist; only bot_core.GluonBotCore (bot_core.py:35) is real.

</details>

#### 3. Detached worker subprocesses are never reaped — zombies defeat crash detection and keep runs RUNNING forever

**[src/gluon/runner.py:992](src/gluon/runner.py#L992)** · Async / runtime quality · _verified: inline this session_

`_spawn_background_process` creates a `subprocess.Popen` and discards the object without ever calling `wait()`; there is no `waitpid`/SIGCHLD handling anywhere in src/gluon (grep confirms). When a worker crashes (OOM, segfault), it becomes a zombie child of the long-lived web server and is only reaped lazily when a future `Popen` construction triggers `subprocess._cleanup()`. Liveness checks use `os.kill(run.pid, 0)` (runner.py:2131, 294), which SUCCEEDS for zombies — so `refresh_run_status` never detects the death, `_salvage_uncommitted_work_sync` never runs, and the run shows RUNNING indefinitely on an idle server. Stale PIDs are also reusable after a server restart, so `os.kill(pid, 0)` can match an unrelated process.

<details><summary>Verification note</summary>

Worker spawned via subprocess.Popen(start_new_session=True) with stdout/stderr→DEVNULL; no .wait()/.poll()/waitpid anywhere → detached workers are never reaped (zombie risk).

</details>

#### 4. Sync git subprocesses (up to 120 s timeout) run on the event loop via refresh_all_runs in async handlers and the 2 s polling loop

**[src/gluon/web/api.py:425](src/gluon/web/api.py#L425)** · Async / runtime quality · _verified: inline this session_

The async `list_runs` handler (api.py:425), `get_run` (api.py:461), and the `_poll_run_status_changes` background task that runs every 2 seconds (api.py:5650) all call `runner.refresh_all_runs()` / `refresh_run_status()` synchronously. When a dead process is detected, that path executes `_salvage_uncommitted_work_sync` which runs blocking `subprocess.run` git commands with timeouts of 30 s (status), 30 s (add), 60 s (commit), and 120 s (push over the network) — runner.py:2193-2259. While any of these run, the single event loop is frozen: every API request, WebSocket stream, scheduler tick, and supervision poll stalls.

<details><summary>Verification note</summary>

refresh_all_runs (runner.py:2278) and refresh_run_status (2113) are SYNC defs running git subprocesses; called directly inside async list_runs/get_run handlers; run_in_executor/to_thread appear NOWHERE in api.py → block the event loop. list_runs is polled frequently by the UI.

</details>

#### 5. _workspace_env mutates process-wide os.environ across await points — cross-workspace credential bleed

**[src/gluon/web/api.py:236](src/gluon/web/api.py#L236)** · Async / runtime quality · _verified: inline this session_

`_workspace_env` injects workspace secrets (e.g. per-org GH_TOKEN) into `os.environ` and restores them after the block. It wraps `await` calls in at least six async handlers (api.py:3580, 3653, 4025, 4115, 4183, 4280), so the mutated environment is visible to every other coroutine while the awaited git/PR operation runs. Concretely, a concurrent `create_run` calls `_spawn_background_process`, which captures `env = os.environ.copy()` (runner.py:952) — a run for workspace B can inherit workspace A's GH_TOKEN, and overlapping `_workspace_env` blocks restore each other's values incorrectly.

<details><summary>Verification note</summary>

SECURITY: _workspace_env mutates process-wide os.environ and the with-block holds an `await` (push_branch_and_create_pr at 3582; refresh_status at 4184). Concurrent requests for different workspaces interleave on the shared os.environ → cross-workspace credential bleed. (The blocking-subprocess bug partially masks it, but the await sites are real.)

</details>

#### 6. Multi-process SQLite access without WAL or busy_timeout — lock contention and 'database is locked' errors, waited out on the event loop

**[src/gluon/store.py:886](src/gluon/store.py#L886)** · Async / runtime quality · _verified: inline this session_

Every store call opens a fresh `sqlite3.connect(self.db_path)` with only `PRAGMA foreign_keys = ON` — no `journal_mode=WAL`, no explicit `busy_timeout` (default 5 s). The same DB file is written concurrently by the web-server process, every detached worker subprocess (`python -m gluon.runner`, one per run, each constructing its own GluonStore), and the optional supervisor daemon. In the default rollback-journal mode any writer takes an exclusive lock that blocks all readers/writers in other processes; under load this surfaces as 5-second stalls (blocking the server's event loop, since all store calls are sync in async handlers) followed by `sqlite3.OperationalError: database is locked`.

<details><summary>Verification note</summary>

Only PRAGMA is foreign_keys=ON (line 888). No journal_mode=WAL and no busy_timeout anywhere → multi-process access (web server + detached workers + supervisor all open the same DB) will hit 'database is locked', and the wait happens on the event loop.

</details>

#### 7. discord.py maintains its own MODEL_ALIASES and DEFAULT_MODEL that have drifted from models_config.py

**[src/gluon/transport/discord.py:396](src/gluon/transport/discord.py#L396)** · Python duplication · _verified: inline this session_

discord.py:396-408 defines a second MODEL_ALIASES table (string->string) and DEFAULT_MODEL = "claude-sonnet-4.6", shadowing the canonical models_config.MODEL_ALIASES (models_config.py:21, string->ModelTier) and DEFAULT_MODEL = ModelTier.OPUS_48 (models_config.py:42). They have already drifted: the canonical table has no bare 'sonnet'/'haiku' aliases while Discord's does, and Discord tasks default to sonnet while CLI/web/orchestrator default to opus-4.8 (CLAUDE.md states the default is claude-opus-4.8). The hardcoded _handle_models_command text (discord.py:1209) also claims 'sonnet... (default)'. Any new model tier must now be added in two unrelated files or Discord goes silently stale.

<details><summary>Verification note</summary>

discord.py keeps its own MODEL_ALIASES (396) and DEFAULT_MODEL='claude-sonnet-4.6' (408), but models_config.py DEFAULT_MODEL=ModelTier.OPUS_48. Discord users get a DIFFERENT default model (sonnet-4.6) than the canonical opus-4.8.

</details>

#### 8. WorktreeError silently disables worktree isolation with no log at all

**[src/gluon/runner.py:1085](src/gluon/runner.py#L1085)** · Python error handling · _verified: verifier agent_

When the user requested an isolated worktree (run.use_worktree) and creation fails, the handler flips use_worktree off and runs the agent directly in the user's main checkout. The comment claims 'Log warning' but there is no logger call, no stderr write, and no AgentMessage — the user gets no signal that the isolation guarantee was dropped and the agent may now mutate their primary working tree. The equivalent path in core.py:761-763 at least logs a warning, so this path is strictly worse.

```
except WorktreeError:
    # Log warning but continue with main directory
    run.use_worktree = False
    worktree_manager = None
```

**Fix:** Log the WorktreeError at WARNING with run id and reason, write a line to the run's stderr log/messages.jsonl, and consider failing the run instead of silently falling back when isolation was explicitly requested.

<details><summary>Verification note</summary>

Verified runner.py:1085-1088: the comment says 'Log warning' but there is no logger call (logger is defined at line 44 and used ~30 times elsewhere in the file), no stderr write, and flipping run.use_worktree=False also suppresses the downstream worktree-context stdout message and persists the flag, erasing the original isolation request. The equivalent core.py:761-763 path does logger.warning, so the user-requested isolation guarantee is dropped with zero signal while the agent runs in the main checkout — a significant hazard.

</details>

#### 9. Chat-agent invalid-model error contradicts itself and lies about defaulting to sonnet

**[src/gluon/chat_agent.py:219](src/gluon/chat_agent.py#L219)** · Python error messages · _verified: verifier agent_

The run_task tool's schema and error message both tell the user to pass 'opus/sonnet/haiku', but validation uses ModelTier(model.lower()) which only accepts 'opus-4.8', 'opus-4.7', 'opus-4.6', 'sonnet', 'haiku' (src/gluon/models_config.py:13-17). 'opus' is only valid via MODEL_ALIASES, which is never consulted, so a user (or the LLM following the tool's own docs) gets "Invalid model 'opus'. Use opus/sonnet/haiku." The message also claims "Defaulting to sonnet." but the return statement aborts the tool before the dead `model = "sonnet"` line, so no pending task is created — the user is told a default will be used while the task silently never starts.

```
try:
    ModelTier(model.lower())
except ValueError:
    return {
        "content": [
            {
                "type": "text",
                "text": f"Error: Invalid model '{model}'. Use opus/sonnet/haiku. Defaulting to sonnet.",
            }
        ]
    }
    model = "sonnet"  # unreachable
```

**Fix:** Resolve the input through MODEL_ALIASES before ModelTier(), and either actually default to sonnet (remove the early return) or change the message to state the task was not started and list the exact accepted values.

<details><summary>Verification note</summary>

Verified chat_agent.py:211-226: ModelTier (models_config.py:13-17) has no 'opus' value and MODEL_ALIASES is never consulted, so the tool's own documented input 'opus' raises ValueError; the return at line 215 precedes the unreachable model='sonnet' at line 223 and self._pending_task is never set, so the task never starts despite the message claiming 'Defaulting to sonnet.'

</details>

#### 10. Runs that end with SDK is_error=True are reported to users as '✅ Complete'

**[src/gluon/agent.py:1185](src/gluon/agent.py#L1185)** · Python error messages · _verified: verifier agent_

In the ResultMessage handler, `msg.is_error` is only copied into message metadata; the `success` flag (initialised True at line 864) is flipped only in the except blocks (lines 1272-1345). A run that terminates with an error-type ResultMessage (e.g. error_during_execution, max-turns errors — which do not raise) therefore yields AgentResult(success=True, error=None). Downstream, core.py:912 calls run.mark_review() on success and bot_core.py:428-433 sends "✅ **Complete** ... Cost: ..." to Telegram/Discord users — the user gets a success message and no error at all for a failed run.

```
yield AgentMessage(
    type="result",
    content=msg.result or "Execution complete",
    metadata={
        ...
        "is_error": msg.is_error,
# success is never updated:
# line 864: success = True
# line 1381: success=success, error=error_msg  (only except blocks set False)
```

**Fix:** In the ResultMessage branch, set `success = not msg.is_error` and capture `error_msg = msg.result` (or msg.errors) when is_error is True, so AgentResult and every user-facing summary reflect the real outcome.

<details><summary>Verification note</summary>

Verified agent.py: success=True at line 864 is only flipped in except blocks (1272-1344); msg.is_error is only stored in metadata (line 1185) and subtype is never checked, so an error ResultMessage yields AgentResult(success=True, error=None). Downstream core.py:912 marks the run REVIEW (and runs post-task git sync) and bot_core.py:428-433 sends '✅ Complete' to users for the failed run.

</details>

#### 11. Merge queue is unreachable end-to-end: MergeQueueService never instantiated, nothing enqueues, no processor loop

**[src/gluon/merge_queue.py:20](src/gluon/merge_queue.py#L20)** · Wiring / dead code · _verified: inline this session_

MergeQueueService (155 lines: enqueue, process_next, test_merge, apply_merge, backoff) is imported only by tests/test_merge_queue.py — no production code constructs it. store.enqueue_merge (store.py:5924) is called only from merge_queue.py itself, so no entry can ever enter the merge_queue table. Yet the CLI ships user-facing commands 'gluon merge list/retry/cancel' (cli.py:3487-3551): 'merge retry' resets an entry to PENDING that no daemon or loop will ever pick up (no caller of process_next exists). The feature is advertised at the CLI surface but cannot function.

<details><summary>Verification note</summary>

MergeQueueService (class/enqueue/process_next) is never instantiated anywhere outside merge_queue.py; process_next (the processor loop) has 0 callers. The /api/merge-queue endpoints read store rows directly and retry via store, never the service. The service layer is dead.

</details>

#### 12. chat_history and message_run_map TTL sweepers exist but are never called — unbounded SQLite growth

**[src/gluon/store.py:5467](src/gluon/store.py#L5467)** · Wiring / dead code · _verified: inline this session_

Every bot message persists a chat_history row with expires_at (bot_core.py:108 create_chat_history) and Discord persists message_run_map rows with expires_at (transport/discord.py:1523, 1626, 1755). The matching sweepers cleanup_expired_chat_history (store.py:5467) and cleanup_expired_message_run_maps (store.py:5339) have zero callers anywhere in src or tests — reads filter on 'expires_at > now' but expired rows are never deleted, so both tables grow without bound in a long-running daemon. The auth subsystem got a periodic sweep (GLUON_AUTH_SWEEP_INTERVAL_SECS) but these two never got wired into any cleanup loop.

<details><summary>Verification note</summary>

cleanup_expired_chat_history (5467) and cleanup_expired_message_run_maps (5339) have ZERO callers anywhere (src + tests) → TTL sweep never runs → unbounded SQLite growth.

</details>

#### 13. RunDetailDialog (2937 LOC) and RunDetailPage (2083 LOC) are copy-pasted twins that have drifted

**[web-ui/src/components/RunDetailPage.tsx:555](web-ui/src/components/RunDetailPage.tsx#L555)** · Web UI duplication & quality · _verified: inline this session_

At least 18 handlers plus helpers (handleCopyLogs, handleResumePaste, handleResume, handleQueueFollowup, handleSendNow, handleResumePromptChange, handleCommandSelect, handleFileSelect, handleAutocompleteClose, handleEditQueuedMessage, handleDeleteQueuedMessage, handleExpandHistoryRun, handleExpandCommit, handleExpandFile, handleCreatePr, handleMerge, formatDuration, formatTokens, parseMessages, AgentMessage, ResumePendingImage) exist verbatim in both files — `diff` of handleSendNow (Dialog:931-975 vs Page:555-599) and of handleCommandSelect/handleFileSelect (Dialog:1002-1060 vs Page:626-684) reports zero differences. They have already drifted: the Dialog's handleMerge (line 1186) gained a merge-conflict resolution flow (handleResolveConflicts, scroll-to-resume prefill, line 1245) that the Page's handleMerge (line 803) lacks; the Dialog has recover/witness/todos/questions features the Page never got; PR polling cadence differs (7s at Dialog:576 vs 30s at Page:365). Every fix now has to land twice and routinely lands once. Notably, the Page is mounted standalone via main.tsx:34 (outside App), so the App-level global QuestionModal (App.tsx:818) does not exist there and the Page itself never fetches questions — a run asking a question on the full-screen page blocks silently.

<details><summary>Verification note</summary>

RunDetailDialog.tsx (2937 LOC) and RunDetailPage.tsx (2083 LOC) are parallel twins — both declare their own `interface AgentMessage`, both copy parseMessages, both poll on independent intervals. Drifted (see polling-race below).

</details>

#### 15. useWebSocket teardown cannot stop the reconnect loop — unmounted hooks leak immortal sockets

**[web-ui/src/hooks/useWebSocket.ts:42](web-ui/src/hooks/useWebSocket.ts#L42)** · Web UI error handling · _verified: verifier agent_

ws.onclose unconditionally schedules connect() after 3s. disconnect() clears the timer and then calls ws.close(), but the close event fires asynchronously AFTER disconnect returns and re-arms the timer — so after unmount the hook reconnects forever with nothing left to cancel it. Every App unmount (navigating /board → /runs/:runId full-page view, logout, StrictMode dev double-mount) leaks a permanently reconnecting socket whose onmessage handler still runs: handlersRef in useRunsWithWebSocket keeps firing real browser notifications, and a leaked instance's run_created handler ([runMessage.run, ...prev]) can double-insert cards while both sockets are live.

```
ws.onclose = (event) => {
  setState({ connected: false, error: null })
  console.log('[WS] Disconnected', event.code)

  // Auto-reconnect after 3 seconds
  reconnectTimeoutRef.current = window.setTimeout(() => {
    console.log('[WS] Reconnecting...')
    connect()
  }, 3000)
}
```

**Fix:** Add an intentional-close flag (or null out ws.onclose) in disconnect() before calling close(), and check it inside onclose before scheduling reconnect; same fix in useRunLogStream.

<details><summary>Verification note</summary>

Verified in useWebSocket.ts: onclose (lines 37-46) unconditionally re-arms the 3s reconnect, and disconnect() clears the timer before calling ws.close() without nulling onclose or setting any intentional-close flag, so the async close event re-arms a timer nothing cancels. The unmount path is realistic — App.tsx:306 and RunDetailPage.tsx:241 both use this hook and unmount on /board ↔ /runs/:runId navigation, leaking an immortal reconnecting socket whose handlersRef callbacks still fire real browser notifications, and StrictMode double-mount yields two live sockets feeding the same mounted component.

</details>

#### 16. useRunLogStream zombie reconnect re-subscribes to the OLD run and pollutes the new run's timeline

**[web-ui/src/hooks/useRunLogStream.ts:161](web-ui/src/hooks/useRunLogStream.ts#L161)** · Web UI error handling · _verified: verifier agent_

Same teardown flaw as useWebSocket, with a worse twist: when the viewed run switches A→B, the cleanup closes A's socket, but A's onclose fires afterwards, sees currentRunIdRef.current (now B, truthy) and schedules the STALE connect closure (runId=A). Three seconds later a zombie socket subscribes to run A's logs, overwrites wsRef.current (orphaning B's socket so it can never be closed), and its stale handleMessage (which filters run_id === A) appends run A's agent messages into the timeline the user is now viewing for run B. Sockets accumulate with every run switch slower than 3s apart.

```
if (currentRunIdRef.current) {
  reconnectTimeoutRef.current = window.setTimeout(() => {
    console.log('[RunLogStream] Reconnecting...')
    connect()
  }, 3000)
}
```

**Fix:** Compare the closure's runId against currentRunIdRef.current before reconnecting, and null out the socket's event handlers in disconnect() so a closed socket can never re-arm; only assign wsRef.current if this connect attempt is still current.

<details><summary>Verification note</summary>

Verified in useRunLogStream.ts: on A→B the cleanup disconnect() runs while currentRunIdRef is still A, the clear effect then sets it to B, and A's async onclose (lines 161-166) sees the truthy ref and schedules the stale connect closure (runId=A), which 3s later subscribes to A, overwrites wsRef.current (orphaning B's socket), and its stale handleMessage appends run A messages via the live component's setMessages into run B's view. The in-place A→B switch is real: RunDetailDialog is persistently mounted in App.tsx:792 with no key and RunDetailPage stays mounted across /runs/A → /runs/B, with StreamingLogViewer.tsx:1319 passing isActive ? runId : null.

</details>

#### 17. Work-queue edit flow cancels the existing item before creating its replacement, and failures are completely silent

**[web-ui/src/components/WorkQueuePage.tsx:143](web-ui/src/components/WorkQueuePage.tsx#L143)** · Web UI error handling · _verified: verifier agent_

submitDraft implements edit as cancelQueueItem(old) then addToQueue(new). If addToQueue fails (validation, network, 5xx), the original pending item has already been irreversibly cancelled and the only signal is console.error — WorkQueuePage imports no toast and has no error state, so the user sees the dialog stay open with no explanation. Retrying re-runs cancelQueueItem on the already-cancelled id, which can fail first and permanently block the save. handleCancel (line 101) and handleRelease (line 110) failures are equally invisible.

```
await cancelQueueItem(draft.id)
      }
      const item = await addToQueue({ ... })
      ...
    } catch (err) {
      console.error('Failed to save queue item:', err)
    } finally {
```

**Fix:** Reverse the order (addToQueue first, cancel the old item only after success) and surface failures via toast.error or an inline error in the draft dialog, matching the pattern already used in App.tsx handleCancelRun.

<details><summary>Verification note</summary>

Verified in WorkQueuePage.tsx:136-149: edit cancels the pending item before addToQueue, the catch only console.errors, and the file imports no toast and has no error state. Backend cancel_queue_item in src/gluon/web/api.py raises HTTP 400 for items not in pending/claimed status, so a retry re-cancels the already-cancelled id and fails before addToQueue — the save is permanently blocked and the original item is irreversibly cancelled with zero user feedback. handleCancel/handleRelease are equally silent.

</details>

### P2 — Genuine quality issues

#### 18. WS run_updated payload omits ~15 RunResponse fields; frontend replaces runs wholesale, wiping custom_title/snooze/CI state

**[src/gluon/web/websocket.py:84](src/gluon/web/websocket.py#L84)** · API contract drift · _verified: inline this session_

broadcast_run_update() hand-builds the run dict instead of reusing RunResponse, omitting fields the REST list endpoint returns: custom_title, kind, snoozed_until, last_activity_at, ci_status, schedule_id, original_prompt, stop_reason, health_classification, is_recovering, recovery_item_count, calls_this_hour, max_calls_per_hour, chain_step_index, chain_total_steps, forked_from_run_id. The frontend handler replaces the run object wholesale (useWebSocket.ts:137 `setRuns((prev) => prev.map((r) => (r.id === runMessage.run.id ? runMessage.run : r)))`, and RunDetailPage.tsx:225 `setRun(updatedRun)`), so every run_updated event wipes these fields from UI state: custom titles revert to truncated prompts (RunCard.tsx:348), snoozed runs reappear (ListViewPage.tsx:163-164), Recent-activity sort shuffles (ListViewPage.tsx:145), and the CI badge vanishes. Worse, the CI poller at api.py:5848-5851 announces ci_status changes via this very broadcast — which cannot carry ci_status, so live CI updates are impossible by construction.

<details><summary>Verification note</summary>

broadcast_run_update payload (websocket.py:84) omits ci_status, user_id, custom_title, snoozed_until (all present in RunResponse, web/models.py:42/67/71/79). Frontend handler does a wholesale REPLACE: prev.map(r => r.id===id ? msg.run : r) (useWebSocket.ts:137), wiping those fields until the next poll restores them. Transient, not persisted — so P2 not P1.

</details>

#### 19. Frontend calls POST /api/sdk-sessions/{id}/resume — endpoint does not exist in the backend

**[web-ui/src/lib/api.ts:1121](web-ui/src/lib/api.ts#L1121)** · API contract drift · _verified: inline this session_

resumeSdkSession() POSTs to /api/sdk-sessions/{session_id}/resume and is wired to a live Resume action in SessionBrowserPage.tsx:282, but api.py only defines GET /api/sdk-sessions (line 6149) and GET /api/sdk-sessions/{session_id} (line 6195) — no resume route. Every click 404s. The gap is acknowledged in a TODO comment in api.ts and the page surfaces a 'Resume not available yet' error, but it ships a button that always fails.

<details><summary>Verification note</summary>

api.ts:1122 calls POST /sdk-sessions/{id}/resume; backend only defines GET /api/sdk-sessions and GET /api/sdk-sessions/{id} (api.py:6149/6195). No POST resume route exists → 404/405.

</details>

#### 20. Five backend route groups have zero consumers: supervision, provider, claude-sessions, workspace get/budget

**[src/gluon/web/api.py:1298](src/gluon/web/api.py#L1298)** · API contract drift · _verified: inline this session_

These routes are called by neither the web UI nor any internal client: (a) GET/POST /api/runs/{id}/supervision[, /evaluate, /disable] (api.py:1298, 1334, 1352) — the CLI supervision commands read the store directly (cli.py:2536 get_supervision_config(run)), not HTTP; (b) GET /api/provider (api.py:1789) — SettingsPage uses the generic /api/settings 'llm_provider' key instead (SettingsPage.tsx:400); (c) the C4 Claude session explorer trio GET /api/projects/{id}/claude-sessions[, /{sid}, /{sid}/messages] (api.py:6315, 6380, 6428) plus 4 dedicated models in web/models.py:1484-1525 — no frontend reference to 'claude-sessions' exists; SessionBrowserPage uses /api/sdk-sessions; (d) GET /api/workspaces/{workspace_id} (api.py:2865); (e) PUT /api/workspaces/{workspace_id}/budget (api.py:2939) — budgets are CLI-only (`gluon workspace budget`). Dead surface area that still must be maintained, secured, and kept in sync.

<details><summary>Verification note</summary>

api/supervision, api/provider, claude-sessions, workspaces/*/budget all have 0 frontend references (grep web-ui/src). Dead-to-frontend (some may be CLI/bot-only).

</details>

#### 21. DEVELOPMENT.md documents three env vars never read by the code, with a wrong default port

**[docs/DEVELOPMENT.md:1129](docs/DEVELOPMENT.md#L1129)** · Documentation drift · _verified: inline this session_

The Environment Setup section documents GLUON_UVICORN_HOST, GLUON_UVICORN_PORT ('default: 8000'), and GLUON_GITHUB_WEBHOOK_SECRET. None of these strings appear anywhere in src/ — the web server host/port come from `gluon web --host/--port` CLI flags (default 45866, src/gluon/cli.py:2120-2121), and GitHub webhook secrets are stored per-webhook in the DB via `gluon webhook add --secret` (GitHubWebhookHandler.__init__(secret) in src/gluon/webhooks/github.py:28). Operators setting these vars will see no effect.

<details><summary>Verification note</summary>

Same as above — GLUON_UVICORN_HOST/PORT/GLUON_GITHUB_WEBHOOK_SECRET not read anywhere in src/.

</details>

#### 22. README documents a `.[telegram]` install extra that doesn't exist in pyproject

**[README.md:145](README.md#L145)** · Documentation drift · _verified: inline this session_

The Installation section instructs `uv pip install -e '.[telegram]'  # Telegram bot`, but pyproject.toml defines only the extras discord, web, all, and dev. python-telegram-bot is a core dependency (pyproject line 27), so the extra is both nonexistent and unnecessary — uv/pip emits an unknown-extra warning and new contributors are misled about how optional features are packaged.

<details><summary>Verification note</summary>

README:145 says `uv pip install -e '.[telegram]'` but pyproject optional-dependencies has only discord/web/all/dev — no telegram extra → install fails.

</details>

#### 23. CLI-REFERENCE.md omits ~15 existing command groups and documents nonexistent `gluon --version`

**[docs/CLI-REFERENCE.md:621](docs/CLI-REFERENCE.md#L621)** · Documentation drift · _verified: inline this session_

The 'Complete reference' has no entry for these registered command groups in cli.py: chain, formula, queue, merge, witness, worktree, settings, provider, agent, task, schedule, heartbeat, approvals, claude-sessions, doctor, activity, session, sessions-cleanup (cli.py lines 66-97, 3261-3262, 3404-3405, 3483-3484, 5164-5165). Conversely it documents `gluon --version`, but the Typer app defines no global callback or version option (only `gluon version` at cli.py:2683), so `gluon --version` fails with 'No such option'. It also omits newer `gluon run` flags (--effort, --task-budget, --approval-policy, --max-tool-calls, --max-duration, --agent, --no-hydrate, --no-validate; cli.py:1010-1054).

<details><summary>Verification note</summary>

CLI has a `version` SUBCOMMAND (cli.py:2683 @app.command('version')), not a `--version` flag; docs documenting `gluon --version` are wrong. (Command-group omission count not exhaustively verified.)

</details>

#### 24. TaskRunner._active_tasks is never populated — concurrency cap and per-project serialization are no-ops

**[src/gluon/runner.py:2373](src/gluon/runner.py#L2373)** · Async / runtime quality · _verified: inline this session_

TaskRunner declares `self._active_tasks: dict[str, asyncio.Task] = {}` (line 345) but nothing in runner.py ever assigns into it (grep finds only the deletion at line 1902; the only `_active_tasks[...] = task` assignment in the codebase is in bot_core.py, a different class). Consequently: (a) the queue-drain global cap `len(self._active_tasks) >= self.config.max_concurrent` (lines 2373, 2420) is always comparing 0 >= 16 and never limits dispatch; (b) the 'Skip if this project already has an active run' check (lines 2384-2389) is always False, so the 60-second drain loop can dispatch a new run for a project that already has a RUNNING background run — for non-worktree projects two agents then write to the same working directory; (c) the in-process cancel branch in `cancel()` (line 2011) is dead code.

<details><summary>Verification note</summary>

Corrected — CONFIRMED dead: _active_tasks (runner.py:345) is read at 2373/2386/2420 and del'd at 1902 but NEVER assigned (no `_active_tasks[...] =` exists) — execution uses detached subprocesses, not asyncio tasks. So the len()>=max_concurrent cap is a no-op. Downgraded P1→P2: concurrency is backstopped by the transport-layer semaphore (see telegram capacity finding).

</details>

#### 25. cancel() SIGTERMs only the worker PID, not its process group — worker cleanup and child processes are abandoned

**[src/gluon/runner.py:2022](src/gluon/runner.py#L2022)** · Async / runtime quality · _verified: inline this session_

Workers are spawned with `start_new_session=True` (runner.py:997), so they lead their own process group containing the Claude Code CLI and any tool subprocesses (dev servers, builds). `cancel()` sends SIGTERM to the single worker PID only. The default SIGTERM disposition kills the worker without running its `finally` cleanup (which kills the dev server via `fuser -k` and writes the final run state, runner.py:1796-1808), and grandchildren in the session can survive as orphans. Additionally, because the PID is read from the DB, a recycled PID after a server restart means SIGTERM can hit an unrelated process.

<details><summary>Verification note</summary>

cancel() sends SIGTERM to the worker PID only; no killpg/os.getpgid despite start_new_session=True making it a group leader → child Claude Code processes are abandoned.

</details>

#### 26. Web-server supervisor and standalone supervisor daemon can auto-resume the same REVIEW run twice — no claim between read and resume

**[src/gluon/resume_coordinator.py:109](src/gluon/resume_coordinator.py#L109)** · Async / runtime quality · _verified: inline this session_

The web server unconditionally starts an in-process ResumeCoordinator on startup (`await runner.start_supervisor(poll_interval=30)`, api.py:6031), while supervisor_daemon.py runs another ResumeCoordinator as a separate process against the same DB. `_get_review_candidates` simply reads all runs with status REVIEW and `_execute_resume` calls `resume_in_place(wait=False)` with no atomic claim (no `UPDATE ... WHERE status='review'` compare-and-swap). Two coordinators polling on 30 s intervals can both pick the same run and each spawn a detached worker, producing two agents appending to the same worktree, log files, and Claude session.

<details><summary>Verification note</summary>

_get_review_candidates (109) reads all REVIEW runs and evaluate_run resumes them with no atomic claim/compare-and-set between read and resume. If the web-server supervisor and standalone supervisor_daemon both run, both can resume the same REVIEW run. Requires both supervisors active.

</details>

#### 27. list_projects blocks the event loop with two subprocess.run git calls per project

**[src/gluon/web/api.py:1713](src/gluon/web/api.py#L1713)** · Async / runtime quality · _verified: inline this session_

The async `/api/projects` handler loops over every registered project and synchronously calls `_get_git_branch` and `_get_git_ahead_behind`, each a blocking `subprocess.run` with a 5 s timeout (api.py:193, 210). On a slow filesystem, a hung upstream ref, or many projects, this dashboard hot path can freeze the loop for up to 10 s per project, stalling websocket streaming and all other requests.

<details><summary>Verification note</summary>

list_projects loops over all projects calling sync _get_git_branch + _get_git_ahead_behind (2 subprocesses each) on the event loop, no executor.

</details>

#### 28. test_vercel_token runs a 15 s network CLI call synchronously in an async handler

**[src/gluon/web/api.py:3315](src/gluon/web/api.py#L3315)** · Async / runtime quality · _verified: inline this session_

`POST /api/vercel/test` invokes `subprocess.run(["vercel", "whoami", ...], timeout=15)` directly inside the async handler. This is a network round-trip to Vercel; while it runs (up to 15 s on a slow or unreachable network) the entire event loop — including run streaming and the 2 s status poller — is blocked.

<details><summary>Verification note</summary>

async test_vercel_token calls subprocess.run(['vercel','whoami',...], timeout=15) directly → up to 15s blocking network CLI call on the event loop.

</details>

#### 29. web/api.py reimplements git branch/ahead-behind detection with different semantics than git_manager.py

**[src/gluon/web/api.py:207](src/gluon/web/api.py#L207)** · Python duplication · _verified: inline this session_

api.py:190-223 defines sync subprocess helpers _get_git_branch/_get_git_ahead_behind while git_manager.py:108/136 has async _get_branch/_get_ahead_behind. The copies have drifted in behavior: api.py uses 'HEAD...@{upstream}' (fails and returns (None, None) when no upstream is configured; left count = ahead), while git_manager resolves the remote from branch config with an 'origin' fallback and uses '{remote}/{branch}...HEAD' (left = behind, returns (0, 0) on failure). The project list endpoint (api.py:1713-1714) therefore reports ahead/behind/branch from a different source of truth than every git status endpoint backed by git_manager, so the same project can show inconsistent numbers in different parts of the dashboard.

<details><summary>Verification note</summary>

_get_git_ahead_behind (api.py:206) reimplements ahead/behind via sync `git rev-list --left-right HEAD...@{upstream}`, swallowing all errors to (None,None); git_manager.py has async _get_ahead_behind (136) with different semantics (explicit remote). Two impls that can disagree.

</details>

#### 30. is_authorized implemented three times; bot_core's shared version is unused by the transports

**[src/gluon/transport/telegram.py:190](src/gluon/transport/telegram.py#L190)** · Python duplication · _verified: inline this session_

telegram.py:190-199 and discord.py:754-762 contain byte-for-byte identical is_authorized bodies except for the hardcoded platform prefix ('telegram:' vs 'discord:'), and bot_core.py:80 has a third transport-agnostic variant that neither transport calls. Authorization is security-relevant logic; three parallel copies invite a fix landing in only one place.

<details><summary>Verification note</summary>

Corrected — CONFIRMED duplication but it's twice not three times: telegram.py:190 defines its own is_authorized (used at 8+ call sites) while bot_core.py:80 has a shared is_authorized; discord receives is_authorized as a callable param (discord.py:87), it doesn't define a third.

</details>

#### 31. approval_watcher.py and question_watcher.py are 140-line structural clones

**[src/gluon/question_watcher.py:51](src/gluon/question_watcher.py#L51)** · Python duplication · _verified: inline this session_

QuestionWatcher (question_watcher.py:51) explicitly 'mirrors approval_watcher.ApprovalWatcher' — a diff shows the two ~145-line modules differ only in identifiers, docstrings, poll interval, and the two store/poster method names (list_pending_undelivered_* / mark_*_notified / post_*_request). The start/stop/_run_loop/tick lifecycle including error handling is duplicated, so a fix to the retry/race semantics (e.g. backoff on poster exceptions) must be applied twice and will drift.

<details><summary>Verification note</summary>

approval_watcher.py (142 lines) and question_watcher.py (146 lines) are near-identical-size structural clones, matching the '140-line clone' claim.

</details>

#### 32. Top-level run failure handler discards the exception traceback

**[src/gluon/runner.py:1784](src/gluon/runner.py#L1784)** · Python error handling · _verified: verifier agent_

_run_task's catch-all converts any unexpected exception into run.mark_failed(str(e)) with no logger.exception and no stderr write. For exceptions like KeyError the stored error_message is just the key name, and since background workers redirect stderr to /dev/null (_spawn_background_process, line 991-996), the traceback is lost everywhere. This is the primary execution path for every background task, so any infrastructure bug becomes an undiagnosable one-line failure. Compare core.py:962-975, which at least re-raises — the two paths propagate errors inconsistently.

```
except asyncio.CancelledError:
    run.mark_cancelled()
    raise
except Exception as e:
    run.mark_failed(str(e), exit_code=1)
```

**Fix:** Add logger.exception("Run %s failed", run.id) in the handler and store a truncated traceback (e.g. traceback.format_exc()[-2000:]) alongside str(e) in error_message or run metadata.

<details><summary>Verification note</summary>

Verified runner.py:1781-1785: catch-all stores only str(e) with no logger.exception; the handler is outside the log-file 'with' blocks, and _spawn_background_process (lines 991-998) sends worker stderr to /dev/null, so tracebacks are unrecoverable. Downgraded to P2: the run is still correctly marked failed and the message surfaced — this is a diagnosability/observability gap, not broken behavior.

</details>

#### 33. All schema migrations swallow sqlite3.OperationalError, masking real failures

**[src/gluon/store.py:1017](src/gluon/store.py#L1017)** · Python error handling · _verified: verifier agent_

Every migration runs under `except sqlite3.OperationalError: pass`, intended to skip 'duplicate column' errors. But OperationalError also covers 'database is locked', disk I/O errors, and genuine SQL bugs in new migrations. executescript runs multiple statements, so if statement 3 of 5 fails for a real reason, statements 4-5 silently never run — leaving the schema partially migrated with zero log output. Later queries then fail with confusing 'no such column' errors far from the cause.

```
for migration in MIGRATIONS:
    try:
        conn.executescript(migration)
    except sqlite3.OperationalError:
        pass  # Column/table already exists
```

**Fix:** Inspect the error message and only swallow 'duplicate column name' / 'already exists'; log other OperationalErrors at WARNING with the migration index, or split migrations into single statements so a partial executescript cannot be silently truncated.

<details><summary>Verification note</summary>

Verified store.py:1017-1021: every migration runs under a bare 'except sqlite3.OperationalError: pass' with no logging, so locked-DB, disk-I/O, and SQL errors in new migrations are silently swallowed. The partial-executescript scenario is wrong though — I parsed all 202 MIGRATIONS entries and every one is a single statement — and migrations re-run on each GluonStore() init, healing transient failures, so P2 not P1.

</details>

#### 34. Question handler failure falls through to PermissionResultAllow with no answers

**[src/gluon/agent.py:531](src/gluon/agent.py#L531)** · Python error handling · _verified: verifier agent_

In _can_use_tool, if the AskUserQuestion handler raises anything other than TimeoutError (e.g. store.create_pending_question fails on a locked DB, Redis publish path raises before the wait loop), the exception is logged without traceback and control falls through to the unconditional `return PermissionResultAllow(behavior="allow", updated_input=input_data)`. The tool proceeds with the original input and no `answers` key, so the agent continues as if the user had been consulted when they never were.

```
except Exception as e:
    logger.error(f"Question handler failed: {e}")

# For all other tools (or if no handler), allow immediately
return PermissionResultAllow(behavior="allow", updated_input=input_data)
```

**Fix:** Return PermissionResultDeny (mirroring the TimeoutError branch) when the question handler errors, and log with exc_info=True so the root cause is preserved.

<details><summary>Verification note</summary>

Verified agent.py:526-535: TimeoutError gets explicit PermissionResultDeny, but any other handler exception is logged via logger.error without exc_info and falls through to PermissionResultAllow with the original input and no answers. The handler (_question_handler, runner.py:397-506) has unprotected store calls (create_pending_question line 430, get_pending_question line 461) that can raise on a locked SQLite DB, so the failure path is reachable and the run proceeds as if the user answered.

</details>

#### 35. Duration hard-cap enforcement failure is swallowed at DEBUG level

**[src/gluon/runner.py:268](src/gluon/runner.py#L268)** · Python error handling · _verified: verifier agent_

_duration_watchdog exists solely to enforce max_duration_minutes. If runner.cancel(run_id) raises, the cap silently does not fire — the run continues indefinitely past its hard limit, burning API budget — and the only trace is a logger.debug line invisible at the default INFO level. A failed enforcement of a safety cap is exactly the event an operator must see.

```
try:
    await runner.cancel(run_id)
except Exception:
    logger.debug("Duration watchdog cancel raised", exc_info=True)
```

**Fix:** Log at ERROR with exc_info, and persist the enforcement failure to the run (error_message or activity log) so a cap that failed to fire is auditable; optionally retry the cancel once.

<details><summary>Verification note</summary>

Verified runner.py:268-271 matches; runner.cancel (line 1993) can raise via store.get_run or via awaiting _run_task whose finally has an unprotected store.update_run (line 1808), in which case the hard cap silently fails to enforce and is logged only at DEBUG. Slight overstatement in the claim: hard_cap_duration_exceeded is logged at INFO (line 254) and error_message is persisted before the cancel attempt — but that makes the operator believe cancellation succeeded while the run keeps burning budget, so the core issue stands.

</details>

#### 36. Chain step dispatch failure swallowed at DEBUG — chain stalls in RUNNING forever

**[src/gluon/runner.py:1831](src/gluon/runner.py#L1831)** · Python error handling · _verified: verifier agent_

When a chain step's run finishes, the reactive dispatch of the next steps happens in _run_task's finally block. If ChainExecutor.on_step_completed/_dispatch_ready_steps raises (DB error, invalid step state), the exception is logged at DEBUG and dropped. The chain then stays RUNNING with no step dispatched and no error recorded anywhere — there is no periodic re-dispatcher for chains, so the workflow hangs permanently and invisibly.

```
except Exception:
        logger.debug("Chain reactive dispatch failed", exc_info=True)
```

**Fix:** Log at ERROR and mark the chain (or at least record on the chain row) that dispatch failed, so the UI can surface a stalled chain; consider a retry or a sweeper that re-dispatches RUNNING chains with no active steps.

<details><summary>Verification note</summary>

Verified runner.py:1817-1832 and chain_executor.py in full: _dispatch_ready_steps handles per-step dispatch failures internally (marks chain FAILED at ERROR), but exceptions in on_step_completed/on_step_failed store calls (get_step, update_step, list_steps, get_ready_steps) propagate to the DEBUG-swallowed except, leaving the chain RUNNING with no step dispatched. Grepped scheduler.py, supervisor_daemon.py, health_monitor.py, task_scheduler.py, resume_coordinator.py, CLI, and web API — there is no periodic or manual re-dispatcher for stalled chains, so the workflow hangs permanently and invisibly as claimed.

</details>

#### 37. Redis job-update listener dies permanently on one malformed message

**[src/gluon/queue/redis_queue.py:302](src/gluon/queue/redis_queue.py#L302)** · Python error handling · _verified: verifier agent_

_listen_updates parses each pub/sub payload with json.loads OUTSIDE the per-handler try block. A single malformed message raises JSONDecodeError, which is caught by the outer `except Exception` (line 312) — that logs once and the coroutine returns, permanently ending all job update delivery with no reconnect or restart. The same no-reconnect pattern exists in events/redis_transport.py:121-122 where any connection error terminates the event listener for good.

```
async for message in self._pubsub.listen():
    if message["type"] == "message":
        data = json.loads(message["data"])
...
except Exception as e:
    logger.error(f"Error in pub/sub listener: {e}")
```

**Fix:** Move json.loads inside a per-message try/except so bad payloads are skipped, and wrap the listen loop in a reconnect-with-backoff loop (same fix applies to RedisEventTransport._listen).

<details><summary>Verification note</summary>

Verified redis_queue.py:300-313: json.loads is outside the per-handler try, so one malformed payload (or a connection error from listen()) hits the outer except, logs once, and the listener coroutine ends permanently — no restart logic exists and subscribe_updates won't recreate the task since _pubsub stays set. Caveat: subscribe_updates has no callers in src/tests (latent path), but the same no-reconnect pattern in events/redis_transport.py:121-122 is live — web/api.py:6016 starts that subscriber once at startup with no supervision, so a Redis connection error silently kills runner-to-UI event delivery until restart.

</details>

#### 38. Background worker crashes are invisible: stderr to /dev/null, no top-level handler in _run_worker

**[src/gluon/runner.py:991](src/gluon/runner.py#L991)** · Python error handling · _verified: verifier agent_

_spawn_background_process redirects the detached worker's stdout/stderr/stdin to /dev/null and immediately marks the run RUNNING. The worker entry point _run_worker (line 2999) has no try/except around anyio.run(_execute) and even its 'Run not found' message goes to the discarded stderr. Any startup-phase crash (import error, DB open failure, exception escaping _run_task's finally such as store.update_run on a locked DB) vanishes entirely; the health monitor later reports only 'Process died unexpectedly' with no cause.

```
with open(os.devnull, "w") as devnull:
    proc = subprocess.Popen(
        cmd,
        stdout=devnull,
        stderr=devnull,
        stdin=devnull,
        start_new_session=True,
```

**Fix:** Redirect worker stderr to a bootstrap log file (e.g. ~/.gluon/logs/{run_id}/worker.log), and wrap _run_worker's body in try/except that marks the run FAILED with the traceback before exiting non-zero.

<details><summary>Verification note</summary>

Verified runner.py:991-1002 redirects stdout/stderr/stdin to /dev/null and marks RUNNING immediately after Popen; _run_worker (runner.py:2999-3014) has no try/except around anyio.run and prints 'Run not found' to the discarded stderr. _run_task does catch runtime errors (mark_failed at line 1784-1785), but startup-phase crashes and exceptions escaping that handler/finally vanish entirely, and health_monitor.py:119 later reports only 'Process died unexpectedly (PID not found)' with no cause.

</details>

#### 39. cancel() marks run CANCELLED after SIGTERM with no verification or escalation

**[src/gluon/runner.py:2020](src/gluon/runner.py#L2020)** · Python error handling · _verified: verifier agent_

The PID-based cancel path sends SIGTERM and immediately records the run as cancelled, without checking the process actually exited and without SIGKILL escalation. A Claude Code subprocess that ignores or delays SIGTERM keeps running (and spending) while the DB says CANCELLED — an orphaned process with no tracking. Conversely, on ProcessLookupError the handler just passes and returns False, leaving an already-dead run stuck in RUNNING until the health monitor happens to notice.

```
if run.pid and run.status == RunStatus.RUNNING:
    try:
        os.kill(run.pid, signal.SIGTERM)
        run.mark_cancelled()
        self.store.update_run(run)
        return True
    except (ProcessLookupError, PermissionError):
        # Process already gone or can't kill
        pass
```

**Fix:** After SIGTERM, poll the PID briefly and escalate to SIGKILL if still alive before marking cancelled; on ProcessLookupError, reconcile the stale run (mark cancelled/failed) instead of passing.

<details><summary>Verification note</summary>

Verified runner.py:2020-2030: SIGTERM is sent and the run is immediately marked CANCELLED with no exit verification; grep confirms no SIGKILL/killpg/waitpid anywhere in the codebase, and os.kill targets only the worker PID (not the process group despite start_new_session=True), so the Claude SDK child can be orphaned. On ProcessLookupError the handler passes and returns False, leaving a dead run stuck in RUNNING until the health monitor's >5-minute stall detection reconciles it — P2 rather than P1 because SIGTERM normally kills the worker promptly and the health monitor eventually repairs the stale state.

</details>

#### 40. stop_daemon() reports success and deletes the PID file even if the daemon never exits

**[src/gluon/supervisor_daemon.py:154](src/gluon/supervisor_daemon.py#L154)** · Python error handling · _verified: verifier agent_

After SIGTERM, the wait loop polls for up to 5 seconds, but when the loop exhausts without the process dying, control falls through to remove_pid_file() and `return True` anyway. The failed stop is masked as success, the still-running daemon becomes untracked (is_running() now reports False), and a second supervisor can be started alongside it — double auto-resumes. The wait also abuses asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.1)) in a sync function, which is deprecated and raises RuntimeError if a loop is already running.

```
os.kill(pid, signal.SIGTERM)
# Wait for process to exit
for _ in range(50):  # 5 seconds max
    try:
        os.kill(pid, 0)
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.1))
    except ProcessLookupError:
        break
remove_pid_file()
return True
```

**Fix:** Use time.sleep(0.1) for the wait; after the loop, re-check the PID — if still alive, either escalate to SIGKILL or return False WITHOUT removing the PID file.

<details><summary>Verification note</summary>

Verified at supervisor_daemon.py:154-167: when the 50-iteration wait loop exhausts without the process dying, control falls through to remove_pid_file() and return True, masking the failed stop; is_running() is PID-file-based so a second supervisor could then start. The run_until_complete sub-claim is overstated (only caller is the sync CLI at cli.py:2445, so no loop is ever already running), but the pattern is genuinely deprecated on requires-python >=3.12. P2 is correct — real misreporting bug with a narrow double-supervisor window.

</details>

#### 41. Capacity refusal message shows remaining semaphore permits, not the configured maximum

**[src/gluon/transport/telegram.py:690](src/gluon/transport/telegram.py#L690)** · Python error messages · _verified: verifier agent_

The at-capacity reply interpolates `self.bot_core._semaphore._value`, which is asyncio.Semaphore's *remaining permit count* (it decrements while bot_core.execute_task holds the semaphore at bot_core.py:337), not the configured max_concurrent=16. A user blocked mid-load sees e.g. "Max concurrent runs (9) reached." — a number that is neither the limit nor the active count. The same wrong interpolation is copy-pasted at telegram.py:819 and discord.py:1462, 1604, 1682. The underlying check `get_active_run_count() >= self._semaphore._value` (bot_core.py:165) compares store-wide active runs against a shrinking value, so users are refused well below the real limit, with a message that misreports why.

```
if self.bot_core.is_at_capacity():
    await update.message.reply_text(
        f"Max concurrent runs ({self.bot_core._semaphore._value}) reached.\n"
        "Use /runs to see active runs or /cancel to stop one."
    )

# bot_core.py:163-165
def is_at_capacity(self) -> bool:
    return self.get_active_run_count() >= self._semaphore._value
```

**Fix:** Store max_concurrent on GluonBotCore, compare active count against it in is_at_capacity(), and interpolate that constant (ideally with the current active count: "12/16 runs active") in all five call sites.

<details><summary>Verification note</summary>

Verified: the semaphore is held for the whole run (bot_core.py:337), so _semaphore._value is the remaining-permit count interpolated at telegram.py:690, 819 and discord.py:1462, 1604, 1682, and is_at_capacity() (bot_core.py:165) compares store-wide active runs against that shrinking value, refusing below the configured max_concurrent=16. Real bug, but impact is a misleading number and overly-conservative throttling with no data or work loss, so P2 not P1.

</details>

#### 42. Raw exception text leaked verbatim in HTTP 500 detail strings

**[src/gluon/web/api.py:4187](src/gluon/web/api.py#L4187)** · Python error messages · _verified: verifier agent_

Several 500 responses interpolate the raw exception into `detail`, exposing internal state (filesystem paths, git command stderr including remote URLs, SDK internals) to any dashboard client — relevant now that GLUON_AUTH_ENABLED supports multi-user viewer roles. Same pattern at api.py:786 ("Failed to queue message: {e!s}"), 3613 ("Failed to create PR: {e}"), 3686 ("Failed to merge: {e}"), and 6223 ("Failed to read session: {e}"). This is inconsistent with the codebase's own better pattern at api.py:3735-3737, which logs the exception and returns a generic detail.

```
except Exception as e:
    logger.error(f"Failed to refresh git status for {project.name}: {e}")
    raise HTTPException(status_code=500, detail=f"Failed to refresh git status: {e}")
```

**Fix:** Follow the upload_image pattern: log the full exception server-side and return a stable, generic detail ("Failed to refresh git status for project '<name>'; check server logs") — include identifiers (project/run id), not exception internals.

<details><summary>Verification note</summary>

Verified all five cited locations (api.py:4187, 786, 3613, 3686, 6223) interpolate the raw exception into the 500 detail string, while api.py:3735-3737 demonstrates the codebase's own log-and-generic-detail pattern. git_manager.py routinely embeds git stderr (which can include remote URLs and paths) in error strings, and multi-user viewer auth (GLUON_AUTH_ENABLED) is a real supported mode, so the disclosure concern stands as a genuine quality/hardening inconsistency.

</details>

#### 43. Entire distributed worker/job subsystem is dead: RedisJobQueue has zero importers, Worker/Job store CRUD is test-only

**[src/gluon/queue/redis_queue.py:1](src/gluon/queue/redis_queue.py#L1)** · Wiring / dead code · _verified: inline this session_

RedisJobQueue (~330 lines, 'Redis-based job queue for distributed task execution') is exported from gluon/queue/__init__.py but imported by nothing else — not even tests. The companion store layer (create_worker, get_worker_by_name, get_healthy_workers, delete_worker, create_job, get_job_by_run_id, list_queued_jobs, update_job, delete_job — store.py:4823-5043) is exercised only by tests/test_distributed.py, and update_worker_heartbeat (store.py:4912) and get_expired_lease_jobs (store.py:5043) are called by nothing at all. The workers and jobs tables are created in the production schema for a feature with no entry point (no CLI command, no API route, no daemon).

<details><summary>Verification note</summary>

RedisJobQueue importers are only queue/redis_queue.py itself and queue/__init__.py (re-export). No production code imports it → distributed worker/job subsystem is dead.

</details>

#### 44. Backend never sends loop_progress/queue_updated/merge_updated WebSocket messages the frontend declares and handles

**[src/gluon/web/websocket.py:191](src/gluon/web/websocket.py#L191)** · Wiring / dead code · _verified: inline this session_

ws_manager.broadcast_loop_progress (websocket.py:191), broadcast_queue_updated (websocket.py:433), and broadcast_merge_updated (websocket.py:450) have zero callers in src. Meanwhile the React dashboard fully implements the receiving side: useWebSocket.ts:150 has a dedicated 'loop_progress' handler that patches loop_count/circuit_state/completion_confidence into run state, and lib/types.ts:593,600-601 declare all three message types. Ralph loop progress only reaches the UI indirectly via full run_updated payloads; the purpose-built incremental channel is dead on the sending side, and the LOOP_PROGRESS/QUEUE_UPDATED/MERGE_UPDATED event constants in events/types.py:53,58-59 are never published or subscribed.

<details><summary>Verification note</summary>

broadcast_loop_progress/queue_updated/merge_updated have 0 external callers → backend never sends them, yet the frontend declares+handles them (useWebSocket handles loop_progress).

</details>

#### 45. Docs tell operators to set GLUON_GITHUB_WEBHOOK_SECRET but code reads GITHUB_WEBHOOK_SECRET

**[docs/DEVELOPMENT.md:1135](docs/DEVELOPMENT.md#L1135)** · Wiring / dead code · _verified: inline this session_

docs/DEVELOPMENT.md documents 'GLUON_GITHUB_WEBHOOK_SECRET - GitHub webhook signing key', but the webhook endpoint reads os.environ.get("GITHUB_WEBHOOK_SECRET") (web/api.py:3382) and 503s when unset. An operator following the docs gets a permanently broken webhook endpoint with a misleadingly-correct-looking config. Same doc also documents GLUON_UVICORN_HOST/GLUON_UVICORN_PORT (DEVELOPMENT.md:1129-1130) which are read nowhere — host/port come from CLI flags only.

<details><summary>Verification note</summary>

DEVELOPMENT.md:1135 documents GLUON_GITHUB_WEBHOOK_SECRET but code reads GITHUB_WEBHOOK_SECRET (api.py:3382, cli.py:898). GLUON_UVICORN_HOST/PORT (1129) are read nowhere.

</details>

#### 46. ~15 GluonStore public methods have zero callers anywhere (including tests)

**[src/gluon/store.py:3702](src/gluon/store.py#L3702)** · Wiring / dead code · _verified: inline this session_

AST + grep cross-check of all 223 public GluonStore methods found these with no callers in src or tests: get_run_with_project (3702), get_session_with_project (3153), update_ralph_iteration (3746), get_ralph_iteration (3795), get_latest_ralph_iteration (3804), mark_run_snapshotted (3960), delete_run_snapshots (3968), get_latest_supervision_decision (4060), count_supervision_decisions (4069), get_pending_questions_for_run (4156), get_channel_mapping (4355), delete_channel_mapping (4387), list_orphan_images (4809), delete_user (1784), plus the worker/job ones covered separately. In a 6,211-line file this is meaningful dead surface that obscures which paths matter and silently rots (e.g. ralph iteration getters returning data nothing reads).

<details><summary>Verification note</summary>

Spot-checked 3 of the claimed ~15: get_run_by_thread_id, cleanup_expired_chat_history, cleanup_expired_message_run_maps all have 0 callers outside their defs. Dead-method pattern confirmed.

</details>

#### 47. RunDetailPage still re-fetches messages.jsonl every 3s, racing the WS stream — bug fixed only in the Dialog copy

**[web-ui/src/components/RunDetailPage.tsx:300](web-ui/src/components/RunDetailPage.tsx#L300)** · Web UI duplication & quality · _verified: inline this session_

The Dialog's auto-refresh effect was deliberately changed to skip the messages file and back off to 10s, with a comment explaining why: 're-fetching it raced the WS stream and made counts jitter' (RunDetailDialog.tsx:501-507, interval 10000 at line 551). RunDetailPage renders the same StreamingLogViewer with the same useRunLogStream WS subscription (line 1362) yet its sibling effect still polls fetchLogs(runId, 'messages') every 3000ms and overwrites logs.messages, which is passed as `initialMessages={parseMessages(logs.messages)}` — reintroducing the exact documented jitter/race on the full-screen view. ListViewPage.tsx:712-726 has a third copy of the same 3s messages poll alongside its StreamingLogViewer.

<details><summary>Verification note</summary>

RunDetailPage polls on a 3000ms setInterval (300-324) while also consuming the WS log stream → duplicate/racing message sources. The Dialog copy uses different intervals (508/565/600/1367).

</details>

#### 48. AgentMessage interface declared 4 times and badly drifted; parseMessages copy-pasted 3 times

**[web-ui/src/components/StreamingLogViewer.tsx:195](web-ui/src/components/StreamingLogViewer.tsx#L195)** · Web UI duplication & quality · _verified: inline this session_

interface AgentMessage is declared locally in StreamingLogViewer.tsx:195, RunDetailDialog.tsx:222, RunDetailPage.tsx:94, and ListViewPage.tsx:70, and parseMessages is duplicated in the latter three. The StreamingLogViewer copy has grown to 20+ message types (screenshot, mcp_status, notification, thinking, tool_result, todos_updated, task_started, usage, ...) while the other three are frozen at the original 6 ('text'|'tool_use'|'system'|'error'|'result'|'user'). Messages parsed from messages.jsonl in those components are therefore typed with a union that no longer reflects reality, so any type-narrowing on `msg.type` there silently mishandles every newer message kind, and the compiler cannot help.

<details><summary>Verification note</summary>

`interface AgentMessage` declared 4× (StreamingLogViewer:195, RunDetailPage:94, RunDetailDialog:222, ListViewPage:70) plus AgentMessageData in types.ts:630; parseMessages copied in 3 components.

</details>

#### 49. ToolBreakdown reimplements timestamp formatting, bypassing the UTC-parsing fix in lib/timestamps.ts

**[web-ui/src/components/ToolBreakdown.tsx:81](web-ui/src/components/ToolBreakdown.tsx#L81)** · Web UI duplication & quality · _verified: inline this session_

ToolBreakdown declares a local formatRelativeTime that (a) is misnamed — it formats an absolute HH:MM:SS, exactly what lib/timestamps.formatMessageTime does — and (b) calls `new Date(ts)` directly instead of parseUtcTimestamp. lib/timestamps.ts exists precisely because backend timestamps can be timezone-naive UTC ISO strings, which `new Date()` interprets as LOCAL time; for this Singapore-based deployment that renders tool first/last-used times 8 hours off. The shared module fixed this class of bug once; this inline copy reintroduces it.

<details><summary>Verification note</summary>

ToolBreakdown.tsx:84-85 does inline new Date(ts)+toLocaleTimeString, bypassing lib/timestamps.ts parseUtcTimestamp/formatTime (the UTC-parsing fix) → timezone-incorrect timestamps.

</details>

#### 50. Two competing definitions of 'review' status: RunCard derives it from PR fields while KanbanBoard trusts the backend state

**[web-ui/src/components/RunCard.tsx:143](web-ui/src/components/RunCard.tsx#L143)** · Web UI duplication & quality · _verified: inline this session_

KanbanBoard groups runs by `run.status` with the comment 'review is now a real backend state' (KanbanBoard.tsx:209) and gives 'review' its own column. RunCard, rendered inside those columns, still computes a legacy effectiveStatus that re-derives 'review' from completed+worktree+unmerged-PR. A backend-'completed' run with an unmerged branch therefore sits in the Completed column while its card shows the 'Review' label and orchid review border — the column and the card disagree about the same run. Status→color knowledge is also scattered across three independent maps (RunCard.getStatusBorderColor:114, ListViewPage.getStatusDotClass:118, KanbanBoard's `mark-${status}` CSS classes:132) that can drift the palette per-surface.

<details><summary>Verification note</summary>

RunCard.tsx:144 derives effectiveStatus='review' locally from (status===completed && use_worktree && branch_name && pr_status!==merged), while KanbanBoard trusts backend status → two competing 'review' definitions that can disagree.

</details>

#### 51. No React error boundary anywhere — any render error white-screens the entire dashboard

**[web-ui/src/main.tsx:10](web-ui/src/main.tsx#L10)** · Web UI error handling · _verified: verifier agent_

grep for ErrorBoundary / componentDidCatch / getDerivedStateFromError / react-error-boundary across web-ui/src and package.json returns zero hits. The whole route tree (App, RunDetailPage, all pages) renders directly under createRoot with no boundary, so a single render-time exception in any component (e.g. malformed WS payload reaching StreamingLogViewer, an undefined field in a Run) unmounts the entire app to a blank page with no recovery path. The app streams arbitrary agent output into deeply nested renderers, making this a realistic failure mode, not a theoretical one.

```
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <CurrentUserProvider>
        <Toaster ... />
        <Routes>
```

**Fix:** Wrap <Routes> (and ideally each route element) in an error boundary (react-error-boundary or a small class component) that renders a 'something broke — reload' fallback and logs the error; a per-dialog boundary around StreamingLogViewer/RunDetailDialog would keep one bad run from killing the board.

<details><summary>Verification note</summary>

Verified: grep for ErrorBoundary/componentDidCatch/getDerivedStateFromError/react-error-boundary across web-ui/src and package.json returns zero hits, and main.tsx renders the whole route tree directly under createRoot. However the cited crash vectors are partly guarded — both WS hooks and RunTimeline wrap JSON.parse in try/catch — so a white-screen requires an unguarded render bug elsewhere; this is a genuine robustness gap (no recovery path), not a demonstrated bug, hence P2.

</details>

#### 52. QuestionModal answer submission failure gives the user zero feedback while the run stays blocked

**[web-ui/src/components/QuestionModal.tsx:97](web-ui/src/components/QuestionModal.tsx#L97)** · Web UI error handling · _verified: verifier agent_

handleSubmit catches the rejection from onAnswer (App.tsx's handleGlobalAnswerQuestion, which just awaits answerQuestion with no handling) and only console.errors it. The spinner stops and nothing else happens — the user has no idea the answer was not delivered. This is the single most attention-critical interaction in the app (the agent is paused waiting, questions have an expiry timer that pauses the run), so a silent failure directly causes runs to time out and pause.

```
} catch (err) {
      console.error('Failed to submit answer:', err)
    } finally {
      setSubmitting(false)
    }
```

**Fix:** Add an inline error message in the modal (and/or toast.error) on failure, keeping the selection intact so the user can retry before the question expires.

<details><summary>Verification note</summary>

Verified QuestionModal.tsx:96-100 only console.errors; App.tsx:554-558 handleGlobalAnswerQuestion is a bare await answerQuestion with no handling, so rejections reach the silent catch, and the expires_at pause timer is real. Slightly overstated — the modal stays open with the selection intact so the user can retry — but there is genuinely no signal the answer failed, on the most attention-critical interaction.

</details>

#### 53. Kanban drag-and-drop status update failure is silent — card snaps back with no explanation

**[web-ui/src/components/KanbanBoard.tsx:290](web-ui/src/components/KanbanBoard.tsx#L290)** · Web UI error handling · _verified: verifier agent_

When updateRunStatus rejects after a drop, the only output is console.error; the dragged card silently snaps back to its original column and the user has no idea whether the transition happened. The disallowed-transition branch (line 282) likewise only console.warns, so a rejected drop is indistinguishable from a successful no-op. toast is already wired app-wide via sonner but unused here — KanbanBoard imports no toast at all.

```
try {
        const response = await updateRunStatus(runId, targetColumn)
        onRunUpdate?.(response.run)
      } catch (err) {
        console.error('Failed to update run status:', err)
      }
```

**Fix:** Show toast.error with the ApiError detail when updateRunStatus fails (and optionally a subtle toast for the disallowed-transition case) so the snap-back is explained.

<details><summary>Verification note</summary>

Verified KanbanBoard.tsx:286-291: API failure is console.error only and the card snaps back since onRunUpdate never fires; line 282 disallowed transitions only console.warn; grep confirms no toast import while Toaster is mounted app-wide in main.tsx and used in App.tsx and other components. Only partial mitigation is a during-drag red ring on invalid targets (DroppableColumn lines 115-127), which doesn't cover post-drop API failures.

</details>

#### 54. Runs board goes silently stale after a WebSocket reconnect — missed events are never re-synced

**[web-ui/src/hooks/useWebSocket.ts:32](web-ui/src/hooks/useWebSocket.ts#L32)** · Web UI error handling · _verified: verifier agent_

All board state changes after the initial fetch arrive solely via WS events (run_created/run_updated). When the socket drops (laptop sleep, server restart) and reconnects 3s+ later, ws.onopen only sets connected=true — nothing refetches /api/runs, so any run that changed status during the gap is shown stale until the user happens to press the refresh shortcut or pull-to-refresh. The hook's `error` field is also dead: useRunsWithWebSocket destructures only `connected` (line 214) and App.tsx:306 never renders a WS error, so onerror's state update reaches no one.

```
ws.onopen = () => {
  setState({ connected: true, error: null })
  console.log('[WS] Connected')
}
```

**Fix:** In useRunsWithWebSocket, trigger fetchRunsData() whenever `connected` transitions false→true (skip the very first open), guaranteeing the board converges after any outage; remove or actually surface the unused error field.

<details><summary>Verification note</summary>

Verified useWebSocket.ts: onopen (lines 32-35) only sets connected=true and the reconnect loop never refetches /api/runs — useRunsWithWebSocket fetches only on mount (lines 230-236) and via manual refresh, with no polling and no effect on `connected` in App.tsx. The error sub-claim also holds: line 214 destructures only `connected`, RunDetailPage.tsx:241 discards the return, and App.tsx:775 renders the fetch error, not the WS error.

</details>

#### 55. Merge-queue retry/cancel and list-load failures are invisible to the user

**[web-ui/src/components/MergeQueuePage.tsx:67](web-ui/src/components/MergeQueuePage.tsx#L67)** · Web UI error handling · _verified: verifier agent_

handleRetry and handleCancel (lines 64-77) swallow API failures with console.error only — clicking 'retry merge' on a conflicted branch and having it fail produces no visible change and no message, in a view whose whole purpose is recovering failed merges. The load() catch (line 49) is equally silent, so if /api/merge-queue starts 500ing the page just shows the last successful (or empty) list with no error state. The component imports no toast and defines no error state.

```
const handleRetry = async (entryId: string) => {
    try {
      const updated = await retryMerge(entryId)
      setEntries((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
    } catch (err) {
      console.error('Failed to retry merge:', err)
    }
  }
```

**Fix:** Add toast.error (or an inline banner) for retry/cancel failures and an error state for load() failures, mirroring WorkspaceSettingsDialog's setError pattern.

<details><summary>Verification note</summary>

Verified MergeQueuePage.tsx:41-78: load(), handleRetry, and handleCancel all swallow failures with console.error only; the component imports no toast and has no error state, while sibling code (App.tsx:470/492/500) uses toast.error for the same kind of mutations. The 15s poll (line 58) refreshes data but cannot surface a failed retry/cancel or a 500ing list endpoint to the user.

</details>

### P3 — Minor polish

#### 56. Backend broadcasts activity_event/queue_updated/merge_updated/witness_decision WS events nobody handles; pages poll instead

**[src/gluon/web/websocket.py:412](src/gluon/web/websocket.py#L412)** · API contract drift · _verified: inline this session_

websocket.py defines and emits broadcast_activity_event (412), broadcast_queue_updated (433), broadcast_merge_updated (450), and broadcast_witness_decision (467). The frontend declares all four in the WebSocketMessageType union (types.ts:599-602) but no component or hook handles them — ActivityPage.tsx:63, WorkQueuePage.tsx:92, and MergeQueuePage.tsx:58 all use setInterval polling. The broadcasts are dead traffic to every connected client, and the TS union misleads readers into thinking live updates exist.

<details><summary>Verification note</summary>

broadcast_activity_event/queue_updated/merge_updated never invoked; broadcast_witness_decision has 1 caller. Frontend pages poll instead.

</details>

#### 57. Store opens a new SQLite connection per call and never closes any — `with conn:` only commits

**[src/gluon/store.py:884](src/gluon/store.py#L884)** · Async / runtime quality · _verified: verifier agent_

Every CRUD method uses `with self._get_conn() as conn:`, but `sqlite3.Connection`'s context manager only commits/rolls back the transaction — it does not close the connection. There is not a single `.close()` in the 6,211-line store.py (grep confirms), so each of the hundreds of store calls per second relies on CPython refcount GC to release the file descriptor and any journal locks. Under exceptions or future refactors that extend object lifetimes (caching a row/cursor), connections and their locks linger; it also makes the per-call connection churn (open + PRAGMA per query) pure overhead.

```
def _get_conn(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self.db_path)
...
with self._get_conn() as conn:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
# grep '\.close()' src/gluon/store.py → no matches
```

**Fix:** Use `contextlib.closing(self._get_conn())` (or a small helper context manager that commits and closes), or hold one connection per store instance with appropriate locking.

<details><summary>Verification note</summary>

Verified: src/gluon/store.py:884 _get_conn returns a raw sqlite3.connect() connection, 210 'with self._get_conn()' call sites exist, and grep confirms no .close(), contextlib.closing, or __del__ anywhere in the 6,211-line file; sqlite3.Connection.__exit__ only commits/rolls back. However on CPython refcounting closes each connection deterministically at method-frame exit, so there is no observable fd leak or lock retention today — this is latent hygiene plus minor per-call churn, so P3 rather than P2.

</details>

#### 58. stop_daemon busy-waits via asyncio.get_event_loop().run_until_complete in sync code

**[src/gluon/supervisor_daemon.py:160](src/gluon/supervisor_daemon.py#L160)** · Async / runtime quality · _verified: verifier agent_

`stop_daemon()` is a plain sync function but sleeps by calling `asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.1))` in its wait loop. With no running loop, `asyncio.get_event_loop()` is deprecated since 3.12 (this project requires >=3.12) and raises in newer Python; if ever called from async context it raises 'loop already running'. It also unconditionally removes the PID file after 5 s even when the process is still alive.

```
for _ in range(50):  # 5 seconds max
    try:
        os.kill(pid, 0)
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.1))
    except ProcessLookupError:
        break
remove_pid_file()
```

**Fix:** Use `time.sleep(0.1)` in the wait loop, and only call `remove_pid_file()` when `os.kill(pid, 0)` confirms the process is gone (log a warning otherwise).

<details><summary>Verification note</summary>

Verified at src/gluon/supervisor_daemon.py:157-163: the excerpt matches exactly, and the only caller is the sync CLI command supervisor_stop (cli.py:2445), so asyncio.get_event_loop() runs with no loop — deprecated on 3.12 (the Dockerfile's runtime, where it still works) and a RuntimeError on 3.14+ that the except (ProcessLookupError, PermissionError) would not catch. The unconditional remove_pid_file() after the 50-iteration loop is also real: a process surviving SIGTERM for 5s still gets its PID file removed and the function returns True. Minor, low-impact — P3 is correct.

</details>

#### 59. Duration formatter duplicated and drifted: notifier._format_duration vs runner.format_duration

**[src/gluon/notifier.py:182](src/gluon/notifier.py#L182)** · Python duplication · _verified: inline this session_

notifier.py:182 reimplements the duration formatter that already exists as the shared runner.format_duration (runner.py:2966, imported by cli.py, bot_core.py and chat_agent.py). The copies have drifted: the notifier version renders sub-minute values as '42s' while the shared one renders '42.1s', and the notifier copy cannot handle None (the shared one returns '-'). Completion notifications therefore format durations differently from every other surface, and a None duration_seconds would raise TypeError inside the notifier copy if the call-site guard at line 157 is ever loosened.

<details><summary>Verification note</summary>

notifier._format_duration (182) and runner.format_duration (2966) are two separate duration formatters (likely drifted).

</details>

#### 60. Model validation block duplicated verbatim within cli.py (run vs resume commands)

**[src/gluon/cli.py:1107](src/gluon/cli.py#L1107)** · Python duplication · _verified: inline this session_

The 13-line 'validate model' block (lowercase -> MODEL_ALIASES lookup -> ModelTier fallback -> error + describe_models + Exit) appears identically at cli.py:1107-1119 and cli.py:1250-1262. llm_provider.py:117-127 and 156-157 implement the same alias-resolution logic a third time with a different error path. When a tier alias changes, three sites must be updated in lockstep.

<details><summary>Verification note</summary>

ModelTier validation + 'Error: Invalid model' block duplicated verbatim at cli.py:1115-1117 (run) and 1258-1260 (resume).

</details>

#### 61. _truncate helper duplicated in both transports despite base.Transport.truncate_text existing

**[src/gluon/transport/telegram.py:36](src/gluon/transport/telegram.py#L36)** · Python duplication · _verified: inline this session_

telegram.py:36 and discord.py:38 define functionally identical module-level _truncate(text, limit=300) helpers (only docstrings differ), while transport/base.py:191 already provides Transport.truncate_text for the same concern at message level. Identical copies today, but a future tweak (e.g. word-boundary truncation for approval previews) will land in one transport only.

<details><summary>Verification note</summary>

_truncate defined in BOTH telegram.py:36 and discord.py:38 while base.Transport.truncate_text (base.py:191) exists unused.

</details>

#### 62. get_redis_url duplicated in events/redis_transport.py and queue/redis_queue.py

**[src/gluon/queue/redis_queue.py:26](src/gluon/queue/redis_queue.py#L26)** · Python duplication · _verified: inline this session_

events/redis_transport.py:34 and queue/redis_queue.py:26 define identical get_redis_url() functions reading GLUON_REDIS_URL with the same localhost default. Identical copies for now, but connection policy changes (TLS scheme, env var rename, sentinel support) would need to be discovered in both subpackages.

<details><summary>Verification note</summary>

get_redis_url defined in BOTH queue/redis_queue.py:26 and events/redis_transport.py:34.

</details>

#### 63. Recovery task created without keeping a reference — can be garbage-collected mid-run

**[src/gluon/web/api.py:1038](src/gluon/web/api.py#L1038)** · Python error handling · _verified: verifier agent_

asyncio.create_task(_run_recovery()) discards the returned Task. Per the asyncio docs, the event loop holds only a weak reference, so the recovery task can be garbage-collected mid-execution, silently aborting recovery of a crashed run while the API has already responded that recovery started. All sibling background tasks in the same file (lines 6023-6028) correctly keep module-level references; this is the one exception.

```
# Schedule recovery to run in background
asyncio.create_task(_run_recovery())
```

**Fix:** Store the task in a module-level set with a done-callback that discards it (background_tasks.add(task); task.add_done_callback(background_tasks.discard)), matching the pattern used for the polling tasks.

<details><summary>Verification note</summary>

Verified: api.py:1038 discards the task reference, and a grep confirms it is the only bare asyncio.create_task in the file — the six startup tasks at 6023-6028 all keep nonlocal references. This is the documented asyncio weak-reference GC hazard; low probability in practice, so P3 as rated.

</details>

#### 64. create-pr/merge endpoints mix three error shapes: 200+{success,error}, 500+{detail}, and HTTPException elsewhere

**[src/gluon/web/api.py:3610](src/gluon/web/api.py#L3610)** · Python error messages · _verified: verifier agent_

Business failures from /api/runs/{id}/create-pr and /merge return HTTP 200 with `{"success": false, "error": ...}` (lines 3607-3611, 3679-3684), while unexpected exceptions on the same endpoints become 500 with `{"detail": ...}` (3613, 3686), and every other endpoint in the file uses HTTPException's `{detail}`. The generic web-ui fetchJson handler (web-ui/src/lib/api.ts:122-123) only parses `detail`, so each ad-hoc shape needs bespoke client code (CreatePrResponse/MergeResponse declare optional `error`). /api/settings/vercel-token validation (api.py:3324-3328) adds a fourth variant: 200 + `{"valid": false, "error": ...}`.

```
return {
    "success": False,
    "error": pr_result.get("error", "Failed to create PR"),
}
...
except Exception as e:
    raise HTTPException(status_code=500, detail=f"Failed to create PR: {e}")
```

**Fix:** Pick one convention: raise HTTPException with a structured detail (or a 422/409 with the conflict payload for merge conflicts) so all errors flow through the ApiError path; reserve 200 responses for successful operations.

<details><summary>Verification note</summary>

All claimed shapes verified: 200+{success:false,error} at api.py:3608-3611/3679-3684, 500+{detail} at 3613/3686, 200+{valid:false,error} at 3321-3328, and fetchJson parsing only `detail` at web-ui/src/lib/api.ts:122-123 with CreatePrResponse/MergeResponse declared at api.ts:585/624. However, the merge endpoint's docstring documents returning conflict info on failure as deliberate design, and every shape already has dedicated working client handling, so this is API-consistency polish rather than a genuine hazard — downgraded to P3.

</details>

#### 65. CLAUDE.md Key Files table references src/gluon/bot.py which does not exist

**[CLAUDE.md:166](CLAUDE.md#L166)** · Wiring / dead code · _verified: inline this session_

The Key Files table lists 'src/gluon/bot.py | Telegram bot interface (legacy, see bot_core.py)' but no bot.py exists in src/gluon (verified by ls). Agents and contributors following the project guide will attempt to read a phantom file. The rest of the table checks out against the tree.

<details><summary>Verification note</summary>

src/gluon/bot.py does not exist; CLAUDE.md Key Files table references it; pyproject.toml:95 also lists 'gluon.bot' in a mypy override.

</details>

#### 66. Event constants TODO_UPDATED and ACTIVITY_CREATED are never published or subscribed

**[src/gluon/events/types.py:46](src/gluon/events/types.py#L46)** · Wiring / dead code · _verified: inline this session_

TODO_UPDATED = "todo.updated" (types.py:46) and ACTIVITY_CREATED = "activity.created" (types.py:57) have zero references outside types.py — neither the constant names nor the string literals appear in any emit, publish, or subscribe call in src or web-ui. Todo snapshots and activity log entries flow through entirely different paths (store polling / direct API), so these constants are misleading dead vocabulary in the event taxonomy.

<details><summary>Verification note</summary>

TODO_UPDATED (types.py:46) and ACTIVITY_CREATED (57) appear ONLY at their definitions — never published or subscribed.

</details>

#### 67. Six WebSocket Pydantic message models defined but websocket.py hand-builds dicts instead

**[src/gluon/web/models.py:421](src/gluon/web/models.py#L421)** · Wiring / dead code · _verified: inline this session_

WebSocketMessage, RunUpdatedMessage, RunCreatedMessage, LogLineMessage, SubscribeLogsRequest, and UnsubscribeLogsRequest (web/models.py:421-459) are referenced nowhere else in src or tests. websocket.py constructs raw dicts ({"type": "run_updated", ...}) for every broadcast, so the typed schema and the actual wire format can drift silently (the dicts already carry fields like ralph_enabled that the models lack).

<details><summary>Verification note</summary>

WebSocketMessage/RunUpdatedMessage/RunCreatedMessage/LogLineMessage defined (web/models.py:421+) but websocket.py imports none of them — it hand-builds dicts (line 84).

</details>

#### 68. Image attachments silently dropped when upload fails after task creation

**[web-ui/src/components/CreateTaskDialog.tsx:681](web-ui/src/components/CreateTaskDialog.tsx#L681)** · Web UI error handling · _verified: verifier agent_

After createRun succeeds, each pending image is uploaded with a per-promise .catch that logs and returns null, so Promise.all always resolves and the dialog closes reporting success even if every screenshot failed to attach. The agent then runs without the visual context the user explicitly provided. The same swallow-and-null pattern exists in RunDetailDialog's resume flows (handleResume/handleSendNow).

```
uploadAndAttachImage(run.id, img.file).catch((err) => {
  console.error(`Failed to upload image ${img.file.name}:`, err)
  return null
})
```

**Fix:** Collect the nulls from Promise.all and, if any upload failed, show a toast.error like 'Task created but 2 of 3 images failed to attach' instead of closing silently.

<details><summary>Verification note</summary>

Verified CreateTaskDialog.tsx:679-686: each upload's .catch returns null so Promise.all always resolves and the dialog closes via the success path (lines 688-690). The identical swallow-and-null pattern exists in RunDetailDialog.tsx:874 and :948 (handleResume/handleSendNow) and RunDetailPage.tsx:502/:572, as claimed; P3 is appropriate since it is a rare error path that degrades context rather than breaking the run.

</details>

#### 69. Stop-loop action fails silently and ignores success=false responses

**[web-ui/src/components/../src/App.tsx:521](web-ui/src/components/../src/App.tsx#L521)** · Web UI error handling · _verified: verifier agent_

handleStopLoop console.errors on rejection with no toast (unlike its siblings handleCancelRun/handleArchiveRun which both toast), and when the API returns {success: false} it does nothing at all. A user clicking 'stop loop' on a runaway ralph loop — a cost-control action — gets no confirmation either way and the loop may keep burning money.

```
} catch (err) {
        console.error('Failed to stop loop:', err)
      }
```

**Fix:** Add toast.success on success and toast.error on rejection or response.success === false, matching handleCancelRun directly above it.

<details><summary>Verification note</summary>

Verified at App.tsx:506-525: the catch only console.errors with no toast, while handleCancelRun (line 470) and handleArchiveRun (line 500) both toast on failure. All backend failure paths (api.py:1263-1277) raise HTTPException, so a failed stop-loop from the kanban card surfaces nothing to the user. Minor caveat: the success:false branch is unreachable (backend always returns success=True; failures are HTTP errors) and the run-detail LoopProgressTab has proper toasts, so this is a feedback gap on one UI path — P3 stands.

</details>

#### 70. useRunsWithWebSocket bypasses the fetchJson client — loses credentials policy and error detail

**[web-ui/src/hooks/useWebSocket.ts:219](web-ui/src/hooks/useWebSocket.ts#L219)** · Web UI error handling · _verified: verifier agent_

The initial runs load uses raw fetch('/api/runs?limit=100') instead of the shared fetchRuns/fetchJson helper, so it omits credentials: 'include' (which api.ts:110-114 documents as critical if the API ever runs on a different origin) and replaces the server's ApiError detail with a generic 'Failed to fetch runs' string that App.tsx renders full-screen. It also hardcodes the limit instead of reusing fetchRuns({limit: 100}).

```
const response = await fetch('/api/runs?limit=100')
      if (!response.ok) throw new Error('Failed to fetch runs')
```

**Fix:** Replace the raw fetch with the existing fetchRuns({ limit: 100 }) from lib/api.ts so auth, error detail extraction, and JSON-parse handling stay consistent.

<details><summary>Verification note</summary>

Verified at useWebSocket.ts:219-220: raw fetch with hardcoded limit and generic 'Failed to fetch runs', while lib/api.ts:107-144 provides fetchRuns/fetchJson with credentials:'include' (comment at 110-114 calls it critical for auth) and ApiError carrying server detail; App.tsx:775-778 renders the generic string in place of the board. However fetch defaults to same-origin credentials, so cookies are still sent in all current deployments (FastAPI serves the SPA same-origin) — this is a real consistency/latent hazard plus error-detail loss, not a live bug, so P3 is correct.

</details>

## Needs focused follow-up

Core claim verified; one sub-claim needs a targeted check before acting.

- **[P2] Dialog 'Full screen' link navigates to tabs RunDetailPage cannot render — blank content panel** — [web-ui/src/components/RunDetailDialog.tsx:134](web-ui/src/components/RunDetailDialog.tsx#L134) · Web UI duplication & quality
  CONFIRMED the 'Full screen' link forwards activeTab into the route (to=/runs/${runId}/${activeTab}). NOT fully confirmed that RunDetailPage can't render every such tab — needs a tab-set diff between the two components.
- **[P2] Hard-cap fields (Theme D3) exist in backend request/response models but are absent from all frontend types** — [web-ui/src/lib/types.ts:207](web-ui/src/lib/types.ts#L207) · API contract drift
  types.ts has max_cost_usd (192/224) but the full hard-cap set (max_duration_minutes/max_total_tokens) present in backend models.py:132/139/181 was not exhaustively diffed → some hard-cap fields likely missing on the TS side. Needs a precise field diff.
- **[P3] TS Run interface lacks user_id attribution field present in RunResponse** — [web-ui/src/lib/types.ts:15](web-ui/src/lib/types.ts#L15) · API contract drift
  Naming mismatch: TS has created_by_user_id (types.ts:99) vs backend RunResponse user_id (web/models.py:67). Whether the Run interface specifically omits it needs a focused check.

## Pattern-consistent (high-probability, not individually re-read)

Each belongs to a cluster whose representative findings were confirmed (e.g. the cross-transport duplication cluster, the docs-drift cluster, the never-called-broadcast cluster). Spot-check before fixing.

### Python duplication (4)

- **[P1] Telegram /resume resolves a specific session but never passes it to execute_task (drifted copy of Discord resume flow)** — [src/gluon/transport/telegram.py:843](src/gluon/transport/telegram.py#L843)
  Resume-flow drift; consistent with confirmed cross-transport drift (MODEL_ALIASES/DEFAULT_MODEL). Flagged for line-diff against discord resume.
- **[P2] Task-launch flow copy-pasted three times inside discord.py (and again in telegram.py) with partial drift** — [src/gluon/transport/discord.py:1668](src/gluon/transport/discord.py#L1668)
  Transport task-launch duplication; consistent with confirmed _truncate/is_authorized/MODEL_ALIASES/get_redis_url duplication across telegram+discord. Not line-diffed this session.
- **[P2] Cancel-run business logic duplicated verbatim in telegram and discord transports** — [src/gluon/transport/telegram.py:886](src/gluon/transport/telegram.py#L886)
  Cancel-run duplication; consistent with confirmed transport cluster.
- **[P2] Approval-decision flow duplicated across telegram callback and discord interaction handlers** — [src/gluon/transport/telegram.py:326](src/gluon/transport/telegram.py#L326)
  Approval-decision duplication; consistent with confirmed transport-duplication cluster. Not line-diffed.

### Wiring / dead code (2)

- **[P1] Event-bus chat notification path can never deliver: subscribers build NotificationDispatcher with zero transports** — [src/gluon/events/subscribers.py:239](src/gluon/events/subscribers.py#L239)
  NotificationDispatcher-with-zero-transports; consistent with confirmed broadcast-never-called wiring gaps. Flagged for a direct read of subscribers.py:239.
- **[P2] Eleven behavior-controlling env vars are read in code but documented nowhere** — [src/gluon/runner.py:451](src/gluon/runner.py#L451)
  Same env-var documentation gap cluster; not individually opened.

### Python error messages (3)

- **[P2] Chat-agent errors sent to bot users as bare 'Error: <raw exception>'** — [src/gluon/bot_core.py:504](src/gluon/bot_core.py#L504)
  Bare 'Error: <raw exception>' to bot users; consistent with confirmed chat_agent/HTTP raw-error leakage.
- **[P2] Cancel endpoint collapses four distinct failure causes into a bare 500 'Failed to cancel run'** — [src/gluon/web/api.py:693](src/gluon/web/api.py#L693)
  Consistent with confirmed py-error-messages cluster (raw-500 leakage at 4187). Not individually opened.
- **[P3] CLI prints 'Failed to remove project' with no reason when remove returns False** — [src/gluon/cli.py:183](src/gluon/cli.py#L183)
  'Failed to remove project' no-reason message; consistent with confirmed bare-error cluster.

### API contract drift (4)

- **[P3] AgentMessageData type union is stale and duplicated: backend emits 5 message types it lacks** — [web-ui/src/lib/types.ts:630](web-ui/src/lib/types.ts#L630)
  Stale AgentMessageData union; consistent with the confirmed 4×-AgentMessage drift.
- **[P3] TS Workspace interface omits the budget/spend fields WorkspaceResponse returns** — [web-ui/src/lib/types.ts:788](web-ui/src/lib/types.ts#L788)
  Workspace budget/spend fields; consistent with the confirmed workspace-budget route having no frontend consumer.
- **[P3] QueueFollowupResponse in api.ts drops message_id returned by the backend** — [web-ui/src/lib/api.ts:229](web-ui/src/lib/api.ts#L229)
  QueueFollowupResponse drops message_id; minor contract gap, not individually opened.
- **[P3] Tasks, approvals, and webhook-CRUD route families have no web-UI consumer (bot/CLI/external only)** — [src/gluon/web/api.py:5088](src/gluon/web/api.py#L5088)
  Consistent with the route-consumer audit above (0 frontend refs); tasks/approvals/webhooks are bot/CLI/external surfaces.

### Web UI duplication & quality (4)

- **[P2] Two QuestionModals render simultaneously for the same question when the run dialog is open** — [web-ui/src/components/RunDetailDialog.tsx:2921](web-ui/src/components/RunDetailDialog.tsx#L2921)
  Double QuestionModal; consistent with the twin-component duplication. Flagged for direct read.
- **[P2] formatDuration/formatTokens duplicated in 4 components despite lib/format.ts; formatDurationMs name collision with different output** — [web-ui/src/components/StreamingLogViewer.tsx:1059](web-ui/src/components/StreamingLogViewer.tsx#L1059)
  formatDuration/formatTokens duplication; consistent with confirmed ToolBreakdown-bypasses-lib finding. lib/format.ts exists.
- **[P2] Mangled dependency array in RunDetailDialog load effect: `resumePendingImages.forEach` as a dep with a displaced comment** — [web-ui/src/components/RunDetailDialog.tsx:488](web-ui/src/components/RunDetailDialog.tsx#L488)
  Mangled effect dependency array; not individually opened. Flagged for a direct read of the load effect.
- **[P3] Polling cadences hardcoded in list pages despite lib/polling.ts existing to centralize them** — [web-ui/src/components/MergeQueuePage.tsx:58](web-ui/src/components/MergeQueuePage.tsx#L58)
  Hardcoded polling cadence vs lib/polling.ts; consistent with confirmed UI-centralization gaps.

### Documentation drift (6)

- **[P1] Task-profile budgets and thinking tokens in CLI help and CLI-REFERENCE are off by orders of magnitude** — [src/gluon/cli.py:1061](src/gluon/cli.py#L1061)
  Task-profile budget/thinking-token docs mismatch; consistent with confirmed README/CLI doc drift. Flagged for a profile-table diff.
- **[P2] CHANGELOG missing v0.12.1 and all eleven v0.11.x patch releases; 'Unreleased' claims nothing pending despite 17 shipped commits** — [CHANGELOG.md:10](CHANGELOG.md#L10)
  CHANGELOG version gaps; consistent with docs-drift cluster. Not individually verified against git tags this session.
- **[P2] README custom-formula directories don't match FormulaLoader search paths** — [README.md:356](README.md#L356)
  Custom-formula dir mismatch vs FormulaLoader; consistent with docs-drift cluster. Not individually opened.
- **[P2] A dozen operational GLUON_* env vars read by code are absent from every doc** — [src/gluon/runner.py:68](src/gluon/runner.py#L68)
  Consistent with confirmed docs-drift cluster (DEVELOPMENT.md env vars, webhook-secret name). Not individually opened this session.
- **[P3] API.md REST listing predates the 0.12 endpoint groups (schedules, tasks, fork/snooze, approvals, formulas, queues)** — [docs/API.md:16](docs/API.md#L16)
  API.md endpoint listing predates 0.12 groups; consistent with confirmed route inventory.
- **[P3] docker-compose comment tells users to set CLAUDE_CODE_USE_BEDROCK manually, contradicting the provider-abstraction rule** — [docker-compose.yml:60](docker-compose.yml#L60)
  Comment recommends manual CLAUDE_CODE_USE_BEDROCK, contradicting the provider-abstraction rule in CLAUDE.md. Plausible; not opened.

## Corrected & refuted

Claims that did not survive verification, recorded so they aren't re-reported.

- **Corrected — ExecutionRun.thread_id resume-detection feature is dead: column persisted but never written, lookup never called** — [src/gluon/models.py:971](src/gluon/models.py#L971)
  CORRECTED: the headline 'never written' is WRONG — thread_id IS written (store.update_run 3363/3398; set in bot_core.py:329). The real dead part is the READ: get_run_by_thread_id (store.py:3691) has 0 callers, so resume-by-thread detection never fires. Reframe: written but never read back.
- **Refuted — Schedule enable endpoint swallows cron errors and returns 200** — `src/gluon/web/api.py:5534`
  The excerpt matches, but the claimed harm is unreachable: invalid timezones never raise (compute_next_fire_in_tz falls back to UTC at recurrence.py:140-143), invalid crons cannot be stored via the API (create at api.py:5409 and PATCH at api.py:5493 both validate via _resolve_cron/validate_cron and return 400), and enable changes neither cron nor timezone, so the retained next_fire_at is still valid — and the scheduler self-heals stale/None values via _advance_next_fire (task_scheduler.py:192-202) and the startup backfill (task_scheduler.py:84-91). The only residue is a missing log line vs the sibling handler — polish, not a bug.
- **Refuted — Discord failure messages give no reason at all — just '❌ Failed - <id>'** — `src/gluon/transport/discord.py:1562`
  The claim misreads control flow: Discord handlers call the shared bot_core.execute_task (discord.py:1539/1640/1769), which sends the failure reason to the same Discord channel via the handler-supplied send_callback — '❌ Failed ({id}): {result.error}' at bot_core.py:435 and '❌ Error ({id}): {e}' at bot_core.py:447 — so the error is NOT logged server-side only. bot_core also swallows its own exceptions (no re-raise at 444-447), so the bare '❌ Failed' edits at discord.py:1562/1663/1792 only fire when editing the status banner itself fails; the finding's claim that bot_core.py:435/447 is Telegram-only is wrong since bot_core is transport-agnostic.

## Suggested fix order

1. **Security/correctness P1s first:** cross-workspace `os.environ` credential bleed ([api.py:236](src/gluon/web/api.py#L236)); false '✅ Complete' on failed runs ([agent.py:1185](src/gluon/agent.py#L1185)); silent worktree-isolation drop ([runner.py:1085](src/gluon/runner.py#L1085)).
2. **Event-loop blocking:** move all sync git/CLI subprocess calls in `web/api.py` off the loop (`refresh_all_runs`, `list_projects`, `test_vercel_token`) — single highest-leverage perf fix; while there, add SQLite WAL + `busy_timeout` ([store.py:886](src/gluon/store.py#L886)).
3. **WebSocket teardown leaks** (useWebSocket / useRunLogStream) and the **partial run_updated payload** wipe.
4. **Silent-failure cluster** in `runner.py` (worker stderr→/dev/null, chain dispatch swallowed at DEBUG, cancel without SIGKILL escalation, never-reaped workers).
5. **Dead subsystems:** decide keep-or-delete on `merge_queue.MergeQueueService`, `queue/redis_queue`, the never-called WS broadcasts, and the un-swept chat_history/message_run_map TTL tables ([store.py:5467](src/gluon/store.py#L5467)).
6. **Docs sweep:** README model table (3 errors), `.[telegram]` extra, `docs/API.md` phantom `gluon.bot`, DEVELOPMENT.md env-var names, CLAUDE.md `bot.py` reference + pyproject mypy override.
7. **Duplication consolidation:** collapse the RunDetailDialog/RunDetailPage twins and the 4× AgentMessage/parseMessages; pull transport-shared logic (truncate, is_authorized, model aliases, approval/cancel/resume) into bot_core/base.

## Coverage gaps

- The **completeness-critic** pass never ran (throttled both times). Categories no dimension explicitly covered: **input validation / authz on API routes**, **auth/OIDC flows** (`auth.py`), **`git_manager.py` correctness**, and **secrets handling**. Worth a dedicated pass.
- The ~15 'dead store methods' claim was spot-checked at 3/15 (all dead); a full sweep would confirm the rest.
