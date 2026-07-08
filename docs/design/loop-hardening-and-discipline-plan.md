# Loop hardening & discipline — implementation plan (Phases D–F)

*A single sequenced plan to extend Gluon's loop engine with the ideas from
three sources — the 2026-07-07 session audit, [looper-analysis.md](looper-analysis.md),
and [loop-engineering-analysis.md](loop-engineering-analysis.md) — without
diluting the recentered vision.*

Companion to [loop-first-pivot.md](loop-first-pivot.md) (Phases A–C, shipped on
`feat/loop-first-pivot` / PR #172).

---

## 0. The vision guardrail (read first)

Every item below is tested against one sentence, and anything that fails it is
cut:

> **The human speaks objectives; the harness owns authority (verification,
> budgets, stopping, safety); the agent authors the work.**

Concretely, that means three non-negotiables that shape the whole plan:

1. **Enforcement lives in the harness, never in a prompt.** The other two
   projects encode policy as instructions an agent is *asked* to follow
   (`loop-constraints.md` is "binding" only by assertion; `loop-budget` is a
   skill the model runs). We adopt their *policy surface* but every rule that
   is mechanically checkable is enforced in code — `claim_work`, merge-back,
   the completion handshake — with the prompt copy as defense-in-depth, not the
   control.
2. **No new orchestration engine, no new core entity.** Everything extends the
   existing `AgentLoop` + work-queue + `LoopManager` seams. We are hardening
   and instrumenting the runtime, not rebuilding it. Looper's *compiler* and
   loop-engineering's *methodology hub* are deliberately **not** ported — those
   are other people's layers; we consume their artifacts, we don't reimplement
   them.
3. **Correctness before capability.** Phase D (the audit fixes + the test
   harness that proves them) ships before any new feature. We do not build the
   operator surface on top of a runtime with 4 known High-severity concurrency
   bugs.

What this plan explicitly rejects, to protect focus: building a Looper-style
design wizard, a general workflow DSL, a multi-vendor model marketplace, or a
public methodology site. Those dilute. The three phases below only make
*loop-based automation* more correct, more trustworthy, and more operable.

---

## Phase D — Correctness & the conformance harness (blocking, no new features)

**Why first:** the audit found four High-severity defects, all in the
parallel-worktree path — the flagship feature. They passed 2,398 tests because
nothing exercises cross-process concurrency or high work-item volume. Fixing
the bugs and building the harness that proves the fixes is one unit of work:
**the harness is how we know the fixes hold**, and it is itself the single
highest-leverage idea from Looper (conformance-on-fake-models).

### D1 — Fake-agent conformance harness *(Looper takeaway #1)*
A deterministic test harness that drives the **real** `LoopManager`,
`claim_work`, `enqueue_loop_task_atomic`, and `integrate_run_branch` with
**fake workers** — no Claude calls — including genuine multi-process scenarios
(`multiprocessing`, real SQLite file, real git repos/worktrees). Fixtures are
canned worker outcomes (completes / fails / enqueues N tasks / commits to
branch / conflicts). This is Looper's build-order lesson verbatim: *"freeze the
artifact contract with dummy host/judge scripts... prove loop control,
max_revisions, resume, and verdict parsing without paying model costs. Keep
them as test fixtures."*
- New: `tests/conformance/` with a `FakeWorker` harness and a scenario runner.
- Scenarios (each an audit finding or a core invariant): concurrent merge-back
  of two branches into one checkout; N parallel siblings completing while one
  runs (stall detection); item finalize/dispatch ordering vs. `depends_on`;
  mark-completed on an item past the 20-row window; budget ceiling under
  parallel fan-out; watch reseed bounded by budget; verifier-marker forgery
  rejection; terminal-state invariant ("no item may be left `claimed`/`running`
  after its run is terminal").
- Wire into CI as a required gate.
- *Deliverable proof:* every D2 fix lands with the scenario that was red before
  it and green after.

### D2 — The audit High fixes (one commit series, each proved by a D1 scenario)
- **D2.1 Merge-back lock** (`loop_integration.py:65`, audit #1) — replace
  `abs(hash(str(project_path)))` with `hashlib.sha256(...).hexdigest()[:16]`.
  Cross-process determinism; the lock actually excludes.
- **D2.2 Work-item transitions** (`work_queue.py` mark_*, audit #2) — mark by
  id via a targeted `UPDATE ... WHERE id=?` (or `store.get_work_item` +
  `update_work_item`), never the bounded `list_work_items()` scan. Fixes the
  silent-miss that empirically orphaned 6 items in `claimed`. Add a reaper for
  stuck `RUNNING` (today `release_stale_work_claims` only covers `CLAIMED`).
- **D2.3 Dependency-gate ordering** (`runner.py:1898` vs `:1912`, audit #3) —
  integrate the branch *before* the item is marked `COMPLETED`, so a dependent
  can never be claimed against un-integrated source. (Move `_finalize_queue_item`
  after `on_run_completed`, or gate finalize on integration success.)
- **D2.4 Stall detection counts in-flight siblings** (`loop_manager.py:417`,
  audit #4) — count `RUNNING`/`CLAIMED` loop items (or active runs carrying
  `loop_id`) as "not stalled" so a healthy parallel loop isn't false-paused or
  fed spurious continuations.
- **D2.5 Watch gate off the event loop** (`loop_manager.py:610`, audit #5) —
  `await asyncio.to_thread(run_gate, ...)`; make `_watch_reseed` async.

### D3 — Medium fixes that ride the same series
- Budget "hard ceiling" comment corrected + the soft-overrun bounded and
  documented (audit #6); `_row_to_work_queue_item` gets the `row.keys()` guard
  its loop-row sibling has (audit #4-low); integration `error`/`branch_moved`
  gets a recovery path, not just a log line (audit #5-low); detached-HEAD /
  unset `source_branch` handled (audit #8).

**Exit criteria for Phase D:** all audit High + Medium closed, each with a
red→green conformance scenario; CI runs the harness; full suite green.
**No user-visible feature ships in this phase.**

---

## Phase E — Trustworthy verification (the "should it stop?" layer)

**Theme:** make the harness's judgment as rigorous as its plumbing. This is the
Looper verification-typing work fused with loop-engineering's default-REJECT
verifier. It directly attacks the *verifier theater* failure mode both external
projects name — which applies verbatim to our same-family agent-verifier.

### E1 — Structured, fail-closed verifier verdicts *(Looper #3, loop-eng #5)*
The independent verifier today signals behaviorally (calls `loop_complete` or
enqueues fixes). Replace with a **structured verdict**: require fenced JSON
`{verdict: pass|revise, blocking_issues[], confidence}`. Parse **fail-closed** —
malformed or unparseable output → `revise` (completion denied), **never**
`pass`. Store the verdict on the loop for the UI/metrics. Adopt default-REJECT
framing ("find reasons to reject; do not trust the implementer's claim — run
the checks yourself") in `_VERIFICATION_PROMPT_TEMPLATE`.
- Touches: `_VERIFICATION_PROMPT_TEMPLATE`, `_resolve_completion_request`, a
  small verdict parser (its own conformance scenario in D1), a `loop_verdicts`
  record or JSON column.

### E2 — `ESCALATE_HUMAN` as a first-class verdict *(loop-eng #5)*
A verifier (or a fix cycle at its attempt cap) may escalate: loop → `PAUSED`
with reason "escalated: <why>" → existing `notify_loop_event` decision card.
Generalizes the L1/L2 plan pause to any point the harness can't safely proceed.

### E3 — Typed verification criteria *(Looper #4)*
Grow the single `verify_cmd` into a small typed list on the loop/task:
`programmatic` (argv + expected exit — what we have), `judge` (a rubric + model,
so the verifier judges against explicit criteria rather than the raw
objective), `human` (a checkpoint criterion, generalizing plan approval). Keep
`verify_cmd` as sugar for a one-element programmatic list — no migration pain.
- Touches: `AgentLoop`/work-item schema (a `criteria` JSON column), the gate
  runner, the verifier prompt, and formula YAML (§F3 metadata dovetails).

### E4 — Cross-family verifier option + same-family lint *(Looper #5)*
`--verifier-model` (and `agent_verifier_model` on the loop) accepting any
configured model, **including a local Ollama model** (`qwen3:14b` on `hwi` is a
free, private cross-family judge). Warn when verifier family == generator family
(the exact blind spot the council rubric targets). Enforcement is our existing
per-run model routing — this is one field + one dispatch branch, reusing the
Phase C `resolve_loop_iteration_model` seam.

### E5 — Attempt-ledger injection *(loop-eng #3)*
A deterministic (no-LLM) digest of prior attempts — built from the DB rows we
already have (iterations, outcomes, gate-failure output, cost) — injected into
continuation, fix, and verifier prompts, pruned to a recent window. This is
loop-engineering's `loop-context --inject`, and it fixes a real hole the audit
implied: our harness fix tasks embed iteration numbers to dodge dedup, so a loop
failing the **same way** repeatedly never trips stall — it just burns budget.
Pair with **failure-signature stall detection** (hash the gate-failure output;
same signature N consecutive iterations → pause), complementing D2.4's
emptiness-based stall.

**Exit criteria for Phase E:** the verifier's verdict is structured,
fail-closed, escalatable, optionally cross-family, and informed by attempt
history; repeat-failure loops stop on signature, not just emptiness. Each piece
has a conformance scenario (fake judge emitting malformed JSON → denied; same
failure ×N → paused).

---

## Phase F — The operator surface (the "run a fleet safely" layer)

**Theme:** Gluon has world-class plumbing behind an ops surface that does not
yet exist. This phase builds it — the thing loop-engineering's 5.5k-star
audience actually lives in — but with **teeth** (harness-enforced), which is our
differentiator over their prompt-level conventions. Ordered by leverage; F1–F3
are small and high-impact.

### F1 — Repo-local binding constraints *(loop-eng #1, Looper #7, audit #9)*
Support `.gluon/constraints.md` (fallback `loop-constraints.md`) in the target
project. Two layers, matching the loop-engineering model but enforced:
- **Prompt layer:** the constraints text is injected verbatim into every
  iteration prompt (seed, task, continuation, verifier) — the agent sees the
  rules.
- **Mechanical layer (the teeth):** a parsed **path denylist** is enforced in
  the harness — merge-back **refuses to integrate** a branch that touches a
  denylisted path (returns a typed `denylist_violation` → escalation task), and
  the completion gate/verifier checks touched paths. The default denylist
  (`.env*`, `secrets/**`, `auth/`, `payments/`, `credentials/`, `**/*.key`)
  applies even with no file present — which **also closes audit finding #9**
  (merge-back's `git add -A` currently can commit `.env`).
- Touches: a constraints loader, prompt-template injection, a `loop_integration`
  denylist check, a redaction glob in the auto-commit path.

### F2 — Global kill switch *(loop-eng #2)*
`gluon loop pause --all [--project X]`, backed by a flag checked as one guard
clause in `claim_work` (dispatch stops fleet-wide instantly), plus a web-UI
toggle and a Telegram/Discord command. Their `loop-pause-all` label — but it
actually halts dispatch in the harness rather than asking a skill to exit.
Resume is explicit. This is the missing "stop everything now" an operator needs
the first time a fleet misbehaves.

### F3 — Budget degradation tier *(loop-eng #4)*
At a configurable fraction of `max_cost_usd` (default 80%), the harness stops
dispatching act-mode fan-out and drops the loop to **report-only**: emit a
surveyor-style status summary, then pause. Degrade before dying — the operator
gets a report and a resumable loop instead of a silent stop at 100%. Enforced at
the `claim_work` budget clause (already the seam for cost caps).

### F4 — SKU cost/risk metadata *(loop-eng #6)*
Port `registry.yaml`'s fields into our formula schema: `cadence`, `risk`,
`human_gates`, `week_one_autonomy`, and a cost model
(`tokens_noop`/`report`/`action`, `suggested_daily_cap`). Powers: (a) a
`gluon loop cost <formula>` estimator, (b) better create-time defaults
(week-one autonomy, suggested caps), (c) cost-anomaly alarming (a loop far over
its pattern's expected spend is a signal). Pure additive YAML + a reader.

### F5 — Run-outcome telemetry *(loop-eng #10)*
Add loop-engineering's run-log fields — `items_found`, `actions_taken`,
`escalations`, `outcome` (`no-op|report-only|fix-proposed|escalated`) — to loop
iteration records and the `get_agent_loop_metrics` endpoint. Near-free given the
schema; turns per-run cost rows into an operational "is this loop worth its
spend" view alongside the existing acceptance-rate metric.

### F6 — Earned-autonomy promotion *(loop-eng #7)*
Use existing `get_agent_loop_metrics` (acceptance rate, cost-per-accepted) to
surface a **suggestion**, never an automatic escalation: "formula X has run N
times at L2 with Y% acceptance — eligible for L3." Human approves; autonomy
becomes a track record, not just a create-time flag. UI badge + API field; zero
new enforcement (the human is the gate).

### F7 — Repo-visible state projection *(loop-eng #9)*
Optionally write/refresh a generated, read-only digest into the project
(`.gluon/LOOP-STATE.md`) or pin it to the loop's channel: objective, status,
recent runs, pending graph, escalations. **DB stays authoritative; the file is a
view** — the inverse of loop-engineering's file-as-authority model, keeping our
correctness while gaining their legibility ("inspect without the UI").

### F8 — Cross-loop coordination *(loop-eng #8)*
An `acting_on` target registry (loop_id → PR/issue/path keys, written at task
dispatch) checked before claiming work that names the same target, plus loop
priority classes for contention (their CI-Sweeper > PR-Babysitter > … order).
Closes a real gap: today two Gluon loops can act on the same PR unaware. This is
the largest F item — schedule it last and only if multi-loop-per-project is a
real usage pattern (it isn't yet), otherwise defer.

### F9 — Formula lint + docs *(Looper #6, loop-eng #11–12)*
`gluon formula lint` porting the anti-pattern checks (all-vibe verification =
gateless + `agent_verifier: false`; missing caps; unreachable criteria;
same-family verifier; unresolved placeholders) over our formula YAML — several
shipped SKUs are "all-vibe" by this bar and should be flagged. Adopt the
failure-mode vocabulary (*verifier theater*, *state rot*, *comprehension debt*)
in `CLAUDE.md`/docs, and map each external failure mode to either a conformance
scenario or an operator doc section.

**Exit criteria for Phase F:** an operator can set binding constraints, halt a
fleet instantly, get a degradation report before a hard stop, see per-loop cost
expectations and outcomes, promote autonomy on evidence, and read loop state
from the repo — all enforced by the harness where checkable.

---

## Sequencing, sizing, and dependencies

| Phase | Items | Size | Ships when | Gated by |
|---|---|---|---|---|
| **D** | D1 harness, D2 High fixes, D3 Medium | ~1 focused series | first, blocking | — |
| **E** | E1–E5 verification | 2–3 small series | after D | D1 harness (proves each) |
| **F** | F1–F3 (constraints, kill switch, degradation) | small, high-leverage | after/with E | D (correct runtime) |
| **F** | F4–F7 (metadata, telemetry, promotion, projection) | small, additive | rolling | existing formula/metrics/UI seams |
| **F** | F8–F9 (coordination, lint) | F8 larger; F9 small | last / as-needed | — |

Dependency spine: **D1 unblocks everything** (it's how E and F changes are
proven). E1 (structured verdict) precedes E2 (`ESCALATE_HUMAN` uses the verdict
shape). F1's mechanical denylist reuses the `loop_integration` seam D2/D3 are
already editing — bundle them. F3/F2 both touch the `claim_work` budget clause —
do them together. F4 metadata feeds F6 promotion defaults and F5 telemetry
alarming.

**Recommended first three PRs:**
1. **D1 + D2 + D3** — the conformance harness and every audit fix, together.
   This is the one that must land before anything else; it also retro-covers
   Phases A–C.
2. **E1 + E2 + E5** — structured fail-closed verdicts, human escalation, and
   attempt-ledger + failure-signature stalls. The trust layer.
3. **F1 + F2 + F3** — constraints (with the denylist teeth that also finish
   audit #9), kill switch, degradation. The operator's day-one safety kit.

Everything after that (E3/E4, F4–F9) is rolling, additive, and independently
shippable.

## What we are deliberately NOT doing (and why it protects the vision)

- **No design wizard / spec compiler** (Looper's layer) — we *consume*
  `loop.resolved.json` as an optional import format at most; we don't build a
  coaching front-end. Our differentiator is runtime, not authoring.
- **No file-as-authority state** — F7 is a *projection*; the DB stays the
  source of truth. Adopting their file model would reintroduce exactly the
  races the bomb-proofing pass removed (their own docs list "state rot" and
  "parallel collision" as consequences).
- **No general workflow DSL, no multi-vendor marketplace, no methodology site.**
  Each is a different product. The plan only hardens and instruments
  loop-based automation.
- **No prompt-only "enforcement."** Any rule we can't check in the harness is
  copy, labeled as such — we don't ship guardrails that are guardrails by
  assertion.

## One-line summary

Fix the runtime and prove it can't regress (D), make its judgment as rigorous
as its plumbing (E), then give it the operator surface the practice community
lives in — with the harness teeth that make us more than a methodology (F) —
all without adding a second core entity or an authoring layer that isn't ours
to own.
