# Looper vs. Gluon Loops — analysis

*Analysis of [ksimback/looper](https://github.com/ksimback/looper) (v0.4.0, commit
`48ed538`, reviewed 2026-07-08) and what Gluon's loop engineering should take
from it. Companion to [loop-first-pivot.md](loop-first-pivot.md); the takeaways
below reference findings from the 2026-07-07 session audit of the
`feat/loop-first-pivot` branch (PR #172).*

---

## 1. What Looper actually is

Looper is **not an orchestrator and is emphatic about it**. Its one hard rule:
*"Looper's own process never invokes a model to do loop work. It only reads
input, coaches, and writes files."* It is a **design-time coach and compiler**
for agent loops, packaged as a Claude Code skill (`/looper`).

**The pipeline:** a 7-stage interview (goal → verification → host model →
council → gates/control → ASCII preview → emit), where each stage is
*critiqued* against a rubric before being accepted. Output is a set of
artifacts: human-authored `loop.yaml` → compiled/normalized
`loop.resolved.json` (argv arrays resolved, rubrics inlined) → two execution
surfaces generated from it: `RUN_IN_SESSION.md` (a handoff prompt the current
Claude session follows) and `run-loop.py` (a strict external Python runner).
The runner *only* reads the resolved JSON — never the YAML.

**The spec's core ideas:**

- **Typed verification taxonomy** — every done-criterion is a typed object,
  never prose: `programmatic` (argv command + expected exit), `judge` (a
  rubric scored by a model, returning fenced-JSON
  `{verdict: pass|revise, blocking_issues, confidence}`), or `human` (a
  signoff prompt). The wizard pushes hard toward programmatic-first and warns
  on "all-vibe" verification.
- **Reviewer ≠ judge** — a *reviewer* emits notes only; a *judge* emits a
  verdict that gates progression. The schema enforces the consequence:
  `revise_until_clean` **requires** a judge or human `verdict_source`, because
  a reviewer-only gate has nothing that can declare "clean."
- **Cross-model council by default** — the judge should be a *different model
  family* than the host ("blind-spot coverage"), wired via a model registry
  (`~/.looper/models.json`, argv metadata only, no keys). `lint` warns when
  judge and host share a family.
- **Termination as a schema requirement** — it refuses to emit a loop without
  guards: `max_iterations`, per-gate `max_revisions`, **no-progress detection**
  (a *signature derived from the gate round's failures*; same signature N
  consecutive times → stop), and budget caps — with an unusual honesty rule:
  wall-clock is enforced; token/USD caps are labeled *advisory* because "a
  runner MUST NOT claim to enforce them when it cannot measure them."
- **Privacy as first-class schema** — per-member egress declarations,
  normative default redaction globs (`.env`, `secrets/**`, `**/*.key` — apply
  even when the spec omits them), two-layer redaction (flagged files are never
  read into prompts; their *content* is scrubbed from every send including to
  the host, with catches logged), and fail-closed consent before the first
  cross-vendor send.
- **A normative runner contract + conformance suite** — `RUNNER-CONTRACT.md`
  is RFC-2119 MUSTs (fail-closed judge parsing: malformed verdict → `revise`,
  *never* `pass`; workspace-escape refusal; exactly one terminal state — "a
  crash MUST NOT leave state claiming running"; exit code 0 means one thing
  only). Ten deterministic conformance scenarios run against **fake model
  CLIs** — canned host/judge scripts — so the whole control machinery is
  tested without ever paying for a model call. The reference runner is held to
  the same suite in CI.
- **A template library** (`security-scan`, `code-review`, `bug-hunt`,
  `docs-sync`, `research-synthesis`) — each a complete, compiler-validated
  `loop.yaml` with `{{PLACEHOLDER}}` slots plus a bundled programmatic checker
  script; templates still pass through critique and lint.

**What it deliberately lacks** (its own words): scheduling, durable state
across restarts, sub-agent lifecycles, concurrency control, run history. *"Use
Looper to design the loop and hand the resulting spec to an orchestrator built
for durable execution."* Its execution model is also thin: the host is invoked
per-step as a stateless CLI call (`claude -p` with the prompt on stdin)
producing a linear plan → delivery-N artifact chain — no work graph, no
parallelism, no tools/session continuity in the external runner.

## 2. Head-to-head

| Dimension | Looper | Gluon loops |
|---|---|---|
| **Layer** | Design-time (pre-flight coach + compiler) | Runtime (durable orchestrator) |
| **Who authors the plan** | The human, coached, before anything runs | The surveyor *agent*, at runtime, from a one-line objective |
| **Human's seat** | Designs everything up front; optional checkpoints | Trust-boundary checkpoints (L1/L2 plan pause), decision cards |
| **Work shape** | Linear: plan → delivery-1..N, two gates | DAG: `depends_on`, ready-set dispatch, parallel worktrees, merge-back |
| **Verification** | Typed criteria (programmatic/judge/human), rubrics, structured JSON verdicts, fail-closed parsing | `verify_cmd` (programmatic) + same-family agent-verifier whose verdict is *behavioral* (calls a tool), + plan approval |
| **Judge model** | Cross-family by default; lint warns on same-family | Always the same Claude family |
| **No-progress** | Failure-*signature* repetition ("same blocker twice") | Queue-*emptiness* ("nothing pending") |
| **Budget enforcement** | Wall-clock enforced; tokens/USD honestly labeled advisory | Real cost accounting enforced at dispatch (stronger), though soft under parallel fan-out per the audit |
| **Caps authority** | External runner = process-enforced; in-session = model self-policing (their weak spot) | Harness-enforced always (our core design tenet) |
| **Spec artifact** | `loop.yaml` → `loop.resolved.json`: versionable, PR-able, lintable in CI | CLI flags/API body → DB row; formulas are the closest analog (no compile/lint) |
| **Testing discipline** | Contract + conformance suite on deterministic fake models | 2,398 real tests, but zero cross-process/fake-agent coverage (audit finding #10) |
| **Privacy** | Egress declarations, normative redaction globs, consent fail-closed | None (single-vendor today; and the audit found merge-back can auto-commit `.env`) |
| **Durability/concurrency/scheduling/UI/notifications** | Explicitly out of scope | The whole product |

**The deep difference is philosophical.** Looper front-loads human judgment:
the loop is fully designed, reviewed, linted, and versioned before a single
model call. Gluon defers judgment to runtime: the agent designs the campaign
and the human is inserted only at autonomy-gated checkpoints. Looper trades
scale for reviewability (a human can't hand-design "fix all 200 issues"); we
trade reviewability for scale (our surveyor authors plans nobody coached — the
`/goal` critique in their README, *"garbage goal in, confidently-wrong loop
out,"* applies verbatim to `gluon loop create`). The two are almost perfectly
complementary — Looper is the front half of a stack whose back half is exactly
what we built, and their spec even names our seam:
`execution.mode: orchestrated`, "hand the spec to a real orchestrator."

## 3. Multiple perspectives

**Product strategist.** Looper validates the loop-engineering thesis and
stakes out the *design* layer, explicitly declining the execution layer we
own. The obvious play is interop, not competition:
`gluon loop import loop.resolved.json` would make Gluon the durable runner
Looper tells its users to find — their gates map to our `verify_cmd` +
verifier, their `loop_control` to our budgets/stalls, their workspace
artifacts to our worktrees. Cheap to build, and it positions Gluon as *the*
orchestrated backend for a spec format someone else evangelizes. The shared
risk for both projects: Claude Code's native `/goal`+`/loop`+workflows
absorbing both layers.

**QA / engineering.** The single biggest lesson is **conformance testing on
fake models**. Their build order froze the artifact contract with "dummy
host/judge scripts that emit canned artifacts... prove loop control,
max_revisions, resume, and verdict parsing — without paying model costs. Keep
these as test fixtures." Our audit found exactly the holes this discipline
prevents: 4 High bugs that 2,398 tests missed because nothing exercises
cross-process concurrency, and our live validations kept being confounded by
nondeterministic agent behavior (the watch loop never idled; the surveyor
short-circuited). A deterministic fake-worker harness for
`on_run_completed`/`claim_work`/merge-back — including real multi-process
scenarios — is the direct antidote, and would have caught the `hash()` lock
bug.

**Safety / authority.** Per-gate, their typing is more rigorous than ours:
reviewer/judge separation enforced in schema, malformed judge output degrades
to `revise` never `pass`, consent fails closed, "exactly one terminal state"
(our audit literally found 6 items stuck in `claimed` — violating that norm).
But their *in-session* mode surrenders enforcement to the model following
instructions — precisely the self-policing failure mode our harness-authority
design exists to prevent, and they admit it. **Verdict: our enforcement spine
is stronger; their typing and fail-closed norms should be grafted onto it.**
Concretely: our independent verifier's verdict today is behavioral (it calls
`loop_complete` or enqueues fixes) — no structured verdict, no
`blocking_issues`, no confidence, no fail-closed parse. And our verifier is
always the same model family as the generator, the exact blind spot their
council rubric targets — notable since we have Ollama (`qwen3:14b`, solid
tool-calling) on the same box as a free cross-family judge.

**Loop design / UX.** Coaching is their genuine differentiator and our genuine
gap. Our objectives are un-coached free text; nothing pushes a user toward a
`definition_of_done`, scope boundaries, or programmatic-first verification,
and nothing lints our SKU formulas for anti-patterns (a gateless loop with
`agent_verifier: false` is "all-vibe" by their taxonomy — several of our SKUs
ship that way). Their goal rubric's critique prompts ("what would count as
done if two competent agents disagreed?") would cost us a paragraph in the
surveyor seed template and materially improve decomposition quality.

**Privacy / ops.** Their normative default redaction globs and blind-spot
surfacing have no Gluon analog — and the audit's finding #9 (merge-back's
`git add -A` can commit copied `.env` files onto `main`) shows we are
currently *worse* than their floor. Their egress-consent model becomes
mandatory the day we add cross-model judges.

## 4. Takeaways to keep (ranked, mapped to our code)

1. **Fake-agent conformance harness** — deterministic worker fixtures driving
   the real `LoopManager`/`claim_work`/merge-back across actual processes.
   Directly closes audit finding #10 and would have caught the lock bug.
   Highest leverage, no product change.
2. **Failure-signature no-progress detection** — hash the task-gate/loop-gate
   failure output; same signature N consecutive iterations → pause.
   Complements our emptiness-based stall (which the audit showed is wrong
   under parallelism) and closes a real hole: our harness-authored fix tasks
   embed iteration numbers to bypass dedup, so a loop failing the *same way*
   forever never "stalls" — it just burns budget. Looper stops that at 2.
3. **Structured judge verdicts for the independent verifier** — require fenced
   JSON (`verdict`/`blocking_issues`/`confidence`), parse fail-closed
   (malformed → completion rejected, never granted), store it on the loop for
   the UI/metrics. Small change to `_VERIFICATION_PROMPT_TEMPLATE` +
   `_resolve_completion_request`.
4. **Typed verification on loops** — grow `verify_cmd` into criteria:
   programmatic (have), `judge` (rubric text + model — the verifier gains a
   real rubric instead of judging against the raw objective), `human` (a
   checkpoint criterion generalizing the L1/L2 plan pause).
5. **Cross-model verifier option + same-family lint warning** —
   `--verifier-model` accepting an Ollama model; warn when verifier family ==
   generator family. Our infra already supports it.
6. **`gluon formula lint` + repo-local loop specs** — port their lint checks
   (all-vibe, missing caps, unreachable criteria, unresolved placeholders)
   over our formula YAML; allow `.gluon/loops/*.yml` in project repos so
   campaign definitions become versioned, PR-reviewable artifacts. Our
   formulas are 80% of their spec already — we lack only the
   compile/lint/versioning discipline.
7. **Normative redaction globs in merge-back** — never auto-commit
   `.env*`/`secrets/**`/`*.key` (also fixes audit finding #9).
8. **Honesty + terminal-state norms** — fix the "hard dispatch ceiling"
   comment (audit #6) and adopt "no state may claim running/claimed after the
   fact" as a tested invariant (audit #2 is the live violation).
9. **Goal coaching in the seed** — one surveyor step: critique the objective
   against a goal rubric (outcome? done-state? scope?) and record a
   `definition_of_done` before decomposing.

## 5. Should we pivot?

**No — extend, don't pivot.** The runtime bet is right: Looper's own boundary
section is a testimonial that the durable, concurrent, harness-authority
orchestrator is the hard, valuable part — they refused to build it and tell
users to go find one. Nothing in Looper challenges our work graph, merge-back,
autonomy ladder, or dispatch authority; their execution model (stateless
per-step CLI calls, linear artifact chain, self-policed in-session caps) is
strictly weaker than ours.

What deserves adoption is their **discipline layer**: design-as-artifact
(compile + lint + version), typed verification with structured fail-closed
verdicts, cross-family judging, failure-signature stalls, and above all
contract-plus-conformance testing on fake models. In one sentence: **Looper
teaches us how to make loops *trustworthy before they run* and *testable
without models*; we already know how to make them *powerful while they run* —
and gluon consuming `loop.resolved.json` as an import format would join the
two stacks at the seam both projects left open.**

Natural next steps, in order of value: (a) the fake-agent conformance harness
together with the audit's High fixes (they belong in one commit series — the
harness proves the fixes), (b) structured verifier verdicts +
failure-signature stalls, (c) formula lint.
