# Gluon-Agent Code Audit — Synthesis Report

*Read-only audit. 248 verified findings across 33 areas. Recommendations only — no code was changed.*

---

## 1. Executive summary

Gluon-Agent is a **functional but over-built** orchestrator carrying a large, well-documented backlog of dead and duplicated code. The core finding: roughly **5,000–6,000 LOC is reclaimable today** with near-zero behavioral risk, and a further large block (god-file decomposition + engine consolidation) would meaningfully improve maintainability without touching behavior.

**Headline numbers:**

| Metric | Value |
|---|---|
| God-files (>2,500 LOC, single-responsibility violations) | **5** — `web/api.py` (6,863), `store.py` (6,295), `cli.py` (5,752), `runner.py` (3,215), `RunDetailDialog.tsx` (2,892) |
| Confirmed-removable dead-code items | **89** (of 110 removable claims) |
| Largest dead subsystem | Distributed Worker/Job/RedisJobQueue — ~1,500 LOC incl. tests + 2 DB tables |
| Overlapping "keep-running" engine pairs | **6+** (resume×2, cleanup×2, scheduler×2, watcher×2, broadcast×2, budget-enforcement×2) |
| Confirmed-dead frontend exports | **30+** api.ts functions + 12 orphan types |
| Refuted false-positives (audit caught) | **12** |

The codebase shows a recurring pattern: **features were scaffolded end-to-end (model → store → API → CLI → frontend) but never wired to a processor or UI**, then a prior audit (`docs/code-quality-audit-2026-06-10.md`) flagged many of them and the team deliberately *kept* them as "future foundations." That decision is defensible but has accumulated cost — three of the four largest dead subsystems (distributed queue, merge-queue, witness recovery) advertise themselves across CLI + API + UI while doing nothing at runtime.

**Two things masquerade as dead code but are actually latent bugs** (fix, don't delete): `WorkQueueManager.mark_completed/mark_failed` never called → queue items leak in `RUNNING` forever (§5); `cli.py:3409 formula_run` uses `anyio.from_thread.run()` from a sync command → `RuntimeError`, command is non-functional (§5).

---

## 2. Complexity hotspots

The four backend god-files concentrate most maintenance risk. Each has a clean decomposition seam.

### `web/api.py` — 6,863 LOC, one `create_app` function
`create_app` (lines 312–6859) nests **~120–161 routes**, all serializer helpers, validators, websocket handlers, and **6 background-task coroutines** (`_poll_run_status_changes`, `_poll_log_updates`, `_poll_pr_status_changes`, `_cleanup_old_logs`, `_cleanup_old_worktrees`, `_sweep_auth_state`) inside a single closure, plus a 140-line model-import block (50–188).
- **Seam:** split into per-domain `APIRouter` modules under `web/routes/` (runs, projects, workspaces, git, tasks, schedules, auth, usage); move the 6 coroutines to `web/background.py`. Pure mechanical move, no behavior change.
- **Embedded sub-hotspots:** `recover_run`/`_run_recovery` (1083–1266) inlines a 150-line background executor **with 9 `print("[RECOVERY]…")` debug statements** (1158–1231) and duplicates the CLI `recover` flow; `sync_project_git` (4469–4651) is a 183-line git orchestrator in the handler with `refresh_status`+`_build_git_status_response` repeated 5×.

### `store.py` — 6,295 LOC, 35 `_row_to_*` converters
- `_row_to_run` (3566–3730, 165 LOC) is **~74 lines of obsolete `row["x"] if "x" in keys else default` guards** — migrations are idempotent and always run before any read, so every column is guaranteed present. `update_run` (3394–3515) is a 60-placeholder monolithic UPDATE.
- **Seam:** (a) drop the `in keys` guards (removes ~70 lines immediately, zero risk); (b) introduce a declarative table-spec (columns + serializers) to collapse the ~35 near-identical `_row_to_*`/`create_*`/`update_*` blocks — migrate one entity first to prove the pattern.

### `cli.py` — 5,752 LOC
- Run-lookup `get_run_by_short_id(id) or get_run(id)` idiom repeated **~9×** despite existing `_resolve_*_or_exit` helpers for tasks/agents; webhook ID-prefix resolution triplicated; three near-identical byte formatters (`_format_bytes`/`_short`/`_compact`); redundant in-function re-imports of already-top-imported `format_run_status`/`format_duration`/`TaskRunner`/`os`.
- **Seam:** add `_resolve_run_or_exit` + `_resolve_webhook_or_exit`, collapse the byte formatters, drop the local re-imports.

### `runner.py` — 3,215 LOC, `_run_task` is ~920 LOC
`_run_task` (1076–1997) spans env setup, worktree creation, prompt assembly, an inline 150-line **blueprint validation engine** (with an inner `_bp_resume` closure that re-implements the streaming loop), git finalization, recovery, and a 130-line `finally`. Nesting reaches 8+ levels.
- **Seam:** extract `_run_blueprint_validation(...)`, `_finalize_worktree(...)`, and a shared `_stream_agent_messages(...)` (reused by `_bp_resume` and the main loop). The worktree-finalization block is *also* duplicated verbatim in `_run_ralph_loop` (1549–1644 vs 2843–2938, ~90 LOC) — one helper fixes both.

### Secondary: `agent.py:_build_options` (543–787, ~245 LOC)
~15 independent SDK feature toggles applied serially, with `ApprovalPolicy` imported twice (482, 637). Extract `_build_system_prompt()`, `_build_extra_pre_tool_hooks()`, `_apply_feature_flags()`.

### Secondary: `chat_agent.py:_create_tools` (142–1758, ~1616 LOC)
All 41 tool closures in one method, **rebuilt on every message** (`chat()` calls `_create_tools()` + `create_sdk_mcp_server()` per turn with no caching). Tool names are maintained in **3 hand-synced lists** (decorators, return list, `allowed_tools`). Cache the tool set + MCP server on `self`; derive `allowed_tools` from the decorated functions.

---

## 3. Confirmed dead code (safe to remove)

All items below have verdict **confirmed**. Grouped by cluster; ~LOC is the removable footprint.

### Cluster A — Distributed Worker/Job/RedisJobQueue subsystem (single coordinated removal, ~1,500 LOC)
*Six finders independently confirmed this; `redis_queue.py:3-8` self-documents "no importers anywhere." Caveat: prior audit recorded a deliberate KEEP-as-future-foundation decision — see §10.*

| Symbol | Location | Evidence | ~LOC |
|---|---|---|---|
| `RedisJobQueue` + `get_queue()`/`_queue_instance` | `queue/redis_queue.py` (whole) | Zero importers except `queue/__init__.py` re-export | 393 |
| `dequeue/update_status/release_job/get_worker_jobs/get_queue_size/recover_expired_leases/subscribe_updates` | `redis_queue.py:116-327` | Zero external callers even within the module | (incl. above) |
| 17 worker/job store CRUD methods | `store.py:4905-5161` | Only callers: `tests/test_distributed.py` | ~260 |
| `update_worker_heartbeat`, `get_expired_lease_jobs` | `store.py:4996, 5127` | Called by **nothing** — not even tests | (incl. above) |
| `workers`, `jobs` tables | `store.py:220, 237` | Written only by test-only CRUD | (DDL) |
| `Worker/Job/WorkerType/WorkerStatus/JobStatus` | `models.py:1709-1841` | Used only by store/queue/tests | ~120 |
| `tests/test_distributed.py` | whole file | Tests dead code | ~570 |

### Cluster B — Witness recovery half (suggest_action stays — see §10)
| Symbol | Location | Evidence | ~LOC |
|---|---|---|---|
| `execute_action` + ESCALATE branch | `witness.py:184-202` | No prod caller; `suggest_action` never returns ESCALATE | ~40 |
| `_send_nudge/_recent_nudge_exists/_record_nudge_outcome` | `witness.py` | Only `tests/test_witness.py` | (incl.) |
| `LOOPING_NUDGE_PROMPT`, `NUDGE_COOLDOWN_SECS` | `witness.py` | Never read in prod | — |

### Cluster C — "Advanced Git Operations" exception family + dead branches
| Symbol | Location | Evidence | ~LOC |
|---|---|---|---|
| `GitOperationError`+4 subclasses | `core.py:175-222` | Zero `raise`/`except` anywhere; git_manager uses dict returns | ~45 |
| `_get_remote` shell-substitution branch | `git_manager.py:128` | `$(…)` never expands under `create_subprocess_exec`; always falls to origin | ~12 |

### Cluster D — Store orphan methods (per-method owner check advised)
`get_run_with_project`, `mark_run_snapshotted`, `delete_run_snapshots`, `get_latest_supervision_decision`, `count_supervision_decisions`, `get_pending_questions_for_run`, `get_channel_mapping`, `list_orphan_images`, `delete_user`, `update_ralph_iteration`, `get_ralph_iteration`, `get_latest_ralph_iteration` — `store.py` (12 methods, 0 callers; live siblings exist for each). **~90 LOC.** `get_channel_mapping` is named in `docs/API.md`/`ARCHITECTURE.md` (doc-only).

### Cluster E — Websocket / web-model dead surface
| Symbol | Location | Evidence | ~LOC |
|---|---|---|---|
| `broadcast_todos_updated` | `websocket.py:345-374` | 0 callers; live path is `message_callback` | ~30 |
| `stream_log_line` | `websocket.py:214-222` | 0 callers; live path is `stream_agent_message` | ~9 |
| `ResumeRunResponse.original_run_id/new_run_id` | `models.py:447-448` | "Deprecated: same as run_id"; no reader (see §10 — TS interface still declares them) | ~2 |
| `RunDetailResponse` 8 redeclared fields | `models.py:111-137` | Byte-identical to inherited `RunResponse` fields; verified no-op in Pydantic v2 | ~8 |

### Cluster F — Agent / hooks / image
| Symbol | Location | Evidence | ~LOC |
|---|---|---|---|
| `GluonAgent.execute_simple` | `agent.py:1405-1438` | 0 callers repo-wide | ~30 |
| `TodoCollector.working_dir`/`image_service` | `agent_hooks.py:330-331` | Never set/read (copy-paste from `ScreenshotCollector`) | 2 |
| `ScreenshotCollector.collected_ids` | `agent_hooks.py:203-205` | Property never read | 3 |
| `print("[AGENT] resume_with_fresh_context…")` | `agent.py:1509` | Debug print beside a `logger.info` | 1 |
| `ImageStorageService.copy_to_worktree` | `image_storage.py:332-379` | Prod uses base64 content blocks | ~47 |
| `ImageStorageService.get_markdown_references` | `image_storage.py:381-402` | Counterpart of above | ~22 |
| `ImageStorageService.save_image_from_file` | `image_storage.py:200-218` | All callers pass bytes to `save_image` | ~18 |
| `ImageAttachment.to_markdown` | `models.py:1701-1703` | No caller; doc-only in API.md | 3 |

### Cluster G — Engines / coordinators / watchers
| Symbol | Location | Evidence | ~LOC |
|---|---|---|---|
| `_coordinator`+`get/set/start/stop_coordinator` | `resume_coordinator.py:359-395` | Singleton never used; all callers construct directly | ~36 |
| `WorktreeConfig.auto_merge`+`_merge_to_source`+cleanup branch | `worktree.py:21,169-186,246-257` | Always False in prod; merge-back via git_manager | ~25 |
| `run_validation` | `blueprint.py:136-168` | Runner uses `run_lint/run_test/run_autofix` directly | ~33 |
| `should_auto_resume` | `policies.py:257-279` | Prod builds `PolicyContext` inline | ~22 |
| `cron_to_recurrence` | `recurrence.py:70-102` | Inverse half never called; UI reads recurrence_days columns | ~33 |
| `ActivityLogger.cleanup` | `activity_log.py:61-63` | API/CLI call `store.cleanup_activities` directly | 3 |

### Cluster H — Bot core / commands / transports
| Symbol | Location | Evidence | ~LOC |
|---|---|---|---|
| `search_commands` | `commands.py:218-230` | 0 callers (UI filters client-side) | ~13 |
| `get_slash_commands` `force_refresh` param | `commands.py:156-179` | Never passed True | — |
| `bot_core.resolve_project` | `bot_core.py:210-241` | Test-only; transports have own `_resolve_project` | ~32 |
| `bot_core.get_task` | `bot_core.py:187-189` | Test-only accessor | 3 |
| `create_thread`/`create_thread_callback` cluster | `base.py:164-182`, `telegram.py:415-427`, `bot_core.py:282-353` | Never invoked; `thread_id` always None | ~50 |
| `run_telegram_transport`/`run_discord_transport` | `telegram.py:1139`, `discord.py:1801` | CLI owns start/stop | ~35 |
| `TransportResponse.editable` | `base.py:100-101` | Never set/read | 2 |
| `supports_threads/editing/reactions/typing/embeds/buttons` | `capabilities.py:17-33` | Only `max_message_length` consumed | ~6 |
| `SLACK_CAPS`, `CLI_CAPS` | `capabilities.py:58-78` | No Slack/CLI transport exists | ~20 |
| `get_telegram_transport` | `transport/__init__.py:26-30` | Callers import class directly | ~5 |

### Cluster I — Misc dead symbols
`MODEL_IDS`/`_ModelIDsProxy` (`models_config.py:56-89`, test-only, ~34 LOC) + its 6 unused dict methods; `Project.is_workspace_managed` (`models.py:909-913`); `TaskRunner.supervisor`/`supervisor_running` properties (`runner.py:2547-2555`); `CircuitBreaker.last_output_length` (write-only); `STALLED_THRESHOLD` (`health_monitor.py:26` — misleading dead constant printing 1800 vs real 900); module-level `SCHEMA` constant (`store.py:100-143`, duplicates inline `_init_db` DDL); `EXECUTION`+`SYSTEM` event constants (9, `events/types.py:48-58`); `scheduler.py` `TaskStatus` re-export; `cli.py recover --wait` flag; RedisEventTransport instance-publisher trio (`connect_publisher`/`publish`/`close_publisher`, test-only — keep subscriber).

### Cluster J — Dead HTTP routes / formula endpoints
`POST /api/formulas/validate` (`api.py:5236`), `GET /api/formulas/{name}` (`api.py:5185`), `GET /api/provider` (`api.py:2002`), `PUT /api/workspaces/{id}/budget` (`api.py:3164`) — all zero in-repo callers (the latter two are documented external surfaces; confirm no external automation first).

---

## 4. Overlapping / duplicated subsystems — consolidation map

The biggest structural debt is **N parallel implementations of the same lifecycle**. A handful of base classes/helpers collapse most of it.

### 4.1 Polling-loop scaffold — 6+ engines, ~180 LOC of pure boilerplate
`resume_coordinator`, `scheduler`, `task_scheduler`, `question_watcher`, `approval_watcher`, `health_monitor` each hand-roll identical `_running`/`_task`/`start`/`stop`/`is_running`/`_run_loop`.
- **Seam:** `PollingService` base (interval + abstract `async tick()`). `ApprovalWatcher`/`QuestionWatcher` are **~95% copy-paste** (differ only in 3 store/poster method names) → one generic `UndeliveredItemWatcher[T]` parameterized by `list_fn/post_fn/mark_fn`.
- **Side note:** Telegram never wires a `QuestionWatcher` (only Discord does) — `AskUserQuestion` from Telegram channels silently never surfaces. The base-class extraction makes parity trivial.

### 4.2 Auto-resume — 3 engines, 2 drifting counters/caps
`ResumeCoordinator` (`supervision_auto_resume_count` vs `config.max_auto_resumes`), `PRMonitorService` (`auto_resume_count` vs hardcoded `MAX_AUTO_RESUMES=5`), and the queue-drain path all call `runner.resume_in_place` with their own prompt-building, counter, and cap. **A run can exceed either nominal cap by being resumed via both paths.**
- **Seam:** one `ResumeCoordinator.request_resume(run, trigger, prompt)` owning a single counter+cap; PRMonitor/queue-drain feed it triggers.

### 4.3 Cleanup — intra-module duplication (NOT cross-module)
*Refuted: `cleanup.py` vs `session_cleanup.py` are disjoint — do not merge.* The real duplication is **inside** `cleanup.py`: `cleanup()` and `preview()` re-implement the full classification cascade in each service (4 places total → preview can silently drift from actual behavior); `LogCleanupService`/`WorktreeCleanupService` share runs-map fetch, disk-usage tally, dir-sizing (~60 LOC); `ARCHIVED_RETENTION_DAYS`/`COMPLETED_RETENTION_DAYS` are both 30 with full duplicate plumbing.
- **Seam:** one `classify(run, cutoffs)->category` per service called by both `cleanup`+`preview`; hoist shared scaffolding to a base.

### 4.4 Schedulers — `validate_cron` defined twice; `compute_next_fire` is a UTC subset
`validate_cron` byte-identical in `scheduler.py:380` and `recurrence.py:105` (CLI uses one, API uses the other — both live). `scheduler.compute_next_fire` is a UTC-only subset of `recurrence.compute_next_fire_in_tz`. *Note: a finder's "compute_next_fire is a strict superset, just delete" claim was downgraded — `scheduler.compute_next_fire` raises `ValueError` on invalid cron, a tested contract the tz version doesn't replicate; consolidation is a refactor, not a deletion.*
- **Seam:** make `recurrence.py` the single cron-utility module; `scheduler` re-exports.

### 4.5 Run-event broadcasting — 3rd hand-built serialization
`websocket.broadcast_run_update`/`broadcast_run_created` (82–162) hand-assemble a ~22-key subset of `RunResponse` (already drifted on `chain_step_name`). Separately, run lifecycle reaches the UI via **both** ~40 direct `ws_manager.broadcast_run_*` calls **and** the event-bus `websocket_broadcaster` subscriber — redundant double-broadcast paths.
- **Seam:** build ws payload from `RunResponse.model_validate(run).model_dump()`; pick one delivery path (bus *or* direct).

### 4.6 Budget enforcement — verbatim duplication core↔runner
`_enforce_agent_budget` (`core.py:352` ≡ `runner.py:88`) and `_enforce_workspace_budget` (`core.py:373` ≡ `runner.py:111`, 56 lines incl. identical 80%-headroom warning strings). Also `_touch_agent_last_active`, `_check_safety_guards` (reimplements `RateLimiter.can_make_call`).
- **Seam:** one canonical free-function per rule; Orchestrator methods delegate.

### 4.7 gh-CLI subprocess boilerplate — 7× in git_manager
Every `gh` call hand-rolls `create_subprocess_exec` + `communicate` + rc-check + inline `import json as json_module` (4× inline json import), while git calls go through `_run_git`. No `_run_gh` exists.
- **Seam:** add `_run_gh(cwd, *args, parse_json=False)` mirroring `_run_git`. (Also: merge-base+empty-tree logic duplicated across 6 methods; numstat→change_type ladder duplicated 2×; `except (FileNotFoundError, Exception)` is redundant — `FileNotFoundError ⊂ Exception`.)

### 4.8 chat_agent guard boilerplate
`GitManager(orchestrator.store)` + function-local import in **13** tools; run-lookup-or-error in **10**; project-name guard in **14**. Extract `resolve_run_or_error`/`resolve_project_or_error` + one module-level `GitManager`.

### 4.9 web/api lookup boilerplate
Run-or-404 inline **32×**, project-or-404 **24×** (despite existing `_resolve_task_ref_or_404`); `WorkspaceResponse` hand-serialized 4× (every other entity has a helper); `project_name = project_lookup.get(...)` + `broadcast_run_update` pair **22×**; `GitManager(store)` re-instantiated 8×. Add `_resolve_run_or_404`/`_resolve_project_or_404`/`_workspace_to_response`/`_broadcast_run`.

---

## 5. Outdated / legacy features & shims

| Feature | Status | Action |
|---|---|---|
| **MergeQueueService** (`merge_queue.py:29-164`) | **Dead at runtime** — instantiated only by tests; nothing enqueues/processes. CLI `gluon merge`, `/api/merge-queue`, web-ui `MergeQueuePage.tsx` all operate on store rows directly; `retry` resets to PENDING that no loop picks up. | Wire a processor loop **or** remove service + advertised surface. (Documented KEEP — see §10.) |
| **Advanced-git UI** (rebase/force-push/conflict-resolve/branch-rename) | GitManager methods + FastAPI routes + 13 typed api.ts clients exist; **no React component calls any**. UI's only "resolve conflicts" affordance prints manual git instructions / builds a Claude prompt. | Decide: build the UI or delete the 13 orphan clients + mutation-only GitManager methods (`force_push`, `check_force_push_needed`, `rebase_skip`, `rename_branch`, `change_base_branch`). |
| **Role-based TaskProfiles** (FIX/REVIEW/REFACTOR/RESEARCH) | `models.py:97-101,309-345` — never surfaced; UI/CLI only offer quick/standard/deep/planning. Reachable only via undocumented free-form `--profile` string. | Surface or collapse. |
| **CompletionDetector fallback heuristics** (`completion_detector.py:199-299`) | Effectively unreachable — Ralph mandates a RALPH_STATUS block every response; both `analyze()`/`should_exit()` early-return on it. ~100 LOC of keyword/no-work/confidence scaffolding for a degenerate case. | Shrink to "block missing → continue" rule. |
| **`get_auth_provider` legacy OIDC branch** (`auth.py:432-440`) | The `backend==OIDC` branch + explicit `backend` param are test-only in prod (all callers pass `None`, password-only). *Downgraded: the env-read/coercion the finder bundled in actually runs on every call — over-scoped.* | Collapse to `LocalAuthProvider(store)`; verify `GLUON_AUTH_BACKEND` not used externally first (§10). |
| **`formula run` CLI** (`cli.py:3409`) | **BROKEN** — `anyio.from_thread.run()` from a sync command raises `RuntimeError`. Documented 8× but non-functional. | Fix to `anyio.run(_run)`. |
| `EXTERNAL_TODO_PLAN.md` | Feb-2026 research doc (SDK 0.1.39, "Research Phase") for a feature that shipped; no inbound links; project now on SDK ≥0.2.87. | Archive/delete. |

**Debug `print()` left in production:** 9× `[RECOVERY]` in `api.py:1158-1231`, 1× `[AGENT]` in `agent.py:1509`. Remove.

---

## 6. Dead config / env / DB columns / dependencies

**DB columns (orphaned, carry no live data):**
- `execution_runs.queued_followup` / `queued_followup_at` (`store.py:380-382`) — superseded by `queued_messages`; in no INSERT/UPDATE/SELECT, no model field.
- `execution_runs.thread_id` (`store.py:167`) — write-only; written by `update_run`, round-tripped to model, **never read** (former `get_run_by_thread_id` consumer already removed). *Note: distinct from the live transport message-thread field.*
- `workspaces.scan_depth` (`store.py:108`) — persisted/round-tripped but `scan_for_projects()` is hardcoded single-level `iterdir()`; setting ≠1 has no effect. Not even surfaced in API/CLI/UI.
- `workers` / `jobs` tables — part of Cluster A.
- *Removal requires a destructive `ALTER TABLE DROP COLUMN` migration; leaving as inert legacy is acceptable.*

**Config / env:**
- `WebhookConfig.labels` (`models.py:1863`) — stored/round-tripped but **no `matches_labels` filter exists**; the create endpoint doesn't even accept it. Unimplemented filter masquerading as a feature.
- `force_refresh` params on `get_project_files` (`files.py:235`) — always default in prod (see §10).
- Transport capability booleans (Cluster H).

**Dependencies (all correct, comment/structure issues only):**
- `Dockerfile:139-141` hardcodes a **3rd copy** of the runtime dep list (hand-maintained fallback already missing `argon2-cffi/authlib/croniter/anthropic`); line 151's `.[all]` install supersedes it. Drop the `--no-deps` + fallback dance.
- `pyproject.toml all` extra restates `discord`+`web` with independent floors → use self-referential `all = ["gluon-agent[discord]", "gluon-agent[web]"]`.
- `anthropic[vertex]` comment wrong: installs `google-auth` (ADC), not `google-cloud-aiplatform`.
- `mcp`/`itsdangerous`/`aiohttp` are correct transitive CVE-floor pins — label consistently with the top-level floor block; add a "remove once upstream floors" review note.
- Duplicate `get_redis_url()` in `events/redis_transport.py:34` and `queue/redis_queue.py:33` — resolves itself if Cluster A is removed.

---

## 7. web-ui dead code & duplication

**`RunDetailDialog.tsx` (2,892) ≈ `RunDetailPage.tsx` (2,041) — copy-paste twins.** 22 identically-named handlers (`handleMerge`, `handleResume`, `handleCreatePr`, `loadCommits`, `handleResolveConflicts`, …), identical data-loading. Every behavior change must be made twice. **Highest-value frontend refactor:** extract shared tab bodies + data-loading hooks; make dialog/page thin shells, or drop one route.

**Confirmed-dead api.ts exports (~30 functions):**
- **Advanced-git cluster (13):** `detectConflicts, getConflictDiff, resolveConflict, startRebase, continueRebase, abortRebase, skipRebaseCommit, checkForcePushNeeded, forcePush, listBranches, renameBranch, changeBaseBranch, deleteBranch` (731–845) — zero component callers.
- **Image cluster (5):** `uploadImage, fetchImage, deleteImage, attachImageToRun, detachImageFromRun` (651–726) — superseded by `uploadAndAttachImage`.
- **Misc (12):** `createProject, fetchProject, updatePrStatus, fetchStatus, clearQueue, fetchFormula, validateFormula, fetchSchedule, fetchScheduleRuns, fetchMyLinks, fetchProjectGitStatus, refreshProjectGitStatus`.

**Orphan types (12, `types.ts`):** `ResumeRunRequest, RecoverRunRequest, SnoozeRunRequest, UpdateStatusRequest, ResolveConflictRequest, RebaseRequest, ForcePushRequest, RenameBranchRequest, ChangeBaseBranchRequest, CloneRepositoryRequest, GitSyncRequest, TodosUpdatedMessage` — defined, never referenced (api functions pass inline body objects).

**Other:** `POLL_FAST` constant (`polling.ts:17`, unused); `useCurrentUserProvider`/`CurrentUserContextProvider` exported but internal-only (drop `export`); `resumeSdkSession` (1115) POSTs `/api/sdk-sessions/{id}/resume` **which doesn't exist** — `SessionBrowserPage` shows a "Resume" button that always fails ("backend endpoint pending"); stale `TODO(stream-followup)` in `format.ts` (migration already complete).

---

## 8. Test & doc debt

**Test:**
- `tests/test_distributed.py` (~570 LOC) tests dead Cluster A.
- 6 files redeclare a `store` fixture byte-identical to conftest's (`test_auth, test_workspace_settings, test_event_subscribers, test_notifications, test_event_integration, test_todo_hooks`) — conftest comment literally says "replaces per-file duplicates." Delete the locals.
- Unused parametrized `llm_provider` fixture in `conftest.py:14-18` (no consumer).
- 4 per-provider test classes (~75 methods) re-implement assertions already parametrized by `TestProviderContract` — fold generics in, keep only provider-unique cases.
- **Coverage gap:** no `tests/test_api_formulas.py` — the 4 formula HTTP routes are untested at the boundary (this is why 2 dead endpoints went unnoticed). Add before deleting them.

**Doc drift:**
- `CLI-REFERENCE.md` claims "Complete CLI reference" but **omits ~18 command groups** (`provider, doctor, formula, queue, merge, witness, activity, schedule, agent, task, approvals, worktree, chain, settings, heartbeat, claude-sessions, …`) — several are headline README features. Either complete it or point to `gluon --help`.
- `chat_agent.py` SYSTEM_PROMPT recommends `sonnet`/`haiku` as defaults and `run_task` defaults `model='sonnet'`, contradicting the project default (opus-4.8) and "Haiku not on Bedrock." Steers runs onto wrong/unavailable models.
- Discord `models` command labels sonnet `(default)`; real default is opus-4.8.
- `redis_transport.publish()` docstring claims dual-channel catch-all publishing the code never does.
- README `80+ REST endpoints` undercounts (now ~161); `40+ MCP tools` accurate (41).

---

## 9. Prioritized roadmap

**Quick wins (do first):**
1. **Fix `formula run`** — `from_thread.run` → `anyio.run`. `[impact: med / effort: trivial / risk: low]` *(latent broken feature, not cleanup)*
2. **Wire `WorkQueueManager.mark_completed/mark_failed`** on run completion — stops RUNNING-state leak. `[high / low / low]` *(latent bug)*
3. **Delete debug prints** (`api.py` ×9, `agent.py` ×1). `[low / trivial / none]`
4. **Remove Cluster E–J leaf dead code** (websocket methods, agent/hook fields, image wrappers, transport wrappers, `search_commands`, `execute_simple`, singletons, `SCHEMA` constant, event constants, `STALLED_THRESHOLD`). ~600 LOC, individually trivial. `[med / low / very-low]`
5. **Delete confirmed web-ui dead exports + orphan types** (~30 functions, 12 types, `POLL_FAST`, stale TODO). `[med / low / very-low]`
6. **Drop store `in keys` guards** in `_row_to_run`/`update_run` (~70 LOC). `[low / low / low]`

**Medium (high leverage):**
7. **Decompose `web/api.py`** into `APIRouter` modules + `web/background.py`; add `_resolve_run_or_404`/`_resolve_project_or_404`/`_broadcast_run`/`_workspace_to_response` helpers (collapses ~56 lookup blocks + 22 broadcast pairs). `[high / med / low]`
8. **Extract `runner._run_task` seams** (`_run_blueprint_validation`, `_finalize_worktree` shared with ralph, `_stream_agent_messages`). `[high / med / med]`
9. **Cache chat_agent tools + derive `allowed_tools`** from decorators; split closures. `[med / med / low]`
10. **Add `_run_gh` helper** + dedupe git_manager merge-base/numstat/commit blocks. `[med / med / low]`
11. **Consolidate budget enforcement** (core↔runner verbatim dupes) into shared functions. `[med / low / low]`
12. **Remove the four dead HTTP routes** + dead frontend api.ts clients; add formula API tests first. `[med / low / med]`

**Larger refactors (need product/owner decisions):**
13. **Remove or formally gate Cluster A** (distributed queue, ~1,500 LOC) — reverse the KEEP-as-foundation decision or feature-flag it visibly. `[high / med / low-once-decided]`
14. **Decide MergeQueueService + Advanced-git UI fate** — wire processors/UI or delete the advertised-but-dead surfaces end-to-end. `[high / med / med]`
15. **`PollingService` base class** for the 6 keep-running engines + generic watcher. `[high / large / med]`
16. **Unify auto-resume** into one coordinator/counter/cap (fixes the cap-bypass bug). `[high / large / med]`
17. **Merge `RunDetailDialog`/`RunDetailPage`** into shared hooks + thin shells. `[high / large / med]`
18. **Witness recovery:** either wire `execute_action` (gated, with caps) or delete the recovery half (keep `classify`+`suggest_action`+broadcast). `[med / med / med]`
19. **Declarative store table-spec** to collapse ~35 `_row_to_*` blocks. `[med / large / med]`

---

## 10. Needs human judgment (uncertain) & refuted false-positives

**Uncertain — removable claim could not be fully confirmed:**

| Item | Why uncertain |
|---|---|
| **Cluster A (distributed queue), MergeQueueService, witness recovery** | Code is genuinely dead, but `docs/remediation-plan-2026-06-10.md` records **explicit owner decisions to KEEP** them as "foundation for a future distributed mode" / "not-yet-wired processor." Removal reverses documented decisions — needs owner sign-off, not autonomous deletion. The store CRUD is also flagged as "public API surface, possibly invoked dynamically." |
| **`witness.suggest_action`** | The finder bundled it into the dead-witness symbol set, but it is **live** (`classify()` calls it; result persisted + shown in UI/CLI). Do **not** remove. Same for `RecoveryAction` enum. |
| **`get_auth_provider` OIDC branch** | Only the `if backend==OIDC` branch + explicit `backend` param are test-only; the env-read + str-coercion the finder included **run on every prod call**. Over-scoped — narrow before removing. It's a documented public legacy helper reachable via `GLUON_AUTH_BACKEND=oidc`. |
| **`ResumeRunResponse.original_run_id/new_run_id`** | Dead on the Python side, but the finder's "frontend never references" is **wrong** — `types.ts:325-326` declares both. Removal must touch the TS interface too. |
| **`RalphStatusBlock.tasks_completed/files_modified/tests_status/recommendation`** | Dead in Python prod, but the **frontend `StreamingLogViewer.tsx` independently parses and renders all four** from the same RALPH_STATUS protocol. Removing breaks tests and leaves Python an incomplete mirror of a live cross-component contract. |
| **`force_refresh` on `get_project_files`** / **`get_setting` raw-SQL delete in `cli.py:3828`** | Production-unreachable but exercised by a live test / functional but breaks store abstraction. Judgment calls, not clean kills. |
| **Supervision HTTP trio** (`api.py:1510-1607`) | Zero in-repo callers but a **documented public/external authenticated API**; engine methods of the same name are live. Retire-an-endpoint decision. |

**Refuted (audit caught these false-positives — no action):**
- `cleanup.py` ↔ `session_cleanup.py` — **disjoint resources**, no shared logic. Do not merge.
- `image_storage.py` ↔ `files.py` — **correctly separate** (blob store vs source-file autocomplete). Do not merge.
- `compute_next_fire` "strict superset, just delete" — **wrong**: the UTC version raises `ValueError` on invalid cron (tested contract) the tz version doesn't replicate.
- `GLUON_REDIS_URL` / `redis` dependency — feed the **live** `events/redis_transport` event bus, **not** the dead `redis_queue`. Removing Cluster A does not touch the event bus.
- The `/api/webhooks` "Phase: Distributed Workers" route — a **stale label**; the handler uses `runner.submit()` (live TaskRunner), never the dead queue.

---

## Appendix A — Completeness critic (coverage gaps)

That confirms the key gaps. `redis_queue` is imported only by its own `__init__` and referenced in pyproject + 2 prior audit docs — likely orphaned. `cli.py` (5752 LOC) is only touched indirectly by 3 tests for narrow features. Stripe was a false positive (doc comment). I have a complete picture.

# Audit Coverage Gaps — Follow-up Pass Targets

The 33 finders mapped well to large modules, but the dimension-by-area framing left several seams. Concrete gaps:

## (a) Source modules with no clear owning area
- **`webhooks/` (github.py 456 LOC, base.py) — security-sensitive ingress, no named area.** The `/api/webhooks/github` route is HMAC-authenticated and **creates runs from external payloads** (`web/api.py:3607`). This is an unauthenticated-by-session attack surface that "web-api" and "auth" may both have skipped as "the other one's job." Follow-up: audit signature validation, replay protection, event-type allowlisting, and payload-driven run creation.
- **Resilience cluster: `circuit_breaker.py`, `rate_limiter.py`, `hard_caps.py`, `merge_queue.py`, `pr_monitor.py`, `resume_coordinator.py`, `completion_detector.py`, `supervisor_daemon.py`, `recurrence.py`, `witness.py`.** None of these names appear in the 33 areas. Some fold into "loop-subsystem"/"ralph"/"health-safety"/"scheduling", but the mapping is implicit — easy to fall between finders. `supervisor_daemon`/`recurrence`/`question_watcher`/`approval_watcher`/`task_scheduler` have **no dedicated test file**.
- **`blueprint.py` + `chain_executor.py` + `formula_executor.py` (multi-step orchestration engines).** "engines"/"formulas"/"dup-engines" plausibly cover these, but they form their own retry-loop subsystem distinct from ralph — worth confirming it wasn't conflated.

## (b) Under-explored dimensions (cross-cutting, invisible to per-module finders)
- **Error-handling debt: 267 `except Exception` and ~90 `except…: pass` swallow sites.** No finder targets this systemically. High-value: silent failures in a long-running autonomous agent are exactly where reliability bugs hide. Follow-up: enumerate swallowed-exception sites in `runner.py`, `core.py`, `git_manager.py`.
- **`type: ignore` debt: 86 suppressions, 54 in `store.py` alone, 22 in `web/api.py`.** "test-debt" was its own category (1 finding) but type-suppression debt wasn't a dimension. The store concentration suggests a row-mapping/typing pattern worth a targeted look.
- **Config sprawl: 90 `os.environ.get`/`getenv` sites across 10+ files** (api 17, cli 17, auth 13, runner 12, llm_provider 9). "dead-config-env" found unused vars, but **no finder assessed the lack of centralized config** — env reads are scattered, untyped, and duplicated. Follow-up: propose a single settings object.
- **DB migration scale: ~219 inline `ALTER/CREATE` statements in one `MIGRATIONS` list (store.py 6295 LOC).** "store"/"dead-db" treated the store as one unit. The migration list itself is a complexity/maintainability hotspot (idempotency, ordering, irreversibility) that deserves its own pass, plus the **45 `json.loads/dumps` JSON-column sites** (schema coupling not enforced by SQLite).
- **`cli.py` (5752 LOC) has no dedicated test file** — only 3 tests touch it indirectly via `CliRunner` for narrow features (budgets, claude-sessions, hard-caps). "cli" finder reviewed code but test-coverage of the largest user-facing surface is effectively zero. Worth flagging as test-debt.
- **WebSocket message contract (`web/websocket.py`, 18 broadcast/stream methods).** "web-models-ws" likely covered Pydantic models, but the **WS event-name ↔ frontend consumer contract** (e.g. `broadcast_witness_decision`, `stream_token_update`) is a drift risk between backend emit and `web-ui` handlers that neither "web-models-ws" nor "web-ui-dead" clearly owns. Follow-up: cross-check each broadcast event name against frontend listeners.

## (c) Suspiciously empty / likely-missed findings
- **`queue/redis_queue.py` (392 LOC) appears orphaned.** Referenced by **no other src module** — only its own `__init__`, `pyproject.toml`, and two *prior* audit docs (`code-quality-audit-2026-06-10.md`, `remediation-plan-2026-06-10.md`). The `events/redis_transport.py` bus *is* wired (core/runner/api); the **redis_queue is not**. Strong dead-module candidate the "events-queue" finder seems to have missed. Verify before deletion (could be CLI-string or dynamic import), but this should have surfaced in dead_code.
- **Concurrency correctness: 59 asyncio Lock/Semaphore/gather sites + subprocess trees in 12 modules, zero findings tagged.** With `GLUON_MAX_CONCURRENT_RUNS` semaphores, subprocess management in `runner.py`/`worktree.py`, and a documented OrbStack VM-lockup incident in memory, **async/concurrency correctness is a known pain area with no finder**. Follow-up: audit lock acquisition ordering, `create_subprocess` cleanup/zombie reaping, and semaphore release on exception paths.
- **Naive `datetime.now()` (3 sites, no tz)** in a UTC+8 context — minor, but timezone-correctness wasn't a dimension despite scheduling/recurrence/cron features.

**Highest-value follow-up:** (1) GitHub-webhook security pass, (2) confirm `redis_queue.py` orphan + remove, (3) swallowed-exception sweep of `runner.py`/`core.py`, (4) centralized-config + cli test-coverage gap.
