"""Agent-loop lifecycle: the outer loop of loop-engineering Phase 2.

An :class:`~gluon.models.AgentLoop` is a persistent objective iterated across
many runs. Each iteration is an ordinary ExecutionRun dispatched from a
work-queue item carrying ``loop_id``; the running agent authors follow-up tasks
via the ``gluon-loop`` MCP tools (see loop_tools.py). This module is the
harness-side authority — it decides, after every iteration, whether the loop
continues, completes, or stops:

- **Completion** is a handshake: the agent *requests* it (``loop_complete``);
  the loop's ``verify_cmd`` gate (exit 0, via gate.run_gate) *grants* it.
  Gateless loops accept the agent's word — matching Phase 1 semantics.
- **Budgets** (max_iterations / max_cost_usd) pause the loop rather than fail
  it, so a human can raise the budget and resume. Pending agent-authored tasks
  are preserved (claim_work skips non-RUNNING loops).
- **Stalls** (an iteration that neither enqueues nor completes) first inject a
  harness-authored continuation task; ``max_stalls`` consecutive stalls pause
  the loop. This is the no-progress detector.

Design + rationale: docs/design/agent-loops.md
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gluon.gate import run_gate
from gluon.models import AgentLoop, ExecutionRun, LoopStatus, RunStatus


@dataclass(frozen=True)
class _TaskGate:
    """A task-level verification gate: the item that declared it + its command."""

    item_id: str
    cmd: str
    task_prompt: str


if TYPE_CHECKING:
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Loop tasks dispatch ahead of default queue items (priority 10) so an active
# loop keeps momentum, but behind anything an operator marks truly urgent.
LOOP_TASK_PRIORITY = 5


def _budget_degrade_fraction() -> float:
    """Fraction of the cost cap at which a loop degrades to report-only + pause
    (Phase F3). Operator-tunable via GLUON_LOOP_BUDGET_DEGRADE_FRACTION; 0 or an
    out-of-range value disables the tier. Default 0.8."""
    import os

    raw = os.environ.get("GLUON_LOOP_BUDGET_DEGRADE_FRACTION", "0.8")
    try:
        frac = float(raw)
    except ValueError:
        return 0.8
    return frac if 0 < frac < 1 else 0.0


_SEED_PROMPT_TEMPLATE = """This is iteration 1 of a new agent loop. You are the SURVEYOR — your job
this iteration is to map the landscape and author the work graph, not to do the work.

Objective:
{objective}

1. SURVEY the landscape relevant to the objective. Depending on what it needs:
   inspect the code and tests; run the test suite; check `git status`; and where
   the objective concerns issues or pull requests, enumerate them (`gh issue list`,
   `gh pr list` — read-only). Build a concrete picture of everything the
   objective requires.
2. DECOMPOSE into tasks via loop_enqueue_task — this authors the work graph:
   - one task per independent unit of work (independent tasks run in PARALLEL
     when the loop uses worktrees) — e.g. one task per bug, per issue, per PR;
   - use depends_on=[earlier task IDs] to chain work that must be sequential;
   - give each task its own verify_cmd where an objective check exists (its
     gate must exit 0 before the task counts as done);
   - every prompt must be fully self-contained: the executing agent has no
     memory of this session, so include file paths, issue/PR numbers, and the
     acceptance criteria.
   - CRITICAL — worktree isolation: each task you author runs in its OWN fresh
     checkout of the repository, cwd already at the repo root. NEVER put an
     absolute path or a worktree/temp location (e.g. `/tmp/gluon-worktrees/...`,
     or the directory YOU are currently in) into a task prompt — that path
     belongs to a different, ephemeral worktree and writing there orphans the
     work so it never merges back. Refer to files ONLY by repo-relative paths
     (e.g. `src/strings.py`), and do NOT do the implementation work yourself
     in this survey — author the tasks and let them run.
   - give each authored task a verification step: reference any available
     verification skill (e.g. `verify-loop-work`) in the task prompt and/or set
     a `verify_cmd` so the executor proves its work before the task counts done.
3. If the objective is genuinely a single small task, you may instead do it now
   and call loop_complete. If your survey finds the objective already met,
   call loop_complete with the evidence. Before claiming completion, VERIFY —
   use any available verification skill and run the gate yourself.

Before finishing you MUST have either enqueued the task graph or called
loop_complete — ending with neither counts as a stall."""

_TASK_GATE_FIX_TEMPLATE = """[TASK GATE FAILED — iteration {iteration}] A completed task did not pass its
verification gate. Fix it so the gate passes.

Original task:
{task_prompt}

Gate command (must exit 0):
  {verify_cmd}
Failing output (tail):
{output}

Fix the underlying problem, run the gate yourself to confirm it passes, then
finish. Do NOT call loop_complete unless the whole loop objective is also met."""

_INTEGRATION_FIX_TEMPLATE = """[INTEGRATION CONFLICT — iteration {iteration}] A completed task's branch could
not be merged back into the project.

Branch with the completed work: {branch}
Target (source) branch: {source}
Merge failure detail:
{detail}

In your working directory, run `git merge {branch}`, resolve every conflict
faithfully (keep BOTH sides' intent — the branch carries completed loop work),
commit the resolution, and finish. Do NOT call loop_complete unless the whole
loop objective is also met."""

_CONTINUATION_PROMPT_TEMPLATE = """[LOOP CONTINUATION — iteration {iteration}] The loop objective is not yet
complete and no follow-up tasks are pending.{gate_block}

Objective:
{objective}
{ledger}
Assess what remains, then EITHER do the next focused slice of work and enqueue
follow-up task(s) via loop_enqueue_task, OR call loop_complete if the objective
is fully met. Before claiming completion, VERIFY — use any available
verification skill (e.g. `verify-loop-work`) and run the gate yourself."""

_WATCH_SEED_TEMPLATE = """[WATCH TRIGGER — iteration {iteration}] This is an event-reactive loop. Its
watch command reported new external state to act on. You are the SURVEYOR for
this reactive cycle: read the signal below, then author the work it implies.

Objective:
{objective}

Watch signal (stdout of `{watch_cmd}`, tail):
{output}

DECOMPOSE the work this signal implies via loop_enqueue_task (one task per
independent unit; use depends_on for ordering; repo-relative paths only — each
task runs in its own fresh checkout). If the signal turns out to need no action,
call loop_complete for this cycle instead. Do NOT do the implementation work
here — author the tasks and let them run."""

_GATE_DENIED_BLOCK = """

Your previous completion request was DENIED — the verification gate failed:
  verify_cmd: {verify_cmd}
Gate output (tail):
{output}
Make the gate pass (exit 0) before requesting completion again."""

# Marker identifying a verifier iteration's prompt. on_run_completed uses it to
# distinguish a verifier's verdict from a work iteration's completion request
# (recursion guard: the verifier's own approval is not re-verified).
VERIFICATION_MARKER = "[LOOP VERIFICATION]"

_VERIFICATION_PROMPT_TEMPLATE = """{marker} — iteration {iteration}. You are an INDEPENDENT VERIFIER.

A previous iteration claims this loop's objective is complete. You did NOT do
that work — judge it SKEPTICALLY, on the evidence in the repository, not on the
claim. Your default stance is to REJECT unless the evidence is strong. Do not
trust the implementer's word that tests pass — run them yourself.

Objective:
{objective}

Claimed completion summary:
{summary}
{ledger}
Read the relevant code/artifacts, run the tests/checks that exist, and look
specifically for gaps between the claim and reality (missing acceptance
criteria, placeholder work, untested paths, unrelated changes).

Then call loop_complete ONCE, with a summary that ENDS with a fenced JSON
verdict block in exactly this shape (do NOT enqueue tasks yourself — the harness
acts on your verdict):

```json
{{"verdict": "pass|revise|escalate", "blocking_issues": ["..."], "confidence": 0.0, "notes": "..."}}
```

- "pass" — the objective is genuinely and fully met (blocking_issues empty).
- "revise" — real gaps remain; list each as a specific, self-contained
  blocking issue. The harness will spawn a fix task per issue.
- "escalate" — you cannot decide safely (ambiguous requirements, risky change,
  can't run the checks). The harness pauses the loop for a human.

Report only issues that affect correctness or the stated objective, not style.
A missing or malformed verdict block is treated as "revise" (fail-closed)."""

_VALID_VERDICTS = ("pass", "revise", "escalate")
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class VerifierVerdict:
    """A structured, fail-closed verdict from the independent verifier (Phase E1).

    ``parsed`` is False when we could not read a valid verdict and fell closed to
    ``revise`` — never to ``pass``: an unreadable verdict must not grant
    completion.
    """

    verdict: str  # pass | revise | escalate
    blocking_issues: list[str] = field(default_factory=list)
    confidence: float | None = None
    notes: str = ""
    parsed: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "verdict": self.verdict,
                "blocking_issues": self.blocking_issues,
                "confidence": self.confidence,
                "notes": self.notes,
                "parsed": self.parsed,
            }
        )


def parse_verifier_verdict(text: str) -> VerifierVerdict:
    """Extract the fenced JSON verdict from a verifier's completion summary.

    Fail-closed: any problem (no block, bad JSON, missing/invalid ``verdict``)
    yields ``revise`` with ``parsed=False`` so a malformed verifier output can
    never be mistaken for a pass. Uses the LAST fenced block in the text.
    """
    text = text or ""
    matches = _JSON_BLOCK_RE.findall(text)
    # Fall back to a bare trailing object if no fenced block was found.
    if not matches:
        brace = text.rfind("{")
        if brace != -1:
            matches = [text[brace:]]
    for raw in reversed(matches):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        verdict = str(data.get("verdict", "")).strip().lower()
        if verdict not in _VALID_VERDICTS:
            continue
        issues_raw = data.get("blocking_issues") or []
        issues = [str(i).strip() for i in issues_raw if str(i).strip()] if isinstance(issues_raw, list) else []
        conf = data.get("confidence")
        return VerifierVerdict(
            verdict=verdict,
            blocking_issues=issues,
            confidence=float(conf) if isinstance(conf, (int, float)) else None,
            notes=str(data.get("notes", "")).strip(),
            parsed=True,
        )
    return VerifierVerdict(verdict="revise", parsed=False, notes="verdict block missing or unparseable")


class LoopManager:
    """Creates agent loops and advances them when iteration runs complete."""

    def __init__(self, store: GluonStore):
        self.store = store

    # ---------- creation ----------

    def create_loop(
        self,
        project_id: str,
        objective: str,
        *,
        verify_cmd: str | None = None,
        agent_verifier: bool = False,
        agent_verifier_model: str | None = None,
        profile: str = "standard",
        model: str | None = None,
        executor_model: str | None = None,
        watch_cmd: str | None = None,
        use_worktree: bool = False,
        autonomy: str = "L3",
        max_iterations: int = 20,
        max_cost_usd: float | None = None,
        max_stalls: int = 2,
        max_fanout: int = 10,
        initiator: str | None = None,
        created_by_user_id: str | None = None,
    ) -> AgentLoop:
        """Create a loop and seed iteration 1 into the work queue.

        The seed task is dispatched by the existing queue machinery (drain loop
        or self-propelling dispatch) — no new execution engine.

        Raises ValueError on an out-of-range budget/objective — validated here so
        every entry path (CLI, formula, API) is covered, not just the web request
        model.
        """
        # Bomb-proofing: reject invariants that would let the loop misbehave —
        # instant stall (max_stalls<1), never author work (max_fanout<1), instant
        # or never budget-stop (max_iterations<1, max_cost_usd<=0), or a blank gate.
        objective = (objective or "").strip()
        if not objective:
            raise ValueError("Loop objective must be a non-empty string.")
        if max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1 (got {max_iterations}).")
        if max_stalls < 1:
            raise ValueError(f"max_stalls must be >= 1 (got {max_stalls}).")
        if max_fanout < 1:
            raise ValueError(f"max_fanout must be >= 1 (got {max_fanout}).")
        if max_cost_usd is not None and max_cost_usd <= 0:
            raise ValueError(f"max_cost_usd must be > 0 when set (got {max_cost_usd}).")
        autonomy = (autonomy or "L3").upper()
        if autonomy not in ("L1", "L2", "L3"):
            raise ValueError(f"autonomy must be L1, L2 or L3 (got {autonomy!r}).")
        # A blank/whitespace verify_cmd is not a gate — normalize to gateless
        # rather than silently running an empty shell command that always exits 0
        # (a false "gated" loop that grants every completion).
        if verify_cmd is not None and not verify_cmd.strip():
            verify_cmd = None
        # Same normalization for the optional routing/watch config — an empty
        # string is "unset", not a real model id or a watch that always fires.
        executor_model = (executor_model or "").strip() or None
        watch_cmd = (watch_cmd or "").strip() or None
        agent_verifier_model = (agent_verifier_model or "").strip() or None
        # A verifier model only means anything with the verifier enabled.
        if agent_verifier_model and not agent_verifier:
            agent_verifier = True

        loop = AgentLoop(
            project_id=project_id,
            objective=objective,
            verify_cmd=verify_cmd,
            agent_verifier=agent_verifier,
            agent_verifier_model=agent_verifier_model,
            profile=profile,
            model=model,
            executor_model=executor_model,
            watch_cmd=watch_cmd,
            use_worktree=use_worktree,
            autonomy=autonomy,
            max_iterations=max_iterations,
            max_cost_usd=max_cost_usd,
            max_stalls=max_stalls,
            max_fanout=max_fanout,
            initiator=initiator,
            created_by_user_id=created_by_user_id,
        )
        self.store.create_agent_loop(loop)
        self.store.enqueue_work(
            project_id=project_id,
            prompt=_SEED_PROMPT_TEMPLATE.format(objective=objective),
            profile=profile,
            priority=LOOP_TASK_PRIORITY,
            loop_id=loop.id,
            source="seed",
        )
        logger.info(
            "Created agent loop %s (project %s, %s, max_iterations=%d)",
            loop.id[:8],
            project_id[:8],
            "gated" if verify_cmd else "gateless",
            max_iterations,
        )
        return loop

    # ---------- advancement (the harness-side authority) ----------

    async def on_run_completed(self, run: ExecutionRun) -> None:
        """Advance the loop after one iteration run reaches a terminal status.

        Called from the runner's completion seam (before self-propelling queue
        dispatch, so continuations enqueued here are picked up immediately).
        """
        if not run.loop_id:
            return

        # Atomically add this iteration + its cost, but ONLY if the loop is still
        # RUNNING. Returns the authoritative post-increment snapshot, or None if the
        # loop already stopped (a manual pause/cancel, budget, or completion won a
        # race). Never derive iteration_count/total_cost_usd from an unlocked read —
        # sibling worker subprocesses would clobber each other's increments.
        loop = self.store.advance_loop_counters(run.loop_id, run.cost_usd or 0.0)
        if loop is None:
            # Loop not RUNNING: still record this run's cost so the ceiling stays
            # accurate across pause windows, but do not advance or decide.
            self.store.add_loop_cost(run.loop_id, run.cost_usd or 0.0)
            return

        # 1. Fail-safe: a failed/cancelled iteration pauses the loop for a human.
        #    Clear any pending completion request so a later resume can't fire it
        #    off this dead iteration.
        if run.status == RunStatus.FAILED:
            self.store.set_loop_completion(loop.id, False, None)
            self._pause(loop, f"iteration run {run.id[:8]} failed: {(run.error_message or 'unknown error')[:200]}")
            return
        if run.status == RunStatus.CANCELLED:
            self.store.set_loop_completion(loop.id, False, None)
            self._pause(loop, f"iteration run {run.id[:8]} was cancelled")
            return

        # 1b. Task-level gate (loop-first pivot Phase A): if this iteration's
        #     queue item declared its own verify_cmd, that gate judges THIS task
        #     (distinct from loop.verify_cmd, which judges the whole objective).
        #     Failure spawns a targeted fix task and demotes any completion claim
        #     made this iteration — a task that can't pass its own gate cannot be
        #     evidence the objective is met. Budgets still bound the fix cycle.
        task_gate = self._task_gate_for_run(run)
        if task_gate is not None:
            cwd = self._gate_cwd(run)
            if cwd is not None:
                result = await asyncio.to_thread(run_gate, task_gate.cmd, cwd)
                if not result.passed:
                    logger.info(
                        "Loop %s: task gate failed for item %s (exit %d) — spawning fix task",
                        loop.id[:8],
                        task_gate.item_id[:8],
                        result.exit_code,
                    )
                    # All mutations here are atomic store helpers — a full-row
                    # update_agent_loop(loop) from this stale snapshot would
                    # clobber the completion clear back to True (the lost-update
                    # class the bomb-proofing pass eliminated).
                    self.store.set_loop_completion(loop.id, False, None)
                    budget_reason = loop.budget_exhausted()
                    if budget_reason is not None:
                        self._pause(loop, budget_reason)
                        return
                    # E5: pause if this task keeps failing its gate the SAME way.
                    if self._register_gate_failure(loop, f"taskgate:{task_gate.item_id}:{result.output}"):
                        self._pause(
                            loop,
                            f"repeating failure: task gate `{task_gate.cmd}` failed the same way "
                            f"{loop.failure_sig_count} times — a different approach is needed",
                        )
                        return
                    self._enqueue_task_fix(loop, task_gate, result.output)
                    return
                # Task gate passed: clear any prior failure-signature streak.
                self.store.reset_loop_failure_signature(loop.id)

        # 1c. Worktree merge-back (loop-first pivot Phase B): a completed
        #     worktree task's branch is merged into the project's source branch
        #     so siblings and later verification build on integrated state
        #     (Phase A live-validation showed parallel outputs were otherwise
        #     invisible to each other). Runs AFTER the task gate (never
        #     integrate work that failed its own gate). Conflicts spawn an
        #     agent resolution task; the checkout is left pristine either way.
        if run.use_worktree:
            project = self.store.get_project(run.project_id)
            if project is not None:
                from gluon.loop_integration import INTEGRATED_STATUSES, integrate_run_branch

                integ = await integrate_run_branch(project.expanded_path, run)
                if integ.status == "conflict":
                    self.store.set_loop_completion(loop.id, False, None)
                    budget_reason = loop.budget_exhausted()
                    if budget_reason is not None:
                        self._pause(loop, budget_reason)
                        return
                    self._enqueue_integration_fix(loop, run, integ.detail)
                    return
                if integ.status not in INTEGRATED_STATUSES and integ.status not in ("skipped", "conflict"):
                    # branch_moved / error / denylist_violation: the completed
                    # task's work is stranded on its branch. Do NOT silently
                    # proceed (audit finding #5) — a later verify_cmd against the
                    # project checkout would fail against work it can't see.
                    # PAUSE for a human with the branch preserved; resume after
                    # they resolve the branch state or the transient fault.
                    self.store.set_loop_completion(loop.id, False, None)
                    logger.warning(
                        "Loop %s: integration of run %s not performed (%s: %s) — pausing for review",
                        loop.id[:8],
                        run.id[:8],
                        integ.status,
                        integ.detail,
                    )
                    self._pause(
                        loop,
                        f"integration {integ.status} for run {run.id[:8]}: {integ.detail[:200]} "
                        f"(work preserved on branch {run.branch_name or '?'}; resume after resolving)",
                    )
                    return

        # 1d. Plan checkpoint (Phase B autonomy ladder): when the SURVEYOR
        #     iteration has authored the work graph and the loop is not fully
        #     unattended (L1/L2), pause for a human before executing — the
        #     plan-approval trust boundary. Pending tasks stay inert while
        #     PAUSED (claim_work skips non-RUNNING loops); `gluon loop resume`
        #     executes the plan.
        if loop.autonomy in ("L1", "L2") and self._is_seed_run(run) and not loop.completion_requested:
            pending = self.store.count_pending_loop_items(loop.id)
            if pending > 0:
                label = "L1 report-only" if loop.autonomy == "L1" else "L2 assisted"
                self._pause(
                    loop,
                    f"plan ready for review ({label}): {pending} task(s) authored — "
                    f"inspect the graph, then resume to execute",
                )
                return

        # 2. Completion handshake — the gate/verifier, not the agent, is authority.
        #    Its non-completing branches enforce budgets too (they used to skip the
        #    step-3 check below, the original runaway-quota bug).
        if loop.completion_requested:
            await self._resolve_completion_request(loop, run)
            return

        # 3. Budgets: pause (not fail) so a human can extend and resume.
        budget_reason = loop.budget_exhausted()
        if budget_reason is not None:
            self._pause(loop, budget_reason)
            return

        # 3a. Budget degradation tier (Phase F3): degrade-before-dying. When
        #     spend crosses the configured fraction of the cost cap (default
        #     80%), PAUSE report-only with the work preserved — the operator
        #     gets a decision card at 80% instead of a silent stop at 100%, and
        #     can review, raise --max-cost, and resume, or cancel. Only fires
        #     once completion isn't already being granted this iteration.
        if not loop.completion_requested:
            degrade_reason = loop.budget_degraded(_budget_degrade_fraction())
            if degrade_reason is not None:
                self._pause(loop, degrade_reason)
                return

        # 3b. Dependency hygiene: cascade-cancel pending items whose deps
        #     failed/cancelled (they can never become ready). Without this the
        #     loop can deadlock silently: pending>0 keeps stall detection quiet
        #     while nothing is ever claimable.
        self.store.cancel_dead_loop_items(loop.id)

        # 4. No-progress detection: nothing pending AND nothing in flight → stall.
        #    A parallel worktree loop dispatches independent tasks that sit in
        #    CLAIMED/RUNNING (not PENDING); if a sibling is still executing the
        #    loop has NOT stalled — treating it as stalled injects spurious
        #    continuations and can false-pause a healthy loop (audit finding #4).
        #    Exclude this run's own item: it is mid-completion, not a live sibling.
        pending = self.store.count_pending_loop_items(loop.id)
        in_flight = self.store.count_active_loop_items(loop.id, exclude_run_id=run.id)
        if pending == 0 and in_flight == 0:
            # 4a. Event-reactive (watch) loops re-seed from external state instead
            #     of stalling: if the watch command reports work (exit 0), enqueue
            #     a fresh surveyor iteration carrying its output. Only when it
            #     reports "no work" (non-zero) do we fall through to stall/idle
            #     bounds — so a quiet watch loop still parks after max_stalls
            #     (resumable; re-arm with a schedule). Budgets already checked above.
            if loop.watch_cmd and await self._watch_reseed(loop):
                self.store.reset_loop_stall(loop.id)
                return
            new_stall = self.store.bump_loop_stall(loop.id)
            if new_stall < 0:
                return  # loop stopped concurrently — nothing to advance
            if new_stall > loop.max_stalls:
                self._pause(
                    loop,
                    f"stalled: {new_stall} consecutive iterations without follow-up tasks or completion",
                )
                return
            self._enqueue_continuation(loop)
            logger.info("Loop %s stalled (%d/%d); continuation enqueued", loop.id[:8], new_stall, loop.max_stalls)
        else:
            self.store.reset_loop_stall(loop.id)

    async def _resolve_completion_request(self, loop: AgentLoop, run: ExecutionRun) -> None:
        """Grant or deny the agent's completion request.

        Authority order: independent-verifier subagent (I2, when enabled and
        the requester was a WORK iteration) → deterministic ``verify_cmd`` gate
        → gateless agent's word. The verifier's own approval is not re-verified
        (recursion guard via VERIFICATION_MARKER), but the shell gate still
        runs beneath it.
        """
        if loop.agent_verifier and VERIFICATION_MARKER not in (run.prompt or ""):
            # A work iteration claimed completion: demote the request and hand
            # judgment to a FRESH verifier iteration (generator never grades its own
            # work). The verifier confirms via loop_complete or rejects by
            # enqueueing fix tasks. (The marker can't be forged: loop_enqueue_task
            # rejects agent prompts containing it, so only harness-authored verifier
            # prompts ever match.)
            self.store.set_loop_completion(loop.id, False, None)  # demote the claim (atomic)
            # This path skips the step-3 budget check, so enforce it here — without
            # it a claim-every-iteration loop would spawn verifier iterations forever.
            budget_reason = loop.budget_exhausted()
            if budget_reason is not None:
                self._pause(loop, budget_reason)
                return
            self._enqueue_verification(loop)
            logger.info("Loop %s: completion claim from run %s sent to independent verifier", loop.id[:8], run.id[:8])
            return

        if loop.agent_verifier and VERIFICATION_MARKER in (run.prompt or ""):
            # The independent verifier is reporting its verdict via loop_complete.
            # Parse the structured, fail-closed verdict from its summary (Phase E1)
            # and act on it — the verdict, not the mere fact it called
            # loop_complete, is authority. This closes "verifier theater": a
            # verifier that rubber-stamps with a malformed/non-pass verdict does
            # NOT complete the loop.
            verdict = parse_verifier_verdict(loop.completion_summary or "")
            self.store.set_loop_verdict(loop.id, verdict.to_json())
            if verdict.verdict == "escalate":
                # E2: the verifier can't decide safely → human handoff.
                self.store.set_loop_completion(loop.id, False, None)
                self._pause(
                    loop,
                    f"verifier escalated to human: {verdict.notes[:200] or 'see blocking_issues'}",
                )
                return
            if verdict.verdict != "pass":
                # revise, or fail-closed on an unparseable verdict.
                self.store.set_loop_completion(loop.id, False, None)
                logger.info(
                    "Loop %s: verifier verdict=%s (parsed=%s) — completion denied",
                    loop.id[:8],
                    verdict.verdict,
                    verdict.parsed,
                )
                budget_reason = loop.budget_exhausted()
                if budget_reason is not None:
                    self._pause(loop, budget_reason)
                    return
                # E5: pause if the verifier keeps rejecting for the SAME reasons.
                if verdict.parsed and self._register_gate_failure(
                    loop, "verifier:" + " | ".join(sorted(verdict.blocking_issues))
                ):
                    self._pause(
                        loop,
                        f"repeating failure: verifier rejected for the same reasons "
                        f"{loop.failure_sig_count} times — escalating for a human",
                    )
                    return
                self._enqueue_verifier_rejection(loop, verdict)
                return
            # verdict == "pass": fall through to the gate/gateless completion
            # below (the deterministic verify_cmd gate still runs beneath a
            # passing verifier — evidence plus the gate, not either alone).

        if not loop.verify_cmd:
            # Gateless: the agent's word is authority (graceful-gateless semantics).
            reason = (
                "objective met (independent verifier confirmed)"
                if loop.agent_verifier
                else "objective met (gateless — agent-declared)"
            )
            self._complete(loop, reason)
            return

        # For worktree loops the PROJECT checkout is authoritative: completed
        # tasks were merged back (Phase B integration), while any single run's
        # worktree only holds its own slice — judging the objective there would
        # wrongly fail on siblings' work. Non-worktree loops keep judging the
        # run's own directory (which IS the project checkout).
        if loop.use_worktree:
            project = self.store.get_project(run.project_id)
            cwd = str(project.expanded_path) if project else None
        else:
            cwd = self._gate_cwd(run)
        if cwd is None:
            self.store.set_loop_completion(loop.id, False, None)
            self._pause(loop, "completion requested but no directory to run verify_cmd in")
            return

        result = await asyncio.to_thread(run_gate, loop.verify_cmd, cwd)
        if result.passed:
            self.store.reset_loop_failure_signature(loop.id)
            self._complete(loop, "objective met; verify_cmd passed")
            return

        # Denied: clear the request, then — because this branch also skips the
        # step-3 budget check — enforce budgets BEFORE feeding the gate output into
        # another continuation. A denied completion is NOT progress, so stall_count
        # is deliberately left to keep climbing toward max_stalls (no false reset).
        logger.info("Loop %s completion DENIED — gate failed (exit %d)", loop.id[:8], result.exit_code)
        self.store.set_loop_completion(loop.id, False, None)
        budget_reason = loop.budget_exhausted()
        if budget_reason is not None:
            self._pause(loop, budget_reason)
            return
        # E5: pause if the objective gate keeps failing the SAME way.
        if self._register_gate_failure(loop, f"loopgate:{result.output}"):
            self._pause(
                loop,
                f"repeating failure: objective gate `{loop.verify_cmd}` failed the same way "
                f"{loop.failure_sig_count} times — a different approach is needed",
            )
            return
        self._enqueue_continuation(loop, gate_failure=result.output)

    # ---------- manual controls ----------

    def pause_loop(self, loop_id: str, reason: str = "paused by user") -> AgentLoop | None:
        # Atomic + guarded on RUNNING so a concurrent worker advance can't undo it.
        self.store.try_transition_loop(loop_id, LoopStatus.PAUSED, reason=reason, expect_status=LoopStatus.RUNNING)
        return self.store.get_agent_loop(loop_id)

    def resume_loop(self, loop_id: str) -> AgentLoop | None:
        """Resume a paused loop; re-seed a continuation if nothing is pending."""
        loop = self.store.get_agent_loop(loop_id)
        if loop is None or loop.status != LoopStatus.PAUSED:
            return loop
        # Clear any completion request captured before the pause — otherwise the
        # first post-resume iteration could fire an unverified completion off a
        # superseded/failed iteration. Then flip PAUSED -> RUNNING atomically.
        self.store.set_loop_completion(loop_id, False, None, expect_status=LoopStatus.PAUSED)
        if not self.store.try_transition_loop(
            loop_id, LoopStatus.RUNNING, reason=None, expect_status=LoopStatus.PAUSED
        ):
            return self.store.get_agent_loop(loop_id)  # lost a race (e.g. cancel)
        self.store.reset_loop_stall(loop_id)
        # Dependency hygiene on resume: drop pending items whose deps failed
        # before the pause — otherwise they'd block the ready-set forever while
        # keeping stall detection quiet (silent deadlock).
        self.store.cancel_dead_loop_items(loop_id)
        if self.store.count_pending_loop_items(loop_id) == 0:
            fresh = self.store.get_agent_loop(loop_id)
            if fresh is not None:
                self._enqueue_continuation(fresh)
        logger.info("Resumed agent loop %s", loop_id[:8])
        return self.store.get_agent_loop(loop_id)

    def cancel_loop(self, loop_id: str, reason: str = "cancelled by user") -> AgentLoop | None:
        loop = self.store.get_agent_loop(loop_id)
        if loop is None or loop.status not in (LoopStatus.RUNNING, LoopStatus.PAUSED):
            return loop
        # Atomically cancel from whichever active status it is currently in.
        cancelled_ok = self.store.try_transition_loop(
            loop_id, LoopStatus.CANCELLED, reason=reason, expect_status=LoopStatus.RUNNING
        ) or self.store.try_transition_loop(
            loop_id, LoopStatus.CANCELLED, reason=reason, expect_status=LoopStatus.PAUSED
        )
        if cancelled_ok:
            dropped = self.store.cancel_pending_loop_items(loop_id)
            logger.info("Cancelled agent loop %s (%d pending tasks dropped)", loop_id[:8], dropped)
        return self.store.get_agent_loop(loop_id)

    # ---------- internals ----------

    def _pause(self, loop: AgentLoop, reason: str) -> None:
        """PAUSED preserves pending tasks (claim_work keeps them inert). Atomic +
        guarded on RUNNING so we never clobber a concurrent cancel/complete."""
        self.store.try_transition_loop(loop.id, LoopStatus.PAUSED, reason=reason, expect_status=LoopStatus.RUNNING)
        logger.info("Paused agent loop %s: %s", loop.id[:8], reason)

    def _complete(self, loop: AgentLoop, reason: str) -> None:
        # Atomic RUNNING -> COMPLETED; only drop pending tasks if we actually won
        # the transition (else a concurrent pause/cancel already owns the loop).
        if self.store.try_transition_loop(
            loop.id, LoopStatus.COMPLETED, reason=reason, expect_status=LoopStatus.RUNNING, set_completed_at=True
        ):
            dropped = self.store.cancel_pending_loop_items(loop.id)
            logger.info("Completed agent loop %s: %s (%d stale tasks dropped)", loop.id[:8], reason, dropped)

    def _enqueue_verification(self, loop: AgentLoop) -> None:
        """Enqueue the independent-verifier iteration (I2).

        Dispatches ahead of other loop tasks (priority) so the verdict lands
        before more work piles onto a possibly-complete loop. Harness-authored:
        bypasses dedup; the iteration number keeps repeated verifications
        distinct.
        """
        prompt = _VERIFICATION_PROMPT_TEMPLATE.format(
            marker=VERIFICATION_MARKER,
            iteration=loop.iteration_count + 1,
            objective=loop.objective,
            summary=loop.completion_summary or "(no summary provided)",
            ledger=self._attempt_ledger(loop),
        )
        self.store.enqueue_work(
            project_id=loop.project_id,
            prompt=prompt,
            profile=loop.profile,
            priority=LOOP_TASK_PRIORITY - 2,
            loop_id=loop.id,
            source="verifier",
        )

    def _enqueue_verifier_rejection(self, loop: AgentLoop, verdict: VerifierVerdict) -> None:
        """Harness-authored fix work from a verifier's ``revise`` verdict (Phase E1).

        The verifier no longer enqueues tasks itself; it reports blocking issues
        and the harness authors one self-contained fix task per issue (bypassing
        dedup, iteration-numbered to stay distinct). With no issues listed — e.g.
        a fail-closed unparseable verdict — a single continuation is enqueued so
        the loop makes another attempt rather than silently stalling.
        """
        issues = verdict.blocking_issues
        if not issues:
            self._enqueue_continuation(loop)
            return
        for idx, issue in enumerate(issues, 1):
            prompt = (
                f"[VERIFIER REJECTION — iteration {loop.iteration_count + 1}, issue {idx}/{len(issues)}] "
                f"The independent verifier rejected the completion of this loop. Fix this specific "
                f"blocking issue, self-contained (you have no memory of prior sessions):\n\n{issue}\n\n"
                f"Objective for context:\n{loop.objective}\n\n"
                f"Make the smallest change that resolves the issue; verify it; do not call loop_complete "
                f"unless the whole loop objective is also met."
            )
            self.store.enqueue_work(
                project_id=loop.project_id,
                prompt=prompt,
                profile=loop.profile,
                priority=LOOP_TASK_PRIORITY - 1,  # ahead of new fan-out
                loop_id=loop.id,
                source="continuation",
            )

    def _attempt_ledger(self, loop: AgentLoop, limit: int = 6) -> str:
        """A compact, deterministic digest of recent iteration outcomes (Phase E5).

        Injected into verifier/continuation/fix prompts so an iteration can see
        what was already tried and how it failed — cheap (no LLM), and the
        countermeasure to a loop repeating the same failed approach. Returns ""
        when there is no useful history (first iterations).
        """
        runs = self.store.list_runs_for_loop(loop.id, limit=limit + 1)
        # Drop the run currently being processed / the in-flight verifier itself;
        # keep prior terminal attempts.
        past = [r for r in runs if r.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)][:limit]
        if not past:
            return ""
        lines = ["", "Prior attempts (most recent first — do not repeat what already failed):"]
        for r in past:
            cost = f"${r.cost_usd:.2f}" if r.cost_usd else "$0.00"
            tail = ""
            if r.status == RunStatus.FAILED and r.error_message:
                tail = f" — {r.error_message.strip().splitlines()[-1][:120]}"
            title = (r.custom_title or r.prompt or "").strip().splitlines()[0][:80]
            lines.append(f"- [{r.id[:8]}] {r.status.value} {cost} {title}{tail}")
        if loop.failure_sig_count >= 2 and loop.last_failure_sig:
            lines.append(
                f"- NOTE: the same failure signature has repeated {loop.failure_sig_count}× — "
                f"a different approach is required, not another attempt at the same fix."
            )
        return "\n".join(lines) + "\n"

    def _register_gate_failure(self, loop: AgentLoop, failure_output: str) -> bool:
        """Record a gate-failure signature and report whether the loop should
        pause for repeating the SAME failure (Phase E5).

        Returns True when the identical failure signature has now occurred
        ``max_stalls`` times in a row — the caller should pause. This catches
        the repeat-failure blind spot that emptiness-based stall detection
        misses (harness fix tasks embed iteration numbers, so a loop failing the
        same way forever never trips the ordinary stall counter).
        """
        norm = re.sub(r"\s+", " ", (failure_output or "").strip()).lower()
        sig = hashlib.sha256(norm.encode()).hexdigest()[:16] if norm else "empty"
        count = self.store.record_loop_failure_signature(loop.id, sig)
        return count >= max(2, loop.max_stalls)

    async def _watch_reseed(self, loop: AgentLoop) -> bool:
        """Event-reactive re-seed: run the watch command; on exit 0 enqueue a
        fresh surveyor iteration carrying its output. Returns True iff work was
        re-seeded (caller then resets the stall counter and returns).

        Exit != 0 (or a missing project dir) means "no work right now" → return
        False so the caller applies the normal stall/idle bounds. Never raises:
        run_gate reports timeouts/spawn failures as not-passed. The watch command
        runs on a worker thread (audit finding #5): a slow/hung watch_cmd must not
        block the runner's event loop while on_run_completed is advancing.
        """
        project = self.store.get_project(loop.project_id)
        if project is None or not loop.watch_cmd:
            return False
        result = await asyncio.to_thread(run_gate, loop.watch_cmd, str(project.expanded_path))
        if not result.passed:
            logger.info("Loop %s: watch reported no work (exit %d) — idling", loop.id[:8], result.exit_code)
            return False
        prompt = _WATCH_SEED_TEMPLATE.format(
            iteration=loop.iteration_count + 1,
            objective=loop.objective,
            watch_cmd=loop.watch_cmd,
            output=result.output or "(watch command produced no output)",
        )
        self.store.enqueue_work(
            project_id=loop.project_id,
            prompt=prompt,
            profile=loop.profile,
            priority=LOOP_TASK_PRIORITY,
            loop_id=loop.id,
            source="seed",  # a reactive surveyor cycle — L1/L2 re-approve its plan
        )
        logger.info("Loop %s: watch fired — re-seeded a reactive surveyor iteration", loop.id[:8])
        return True

    def _enqueue_continuation(self, loop: AgentLoop, gate_failure: str | None = None) -> None:
        """Harness-authored recovery iteration (stall or denied completion).

        Embeds the iteration number so repeated continuations stay distinct;
        harness items bypass the agent-path dedup by design.
        """
        gate_block = ""
        if gate_failure is not None:
            gate_block = _GATE_DENIED_BLOCK.format(verify_cmd=loop.verify_cmd, output=gate_failure)
        prompt = _CONTINUATION_PROMPT_TEMPLATE.format(
            iteration=loop.iteration_count + 1,
            objective=loop.objective,
            gate_block=gate_block,
            ledger=self._attempt_ledger(loop),
        )
        self.store.enqueue_work(
            project_id=loop.project_id,
            prompt=prompt,
            profile=loop.profile,
            priority=LOOP_TASK_PRIORITY,
            loop_id=loop.id,
            source="continuation",
        )

    def _gate_cwd(self, run: ExecutionRun) -> str | None:
        """Directory for the loop-level gate: the run's worktree, else the project."""
        if run.use_worktree and run.worktree_path:
            return str(run.worktree_path)
        project = self.store.get_project(run.project_id)
        return str(project.expanded_path) if project else None

    def _task_gate_for_run(self, run: ExecutionRun) -> _TaskGate | None:
        """The task-level gate of the queue item this run executed, if any.

        The run's originating item is linked via ``initiator`` ("queue:<id>" /
        "queue_drain:<id>") — no extra plumbing. Only item-level verify_cmds
        count: the loop-level gate is judged separately at the completion
        handshake, and inherited run.verify_cmd would wrongly gate every
        intermediate iteration.
        """
        item = self._item_for_run(run)
        if item is None or not item.verify_cmd:
            return None
        return _TaskGate(item_id=item.id, cmd=item.verify_cmd, task_prompt=item.prompt)

    def _item_for_run(self, run: ExecutionRun):
        """The work-queue item this run was dispatched from (via ``initiator``)."""
        initiator = run.initiator or ""
        if not (initiator.startswith("queue:") or initiator.startswith("queue_drain:")):
            return None
        item = self.store.get_work_item(initiator.split(":", 1)[1])
        if item is None or item.loop_id != run.loop_id:
            return None
        return item

    def _is_seed_run(self, run: ExecutionRun) -> bool:
        """True when this run executed the loop's SURVEYOR (seed) item."""
        item = self._item_for_run(run)
        return item is not None and item.source == "seed"

    def _enqueue_integration_fix(self, loop: AgentLoop, run: ExecutionRun, conflict_detail: str) -> None:
        """Harness-authored task to resolve a merge conflict from integration.

        The resolution task runs like any loop task (fresh worktree branched
        from the current source): the agent merges the conflicted branch there,
        resolves, and its own completion is integrated back — pure graph flow,
        no special executor. Harness-authored → bypasses dedup.
        """
        prompt = _INTEGRATION_FIX_TEMPLATE.format(
            iteration=loop.iteration_count + 1,
            branch=run.branch_name or "(unknown branch)",
            source=run.source_branch or "the project's main branch",
            detail=conflict_detail or "(no detail captured)",
        )
        self.store.enqueue_work(
            project_id=loop.project_id,
            prompt=prompt,
            profile=loop.profile,
            priority=LOOP_TASK_PRIORITY - 1,  # resolve before new fan-out work
            loop_id=loop.id,
            source="continuation",
        )

    def _enqueue_task_fix(self, loop: AgentLoop, gate: _TaskGate, gate_output: str) -> None:
        """Harness-authored fix task for a failed task-level gate.

        Targeted evaluator-optimizer at the task scope: carries the original
        task, the gate command, and its failing output; keeps the same task
        gate so the fix is judged by the same standard. Harness-authored →
        bypasses dedup (embeds iteration number to stay distinct).
        """
        prompt = _TASK_GATE_FIX_TEMPLATE.format(
            iteration=loop.iteration_count + 1,
            task_prompt=gate.task_prompt,
            verify_cmd=gate.cmd,
            output=gate_output,
        )
        self.store.enqueue_work(
            project_id=loop.project_id,
            prompt=prompt,
            profile=loop.profile,
            priority=LOOP_TASK_PRIORITY - 1,  # fix ahead of new fan-out work
            loop_id=loop.id,
            source="continuation",
            verify_cmd=gate.cmd,
        )
