"""Tests for HeartbeatScheduler, compute_next_fire, and schedule/heartbeat CRUD
(Theme B Phase 2)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gluon.models import (
    HeartbeatStatus,
    utc_now,
)
from gluon.scheduler import (
    DEFAULT_PROMPT_TEMPLATE,
    MAX_CONSECUTIVE_FAILURES,
    HeartbeatScheduler,
    compute_next_fire,
    validate_cron,
)
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "scheduler.db")


def _make_workspace_with_project(store: GluonStore, tmp_path: Path, ws_name: str = "ws"):
    ws_path = tmp_path / ws_name
    ws_path.mkdir(exist_ok=True)
    workspace = store.create_workspace(ws_name, ws_path)
    proj_path = ws_path / "proj"
    proj_path.mkdir(exist_ok=True)
    project = store.create_project("proj", proj_path, workspace_id=workspace.id)
    return workspace, project


# ========== Cron utilities ==========


def test_validate_cron_accepts_valid():
    assert validate_cron("*/15 * * * *") is True
    assert validate_cron("0 9 * * 1-5") is True
    assert validate_cron("0 */6 * * *") is True


def test_validate_cron_rejects_invalid():
    assert validate_cron("bogus") is False
    assert validate_cron("99 * * * *") is False  # invalid minute
    assert validate_cron("") is False


def test_compute_next_fire_returns_future_datetime():
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    next_fire = compute_next_fire("*/30 * * * *", base)
    assert next_fire > base
    # Next */30 after 12:00 is 12:30
    assert next_fire == datetime(2026, 1, 1, 12, 30, 0, tzinfo=UTC)


def test_compute_next_fire_raises_on_invalid():
    with pytest.raises(ValueError, match="Invalid cron"):
        compute_next_fire("not-a-cron")


# ========== Schedule CRUD ==========


def test_create_schedule_defaults(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "researcher")

    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="Hello {agent_name}",
        schedule_cron="0 9 * * *",
    )

    assert schedule.id
    assert schedule.agent_id == agent.id
    assert schedule.is_enabled is True
    assert schedule.task_profile == "quick"
    assert schedule.coalesce_ttl_seconds == 300
    assert schedule.consecutive_failures == 0


def test_schedule_prefix_lookup(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "a")
    s = store.create_schedule(agent.id, "x", "0 * * * *")

    fetched = store.get_schedule(s.id[:8])
    assert fetched is not None
    assert fetched.id == s.id


def test_list_schedules_filters(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    a1 = store.create_agent(ws.id, "a1")
    a2 = store.create_agent(ws.id, "a2")

    store.create_schedule(a1.id, "t1", "0 * * * *")
    s_disabled = store.create_schedule(a1.id, "t2", "0 * * * *")
    s_disabled.is_enabled = False
    store.update_schedule(s_disabled)
    store.create_schedule(a2.id, "t3", "0 * * * *")

    all_for_a1 = store.list_schedules(agent_id=a1.id)
    assert len(all_for_a1) == 2

    enabled_only = store.list_schedules(enabled_only=True)
    assert len(enabled_only) == 2  # a1 enabled + a2

    enabled_a1 = store.list_schedules(agent_id=a1.id, enabled_only=True)
    assert len(enabled_a1) == 1


def test_list_due_schedules(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "a")

    # Schedule with next_fire_at in past → due
    past = utc_now() - timedelta(minutes=5)
    due = store.create_schedule(agent.id, "x", "0 * * * *", next_fire_at=past)

    # Schedule with next_fire_at in future → NOT due
    future = utc_now() + timedelta(hours=1)
    store.create_schedule(agent.id, "y", "0 * * * *", next_fire_at=future)

    # Schedule with null next_fire_at → due (first-time computation)
    never = store.create_schedule(agent.id, "z", "0 * * * *")

    # Disabled schedule → not due even if past
    disabled = store.create_schedule(agent.id, "w", "0 * * * *", next_fire_at=past)
    disabled.is_enabled = False
    store.update_schedule(disabled)

    due_ids = {s.id for s in store.list_due_schedules()}
    assert due.id in due_ids
    assert never.id in due_ids
    assert disabled.id not in due_ids


def test_delete_schedule_cascades_heartbeats(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "a")
    s = store.create_schedule(agent.id, "x", "0 * * * *")

    from gluon.models import HeartbeatRun

    hb = HeartbeatRun(schedule_id=s.id, agent_id=agent.id)
    store.record_heartbeat(hb)

    assert store.delete_schedule(s.id) is True
    assert store.get_schedule(s.id) is None
    # Heartbeat should be cascade-deleted
    assert store.list_heartbeats(schedule_id=s.id) == []


# ========== Heartbeat CRUD ==========


def test_record_and_list_heartbeat(tmp_path):
    from gluon.models import HeartbeatRun

    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "a")
    s = store.create_schedule(agent.id, "x", "0 * * * *")

    hb = HeartbeatRun(schedule_id=s.id, agent_id=agent.id)
    store.record_heartbeat(hb)

    results = store.list_heartbeats(schedule_id=s.id)
    assert len(results) == 1
    assert results[0].id == hb.id


def test_get_last_active_heartbeat(tmp_path):
    from gluon.models import HeartbeatRun

    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "a")
    s = store.create_schedule(agent.id, "x", "0 * * * *")

    # Record a COMPLETED heartbeat — should NOT count as active
    done_hb = HeartbeatRun(
        schedule_id=s.id,
        agent_id=agent.id,
        status=HeartbeatStatus.COMPLETED,
    )
    store.record_heartbeat(done_hb)
    assert store.get_last_active_heartbeat(s.id, within_seconds=3600) is None

    # Record a RUNNING heartbeat — should count
    running_hb = HeartbeatRun(
        schedule_id=s.id,
        agent_id=agent.id,
        status=HeartbeatStatus.RUNNING,
    )
    store.record_heartbeat(running_hb)
    active = store.get_last_active_heartbeat(s.id, within_seconds=3600)
    assert active is not None
    assert active.id == running_hb.id

    # Outside the window → not returned
    assert store.get_last_active_heartbeat(s.id, within_seconds=0) is None


# ========== HeartbeatScheduler.fire_heartbeat ==========


def _make_scheduler_with_runner(store: GluonStore):
    """Build a scheduler with a mocked TaskRunner — submit actually persists a run
    via the real store so the heartbeat's FK to execution_runs resolves."""
    from gluon.runner import TaskRunner

    runner = TaskRunner.__new__(TaskRunner)
    runner.store = store  # type: ignore[attr-defined]

    async def _fake_submit(**kwargs):
        return store.create_run(
            project_id=kwargs["project_id"],
            prompt=kwargs["prompt"],
            agent_id=kwargs.get("agent_id"),
            initiator=kwargs.get("initiator"),
        )

    runner.submit = _fake_submit  # type: ignore[method-assign]
    scheduler = HeartbeatScheduler(store, runner, poll_interval_secs=60)
    return scheduler, runner


@pytest.mark.anyio
async def test_fire_heartbeat_happy_path(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "worker")
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="Hello {agent_name}",
        schedule_cron="0 * * * *",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    scheduler, runner = _make_scheduler_with_runner(store)
    heartbeat = await scheduler.fire_heartbeat(schedule)

    assert heartbeat.status == HeartbeatStatus.RUNNING
    assert heartbeat.execution_run_id is not None

    # Schedule should have advanced next_fire_at and reset failures
    refreshed = store.get_schedule(schedule.id)
    assert refreshed is not None
    assert refreshed.last_fired_at is not None
    assert refreshed.next_fire_at is not None
    assert refreshed.next_fire_at > refreshed.last_fired_at
    assert refreshed.consecutive_failures == 0


@pytest.mark.anyio
async def test_fire_heartbeat_coalesces_if_recent_active(tmp_path):
    from gluon.models import HeartbeatRun

    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "worker")
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="* * * * *",
        project_id=project.id,
        coalesce_ttl_seconds=3600,  # 1h window
        next_fire_at=utc_now(),
    )

    # Seed a RUNNING heartbeat within the window
    existing = HeartbeatRun(
        schedule_id=schedule.id,
        agent_id=agent.id,
        status=HeartbeatStatus.RUNNING,
    )
    store.record_heartbeat(existing)

    scheduler, runner = _make_scheduler_with_runner(store)
    heartbeat = await scheduler.fire_heartbeat(schedule)

    assert heartbeat.status == HeartbeatStatus.COALESCED
    assert heartbeat.execution_run_id is None
    assert "Coalesced" in (heartbeat.result_summary or "")


@pytest.mark.anyio
async def test_fire_heartbeat_force_bypasses_coalesce(tmp_path):
    from gluon.models import HeartbeatRun

    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "worker")
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="* * * * *",
        project_id=project.id,
        coalesce_ttl_seconds=3600,
        next_fire_at=utc_now(),
    )
    store.record_heartbeat(HeartbeatRun(schedule_id=schedule.id, agent_id=agent.id, status=HeartbeatStatus.RUNNING))

    scheduler, _ = _make_scheduler_with_runner(store)
    heartbeat = await scheduler.fire_heartbeat(schedule, force=True)

    assert heartbeat.status == HeartbeatStatus.RUNNING
    assert heartbeat.execution_run_id is not None


@pytest.mark.anyio
async def test_fire_heartbeat_skipped_when_agent_inactive(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "idle")
    agent.is_active = False
    store.update_agent(agent)
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="* * * * *",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    scheduler, _ = _make_scheduler_with_runner(store)
    heartbeat = await scheduler.fire_heartbeat(schedule)

    assert heartbeat.status == HeartbeatStatus.SKIPPED
    assert heartbeat.execution_run_id is None

    # Failure counter should NOT increment — inactive is a legit skip
    refreshed = store.get_schedule(schedule.id)
    assert refreshed is not None
    assert refreshed.consecutive_failures == 0


@pytest.mark.anyio
async def test_fire_heartbeat_skipped_when_at_concurrency_cap(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "busy", max_concurrent_runs=1)
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="* * * * *",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    # Seed a running run
    run = store.create_run(project_id=project.id, prompt="existing", agent_id=agent.id)
    from gluon.models import RunStatus

    run.status = RunStatus.RUNNING
    store.update_run(run)

    scheduler, _ = _make_scheduler_with_runner(store)
    heartbeat = await scheduler.fire_heartbeat(schedule)

    assert heartbeat.status == HeartbeatStatus.SKIPPED
    assert "concurrency cap" in (heartbeat.result_summary or "").lower()


@pytest.mark.anyio
async def test_fire_heartbeat_circuit_breaker(tmp_path):
    """3 consecutive failures should auto-disable the schedule."""
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "unlucky")
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="* * * * *",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    # Build a scheduler whose runner.submit always raises
    from gluon.runner import TaskRunner

    runner = TaskRunner.__new__(TaskRunner)
    runner.store = store  # type: ignore[attr-defined]
    runner.submit = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    scheduler = HeartbeatScheduler(store, runner)

    for _ in range(MAX_CONSECUTIVE_FAILURES):
        await scheduler.fire_heartbeat(schedule)
        # Refresh the schedule each iteration so consecutive_failures bumps
        schedule = store.get_schedule(schedule.id)  # type: ignore[assignment]
        assert schedule is not None
        schedule.next_fire_at = utc_now()  # force due again

    refreshed = store.get_schedule(schedule.id)
    assert refreshed is not None
    assert refreshed.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
    assert refreshed.is_enabled is False


@pytest.mark.anyio
async def test_fire_heartbeat_invalid_cron_fails_cleanly(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "a")
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="bogus",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    scheduler, _ = _make_scheduler_with_runner(store)
    heartbeat = await scheduler.fire_heartbeat(schedule)

    assert heartbeat.status == HeartbeatStatus.FAILED
    assert "Invalid cron" in (heartbeat.error_message or "")


# ========== Prompt rendering ==========


@pytest.mark.anyio
async def test_render_prompt_substitutes_all_placeholders(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "rendy", role="engineer")

    # Seed one inbox task
    store.create_task(
        project.id,
        "Fix the thing",
        priority=9,
        assigned_agent_id=agent.id,
    )

    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template=DEFAULT_PROMPT_TEMPLATE,
        schedule_cron="0 * * * *",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    scheduler, _ = _make_scheduler_with_runner(store)
    rendered = scheduler._render_prompt(schedule, agent, project.id)

    assert "rendy" in rendered
    assert "engineer" in rendered
    assert "ws" in rendered  # workspace name
    assert "proj" in rendered  # project name
    assert "Fix the thing" in rendered
    assert "[P9]" in rendered
    # assigned — inbox_count = 1
    assert "1 tasks" in rendered


@pytest.mark.anyio
async def test_render_prompt_empty_inbox(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "idle")

    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="{inbox_summary}",
        schedule_cron="0 * * * *",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    scheduler, _ = _make_scheduler_with_runner(store)
    rendered = scheduler._render_prompt(schedule, agent, project.id)

    assert rendered == "(inbox empty)"


@pytest.mark.anyio
async def test_render_prompt_ignores_unknown_placeholders(tmp_path):
    """Missing placeholders should render as the literal {name} — not raise."""
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "a")

    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="Hello {agent_name}, {undefined_var} is untouched",
        schedule_cron="0 * * * *",
        project_id=project.id,
        next_fire_at=utc_now(),
    )

    scheduler, _ = _make_scheduler_with_runner(store)
    rendered = scheduler._render_prompt(schedule, agent, project.id)

    assert "Hello a" in rendered
    assert "{undefined_var}" in rendered  # Pass-through


# ========== Tick (full loop) ==========


@pytest.mark.anyio
async def test_tick_fires_due_schedules_and_skips_future(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "ticker")

    past = utc_now() - timedelta(minutes=5)
    future = utc_now() + timedelta(hours=1)

    due_sched = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="* * * * *",
        project_id=project.id,
        next_fire_at=past,
    )
    not_due_sched = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="* * * * *",
        project_id=project.id,
        next_fire_at=future,
    )

    scheduler, _ = _make_scheduler_with_runner(store)
    fired = await scheduler.tick()

    assert fired == 1
    heartbeats_due = store.list_heartbeats(schedule_id=due_sched.id)
    heartbeats_not_due = store.list_heartbeats(schedule_id=not_due_sched.id)
    assert len(heartbeats_due) == 1
    assert len(heartbeats_not_due) == 0


@pytest.mark.anyio
async def test_tick_first_time_schedules_just_compute_next_fire(tmp_path):
    """A schedule with null next_fire_at should get its first fire time computed,
    not fire immediately — the first cron tick arrives at next_fire_at."""
    store = _make_store(tmp_path)
    ws, project = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "fresh")
    schedule = store.create_schedule(
        agent_id=agent.id,
        prompt_template="x",
        schedule_cron="0 0 * * *",  # daily at midnight UTC
        project_id=project.id,
        next_fire_at=None,  # never fired
    )

    scheduler, _ = _make_scheduler_with_runner(store)
    fired = await scheduler.tick()

    assert fired == 0
    refreshed = store.get_schedule(schedule.id)
    assert refreshed is not None
    assert refreshed.next_fire_at is not None  # First fire time computed
    assert refreshed.next_fire_at > utc_now()


@pytest.mark.anyio
async def test_scheduler_start_stop_is_safe(tmp_path):
    """start() then stop() should complete cleanly without hanging."""
    import asyncio

    store = _make_store(tmp_path)
    scheduler, _ = _make_scheduler_with_runner(store)

    await scheduler.start()
    assert scheduler.is_running

    # Double-start is a warning, not a crash
    await scheduler.start()

    # Brief sleep so the loop actually enters the tick cycle
    await asyncio.sleep(0.05)

    await scheduler.stop()
    assert not scheduler.is_running
