"""Tests for Notification model and store CRUD."""

import pytest

from gluon.models import (
    Notification,
    NotificationSeverity,
    NotificationType,
)
from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    return GluonStore(db_path=db_path)


def _make_notification(**kwargs) -> Notification:
    defaults = {
        "type": NotificationType.INFO,
        "severity": NotificationSeverity.INFO,
        "title": "Test notification",
    }
    defaults.update(kwargs)
    return Notification(**defaults)


class TestNotificationCRUD:
    def test_create_and_list(self, store: GluonStore):
        n1 = _make_notification(title="First")
        n2 = _make_notification(title="Second")
        store.create_notification(n1)
        store.create_notification(n2)

        notifications = store.list_notifications()
        assert len(notifications) == 2
        # Most recent first
        assert notifications[0].title == "Second"
        assert notifications[1].title == "First"

    def test_list_unread_only(self, store: GluonStore):
        n1 = _make_notification(title="Unread")
        n2 = _make_notification(title="Read", read=True)
        store.create_notification(n1)
        store.create_notification(n2)

        unread = store.list_notifications(unread_only=True)
        assert len(unread) == 1
        assert unread[0].title == "Unread"

    def test_list_by_workspace(self, store: GluonStore):
        n1 = _make_notification(title="WS1", workspace_id="ws-1")
        n2 = _make_notification(title="WS2", workspace_id="ws-2")
        store.create_notification(n1)
        store.create_notification(n2)

        ws1 = store.list_notifications(workspace_id="ws-1")
        assert len(ws1) == 1
        assert ws1[0].title == "WS1"

    def test_get_unread_count(self, store: GluonStore):
        store.create_notification(_make_notification())
        store.create_notification(_make_notification())
        store.create_notification(_make_notification(read=True))

        assert store.get_unread_count() == 2

    def test_get_unread_count_by_workspace(self, store: GluonStore):
        store.create_notification(_make_notification(workspace_id="ws-1"))
        store.create_notification(_make_notification(workspace_id="ws-2"))

        assert store.get_unread_count(workspace_id="ws-1") == 1
        assert store.get_unread_count(workspace_id="ws-2") == 1

    def test_mark_notification_read(self, store: GluonStore):
        n = _make_notification()
        store.create_notification(n)

        result = store.mark_notification_read(n.id)
        assert result is not None
        assert result.read is True
        assert result.read_at is not None

    def test_mark_notification_read_not_found(self, store: GluonStore):
        result = store.mark_notification_read("nonexistent")
        assert result is None

    def test_mark_all_read(self, store: GluonStore):
        store.create_notification(_make_notification())
        store.create_notification(_make_notification())
        store.create_notification(_make_notification())

        count = store.mark_all_notifications_read()
        assert count == 3
        assert store.get_unread_count() == 0

    def test_mark_all_read_by_workspace(self, store: GluonStore):
        store.create_notification(_make_notification(workspace_id="ws-1"))
        store.create_notification(_make_notification(workspace_id="ws-2"))

        count = store.mark_all_notifications_read(workspace_id="ws-1")
        assert count == 1
        assert store.get_unread_count(workspace_id="ws-1") == 0
        assert store.get_unread_count(workspace_id="ws-2") == 1

    def test_delete_old_notifications(self, store: GluonStore):
        from datetime import timedelta

        from gluon.models import utc_now

        # Create old notification by manually setting created_at
        old = _make_notification(title="Old")
        old.created_at = utc_now() - timedelta(days=60)
        store.create_notification(old)

        recent = _make_notification(title="Recent")
        store.create_notification(recent)

        deleted = store.delete_old_notifications(days=30)
        assert deleted == 1

        remaining = store.list_notifications()
        assert len(remaining) == 1
        assert remaining[0].title == "Recent"

    def test_notification_with_metadata(self, store: GluonStore):
        n = _make_notification(
            metadata={"question_ids": ["q1", "q2"]},
            run_id="run-123",
        )
        store.create_notification(n)

        result = store.list_notifications()
        assert len(result) == 1
        assert result[0].metadata == {"question_ids": ["q1", "q2"]}
        assert result[0].run_id == "run-123"

    def test_notification_types(self, store: GluonStore):
        for ntype in NotificationType:
            n = _make_notification(type=ntype, title=f"Type: {ntype.value}")
            store.create_notification(n)

        all_notifications = store.list_notifications(limit=10)
        assert len(all_notifications) == len(NotificationType)

    def test_limit(self, store: GluonStore):
        for i in range(10):
            store.create_notification(_make_notification(title=f"N{i}"))

        limited = store.list_notifications(limit=3)
        assert len(limited) == 3

    def test_duplicate_notification_id(self, store: GluonStore):
        """Creating two notifications with the same ID should raise IntegrityError."""
        import sqlite3

        n1 = _make_notification(id="dup-id", title="First")
        n2 = _make_notification(id="dup-id", title="Second")

        store.create_notification(n1)
        with pytest.raises(sqlite3.IntegrityError):
            store.create_notification(n2)

    def test_very_long_title_and_message(self, store: GluonStore):
        """Create notification with 10,000 character title and message."""
        long_text = "x" * 10000
        n = _make_notification(
            title=long_text,
            message=long_text,
        )
        store.create_notification(n)

        # Verify roundtrip through create + list
        result = store.list_notifications()
        assert len(result) == 1
        assert result[0].title == long_text
        assert result[0].message == long_text

    def test_mark_notification_read_idempotent(self, store: GluonStore):
        """Mark a notification read twice should not raise error and remain read."""
        n = _make_notification()
        store.create_notification(n)

        # First mark as read
        result1 = store.mark_notification_read(n.id)
        assert result1 is not None
        assert result1.read is True
        assert result1.read_at is not None

        # Mark again (should not raise error)
        result2 = store.mark_notification_read(n.id)
        assert result2 is not None
        assert result2.read is True
        # read_at is updated to reflect the latest mark time
        assert result2.read_at is not None

    def test_mark_all_read_concurrent_workspaces(self, store: GluonStore):
        """Create notifications in two workspaces, mark one as read, verify other is unaffected."""
        # Create 5 notifications in ws-1
        for i in range(5):
            store.create_notification(_make_notification(title=f"WS1-N{i}", workspace_id="ws-1"))

        # Create 5 notifications in ws-2
        for i in range(5):
            store.create_notification(_make_notification(title=f"WS2-N{i}", workspace_id="ws-2"))

        # Mark all in ws-1 as read
        count1 = store.mark_all_notifications_read(workspace_id="ws-1")
        assert count1 == 5
        assert store.get_unread_count(workspace_id="ws-1") == 0
        assert store.get_unread_count(workspace_id="ws-2") == 5

        # Mark all in ws-2 as read
        count2 = store.mark_all_notifications_read(workspace_id="ws-2")
        assert count2 == 5
        assert store.get_unread_count(workspace_id="ws-1") == 0
        assert store.get_unread_count(workspace_id="ws-2") == 0
