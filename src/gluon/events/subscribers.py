"""Built-in event subscribers for the Gluon Event Bus.

Three subscribers:
1. websocket_broadcaster — routes events to WebSocket clients via ws_manager
2. notification_persister — stores notification-worthy events in DB
3. transport_dispatcher — sends terminal run events to Telegram/Discord
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gluon.events.bus import EventBus
from gluon.events.types import (
    QUESTION_ANSWERED,
    QUESTION_CREATED,
    QUESTION_ESCALATED,
    QUESTION_EXPIRED,
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_CREATED,
    RUN_FAILED,
    RUN_REVIEW,
    RUN_UPDATED,
    EventCategory,
    GluonEvent,
)
from gluon.models import (
    Notification,
    NotificationSeverity,
    NotificationType,
)

if TYPE_CHECKING:
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


async def websocket_broadcaster(event: GluonEvent) -> None:
    """Route events to WebSocket clients using existing ws_manager."""
    from gluon.web.websocket import ws_manager

    try:
        if event.type == RUN_CREATED:
            run = event.data.get("run")
            project_name = event.data.get("project_name", "")
            if run:
                await ws_manager.broadcast_run_created(run, project_name)

        elif event.type in (RUN_UPDATED, RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED, RUN_REVIEW):
            run = event.data.get("run")
            project_name = event.data.get("project_name", "")
            if run:
                await ws_manager.broadcast_run_update(run, project_name)

        elif event.type == QUESTION_CREATED:
            run_id = event.run_id or ""
            questions = event.data.get("questions", [])
            question_ids = event.data.get("question_ids", [])
            await ws_manager.broadcast_pending_questions(run_id, questions, question_ids)

        elif event.type == QUESTION_ANSWERED:
            run_id = event.run_id or ""
            question_id = event.data.get("question_id", "")
            await ws_manager.broadcast_question_answered(run_id, question_id)

        elif event.type == QUESTION_EXPIRED:
            run_id = event.run_id or ""
            question_ids = event.data.get("question_ids", [])
            await ws_manager.broadcast(
                {
                    "type": "questions_expired",
                    "run_id": run_id,
                    "question_ids": question_ids,
                    "reason": event.data.get("reason", "timeout"),
                }
            )

        # Emit a generic notification_created WS message for notification-worthy events
        if event.category == EventCategory.INTERACTION or event.type in (
            RUN_COMPLETED,
            RUN_FAILED,
            RUN_REVIEW,
        ):
            notification = event.data.get("notification")
            if notification and isinstance(notification, Notification):
                await ws_manager.broadcast(
                    {
                        "type": "notification_created",
                        "notification": {
                            "id": notification.id,
                            "type": notification.type.value,
                            "severity": notification.severity.value,
                            "title": notification.title,
                            "message": notification.message,
                            "run_id": notification.run_id,
                            "created_at": notification.created_at.isoformat(),
                            "read": notification.read,
                        },
                    }
                )
    except Exception:
        logger.debug("WebSocket broadcast from event bus failed", exc_info=True)


def _make_notification_persister(store: GluonStore):
    """Create a notification persister subscriber bound to a store."""

    async def notification_persister(event: GluonEvent) -> None:
        """Persist notification-worthy events to DB."""
        notification: Notification | None = None

        if event.type == QUESTION_CREATED:
            header = ""
            questions = event.data.get("questions", [])
            if questions:
                header = questions[0].get("header", "Question")
            notification = Notification(
                run_id=event.run_id,
                project_id=event.project_id,
                workspace_id=event.workspace_id,
                type=NotificationType.QUESTION,
                severity=NotificationSeverity.WARNING,
                title=f"Input required: {header}",
                message=questions[0].get("question", "") if questions else None,
                metadata={"question_ids": event.data.get("question_ids", [])},
            )

        elif event.type == RUN_COMPLETED:
            prompt = event.data.get("prompt", "")[:60]
            notification = Notification(
                run_id=event.run_id,
                project_id=event.project_id,
                workspace_id=event.workspace_id,
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title="Run completed",
                message=prompt,
            )

        elif event.type == RUN_FAILED:
            error = event.data.get("error_message", "")[:100]
            notification = Notification(
                run_id=event.run_id,
                project_id=event.project_id,
                workspace_id=event.workspace_id,
                type=NotificationType.FAILURE,
                severity=NotificationSeverity.ERROR,
                title="Run failed",
                message=error,
            )

        elif event.type == QUESTION_EXPIRED:
            notification = Notification(
                run_id=event.run_id,
                project_id=event.project_id,
                workspace_id=event.workspace_id,
                type=NotificationType.WARNING,
                severity=NotificationSeverity.ERROR,
                title="Run paused — question timed out",
                message="Questions expired after 5 minutes without response. The run has been paused.",
                metadata={"question_ids": event.data.get("question_ids", [])},
            )

        elif event.type == RUN_REVIEW:
            notification = Notification(
                run_id=event.run_id,
                project_id=event.project_id,
                workspace_id=event.workspace_id,
                type=NotificationType.REVIEW,
                severity=NotificationSeverity.WARNING,
                title="Run needs review",
                message=event.data.get("prompt", "")[:60],
            )

        if notification:
            try:
                store.create_notification(notification)
                # Attach notification to event data so websocket_broadcaster can use it
                event.data["notification"] = notification
            except Exception:
                logger.debug("Failed to persist notification", exc_info=True)

    return notification_persister


def _make_transport_dispatcher(store: GluonStore):
    """Create a transport dispatcher subscriber bound to a store."""

    async def transport_dispatcher(event: GluonEvent) -> None:
        """Route terminal run events to Telegram/Discord via NotificationDispatcher."""
        try:
            from gluon.notifier import NotificationDispatcher

            run = event.data.get("run")
            old_status = event.data.get("old_status")
            if not run:
                return

            notifier = NotificationDispatcher(store=store)
            await notifier.notify(run, old_status, run.status)
        except Exception:
            logger.debug("Transport dispatch failed", exc_info=True)

    return transport_dispatcher


def _make_question_escalator(store: GluonStore):
    """Create a question escalation subscriber that sends to Telegram/Discord."""

    async def question_escalator(event: GluonEvent) -> None:
        """Send unanswered question alert to Telegram/Discord channels."""
        try:
            from gluon.notifier import NotificationDispatcher

            run_id = event.run_id
            if not run_id:
                return

            run = store.get_run(run_id)
            if not run:
                return

            project = store.get_project(run.project_id)
            project_name = project.name if project else run.project_id[:8]

            questions = event.data.get("questions", [])
            header = questions[0].get("header", "Question") if questions else "Question"
            question_text = questions[0].get("question", "") if questions else ""
            lines = [
                f"⏳ **{project_name}** — Waiting for input (`{run_id[:8]}`)",
                f"> {header}: {question_text[:100]}",
                f"{len(questions)} question(s) pending · will pause in 2 minutes",
            ]
            text = "\n".join(lines)

            notifier = NotificationDispatcher(store=store)
            mappings = store.list_channel_mappings_for_project(run.project_id)
            for mapping in mappings:
                transport = notifier.transports.get(mapping.transport)
                if not transport:
                    continue
                try:
                    from gluon.transport.base import TransportContext, TransportResponse

                    ctx = TransportContext(
                        transport=mapping.transport,
                        user_id="gluon:system",
                        chat_id=mapping.channel_id,
                    )
                    await transport.send(ctx, TransportResponse(text=text, parse_mode="markdown"))
                except Exception:
                    logger.debug("Failed to escalate to %s:%s", mapping.transport, mapping.channel_id, exc_info=True)
        except Exception:
            logger.debug("Question escalation dispatch failed", exc_info=True)

    return question_escalator


def register_subscribers(bus: EventBus, store: GluonStore) -> None:
    """Register all built-in subscribers on the event bus."""
    notification_persister = _make_notification_persister(store)
    transport_dispatcher = _make_transport_dispatcher(store)
    question_escalator = _make_question_escalator(store)

    # Notification persister — only for notification-worthy events
    bus.subscribe(QUESTION_CREATED, notification_persister)
    bus.subscribe(QUESTION_EXPIRED, notification_persister)
    bus.subscribe(RUN_COMPLETED, notification_persister)
    bus.subscribe(RUN_FAILED, notification_persister)
    bus.subscribe(RUN_REVIEW, notification_persister)

    # WebSocket broadcaster — all events (registered after persister so notification is attached)
    bus.subscribe("*", websocket_broadcaster)

    # Transport dispatcher — terminal run events
    bus.subscribe(RUN_COMPLETED, transport_dispatcher)
    bus.subscribe(RUN_FAILED, transport_dispatcher)
    bus.subscribe(RUN_CANCELLED, transport_dispatcher)

    # Question escalation — send to Telegram/Discord at 3 minute mark
    bus.subscribe(QUESTION_ESCALATED, question_escalator)

    logger.info("Registered event bus subscribers: websocket, notification, transport, escalation")
