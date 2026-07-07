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
import logging
from dataclasses import dataclass
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
3. If the objective is genuinely a single small task, you may instead do it now
   and call loop_complete. If your survey finds the objective already met,
   call loop_complete with the evidence.

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

_CONTINUATION_PROMPT_TEMPLATE = """[LOOP CONTINUATION — iteration {iteration}] The loop objective is not yet
complete and no follow-up tasks are pending.{gate_block}

Objective:
{objective}

Assess what remains, then EITHER do the next focused slice of work and enqueue
follow-up task(s) via loop_enqueue_task, OR call loop_complete if the objective
is fully met."""

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
that work — judge it skeptically, on the evidence in the repository, not on
the claim.

Objective:
{objective}

Claimed completion summary:
{summary}

Verify the objective is genuinely met: read the relevant code/artifacts, run
tests or checks where they exist, and look specifically for gaps between the
claim and reality (missing acceptance criteria, placeholder work, untested
paths). Then issue your verdict via the gluon-loop MCP tools:

- CONFIRM — call loop_complete with your own independent assessment of what
  was verified and how.
- REJECT — call loop_enqueue_task with specific, self-contained fix task(s)
  for each gap you found. Do NOT call loop_complete.

Report gaps that affect correctness or the stated objective, not style
preferences."""


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
        profile: str = "standard",
        model: str | None = None,
        use_worktree: bool = False,
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
        # A blank/whitespace verify_cmd is not a gate — normalize to gateless
        # rather than silently running an empty shell command that always exits 0
        # (a false "gated" loop that grants every completion).
        if verify_cmd is not None and not verify_cmd.strip():
            verify_cmd = None

        loop = AgentLoop(
            project_id=project_id,
            objective=objective,
            verify_cmd=verify_cmd,
            agent_verifier=agent_verifier,
            profile=profile,
            model=model,
            use_worktree=use_worktree,
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
                    self._enqueue_task_fix(loop, task_gate, result.output)
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

        # 3b. Dependency hygiene: cascade-cancel pending items whose deps
        #     failed/cancelled (they can never become ready). Without this the
        #     loop can deadlock silently: pending>0 keeps stall detection quiet
        #     while nothing is ever claimable.
        self.store.cancel_dead_loop_items(loop.id)

        # 4. No-progress detection: nothing pending → stall.
        pending = self.store.count_pending_loop_items(loop.id)
        if pending == 0:
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

        if not loop.verify_cmd:
            # Gateless: the agent's word is authority (graceful-gateless semantics).
            reason = (
                "objective met (independent verifier confirmed)"
                if loop.agent_verifier
                else "objective met (gateless — agent-declared)"
            )
            self._complete(loop, reason)
            return

        cwd = self._gate_cwd(run)
        if cwd is None:
            self.store.set_loop_completion(loop.id, False, None)
            self._pause(loop, "completion requested but no directory to run verify_cmd in")
            return

        result = await asyncio.to_thread(run_gate, loop.verify_cmd, cwd)
        if result.passed:
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
        )
        self.store.enqueue_work(
            project_id=loop.project_id,
            prompt=prompt,
            profile=loop.profile,
            priority=LOOP_TASK_PRIORITY - 2,
            loop_id=loop.id,
            source="verifier",
        )

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
        initiator = run.initiator or ""
        if not (initiator.startswith("queue:") or initiator.startswith("queue_drain:")):
            return None
        item = self.store.get_work_item(initiator.split(":", 1)[1])
        if item is None or not item.verify_cmd or item.loop_id != run.loop_id:
            return None
        return _TaskGate(item_id=item.id, cmd=item.verify_cmd, task_prompt=item.prompt)

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
