"""Tests for the work queue."""

from gluon.models import WorkQueueStatus
from gluon.store import GluonStore
from gluon.work_queue import WorkQueueManager


def _make_store(tmp_path):
    return GluonStore(db_path=tmp_path / "test.db")


def test_enqueue_and_claim(tmp_path):
    store = _make_store(tmp_path)
    wq = WorkQueueManager(store)

    item = wq.enqueue("proj1", "Fix the bug", priority=5)
    assert item.status == WorkQueueStatus.PENDING
    assert item.priority == 5

    claimed = wq.claim_next("proj1")
    assert claimed is not None
    assert claimed.id == item.id
    assert claimed.status == WorkQueueStatus.CLAIMED


def test_claim_priority_ordering(tmp_path):
    store = _make_store(tmp_path)
    wq = WorkQueueManager(store)

    wq.enqueue("proj1", "Low priority", priority=10)
    high = wq.enqueue("proj1", "High priority", priority=1)
    wq.enqueue("proj1", "Medium priority", priority=5)

    claimed = wq.claim_next("proj1")
    assert claimed is not None
    assert claimed.id == high.id
    assert claimed.prompt == "High priority"


def test_claim_empty_queue_returns_none(tmp_path):
    store = _make_store(tmp_path)
    wq = WorkQueueManager(store)

    result = wq.claim_next("proj1")
    assert result is None


def test_release_stale_claims(tmp_path):
    store = _make_store(tmp_path)
    wq = WorkQueueManager(store)

    wq.enqueue("proj1", "Task")
    wq.claim_next("proj1")

    # Release with threshold of 0 seconds (everything is stale)
    released = wq.release_stale_claims(threshold_secs=0)
    assert released == 1

    # Item should be claimable again
    reclaimed = wq.claim_next("proj1")
    assert reclaimed is not None


def test_mark_completed_and_failed(tmp_path):
    store = _make_store(tmp_path)
    wq = WorkQueueManager(store)

    item1 = wq.enqueue("proj1", "Task 1")
    item2 = wq.enqueue("proj1", "Task 2")

    wq.mark_completed(item1.id)
    wq.mark_failed(item2.id, "Something broke")

    items = wq.list_items(project_id="proj1")
    completed = next(i for i in items if i.id == item1.id)
    failed = next(i for i in items if i.id == item2.id)

    assert completed.status == WorkQueueStatus.COMPLETED
    assert failed.status == WorkQueueStatus.FAILED
    assert failed.error_message == "Something broke"


def test_release_back_to_pending(tmp_path):
    store = _make_store(tmp_path)
    wq = WorkQueueManager(store)

    item = wq.enqueue("proj1", "Task")
    wq.claim_next("proj1")

    wq.release(item.id)

    items = wq.list_items(project_id="proj1")
    assert items[0].status == WorkQueueStatus.PENDING
    assert items[0].claimed_by is None


def test_cancel_item(tmp_path):
    store = _make_store(tmp_path)
    wq = WorkQueueManager(store)

    item = wq.enqueue("proj1", "Task")
    wq.cancel(item.id)

    items = wq.list_items(project_id="proj1")
    assert items[0].status == WorkQueueStatus.CANCELLED
