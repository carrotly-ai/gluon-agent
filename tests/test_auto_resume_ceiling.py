"""Tests for the combined auto-resume ceiling (#164).

Two independent automatic resume paths each have their own per-trigger cap:
  * PR-monitor (comment / CI failure) increments ``auto_resume_count`` and gates
    on ``MAX_AUTO_RESUMES``.
  * Supervisor increments ``supervision_auto_resume_count`` and gates on
    ``SupervisionConfig.max_auto_resumes``.

``MAX_TOTAL_AUTO_RESUMES`` is a single hard ceiling on the SUM of the two counts
so the per-trigger caps cannot compound into a runaway loop. These tests prove
BOTH paths refuse once the combined count reaches the ceiling, while still
resuming below it (so the ceiling neither over-blocks nor is bypassable).

User-queued follow-ups (``_handle_queued_followup``) are deliberately uncapped
and are not exercised here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.git_manager import GitManager
from gluon.models import MAX_TOTAL_AUTO_RESUMES, ExecutionRun, RunStatus
from gluon.policies import PolicyDecision
from gluon.pr_monitor import MAX_AUTO_RESUMES, PRMonitorService
from gluon.resume_coordinator import ResumeCoordinator
from gluon.runner import TaskRunner
from gluon.store import GluonStore


@pytest.fixture
def mock_runner() -> MagicMock:
    runner = MagicMock()
    runner.resume_in_place = AsyncMock()
    return runner


# ---------------------------------------------------------------------------
# PR-monitor path: should_monitor_run() is the single eligibility gate that
# fronts both auto_resume_for_comment and auto_resume_for_ci_failure.
# ---------------------------------------------------------------------------


@pytest.fixture
def pr_monitor(store: GluonStore) -> PRMonitorService:
    return PRMonitorService(
        store=store,
        runner=TaskRunner(store=store),
        git_manager=GitManager(store=store),
    )


def _open_pr_run(store: GluonStore, tmp_path: Path) -> ExecutionRun:
    project_path = tmp_path / "proj"
    project_path.mkdir(exist_ok=True)
    project = store.create_project("proj", project_path)
    run = store.create_run(project.id, "Test prompt")
    run.status = RunStatus.REVIEW
    run.pr_number = 123
    run.pr_status = "open"
    run.branch_name = "gluon-task/abc123"
    run.claude_session_id = "session-123"
    store.update_run(run)
    return run


class TestPrMonitorCombinedCeiling:
    def test_blocks_when_combined_at_ceiling_below_per_trigger_cap(
        self, pr_monitor: PRMonitorService, store: GluonStore, tmp_path: Path
    ):
        """Combined count == ceiling blocks even though neither per-trigger cap is hit."""
        run = _open_pr_run(store, tmp_path)
        # Split so neither per-trigger cap (MAX_AUTO_RESUMES) is reached, but the
        # sum equals the ceiling.
        run.auto_resume_count = MAX_TOTAL_AUTO_RESUMES - 4
        run.supervision_auto_resume_count = 4
        assert run.auto_resume_count < MAX_AUTO_RESUMES  # per-trigger cap NOT hit
        assert run.auto_resume_count + run.supervision_auto_resume_count == MAX_TOTAL_AUTO_RESUMES
        store.update_run(run)

        assert pr_monitor.should_monitor_run(run) is False

    def test_allows_just_below_ceiling(self, pr_monitor: PRMonitorService, store: GluonStore, tmp_path: Path):
        """Combined count == ceiling - 1 still eligible (ceiling does not over-block)."""
        run = _open_pr_run(store, tmp_path)
        run.auto_resume_count = MAX_TOTAL_AUTO_RESUMES - 5
        run.supervision_auto_resume_count = 4
        assert run.auto_resume_count + run.supervision_auto_resume_count == MAX_TOTAL_AUTO_RESUMES - 1
        store.update_run(run)

        assert pr_monitor.should_monitor_run(run) is True

    def test_per_trigger_cap_still_enforced(self, pr_monitor: PRMonitorService, store: GluonStore, tmp_path: Path):
        """The existing per-trigger cap is untouched by the combined ceiling."""
        run = _open_pr_run(store, tmp_path)
        run.auto_resume_count = MAX_AUTO_RESUMES  # per-trigger cap reached, combined < ceiling
        run.supervision_auto_resume_count = 0
        store.update_run(run)

        assert pr_monitor.should_monitor_run(run) is False


# ---------------------------------------------------------------------------
# Supervisor path: _execute_resume is the single chokepoint after the policy
# decides to resume. Calling it directly with a positive decision isolates the
# ceiling from policy-threshold internals.
# ---------------------------------------------------------------------------


@pytest.fixture
def coordinator(store: GluonStore, mock_runner: MagicMock) -> ResumeCoordinator:
    return ResumeCoordinator(store=store, runner=mock_runner, poll_interval=1)


def _review_run(store: GluonStore) -> ExecutionRun:
    project = store.get_project_by_name("sup-proj") or store.create_project("sup-proj", "/tmp/sup-proj")
    run = store.create_run(project_id=project.id, prompt="fix tests", initiator="test")
    run.status = RunStatus.REVIEW
    run.claude_session_id = "session-abc"
    store.update_run(run)
    return run


class TestSupervisorCombinedCeiling:
    @pytest.mark.asyncio
    async def test_blocks_when_combined_at_ceiling_below_per_trigger_cap(
        self, coordinator: ResumeCoordinator, store: GluonStore, mock_runner: MagicMock
    ):
        """_execute_resume refuses at the ceiling even with a positive decision."""
        run = _review_run(store)
        run.auto_resume_count = 4
        run.supervision_auto_resume_count = MAX_TOTAL_AUTO_RESUMES - 4
        # Supervision per-trigger cap defaults to 5, so this is below it.
        assert run.supervision_auto_resume_count < 5
        assert run.auto_resume_count + run.supervision_auto_resume_count == MAX_TOTAL_AUTO_RESUMES
        store.update_run(run)

        decision = PolicyDecision(should_resume=True, reason="test")
        await coordinator._execute_resume(run, decision, "scheduler")

        mock_runner.resume_in_place.assert_not_called()
        # The count was NOT incremented because the resume was refused.
        refreshed = store.get_run(run.id)
        assert refreshed is not None
        assert refreshed.supervision_auto_resume_count == MAX_TOTAL_AUTO_RESUMES - 4

    @pytest.mark.asyncio
    async def test_allows_just_below_ceiling(
        self, coordinator: ResumeCoordinator, store: GluonStore, mock_runner: MagicMock
    ):
        """Just below the ceiling, _execute_resume proceeds to the runner."""
        run = _review_run(store)
        run.auto_resume_count = 4
        run.supervision_auto_resume_count = MAX_TOTAL_AUTO_RESUMES - 5
        assert run.auto_resume_count + run.supervision_auto_resume_count == MAX_TOTAL_AUTO_RESUMES - 1
        store.update_run(run)

        decision = PolicyDecision(should_resume=True, reason="test")
        await coordinator._execute_resume(run, decision, "scheduler")

        mock_runner.resume_in_place.assert_awaited_once()
