"""Tests for the EventBus."""

import asyncio

import pytest

from gluon.events.bus import EventBus
from gluon.events.types import EventCategory, GluonEvent


@pytest.fixture
def bus():
    return EventBus()


def _make_event(event_type: str = "test.event", **kwargs) -> GluonEvent:
    return GluonEvent(
        type=event_type,
        category=EventCategory.SYSTEM,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_emit_and_subscribe(bus: EventBus):
    """Events emitted are received by matching subscribers."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    bus.subscribe("test.event", handler)
    await bus.start()
    try:
        await bus.emit(_make_event())
        # Give the dispatch loop time to process
        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0].type == "test.event"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_wildcard_subscriber(bus: EventBus):
    """Wildcard subscribers receive all events."""
    received: list[str] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event.type)

    bus.subscribe("*", handler)
    await bus.start()
    try:
        await bus.emit(_make_event("a.one"))
        await bus.emit(_make_event("b.two"))
        await asyncio.sleep(0.1)
        assert received == ["a.one", "b.two"]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_prefix_match_subscriber(bus: EventBus):
    """Pattern 'question.*' matches 'question.created'."""
    received: list[str] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event.type)

    bus.subscribe("question.*", handler)
    await bus.start()
    try:
        await bus.emit(_make_event("question.created"))
        await bus.emit(_make_event("question.answered"))
        await bus.emit(_make_event("run.completed"))
        await asyncio.sleep(0.1)
        assert received == ["question.created", "question.answered"]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_filter_fn(bus: EventBus):
    """Filter function can narrow which events a subscriber receives."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    bus.subscribe(
        "test.event",
        handler,
        filter_fn=lambda e: e.run_id == "run-123",
    )
    await bus.start()
    try:
        await bus.emit(_make_event(run_id="run-123"))
        await bus.emit(_make_event(run_id="run-456"))
        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0].run_id == "run-123"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_unsubscribe(bus: EventBus):
    """Unsubscribed handlers stop receiving events."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    sub_id = bus.subscribe("test.event", handler)
    await bus.start()
    try:
        await bus.emit(_make_event())
        await asyncio.sleep(0.1)
        assert len(received) == 1

        bus.unsubscribe(sub_id)
        await bus.emit(_make_event())
        await asyncio.sleep(0.1)
        assert len(received) == 1  # no new events
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_exception_isolation(bus: EventBus):
    """One subscriber failing doesn't block others."""
    received: list[str] = []

    async def failing_handler(event: GluonEvent) -> None:
        raise RuntimeError("boom")

    async def good_handler(event: GluonEvent) -> None:
        received.append(event.type)

    bus.subscribe("test.event", failing_handler)
    bus.subscribe("test.event", good_handler)
    await bus.start()
    try:
        await bus.emit(_make_event())
        await asyncio.sleep(0.1)
        assert len(received) == 1
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_start_stop_lifecycle(bus: EventBus):
    """Bus can be started and stopped cleanly."""
    await bus.start()
    assert bus._running is True
    await bus.stop()
    assert bus._running is False


@pytest.mark.asyncio
async def test_stop_drains_queue(bus: EventBus):
    """Stop drains remaining events before shutting down."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    bus.subscribe("test.event", handler)
    # Don't start — put events directly on queue
    await bus._queue.put(_make_event())
    await bus._queue.put(_make_event())

    # Start and immediately stop — should drain
    bus._running = True
    await bus.stop()
    assert len(received) == 2


@pytest.mark.asyncio
async def test_double_start(bus: EventBus):
    """Calling start() twice should be safe — only one task exists."""
    await bus.start()
    assert bus._running is True
    task_1 = bus._task
    assert task_1 is not None

    # Second start should be no-op
    await bus.start()
    assert bus._running is True
    task_2 = bus._task
    assert task_2 is task_1  # Same task object

    await bus.stop()


@pytest.mark.asyncio
async def test_double_stop(bus: EventBus):
    """Calling stop() twice should be safe — no exception."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    bus.subscribe("test.event", handler)
    await bus.start()
    await bus.emit(_make_event())
    await asyncio.sleep(0.1)

    # First stop
    await bus.stop()
    assert bus._running is False

    # Second stop should be no-op, no exception
    await bus.stop()
    assert bus._running is False
    assert len(received) == 1


@pytest.mark.asyncio
async def test_emit_before_start(bus: EventBus):
    """Events emitted before start() should be dispatched once bus starts."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    bus.subscribe("test.event", handler)

    # Emit before start
    await bus.emit(_make_event())
    await bus.emit(_make_event())

    # Now start — queued events should be dispatched
    await bus.start()
    try:
        await asyncio.sleep(0.1)
        assert len(received) == 2
        assert all(e.type == "test.event" for e in received)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_concurrent_emit_from_multiple_coroutines(bus: EventBus):
    """Multiple coroutines emitting simultaneously should all be received."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    bus.subscribe("test.event", handler)
    await bus.start()
    try:

        async def emitter(index: int):
            # Use run_id to track which emission this was
            await bus.emit(_make_event(run_id=f"run-{index}"))

        # Emit 100 events concurrently
        await asyncio.gather(*[emitter(i) for i in range(100)])
        await asyncio.sleep(0.2)

        assert len(received) == 100
        # Verify all run_ids are present
        run_ids = {int(e.run_id.split("-")[1]) for e in received if e.run_id}
        assert run_ids == set(range(100))
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_sync_handler_fails_gracefully(bus: EventBus):
    """Sync handler passed to subscribe should fail gracefully — other subscribers work."""
    received: list[str] = []

    def sync_handler(event: GluonEvent) -> None:
        """Accidentally a sync function instead of async."""
        received.append("sync")

    async def good_handler(event: GluonEvent) -> None:
        received.append("good")

    bus.subscribe("test.event", sync_handler)  # type: ignore
    bus.subscribe("test.event", good_handler)
    await bus.start()
    try:
        await bus.emit(_make_event())
        await asyncio.sleep(0.1)
        # The good handler should still have been called despite sync handler failing
        assert "good" in received
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_unsubscribe_invalid_id(bus: EventBus):
    """Unsubscribing with nonexistent ID should be silent no-op."""
    received: list[GluonEvent] = []

    async def handler(event: GluonEvent) -> None:
        received.append(event)

    bus.subscribe("test.event", handler)
    await bus.start()
    try:
        # Unsubscribe with fake ID — should not raise
        bus.unsubscribe("nonexistent-id-12345")

        # Original subscriber should still work
        await bus.emit(_make_event())
        await asyncio.sleep(0.1)
        assert len(received) == 1
    finally:
        await bus.stop()
