"""Tests for the merge queue."""

from gluon.merge_queue import MergeQueueService
from gluon.models import ExecutionRun, MergeQueueEntry, MergeQueueStatus, RunStatus
from gluon.store import GluonStore


def _make_store(tmp_path):
    return GluonStore(db_path=tmp_path / "test.db")


def _make_run(project_id="proj1", branch="feat/test", pr_number=42) -> ExecutionRun:
    run = ExecutionRun(
        project_id=project_id,
        prompt="test",
        status=RunStatus.REVIEW,
    )
    run.branch_name = branch
    run.pr_number = pr_number
    run.pr_url = f"https://github.com/org/repo/pull/{pr_number}"
    return run


def test_enqueue_creates_entry(tmp_path):
    store = _make_store(tmp_path)
    from unittest.mock import MagicMock

    mq = MergeQueueService(store, MagicMock())

    run = _make_run()
    entry = mq.enqueue(run)

    assert entry.status == MergeQueueStatus.PENDING
    assert entry.branch_name == "feat/test"
    assert entry.pr_number == 42
    assert entry.run_id == run.id


def test_process_next_priority_ordering(tmp_path):
    store = _make_store(tmp_path)

    # Create entries directly
    e1 = MergeQueueEntry(run_id="run1", project_id="proj1", branch_name="feat/low", priority=10)
    e2 = MergeQueueEntry(run_id="run2", project_id="proj1", branch_name="feat/high", priority=1)
    store.enqueue_merge(e1)
    store.enqueue_merge(e2)

    entries = store.list_merge_entries(status="pending")
    assert entries[0].priority == 1  # High priority first
    assert entries[0].branch_name == "feat/high"


def test_conflict_increments_retry_count(tmp_path):
    store = _make_store(tmp_path)

    entry = MergeQueueEntry(run_id="run1", project_id="proj1", branch_name="feat/test")
    entry = store.enqueue_merge(entry)

    entry.conflict_count += 1
    entry.last_error = "merge conflict"
    store.update_merge_entry(entry)

    fetched = store.get_merge_entry(entry.id)
    assert fetched is not None
    assert fetched.conflict_count == 1
    assert fetched.last_error == "merge conflict"


def test_exponential_backoff_calculation(tmp_path):
    store = _make_store(tmp_path)
    from unittest.mock import MagicMock

    mq = MergeQueueService(store, MagicMock())

    assert mq.calculate_backoff(0) == 60
    assert mq.calculate_backoff(1) == 120
    assert mq.calculate_backoff(2) == 240
    assert mq.calculate_backoff(3) == 480


def test_max_retries_marks_failed(tmp_path):
    store = _make_store(tmp_path)
    from unittest.mock import MagicMock

    mq = MergeQueueService(store, MagicMock())

    entry = MergeQueueEntry(run_id="run1", project_id="proj1", branch_name="feat/test", max_retries=3)
    entry.conflict_count = 3

    assert not mq.should_retry(entry)


def test_cancel_entry(tmp_path):
    store = _make_store(tmp_path)

    entry = MergeQueueEntry(run_id="run1", project_id="proj1", branch_name="feat/test")
    entry = store.enqueue_merge(entry)

    from gluon.models import utc_now

    entry.status = MergeQueueStatus.CANCELLED
    entry.completed_at = utc_now()
    store.update_merge_entry(entry)

    fetched = store.get_merge_entry(entry.id)
    assert fetched is not None
    assert fetched.status == MergeQueueStatus.CANCELLED
