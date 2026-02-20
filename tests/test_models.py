"""Tests for Gluon models."""

from datetime import datetime
from pathlib import Path

import pytest

from gluon.models import (
    TASK_PROFILES,
    THINKING_BUDGET_TOKENS,
    CircuitState,
    ExecutionRun,
    Project,
    RunStatus,
    Session,
    SessionStatus,
    SupervisionPolicy,
    TaskProfile,
    ThinkingBudget,
)


class TestProject:
    """Tests for Project model."""

    def test_create_project(self, tmp_path: Path):
        """Test creating a project."""
        project = Project(name="test-project", path=tmp_path)

        assert project.name == "test-project"
        assert project.path == tmp_path
        assert project.id is not None
        assert isinstance(project.created_at, datetime)
        assert isinstance(project.updated_at, datetime)

    def test_project_path_resolved(self):
        """Test that expanded_path returns absolute path."""
        project = Project(name="test", path=Path("."))

        # path stores original value, expanded_path returns absolute
        assert project.expanded_path.is_absolute()

    def test_project_metadata(self, tmp_path: Path):
        """Test project with metadata."""
        metadata = {"language": "python", "framework": "fastapi"}
        project = Project(name="test", path=tmp_path, metadata=metadata)

        assert project.metadata == metadata


class TestSession:
    """Tests for Session model."""

    def test_create_session(self):
        """Test creating a session."""
        session = Session(project_id="test-project-id")

        assert session.project_id == "test-project-id"
        assert session.id is not None
        assert session.status == SessionStatus.ACTIVE
        assert session.claude_session_id is None
        assert session.total_cost_usd == 0.0
        assert session.total_turns == 0

    def test_session_mark_paused(self):
        """Test marking session as paused."""
        session = Session(project_id="test")
        original_updated = session.updated_at

        session.mark_paused()

        assert session.status == SessionStatus.PAUSED
        assert session.updated_at >= original_updated

    def test_session_mark_completed(self):
        """Test marking session as completed."""
        session = Session(project_id="test")
        session.mark_completed()

        assert session.status == SessionStatus.COMPLETED

    def test_session_mark_failed(self):
        """Test marking session as failed."""
        session = Session(project_id="test")
        session.mark_failed()

        assert session.status == SessionStatus.FAILED

    def test_session_add_cost(self):
        """Test adding cost to session."""
        session = Session(project_id="test")
        session.add_cost(0.05)
        session.add_cost(0.03)

        assert session.total_cost_usd == pytest.approx(0.08)

    def test_session_increment_turns(self):
        """Test incrementing turn count."""
        session = Session(project_id="test")
        session.increment_turns()
        session.increment_turns()

        assert session.total_turns == 2


class TestSessionStatus:
    """Tests for SessionStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert SessionStatus.ACTIVE.value == "active"
        assert SessionStatus.PAUSED.value == "paused"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.FAILED.value == "failed"

    def test_status_from_string(self):
        """Test creating status from string."""
        assert SessionStatus("active") == SessionStatus.ACTIVE
        assert SessionStatus("paused") == SessionStatus.PAUSED


class TestRunStatus:
    """Tests for RunStatus enum."""

    def test_status_values(self):
        """Test all status values exist including REVIEW."""
        assert RunStatus.PENDING.value == "pending"
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.REVIEW.value == "review"
        assert RunStatus.COMPLETED.value == "completed"
        assert RunStatus.FAILED.value == "failed"
        assert RunStatus.CANCELLED.value == "cancelled"

    def test_status_from_string(self):
        """Test creating status from string."""
        assert RunStatus("pending") == RunStatus.PENDING
        assert RunStatus("running") == RunStatus.RUNNING
        assert RunStatus("review") == RunStatus.REVIEW
        assert RunStatus("completed") == RunStatus.COMPLETED


class TestExecutionRun:
    """Tests for ExecutionRun model."""

    def test_create_run(self):
        """Test creating an execution run."""
        run = ExecutionRun(project_id="test-project", prompt="Fix the bug")

        assert run.project_id == "test-project"
        assert run.prompt == "Fix the bug"
        assert run.status == RunStatus.PENDING
        assert run.id is not None

    def test_mark_review(self):
        """Test marking run as in review."""
        run = ExecutionRun(project_id="test", prompt="test")
        run.status = RunStatus.RUNNING

        run.mark_review()

        assert run.status == RunStatus.REVIEW

    def test_is_active_includes_review(self):
        """Test that is_active includes REVIEW state."""
        run = ExecutionRun(project_id="test", prompt="test")

        # Test pending
        run.status = RunStatus.PENDING
        assert run.is_active is True

        # Test running
        run.status = RunStatus.RUNNING
        assert run.is_active is True

        # Test review - should be active
        run.status = RunStatus.REVIEW
        assert run.is_active is True

        # Test completed - not active
        run.status = RunStatus.COMPLETED
        assert run.is_active is False

        # Test failed - not active
        run.status = RunStatus.FAILED
        assert run.is_active is False

    def test_is_resumable_includes_review(self):
        """Test that is_resumable includes REVIEW state."""
        run = ExecutionRun(project_id="test", prompt="test")
        run.claude_session_id = "session-123"  # Required for resumability

        # Test review - should be resumable
        run.status = RunStatus.REVIEW
        assert run.is_resumable is True

        # Test completed - should be resumable
        run.status = RunStatus.COMPLETED
        assert run.is_resumable is True

        # Test failed - should be resumable
        run.status = RunStatus.FAILED
        assert run.is_resumable is True

        # Test running - not resumable
        run.status = RunStatus.RUNNING
        assert run.is_resumable is False

    def test_is_resumable_requires_session(self):
        """Test that is_resumable requires claude_session_id."""
        run = ExecutionRun(project_id="test", prompt="test")
        run.status = RunStatus.REVIEW

        # Without session ID - not resumable
        assert run.is_resumable is False

        # With session ID - resumable
        run.claude_session_id = "session-123"
        assert run.is_resumable is True

    def test_prepare_for_resume_resets_status(self):
        """Test prepare_for_resume resets status but preserves session ID."""
        run = ExecutionRun(
            project_id="test",
            prompt="original prompt",
            claude_session_id="session-123",
        )
        run.status = RunStatus.COMPLETED
        run.exit_code = 0
        run.error_message = None
        original_id = run.id

        run.prepare_for_resume("new prompt")

        assert run.status == RunStatus.RUNNING
        assert run.prompt == "new prompt"
        assert run.claude_session_id == "session-123"  # Preserved
        assert run.id == original_id  # Preserved
        assert run.completed_at is None  # Reset
        assert run.exit_code is None  # Reset
        assert run.resume_count == 1
        assert run.last_resumed_at is not None

    def test_prepare_for_resume_increments_count(self):
        """Test that resume_count increments on each resume."""
        run = ExecutionRun(project_id="test", prompt="test")
        run.prepare_for_resume("resume 1")
        assert run.resume_count == 1
        run.prepare_for_resume("resume 2")
        assert run.resume_count == 2


class TestThinkingBudgetEnum:
    """Tests for ThinkingBudget enum values."""

    def test_all_values(self):
        assert ThinkingBudget.NONE.value == "none"
        assert ThinkingBudget.LOW.value == "low"
        assert ThinkingBudget.MEDIUM.value == "medium"
        assert ThinkingBudget.HIGH.value == "high"
        assert ThinkingBudget.ULTRATHINK.value == "ultrathink"
        assert ThinkingBudget.ADAPTIVE.value == "adaptive"

    def test_from_string(self):
        assert ThinkingBudget("adaptive") == ThinkingBudget.ADAPTIVE
        assert ThinkingBudget("none") == ThinkingBudget.NONE


class TestThinkingBudgetTokens:
    """Tests for THINKING_BUDGET_TOKENS mapping."""

    def test_adaptive_is_sentinel(self):
        assert THINKING_BUDGET_TOKENS[ThinkingBudget.ADAPTIVE] == -1

    def test_non_adaptive_are_positive(self):
        for budget, tokens in THINKING_BUDGET_TOKENS.items():
            if budget != ThinkingBudget.ADAPTIVE and budget != ThinkingBudget.NONE:
                assert tokens > 0, f"{budget} should have positive tokens"

    def test_none_is_zero(self):
        assert THINKING_BUDGET_TOKENS[ThinkingBudget.NONE] == 0

    def test_all_budgets_have_mapping(self):
        for budget in ThinkingBudget:
            assert budget in THINKING_BUDGET_TOKENS

    def test_ordering(self):
        assert THINKING_BUDGET_TOKENS[ThinkingBudget.LOW] < THINKING_BUDGET_TOKENS[ThinkingBudget.MEDIUM]
        assert THINKING_BUDGET_TOKENS[ThinkingBudget.MEDIUM] < THINKING_BUDGET_TOKENS[ThinkingBudget.HIGH]
        assert THINKING_BUDGET_TOKENS[ThinkingBudget.HIGH] < THINKING_BUDGET_TOKENS[ThinkingBudget.ULTRATHINK]


class TestTaskProfileEnum:
    """Tests for TaskProfile enum values."""

    def test_all_values(self):
        assert TaskProfile.QUICK.value == "quick"
        assert TaskProfile.STANDARD.value == "standard"
        assert TaskProfile.DEEP.value == "deep"
        assert TaskProfile.PLANNING.value == "planning"


class TestTaskProfiles:
    """Tests for TASK_PROFILES dict."""

    @pytest.mark.parametrize("profile", list(TaskProfile))
    def test_each_profile_has_required_keys(self, profile: TaskProfile):
        config = TASK_PROFILES[profile]
        required_keys = {"model", "max_thinking_tokens", "max_turns", "max_budget_usd", "force_planning", "effort"}
        assert required_keys.issubset(set(config.keys())), f"Profile {profile} missing keys"

    def test_all_profiles_present(self):
        for profile in TaskProfile:
            assert profile in TASK_PROFILES


class TestCircuitStateEnum:
    """Tests for CircuitState enum values."""

    def test_all_values(self):
        assert CircuitState.CLOSED.value == "CLOSED"
        assert CircuitState.HALF_OPEN.value == "HALF_OPEN"
        assert CircuitState.OPEN.value == "OPEN"


class TestSupervisionPolicyEnum:
    """Tests for SupervisionPolicy enum values."""

    def test_all_values(self):
        assert SupervisionPolicy.AGGRESSIVE.value == "aggressive"
        assert SupervisionPolicy.CONSERVATIVE.value == "conservative"
        assert SupervisionPolicy.MANUAL.value == "manual"
