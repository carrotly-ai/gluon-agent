"""Periodic health monitoring for background runs.

Detects stalled processes and marks them as failed, with optional
notification dispatch.
"""

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from gluon.models import RunStatus
from gluon.runner import RunHealth, assess_run_health

if TYPE_CHECKING:
    from gluon.notifier import NotificationDispatcher
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Periodic health checker for background runs."""

    STALLED_THRESHOLD = 1800  # 30 min
    CHECK_INTERVAL = 60  # Check every 60s

    def __init__(
        self,
        store: "GluonStore",
        log_path: "os.PathLike[str]",
        notifier: "NotificationDispatcher | None" = None,
    ):
        from pathlib import Path

        self.store = store
        self.log_path = Path(log_path)
        self.notifier = notifier
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start periodic health monitoring loop."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitor started (interval=%ds)", self.CHECK_INTERVAL)

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Health monitor stopped")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while True:
            await asyncio.sleep(self.CHECK_INTERVAL)
            try:
                await self._check_all_runs()
            except Exception:
                logger.debug("Health monitor check failed", exc_info=True)

    async def _check_all_runs(self) -> None:
        """Check all active runs for health issues."""
        active_runs = self.store.list_active_runs()
        for run in active_runs:
            if run.status != RunStatus.RUNNING:
                continue
            health = assess_run_health(run, self.log_path)
            if health == RunHealth.STALLED:
                await self._handle_stalled(run)

    async def _handle_stalled(self, run: "object") -> None:
        """Handle a stalled run: check PID, mark failed if dead."""
        from gluon.models import ExecutionRun

        if not isinstance(run, ExecutionRun):
            return

        pid_dead = False
        if run.pid:
            try:
                os.kill(run.pid, 0)
            except ProcessLookupError:
                pid_dead = True
            except PermissionError:
                pass  # Process exists

        if pid_dead:
            old_status = run.status
            run.mark_failed("Process died unexpectedly (PID not found, detected by health monitor)")
            self.store.update_run(run)
            logger.warning("Run %s marked FAILED: dead PID %d", run.id[:8], run.pid)

            try:
                from gluon.activity_log import ActivityLogger

                ActivityLogger(self.store).log(
                    actor="system",
                    action="health_alert",
                    result="pid_dead",
                    message=f"Run {run.id[:8]} PID {run.pid} died",
                    metadata={"run_id": run.id, "pid": run.pid},
                )
            except Exception:
                pass

            if self.notifier and run.status != old_status:
                try:
                    await self.notifier.notify(run, old_status, run.status)
                except Exception:
                    logger.debug("Health monitor notification failed", exc_info=True)
        else:
            logger.warning("Run %s stalled: no output for >%ds but PID alive", run.id[:8], self.STALLED_THRESHOLD)
