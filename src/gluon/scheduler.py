"""Heartbeat scheduler — cron-based agent wakeups (Theme B Phase 2).

Asyncio-based polling scheduler. Tick every `poll_interval` seconds; find
due schedules; fire them (with coalesce + circuit-breaker); spawn cheap
ExecutionRuns via the shared TaskRunner.

Deliberately small — no APScheduler, no multiprocessing. The scheduler is
best-effort and exactly-once-ish: at most one run per (schedule, window).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from croniter import croniter

from gluon.models import (
    AgentSchedule,
    HeartbeatRun,
    HeartbeatStatus,
    TaskStatus,
    utc_now,
)

if TYPE_CHECKING:
    from gluon.runner import TaskRunner
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Circuit breaker — a schedule is auto-disabled after this many consecutive fire failures
MAX_CONSECUTIVE_FAILURES = 3

# Default poll cadence for the scheduler tick
DEFAULT_POLL_INTERVAL_SECS = 60


class HeartbeatScheduler:
    """Polls for due schedules and fires them via the TaskRunner.

    Usage:
        scheduler = HeartbeatScheduler(store, runner)
        await scheduler.start()
        ...
        await scheduler.stop()

    Start is idempotent-ish: calling twice warns and does nothing. Stop
    cancels the tick task cleanly.
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
        """Start the background polling loop."""
        if self.is_running:
            logger.warning("HeartbeatScheduler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("HeartbeatScheduler started (poll=%ds)", self.poll_interval_secs)

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("HeartbeatScheduler stopped")

    async def _run_loop(self) -> None:
        """Main tick loop. Catches and logs exceptions so the loop never dies."""
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval_secs)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("HeartbeatScheduler tick failed")

    async def tick(self) -> int:
        """Run one scheduler pass. Returns the number of heartbeats fired this tick."""
        now = utc_now()
        try:
            schedules = self.store.list_due_schedules(now=now)
        except Exception:
            logger.exception("Failed to list due schedules")
            return 0

        fired = 0
        for schedule in schedules:
            # Skip disabled schedules — defensive; list_due_schedules already filters
            if not schedule.is_enabled:
                continue

            # First-time computation: if next_fire_at is None, set it and skip this tick.
            # We don't fire on the initial registration — we wait for the first cron tick.
            if schedule.next_fire_at is None:
                schedule.next_fire_at = compute_next_fire(schedule.schedule_cron, now)
                self.store.update_schedule(schedule)
                continue

            try:
                heartbeat = await self.fire_heartbeat(schedule, now=now)
                if heartbeat.status in (HeartbeatStatus.RUNNING, HeartbeatStatus.COMPLETED):
                    fired += 1
            except Exception:
                logger.exception("Failed to fire heartbeat for schedule %s", schedule.id[:8])

        return fired

    async def fire_heartbeat(
        self,
        schedule: AgentSchedule,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> HeartbeatRun:
        """Fire a single heartbeat for the given schedule.

        Checks coalesce (skip if a heartbeat is still live in the window),
        checks the agent's concurrency cap, renders the prompt, and spawns
        an ExecutionRun via the runner. On failure, increments
        consecutive_failures and auto-disables the schedule if it hits the
        circuit-breaker threshold.

        Note: coalescing here is best-effort. Two schedulers racing on the
        same schedule could both pass the check and both spawn runs. In
        practice Gluon runs a single scheduler process, so this is acceptable.

        Args:
            schedule: The schedule to fire
            now: Timestamp override (for tests)
            force: If True, skip the coalesce check — used by manual CLI triggers
        """
        now = now or utc_now()

        # Coalesce check — do this BEFORE recording a new heartbeat so we can
        # distinguish the pre-existing live heartbeat from the one we create.
        if not force:
            existing = self.store.get_last_active_heartbeat(
                schedule.id,
                within_seconds=schedule.coalesce_ttl_seconds,
            )
            if existing is not None:
                coalesced = HeartbeatRun(
                    schedule_id=schedule.id,
                    agent_id=schedule.agent_id,
                    fired_at=now,
                    status=HeartbeatStatus.COALESCED,
                    result_summary=f"Coalesced with heartbeat {existing.id[:8]}",
                    completed_at=now,
                )
                self.store.record_heartbeat(coalesced)
                # Advance next_fire_at so we don't re-evaluate this tick
                try:
                    schedule.last_fired_at = now
                    schedule.next_fire_at = compute_next_fire(schedule.schedule_cron, now)
                    self.store.update_schedule(schedule)
                except Exception:
                    logger.exception("Failed to update schedule after coalesce")
                return coalesced

        # Record the firing as PENDING so subsequent coalesces see it
        heartbeat = HeartbeatRun(
            schedule_id=schedule.id,
            agent_id=schedule.agent_id,
            fired_at=now,
            status=HeartbeatStatus.PENDING,
        )
        self.store.record_heartbeat(heartbeat)

        # Advance next_fire_at whether we end up firing or not so we don't re-evaluate this tick
        try:
            schedule.last_fired_at = now
            schedule.next_fire_at = compute_next_fire(schedule.schedule_cron, now)
        except Exception as e:
            heartbeat.status = HeartbeatStatus.FAILED
            heartbeat.error_message = f"Invalid cron: {e}"
            heartbeat.completed_at = utc_now()
            self.store.update_heartbeat(heartbeat)
            self._record_failure(schedule, f"cron parse: {e}")
            return heartbeat

        # Resolve agent; skip if missing or inactive
        agent = self.store.get_agent(schedule.agent_id)
        if agent is None:
            heartbeat.status = HeartbeatStatus.FAILED
            heartbeat.error_message = "Agent no longer exists"
            heartbeat.completed_at = utc_now()
            self.store.update_heartbeat(heartbeat)
            self._record_failure(schedule, "agent missing")
            return heartbeat

        if not agent.is_active:
            heartbeat.status = HeartbeatStatus.SKIPPED
            heartbeat.result_summary = "Agent is inactive"
            heartbeat.completed_at = utc_now()
            self.store.update_heartbeat(heartbeat)
            # Don't mark as failure — inactive is a legitimate skip
            schedule.consecutive_failures = 0
            self.store.update_schedule(schedule)
            return heartbeat

        # Concurrency cap
        active = self.store.count_agent_active_runs(agent.id)
        if active >= agent.max_concurrent_runs:
            heartbeat.status = HeartbeatStatus.SKIPPED
            heartbeat.result_summary = f"Agent at concurrency cap ({active}/{agent.max_concurrent_runs})"
            heartbeat.completed_at = utc_now()
            self.store.update_heartbeat(heartbeat)
            schedule.consecutive_failures = 0
            self.store.update_schedule(schedule)
            return heartbeat

        # Resolve project (schedule.project_id if set, else first project in workspace)
        project_id = self._resolve_project_id(schedule, agent.workspace_id)
        if project_id is None:
            heartbeat.status = HeartbeatStatus.SKIPPED
            heartbeat.result_summary = "No project available for heartbeat"
            heartbeat.completed_at = utc_now()
            self.store.update_heartbeat(heartbeat)
            schedule.consecutive_failures = 0
            self.store.update_schedule(schedule)
            return heartbeat

        # Render the prompt with context
        try:
            rendered_prompt = self._render_prompt(schedule, agent, project_id)
        except Exception as e:
            heartbeat.status = HeartbeatStatus.FAILED
            heartbeat.error_message = f"Prompt render failed: {e}"
            heartbeat.completed_at = utc_now()
            self.store.update_heartbeat(heartbeat)
            self._record_failure(schedule, f"prompt: {e}")
            return heartbeat

        # Spawn the run
        try:
            run = await self.runner.submit(
                project_id=project_id,
                prompt=rendered_prompt,
                wait=False,
                initiator=f"heartbeat:{schedule.id[:8]}",
                profile=schedule.task_profile,
                agent_id=agent.id,
            )
            heartbeat.execution_run_id = run.id
            heartbeat.status = HeartbeatStatus.RUNNING
            self.store.update_heartbeat(heartbeat)
            # Success — reset the failure counter
            schedule.consecutive_failures = 0
            self.store.update_schedule(schedule)
            logger.info(
                "Heartbeat fired: schedule=%s agent=%s run=%s",
                schedule.id[:8],
                agent.name,
                run.id[:8],
            )
        except Exception as e:
            heartbeat.status = HeartbeatStatus.FAILED
            heartbeat.error_message = str(e)
            heartbeat.completed_at = utc_now()
            self.store.update_heartbeat(heartbeat)
            self._record_failure(schedule, str(e))

        return heartbeat

    def _resolve_project_id(self, schedule: AgentSchedule, workspace_id: str | None) -> str | None:
        """Pick a project for this heartbeat.

        Priority:
          1. schedule.project_id if set and still exists
          2. First project in the agent's workspace
          3. Any project (if the agent has no workspace — shouldn't happen in practice)
        """
        if schedule.project_id:
            project = self.store.get_project(schedule.project_id)
            if project is not None:
                return project.id

        if workspace_id:
            projects = self.store.list_projects_by_workspace(workspace_id)
            if projects:
                return projects[0].id

        # Fallback: any project. Mostly a safety net.
        all_projects = self.store.list_projects()
        if all_projects:
            return all_projects[0].id

        return None

    def _render_prompt(self, schedule: AgentSchedule, agent, project_id: str) -> str:
        """Render the schedule's prompt_template with context variables.

        Available placeholders:
          {agent_name}, {agent_role}
          {workspace_name}
          {project_name}
          {inbox_count}, {inbox_summary}  (from get_agent_inbox)
          {active_runs}
        """
        project = self.store.get_project(project_id)
        workspace = self.store.get_workspace(agent.workspace_id) if agent.workspace_id else None

        inbox = self.store.get_agent_inbox(agent.id, limit=10)
        inbox_lines = [f"- [P{t.priority}] {t.title} ({t.status.value})" for t in inbox]
        inbox_summary = "\n".join(inbox_lines) if inbox_lines else "(inbox empty)"

        active_runs = self.store.count_agent_active_runs(agent.id)

        context = {
            "agent_name": agent.name,
            "agent_role": agent.role,
            "workspace_name": workspace.name if workspace else "(no workspace)",
            "project_name": project.name if project else "(no project)",
            "inbox_count": len(inbox),
            "inbox_summary": inbox_summary,
            "active_runs": active_runs,
        }

        # Use format_map with a defaultdict so missing keys don't crash templates
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        return schedule.prompt_template.format_map(_SafeDict(context))

    def _record_failure(self, schedule: AgentSchedule, reason: str) -> None:
        """Increment the schedule's failure counter; auto-disable on circuit-breaker."""
        schedule.consecutive_failures += 1
        if schedule.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            schedule.is_enabled = False
            logger.warning(
                "Schedule %s auto-disabled after %d consecutive failures (%s)",
                schedule.id[:8],
                schedule.consecutive_failures,
                reason,
            )
        self.store.update_schedule(schedule)


def compute_next_fire(cron_expr: str, base: datetime | None = None) -> datetime:
    """Compute the next fire time for a cron expression, in UTC.

    Raises ValueError on invalid cron expressions.
    """
    base = base or utc_now()
    try:
        itr = croniter(cron_expr, base)
        return itr.get_next(datetime)
    except Exception as e:
        raise ValueError(f"Invalid cron expression {cron_expr!r}: {e}") from e


def validate_cron(cron_expr: str) -> bool:
    """Return True if the cron expression is parseable."""
    try:
        croniter(cron_expr)
        return True
    except Exception:
        return False


# Default template used by `gluon schedule create` when none is provided.
DEFAULT_PROMPT_TEMPLATE = """You are agent {agent_name} ({agent_role}) responsible for workspace {workspace_name}.

Current context:
- Active runs: {active_runs}
- Inbox ({inbox_count} tasks assigned to you):
{inbox_summary}

Survey the project {project_name} and decide what to work on. If your inbox \
is empty and no new issues need attention, write a brief status update to \
HEARTBEAT.md and stop. Otherwise, pick the highest-priority task and make \
concrete progress — don't just plan."""


# Re-export TaskStatus to keep the imports in this module tidy
__all__ = [
    "DEFAULT_POLL_INTERVAL_SECS",
    "DEFAULT_PROMPT_TEMPLATE",
    "MAX_CONSECUTIVE_FAILURES",
    "HeartbeatScheduler",
    "compute_next_fire",
    "validate_cron",
    "TaskStatus",  # convenience re-export; used by some CLI callers
]
