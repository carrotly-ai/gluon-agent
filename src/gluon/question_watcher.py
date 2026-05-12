"""Question watcher — polls pending questions and posts them to transports.

Mirrors `approval_watcher.ApprovalWatcher` for AskUserQuestion: the runner
loop persists `PendingQuestion` rows and polls them, but it has no way to
reach Telegram/Discord. This watcher bridges that gap by streaming
un-notified pending questions to a transport that knows how to render them
in the originating channel.

One watcher per transport process. `mark_question_notified()` is atomic, so
multiple watchers across processes won't double-post.

If the transport call fails, the question stays in the queue and is retried
on the next tick. Delivery is eventually-consistent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from gluon.models import PendingQuestion
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# How often the watcher polls for new un-notified questions. Set tighter
# than the approval poll because question UX is interactive — users notice
# a 5s lag less for approvals than they do for "the bot asked me something."
DEFAULT_POLL_INTERVAL_SECS = 2


class QuestionPoster(Protocol):
    """Interface a transport implements to receive question notifications.

    The DiscordTransport implements this by sending an embed with a
    select-menu of options into the channel the run originated from.
    """

    async def post_question_request(self, question: PendingQuestion) -> bool:
        """Deliver a pending question to the transport's users.

        Returns True if the notification was successfully posted (so the
        watcher marks it notified), False if delivery failed and the
        watcher should retry on the next tick.
        """
        ...


class QuestionWatcher:
    """Background loop that surfaces pending questions to a transport.

    Usage:
        watcher = QuestionWatcher(store, transport)
        await watcher.start()
        ...
        await watcher.stop()
    """

    def __init__(
        self,
        store: GluonStore,
        poster: QuestionPoster,
        *,
        poll_interval_secs: int = DEFAULT_POLL_INTERVAL_SECS,
        name: str = "question-watcher",
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
            logger.warning("QuestionWatcher %s already running", self.name)
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("QuestionWatcher %s started (poll=%ds)", self.name, self.poll_interval_secs)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("QuestionWatcher %s stopped", self.name)

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval_secs)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("QuestionWatcher %s tick failed", self.name)

    async def tick(self) -> int:
        """One pass: fetch un-notified pending questions and post each.

        Returns the number of questions successfully posted this tick.
        """
        try:
            pending = self.store.list_pending_undelivered_questions(limit=20)
        except Exception:
            logger.exception("QuestionWatcher %s: failed to list pending questions", self.name)
            return 0

        posted = 0
        for question in pending:
            try:
                ok = await self.poster.post_question_request(question)
            except Exception:
                logger.exception(
                    "QuestionWatcher %s: poster raised for question %s",
                    self.name,
                    question.id[:8],
                )
                ok = False

            if not ok:
                # Retry next tick
                continue

            # Atomically mark as notified — protects against duplicate posts
            # if two watchers run concurrently against the same DB.
            if self.store.mark_question_notified(question.id):
                posted += 1
                logger.info(
                    "Posted question %s to %s",
                    question.id[:8],
                    self.name,
                )

        return posted
