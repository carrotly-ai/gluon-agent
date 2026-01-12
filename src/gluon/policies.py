"""Supervision policy engine for auto-resume decisions.

Provides policy implementations that determine whether a task should be
auto-resumed based on its current state and configured policy.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from gluon.models import (
    CircuitState,
    ExecutionRun,
    RunStatus,
    SupervisionConfig,
    SupervisionPolicy,
)


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""

    should_resume: bool
    reason: str
    wait_seconds: int = 0  # If > 0, retry after this many seconds


@dataclass
class PolicyContext:
    """Context for policy evaluation."""

    run: ExecutionRun
    circuit_state: CircuitState
    calls_this_hour: int
    max_calls_per_hour: int
    total_cost_usd: float
    max_cost_usd: float | None
    completion_confidence: float
    now: datetime


def get_supervision_config(run: ExecutionRun) -> SupervisionConfig:
    """Get supervision config for a run, using defaults if not set."""
    return run.supervision_config or SupervisionConfig()


def evaluate_policy(ctx: PolicyContext) -> PolicyDecision:
    """Evaluate supervision policy and return decision.

    Args:
        ctx: PolicyContext with run state and safety metrics

    Returns:
        PolicyDecision indicating whether to resume and why
    """
    config = get_supervision_config(ctx.run)

    # Check if supervision is disabled
    if not config.enabled:
        return PolicyDecision(
            should_resume=False,
            reason="Supervision disabled for this task",
        )

    # Manual policy never auto-resumes
    if config.policy == SupervisionPolicy.MANUAL:
        return PolicyDecision(
            should_resume=False,
            reason="Manual policy - no auto-resume",
        )

    # Safety checks (apply to all policies)
    safety_result = _check_safety_guards(ctx, config)
    if safety_result is not None:
        return safety_result

    # Policy-specific evaluation
    if config.policy == SupervisionPolicy.AGGRESSIVE:
        return _evaluate_aggressive(ctx, config)
    elif config.policy == SupervisionPolicy.CONSERVATIVE:
        return _evaluate_conservative(ctx, config)
    else:
        return PolicyDecision(
            should_resume=False,
            reason=f"Unknown policy: {config.policy}",
        )


def _check_safety_guards(ctx: PolicyContext, config: SupervisionConfig) -> PolicyDecision | None:
    """Check safety guards that apply to all policies.

    Returns PolicyDecision if guard blocks resume, None if safe to continue.
    """
    # Circuit breaker check
    if ctx.circuit_state == CircuitState.OPEN:
        return PolicyDecision(
            should_resume=False,
            reason="Circuit breaker OPEN - execution halted",
        )

    # Max auto-resumes check
    if ctx.run.supervision_auto_resume_count >= config.max_auto_resumes:
        return PolicyDecision(
            should_resume=False,
            reason=f"Max auto-resumes ({config.max_auto_resumes}) reached",
        )

    # Cost cap check
    if ctx.max_cost_usd and ctx.total_cost_usd >= ctx.max_cost_usd:
        return PolicyDecision(
            should_resume=False,
            reason=f"Cost cap reached: ${ctx.total_cost_usd:.2f} >= ${ctx.max_cost_usd:.2f}",
        )

    # Rate limit check
    if ctx.calls_this_hour >= ctx.max_calls_per_hour:
        # Calculate wait time until next hour
        wait_seconds = 3600 - (ctx.now.minute * 60 + ctx.now.second)
        return PolicyDecision(
            should_resume=False,
            reason=f"Rate limit: {ctx.calls_this_hour}/{ctx.max_calls_per_hour} calls/hour",
            wait_seconds=wait_seconds,
        )

    # Minimum time between resumes
    if ctx.run.last_supervision_resume_at:
        elapsed = (ctx.now - ctx.run.last_supervision_resume_at).total_seconds()
        if elapsed < config.min_time_between_resumes:
            wait_seconds = int(config.min_time_between_resumes - elapsed)
            return PolicyDecision(
                should_resume=False,
                reason=f"Too soon since last resume ({elapsed:.0f}s < {config.min_time_between_resumes}s)",
                wait_seconds=wait_seconds,
            )

    # Status check - must be in REVIEW to auto-resume
    if ctx.run.status != RunStatus.REVIEW:
        return PolicyDecision(
            should_resume=False,
            reason=f"Run status is {ctx.run.status.value}, not REVIEW",
        )

    # Must have Claude session ID to resume
    if not ctx.run.claude_session_id:
        return PolicyDecision(
            should_resume=False,
            reason="No Claude session ID - cannot resume",
        )

    return None  # All safety guards passed


def _evaluate_aggressive(ctx: PolicyContext, config: SupervisionConfig) -> PolicyDecision:
    """Aggressive policy: Resume if any chance of success.

    Only blocked by safety guards. Will resume even with low confidence.
    """
    # HALF_OPEN circuit is a warning but doesn't block aggressive
    warning = ""
    if ctx.circuit_state == CircuitState.HALF_OPEN:
        warning = " (circuit HALF_OPEN, monitoring recovery)"

    # Check triggers
    triggers_matched = []

    # Low confidence trigger
    if ctx.completion_confidence < 60 and "low_confidence" in config.auto_resume_triggers:
        triggers_matched.append("low_confidence")

    # Incomplete work trigger (only if completion reason suggests more work is needed)
    # Note: "Max loops" and "Max iterations" mean Ralph finished - NOT incomplete work
    if ctx.run.completion_reason:
        reason_lower = ctx.run.completion_reason.lower()
        # Skip if this is a Ralph Loop completion (max loops reached)
        if not ("max loops" in reason_lower or "max iterations" in reason_lower):
            if any(keyword in reason_lower for keyword in ["iteration", "test", "continue"]):
                triggers_matched.append("incomplete_work")

    # Test-only trigger
    if ctx.run.test_only_loops > 0 and "test_only" in config.auto_resume_triggers:
        triggers_matched.append("test_only")

    if triggers_matched:
        return PolicyDecision(
            should_resume=True,
            reason=f"Aggressive: triggers={triggers_matched}{warning}",
        )

    # Default: resume anyway (aggressive always resumes unless blocked by safety)
    return PolicyDecision(
        should_resume=True,
        reason=f"Aggressive: default resume{warning}",
    )


def _evaluate_conservative(ctx: PolicyContext, config: SupervisionConfig) -> PolicyDecision:
    """Conservative policy: Resume only with high confidence of progress.

    Requires specific signals before auto-resuming.
    """
    # HALF_OPEN circuit blocks conservative
    if ctx.circuit_state == CircuitState.HALF_OPEN:
        return PolicyDecision(
            should_resume=False,
            reason="Circuit HALF_OPEN - conservative policy holds",
        )

    # Check for strong resume signals
    reasons_to_resume = []

    # Low completion confidence suggests incomplete work
    if ctx.completion_confidence < 40 and "low_confidence" in config.auto_resume_triggers:
        reasons_to_resume.append(f"low_confidence ({ctx.completion_confidence:.0f}%)")

    # Test-only loop ended - might need more implementation
    if ctx.run.test_only_loops >= 2 and "test_only" in config.auto_resume_triggers:
        reasons_to_resume.append(f"test_saturation ({ctx.run.test_only_loops} loops)")

    # Explicit incomplete work indicators
    # Note: "Max loops" means Ralph finished its configured iterations - NOT incomplete work
    if ctx.run.completion_reason and "incomplete_work" in config.auto_resume_triggers:
        reason_lower = ctx.run.completion_reason.lower()
        # Skip if this is a Ralph Loop completion (max loops reached)
        if not ("max loops" in reason_lower or "max iterations" in reason_lower):
            incomplete_keywords = ["iteration limit", "partially", "incomplete"]
            if any(keyword in reason_lower for keyword in incomplete_keywords):
                reasons_to_resume.append("incomplete_work_signal")

    # At least one strong signal required for conservative
    if len(reasons_to_resume) >= 1:
        return PolicyDecision(
            should_resume=True,
            reason=f"Conservative: signals={reasons_to_resume}",
        )

    # No strong signals - hold for human review
    return PolicyDecision(
        should_resume=False,
        reason=f"Conservative: no strong resume signals (confidence={ctx.completion_confidence:.0f}%)",
    )


def should_auto_resume(run: ExecutionRun, completion_confidence: float = 0.0) -> tuple[bool, str]:
    """Convenience function to check if a run should be auto-resumed.

    Args:
        run: ExecutionRun to evaluate
        completion_confidence: Current completion confidence score

    Returns:
        Tuple of (should_resume, reason)
    """
    ctx = PolicyContext(
        run=run,
        circuit_state=run.circuit_state,
        calls_this_hour=run.calls_this_hour,
        max_calls_per_hour=run.max_calls_per_hour,
        total_cost_usd=run.cost_usd or 0.0,
        max_cost_usd=run.max_cost_usd,
        completion_confidence=completion_confidence or run.completion_confidence,
        now=datetime.now(UTC),
    )

    decision = evaluate_policy(ctx)
    return decision.should_resume, decision.reason
