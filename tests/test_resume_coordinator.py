"""Unit tests for ResumeCoordinator.

Tests candidate evaluation, resume execution, disable supervision,
and prompt building. Runner calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.models import (
    ExecutionRun,
    RunStatus,
    SupervisionConfig,
    SupervisionPolicy,
)
from gluon.resume_coordinator import ResumeCoordinator
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_runner():
    runner = MagicMock()
    runner.resume_in_place = AsyncMock()
    return runner


@pytest.fixture
def coordinator(store, mock_runner) -> ResumeCoordinator:
    return ResumeCoordinator(store=store, runner=mock_runner, poll_interval=1)


def _seed_review_run(
    store: GluonStore,
    *,
    supervision_config: SupervisionConfig | None = None,
    claude_session_id: str = "session-abc",
    completion_confidence: float = 30.0,
    completion_reason: str | None = None,
    loop_count: int = 0,
) -> ExecutionRun:
    """Create a run in REVIEW status with supervision config."""
    project = store.get_project_by_name("test-proj")
    if not project:
        project = store.create_project("test-proj", "/tmp/test-proj")
    run = store.create_run(project_id=project.id, prompt="fix tests", initiator="test")
    run.status = RunStatus.REVIEW
    run.claude_session_id = claude_session_id
    run.completion_confidence = completion_confidence
    run.completion_reason = completion_reason
    run.loop_count = loop_count
    if supervision_config:
        run.supervision_config = supervision_config
    store.update_run(run)
    return run


# ===================================================================
# Candidate selection
# ===================================================================


class TestGetReviewCandidates:
    def test_empty_store(self, coordinator):
        candidates = coordinator._get_review_candidates()
        assert candidates == []

    def test_finds_review_with_aggressive_policy(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        _seed_review_run(store, supervision_config=cfg)
        candidates = coordinator._get_review_candidates()
        assert len(candidates) == 1

    def test_skips_manual_policy(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.MANUAL)
        _seed_review_run(store, supervision_config=cfg)
        candidates = coordinator._get_review_candidates()
        assert len(candidates) == 0

    def test_skips_disabled_supervision(self, store, coordinator):
        cfg = SupervisionConfig(enabled=False, policy=SupervisionPolicy.AGGRESSIVE)
        _seed_review_run(store, supervision_config=cfg)
        candidates = coordinator._get_review_candidates()
        assert len(candidates) == 0

    def test_skips_no_session_id(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        _seed_review_run(store, supervision_config=cfg, claude_session_id="")
        # Empty string is truthy but let's test None
        run = store.list_runs()[0]
        run.claude_session_id = None
        store.update_run(run)
        candidates = coordinator._get_review_candidates()
        assert len(candidates) == 0

    def test_skips_non_review_status(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)
        run.status = RunStatus.COMPLETED
        store.update_run(run)
        candidates = coordinator._get_review_candidates()
        assert len(candidates) == 0


# ===================================================================
# evaluate_run
# ===================================================================


class TestEvaluateRun:
    @pytest.mark.asyncio
    async def test_resume_decision_triggers_runner(self, store, coordinator, mock_runner):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)

        decision = await coordinator.evaluate_run(run)
        assert decision.should_resume is True
        mock_runner.resume_in_place.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skip_decision_no_runner_call(self, store, coordinator, mock_runner):
        cfg = SupervisionConfig(policy=SupervisionPolicy.MANUAL)
        run = _seed_review_run(store, supervision_config=cfg)

        decision = await coordinator.evaluate_run(run)
        assert decision.should_resume is False
        mock_runner.resume_in_place.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_supervision_decision(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)

        await coordinator.evaluate_run(run)
        decisions = store.list_supervision_decisions(run.id)
        assert len(decisions) >= 1

    @pytest.mark.asyncio
    async def test_updates_last_check_at(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)

        await coordinator.evaluate_run(run)
        refreshed = store.get_run(run.id)
        assert refreshed is not None
        assert refreshed.last_supervision_check_at is not None

    @pytest.mark.asyncio
    async def test_resume_failure_recorded(self, store, coordinator, mock_runner):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)
        mock_runner.resume_in_place = AsyncMock(side_effect=RuntimeError("subprocess died"))

        decision = await coordinator.evaluate_run(run)
        # Decision was resume, but execution failed
        assert decision.should_resume is True
        # Failure should be recorded as a decision
        decisions = store.list_supervision_decisions(run.id)
        failure_decisions = [d for d in decisions if d.decision == "resume_failed"]
        assert len(failure_decisions) == 1


# ===================================================================
# evaluate_candidates
# ===================================================================


class TestEvaluateCandidates:
    @pytest.mark.asyncio
    async def test_evaluates_all_candidates(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        _seed_review_run(store, supervision_config=cfg)

        project = store.get_project_by_name("test-proj")
        assert project is not None
        run2 = store.create_run(project_id=project.id, prompt="second", initiator="test")
        run2.status = RunStatus.REVIEW
        run2.claude_session_id = "session-2"
        run2.supervision_config = cfg
        store.update_run(run2)

        results = await coordinator.evaluate_candidates()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_candidates(self, coordinator):
        results = await coordinator.evaluate_candidates()
        assert results == []


# ===================================================================
# Build resume prompt
# ===================================================================


class TestBuildResumePrompt:
    def test_basic_prompt(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)
        prompt = coordinator._build_resume_prompt(run, "scheduler")
        assert "[SUPERVISION AUTO-RESUME]" in prompt
        assert "scheduler" in prompt
        assert "fix tests" in prompt  # original prompt

    def test_includes_completion_reason(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg, completion_reason="Tests still failing")
        prompt = coordinator._build_resume_prompt(run, "scheduler")
        assert "Tests still failing" in prompt

    def test_includes_loop_count(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg, loop_count=5)
        prompt = coordinator._build_resume_prompt(run, "scheduler")
        assert "5" in prompt

    def test_includes_confidence(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg, completion_confidence=45.0)
        prompt = coordinator._build_resume_prompt(run, "scheduler")
        assert "45%" in prompt


# ===================================================================
# Disable supervision
# ===================================================================


class TestDisableSupervision:
    @pytest.mark.asyncio
    async def test_disable(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)

        result = await coordinator.disable_supervision(run.id, "User requested")
        assert result is True

        refreshed = store.get_run(run.id)
        assert refreshed is not None
        assert refreshed.supervision_disabled_reason == "User requested"
        assert refreshed.supervision_config is not None
        assert refreshed.supervision_config.enabled is False

    @pytest.mark.asyncio
    async def test_disable_not_found(self, coordinator):
        result = await coordinator.disable_supervision("nonexistent", "reason")
        assert result is False

    @pytest.mark.asyncio
    async def test_disable_records_decision(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE)
        run = _seed_review_run(store, supervision_config=cfg)

        await coordinator.disable_supervision(run.id, "Too noisy")
        decisions = store.list_supervision_decisions(run.id)
        disable_decisions = [d for d in decisions if d.decision == "disable"]
        assert len(disable_decisions) == 1
        assert disable_decisions[0].reason == "Too noisy"


# ===================================================================
# Supervision status
# ===================================================================


class TestGetSupervisionStatus:
    def test_status_for_run(self, store, coordinator):
        cfg = SupervisionConfig(policy=SupervisionPolicy.AGGRESSIVE, max_auto_resumes=5)
        run = _seed_review_run(store, supervision_config=cfg)

        status = coordinator.get_supervision_status(run.id)
        assert status is not None
        assert status["enabled"] is True
        assert status["policy"] == "aggressive"
        assert status["max_auto_resumes"] == 5

    def test_status_not_found(self, coordinator):
        assert coordinator.get_supervision_status("nonexistent") is None


# ===================================================================
# Start / stop lifecycle
# ===================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, coordinator):
        await coordinator.start()
        assert coordinator.is_running is True

        await coordinator.stop()
        assert coordinator.is_running is False

    @pytest.mark.asyncio
    async def test_double_start(self, coordinator):
        await coordinator.start()
        await coordinator.start()  # Should not raise
        assert coordinator.is_running is True
        await coordinator.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, coordinator):
        await coordinator.stop()  # Should not raise
        assert coordinator.is_running is False
