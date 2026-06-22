"""Tests for Redis event transport (cross-process event propagation)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.events.bus import EventBus
from gluon.events.redis_transport import (
    CHANNEL_PREFIX,
    RedisEventTransport,
    get_redis_url,
    publish_event_via_redis,
)
from gluon.events.types import EventCategory, GluonEvent


class TestGetRedisUrl:
    def test_default_url(self):
        with patch.dict("os.environ", {}, clear=True):
            url = get_redis_url()
            assert url == "redis://localhost:6379/0"

    def test_custom_url(self):
        with patch.dict("os.environ", {"GLUON_REDIS_URL": "redis://myhost:1234/2"}):
            url = get_redis_url()
            assert url == "redis://myhost:1234/2"


class TestRedisEventTransport:
    def test_init_default_url(self):
        transport = RedisEventTransport()
        assert transport.redis_url == get_redis_url()
        assert transport._pub_client is None
        assert transport._running is False

    def test_init_custom_url(self):
        transport = RedisEventTransport("redis://custom:9999/1")
        assert transport.redis_url == "redis://custom:9999/1"

    @pytest.mark.asyncio
    async def test_publish_connects_lazily(self):
        """Publishing auto-connects if not already connected."""
        transport = RedisEventTransport()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.publish = AsyncMock()

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            event = GluonEvent(
                type="question.created",
                category=EventCategory.INTERACTION,
                run_id="run-1",
                data={"questions": []},
            )
            await transport.publish(event.model_dump_json(), event.type)

            mock_redis.ping.assert_awaited_once()
            mock_redis.publish.assert_awaited_once()
            call_args = mock_redis.publish.call_args
            assert call_args[0][0] == f"{CHANNEL_PREFIX}:question.created"

    @pytest.mark.asyncio
    async def test_publish_reuses_connection(self):
        """Multiple publishes reuse the same Redis connection."""
        transport = RedisEventTransport()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.publish = AsyncMock()

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis) as from_url:
            event = GluonEvent(
                type="test.event",
                category=EventCategory.SYSTEM,
                data={},
            )
            await transport.publish(event.model_dump_json(), event.type)
            await transport.publish(event.model_dump_json(), event.type)

            # from_url called once (lazy connect), publish called twice
            from_url.assert_called_once()
            assert mock_redis.publish.await_count == 2

    @pytest.mark.asyncio
    async def test_start_subscriber_creates_listener_task(self):
        """start_subscriber connects and creates a background task."""
        transport = RedisEventTransport()
        bus = EventBus()
        await bus.start()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        # Make listen() return an empty async iterator
        mock_pubsub.listen = MagicMock(return_value=_empty_async_iter())
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            await transport.start_subscriber(bus)

            assert transport._running is True
            assert transport._listener_task is not None
            mock_pubsub.psubscribe.assert_awaited_once_with(f"{CHANNEL_PREFIX}:*")

        await transport.stop()
        await bus.stop()

    @pytest.mark.asyncio
    async def test_start_subscriber_idempotent(self):
        """Calling start_subscriber twice doesn't create duplicate listeners."""
        transport = RedisEventTransport()
        bus = EventBus()
        await bus.start()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.listen = MagicMock(return_value=_empty_async_iter())
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            await transport.start_subscriber(bus)
            task1 = transport._listener_task
            await transport.start_subscriber(bus)
            task2 = transport._listener_task

            assert task1 is task2  # Same task, not duplicated

        await transport.stop()
        await bus.stop()

    @pytest.mark.asyncio
    async def test_listener_emits_to_event_bus(self):
        """Messages received from Redis are emitted to the local event bus."""
        transport = RedisEventTransport()
        bus = EventBus()

        received: list[GluonEvent] = []

        async def handler(event: GluonEvent) -> None:
            received.append(event)

        bus.subscribe("question.created", handler)
        await bus.start()

        event = GluonEvent(
            type="question.created",
            category=EventCategory.INTERACTION,
            run_id="run-abc",
            data={"questions": [{"header": "Test?"}], "question_ids": ["q1"]},
        )

        # Simulate receiving a message from Redis
        messages = [
            {
                "type": "pmessage",
                "pattern": f"{CHANNEL_PREFIX}:*",
                "channel": f"{CHANNEL_PREFIX}:question.created",
                "data": event.model_dump_json(),
            },
        ]

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.listen = MagicMock(return_value=_async_iter(messages))
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            await transport.start_subscriber(bus)
            # Give the listener task time to process
            await asyncio.sleep(0.1)

        await transport.stop()
        await bus.stop()

        assert len(received) == 1
        assert received[0].type == "question.created"
        assert received[0].run_id == "run-abc"

    @pytest.mark.asyncio
    async def test_listener_ignores_non_pmessage(self):
        """Non-pmessage types (subscribe confirmations etc) are skipped."""
        transport = RedisEventTransport()
        bus = EventBus()

        received: list[GluonEvent] = []

        async def handler(event: GluonEvent) -> None:
            received.append(event)

        bus.subscribe("*", handler)
        await bus.start()

        messages = [
            {"type": "psubscribe", "pattern": f"{CHANNEL_PREFIX}:*", "channel": None, "data": 1},
        ]

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.listen = MagicMock(return_value=_async_iter(messages))
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            await transport.start_subscriber(bus)
            await asyncio.sleep(0.05)

        await transport.stop()
        await bus.stop()

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_listener_handles_invalid_json(self):
        """Invalid JSON messages are logged and skipped, not fatal."""
        transport = RedisEventTransport()
        bus = EventBus()
        await bus.start()

        messages = [
            {
                "type": "pmessage",
                "pattern": f"{CHANNEL_PREFIX}:*",
                "channel": f"{CHANNEL_PREFIX}:bad",
                "data": "not-json{{{",
            },
        ]

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.listen = MagicMock(return_value=_async_iter(messages))
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            await transport.start_subscriber(bus)
            await asyncio.sleep(0.05)

        # Should not raise — graceful handling
        await transport.stop()
        await bus.stop()

    @pytest.mark.asyncio
    async def test_listener_reconnects_after_transient_error(self):
        """A transient listener error triggers reconnect, not a permanent crash."""
        transport = RedisEventTransport()
        transport._max_retries = 3
        transport._retry_base_delay = 0.0  # no backoff wait in tests
        bus = EventBus()

        received: list[GluonEvent] = []

        async def handler(event: GluonEvent) -> None:
            received.append(event)

        bus.subscribe("question.created", handler)
        await bus.start()

        event = GluonEvent(
            type="question.created",
            category=EventCategory.INTERACTION,
            run_id="run-reconnect",
            data={"questions": [], "question_ids": []},
        )
        message = {
            "type": "pmessage",
            "pattern": f"{CHANNEL_PREFIX}:*",
            "channel": f"{CHANNEL_PREFIX}:question.created",
            "data": event.model_dump_json(),
        }

        # First listen() raises (transient), second yields the message then ends.
        calls = {"n": 0}

        def listen_side_effect():
            calls["n"] += 1
            if calls["n"] == 1:
                return _raising_async_iter(TimeoutError("transient"))
            return _async_iter([message])

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.punsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()
        mock_pubsub.listen = MagicMock(side_effect=listen_side_effect)
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            await transport.start_subscriber(bus)
            await asyncio.sleep(0.1)

        await transport.stop()
        await bus.stop()

        # Reconnected and delivered the event despite the first-attempt failure.
        assert calls["n"] >= 2
        assert len(received) == 1
        assert received[0].run_id == "run-reconnect"

    @pytest.mark.asyncio
    async def test_listener_disables_after_max_retries(self):
        """After N consecutive failures the transport disables itself (polling fallback)."""
        transport = RedisEventTransport()
        transport._max_retries = 2
        transport._retry_base_delay = 0.0
        bus = EventBus()
        await bus.start()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.punsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()
        # Every listen() attempt fails — Redis is genuinely unavailable.
        mock_pubsub.listen = MagicMock(side_effect=lambda: _raising_async_iter(TimeoutError("down")))
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            await transport.start_subscriber(bus)
            # Let the listener exhaust its retries.
            for _ in range(50):
                if not transport._running:
                    break
                await asyncio.sleep(0.01)

        assert transport._running is False  # gave up, disabled
        # Attempted the initial connection plus _max_retries reconnects.
        assert mock_pubsub.listen.call_count >= transport._max_retries + 1

        await transport.stop()
        await bus.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self):
        """stop() closes all connections and cancels tasks."""
        transport = RedisEventTransport()
        mock_pub = AsyncMock()
        mock_sub = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.punsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        transport._pub_client = mock_pub
        transport._sub_client = mock_sub
        transport._pubsub = mock_pubsub
        transport._running = True
        transport._listener_task = asyncio.create_task(asyncio.sleep(100))

        await transport.stop()

        assert transport._running is False
        assert transport._listener_task is None
        assert transport._pubsub is None
        assert transport._pub_client is None
        assert transport._sub_client is None
        mock_pub.close.assert_awaited_once()
        mock_sub.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_publisher_only(self):
        """close_publisher() only closes the publish client."""
        transport = RedisEventTransport()
        mock_pub = AsyncMock()
        transport._pub_client = mock_pub

        await transport.close_publisher()

        assert transport._pub_client is None
        mock_pub.close.assert_awaited_once()


class TestPublishEventViaRedis:
    @pytest.mark.asyncio
    async def test_one_shot_publish(self):
        """publish_event_via_redis connects, publishes, and disconnects."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.close = AsyncMock()

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            event = GluonEvent(
                type="run.completed",
                category=EventCategory.LIFECYCLE,
                run_id="run-xyz",
                data={},
            )
            await publish_event_via_redis(event.model_dump_json(), event.type)

            mock_redis.publish.assert_awaited_once()
            channel = mock_redis.publish.call_args[0][0]
            assert channel == f"{CHANNEL_PREFIX}:run.completed"
            mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_one_shot_closes_on_error(self):
        """Connection is closed even if publish fails."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(side_effect=ConnectionError("boom"))
        mock_redis.close = AsyncMock()

        with patch("gluon.events.redis_transport.aioredis.from_url", return_value=mock_redis):
            with pytest.raises(ConnectionError):
                await publish_event_via_redis('{"type":"test"}', "test")

            mock_redis.close.assert_awaited_once()


# Helpers for async iteration in mocks


async def _empty_async_iter():
    return
    yield  # noqa: unreachable — makes this a proper async generator


async def _async_iter(items):
    for item in items:
        yield item


async def _raising_async_iter(exc):
    """An async iterator that raises ``exc`` on first iteration (simulates a
    dropped/timed-out pubsub connection)."""
    raise exc
    yield  # noqa: unreachable — makes this a proper async generator
