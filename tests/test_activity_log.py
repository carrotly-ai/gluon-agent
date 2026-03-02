"""Tests for the structured activity log."""

from datetime import timedelta

from gluon.activity_log import ActivityLogger
from gluon.models import utc_now
from gluon.store import GluonStore


def _make_store(tmp_path):
    return GluonStore(db_path=tmp_path / "test.db")


def test_log_and_query(tmp_path):
    store = _make_store(tmp_path)
    logger = ActivityLogger(store)

    logger.log(actor="run_abc", action="task_started", message="Starting task")
    logger.log(actor="run_abc", action="task_completed", result="success")

    events = logger.query()
    assert len(events) == 2
    assert events[0].action == "task_completed"  # Most recent first
    assert events[1].action == "task_started"


def test_query_by_actor(tmp_path):
    store = _make_store(tmp_path)
    logger = ActivityLogger(store)

    logger.log(actor="run_aaa", action="task_started")
    logger.log(actor="run_bbb", action="task_started")
    logger.log(actor="run_aaa", action="task_completed")

    events = logger.query(actor="run_aaa")
    assert len(events) == 2
    assert all(e.actor == "run_aaa" for e in events)


def test_query_by_action(tmp_path):
    store = _make_store(tmp_path)
    logger = ActivityLogger(store)

    logger.log(actor="run_aaa", action="task_started")
    logger.log(actor="run_bbb", action="chain_started")
    logger.log(actor="run_aaa", action="task_completed")

    events = logger.query(action="task_started")
    assert len(events) == 1
    assert events[0].action == "task_started"


def test_query_since(tmp_path):
    store = _make_store(tmp_path)
    logger = ActivityLogger(store)

    logger.log(actor="run_aaa", action="old_event")
    # All events logged now will have ~same timestamp, so test with since=past
    since = utc_now() - timedelta(seconds=5)
    events = logger.query(since=since)
    assert len(events) >= 1


def test_cleanup_old_events(tmp_path):
    store = _make_store(tmp_path)
    logger = ActivityLogger(store)

    logger.log(actor="run_aaa", action="task_started")
    logger.log(actor="run_bbb", action="task_completed")

    # Cleanup with 0 days should remove all events
    deleted = logger.cleanup(days=0)
    assert deleted == 2

    events = logger.query()
    assert len(events) == 0


def test_metadata_json_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    logger = ActivityLogger(store)

    meta = {"project": "myproject", "step": 3, "tags": ["fast", "test"]}
    logger.log(actor="run_aaa", action="task_started", metadata=meta)

    events = logger.query()
    assert len(events) == 1
    assert events[0].metadata == meta
    assert events[0].metadata["tags"] == ["fast", "test"]


def test_log_swallows_exceptions(tmp_path):
    """ActivityLogger.log should not raise even if store fails."""
    store = _make_store(tmp_path)
    logger = ActivityLogger(store)
    # Close the DB to force an error
    # Even a broken store should not raise from log()
    original_fn = store.log_activity
    store.log_activity = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[assignment]
    logger.log(actor="x", action="y")  # Should not raise
    store.log_activity = original_fn  # type: ignore[assignment]
