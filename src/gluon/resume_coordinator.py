"""Resume coordinator for supervision-based auto-resume.

Centralizes logic for evaluating REVIEW tasks and deciding whether to
auto-resume based on configured policies and safety guards.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gluon.models import (
    ExecutionRun,
    RunStatus,
    SupervisionDecision,
    SupervisionPolicy,
)
from gluon.policies import PolicyContext, PolicyDecision, evaluate_policy, get_supervision_config

if TYPE_CHECKING:
    from gluon.runner import TaskRunner
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Default polling interval in seconds
DEFAULT_POLL_INTERVAL = 30


class ResumeCoordinator:
    """Coordinates auto-resume decisions for supervised tasks.

    Polls REVIEW tasks, evaluates policies, and auto-resumes when appropriate.
    Maintains audit trail of all decisions.
    """

    def __init__(
        self,
        store: "GluonStore",
        runner: "TaskRunner",
        poll_interval: int = DEFAULT_POLL_INTERVAL,
    ):
        self.store = store
        self.runner = runner
        self.poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the supervision background polling loop."""
        if self._running:
            logger.warning("ResumeCoordinator already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"ResumeCoordinator started with {self.poll_interval}s interval")

    async def stop(self) -> None:
        """Stop the supervision polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ResumeCoordinator stopped")

    @property
    def is_running(self) -> bool:
        """Check if coordinator is running."""
        return self._running

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self.evaluate_candidates()
            except Exception as e:
                logger.error(f"Error in supervision poll: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    async def evaluate_candidates(self) -> list[tuple[str, PolicyDecision]]:
        """Evaluate all REVIEW tasks for auto-resume.

        Returns:
            List of (run_id, decision) tuples for tasks that were evaluated
        """
        results = []

        # Find all REVIEW tasks
        candidates = self._get_review_candidates()

        for run in candidates:
            try:
                decision = await self.evaluate_run(run)
                results.append((run.id, decision))
            except Exception as e:
                logger.error(f"Error evaluating run {run.id[:8]}: {e}", exc_info=True)

        return results

    def _get_review_candidates(self) -> list[ExecutionRun]:
        """Get all runs in REVIEW status that are eligible for supervision."""
        # Get all runs in REVIEW status
        all_runs = self.store.list_runs()
        candidates = []

        for run in all_runs:
            if run.status != RunStatus.REVIEW:
                continue

            # Skip if supervision explicitly disabled
            config = get_supervision_config(run)
            if not config.enabled:
                continue

            # Skip manual policy (let user handle)
            if config.policy == SupervisionPolicy.MANUAL:
                continue

            # Skip if no session ID (can't resume)
            if not run.claude_session_id:
                continue

            candidates.append(run)

        return candidates

    async def evaluate_run(self, run: ExecutionRun, trigger: str = "scheduler") -> PolicyDecision:
        """Evaluate a single run for auto-resume.

        Args:
            run: ExecutionRun to evaluate
            trigger: What triggered this evaluation

        Returns:
            PolicyDecision with the decision made
        """
        config = get_supervision_config(run)
        now = datetime.now(UTC)

        # Build policy context
        ctx = PolicyContext(
            run=run,
            circuit_state=run.circuit_state,
            calls_this_hour=run.calls_this_hour,
            max_calls_per_hour=run.max_calls_per_hour,
            total_cost_usd=run.cost_usd or 0.0,
            max_cost_usd=run.max_cost_usd,
            completion_confidence=run.completion_confidence,
            now=now,
        )

        # Evaluate policy
        decision = evaluate_policy(ctx)

        # Record decision
        supervision_decision = SupervisionDecision(
            run_id=run.id,
            timestamp=now,
            decision="resume" if decision.should_resume else "skip",
            reason=decision.reason,
            trigger=trigger,
            circuit_state=run.circuit_state,
            completion_confidence=run.completion_confidence,
            calls_this_hour=run.calls_this_hour,
            cost_usd=run.cost_usd,
            auto_resume_count=run.supervision_auto_resume_count,
            policy=config.policy,
        )
        self.store.create_supervision_decision(supervision_decision)

        # Update run's last check timestamp
        run.last_supervision_check_at = now
        self.store.update_run(run)

        # Execute resume if decision is positive
        if decision.should_resume:
            await self._execute_resume(run, decision, trigger)

        logger.info(
            f"Supervision decision for {run.id[:8]}: "
            f"{'RESUME' if decision.should_resume else 'SKIP'} - {decision.reason}"
        )

        return decision

    async def _execute_resume(self, run: ExecutionRun, decision: PolicyDecision, trigger: str) -> None:
        """Execute auto-resume for a run.

        Args:
            run: ExecutionRun to resume
            decision: PolicyDecision that triggered resume
            trigger: What triggered this resume
        """
        now = datetime.now(UTC)

        # Build resume prompt with context
        resume_prompt = self._build_resume_prompt(run, trigger)

        try:
            # Update tracking before resume
            run.supervision_auto_resume_count += 1
            run.last_supervision_resume_at = now
            self.store.update_run(run)

            # Execute resume via runner
            await self.runner.resume_in_place(
                run_id=run.id,
                new_prompt=resume_prompt,
                wait=False,  # Don't block coordinator
                initiator=f"supervision:{trigger}",
            )

            logger.info(f"Auto-resumed run {run.id[:8]} (attempt #{run.supervision_auto_resume_count})")

        except Exception as e:
            logger.error(f"Failed to auto-resume run {run.id[:8]}: {e}")
            # Record failure
            failure_decision = SupervisionDecision(
                run_id=run.id,
                timestamp=datetime.now(UTC),
                decision="resume_failed",
                reason=str(e),
                trigger=trigger,
                circuit_state=run.circuit_state,
                completion_confidence=run.completion_confidence,
                calls_this_hour=run.calls_this_hour,
                cost_usd=run.cost_usd,
                auto_resume_count=run.supervision_auto_resume_count,
                policy=get_supervision_config(run).policy,
            )
            self.store.create_supervision_decision(failure_decision)

    def _build_resume_prompt(self, run: ExecutionRun, trigger: str) -> str:
        """Build prompt for auto-resume with context.

        Args:
            run: ExecutionRun being resumed
            trigger: What triggered this resume

        Returns:
            Prompt string for resume
        """
        context_parts = [
            "[SUPERVISION AUTO-RESUME]",
            f"Resume attempt: #{run.supervision_auto_resume_count + 1}",
            f"Trigger: {trigger}",
        ]

        # Add completion reason if available
        if run.completion_reason:
            context_parts.append(f"Previous exit: {run.completion_reason}")

        # Add progress info
        if run.loop_count > 0:
            context_parts.append(f"Loops completed: {run.loop_count}")

        if run.completion_confidence > 0:
            context_parts.append(f"Last confidence: {run.completion_confidence:.0f}%")

        context = "\n".join(context_parts)

        # Use original_prompt if available (preserved across resumes), else fall back to prompt
        task_prompt = run.original_prompt or run.prompt

        # Prepend context to original prompt
        return f"{context}\n\n---\n\nContinue with the original task:\n\n{task_prompt}"

    async def disable_supervision(self, run_id: str, reason: str) -> bool:
        """Disable supervision for a specific run.

        Args:
            run_id: ID of run to disable
            reason: Why supervision is being disabled

        Returns:
            True if disabled, False if run not found
        """
        run = self.store.get_run(run_id)
        if not run:
            return False

        # Update config to disabled
        config = get_supervision_config(run)
        config.enabled = False
        run.supervision_config = config
        run.supervision_disabled_reason = reason
        self.store.update_run(run)

        # Record decision
        decision = SupervisionDecision(
            run_id=run.id,
            timestamp=datetime.now(UTC),
            decision="disable",
            reason=reason,
            trigger="manual",
            circuit_state=run.circuit_state,
            completion_confidence=run.completion_confidence,
            calls_this_hour=run.calls_this_hour,
            cost_usd=run.cost_usd,
            auto_resume_count=run.supervision_auto_resume_count,
            policy=config.policy,
        )
        self.store.create_supervision_decision(decision)

        logger.info(f"Supervision disabled for run {run_id[:8]}: {reason}")
        return True

    def get_supervision_status(self, run_id: str) -> dict | None:
        """Get supervision status for a run.

        Args:
            run_id: ID of run to check

        Returns:
            Dict with supervision status, or None if run not found
        """
        run = self.store.get_run(run_id)
        if not run:
            return None

        config = get_supervision_config(run)
        decisions = self.store.list_supervision_decisions(run_id, limit=10)

        return {
            "run_id": run.id,
            "enabled": config.enabled,
            "policy": config.policy.value,
            "max_auto_resumes": config.max_auto_resumes,
            "auto_resume_count": run.supervision_auto_resume_count,
            "last_check_at": run.last_supervision_check_at.isoformat() if run.last_supervision_check_at else None,
            "last_resume_at": run.last_supervision_resume_at.isoformat() if run.last_supervision_resume_at else None,
            "disabled_reason": run.supervision_disabled_reason,
            "recent_decisions": [
                {
                    "timestamp": d.timestamp.isoformat(),
                    "decision": d.decision,
                    "reason": d.reason,
                    "trigger": d.trigger,
                }
                for d in decisions
            ],
        }


# Singleton instance for use in web server
_coordinator: ResumeCoordinator | None = None


def get_coordinator() -> ResumeCoordinator | None:
    """Get the singleton coordinator instance."""
    return _coordinator


def set_coordinator(coordinator: ResumeCoordinator) -> None:
    """Set the singleton coordinator instance."""
    global _coordinator
    _coordinator = coordinator


async def start_coordinator(store: "GluonStore", runner: "TaskRunner") -> ResumeCoordinator:
    """Create and start a coordinator instance.

    Args:
        store: GluonStore for database access
        runner: TaskRunner for executing resumes

    Returns:
        Started ResumeCoordinator instance
    """
    coordinator = ResumeCoordinator(store, runner)
    await coordinator.start()
    set_coordinator(coordinator)
    return coordinator


async def stop_coordinator() -> None:
    """Stop the singleton coordinator if running."""
    global _coordinator
    if _coordinator:
        await _coordinator.stop()
        _coordinator = None
