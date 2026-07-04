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
from typing import TYPE_CHECKING

from gluon.gate import run_gate
from gluon.models import AgentLoop, ExecutionRun, LoopStatus, RunStatus

if TYPE_CHECKING:
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Loop tasks dispatch ahead of default queue items (priority 10) so an active
# loop keeps momentum, but behind anything an operator marks truly urgent.
LOOP_TASK_PRIORITY = 5

_SEED_PROMPT_TEMPLATE = """This is iteration 1 of a new agent loop.

Objective:
{objective}

Assess the project state relative to the objective, then do the FIRST focused
slice of work toward it. Before finishing you MUST either enqueue the next
task(s) with the loop_enqueue_task tool, or call loop_complete if the objective
is already fully met."""

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
        """
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
        loop = self.store.get_agent_loop(run.loop_id)
        if loop is None:
            logger.warning("Run %s references unknown loop %s", run.id[:8], run.loop_id[:8])
            return
        if loop.status != LoopStatus.RUNNING:
            return  # Loop already stopped (raced with a manual pause/cancel)

        loop.iteration_count += 1
        loop.total_cost_usd += run.cost_usd or 0.0

        # 1. Fail-safe: a failed/cancelled iteration pauses the loop for a human.
        if run.status == RunStatus.FAILED:
            self._pause(loop, f"iteration run {run.id[:8]} failed: {(run.error_message or 'unknown error')[:200]}")
            return
        if run.status == RunStatus.CANCELLED:
            self._pause(loop, f"iteration run {run.id[:8]} was cancelled")
            return

        # 2. Completion handshake — the gate, not the agent, is authority.
        if loop.completion_requested:
            await self._resolve_completion_request(loop, run)
            return

        # 3. Budgets: pause (not fail) so a human can extend and resume.
        budget_reason = loop.budget_exhausted()
        if budget_reason is not None:
            self._pause(loop, budget_reason)
            return

        # 4. No-progress detection: nothing pending → stall.
        pending = self.store.count_pending_loop_items(loop.id)
        if pending == 0:
            loop.stall_count += 1
            if loop.stall_count > loop.max_stalls:
                self._pause(
                    loop,
                    f"stalled: {loop.stall_count} consecutive iterations without follow-up tasks or completion",
                )
                return
            self._enqueue_continuation(loop)
            logger.info(
                "Loop %s stalled (%d/%d); continuation enqueued", loop.id[:8], loop.stall_count, loop.max_stalls
            )
        else:
            loop.stall_count = 0

        self.store.update_agent_loop(loop)

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
            # judgment to a FRESH verifier iteration (generator never grades
            # its own work). The verifier confirms via loop_complete or
            # rejects by enqueueing fix tasks.
            loop.completion_requested = False
            loop.stall_count = 0
            self._enqueue_verification(loop)
            self.store.update_agent_loop(loop)
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
            self._pause(loop, "completion requested but no directory to run verify_cmd in")
            return

        result = await asyncio.to_thread(run_gate, loop.verify_cmd, cwd)
        if result.passed:
            self._complete(loop, "objective met; verify_cmd passed")
            return

        # Denied: demote the request and feed the gate output into a
        # continuation iteration (evaluator-optimizer at the loop level).
        logger.info("Loop %s completion DENIED — gate failed (exit %d)", loop.id[:8], result.exit_code)
        loop.completion_requested = False
        loop.completion_summary = None
        loop.stall_count = 0
        self._enqueue_continuation(loop, gate_failure=result.output)
        self.store.update_agent_loop(loop)

    # ---------- manual controls ----------

    def pause_loop(self, loop_id: str, reason: str = "paused by user") -> AgentLoop | None:
        loop = self.store.get_agent_loop(loop_id)
        if loop is None or loop.status != LoopStatus.RUNNING:
            return loop
        self._pause(loop, reason)
        return loop

    def resume_loop(self, loop_id: str) -> AgentLoop | None:
        """Resume a paused loop; re-seed a continuation if nothing is pending."""
        loop = self.store.get_agent_loop(loop_id)
        if loop is None or loop.status != LoopStatus.PAUSED:
            return loop
        loop.status = LoopStatus.RUNNING
        loop.status_reason = None
        loop.stall_count = 0
        if self.store.count_pending_loop_items(loop.id) == 0:
            self._enqueue_continuation(loop)
        self.store.update_agent_loop(loop)
        logger.info("Resumed agent loop %s", loop.id[:8])
        return loop

    def cancel_loop(self, loop_id: str, reason: str = "cancelled by user") -> AgentLoop | None:
        loop = self.store.get_agent_loop(loop_id)
        if loop is None or loop.status not in (LoopStatus.RUNNING, LoopStatus.PAUSED):
            return loop
        loop.status = LoopStatus.CANCELLED
        loop.status_reason = reason
        cancelled = self.store.cancel_pending_loop_items(loop.id)
        self.store.update_agent_loop(loop)
        logger.info("Cancelled agent loop %s (%d pending tasks dropped)", loop.id[:8], cancelled)
        return loop

    # ---------- internals ----------

    def _pause(self, loop: AgentLoop, reason: str) -> None:
        """PAUSED preserves pending tasks (claim_work keeps them inert)."""
        loop.status = LoopStatus.PAUSED
        loop.status_reason = reason
        self.store.update_agent_loop(loop)
        logger.info("Paused agent loop %s: %s", loop.id[:8], reason)

    def _complete(self, loop: AgentLoop, reason: str) -> None:
        from gluon.models import utc_now

        loop.status = LoopStatus.COMPLETED
        loop.status_reason = reason
        loop.completed_at = utc_now()
        cancelled = self.store.cancel_pending_loop_items(loop.id)
        self.store.update_agent_loop(loop)
        logger.info("Completed agent loop %s: %s (%d stale tasks dropped)", loop.id[:8], reason, cancelled)

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
