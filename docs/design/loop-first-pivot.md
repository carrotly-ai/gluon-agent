# Loop-First Pivot — Gluon as the Loop Environment (PROPOSITION)

Research synthesis + product proposition, 2026-07-06. Inputs: Notion loop-engineering
library (Cherny/Osmani/Steinberger, June–July 2026), Claude Agent SDK capability brief
(v0.2.110 / CLI 2.1.191), and a full survey of Gluon's orchestration primitives.
Companion to `agent-loops.md` (Phase 2, shipped) and `loop-engineering.md` (Phase 1, shipped).

## 1. Why the current loop feature underwhelms (diagnosis, not opinion)

The Phase-2 AgentLoop is mechanically sound (verified: gate authority, budgets,
stall detection, atomic under concurrency). It underwhelms because **the
architecture cannot express the vision**:

| The vision | What actually happens today | Root cause |
|---|---|---|
| "Fix all the bugs" — agent surveys the landscape, decomposes | Iteration 1 gets a prose nudge to "assess project state" | No survey/plan primitive; no issue/PR enumeration bridge (`loop_manager.py:41-49`) |
| Spawn parallel tasks where independent | Fan-out tasks execute **one at a time** | Per-project serialization: drain skips projects with an active run (`runner.py:2451-2457`); self-propel claims one (`runner.py:1904`) |
| Daisy-chain tasks where dependent | Impossible — only integer `priority` ordering | `work_queue` has **no dependency edges** (`store.py:445-459`) |
| Implementer → verifiers → fixer per task | One flat loop for the whole objective | Loop iterations are peers; no per-task verify loop |
| Bother the human only at handoffs | A paused loop is **silent**; you discover it by looking | No objective-level escalation (`loop_manager._pause` notifies no one) |
| "Everything is a loop" as the default way of working | Loops are 1 of ~7 competing modes | ralph vs AgentLoop vs chains vs 2 schedulers vs subagents vs merge-queue |

The feeling of "a slow serial chain of disconnected runs" is exactly what the
current substrate produces. The control plane is right; the **work-graph and the
experience** are missing.

## 2. What the research says (the frames that matter)

- **Cherny's orchestrator pattern** (the canonical loop-engineering shape): a
  top-level orchestrator kicks off N tasks (up to 100s); each task fans out
  **Implementer → two Verifiers → single Fixer**; loop-per-task until verifiers
  pass; orchestrator returns when all branches complete. *"You write the
  definition of 'done' once — the loop handles the rest."*
- **The harness thesis**: *"Build the environment, not the loop."* Claude Code
  ships the inner loop (context mgmt, tools, compaction, subagents). The moat is
  the environment: context, tools, permissions, workflows, memory, observability.
  Gluon should not compete with the vendor loop — it should be **the environment
  many loops live in**.
- **The autonomy ladder** (practice library): **L1 report-only → L2 assisted
  (propose, human approves) → L3 unattended within an allowlisted scope.** Trust
  is granted per loop and earned with evidence (acceptance-rate metrics — which
  Gluon already computes per loop).
- **Named loop products**: daily-triage, pr-babysitter, ci-sweeper,
  dependency-sweeper, issue-triage, changelog-drafter, post-merge-cleanup.
  These are **SKUs users recognize**, not features.
- **Observability** (CMUX): "an agent you can't see is an agent you can't
  improve" — the running fleet/graph must be *visible*; programmatic access is
  non-negotiable.
- **SDK facts** (v0.2.110): native task DAG **inside a session**
  (`TaskCreate` with `blocks`/`blockedBy`, auto-unblock; shared file-locked task
  lists under `~/.claude/tasks/{team}/`); agent teams still experimental
  (single lead, no nesting, no in-process resume); `/goal` evaluator loops;
  team-authority hooks (`TaskCreated`/`TaskCompleted` can veto with feedback);
  and **no native cross-session orchestration/scheduling/DAG — explicitly the
  orchestrator's job**. That last point is Gluon's charter, from the vendor.

## 3. The proposition

> **Gluon is the loop environment.** The user speaks *objectives*; Gluon runs
> *campaigns*. A campaign is a living graph of verified tasks; every task is a
> loop (implement → verify → fix) that may spawn more tasks; humans sit at
> defined trust boundaries, not in the inner loop.

"Everything is a loop" operationalized: a one-shot task is simply a campaign
whose graph has one node. Same entity, same lifecycle, same UI — no modes.

### The three layers

```
OBJECTIVE  "Clear all the PRs"        ← user speaks this
   │  survey → plan(graph) → approve(L1/2/3) → execute → report
CAMPAIGN   task graph (DAG)           ← Gluon owns this (the moat)
   │  ready-set dispatch · parallel via worktrees · budgets · escalation
TASK       implement → verify → fix   ← each node; vendor loop does the inner work
```

**Layer 1 — Objective (new).** A `campaign` starts with a **survey iteration**:
enumerate the landscape (gh issue list / gh pr list / failing tests / TODO scan
— the `gh` CLI is already in the container), and emit a **plan artifact**: a
structured task graph (nodes + `depends_on` edges + per-node verify commands +
an autonomy-level and budget proposal). At L2 the plan is a human checkpoint; at
L3 it just runs. The plan is data, not prose — it renders as the campaign UI.

**Layer 2 — Campaign graph (merge what exists).** ONE dependency-aware unit of
work, consolidating today's three: `work_queue` (executed, no deps) +
`TaskChain.depends_on` + ready-set dispatch (`chain_executor.py:234` — right
model, wrong executor) + `orchestrator_tasks` (checkout locks, hierarchy,
comments — orphaned). Concretely: **add `depends_on` edges + ready-set dispatch
to the work queue** and give the loop write-path (`loop_enqueue_task` →
`task_spawn(prompt, depends_on=[…], verify_cmd=…)`) the power to grow the graph
mid-flight. **Remove per-project serialization**: ready tasks run in parallel
worktrees (worktree-per-task default; the existing merge queue serializes
integration back to the branch — the collision-safety story already exists).

**Layer 3 — Task loop (consolidate).** Each node runs the Cherny cell:
implementer iterates until its *node-level* gate passes (this is Ralph's in-run
loop, kept), optional independent verifier(s) judge the claim (AgentLoop's I2,
kept), a fixer round consumes verifier feedback (gate-denial continuation,
kept). **Ralph and AgentLoop stop being two products** — one loop contract at
two scopes: in-run (node) and cross-run (campaign).

### Cross-cutting contracts

- **Autonomy ladder, not approval prompts.** Campaign-level `autonomy: L1|L2|L3`
  maps onto the existing ApprovalPolicy machinery + two new objective-level
  gates: plan-approval (L2) and merge/ship-approval (allowlist at L3). Per-loop
  acceptance-rate metrics (already computed) are the evidence for promotion.
- **Objective-level escalation.** Any campaign pause (budget, stall, blocked
  decision) emits a **decision card** to Telegram/Discord/web: what happened,
  what the agent recommends, one-tap choices (raise budget / approve / answer /
  abort). Wire `loop_manager._pause` → notifier (the transports and
  question-escalation plumbing already exist — `events/subscribers.py:197-280`).
  This is the "only bother me at handoffs" contract, made real.
- **The graph is the UI.** The campaign page shows the live DAG: node status,
  cost, verifier verdicts, worktree/PR links. Kanban stays for humans; campaigns
  are the agent-native view. (CMUX lesson: visibility *is* the improvement loop.)
- **Formulas become loop SKUs.** Ship the seven named patterns as builtin
  formulas with `kind: loop` + schedules: `issue-triage`, `pr-babysitter`,
  `ci-sweeper`, `dependency-sweeper`, `daily-triage`, `changelog-drafter`,
  `post-merge-cleanup` — each defaulting to L1 (report-only) with a documented
  promotion path. This is the onboarding story: install → pick a loop → watch it
  report → promote it.

### What consolidates (the honest refactor bill)

| Today (7 engines) | Loop-first |
|---|---|
| AgentLoop (cross-run) + Ralph (in-run) | One loop contract, two scopes |
| work_queue + TaskChain DAG + orchestrator_tasks | One campaign graph (deps + checkout + dispatch) |
| HeartbeatScheduler + TaskScheduleManager | One scheduler (campaign triggers) |
| 4+ dispatch seams (self-propel, drain, chain-advance, loop-advance) | One ready-set scheduler |
| Webhook→run one-shots | Webhook→campaign-node (same graph) |

SDK alignment: inside a session, adopt native `TaskCreate blocks/blockedBy`
(agents already have the tools); across sessions the campaign graph remains
Gluon's — the SDK explicitly leaves that to orchestrators.

## 4. Sequencing (each phase independently shippable)

**Phase A — unblock the felt experience (highest leverage, smallest diff)**
*STATUS: IMPLEMENTED 2026-07-07 (all five, + tests in
`tests/test_loop_graph_phase_a.py`; live-validated on the dev deployment).*
1. ✅ `depends_on` edges on `work_queue` (JSON array column, additive migration)
   + **ready-set dispatch**: `claim_work` only claims items whose deps are all
   COMPLETED (fail-closed on dangling refs). Cycle-free by construction (an
   item can only reference already-existing items). Failed/cancelled deps
   **cascade-cancel** dependents to a fixpoint (`cancel_dead_loop_items`) —
   invoked on the stall path and on resume, closing the silent-deadlock edge.
2. ✅ Per-project serialization removed for worktree loops:
   `claim_work(parallel_only=True)` claims only worktree-loop items; drain +
   self-propel fan out within a project up to
   `GLUON_MAX_PARALLEL_RUNS_PER_PROJECT` (default 3), global cap still binds.
   Non-worktree work keeps historical serialization.
3. ✅ `loop_enqueue_task` grows `depends_on` + per-task `verify_cmd`. Task-level
   gates run at run-completion (item looked up via run.initiator); failure
   spawns a targeted `[TASK GATE FAILED]` fix task (same gate, priority ahead
   of fan-out), demotes any loop-completion claim from that iteration, and
   respects budgets (no unbounded fix cycles).
4. ✅ Survey seed: iteration 1 is the SURVEYOR — survey the landscape (incl.
   `gh issue list` / `gh pr list`), decompose into a dependency-ordered task
   graph, degenerate single-task objectives may complete directly.
5. ✅ Loop pause/complete/cancel → `loop.paused|completed|cancelled` events
   (worker → Redis → server bus) → `notify_loop_event` decision card in
   project channels (same path as question escalation).

*Phase A alone delivers: "fix all the bugs" → survey → parallel verified tasks →
escalation on pause. The vision becomes visible in the product.*

**Phase B — the campaign experience**
*STATUS: IMPLEMENTED 2026-07-07 (B1–B5, + tests in
`tests/test_loop_pivot_phase_b.py`; loop suites 66 green, ruff + mypy clean).*
1. ✅ **Worktree merge-back** (`src/gluon/loop_integration.py`): a completed
   loop-task's branch is merged into the project's source branch under a
   cross-process `fcntl` lock, so siblings and later verification build on
   integrated state — closing the Phase-A integration gap. Typed statuses
   (`merged`/`up_to_date`/`no_changes`/`conflict`/`branch_moved`/`skipped`/
   `error`); never raises into the advancement seam. Conflicts spawn an agent
   resolution task; the checkout is left pristine. Wired into
   `on_run_completed` after the task gate, before the plan checkpoint.
2. ✅ **Autonomy ladder** (L1 report-only / L2 assisted / L3 unattended): L1/L2
   loops PAUSE after the surveyor authors the plan (plan-approval trust
   boundary); `gluon loop resume` executes. L3 runs straight through. `autonomy`
   validated at `create_loop` (every entry path) and plumbed through CLI
   (`--autonomy`), formulas (templated), web API, and the store.
3. ✅ **Loop SKU library** (`kind: loop`, `use_worktree`, templated autonomy):
   `issue-triage`, `pr-babysitter`, `ci-sweeper`, `daily-triage`,
   `dependency-sweeper`, `post-merge-cleanup`, `changelog-drafter` — the
   practice library of ready-to-run campaigns (`gluon formula run <sku> <proj>`).
4. ✅ **Campaign task graph UI**: `GET /api/loops/{id}` returns work-graph nodes;
   LoopsPage renders the campaign graph (deps + gate markers), an autonomy
   selector in the create dialog, and an autonomy detail stat.

*Deferred to Phase C: campaign-level (cross-loop) allowlists and schedule
presets; the per-loop `claim_work` budget guard already bounds token burn.*

**Phase C — consolidation (debt paydown)**
- Ralph→node-loop merge; chains→campaign migration; scheduler unification;
  webhook→campaign-node; retire duplicate completion detectors.

### Live-validation results (2026-07-07, dev deployment)

- **Dependency chain (serial):** objective "three-stage pipeline in order" →
  surveyor authored exactly 3 tasks chained via `depends_on`; ready-set
  dispatch held each stage until its dep completed; files prove order
  (A → AB → ABC); loop COMPLETED via gate ("objective met; verify_cmd
  passed"), 4 iterations, $0.39.
- **Parallel fan-out (worktree):** objective "three independent poems" →
  **3 simultaneous running iterations in one project** (per-project cap
  saturated) — impossible before Phase A's serialization removal.
- **Empirical Phase-B motivation:** the parallel test surfaced the integration
  gap live — each task's output lands in its own worktree, so a later
  verification task (fresh worktree) cannot see siblings' work. Parallel
  fan-out therefore needs merge-queue integration *before* objective
  verification (already scheduled as Phase B; budgets bound the behavior
  meanwhile). Stall-recovery also proved itself: a surveyor that under-
  delivered was corrected by harness continuations within 2 iterations.

## 5. Risks / honest caveats

- **Token burn scales with parallelism** (practice library's #1 caveat): the
  campaign budget must be a *graph-wide* ceiling enforced at dispatch (the
  hardened `claim_work` guard already does this per loop — extend to campaigns).
- **Verification is still the bottleneck**: L3 without strong per-node gates =
  unattended mistakes at scale. The ladder exists precisely so trust follows
  evidence.
- **Agent teams remain experimental** (single lead, no nesting, resume gaps) —
  use for in-node parallelism only; campaign-level parallelism stays on
  worktrees + processes (durable, resumable, observable).
- **Comprehension debt**: L1-first defaults + the report artifact keep the human
  reading what ships.

## 6. Decision asked of the product owner

1. Adopt "campaign" (objective → graph → verified tasks) as THE core entity?
2. Green-light Phase A now (5 items, all additive)?
3. Naming: keep "loop" user-facing, or "campaign/mission" for the objective
   layer with "loop" reserved for the per-task cell?
