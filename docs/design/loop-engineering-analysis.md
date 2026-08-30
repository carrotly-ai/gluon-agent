# loop-engineering (Cobus Greyling) vs. Gluon Loops — analysis

*Analysis of [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering)
(HEAD `3eac4fb`, reviewed 2026-07-08) and what Gluon should take from it.
Companion to [looper-analysis.md](looper-analysis.md) and
[loop-first-pivot.md](loop-first-pivot.md). Note on lineage: this repo (or its
upstream essays) is almost certainly the "practice library" the pivot research
drew on — our Phase B SKU names match its pattern registry **1:1**
(daily-triage, pr-babysitter, ci-sweeper, dependency-sweeper,
changelog-drafter, post-merge-cleanup, issue-triage), and our L1/L2/L3
autonomy ladder matches its readiness levels. This analysis is therefore
partly a check of our homework against the source — and the check shows what
we ported (the patterns) and what we didn't (the operating discipline around
them).*

---

## 1. What loop-engineering actually is

A **methodology, pattern library, and community hub** — not a runtime. ~5.5k
stars, MIT, actively maintained, with an npm toolchain and a marketing-forward
showcase site. Tagline: *"Stop prompting. Design the loop. Get a score."* It
is explicitly tool-agnostic (Grok, Claude Code, Codex, opencode, OpenClaw,
GitHub Actions): patterns are neutral in intent, per-tool starters carry the
specifics.

**The doctrine — Five Building Blocks + Memory:** Automations/Scheduling,
Worktrees, Skills, Plugins/Connectors (MCP), Sub-agents (maker/checker split),
plus **Memory/State** as the "durable spine outside any conversation." A loop
is defined as *harness + schedule + state + verification chain* (the harness
being one session's environment).

**The file-convention operating model.** Everything lives as human-readable
files in the repo being looped over:

- `LOOP.md` — which loops run here, cadence, gates, worktree policy, kill
  switch. Both documentation and the seed the loops read.
- `STATE.md` — durable memory: High Priority (acting/waiting-on-human), Watch
  List, Recent Noise, last-run timestamp. Read at start, pruned every run.
- `loop-run-log.md` — append-only run history, one JSON object per run
  (`pattern`, `duration_s`, `items_found`, `actions_taken`, `escalations`,
  `tokens_estimate`, `outcome`).
- `loop-budget.md` — daily caps per loop (runs/day, tokens/day, max sub-agent
  spawns/run), on-exceed procedure, and a **kill switch** (`loop-pause-all`
  label/flag → every loop exits immediately).
- `loop-constraints.md` — **binding, repo-local, human-authored guardrails**
  read at the start of every run: push/merge rules ("never auto-merge to main
  without approval"), path denylists ("never edit `.env`, `auth/`,
  `payments/`, `secrets/`"), code rules ("never disable tests to make CI
  green", "max 3 fix attempts per item; escalate after"), and budget rules
  ("at 80% of daily cap, switch to report-only").

**The skills** (the agent-facing prompts — tight and well-crafted):

- `loop-triage` — structured report contract: High-Priority (with *suggested
  loop action* per item) / Watch / Noise / State Updates. "Signal, not
  invention"; never propose architecture during triage.
- `loop-verifier` — the checker in the maker/checker split. **Default stance:
  REJECT until proven otherwise.** Must *run* the tests itself ("do not trust
  implementer's claim"), check diff scope against the denylist, and return
  `APPROVE | REJECT | ESCALATE_HUMAN` with evidence.
- `minimal-fix` — smallest possible diff, one problem per invocation, never
  refactor unrelated code, "do not mark your own work done — the verifier
  decides."
- `loop-budget` — start-of-run spend check (≥80% → report-only mode; ≥100% or
  kill switch → exit immediately; empty watchlist → exit in <5k tokens), plus
  the end-of-run log append.
- `loop-constraints` — loads the constraints file before anything else and
  re-checks the relevant section before each push/edit/fix/merge.

**The npm toolchain** (thin, deterministic, dependency-free — deliberately no
LLM calls):

- `loop-audit` — a **Loop Readiness Score** (0–100, levels L0–L3) computed
  from ~15 signals: state file, triage/verifier skills, LOOP.md, budget + run
  log, safety docs, worktree evidence, and (v1.4) **proven loop activity** —
  timestamps, loop commits, scheduled workflows — so L3 can't be claimed with
  files alone. `--suggest` prints gap-closing commands; `--badge` emits a
  README badge; exit 2 below 40 for CI gating.
- `loop-context` — a **circuit breaker + deterministic memory manager** for
  long runs. Reads a JSON *attempt ledger* (`goal`, per-iteration
  `action/outcome/error/tokensUsed`) and: summarizes what was tried, prunes
  stale traces, injects only essentials into the next prompt; escalates (exit
  2) on max-iterations, **stagnation** (same error N× consecutively),
  **no-progress** (N consecutive failures), or token budget.
- `loop-init` (scaffold + first score), `loop-cost` (per-pattern spend
  estimator), `loop-sync` (drift detection between STATE/LOOP/skills),
  `loop-worktree` (worktree per fix attempt), `loop-mcp-server` (runtime
  lookup of patterns/skills/state for agents).

**The pattern registry** (`patterns/registry.yaml`) is machine-readable and
richer than a name: per-pattern `cadence`, `risk`, `phases`, `human_gates`,
`state` file, `week_one_mode` (always L1), `token_cost` tier, and a **cost
model** (`tokens_noop` / `tokens_report` / `tokens_action`,
`suggested_daily_cap`, `early_exit_required`).

**The operating literature** is the deepest part: a severity-classified
failure-mode catalog (infinite fix loop, **state rot**, **verifier theater**,
notification fatigue, token burn, over-reach, comprehension-debt spiral,
cognitive surrender, parallel collision, escalation failure), an anti-pattern
list (same agent implements and verifies; no attempt cap; L3 before L1
quality; shared state without schema; write-everything MCP scope; no kill
switch; fixing flakes with code; auto-merge without allowlist), a ship-
readiness checklist mapped to the L-levels, and **multi-loop coordination**
rules: one owner per branch, separate state files per loop, an explicit
priority order when loops conflict (CI Sweeper > PR Babysitter > Dependency
Sweeper > cleanup > triage), collision detection via `acting_on` markers in
state files, and a shared human inbox. Vocabulary worth keeping: *intent
debt* (skills pay it down), *comprehension debt* (grows unless you read what
the loop ships), *cognitive surrender*, *orchestration tax*.

**Reality check on the self-hosting.** The repo "eats its own dogfood," but
inspect the dogfood: the daily-triage GitHub Action is a **deterministic
shell workflow** — build `loop-audit`, compute the score, check workflow
health via `gh run list`, update `STATE.md`, append a run-log line with a
**hardcoded** `tokens_estimate: 52000` and `items_found` almost always 1. No
LLM is in the loop at all; 20+ logged runs are mechanical bookkeeping. The
operational wisdom in the docs is real (much of it community-contributed
failure stories), but the live "loops" maintaining the repo are far thinner
than the methodology they advertise. The value here is the **doctrine and
ops surface**, not running code.

## 2. The three-layer stack (where everyone sits)

With Looper analyzed earlier, the ecosystem now has a clear shape:

| Layer | Project | What it owns |
|---|---|---|
| **Methodology / practice** | loop-engineering | Which loops to run, how to operate them safely, community patterns + failure stories, readiness scoring |
| **Design / compilation** | Looper | One loop's spec: coached goal, typed verification, cross-model judge, portable compiled artifact |
| **Runtime / orchestration** | **Gluon** | Durable execution: work graph, parallel worktrees + merge-back, harness-enforced budgets/gates, autonomy ladder, resume, notifications, UI |

Both upper layers *explicitly* point at our layer as the missing piece:
Looper says "hand the spec to an orchestrator built for durable execution";
loop-engineering lists *"scheduled or multi-agent work with no durable
orchestrator or concurrency story"* as a named anti-pattern. Three
independent projects converge on the same control doctrine — budgets + kill
switches, stagnation/no-progress detection, maker/checker separation,
worktree isolation, staged autonomy — which is strong evidence the Phase A–C
architecture bets are the consensus, and that the runtime seat is both open
and load-bearing.

## 3. Head-to-head vs. Gluon

| Dimension | loop-engineering | Gluon loops |
|---|---|---|
| **Nature** | Doctrine + file conventions + thin deterministic CLIs | Durable runtime with harness authority |
| **State** | Human-readable files in the repo (`STATE.md` et al.), pruned by convention | SQLite + atomic cross-process primitives + web UI |
| **Concurrency** | Manual conventions (one owner per branch, `acting_on` markers, priority table) | Mechanical: atomic claims, ready-set dispatch, per-project caps, merge-back lock |
| **Budgets** | `loop-budget.md` caps + 80% report-only degradation + kill switch — *enforced by the agent reading a skill* | Enforced at dispatch by the harness (real cost accounting) — but no degradation tier, no kill switch |
| **No-progress** | Circuit breaker: same-error-N× stagnation + N-consecutive-failures, deterministic ledger | Empty-queue stall counter (audit showed it's blind to same-failure spinning) |
| **Constraints/policy** | Binding repo-local `loop-constraints.md` + path denylists, read every run | Autonomy levels + gates; **no per-repo policy surface, no denylist** |
| **Verifier** | Default-REJECT, must run tests itself, `APPROVE/REJECT/ESCALATE_HUMAN` | Confirms/rejects via tools; no explicit escalate-human verdict; same model family |
| **Cross-iteration memory** | Ledger summarize/prune/inject into next prompt | Fresh prompt per iteration; fix tasks carry only the latest gate output |
| **Patterns** | Registry with cadence/risk/cost-model/human-gates metadata | Same 7 SKUs as formulas — but with none of that metadata |
| **Autonomy** | L-levels as *earned maturity* (week-one L1; L3 requires activity evidence) | L-levels as a *setting* chosen at create time |
| **Multi-loop** | Priority order + collision detection + shared inbox | Nothing — two Gluon loops can act on the same PR unaware of each other |
| **Legibility** | Everything greppable in the repo; "team inspects state without chat logs" | DB + web UI; nothing loop-related visible in the target repo |
| **Kill switch** | `loop-pause-all` label/flag, all loops exit | Per-loop pause only |
| **Actual execution** | None (agent-follows-instructions, or plain CI shell) | The product |

**The core contrast:** they have the **policy and operations surface** without
enforcement; we have **enforcement** without the policy and operations
surface. Their constraints are binding only because a prompt says "binding" —
an agent can ignore `loop-constraints.md`, and their budget guard is a skill
the model is asked to run. That's exactly the self-policing gap our
harness-authority thesis exists to close. Conversely, we have nowhere for an
operator to write "never touch `payments/`," no kill switch for a runaway
fleet, no repo-visible trace that loops are operating at all, and our SKUs
carry a name where theirs carry an operating manual.

## 4. Multiple perspectives

**Ecosystem strategist.** This is the community hub for precisely the market
we recentered on, and our SKU library already descends from its registry. The
gravity play: make Gluon *the runtime for loop-engineering patterns* —
consume `registry.yaml` metadata into our formulas, run their
`loop-audit`-style readiness scoring for Gluon-managed projects, and show up
in their adopters/stories channel (they actively solicit failure stories;
5.5k stars of exactly-right audience). Their anti-pattern list is literally a
sales page for Gluon: every "don't do X without a durable orchestrator" is
our pitch. Caveat: the methodology commoditizes fast (it's MIT prose); the
runtime is the defensible layer. Their thin-CLI + badge + showcase marketing
is also simply good developer-tools distribution worth studying.

**Operations / SRE.** Their ops artifacts are the manual our runtime lacks.
If Gluon ran a real fleet today, the operator would have: no global kill
switch, no per-repo constraints, no budget degradation (loops go straight
from working to paused), no repo-visible run history, no per-SKU cost
expectations to alarm against, and no cross-loop collision protection. Every
one of those exists in this repo as a convention we can implement *with
teeth*. The run-log JSON shape (`items_found`, `actions_taken`,
`escalations`, `outcome` per run) is also a better operational summary than
our per-run cost rows — it aggregates to "is this loop worth its spend"
almost directly (and complements our existing acceptance-rate metrics).

**Safety / trust.** Their best ideas are policy ideas: binding constraints
read every run, default-REJECT verification, earned autonomy (L3 requires
*evidence of activity*, not configuration), comprehension-debt mitigations
(read what ships; weekly digest). Their weakness is that all of it is
prompt-level. The synthesis is obvious and powerful: **take their policy
surface, give it our enforcement.** A Gluon constraints file should be
injected into every iteration prompt (their layer) *and* mechanically
enforced where checkable — path denylist enforced at merge-back (refuse to
integrate a branch touching denylisted paths) and checked by the verifier;
budget rules enforced at dispatch as they already are. "Verifier theater" is
a named failure mode that applies to our same-family agent-verifier verbatim
— their default-REJECT framing plus our planned structured verdicts
(looper-analysis takeaway #3) is the countermeasure.

**Architecture.** State-in-repo vs. state-in-DB is the interesting tension.
Their file model buys legibility and portability and pays in exactly the
races our bomb-proofing pass eliminated — their own docs concede it (state
rot, parallel collision, one-file-per-loop conventions are manual locks). Our
DB model is correct; what we should copy is the *projection*: a generated,
read-only `LOOP-STATE.md`-style digest written into the project (or posted to
the channel) so the loop's memory is inspectable without Gluon's UI —
DB as authority, file as view. Second architectural steal: the
**attempt ledger**. Our continuation/fix prompts carry only the latest gate
output; a deterministic digest of *what was tried and how it failed* (we have
every prior run's status, cost, and gate output in the DB) injected into
continuation, fix, and verifier prompts is their `loop-context --inject` —
cheap, no LLM needed, and directly attacks our repeat-failure blind spot.

**Research validation.** Three projects arrived independently at
stagnation-detection, attempt caps, maker/checker, worktrees, staged
autonomy, and budget-plus-kill-switch. Where our session audit found real
bugs was precisely where we implemented consensus ideas with mechanical
sophistication the doctrine layer never needed (parallel dispatch,
cross-process locks). The doctrine repos de-risk our *what*; only our own
conformance testing de-risks our *how*.

## 5. Takeaways to keep (ranked, mapped to our code)

1. **Repo-local binding constraints** — support `loop-constraints.md` (or
   `.gluon/constraints.md`) in target projects: injected verbatim into every
   iteration's prompt (seed, task, continuation, verifier), with the **path
   denylist mechanically enforced** — merge-back refuses to integrate
   branches touching denylisted paths, and the default denylist
   (`.env*`, `secrets/**`, `auth/`, `payments/`, `*.key`) applies even with
   no file (converges with looper-analysis takeaway #7 and audit finding #9).
2. **Global kill switch** — `gluon loop pause --all [--project X]` backed by
   a flag checked in `claim_work` (one guard clause) + web UI toggle +
   Telegram/Discord command. Their `loop-pause-all`, with teeth.
3. **Attempt-ledger injection** — build a compact prior-attempts digest from
   the DB (iterations, outcomes, gate failures, costs) and inject it into
   continuation/fix/verifier prompts; prune to a recent window. Their
   `loop-context` inject/prune, no LLM required.
4. **Budget degradation tier** — at a configurable fraction of `max_cost_usd`
   (default 80%), the harness stops dispatching act-mode work and the loop
   drops to report-only (surveyor-style summary task, then pause). Degrade
   before stopping; the operator gets a report instead of a dead loop.
5. **Verifier hardening** — adopt default-REJECT framing and an explicit
   `ESCALATE_HUMAN` outcome (→ PAUSED + decision card) in the verifier
   template; fold into the structured-verdict work (looper takeaway #3).
6. **SKU metadata enrichment** — port `registry.yaml`'s fields into our
   formula schema: `cadence`, `risk`, `human_gates`, `week_one_autonomy`,
   and the cost model (`tokens_noop/report/action`, suggested daily cap) →
   powers a `gluon loop cost` estimator, better create-time defaults, and
   alarming on cost anomalies.
7. **Earned autonomy promotion** — use our existing per-loop metrics
   (acceptance rate, cost-per-accepted-change) to gate a *suggestion*:
   "this formula has run N times at L2 with X% acceptance — eligible for
   L3." Human approves; autonomy becomes a track record, not just a flag.
8. **Cross-loop coordination** — an `acting_on` target registry (loop_id →
   PR/issue/path keys, written at task dispatch) checked before claiming
   work that names the same target, plus loop priority classes for
   contention. Closes a real gap: today two Gluon loops can fight over one
   PR.
9. **Repo-visible state projection** — optionally write/update a generated
   loop digest in the project (or pin it to the channel): objective, status,
   last runs, pending graph, escalations. DB stays authoritative; the file
   is a view. Their "inspect without chat logs" legibility.
10. **Run-outcome telemetry** — add their run-log fields (`items_found`,
    `actions_taken`, `escalations`, `outcome`) to loop iteration records and
    the metrics endpoint; near-free given our schema.
11. **Failure-mode catalog → conformance scenarios + docs** — verifier
    theater, state rot (our queue-item rot from the audit is the same
    disease), escalation failure, notification fatigue each map to either a
    conformance test or an operator doc section.
12. **Vocabulary** — adopt *comprehension debt*, *verifier theater*,
    *intent debt* in our docs; they name things our design already believes.

## 6. Should we pivot?

**No — same verdict as Looper, with a sharper directional lesson.** Nothing
here challenges the runtime: this project *names the absence of Gluon as an
anti-pattern*. The consensus doctrine across both analyzed projects validates
Phases A–C nearly point-for-point.

The directional lesson is about emphasis: **we over-indexed on mechanics and
under-indexed on the operator.** The community's center of gravity — where
5.5k stars of practitioners actually live — is constraints files, kill
switches, cost expectations, readiness scores, legible state, and multi-loop
etiquette. Gluon has world-class plumbing behind an ops surface that doesn't
yet exist. The synthesis for our roadmap: their policy surface + Looper's
verification typing + our enforcement spine. Phase F (design discipline)
should grow an explicit **operator track**: constraints (#1), kill switch
(#2), degradation (#4), cost metadata (#6), and the state projection (#9) —
alongside the already-planned lint/coaching work. Items #1–#5 are small,
self-contained, and compound with the audit fixes; #6–#9 ride the existing
formula/metrics/UI seams.

One-sentence summary: **loop-engineering is the field manual for the army we
built the weapons for — adopt its rules of engagement, keep our artillery,
and make sure the manual's safety chapters are enforced by hardware, not
memos.**
