"""Tests for the periodic queue drain in TaskRunner."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gluon.models import ExecutionRun, RunStatus, WorkQueueStatus
from gluon.runner import RunnerConfig, TaskRunner
from gluon.store import GluonStore
from gluon.work_queue import WorkQueueManager


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "queue_drain.db")


def _make_project(store: GluonStore, tmp_path: Path, name: str):
    project_path = tmp_path / name
    project_path.mkdir(exist_ok=True)
    return store.create_project(name, project_path)


@pytest.mark.anyio
async def test_drain_dispatches_pending_item(tmp_path):
    """With nothing active, drain claims a PENDING item and submits it."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path, "proj-a")
    store.enqueue_work(project_id=project.id, prompt="Do the thing", priority=5)

    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
    submitted_run = ExecutionRun(project_id=project.id, prompt="Do the thing")
    runner.submit = AsyncMock(return_value=submitted_run)  # type: ignore[method-assign]

    dispatched = await runner._drain_queue_once()

    assert dispatched == 1
    runner.submit.assert_called_once()
    # The one pending item is now RUNNING (mark_running)
    items = store.list_work_items(project_id=project.id)
    assert items[0].status == WorkQueueStatus.RUNNING
    assert items[0].claimed_by == submitted_run.id


@pytest.mark.anyio
async def test_drain_skips_project_with_active_run(tmp_path):
    """Drain must not dispatch for a project that already has an active run."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path, "proj-b")
    store.enqueue_work(project_id=project.id, prompt="Queued work")

    # Seed an active (RUNNING) run on this project — concurrency control reads
    # active runs from the store, so this is what the drain must observe.
    active_run = store.create_run(project_id=project.id, prompt="Already running")
    active_run.status = RunStatus.RUNNING
    store.update_run(active_run)

    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
    runner.submit = AsyncMock()  # type: ignore[method-assign]

    dispatched = await runner._drain_queue_once()

    assert dispatched == 0
    runner.submit.assert_not_called()
    # Queue item stays PENDING
    items = store.list_work_items(project_id=project.id, status=WorkQueueStatus.PENDING.value)
    assert len(items) == 1


@pytest.mark.anyio
async def test_drain_respects_concurrency_cap(tmp_path):
    """If max_concurrent tasks are active, drain should not dispatch anything."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path, "proj-c")
    store.enqueue_work(project_id=project.id, prompt="Queued")

    runner = TaskRunner(
        store=store,
        config=RunnerConfig(log_path=tmp_path / "logs", max_concurrent=1),
    )
    runner.submit = AsyncMock()  # type: ignore[method-assign]

    # Saturate the cap with one active RUNNING run on a different project.
    other_project = _make_project(store, tmp_path, "proj-c-other")
    other_run = store.create_run(project_id=other_project.id, prompt="Running elsewhere")
    other_run.status = RunStatus.RUNNING
    store.update_run(other_run)

    dispatched = await runner._drain_queue_once()

    assert dispatched == 0
    runner.submit.assert_not_called()


@pytest.mark.anyio
async def test_drain_returns_zero_when_queue_empty(tmp_path):
    """No pending items → no work dispatched, no errors."""
    store = _make_store(tmp_path)
    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
    runner.submit = AsyncMock()  # type: ignore[method-assign]

    dispatched = await runner._drain_queue_once()

    assert dispatched == 0
    runner.submit.assert_not_called()


def _dispatched_item(store: GluonStore, project_id: str, run_id: str):
    """Enqueue + claim + mark_running an item linked to run_id (as dispatch does)."""
    wq = WorkQueueManager(store)
    item = wq.enqueue(project_id, "Queued task")
    wq.claim_next(project_id)
    wq.mark_running(item.id, run_id)
    return item


def test_finalize_queue_item_marks_completed(tmp_path):
    """A queue-dispatched run reaching COMPLETED closes out its work item."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path, "fin-a")
    run = ExecutionRun(project_id=project.id, prompt="Queued task")
    item = _dispatched_item(store, project.id, run.id)
    run.initiator = f"queue:{item.id}"
    run.status = RunStatus.COMPLETED

    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
    runner._finalize_queue_item(run)

    items = store.list_work_items(project_id=project.id)
    assert items[0].status == WorkQueueStatus.COMPLETED
    assert items[0].completed_at is not None


def test_finalize_queue_item_marks_failed(tmp_path):
    """A queue-dispatched run reaching FAILED marks its work item FAILED with the error."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path, "fin-b")
    run = ExecutionRun(project_id=project.id, prompt="Queued task")
    item = _dispatched_item(store, project.id, run.id)
    run.initiator = f"queue_drain:{item.id}"
    run.status = RunStatus.FAILED
    run.error_message = "boom"

    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
    runner._finalize_queue_item(run)

    items = store.list_work_items(project_id=project.id)
    assert items[0].status == WorkQueueStatus.FAILED
    assert items[0].error_message == "boom"


def test_finalize_queue_item_noop_for_non_queue_run(tmp_path):
    """A run not dispatched from the queue must not touch any work item."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path, "fin-c")
    run = ExecutionRun(project_id=project.id, prompt="Manual task")
    item = _dispatched_item(store, project.id, "some-other-run")
    run.initiator = "cli"
    run.status = RunStatus.COMPLETED

    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
    runner._finalize_queue_item(run)

    items = store.list_work_items(project_id=project.id)
    assert items[0].id == item.id
    assert items[0].status == WorkQueueStatus.RUNNING  # untouched
