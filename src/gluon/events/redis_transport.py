"""Redis pub/sub transport for cross-process event propagation.

Solves the subprocess isolation problem: runner subprocesses have their own
event_bus/ws_manager instances that can't communicate with the web server.
This transport layer uses Redis pub/sub so events emitted in any process
reach the web server's event bus for dispatch to WebSocket clients.

Usage:
- Web server: creates RedisEventTransport, calls start_subscriber() to
  feed incoming events into the local event_bus for dispatch.
- Runner subprocess: creates RedisEventTransport, calls publish() to
  send events to Redis. No local subscribers needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from gluon.events.bus import EventBus

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "gluon:events"
ALL_EVENTS_CHANNEL = f"{CHANNEL_PREFIX}:*"


def get_redis_url() -> str:
    """Get Redis URL from environment or default to localhost."""
    return os.environ.get("GLUON_REDIS_URL", "redis://localhost:6379/0")


class RedisEventTransport:
    """Redis pub/sub transport for cross-process GluonEvent propagation."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or get_redis_url()
        self._pub_client: aioredis.Redis | None = None
        self._sub_client: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._running = False
        # Subscriber resilience: reconnect on transient errors, then give up and
        # let the UI fall back to polling. Redis is best-effort (cross-process
        # event push), never required — see module docstring.
        self._max_retries = int(os.environ.get("GLUON_REDIS_MAX_RETRIES", "5"))
        self._retry_base_delay = float(os.environ.get("GLUON_REDIS_RETRY_BASE_DELAY", "0.5"))
        self._retry_max_delay = float(os.environ.get("GLUON_REDIS_RETRY_MAX_DELAY", "30"))

    async def connect_publisher(self) -> None:
        """Connect the publish client. Used by runner subprocesses."""
        if self._pub_client is None:
            self._pub_client = aioredis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
            await self._pub_client.ping()  # type: ignore[misc]
            logger.info("Redis event transport: publisher connected")

    async def publish(self, event_json: str, event_type: str) -> None:
        """Publish a serialized GluonEvent to Redis.

        Publishes to both a type-specific channel and the catch-all channel
        so subscribers can filter by category or receive everything.

        Args:
            event_json: JSON-serialized GluonEvent
            event_type: Event type string (e.g. "question.created")
        """
        if self._pub_client is None:
            await self.connect_publisher()
        assert self._pub_client is not None

        # Publish to type-specific channel (e.g. gluon:events:question.created)
        channel = f"{CHANNEL_PREFIX}:{event_type}"
        await self._pub_client.publish(channel, event_json)

    async def start_subscriber(self, event_bus: EventBus) -> None:
        """Subscribe to all event channels and feed into the local event_bus.

        Used by the web server process to receive events from runner subprocesses.

        Args:
            event_bus: The local EventBus instance to dispatch events into
        """
        if self._running:
            return

        # Initial connect is synchronous so a Redis that's down at startup surfaces
        # immediately to the caller (which logs a warning and continues). Once the
        # listener is running, transient drops are handled by reconnect-with-retry.
        await self._connect_subscriber()

        self._running = True
        self._listener_task = asyncio.create_task(self._listen(event_bus), name="redis-event-listener")
        logger.info("Redis event transport: subscriber started (pattern=%s)", ALL_EVENTS_CHANNEL)

    async def _connect_subscriber(self) -> None:
        """(Re)establish the subscriber connection and pattern subscription.

        Closes any prior subscriber connection first so reconnects don't leak.

        Connection options matter for pubsub correctness:
        - ``socket_timeout=None``: a pubsub listener must *block* on reads waiting
          for messages. A non-None socket timeout (e.g. inherited from a
          ``?socket_timeout=`` in the URL) makes idle reads raise ``TimeoutError``
          even against a healthy Redis — the root cause of the listener churn.
        - ``socket_connect_timeout``: still bound the initial connect so an
          unreachable Redis fails fast into the reconnect/giveup path instead of
          hanging forever.
        - ``socket_keepalive`` + ``health_check_interval``: detect genuinely dead
          peers so a half-open socket surfaces as an error (→ reconnect) rather
          than blocking indefinitely.
        """
        await self._close_subscriber()
        self._sub_client = aioredis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=None,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
        )
        await self._sub_client.ping()  # type: ignore[misc]

        self._pubsub = self._sub_client.pubsub()
        # Subscribe to all gluon event channels via pattern
        await self._pubsub.psubscribe(ALL_EVENTS_CHANNEL)

    async def _close_subscriber(self) -> None:
        """Tear down the subscriber pubsub + client (best-effort, never raises)."""
        if self._pubsub is not None:
            try:
                await self._pubsub.punsubscribe()
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None
        if self._sub_client is not None:
            try:
                await self._sub_client.close()
            except Exception:
                pass
            self._sub_client = None

    async def _listen(self, event_bus: EventBus) -> None:
        """Background loop: receive Redis messages and emit to local event_bus.

        Resilient to transient Redis failures: on a listener error the connection
        is rebuilt and the loop retries, up to ``_max_retries`` consecutive
        failures. Past that, the transport gives up and disables itself
        (``_running = False``) so the UI falls back to polling — Redis is
        best-effort cross-process push, never required. Receiving an actual
        cross-process message resets the failure counter, so isolated blips never
        accumulate toward the cap over a long-lived connection. The reset happens
        only on a genuine ``pmessage`` — not on the subscribe-confirmation frame —
        otherwise a connection that subscribes successfully but then fails every
        read would reset to zero each cycle and retry forever instead of
        eventually giving up.
        """
        from gluon.events.types import GluonEvent

        attempt = 0
        while self._running:
            try:
                # The initial connection is established by start_subscriber; on a
                # reconnect the prior except-branch dropped it, so rebuild here.
                if self._pubsub is None:
                    await self._connect_subscriber()
                assert self._pubsub is not None

                async for message in self._pubsub.listen():
                    if not self._running:
                        break
                    if message["type"] != "pmessage":
                        continue
                    attempt = 0  # genuine cross-process delivery → healthy

                    try:
                        data = json.loads(message["data"])
                        event = GluonEvent(**data)
                        await event_bus.emit(event)
                    except Exception:
                        logger.debug(
                            "Redis event transport: failed to parse message: %s",
                            str(message.get("data", ""))[:200],
                            exc_info=True,
                        )
                # listen() returned without error → connection closed cleanly; stop.
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                attempt += 1
                if attempt > self._max_retries:
                    logger.warning(
                        "Redis event transport unavailable after %d retries; disabling "
                        "cross-process events (UI falls back to polling): %s",
                        self._max_retries,
                        e,
                    )
                    break
                delay = min(self._retry_base_delay * (2 ** (attempt - 1)), self._retry_max_delay)
                logger.warning(
                    "Redis event transport listener error (attempt %d/%d), reconnecting in %.1fs: %s",
                    attempt,
                    self._max_retries,
                    delay,
                    e,
                )
                # Drop the dead connection so the next iteration reconnects.
                await self._close_subscriber()
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break

        self._running = False

    async def stop(self) -> None:
        """Stop subscriber and close all connections."""
        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        await self._close_subscriber()

        if self._pub_client:
            try:
                await self._pub_client.close()
            except Exception:
                pass
            self._pub_client = None

        logger.info("Redis event transport: stopped")

    async def close_publisher(self) -> None:
        """Close just the publisher connection (used by runner subprocess cleanup)."""
        if self._pub_client:
            try:
                await self._pub_client.close()
            except Exception:
                pass
            self._pub_client = None


async def publish_event_via_redis(event_json: str, event_type: str, redis_url: str | None = None) -> None:
    """One-shot helper: publish a single event and disconnect.

    Convenience for runner subprocesses that need to fire-and-forget.
    For multiple publishes, prefer creating a RedisEventTransport instance.
    """
    url = redis_url or get_redis_url()
    client = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        channel = f"{CHANNEL_PREFIX}:{event_type}"
        await client.publish(channel, event_json)
    finally:
        await client.close()
