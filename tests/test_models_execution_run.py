"""Tests for ExecutionRun lifecycle methods."""

from datetime import timedelta
from pathlib import Path

import pytest

from gluon.models import ExecutionRun, RunStatus, utc_now


@pytest.fixture
def run() -> ExecutionRun:
    """Create a basic ExecutionRun in PENDING state."""
    return ExecutionRun(project_id="proj-1", prompt="fix the bug")


class TestMarkRunning:
    def test_sets_status_running(self, run: ExecutionRun):
        run.mark_running(pid=1234, log_path=Path("/tmp/logs"))
        assert run.status == RunStatus.RUNNING

    def test_sets_pid(self, run: ExecutionRun):
        run.mark_running(pid=5678, log_path=Path("/tmp/logs"))
        assert run.pid == 5678

    def test_sets_log_path(self, run: ExecutionRun):
        log_path = Path("/tmp/logs/abc123")
        run.mark_running(pid=1, log_path=log_path)
        assert run.log_path == log_path

    def test_sets_started_at(self, run: ExecutionRun):
        assert run.started_at is None
        run.mark_running(pid=1, log_path=Path("/tmp"))
        assert run.started_at is not None
        assert run.started_at.tzinfo is not None


class TestMarkCompleted:
    def test_sets_status_completed(self, run: ExecutionRun):
        run.mark_completed()
        assert run.status == RunStatus.COMPLETED

    def test_default_exit_code_zero(self, run: ExecutionRun):
        run.mark_completed()
        assert run.exit_code == 0

    def test_custom_exit_code(self, run: ExecutionRun):
        run.mark_completed(exit_code=2)
        assert run.exit_code == 2

    def test_sets_completed_at(self, run: ExecutionRun):
        assert run.completed_at is None
        run.mark_completed()
        assert run.completed_at is not None


class TestMarkFailed:
    def test_sets_status_failed(self, run: ExecutionRun):
        run.mark_failed("something broke")
        assert run.status == RunStatus.FAILED

    def test_sets_error_message(self, run: ExecutionRun):
        run.mark_failed("timeout exceeded")
        assert run.error_message == "timeout exceeded"

    def test_sets_exit_code(self, run: ExecutionRun):
        run.mark_failed("err", exit_code=137)
        assert run.exit_code == 137

    def test_default_exit_code_one(self, run: ExecutionRun):
        run.mark_failed("err")
        assert run.exit_code == 1

    def test_sets_completed_at(self, run: ExecutionRun):
        run.mark_failed("err")
        assert run.completed_at is not None

    def test_preserves_special_characters(self, run: ExecutionRun):
        msg = "Error: 'path/to/file' has \"invalid\" chars & <tags>"
        run.mark_failed(msg)
        assert run.error_message == msg


class TestMarkCancelled:
    def test_sets_status_cancelled(self, run: ExecutionRun):
        run.mark_cancelled()
        assert run.status == RunStatus.CANCELLED

    def test_sets_completed_at(self, run: ExecutionRun):
        run.mark_cancelled()
        assert run.completed_at is not None


class TestMarkReview:
    def test_sets_status_review(self, run: ExecutionRun):
        run.mark_review()
        assert run.status == RunStatus.REVIEW

    def test_does_not_set_completed_at(self, run: ExecutionRun):
        run.mark_review()
        assert run.completed_at is None


class TestIsResumable:
    def test_true_for_cancelled_with_session(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            status=RunStatus.CANCELLED,
            claude_session_id="sess-1",
        )
        assert run.is_resumable is True

    def test_true_for_completed_with_session(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            status=RunStatus.COMPLETED,
            claude_session_id="sess-1",
        )
        assert run.is_resumable is True

    def test_true_for_failed_with_session(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            status=RunStatus.FAILED,
            claude_session_id="sess-1",
        )
        assert run.is_resumable is True

    def test_true_for_review_with_session(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            status=RunStatus.REVIEW,
            claude_session_id="sess-1",
        )
        assert run.is_resumable is True

    def test_false_for_pending(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            status=RunStatus.PENDING,
            claude_session_id="sess-1",
        )
        assert run.is_resumable is False

    def test_false_for_running(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            status=RunStatus.RUNNING,
            claude_session_id="sess-1",
        )
        assert run.is_resumable is False

    def test_false_without_session_id(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            status=RunStatus.COMPLETED,
            claude_session_id=None,
        )
        assert run.is_resumable is False


class TestDurationSeconds:
    def test_none_when_not_started(self, run: ExecutionRun):
        assert run.duration_seconds is None

    def test_calculates_from_started_to_completed(self):
        now = utc_now()
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            started_at=now - timedelta(seconds=120),
            completed_at=now,
        )
        assert run.duration_seconds == pytest.approx(120.0, abs=1.0)

    def test_uses_utc_now_when_still_running(self):
        run = ExecutionRun(
            project_id="p1",
            prompt="test",
            started_at=utc_now() - timedelta(seconds=10),
        )
        duration = run.duration_seconds
        assert duration is not None
        assert duration >= 9.0


class TestFullLifecycle:
    def test_pending_to_running_to_completed(self, run: ExecutionRun):
        assert run.status == RunStatus.PENDING
        run.mark_running(pid=100, log_path=Path("/tmp"))
        assert run.status == RunStatus.RUNNING
        run.mark_completed()
        assert run.status == RunStatus.COMPLETED
        assert run.exit_code == 0

    def test_running_to_failed(self, run: ExecutionRun):
        run.mark_running(pid=100, log_path=Path("/tmp"))
        run.mark_failed("crash", exit_code=1)
        assert run.status == RunStatus.FAILED
        assert run.error_message == "crash"

    def test_double_mark_completed(self, run: ExecutionRun):
        run.mark_completed()
        first_completed_at = run.completed_at
        run.mark_completed(exit_code=3)
        assert run.exit_code == 3
        # completed_at gets updated
        assert run.completed_at is not None
        assert run.completed_at >= first_completed_at
