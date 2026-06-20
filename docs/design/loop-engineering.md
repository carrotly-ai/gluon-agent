# Loop Engineering — Design & Progress (STATE.md)

Living design + progress doc for the loop-engineering work (single stacked PR on
`feat/loop-engineering`). Each iteration: ground the next step's design in the
real code, validate it, implement, gate on `ruff + mypy + pytest`, commit.

## Why
Gluon's `--ralph` autonomous loop decides "done" purely from **agent self-report**
(`completion_detector.py`: `RALPH_STATUS EXIT_SIGNAL`, self-marked TODOs,
consecutive "done" signals, a test-saturation heuristic). None runs an objective
test/lint/build gate → the "Ralph Wiggum trap" (agent declares done early, loop
exits on half-done work, keeps spending). Consensus from a 4-way design debate:
add **objective gates where tasks are gateable**, **measure cost-per-accepted-change**,
and **degrade gracefully (cap → draft PR → handoff) where they aren't** — without
breaking the large share of Gluon's work (research/docs/review) that has no gate.

## Plan (ordered; every step OPT-IN & ADDITIVE — default behavior unchanged)
- **Step 0 — I5 metric** (zero behavior change): cost-per-accepted-change + acceptance
  rate, broken down by gateability (work `kind`). Baseline to prove anything works.
- **Step 1 — I4 warn-only** (zero behavior change): `verify_cmd` config surface (field +
  plumbing, not enforced) + a per-run readiness classifier ("gated"/"gateless").
- **Step 2 — I1 objective gate** (opt-in): when `verify_cmd` is set, ralph runs it in a
  clean checkout with a timeout and exits only on exit-0; demote `RALPH_STATUS` to a
  progress hint. Build as a small **reusable contract** (trigger → run → gate → state).
  Gateless runs degrade gracefully: cap → draft PR → handoff (never refuse / never loop forever).
- **Step 3 — completion_detector demotion** (not deletion): gate is authority when one
  exists; otherwise keep current behavior. Keep `witness.py`.
- **Future (documented, not built here):** I6 security-in-gate · I2 independent verifier
  subagent · S2 unify the ~7 keep-running engines onto the reusable contract · S3 revisit `witness.py`.

## Grounded facts (verified against the code)
- `ExecutionRun` (models.py): `cost_usd`, `pr_number`, `pr_url`, `pr_status`
  ('open'/'merged'/'closed'/'draft'), `ci_status`, `ralph_enabled`, `max_cost_usd`,
  and `kind` (research/build/docs/bug/review/chore).
- `kind` is auto-detected from the prompt by `auto_detect_kind()` (runner.py, regex;
  default `build`); user-overridable via `PATCH /api/runs/{id}`.
- Usage today: `GET /api/usage/summary` → `store.get_usage_summary()` →
  `UsageSummaryResponse`; `GET /api/usage/by-project`.

---

## Step 0 — I5 metric (DETAILED DESIGN — validated)

**Definitions**
- *accepted change* = run with `pr_status == 'merged'`.
- *PR-producing run* = run with `pr_number IS NOT NULL`.
- *acceptance_rate* = accepted / PR-producing (0.0 if none).
- *cost_per_accepted_usd* = total cost / accepted (null if 0 accepted).
- *gateability* (proxy from `kind`): **gateable** = {build, bug, chore} (code-producing,
  objectively verifiable); **gateless** = {research, docs, review}. `kind` NULL → gateable
  (matches `auto_detect_kind` default `build`). Rationale: gateability here is a coarse
  proxy for "could an objective test/lint/build verify done"; Step 1 refines per-run
  "gated" to mean *verify_cmd is actually set*.

**Surface (all additive — nothing existing changes)**
- `models.py`: `GATEABLE_KINDS` frozenset + `is_gateable_kind(kind) -> bool`.
- `store.py`: new `get_loop_effectiveness() -> dict` (single pass over runs).
- `web/models.py`: `GateabilityBucket` + `LoopEffectivenessResponse`.
- `web/api.py`: new `GET /api/usage/effectiveness`.
- Tests: `tests/test_loop_effectiveness.py` — helper, store method, endpoint; assert
  existing `get_usage_summary` is untouched.

**Opt-in / non-breaking:** brand-new method/model/route; zero change to existing behavior.

## Progress
- [x] Step 0 — I5 metric — backend (PR #154): `is_gateable_kind`, `store.get_loop_effectiveness`,
  `GET /api/usage/effectiveness`, 6 tests. Additive + read-only; gate green on touched files.
- [x] Step 0 — I5 metric — Usage-page surface (frontend): `LoopEffectiveness` types,
  `fetchLoopEffectiveness`, "Loop Effectiveness" panel (gateable vs gateless), hidden until
  there are runs, graceful `.catch → null`. biome + build green. (Runtime screenshot pending a
  container rebuild from this branch — the running image predates the backend endpoint.)
- [x] Step 1 — I4 warn-only (FULLY DONE):
  - core: `ExecutionRun.verify_cmd` + `run_readiness`; additive migration; `create_run`
    persistence + `_row_to_run`; `RunResponse.verify_cmd`/`readiness`; ralph-start log line.
  - setters: `gluon run --verify-cmd` + `CreateRunRequest.verify_cmd` → `runner.submit` → `create_run`.
  - tests/test_verify_cmd_readiness.py (helper, persistence, API exposure, API setter forward, non-regression). Full suite 2303 passed.
- [x] Step 2 — I1 objective gate: `gluon/gate.py` (`run_gate`) + `RalphManager._apply_objective_gate`
  (gate is authority for gated runs; self-report demoted to a hint; failure fed back into the next
  prompt) + 9 tests. Full suite 2312 passed.
- [x] Step 3 — completion_detector demotion: SUBSUMED by Step 2's `_apply_objective_gate` seam —
  for gated runs the detector's signals are advisory (gate decides); gateless unchanged; detector +
  witness.py kept. Documented in completion_detector.py's docstring; covered by test_objective_gate.

## DONE — Steps 0–3 + item A complete.
- [x] **Item A — draft-PR handoff** (gluon-managed): a gated run that exhausts caps without the gate
  passing flags `run.metadata["gate_not_passed"]` (ralph_manager `_flag_gate_outcome`); the runner
  drafts the handoff PR + comments — converting the agent's PR (`convert_pr_to_draft` = `gh pr ready
  --undo`) or creating the rescue PR with `--draft`. Gateless unchanged. +4 tests; full suite 2316 passed.

### Future work (documented, not built):
- I6 security-in-gate · I2 independent-verifier subagent · S2 unify the ~7 keep-running engines onto
  the reusable gate/loop contract · S3 revisit witness.py once I5 cost-per-accepted data judges it.

## Step 2 — I1 objective gate (DESIGN — to validate next iteration; design-heaviest)
When `run.verify_cmd` is set, the ralph loop must treat `verify_cmd` exit-0 as the
authoritative "done", overriding self-report. Plan:
1. A small reusable helper (e.g. `gluon/gate.py`) `run_gate(cmd, cwd, timeout) -> (passed, output)`
   that runs the command (subprocess, timeout) and returns exit-0 == passed.
2. In the ralph loop body (runner._run_ralph_loop / ralph_manager), after each iteration:
   if gated, run the gate in a CLEAN checkout of the run's worktree/branch (validate how the
   worktree is created — git_manager); exit the loop only when the gate passes. RALPH_STATUS
   EXIT_SIGNAL becomes a *progress hint* (logged), not the exit authority, when gated.
3. Gateless (no verify_cmd): unchanged today, but ensure the bound exists — cap iterations/cost
   (max_loops/max_cost already exist) → on exhaustion open a DRAFT PR + hand off (validate the
   PR-creation path in git_manager; "draft" flag). Never refuse / never infinite-loop.
VALIDATE against: completion_detector.py (where EXIT_SIGNAL is treated as authority — the
demotion seam), the ralph loop body (where it decides to continue/stop + iteration accounting),
and git_manager (worktree checkout + `gh pr create --draft`). **If the clean-checkout or
draft-handoff mechanics require a big refactor or are genuinely ambiguous, STOP and escalate
options in the PR rather than guessing** (per guardrails).

### VALIDATED (grounded against the real code) — it's a clean additive change, NOT a refactor
- **Demotion seam** = `RalphManager.execute_loop()` (ralph_manager.py): after
  `should_exit, _ = completion_detector.should_exit(...)` it sets status=REVIEW + break.
  `self.working_dir` is the run's worktree (the agent's changes are on disk there) — running
  `verify_cmd` as a fresh subprocess in it IS the "clean checkout" (no new git checkout needed).
- **Plan**:
  1. `gluon/gate.py`: `run_gate(cmd, cwd, timeout=GLUON_GATE_TIMEOUT_SECS|600) -> GateResult(passed, exit_code, output)`. exit-0 == passed; timeout → not passed. Reusable.
  2. `RalphManager._apply_objective_gate(should_exit, exit_reason)`: only when `should_exit and run.verify_cmd`, run the gate; if passed → exit (reason + "verify_cmd passed"); if failed → `should_exit=False` (self-report is demoted to a hint; the gate is authority) + stash `self._last_gate_failure`. Gateless/no-verify_cmd → returns input unchanged (NON-REGRESSION is trivial; gate not run). `execute_loop` calls it in one line after `should_exit`.
  3. Feedback: `_build_loop_prompt` injects `self._last_gate_failure` so the next iteration FIXES the failure instead of re-declaring done (evaluator-optimizer). max_loops still bounds it.
- **Gateless graceful degrade** ALREADY exists: max_loops / cost-cap → status=REVIEW → `_run_ralph_loop` auto-creates a PR. So "never loop forever / handoff" is satisfied today.
- **SCOPED OUT this step (noted, not guessed):** marking the handoff PR as a *draft* when a gated
  run exhausts max_loops without the gate passing. The PR is normally **agent-created** (via the
  REQUIRED-PR instructions), so forcing `--draft` needs a decision on agent-vs-gluon PR creation —
  recorded as a Step-2 follow-up / future item rather than guessed.

## Step 1 — I4 warn-only (DESIGN — to validate next iteration)
Add a `verify_cmd: str | None` field to `ExecutionRun` (additive migration in store.py
MIGRATIONS) + plumb it through create/run (CLI flag + API create body), NOT enforced.
Add a readiness classifier: a run is **gated** iff `verify_cmd` is set, else **gateless**.
Surface the verdict (run record field / list-view chip / a log line at ralph start). No
exit-criteria change. Validate against: ExecutionRun fields, create_run signature,
the MIGRATIONS list pattern, RunResponse, and the ralph start path in runner.py.

## NEXT STEPS
None — Steps 0–3 are implemented, tested (full suite green except the pre-existing environmental
test_doctor), and committed to PR #154. The loop is complete; PR is ready for review (do not merge
without review). Future items above are deliberately deferred.
