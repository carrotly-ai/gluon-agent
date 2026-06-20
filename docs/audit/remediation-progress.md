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
- [ ] **2. Debug prints** `[low/trivial/none]` — remove 9×`[RECOVERY]` (`api.py`) + 1×`[AGENT]` (`agent.py`).
- [ ] **3. Leaf dead code (Clusters E–J)** `[med/low/very-low]` — re-grep each symbol before deletion; delete test-only symbols + their tests together.
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
_Filled in during SETUP._

## NEVER TOUCH (audit refuted — §10)
Do not merge `cleanup.py`↔`session_cleanup.py`; do not merge `image_storage.py`↔`files.py`;
do not delete `compute_next_fire` (ValueError contract); do not remove `GLUON_REDIS_URL`/`redis`
(feed the LIVE event bus).

## NEXT STEPS
SETUP done → item 1 (latent bugs) committed. Next: **item 2 — remove debug prints** (`api.py` ×9, `agent.py` ×1).
