"""Event types and constants for the Gluon Event Bus."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from gluon.models import utc_now


class EventCategory(StrEnum):
    LIFECYCLE = "lifecycle"
    INTERACTION = "interaction"
    EXECUTION = "execution"
    SYSTEM = "system"


class GluonEvent(BaseModel):
    """Base event — all events carry scope context."""

    type: str
    category: EventCategory
    timestamp: datetime = Field(default_factory=utc_now)
    workspace_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


# Lifecycle events
RUN_CREATED = "run.created"
RUN_UPDATED = "run.updated"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_CANCELLED = "run.cancelled"
RUN_REVIEW = "run.review"

# Interaction events
QUESTION_CREATED = "question.created"
QUESTION_ANSWERED = "question.answered"
QUESTION_ESCALATED = "question.escalated"
QUESTION_EXPIRED = "question.expired"
