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
        # Question escalation is the only event-bus transport dispatch; terminal
        # run-status notifications are delivered directly by the orchestrator.
        assert "question.escalated" in bus._subscribers
        # run.cancelled no longer has a dedicated subscriber (only the wildcard
        # websocket broadcaster observes it).
        assert "run.cancelled" not in bus._subscribers


class TestQuestionEscalator:
    """The escalator dispatches over the *shared* notifier's live transports."""

    @pytest.mark.asyncio
    async def test_noop_without_notifier(self, store: GluonStore):
        """No shared notifier → graceful no-op (does not touch the store)."""
        from gluon.events.subscribers import _make_question_escalator

        escalator = _make_question_escalator(store, None)
        with patch.object(store, "get_run") as mock_get_run:
            event = _make_event(
                "question.escalated",
                category=EventCategory.INTERACTION,
                run_id="run-123",
                data={"questions": [{"header": "Q1", "question": "Allow?"}]},
            )
            await escalator(event)
            mock_get_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_no_transports(self, store: GluonStore):
        """Notifier present but transport-less → graceful no-op."""
        from gluon.events.subscribers import _make_question_escalator
        from gluon.notifier import NotificationDispatcher

        notifier = NotificationDispatcher(store=store)  # empty transports
        escalator = _make_question_escalator(store, notifier)
        with patch.object(store, "get_run") as mock_get_run:
            event = _make_event(
                "question.escalated",
                category=EventCategory.INTERACTION,
                run_id="run-123",
                data={"questions": [{"header": "Q1", "question": "Allow?"}]},
            )
            await escalator(event)
            mock_get_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_to_mapped_channel(self, store: GluonStore, tmp_path):
        """With a live transport + channel mapping, the escalation is sent."""
        from gluon.events.subscribers import _make_question_escalator
        from gluon.notifier import NotificationDispatcher

        project_path = tmp_path / "proj"
        project_path.mkdir()
        project = store.create_project("proj", project_path)
        run = store.create_run(project.id, "Fix bug", initiator="test-user")
        store.create_channel_mapping("telegram", "chat-1", project.id, project.name)

        mock_transport = MagicMock()
        mock_transport.name = "telegram"
        mock_transport.send = AsyncMock()
        notifier = NotificationDispatcher(store=store, transports={"telegram": mock_transport})

        escalator = _make_question_escalator(store, notifier)
        event = _make_event(
            "question.escalated",
            category=EventCategory.INTERACTION,
            run_id=run.id,
            data={"questions": [{"header": "Perms", "question": "Allow write?"}]},
        )
        await escalator(event)

        mock_transport.send.assert_called_once()
        _ctx, response = mock_transport.send.call_args[0]
        assert "Perms" in response.text


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
