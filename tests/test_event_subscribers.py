"""Tests for event bus subscribers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.events.bus import EventBus
from gluon.events.subscribers import (
    _make_notification_persister,
    register_subscribers,
    websocket_broadcaster,
)
from gluon.events.types import EventCategory, GluonEvent
from gluon.models import Notification, NotificationType
from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    return GluonStore(db_path=db_path)


@pytest.fixture
def bus():
    return EventBus()


def _make_event(event_type: str, **kwargs) -> GluonEvent:
    return GluonEvent(
        type=event_type,
        category=kwargs.pop("category", EventCategory.LIFECYCLE),
        **kwargs,
    )


class TestNotificationPersister:
    @pytest.mark.asyncio
    async def test_question_created_persists_notification(self, store: GluonStore):
        persister = _make_notification_persister(store)
        event = _make_event(
            "question.created",
            category=EventCategory.INTERACTION,
            run_id="run-123",
            data={
                "questions": [{"header": "File permissions", "question": "Allow write?"}],
                "question_ids": ["q-1"],
            },
        )
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 1
        assert notifications[0].type == NotificationType.QUESTION
        assert "File permissions" in notifications[0].title
        assert notifications[0].run_id == "run-123"

    @pytest.mark.asyncio
    async def test_run_completed_persists_notification(self, store: GluonStore):
        persister = _make_notification_persister(store)
        event = _make_event(
            "run.completed",
            run_id="run-456",
            data={"prompt": "Fix the bug"},
        )
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 1
        assert notifications[0].type == NotificationType.COMPLETION
        assert notifications[0].title == "Run completed"

    @pytest.mark.asyncio
    async def test_run_failed_persists_notification(self, store: GluonStore):
        persister = _make_notification_persister(store)
        event = _make_event(
            "run.failed",
            run_id="run-789",
            data={"error_message": "Context overflow"},
        )
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 1
        assert notifications[0].type == NotificationType.FAILURE
        assert "Context overflow" in (notifications[0].message or "")

    @pytest.mark.asyncio
    async def test_run_review_persists_notification(self, store: GluonStore):
        persister = _make_notification_persister(store)
        event = _make_event(
            "run.review",
            run_id="run-abc",
            data={"prompt": "Add feature"},
        )
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 1
        assert notifications[0].type == NotificationType.REVIEW

    @pytest.mark.asyncio
    async def test_non_notification_event_skipped(self, store: GluonStore):
        persister = _make_notification_persister(store)
        event = _make_event("run.created", run_id="run-xyz", data={})
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 0


class TestWebSocketBroadcaster:
    @pytest.mark.asyncio
    async def test_question_created_broadcasts(self):
        mock_ws = MagicMock()
        mock_ws.broadcast_pending_questions = AsyncMock()
        mock_ws.broadcast = AsyncMock()

        with patch("gluon.web.websocket.ws_manager", mock_ws):
            event = _make_event(
                "question.created",
                category=EventCategory.INTERACTION,
                run_id="run-123",
                data={
                    "questions": [{"header": "Q1"}],
                    "question_ids": ["q-1"],
                },
            )
            await websocket_broadcaster(event)
            mock_ws.broadcast_pending_questions.assert_called_once()

    @pytest.mark.asyncio
    async def test_question_answered_broadcasts(self):
        mock_ws = MagicMock()
        mock_ws.broadcast_question_answered = AsyncMock()
        mock_ws.broadcast = AsyncMock()

        with patch("gluon.web.websocket.ws_manager", mock_ws):
            event = _make_event(
                "question.answered",
                category=EventCategory.INTERACTION,
                run_id="run-123",
                data={"question_id": "q-1"},
            )
            await websocket_broadcaster(event)
            mock_ws.broadcast_question_answered.assert_called_once_with("run-123", "q-1")


class TestRegisterSubscribers:
    def test_register_subscribers_adds_handlers(self, bus: EventBus, store: GluonStore):
        register_subscribers(bus, store)

        # Check that subscribers are registered
        assert len(bus._wildcard_subscribers) >= 1  # websocket_broadcaster
        assert "question.created" in bus._subscribers
        assert "run.completed" in bus._subscribers
        assert "run.failed" in bus._subscribers
        assert "run.review" in bus._subscribers
        assert "run.cancelled" in bus._subscribers


class TestTransportDispatcher:
    @pytest.mark.asyncio
    async def test_transport_dispatcher_calls_notify_for_run_completed(self, store: GluonStore):
        """Test that transport_dispatcher calls NotificationDispatcher.notify() for a run.completed event."""
        from gluon.events.subscribers import _make_transport_dispatcher
        from gluon.models import ExecutionRun, RunStatus

        # Create a real ExecutionRun object
        run = ExecutionRun(
            id="run-123",
            project_id="proj-1",
            workspace_id="ws-1",
            prompt="Fix bug",
            status=RunStatus.COMPLETED,
            initiator="test-user",
        )

        dispatcher = _make_transport_dispatcher(store)

        with patch("gluon.notifier.NotificationDispatcher") as mock_notifier_class:
            mock_instance = MagicMock()
            mock_instance.notify = AsyncMock()
            mock_notifier_class.return_value = mock_instance

            event = _make_event(
                "run.completed",
                run_id="run-123",
                data={"run": run, "old_status": RunStatus.RUNNING},
            )
            await dispatcher(event)

            mock_notifier_class.assert_called_once_with(store=store)
            mock_instance.notify.assert_called_once_with(run, RunStatus.RUNNING, RunStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_transport_dispatcher_handles_missing_run_gracefully(self, store: GluonStore):
        """Test that transport_dispatcher handles missing run gracefully."""
        from gluon.events.subscribers import _make_transport_dispatcher

        dispatcher = _make_transport_dispatcher(store)

        with patch("gluon.notifier.NotificationDispatcher") as mock_notifier_class:
            mock_instance = MagicMock()
            mock_instance.notify = AsyncMock()
            mock_notifier_class.return_value = mock_instance

            # Event with no "run" key in data
            event = _make_event("run.completed", run_id="run-123", data={})
            await dispatcher(event)

            # NotificationDispatcher.notify should not be called
            mock_instance.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_transport_dispatcher_handles_import_error_gracefully(self, store: GluonStore):
        """Test that transport_dispatcher handles import error gracefully."""
        from gluon.events.subscribers import _make_transport_dispatcher
        from gluon.models import ExecutionRun, RunStatus

        run = ExecutionRun(
            id="run-123",
            project_id="proj-1",
            workspace_id="ws-1",
            prompt="Fix bug",
            status=RunStatus.COMPLETED,
            initiator="test-user",
        )

        dispatcher = _make_transport_dispatcher(store)

        # Simulate import failure by patching the import
        with patch("gluon.notifier.NotificationDispatcher", side_effect=ImportError("Notifier unavailable")):
            event = _make_event(
                "run.completed",
                run_id="run-123",
                data={"run": run, "old_status": RunStatus.RUNNING},
            )
            # Should not raise, just log and return
            await dispatcher(event)


class TestWebSocketBroadcasterRunEvents:
    @pytest.mark.asyncio
    async def test_run_created_calls_broadcast_run_created(self):
        """Test that RUN_CREATED event calls ws_manager.broadcast_run_created."""
        from gluon.models import ExecutionRun, RunStatus

        mock_ws = MagicMock()
        mock_ws.broadcast_run_created = AsyncMock()
        mock_ws.broadcast = AsyncMock()

        run = ExecutionRun(
            id="run-123",
            project_id="proj-1",
            workspace_id="ws-1",
            prompt="Test prompt",
            status=RunStatus.RUNNING,
            initiator="test-user",
        )

        with patch("gluon.web.websocket.ws_manager", mock_ws):
            event = _make_event(
                "run.created",
                category=EventCategory.LIFECYCLE,
                run_id="run-123",
                data={"run": run, "project_name": "test-project"},
            )
            await websocket_broadcaster(event)
            mock_ws.broadcast_run_created.assert_called_once_with(run, "test-project")

    @pytest.mark.asyncio
    async def test_run_updated_calls_broadcast_run_update(self):
        """Test that RUN_UPDATED event calls ws_manager.broadcast_run_update."""
        from gluon.models import ExecutionRun, RunStatus

        mock_ws = MagicMock()
        mock_ws.broadcast_run_update = AsyncMock()
        mock_ws.broadcast = AsyncMock()

        run = ExecutionRun(
            id="run-456",
            project_id="proj-1",
            workspace_id="ws-1",
            prompt="Test prompt",
            status=RunStatus.RUNNING,
            initiator="test-user",
        )

        with patch("gluon.web.websocket.ws_manager", mock_ws):
            event = _make_event(
                "run.updated",
                category=EventCategory.LIFECYCLE,
                run_id="run-456",
                data={"run": run, "project_name": "test-project"},
            )
            await websocket_broadcaster(event)
            mock_ws.broadcast_run_update.assert_called_once_with(run, "test-project")


class TestWebSocketBroadcasterNotificationMessage:
    @pytest.mark.asyncio
    async def test_notification_created_broadcasts_ws_message(self):
        """Test notification_created ws broadcast when event.data contains a notification."""
        from gluon.models import Notification, NotificationSeverity, NotificationType

        mock_ws = MagicMock()
        mock_ws.broadcast = AsyncMock()
        mock_ws.broadcast_pending_questions = AsyncMock()

        notification = Notification(
            id="notif-123",
            run_id="run-123",
            project_id="proj-1",
            workspace_id="ws-1",
            type=NotificationType.QUESTION,
            severity=NotificationSeverity.WARNING,
            title="Input required: File permissions",
            message="Allow write to config?",
        )

        with patch("gluon.web.websocket.ws_manager", mock_ws):
            event = _make_event(
                "question.created",
                category=EventCategory.INTERACTION,
                run_id="run-123",
                data={
                    "questions": [{"header": "File permissions"}],
                    "question_ids": ["q-1"],
                    "notification": notification,
                },
            )
            await websocket_broadcaster(event)

            # Check that broadcast was called with notification_created type
            assert mock_ws.broadcast.call_count >= 1
            call_args = mock_ws.broadcast.call_args
            assert call_args is not None
            message = call_args[0][0]
            assert message["type"] == "notification_created"
            assert message["notification"]["id"] == "notif-123"
            assert message["notification"]["title"] == "Input required: File permissions"


class TestNotificationPersisterEdgeCases:
    @pytest.mark.asyncio
    async def test_question_created_with_empty_questions_list(self, store: GluonStore):
        """Test that persister handles empty questions list gracefully."""
        persister = _make_notification_persister(store)
        event = _make_event(
            "question.created",
            category=EventCategory.INTERACTION,
            run_id="run-123",
            data={"questions": [], "question_ids": []},
        )
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 1
        # Should create notification with fallback title
        assert "Input required:" in notifications[0].title

    @pytest.mark.asyncio
    async def test_run_completed_message_truncation(self, store: GluonStore):
        """Test that RUN_COMPLETED message is truncated to 60 chars."""
        persister = _make_notification_persister(store)
        long_prompt = "a" * 200  # 200-char string
        event = _make_event(
            "run.completed",
            run_id="run-456",
            data={"prompt": long_prompt},
        )
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 1
        assert len(notifications[0].message or "") <= 60
        assert notifications[0].message == "a" * 60

    @pytest.mark.asyncio
    async def test_run_failed_message_truncation(self, store: GluonStore):
        """Test that RUN_FAILED message is truncated to 100 chars."""
        persister = _make_notification_persister(store)
        long_error = "b" * 200  # 200-char string
        event = _make_event(
            "run.failed",
            run_id="run-789",
            data={"error_message": long_error},
        )
        await persister(event)

        notifications = store.list_notifications()
        assert len(notifications) == 1
        assert len(notifications[0].message or "") <= 100
        assert notifications[0].message == "b" * 100

    @pytest.mark.asyncio
    async def test_persister_attaches_notification_to_event_data(self, store: GluonStore):
        """Test that persister attaches notification to event.data after persistence."""
        persister = _make_notification_persister(store)
        event = _make_event(
            "run.completed",
            run_id="run-123",
            data={"prompt": "Test prompt"},
        )

        # Before persister
        assert "notification" not in event.data

        await persister(event)

        # After persister
        assert "notification" in event.data
        notification = event.data["notification"]
        assert isinstance(notification, Notification)
        assert notification.run_id == "run-123"
        assert notification.type == NotificationType.COMPLETION
