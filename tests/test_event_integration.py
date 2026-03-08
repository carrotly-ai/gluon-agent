"""Integration tests for event bus + notification system.

Tests the full pipeline of events being emitted, persisted to DB,
and broadcasted via WebSocket.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.events.bus import EventBus
from gluon.events.subscribers import register_subscribers
from gluon.events.types import (
    QUESTION_CREATED,
    RUN_COMPLETED,
    RUN_FAILED,
    EventCategory,
    GluonEvent,
)
from gluon.models import (
    NotificationSeverity,
    NotificationType,
)
from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path):
    """Create a temporary store for testing."""
    db_path = tmp_path / "test.db"
    return GluonStore(db_path=db_path)


@pytest.fixture
def bus():
    """Create an EventBus for testing."""
    return EventBus()


def _make_event(event_type: str, **kwargs) -> GluonEvent:
    """Helper to create events with defaults."""
    return GluonEvent(
        type=event_type,
        category=kwargs.pop("category", EventCategory.LIFECYCLE),
        **kwargs,
    )


class TestFullPipeline:
    """Tests for the full event → persister → broadcaster pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_run_completed(self, bus: EventBus, store: GluonStore):
        """Emit run.completed, verify notification is persisted with correct type/severity."""
        # Setup
        register_subscribers(bus, store)
        await bus.start()

        try:
            event = _make_event(
                RUN_COMPLETED,
                run_id="run-123",
                project_id="proj-1",
                workspace_id="ws-1",
                data={"prompt": "Fix the bug", "run": None},
            )

            # Emit event
            await bus.emit(event)
            await asyncio.sleep(0.2)  # Allow dispatch

            # Verify notification persisted
            notifications = store.list_notifications()
            assert len(notifications) == 1

            notif = notifications[0]
            assert notif.type == NotificationType.COMPLETION
            assert notif.severity == NotificationSeverity.SUCCESS
            assert notif.title == "Run completed"
            assert notif.message == "Fix the bug"
            assert notif.run_id == "run-123"
            assert notif.project_id == "proj-1"
            assert notif.workspace_id == "ws-1"
            assert notif.read is False
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_full_pipeline_question_created(self, bus: EventBus, store: GluonStore):
        """Emit question.created, verify notification persisted with type=QUESTION."""
        register_subscribers(bus, store)
        await bus.start()

        try:
            event = _make_event(
                QUESTION_CREATED,
                category=EventCategory.INTERACTION,
                run_id="run-456",
                project_id="proj-2",
                workspace_id="ws-1",
                data={
                    "questions": [{"header": "File permissions", "question": "Allow write?"}],
                    "question_ids": ["q-1"],
                },
            )

            await bus.emit(event)
            await asyncio.sleep(0.2)

            notifications = store.list_notifications()
            assert len(notifications) == 1

            notif = notifications[0]
            assert notif.type == NotificationType.QUESTION
            assert notif.severity == NotificationSeverity.WARNING
            assert "File permissions" in notif.title
            assert notif.message == "Allow write?"
            assert notif.run_id == "run-456"
            assert "q-1" in notif.metadata.get("question_ids", [])
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_full_pipeline_run_failed(self, bus: EventBus, store: GluonStore):
        """Emit run.failed, verify notification persisted with type=FAILURE."""
        register_subscribers(bus, store)
        await bus.start()

        try:
            event = _make_event(
                RUN_FAILED,
                run_id="run-789",
                project_id="proj-3",
                workspace_id="ws-1",
                data={"error_message": "Context length exceeded", "run": None},
            )

            await bus.emit(event)
            await asyncio.sleep(0.2)

            notifications = store.list_notifications()
            assert len(notifications) == 1

            notif = notifications[0]
            assert notif.type == NotificationType.FAILURE
            assert notif.severity == NotificationSeverity.ERROR
            assert notif.title == "Run failed"
            assert "Context length exceeded" in (notif.message or "")
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_persister_attaches_notification_to_event_data(self, bus: EventBus, store: GluonStore):
        """Verify persister attaches notification to event.data for broadcaster to use."""
        mock_ws = MagicMock()
        mock_ws.broadcast = AsyncMock()

        with patch("gluon.web.websocket.ws_manager", mock_ws):
            register_subscribers(bus, store)
            await bus.start()

            try:
                event = _make_event(
                    RUN_COMPLETED,
                    run_id="run-999",
                    project_id="proj-1",
                    workspace_id="ws-1",
                    data={"prompt": "Test", "run": None},
                )

                # Before emit, no notification in event data
                assert "notification" not in event.data

                await bus.emit(event)
                await asyncio.sleep(0.2)

                # After dispatch, persister attaches notification and broadcaster picks it up
                # We can verify by checking the broadcast call
                assert mock_ws.broadcast.call_count >= 1

                # Find the notification_created call
                for call in mock_ws.broadcast.call_args_list:
                    message = call[0][0]
                    if message.get("type") == "notification_created":
                        assert "notification" in message
                        assert message["notification"]["type"] == "completion"
                        break
                else:
                    pytest.fail("No notification_created broadcast found")
            finally:
                await bus.stop()

    @pytest.mark.asyncio
    async def test_persister_broadcaster_side_effect_chain(self, bus: EventBus, store: GluonStore):
        """Test side-effect chain: persister creates notification → broadcaster reads it."""
        mock_ws = MagicMock()
        mock_ws.broadcast_pending_questions = AsyncMock()
        mock_ws.broadcast = AsyncMock()

        with patch("gluon.web.websocket.ws_manager", mock_ws):
            register_subscribers(bus, store)
            await bus.start()

            try:
                event = _make_event(
                    QUESTION_CREATED,
                    category=EventCategory.INTERACTION,
                    run_id="run-side-effect",
                    project_id="proj-1",
                    workspace_id="ws-1",
                    data={
                        "questions": [{"header": "Test Q", "question": "Answer?"}],
                        "question_ids": ["q-side"],
                    },
                )

                await bus.emit(event)
                await asyncio.sleep(0.2)

                # Verify broadcaster was called with pending questions
                assert mock_ws.broadcast_pending_questions.call_count >= 1

                # Verify notification_created broadcast was called
                assert mock_ws.broadcast.call_count >= 1
                found_notification = False
                for call in mock_ws.broadcast.call_args_list:
                    message = call[0][0]
                    if message.get("type") == "notification_created":
                        found_notification = True
                        # Verify the notification from persister is included
                        assert "notification" in message
                        assert message["notification"]["type"] == "question"
                        break

                assert found_notification, "Expected notification_created broadcast"
            finally:
                await bus.stop()

    @pytest.mark.asyncio
    async def test_multiple_events_queued_and_dispatched(self, bus: EventBus, store: GluonStore):
        """Emit multiple events rapidly, verify all are persisted."""
        register_subscribers(bus, store)
        await bus.start()

        try:
            # Emit 3 different notification-worthy events
            await bus.emit(
                _make_event(
                    RUN_COMPLETED,
                    run_id="run-1",
                    data={"prompt": "Task 1"},
                )
            )
            await bus.emit(
                _make_event(
                    RUN_FAILED,
                    run_id="run-2",
                    data={"error_message": "Error 1"},
                )
            )
            await bus.emit(
                _make_event(
                    QUESTION_CREATED,
                    category=EventCategory.INTERACTION,
                    run_id="run-3",
                    data={
                        "questions": [{"header": "Q", "question": "?"}],
                        "question_ids": ["q-1"],
                    },
                )
            )

            await asyncio.sleep(0.3)

            notifications = store.list_notifications()
            assert len(notifications) == 3

            types = {n.type for n in notifications}
            assert types == {
                NotificationType.COMPLETION,
                NotificationType.FAILURE,
                NotificationType.QUESTION,
            }
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_unread_count_after_events(self, bus: EventBus, store: GluonStore):
        """Verify unread_count reflects newly persisted notifications."""
        register_subscribers(bus, store)
        await bus.start()

        try:
            # Unread count should be 0 initially
            assert store.get_unread_count() == 0

            # Emit 2 events
            await bus.emit(
                _make_event(
                    RUN_COMPLETED,
                    run_id="run-1",
                    data={"prompt": "Task 1"},
                )
            )
            await bus.emit(
                _make_event(
                    RUN_FAILED,
                    run_id="run-2",
                    data={"error_message": "Error 1"},
                )
            )

            await asyncio.sleep(0.2)

            # Unread count should be 2
            assert store.get_unread_count() == 2

            # Mark one as read
            notifications = store.list_notifications()
            store.mark_notification_read(notifications[0].id)

            # Unread count should be 1
            assert store.get_unread_count() == 1
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_event_with_workspace_filter(self, bus: EventBus, store: GluonStore):
        """Verify notifications can be filtered by workspace_id."""
        register_subscribers(bus, store)
        await bus.start()

        try:
            # Emit events for different workspaces
            await bus.emit(
                _make_event(
                    RUN_COMPLETED,
                    run_id="run-ws1",
                    workspace_id="ws-1",
                    data={"prompt": "WS1 task"},
                )
            )
            await bus.emit(
                _make_event(
                    RUN_COMPLETED,
                    run_id="run-ws2",
                    workspace_id="ws-2",
                    data={"prompt": "WS2 task"},
                )
            )

            await asyncio.sleep(0.2)

            # Query by workspace
            ws1_notifs = store.list_notifications(workspace_id="ws-1")
            ws2_notifs = store.list_notifications(workspace_id="ws-2")

            assert len(ws1_notifs) == 1
            assert len(ws2_notifs) == 1
            assert ws1_notifs[0].workspace_id == "ws-1"
            assert ws2_notifs[0].workspace_id == "ws-2"
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_notification_metadata_preserved(self, bus: EventBus, store: GluonStore):
        """Verify metadata is persisted correctly for question notifications."""
        register_subscribers(bus, store)
        await bus.start()

        try:
            question_ids = ["q-a", "q-b", "q-c"]
            await bus.emit(
                _make_event(
                    QUESTION_CREATED,
                    category=EventCategory.INTERACTION,
                    run_id="run-meta",
                    data={
                        "questions": [
                            {
                                "header": "Q1",
                                "question": "Question 1?",
                            }
                        ],
                        "question_ids": question_ids,
                    },
                )
            )

            await asyncio.sleep(0.2)

            notifications = store.list_notifications()
            assert len(notifications) == 1

            notif = notifications[0]
            assert notif.metadata.get("question_ids") == question_ids
        finally:
            await bus.stop()
