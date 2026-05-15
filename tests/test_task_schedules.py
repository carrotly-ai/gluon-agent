"""Tests for the TaskSchedule subsystem (user-defined recurring tasks).

Covers:
  - recurrence_to_cron / cron_to_recurrence round-trip
  - human_summary friendly phrasing
  - validate_cron
  - compute_next_fire_in_tz timezone correctness (incl. a DST boundary)
  - GluonStore CRUD for task_schedules + spawned-run linkage
  - API endpoints: create / list / patch / delete / preview / fire / runs
  - TaskScheduleManager concurrency policies (skip / cancel_replace / allow_overlap)

Manager-level tests stub out runner.submit / runner.cancel so we can verify
the orchestration logic without spawning real subprocesses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from gluon.models import (
    ConcurrencyPolicy,
    RunStatus,
    TaskSchedule,
    utc_now,
)
from gluon.recurrence import (
    EVERY_DAY,
    WEEKDAYS,
    WEEKENDS,
    compute_next_fire_in_tz,
    cron_to_recurrence,
    human_summary,
    next_n_fires_in_tz,
    recurrence_to_cron,
    validate_cron,
)
from gluon.task_scheduler import TaskScheduleManager

# ---------------------------------------------------------------------------
# Recurrence helpers
# ---------------------------------------------------------------------------


class TestRecurrenceConversions:
    def test_weekdays_at_9am(self):
        assert recurrence_to_cron(WEEKDAYS, "09:00") == "0 9 * * 1,2,3,4,5"

    def test_weekends_at_2pm(self):
        assert recurrence_to_cron(WEEKENDS, "14:00") == "0 14 * * 0,6"

    def test_every_day(self):
        # Sun=0..Sat=6 in cron — full set
        assert recurrence_to_cron(EVERY_DAY, "08:30") == "30 8 * * 0,1,2,3,4,5,6"

    def test_arbitrary_subset_round_trip(self):
        cron = recurrence_to_cron([0, 2, 4], "11:15")  # Mon, Wed, Fri
        parsed = cron_to_recurrence(cron)
        assert parsed == {"days": [0, 2, 4], "time": "11:15"}

    def test_empty_days_rejected(self):
        with pytest.raises(ValueError):
            recurrence_to_cron([], "09:00")

    def test_bad_time_rejected(self):
        with pytest.raises(ValueError):
            recurrence_to_cron([0], "9am")
        with pytest.raises(ValueError):
            recurrence_to_cron([0], "25:00")

    def test_complex_cron_returns_none(self):
        # Anything outside the friendly subset (e.g. */5 minutes) must round-trip to None
        assert cron_to_recurrence("*/5 9 * * 1-5") is None
        assert cron_to_recurrence("0 9 1 * *") is None  # day-of-month set
        assert cron_to_recurrence("0 9-17 * * 1-5") is None  # hour range

    def test_cron_to_recurrence_every_day_star(self):
        assert cron_to_recurrence("0 9 * * *") == {"days": EVERY_DAY, "time": "09:00"}

    def test_invalid_cron_returns_none(self):
        assert cron_to_recurrence("not a cron") is None


class TestHumanSummary:
    def test_weekdays(self):
        assert human_summary(WEEKDAYS, "09:00", "Asia/Singapore") == "Weekdays at 9:00 AM (Asia/Singapore)"

    def test_weekends(self):
        assert human_summary(WEEKENDS, "14:30", "UTC") == "Weekends at 2:30 PM (UTC)"

    def test_every_day(self):
        assert human_summary(EVERY_DAY, "06:00", "UTC") == "Every day at 6:00 AM (UTC)"

    def test_arbitrary(self):
        assert human_summary([0, 2, 4], "11:15", "UTC") == "Mon, Wed, Fri at 11:15 AM (UTC)"

    def test_missing_falls_back_to_custom(self):
        assert "Custom" in human_summary(None, None, "UTC")

    def test_noon_renders_correctly(self):
        assert human_summary([0], "12:00", "UTC") == "Mon at 12:00 PM (UTC)"


class TestValidateCron:
    def test_valid(self):
        assert validate_cron("0 9 * * 1-5") is True

    def test_invalid(self):
        assert validate_cron("nonsense") is False


# ---------------------------------------------------------------------------
# Timezone-aware fire computation
# ---------------------------------------------------------------------------


class TestComputeNextFireInTz:
    def test_singapore_9am_weekday(self):
        # Reference: a Sunday (no fire that day) in SGT
        sgt = ZoneInfo("Asia/Singapore")
        base = datetime(2026, 5, 17, 0, 0, tzinfo=sgt)  # Sun
        fire = compute_next_fire_in_tz("0 9 * * 1-5", "Asia/Singapore", base=base.astimezone(UTC))
        # Next fire should be Mon 09:00 SGT == 01:00 UTC
        assert fire.tzinfo is not None
        local = fire.astimezone(sgt)
        assert local.weekday() == 0  # Mon
        assert local.hour == 9 and local.minute == 0

    def test_us_pacific_dst_boundary(self):
        """When DST kicks in (2nd Sunday of March in the US), a 9 AM local
        schedule should remain 9 AM local — the UTC offset shifts."""
        pacific = ZoneInfo("America/Los_Angeles")
        # March 8, 2026 is the second Sunday of March (DST starts).
        # Compute next 9 AM Mon fire from a base just before DST.
        base = datetime(2026, 3, 7, 0, 0, tzinfo=pacific).astimezone(UTC)
        fire = compute_next_fire_in_tz("0 9 * * 1", "America/Los_Angeles", base=base)
        local = fire.astimezone(pacific)
        # Should land on Mon March 9 at 09:00 PDT (after DST), not 10:00.
        assert local.month == 3 and local.day == 9
        assert local.hour == 9 and local.minute == 0
        assert local.utcoffset() == timedelta(hours=-7)  # PDT

    def test_unknown_tz_falls_back_to_utc(self):
        # Should not raise — fall back to UTC silently.
        fire = compute_next_fire_in_tz("0 9 * * *", "Not/A/Real/Tz")
        assert fire.tzinfo is not None

    def test_next_n_fires_distinct(self):
        fires = next_n_fires_in_tz("0 9 * * 1-5", "Asia/Singapore", n=5)
        assert len(fires) == 5
        # Strictly ascending
        assert all(fires[i] < fires[i + 1] for i in range(len(fires) - 1))


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------


def _seed_schedule(store, project_id: str, **overrides) -> TaskSchedule:
    cron = overrides.pop("schedule_cron", "0 9 * * 1-5")
    s = TaskSchedule(
        name=overrides.pop("name", "Morning audit"),
        project_id=project_id,
        prompt=overrides.pop("prompt", "Audit the morning standup"),
        timezone=overrides.pop("timezone", "Asia/Singapore"),
        recurrence_days=overrides.pop("recurrence_days", WEEKDAYS),
        recurrence_time=overrides.pop("recurrence_time", "09:00"),
        schedule_cron=cron,
        **overrides,
    )
    return store.create_task_schedule(s)


class TestTaskScheduleStore:
    def test_create_and_fetch(self, temp_store, project_with_path):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        fetched = temp_store.get_task_schedule(s.id)
        assert fetched is not None
        assert fetched.name == "Morning audit"
        assert fetched.recurrence_days == WEEKDAYS
        assert fetched.timezone == "Asia/Singapore"
        assert fetched.concurrency_policy == ConcurrencyPolicy.SKIP

    def test_list_filter_by_project(self, temp_store, project_with_path):
        project, _ = project_with_path
        _seed_schedule(temp_store, project.id, name="A")
        _seed_schedule(temp_store, project.id, name="B")
        results = temp_store.list_task_schedules(project_id=project.id)
        assert len(results) == 2
        names = {r.name for r in results}
        assert names == {"A", "B"}

    def test_include_disabled_filter(self, temp_store, project_with_path):
        project, _ = project_with_path
        a = _seed_schedule(temp_store, project.id, name="enabled")
        b = _seed_schedule(temp_store, project.id, name="disabled", is_enabled=False)
        only_enabled = temp_store.list_task_schedules(include_disabled=False)
        ids = {r.id for r in only_enabled}
        assert a.id in ids
        assert b.id not in ids

    def test_list_due_returns_only_past_next_fire(self, temp_store, project_with_path):
        project, _ = project_with_path
        past = _seed_schedule(temp_store, project.id, name="due")
        past.next_fire_at = utc_now() - timedelta(minutes=1)
        temp_store.update_task_schedule(past)
        future = _seed_schedule(temp_store, project.id, name="future")
        future.next_fire_at = utc_now() + timedelta(hours=1)
        temp_store.update_task_schedule(future)

        due = temp_store.list_due_task_schedules()
        ids = {d.id for d in due}
        assert past.id in ids
        assert future.id not in ids

    def test_update_persists_changes(self, temp_store, project_with_path):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        s.name = "Renamed"
        s.concurrency_policy = ConcurrencyPolicy.CANCEL_REPLACE
        temp_store.update_task_schedule(s)
        fetched = temp_store.get_task_schedule(s.id)
        assert fetched.name == "Renamed"
        assert fetched.concurrency_policy == ConcurrencyPolicy.CANCEL_REPLACE

    def test_delete(self, temp_store, project_with_path):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        assert temp_store.delete_task_schedule(s.id) is True
        assert temp_store.get_task_schedule(s.id) is None
        assert temp_store.delete_task_schedule(s.id) is False

    def test_runs_for_schedule(self, temp_store, project_with_path):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        run = temp_store.create_run(
            project_id=project.id,
            prompt="spawned",
            initiator="schedule:test",
            schedule_id=s.id,
        )
        rows = temp_store.list_runs_for_schedule(s.id)
        assert any(r.id == run.id for r in rows)
        # Active filter
        run.status = RunStatus.RUNNING
        temp_store.update_run(run)
        actives = temp_store.list_active_runs_for_schedule(s.id)
        assert any(r.id == run.id for r in actives)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestSchedulesAPI:
    def test_create_with_friendly_recurrence(self, project_with_path, api_client):
        project, _ = project_with_path
        client, _ = api_client
        body = {
            "name": "Morning audit",
            "project_name": project.name,
            "prompt": "Audit overnight build state",
            "timezone": "Asia/Singapore",
            "recurrence_days": [0, 1, 2, 3, 4],
            "recurrence_time": "09:00",
        }
        resp = client.post("/api/schedules", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["schedule_cron"] == "0 9 * * 1,2,3,4,5"
        assert data["summary"].startswith("Weekdays at 9:00 AM")
        assert data["concurrency_policy"] == "skip"
        assert data["next_fire_at"] is not None

    def test_create_with_raw_cron(self, project_with_path, api_client):
        project, _ = project_with_path
        client, _ = api_client
        body = {
            "name": "Hourly",
            "project_name": project.name,
            "prompt": "Sync state",
            "timezone": "UTC",
            "schedule_cron": "*/15 * * * *",
        }
        resp = client.post("/api/schedules", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["schedule_cron"] == "*/15 * * * *"

    def test_create_invalid_cron_rejected(self, project_with_path, api_client):
        project, _ = project_with_path
        client, _ = api_client
        body = {
            "name": "Broken",
            "project_name": project.name,
            "prompt": "x",
            "timezone": "UTC",
            "schedule_cron": "not a cron",
        }
        resp = client.post("/api/schedules", json=body)
        assert resp.status_code == 400

    def test_create_no_recurrence_rejected(self, project_with_path, api_client):
        project, _ = project_with_path
        client, _ = api_client
        body = {
            "name": "Nope",
            "project_name": project.name,
            "prompt": "x",
            "timezone": "UTC",
        }
        resp = client.post("/api/schedules", json=body)
        assert resp.status_code == 400

    def test_list_returns_created(self, project_with_path, api_client, temp_store):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        client, _ = api_client
        resp = client.get("/api/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(r["id"] == s.id for r in data["schedules"])

    def test_patch_round_trip(self, project_with_path, api_client, temp_store):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        client, _ = api_client
        resp = client.patch(
            f"/api/schedules/{s.id}",
            json={"name": "Renamed", "concurrency_policy": "cancel_replace"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Renamed"
        assert body["concurrency_policy"] == "cancel_replace"

    def test_patch_recurrence_recomputes_cron(self, project_with_path, api_client, temp_store):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        client, _ = api_client
        resp = client.patch(
            f"/api/schedules/{s.id}",
            json={"recurrence_days": [5, 6], "recurrence_time": "10:30"},
        )
        assert resp.status_code == 200
        assert resp.json()["schedule_cron"] == "30 10 * * 0,6"

    def test_disable_then_enable(self, project_with_path, api_client, temp_store):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        client, _ = api_client
        d = client.post(f"/api/schedules/{s.id}/disable").json()
        assert d["is_enabled"] is False
        e = client.post(f"/api/schedules/{s.id}/enable").json()
        assert e["is_enabled"] is True

    def test_delete(self, project_with_path, api_client, temp_store):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        client, _ = api_client
        resp = client.delete(f"/api/schedules/{s.id}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert client.get(f"/api/schedules/{s.id}").status_code == 404

    def test_preview_returns_friendly_summary(self, api_client):
        client, _ = api_client
        resp = client.post(
            "/api/schedules/preview",
            json={
                "timezone": "Asia/Singapore",
                "recurrence_days": [0, 1, 2, 3, 4],
                "recurrence_time": "09:00",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["schedule_cron"] == "0 9 * * 1,2,3,4,5"
        assert body["summary"].startswith("Weekdays at 9:00 AM")
        assert len(body["next_fires"]) == 5

    def test_runs_for_schedule_endpoint(self, project_with_path, api_client, temp_store):
        project, _ = project_with_path
        s = _seed_schedule(temp_store, project.id)
        run = temp_store.create_run(
            project_id=project.id,
            prompt="spawned",
            initiator="schedule:test",
            schedule_id=s.id,
        )
        client, _ = api_client
        resp = client.get(f"/api/schedules/{s.id}/runs")
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json()]
        assert run.id in ids


# ---------------------------------------------------------------------------
# TaskScheduleManager — concurrency policy
# ---------------------------------------------------------------------------


def _make_manager_for(temp_store, project_with_path, monkeypatch):
    """Build a manager with stubbed runner so .submit and .cancel can be inspected."""
    project, _ = project_with_path
    runner = MagicMock()
    runner.submit = AsyncMock()
    runner.cancel = AsyncMock(return_value=True)
    mgr = TaskScheduleManager(temp_store, runner)
    return mgr, runner, project


@pytest.mark.asyncio
async def test_skip_when_active_run_present(temp_store, project_with_path, monkeypatch):
    mgr, runner, project = _make_manager_for(temp_store, project_with_path, monkeypatch)
    s = _seed_schedule(temp_store, project.id)
    s.next_fire_at = utc_now() - timedelta(seconds=1)
    temp_store.update_task_schedule(s)

    # Seed an in-flight run linked to this schedule
    in_flight = temp_store.create_run(
        project_id=project.id,
        prompt="busy",
        initiator="schedule:test",
        schedule_id=s.id,
    )
    in_flight.status = RunStatus.RUNNING
    temp_store.update_run(in_flight)

    fired = await mgr.tick()
    assert fired == 0
    runner.submit.assert_not_called()
    runner.cancel.assert_not_called()
    # next_fire_at should advance so we don't busy-loop
    refetched = temp_store.get_task_schedule(s.id)
    assert refetched.next_fire_at > utc_now()


@pytest.mark.asyncio
async def test_cancel_replace_cancels_then_fires(temp_store, project_with_path, monkeypatch):
    mgr, runner, project = _make_manager_for(temp_store, project_with_path, monkeypatch)
    s = _seed_schedule(temp_store, project.id, concurrency_policy=ConcurrencyPolicy.CANCEL_REPLACE)
    s.next_fire_at = utc_now() - timedelta(seconds=1)
    temp_store.update_task_schedule(s)

    in_flight = temp_store.create_run(
        project_id=project.id,
        prompt="busy",
        initiator="schedule:test",
        schedule_id=s.id,
    )
    in_flight.status = RunStatus.RUNNING
    temp_store.update_run(in_flight)

    fired = await mgr.tick()
    assert fired == 1
    runner.cancel.assert_awaited_once_with(in_flight.id)
    runner.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_allow_overlap_fires_alongside(temp_store, project_with_path, monkeypatch):
    mgr, runner, project = _make_manager_for(temp_store, project_with_path, monkeypatch)
    s = _seed_schedule(temp_store, project.id, concurrency_policy=ConcurrencyPolicy.ALLOW_OVERLAP)
    s.next_fire_at = utc_now() - timedelta(seconds=1)
    temp_store.update_task_schedule(s)

    in_flight = temp_store.create_run(
        project_id=project.id,
        prompt="busy",
        initiator="schedule:test",
        schedule_id=s.id,
    )
    in_flight.status = RunStatus.RUNNING
    temp_store.update_run(in_flight)

    fired = await mgr.tick()
    assert fired == 1
    runner.cancel.assert_not_called()
    runner.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_idle_schedule_fires_normally(temp_store, project_with_path, monkeypatch):
    mgr, runner, project = _make_manager_for(temp_store, project_with_path, monkeypatch)
    s = _seed_schedule(temp_store, project.id)
    s.next_fire_at = utc_now() - timedelta(seconds=1)
    temp_store.update_task_schedule(s)

    fired = await mgr.tick()
    assert fired == 1
    runner.submit.assert_awaited_once()
    refetched = temp_store.get_task_schedule(s.id)
    assert refetched.last_fired_at is not None
    assert refetched.next_fire_at > utc_now()
