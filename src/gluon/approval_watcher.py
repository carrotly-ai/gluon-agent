"""Approval watcher — polls for pending approvals and posts them to transports.

Decouples the approval mechanism (hook blocks polling the store) from the
transport notification (Telegram/Discord/etc). One watcher per transport
process; the watcher queries `list_pending_undelivered_approvals()` every
tick, hands each approval to the transport's `post_approval_request()`,
and calls `mark_approval_notified()` atomically so the same approval
isn't posted twice — even if multiple watchers race.

If the transport call fails, the approval stays in the queue and is retried
on the next tick. Delivery is eventually-consistent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from gluon.models import PendingApproval
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# How often the watcher polls for new un-notified approvals
DEFAULT_POLL_INTERVAL_SECS = 2


class ApprovalPoster(Protocol):
    """Interface a transport implements to receive approval notifications.

    The TelegramTransport (and eventually DiscordTransport) implement this
    by sending a message with Approve/Deny inline buttons.
    """

    async def post_approval_request(self, approval: PendingApproval) -> bool:
        """Deliver an approval notification to the transport's users.

        Returns True if the notification was successfully posted (and the
        watcher should mark it as notified), False if the transport couldn't
        deliver and the watcher should retry on the next tick.
        """
        ...


class ApprovalWatcher:
    """Background loop that surfaces pending approvals to a transport.

    Usage:
        watcher = ApprovalWatcher(store, transport)
        await watcher.start()
        ...
        await watcher.stop()
    """

    def __init__(
        self,
        store: GluonStore,
        poster: ApprovalPoster,
        *,
        poll_interval_secs: int = DEFAULT_POLL_INTERVAL_SECS,
        name: str = "approval-watcher",
    ):
        self.store = store
        self.poster = poster
        self.poll_interval_secs = poll_interval_secs
        self.name = name
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            logger.warning("ApprovalWatcher %s already running", self.name)
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("ApprovalWatcher %s started (poll=%ds)", self.name, self.poll_interval_secs)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("ApprovalWatcher %s stopped", self.name)

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval_secs)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ApprovalWatcher %s tick failed", self.name)

    async def tick(self) -> int:
        """One pass: fetch un-notified pending approvals and post each.

        Returns the number of approvals successfully posted this tick.
        """
        try:
            pending = self.store.list_pending_undelivered_approvals(limit=20)
        except Exception:
            logger.exception("ApprovalWatcher %s: failed to list pending approvals", self.name)
            return 0

        posted = 0
        for approval in pending:
            try:
                ok = await self.poster.post_approval_request(approval)
            except Exception:
                logger.exception(
                    "ApprovalWatcher %s: poster raised for approval %s",
                    self.name,
                    approval.id[:8],
                )
                ok = False

            if not ok:
                # Retry next tick
                continue

            # Atomically mark as notified — protects against duplicate posts
            # if two watchers run concurrently against the same DB.
            if self.store.mark_approval_notified(approval.id):
                posted += 1
                logger.info(
                    "Posted approval %s to %s",
                    approval.id[:8],
                    self.name,
                )

        return posted
