"""Lightweight async event dispatcher using asyncio.Queue."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from gluon.events.types import GluonEvent

logger = logging.getLogger(__name__)

FilterFn = Callable[[GluonEvent], bool]
Handler = Callable[[GluonEvent], Coroutine[Any, Any, None]]


@dataclass
class Subscriber:
    id: str
    handler: Handler
    filter_fn: FilterFn | None = None


class EventBus:
    """Lightweight async event dispatcher using asyncio.Queue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[GluonEvent] = asyncio.Queue()
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._wildcard_subscribers: list[Subscriber] = []
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

    async def emit(self, event: GluonEvent) -> None:
        """Queue event for dispatch (non-blocking)."""
        await self._queue.put(event)

    def subscribe(
        self,
        event_type: str,
        handler: Handler,
        filter_fn: FilterFn | None = None,
    ) -> str:
        """Register handler. Use '*' for all events. Returns subscriber_id."""
        sub = Subscriber(id=str(uuid4()), handler=handler, filter_fn=filter_fn)
        if event_type == "*":
            self._wildcard_subscribers.append(sub)
        else:
            self._subscribers.setdefault(event_type, []).append(sub)
        return sub.id

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a subscriber by ID."""
        self._wildcard_subscribers = [s for s in self._wildcard_subscribers if s.id != subscriber_id]
        for event_type in list(self._subscribers):
            self._subscribers[event_type] = [s for s in self._subscribers[event_type] if s.id != subscriber_id]
            if not self._subscribers[event_type]:
                del self._subscribers[event_type]

    async def start(self) -> None:
        """Start background dispatch loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("EventBus started")

    async def stop(self) -> None:
        """Graceful shutdown — drain queue then stop."""
        if not self._running:
            return
        self._running = False
        # Drain remaining events
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                await self._dispatch(event)
            except asyncio.QueueEmpty:
                break
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("EventBus stopped")

    async def _run_loop(self) -> None:
        """Background dispatch loop."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("EventBus loop error")

    async def _dispatch(self, event: GluonEvent) -> None:
        """Invoke matching subscribers. Exceptions logged, never block others."""
        # Exact-match subscribers
        exact = self._subscribers.get(event.type, [])
        # Prefix-match subscribers (e.g. "question.*" matches "question.created")
        prefix_matches: list[Subscriber] = []
        for pattern, subs in self._subscribers.items():
            if pattern.endswith(".*"):
                prefix = pattern[:-2]
                if event.type.startswith(prefix + ".") and pattern != event.type:
                    prefix_matches.extend(subs)

        all_subs = list(exact) + prefix_matches + list(self._wildcard_subscribers)

        for sub in all_subs:
            try:
                if sub.filter_fn and not sub.filter_fn(event):
                    continue
                await sub.handler(event)
            except Exception:
                logger.exception(
                    "EventBus subscriber %s failed for event %s",
                    sub.id[:8],
                    event.type,
                )
