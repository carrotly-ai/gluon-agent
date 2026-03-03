"""Tests for run health assessment and HealthMonitor."""

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.models import ExecutionRun, RunStatus
from gluon.runner import RunHealth, assess_run_health, format_run_status

# ===================================================================
# RunHealth enum
# ===================================================================


class TestRunHealthEnum:
    def test_values(self):
        assert RunHealth.HEALTHY == "healthy"
        assert RunHealth.SLOW == "slow"
        assert RunHealth.STALLED == "stalled"
        assert RunHealth.UNKNOWN == "unknown"


# ===================================================================
# assess_run_health
# ===================================================================


def _make_run(
    *,
    status: RunStatus = RunStatus.RUNNING,
    pid: int | None = None,
    run_id: str = "testrun123456",
) -> ExecutionRun:
    run = ExecutionRun(
        project_id="proj-1",
        prompt="test task",
        status=status,
        pid=pid,
    )
    # Override the auto-generated ID for predictable log paths
    run.id = run_id
    return run


class TestAssessRunHealth:
    def test_non_running_returns_unknown(self, tmp_path: Path):
        run = _make_run(status=RunStatus.COMPLETED)
        assert assess_run_health(run, tmp_path) == RunHealth.UNKNOWN

    def test_pending_returns_unknown(self, tmp_path: Path):
        run = _make_run(status=RunStatus.PENDING)
        assert assess_run_health(run, tmp_path) == RunHealth.UNKNOWN

    def test_dead_pid_returns_stalled(self, tmp_path: Path):
        run = _make_run(pid=999999999)
        assert assess_run_health(run, tmp_path) == RunHealth.STALLED

    def test_alive_pid_no_log_returns_healthy(self, tmp_path: Path):
        """Running with alive PID and no log yet = just started = HEALTHY."""
        run = _make_run(pid=os.getpid())
        assert assess_run_health(run, tmp_path) == RunHealth.HEALTHY

    def test_recent_output_returns_healthy(self, tmp_path: Path):
        run = _make_run(pid=os.getpid(), run_id="test-recent")
        log_dir = tmp_path / "test-recent"
        log_dir.mkdir()
        messages_file = log_dir / "messages.jsonl"
        messages_file.write_text('{"type":"text"}\n')
        # File was just written, so mtime is recent

        assert assess_run_health(run, tmp_path) == RunHealth.HEALTHY

    def test_old_output_returns_stalled(self, tmp_path: Path):
        run = _make_run(pid=os.getpid(), run_id="test-old")
        log_dir = tmp_path / "test-old"
        log_dir.mkdir()
        messages_file = log_dir / "messages.jsonl"
        messages_file.write_text('{"type":"text"}\n')
        # Set mtime to 20 minutes ago
        old_time = time.time() - 1200
        os.utime(messages_file, (old_time, old_time))

        assert assess_run_health(run, tmp_path) == RunHealth.STALLED

    def test_slow_output_returns_slow(self, tmp_path: Path):
        run = _make_run(pid=os.getpid(), run_id="test-slow")
        log_dir = tmp_path / "test-slow"
        log_dir.mkdir()
        messages_file = log_dir / "messages.jsonl"
        messages_file.write_text('{"type":"text"}\n')
        # Set mtime to 8 minutes ago (between 5-15 min)
        old_time = time.time() - 480
        os.utime(messages_file, (old_time, old_time))

        assert assess_run_health(run, tmp_path) == RunHealth.SLOW

    def test_no_pid_with_recent_output(self, tmp_path: Path):
        """Run without PID (e.g., PID not yet recorded) + recent output = HEALTHY."""
        run = _make_run(pid=None, run_id="test-nopid")
        log_dir = tmp_path / "test-nopid"
        log_dir.mkdir()
        (log_dir / "messages.jsonl").write_text('{"type":"text"}\n')

        assert assess_run_health(run, tmp_path) == RunHealth.HEALTHY

    def test_boundary_exactly_300s(self, tmp_path: Path):
        """Output exactly 300s (5 min) old — at HEALTHY/SLOW boundary."""
        run = _make_run(pid=os.getpid(), run_id="test-boundary-300")
        log_dir = tmp_path / "test-boundary-300"
        log_dir.mkdir()
        messages_file = log_dir / "messages.jsonl"
        messages_file.write_text('{"type":"text"}\n')
        boundary_time = time.time() - 300
        os.utime(messages_file, (boundary_time, boundary_time))

        health = assess_run_health(run, tmp_path)
        # age < 300 → HEALTHY, age >= 300 → SLOW
        assert health in (RunHealth.HEALTHY, RunHealth.SLOW)

    def test_boundary_exactly_900s(self, tmp_path: Path):
        """Output exactly 900s (15 min) old — at SLOW/STALLED boundary."""
        run = _make_run(pid=os.getpid(), run_id="test-boundary-900")
        log_dir = tmp_path / "test-boundary-900"
        log_dir.mkdir()
        messages_file = log_dir / "messages.jsonl"
        messages_file.write_text('{"type":"text"}\n')
        boundary_time = time.time() - 900
        os.utime(messages_file, (boundary_time, boundary_time))

        health = assess_run_health(run, tmp_path)
        # age < 900 → SLOW, age >= 900 → STALLED
        assert health in (RunHealth.SLOW, RunHealth.STALLED)


# ===================================================================
# format_run_status with health
# ===================================================================


class TestFormatRunStatusWithHealth:
    def test_running_healthy(self):
        emoji, color = format_run_status(RunStatus.RUNNING, RunHealth.HEALTHY)
        assert color == "green"

    def test_running_slow(self):
        emoji, color = format_run_status(RunStatus.RUNNING, RunHealth.SLOW)
        assert color == "yellow"

    def test_running_stalled(self):
        emoji, color = format_run_status(RunStatus.RUNNING, RunHealth.STALLED)
        assert color == "red"

    def test_running_unknown_health(self):
        emoji, color = format_run_status(RunStatus.RUNNING, RunHealth.UNKNOWN)
        # UNKNOWN not in the dict → falls through to default RUNNING
        assert color == "blue"

    def test_running_no_health(self):
        emoji, color = format_run_status(RunStatus.RUNNING)
        assert color == "blue"

    def test_completed_ignores_health(self):
        emoji, color = format_run_status(RunStatus.COMPLETED, RunHealth.STALLED)
        assert color == "green"

    def test_failed_ignores_health(self):
        emoji, color = format_run_status(RunStatus.FAILED, RunHealth.HEALTHY)
        assert color == "red"


# ===================================================================
# HealthMonitor
# ===================================================================


class TestHealthMonitor:
    def test_import(self):
        from gluon.health_monitor import HealthMonitor

        assert HealthMonitor is not None

    def test_init(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        monitor = HealthMonitor(store=store, log_path=tmp_path)
        assert monitor.store is store
        assert not monitor.is_running

    @pytest.mark.asyncio
    async def test_start_stop(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        store.list_active_runs.return_value = []
        monitor = HealthMonitor(store=store, log_path=tmp_path)

        await monitor.start()
        assert monitor.is_running

        await monitor.stop()
        assert not monitor.is_running

    @pytest.mark.asyncio
    async def test_start_idempotent(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        store.list_active_runs.return_value = []
        monitor = HealthMonitor(store=store, log_path=tmp_path)

        await monitor.start()
        task1 = monitor._task
        await monitor.start()  # Should not create a new task
        assert monitor._task is task1

        await monitor.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        monitor = HealthMonitor(store=store, log_path=tmp_path)
        await monitor.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_check_all_runs_no_active(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        store.list_active_runs.return_value = []
        monitor = HealthMonitor(store=store, log_path=tmp_path)

        await monitor._check_all_runs()
        store.list_active_runs.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_stalled_dead_pid(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        run = ExecutionRun(
            project_id="proj-1",
            prompt="test",
            status=RunStatus.RUNNING,
            pid=999999999,
        )

        monitor = HealthMonitor(store=store, log_path=tmp_path)
        await monitor._handle_stalled(run)

        store.update_run.assert_called_once()
        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_handle_stalled_alive_pid(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        run = ExecutionRun(
            project_id="proj-1",
            prompt="test",
            status=RunStatus.RUNNING,
            pid=os.getpid(),  # This PID is alive
        )

        monitor = HealthMonitor(store=store, log_path=tmp_path)
        await monitor._handle_stalled(run)

        # Should NOT mark as failed when PID is alive
        store.update_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_stalled_with_notifier(self, tmp_path: Path):
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        notifier = AsyncMock()
        run = ExecutionRun(
            project_id="proj-1",
            prompt="test",
            status=RunStatus.RUNNING,
            pid=999999999,
        )

        monitor = HealthMonitor(store=store, log_path=tmp_path, notifier=notifier)
        await monitor._handle_stalled(run)

        notifier.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_stalled_no_pid(self, tmp_path: Path):
        """Run with pid=None should NOT be marked failed (can't confirm dead)."""
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        run = ExecutionRun(
            project_id="proj-1",
            prompt="test",
            status=RunStatus.RUNNING,
            pid=None,
        )

        monitor = HealthMonitor(store=store, log_path=tmp_path)
        await monitor._handle_stalled(run)

        store.update_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_stalled_non_execution_run(self, tmp_path: Path):
        """Type guard: non-ExecutionRun object should be silently ignored."""
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        monitor = HealthMonitor(store=store, log_path=tmp_path)
        await monitor._handle_stalled("not-a-run")  # type: ignore[arg-type]

        store.update_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_stalled_notifier_failure_swallowed(self, tmp_path: Path):
        """If notifier.notify raises, run should still be marked failed."""
        from gluon.health_monitor import HealthMonitor

        store = MagicMock()
        notifier = AsyncMock()
        notifier.notify = AsyncMock(side_effect=Exception("Network error"))
        run = ExecutionRun(
            project_id="proj-1",
            prompt="test",
            status=RunStatus.RUNNING,
            pid=999999999,
        )

        monitor = HealthMonitor(store=store, log_path=tmp_path, notifier=notifier)
        await monitor._handle_stalled(run)

        # Run should still be marked failed even though notification failed
        store.update_run.assert_called_once()
        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_check_all_runs_with_stalled_run(self, tmp_path: Path):
        """Integration: list_active_runs → assess → handle for a stalled run."""
        from gluon.health_monitor import HealthMonitor

        run = ExecutionRun(
            project_id="proj-1",
            prompt="test",
            status=RunStatus.RUNNING,
            pid=999999999,  # Dead PID
        )
        store = MagicMock()
        store.list_active_runs.return_value = [run]

        monitor = HealthMonitor(store=store, log_path=tmp_path)
        await monitor._check_all_runs()

        store.update_run.assert_called_once()
        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_check_all_runs_skips_non_running(self, tmp_path: Path):
        """Runs with non-RUNNING status should be skipped."""
        from gluon.health_monitor import HealthMonitor

        run = ExecutionRun(
            project_id="proj-1",
            prompt="test",
            status=RunStatus.PENDING,
        )
        store = MagicMock()
        store.list_active_runs.return_value = [run]

        monitor = HealthMonitor(store=store, log_path=tmp_path)
        await monitor._check_all_runs()

        store.update_run.assert_not_called()
