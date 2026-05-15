"""Polling scheduler for user-defined recurring tasks (TaskSchedule).

Sister of the existing HeartbeatScheduler — they share the same poll-loop
shape and croniter dependency, but operate on distinct tables and pursue
different goals:

  HeartbeatScheduler  → AgentSchedule  → cheap Haiku "wake the agent" runs
  TaskScheduleManager → TaskSchedule   → user-driven full task runs with
                                         IANA-timezone evaluation and a
                                         per-schedule concurrency policy

We keep them separate to avoid coupling the user-facing scheduler to the
agent-internal heartbeat semantics. They run in parallel asyncio tasks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from gluon.models import (
    ConcurrencyPolicy,
    TaskSchedule,
    utc_now,
)
from gluon.recurrence import compute_next_fire_in_tz

if TYPE_CHECKING:
    from gluon.runner import TaskRunner
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECS = 30


class TaskScheduleManager:
    """Polls task_schedules and fires due ones via the shared TaskRunner.

    Lifecycle::

        mgr = TaskScheduleManager(store, runner)
        await mgr.start()
        ...
        await mgr.stop()

    Behavior contract per tick:
      1. List enabled schedules with ``next_fire_at <= now`` (UTC).
      2. For each due schedule, apply the concurrency policy:
         - SKIP            → if any active spawn exists, skip this fire.
         - CANCEL_REPLACE  → cancel active spawns, then fire.
         - ALLOW_OVERLAP   → fire regardless.
      3. Spawn an ExecutionRun via ``runner.submit(schedule_id=...)``.
      4. Bump ``last_fired_at`` and recompute ``next_fire_at`` in the
         schedule's timezone so it accurately tracks DST.

    The loop catches and logs any exception so a single bad schedule
    doesn't kill the whole scheduler.
    """

    def __init__(
        self,
        store: GluonStore,
        runner: TaskRunner,
        *,
        poll_interval_secs: int = DEFAULT_POLL_INTERVAL_SECS,
    ):
        self.store = store
        self.runner = runner
        self.poll_interval_secs = poll_interval_secs
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            logger.warning("TaskScheduleManager already running")
            return
        # Backfill next_fire_at on any schedules that lack one.
        try:
            for s in self.store.list_task_schedules(include_disabled=False):
                if s.next_fire_at is None:
                    s.next_fire_at = compute_next_fire_in_tz(s.schedule_cron, s.timezone)
                    self.store.update_task_schedule(s)
        except Exception:
            logger.exception("TaskScheduleManager: failed to backfill next_fire_at")

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("TaskScheduleManager started (poll=%ds)", self.poll_interval_secs)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("TaskScheduleManager stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval_secs)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TaskScheduleManager tick failed")

    async def tick(self, now: datetime | None = None) -> int:
        """Run one scheduler pass. Returns the number of schedules fired."""
        now = now or utc_now()
        try:
            due = self.store.list_due_task_schedules(now=now)
        except Exception:
            logger.exception("Failed to list due task schedules")
            return 0

        fired = 0
        for schedule in due:
            try:
                if await self._fire_one(schedule, now):
                    fired += 1
            except Exception:
                logger.exception(
                    "TaskScheduleManager: error firing schedule %s (%s)",
                    schedule.id[:8],
                    schedule.name,
                )
        return fired

    async def _fire_one(self, schedule: TaskSchedule, now: datetime) -> bool:
        """Fire a single schedule, applying concurrency policy. Returns True
        when a run was actually spawned."""
        active = self.store.list_active_runs_for_schedule(schedule.id)

        if active:
            if schedule.concurrency_policy == ConcurrencyPolicy.SKIP:
                logger.info(
                    "Schedule %s (%s) skipped: %d active spawn(s)",
                    schedule.id[:8],
                    schedule.name,
                    len(active),
                )
                # Still advance next_fire_at so we don't busy-loop.
                self._advance_next_fire(schedule, now)
                return False
            if schedule.concurrency_policy == ConcurrencyPolicy.CANCEL_REPLACE:
                for r in active:
                    try:
                        await self.runner.cancel(r.id)
                    except Exception:
                        logger.exception(
                            "Schedule %s: failed to cancel active run %s",
                            schedule.id[:8],
                            r.id[:8],
                        )
            # ALLOW_OVERLAP falls through.

        try:
            await self.runner.submit(
                project_id=schedule.project_id,
                prompt=schedule.prompt,
                wait=False,
                use_worktree=schedule.use_worktree,
                initiator=f"schedule:{schedule.id[:8]}",
                model=schedule.model,
                profile=schedule.profile,
                user_id=schedule.created_by_user_id,
                schedule_id=schedule.id,
            )
        except Exception:
            logger.exception(
                "Schedule %s (%s): runner.submit failed",
                schedule.id[:8],
                schedule.name,
            )
            return False

        schedule.last_fired_at = now
        self._advance_next_fire(schedule, now)
        return True

    def _advance_next_fire(self, schedule: TaskSchedule, now: datetime) -> None:
        """Recompute ``next_fire_at`` in the schedule's timezone and persist."""
        try:
            schedule.next_fire_at = compute_next_fire_in_tz(schedule.schedule_cron, schedule.timezone, base=now)
        except Exception:
            logger.exception(
                "Schedule %s: failed to compute next fire — leaving as-is",
                schedule.id[:8],
            )
            return
        self.store.update_task_schedule(schedule)
