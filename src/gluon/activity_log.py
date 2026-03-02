"""Structured activity log for cross-agent queryable event stream.

Provides a fire-and-forget logging interface for orchestration events.
Events are stored in SQLite and queryable by actor, action, and time range.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from gluon.models import ActivityEvent

if TYPE_CHECKING:
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


class ActivityLogger:
    """Fire-and-forget activity event logger."""

    def __init__(self, store: "GluonStore"):
        self.store = store

    def log(
        self,
        actor: str,
        action: str,
        result: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an activity event. Fire-and-forget — exceptions are swallowed."""
        try:
            self.store.log_activity(
                actor=actor,
                action=action,
                result=result,
                message=message,
                metadata=metadata,
            )
        except Exception:
            logger.debug("Failed to log activity event", exc_info=True)

    def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[ActivityEvent]:
        """Query activity events with optional filters."""
        return self.store.list_activities(
            actor=actor,
            action=action,
            since=since,
            limit=limit,
        )

    def cleanup(self, days: int = 90) -> int:
        """Delete events older than N days. Returns count deleted."""
        return self.store.cleanup_activities(days=days)
