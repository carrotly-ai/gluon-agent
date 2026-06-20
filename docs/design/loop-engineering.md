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
- [ ] Step 1 — I4 warn-only
- [ ] Step 2 — I1 objective gate
- [ ] Step 3 — completion_detector demotion

## Step 1 — I4 warn-only (DESIGN — to validate next iteration)
Add a `verify_cmd: str | None` field to `ExecutionRun` (additive migration in store.py
MIGRATIONS) + plumb it through create/run (CLI flag + API create body), NOT enforced.
Add a readiness classifier: a run is **gated** iff `verify_cmd` is set, else **gateless**.
Surface the verdict (run record field / list-view chip / a log line at ralph start). No
exit-criteria change. Validate against: ExecutionRun fields, create_run signature,
the MIGRATIONS list pattern, RunResponse, and the ralph start path in runner.py.

## NEXT STEPS
Implement Step 1 (I4 warn-only): ground the `verify_cmd` field + migration + readiness
classifier against the real create_run / runner ralph-start / RunResponse, then implement
additively (field default None = today's behavior), prove non-regression, gate, commit.
