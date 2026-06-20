# Audit Remediation — Progress (STATE.md)

Living tracker for implementing `docs/audit/gluon-simplicity-audit-2026-06-20.md` §9 roadmap.
Stacked onto **PR #154** (branch `feat/loop-engineering`). One roadmap item per loop
iteration; every commit gated on `ruff + mypy + pytest` (web-ui also `biome + build`).
Behavior-preserving except the two named latent-bug fixes.

## Gate baseline (known pre-existing noise — do NOT fix, do NOT add to)
- 3 mypy errors: `formulas.py`/`cli.py:2892` (yaml stubs), `agent_hooks.py:367` (arg-type).
- 1 environmental test failure: `tests/test_doctor.py::test_all_ok_for_clean_store` (host session disk usage).
Green = no NEW ruff/mypy errors in touched files, no NEW test failures.

## IN SCOPE (implement autonomously)

### Tier 1 — quick wins
- [x] **1. Latent bugs** `[bug/high/low]` — fix `formula run` (`anyio.from_thread.run`→`anyio.run`); wire `WorkQueueManager` finalization (`_finalize_queue_item`) so queue items stop leaking in RUNNING. +4 regression tests. *(commit: see below)*
- [x] **2. Debug prints** `[low/trivial/none]` — removed 9×`[RECOVERY]` (`api.py` `_run_recovery`) + 1×`[AGENT]` (`agent.py`) + a redundant `traceback.print_exc()`; all sat beside `logger.*` calls so logging behavior is unchanged.
- [~] **3. Leaf dead code (Clusters E–J)** `[med/low/very-low]` — re-grep each symbol before deletion; delete test-only symbols + their tests together.
  - [x] **3A — prod-only zero-caller deletes**: websocket `stream_log_line`/`broadcast_todos_updated`; `agent.execute_simple`; resume_coordinator singleton (`_coordinator`+get/set/start/stop); `transport.get_telegram_transport`; capabilities `SLACK_CAPS`/`CLI_CAPS`; `models.Project.is_workspace_managed` + `ImageAttachment.to_markdown`; `commands.search_commands`; `run_telegram_transport`/`run_discord_transport`; runner `supervisor`/`supervisor_running` props. Gate green (2320 passed).
  - [~] **3B — test-only symbols + their tests**:
    - [x] **3B-i**: image_storage `save_image_from_file`/`copy_to_worktree`/`get_markdown_references` (+ TestCopyToWorktree/TestMarkdownReferences/test_save_from_file); `blueprint.run_validation` (+ TestRunValidation). −17 tests; suite 2303 passed.
    - [x] **3B-ii**: `policies.should_auto_resume` (+ TestShouldAutoResume); `recurrence.cron_to_recurrence` (+ orphaned `_CRON_TO_ISO` + 4 scattered tests, kept recurrence_to_cron tests); `bot_core.resolve_project` (+ TestResolveProject). **KEPT `bot_core.get_task`** — it's a legitimate symmetric accessor (register/unregister/get) and the only clean way TestTaskRegistration verifies the live register/unregister; removing it would force tests to inspect private `_active_tasks`. −11 tests; suite 2292 passed.
  - [ ] **3C — misc + needs-care**: `MODEL_IDS`/`_ModelIDsProxy`, store `SCHEMA` constant, `events/types` EXECUTION/SYSTEM, `circuit_breaker.last_output_length` (write-only field — read method first), capability boolean fields, `scheduler.TaskStatus` re-export, `cli recover --wait`, RedisEventTransport publisher trio. **SKIP `STALLED_THRESHOLD`** (live at health_monitor.py:142 — misleading-value fix, not a deletion).
- [ ] **4. web-ui dead exports** `[med/low/very-low]` — ~30 dead `api.ts` fns + 12 orphan types + `POLL_FAST` + stale TODOs (§7). *(advanced-git api.ts clients → DEFER, see issues)*
- [ ] **5. store `in keys` guards** `[low/low/low]` — drop obsolete guards in `_row_to_run`/`update_run` (§2).

### Tier 2 — medium dedup
- [ ] **6. web/api lookup helpers** `[high/med/low]` — `_resolve_run_or_404`/`_resolve_project_or_404`/`_broadcast_run`/`_workspace_to_response`; collapse 32×/24×/22×/4× boilerplate (§4.9).
- [ ] **7. runner `_run_task` seams** `[high/med/med]` — `_run_blueprint_validation`, `_finalize_worktree` (shared with `_run_ralph_loop`), `_stream_agent_messages` (§2/§4).
- [ ] **8. chat_agent tool cache** `[med/med/low]` — cache tools + MCP server on `self`; derive `allowed_tools` from decorators (§2).
- [ ] **9. git_manager `_run_gh`** `[med/med/low]` — add helper + dedupe merge-base/numstat (§4.7).
- [ ] **10. budget enforcement** `[med/low/low]` — collapse core↔runner verbatim dupes (§4.6).
- [ ] **11. dead formula routes** `[med/low/med]` — add `tests/test_api_formulas.py` first, then remove the 2 internally-dead formula HTTP routes (§3J). *(provider/budget/supervision routes → DEFER, external surfaces.)*

### Tier 3 — larger behavior-preserving refactors (each its own iteration, full-suite gated)
- [ ] **12. web/api.py decomposition** `[high/med/low]` — split into `web/routes/*` APIRouter modules + `web/background.py` for the 6 coroutines (§2).
- [ ] **13. PollingService base** `[high/large/med]` — base for the 6 keep-running engines + generic `UndeliveredItemWatcher`; wire a Telegram `QuestionWatcher` for parity (§4.1).
- [ ] **14. Unify auto-resume** `[high/large/med]` — one coordinator/counter/cap; fixes the cap-bypass (§4.2).
- [ ] **15. Merge RunDetail twins** `[high/large/med]` — shared hooks + thin shells for `RunDetailDialog`/`RunDetailPage` (§7).
- [ ] **16. Declarative store table-spec** `[med/large/med]` — migrate ONE entity first to prove the pattern (§2).

### Tier 4 — doc drift
- [ ] **17. Doc fixes** `[low/low/none]` — chat_agent SYSTEM_PROMPT + `run_task` sonnet/haiku→opus-4.8; `CLI-REFERENCE.md` omissions; redis_transport/README counts (§8).

## Deferred — tracked as issues (owner sign-off; reverses documented KEEP or deletes public API)
- [#155](https://github.com/carrotly-ai/gluon-agent/issues/155) — distributed Worker/Job/RedisJobQueue subsystem (~1,500 LOC) — remove or gate (§3A; KEEP at remediation-plan L220)
- [#156](https://github.com/carrotly-ai/gluon-agent/issues/156) — MergeQueueService dead at runtime — wire or remove (§5; KEEP at remediation-plan L217)
- [#157](https://github.com/carrotly-ai/gluon-agent/issues/157) — advanced-git UI unbuilt — build or delete 13 api.ts clients + GitManager methods (§5/§7)
- [#158](https://github.com/carrotly-ai/gluon-agent/issues/158) — witness recovery half — wire `execute_action` or delete (§3B)
- [#159](https://github.com/carrotly-ai/gluon-agent/issues/159) — externally-documented dead routes (provider/budget/supervision) — retire-or-keep (§3J)
- [#160](https://github.com/carrotly-ai/gluon-agent/issues/160) — §10 frontend-coupled "dead" fields — coordinated FE+BE change (RalphStatusBlock, ResumeRunResponse ids, auth OIDC branch)

## NEVER TOUCH (audit refuted — §10)
Do not merge `cleanup.py`↔`session_cleanup.py`; do not merge `image_storage.py`↔`files.py`;
do not delete `compute_next_fire` (ValueError contract); do not remove `GLUON_REDIS_URL`/`redis`
(feed the LIVE event bus).

## NEXT STEPS
Items 1–2, 3A, 3B (i+ii) done. Next: **item 3C — misc/needs-care dead symbols**: `MODEL_IDS`/`_ModelIDsProxy` (re-grep the 30 refs — confirm test-only first), store module-level `SCHEMA` constant (confirm it duplicates inline `_init_db` DDL and is unused), `events/types` EXECUTION/SYSTEM constants, `circuit_breaker.last_output_length` (write-only — READ `record()` first), capability boolean fields (dataclass surgery on TELEGRAM_CAPS/DISCORD_CAPS), `scheduler.TaskStatus` re-export, `cli recover --wait` flag, RedisEventTransport publisher trio (keep subscriber). SKIP `STALLED_THRESHOLD`. Then check item 3 and move to item 4.
