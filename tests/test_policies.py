"""Unit tests for supervision policy engine.

Tests the decision logic that determines whether a run should be auto-resumed.
Pure logic with no I/O — all state is constructed directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gluon.models import (
    CircuitState,
    ExecutionRun,
    RunStatus,
    SupervisionConfig,
    SupervisionPolicy,
)
from gluon.policies import (
    PolicyContext,
    _check_safety_guards,
    _evaluate_aggressive,
    _evaluate_conservative,
    evaluate_policy,
    get_supervision_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 2, 20, 12, 0, 0, tzinfo=UTC)


def _make_run(
    *,
    status: RunStatus = RunStatus.REVIEW,
    circuit_state: CircuitState = CircuitState.CLOSED,
    claude_session_id: str | None = "session-abc",
    supervision_config: SupervisionConfig | None = None,
    supervision_auto_resume_count: int = 0,
    last_supervision_resume_at: datetime | None = None,
    cost_usd: float = 0.0,
    max_cost_usd: float | None = None,
    calls_this_hour: int = 0,
    max_calls_per_hour: int = 100,
    completion_confidence: float = 0.0,
    completion_reason: str | None = None,
    pr_status: str | None = None,
    test_only_loops: int = 0,
    loop_count: int = 0,
    prompt: str = "fix the tests",
) -> ExecutionRun:
    run = ExecutionRun(
        project_id="proj-1",
        prompt=prompt,
        original_prompt=prompt,
        initiator="test",
        status=status,
        circuit_state=circuit_state,
        claude_session_id=claude_session_id,
        supervision_config=supervision_config,
        supervision_auto_resume_count=supervision_auto_resume_count,
        last_supervision_resume_at=last_supervision_resume_at,
        cost_usd=cost_usd,
        max_cost_usd=max_cost_usd,
        calls_this_hour=calls_this_hour,
        max_calls_per_hour=max_calls_per_hour,
        completion_confidence=completion_confidence,
        completion_reason=completion_reason,
        pr_status=pr_status,
        test_only_loops=test_only_loops,
        loop_count=loop_count,
    )
    return run


def _make_ctx(run: ExecutionRun | None = None, **overrides) -> PolicyContext:
    if run is None:
        run = _make_run(**overrides)
    return PolicyContext(
        run=run,
        circuit_state=run.circuit_state,
        calls_this_hour=run.calls_this_hour,
        max_calls_per_hour=run.max_calls_per_hour,
        total_cost_usd=run.cost_usd or 0.0,
        max_cost_usd=run.max_cost_usd,
        completion_confidence=run.completion_confidence,
        now=NOW,
    )


# ===================================================================
# get_supervision_config
# ===================================================================


class TestGetSupervisionConfig:
    def test_returns_default_when_none(self):
        run = _make_run()
        config = get_supervision_config(run)
        assert isinstance(config, SupervisionConfig)
        assert config.enabled is True
        assert config.policy == SupervisionPolicy.CONSERVATIVE

    def test_returns_stored_config(self):
        cfg = SupervisionConfig(
            enabled=True,
            policy=SupervisionPolicy.AGGRESSIVE,
            max_auto_resumes=5,
        )
        run = _make_run(supervision_config=cfg)
        result = get_supervision_config(run)
        assert result.policy == SupervisionPolicy.AGGRESSIVE
        assert result.max_auto_resumes == 5


# ===================================================================
# Safety Guards
# ===================================================================


class TestSafetyGuards:
    def test_circuit_breaker_open_blocks(self):
        run = _make_run(
            circuit_state=CircuitState.OPEN,
            supervision_config=SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE),
        )
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, get_supervision_config(run))
        assert result is not None
        assert result.should_resume is False
        assert "OPEN" in result.reason

    def test_max_auto_resumes_reached(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE, max_auto_resumes=3)
        run = _make_run(supervision_config=cfg, supervision_auto_resume_count=3)
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "Max auto-resumes" in result.reason

    def test_max_auto_resumes_not_yet_reached(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE, max_auto_resumes=3)
        run = _make_run(supervision_config=cfg, supervision_auto_resume_count=2)
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        # Should not block (returns None if no guard fires)
        # Other guards may fire, so only check this specific one passes
        if result:
            assert "Max auto-resumes" not in result.reason

    def test_cost_cap_blocks(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg, cost_usd=10.0, max_cost_usd=5.0)
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "Cost cap" in result.reason

    def test_rate_limit_blocks_with_wait(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg, calls_this_hour=100, max_calls_per_hour=100)
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "Rate limit" in result.reason
        assert result.wait_seconds > 0

    def test_min_time_between_resumes(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE, min_time_between_resumes=60)
        run = _make_run(
            supervision_config=cfg,
            last_supervision_resume_at=NOW - timedelta(seconds=30),
        )
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "Too soon" in result.reason
        assert result.wait_seconds > 0

    def test_min_time_elapsed_passes(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE, min_time_between_resumes=60)
        run = _make_run(
            supervision_config=cfg,
            last_supervision_resume_at=NOW - timedelta(seconds=120),
        )
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        if result:
            assert "Too soon" not in result.reason

    def test_non_review_status_blocks(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg, status=RunStatus.RUNNING)
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "not REVIEW" in result.reason

    def test_no_session_id_blocks(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg, claude_session_id=None)
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "No Claude session" in result.reason

    def test_pr_merged_blocks(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg, pr_status="merged")
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "merged" in result.reason

    def test_pr_closed_blocks(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg, pr_status="closed")
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert "closed" in result.reason

    def test_completion_reason_blocks(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg, completion_reason="All tests pass")
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is not None
        assert result.should_resume is False
        assert "completed with reason" in result.reason

    def test_all_guards_pass(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg)
        ctx = _make_ctx(run)
        result = _check_safety_guards(ctx, cfg)
        assert result is None  # No guard fired


# ===================================================================
# evaluate_policy — top-level routing
# ===================================================================


class TestEvaluatePolicy:
    def test_disabled_supervision_skips(self):
        cfg = SupervisionConfig(enabled=False)
        run = _make_run(supervision_config=cfg)
        ctx = _make_ctx(run)
        result = evaluate_policy(ctx)
        assert result.should_resume is False
        assert "disabled" in result.reason.lower()

    def test_manual_policy_never_resumes(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.MANUAL)
        run = _make_run(supervision_config=cfg)
        ctx = _make_ctx(run)
        result = evaluate_policy(ctx)
        assert result.should_resume is False
        assert "Manual" in result.reason

    def test_routes_to_aggressive(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _make_run(supervision_config=cfg)
        ctx = _make_ctx(run)
        result = evaluate_policy(ctx)
        assert result.should_resume is True
        assert "Aggressive" in result.reason

    def test_routes_to_conservative(self):
        cfg = SupervisionConfig(policy=SupervisionPolicy.CONSERVATIVE)
        run = _make_run(supervision_config=cfg, completion_confidence=80.0)
        ctx = _make_ctx(run)
        result = evaluate_policy(ctx)
        # Conservative with high confidence — no strong signals → skip
        assert result.should_resume is False
        assert "Conservative" in result.reason


# ===================================================================
# Aggressive Policy
# ===================================================================


class TestAggressivePolicy:
    def _cfg(self, **overrides):
        defaults = dict(
            policy=SupervisionPolicy.AGGRESSIVE,
            auto_resume_triggers=["low_confidence", "incomplete_work", "test_only"],
        )
        defaults.update(overrides)
        return SupervisionConfig(**defaults)

    def test_default_always_resumes(self):
        cfg = self._cfg()
        run = _make_run(supervision_config=cfg, completion_confidence=90.0)
        ctx = _make_ctx(run)
        result = _evaluate_aggressive(ctx, cfg)
        assert result.should_resume is True
        assert "default resume" in result.reason

    def test_low_confidence_trigger(self):
        cfg = self._cfg()
        run = _make_run(supervision_config=cfg, completion_confidence=30.0)
        ctx = _make_ctx(run)
        result = _evaluate_aggressive(ctx, cfg)
        assert result.should_resume is True
        assert "low_confidence" in result.reason

    def test_test_only_trigger(self):
        cfg = self._cfg()
        run = _make_run(supervision_config=cfg, test_only_loops=3, completion_confidence=90.0)
        ctx = _make_ctx(run)
        result = _evaluate_aggressive(ctx, cfg)
        assert result.should_resume is True
        assert "test_only" in result.reason

    def test_half_open_circuit_adds_warning(self):
        cfg = self._cfg()
        run = _make_run(supervision_config=cfg, circuit_state=CircuitState.HALF_OPEN)
        ctx = _make_ctx(run)
        result = _evaluate_aggressive(ctx, cfg)
        assert result.should_resume is True
        assert "HALF_OPEN" in result.reason

    def test_max_loops_completion_not_incomplete(self):
        """Max loops reason means Ralph finished — should NOT trigger incomplete_work."""
        cfg = self._cfg()
        run = _make_run(
            supervision_config=cfg,
            completion_reason="Max loops reached",
            completion_confidence=90.0,
        )
        ctx = _make_ctx(run)
        result = _evaluate_aggressive(ctx, cfg)
        assert "incomplete_work" not in result.reason


# ===================================================================
# Conservative Policy
# ===================================================================


class TestConservativePolicy:
    def _cfg(self, **overrides):
        defaults = dict(
            policy=SupervisionPolicy.CONSERVATIVE,
            auto_resume_triggers=["low_confidence", "incomplete_work", "test_only"],
        )
        defaults.update(overrides)
        return SupervisionConfig(**defaults)

    def test_half_open_blocks_conservative(self):
        cfg = self._cfg()
        run = _make_run(supervision_config=cfg, circuit_state=CircuitState.HALF_OPEN)
        ctx = _make_ctx(run)
        result = _evaluate_conservative(ctx, cfg)
        assert result.should_resume is False
        assert "HALF_OPEN" in result.reason

    def test_low_confidence_triggers_resume(self):
        cfg = self._cfg()
        run = _make_run(supervision_config=cfg, completion_confidence=20.0)
        ctx = _make_ctx(run)
        result = _evaluate_conservative(ctx, cfg)
        assert result.should_resume is True
        assert "low_confidence" in result.reason

    def test_high_confidence_no_resume(self):
        cfg = self._cfg()
        run = _make_run(supervision_config=cfg, completion_confidence=80.0)
        ctx = _make_ctx(run)
        result = _evaluate_conservative(ctx, cfg)
        assert result.should_resume is False
        assert "no strong resume signals" in result.reason

    def test_test_saturation_triggers_resume(self):
        cfg = self._cfg()
        run = _make_run(
            supervision_config=cfg,
            test_only_loops=3,
            completion_confidence=80.0,
        )
        ctx = _make_ctx(run)
        result = _evaluate_conservative(ctx, cfg)
        assert result.should_resume is True
        assert "test_saturation" in result.reason

    def test_incomplete_work_signal(self):
        cfg = self._cfg()
        run = _make_run(
            supervision_config=cfg,
            completion_reason="Iteration limit partially complete",
            completion_confidence=80.0,
        )
        ctx = _make_ctx(run)
        result = _evaluate_conservative(ctx, cfg)
        assert result.should_resume is True
        assert "incomplete_work_signal" in result.reason

    def test_max_loops_not_incomplete_signal(self):
        """Max loops/iterations reason means Ralph finished — NOT incomplete."""
        cfg = self._cfg()
        run = _make_run(
            supervision_config=cfg,
            completion_reason="Max loops reached after 10 iterations",
            completion_confidence=80.0,
        )
        ctx = _make_ctx(run)
        result = _evaluate_conservative(ctx, cfg)
        assert result.should_resume is False

    def test_requires_trigger_in_config(self):
        """If low_confidence not in triggers, 20% confidence alone doesn't resume."""
        cfg = self._cfg(auto_resume_triggers=[])
        run = _make_run(supervision_config=cfg, completion_confidence=20.0)
        ctx = _make_ctx(run)
        result = _evaluate_conservative(ctx, cfg)
        assert result.should_resume is False
