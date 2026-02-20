"""Unit tests for NotificationDispatcher.

Tests notification formatting, status filtering, and multi-transport dispatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.models import ExecutionRun, RunStatus
from gluon.notifier import _NOTIFY_STATUSES, _STATUS_LABELS, NotificationDispatcher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    *,
    prompt: str = "Fix the login bug",
    status: RunStatus = RunStatus.COMPLETED,
    cost_usd: float | None = 1.50,
    loop_count: int = 0,
    error_message: str | None = None,
) -> ExecutionRun:
    run = ExecutionRun(
        project_id="proj-1",
        prompt=prompt,
        initiator="test",
        status=status,
        cost_usd=cost_usd,
        loop_count=loop_count,
        error_message=error_message,
    )
    return run


def _make_mock_store(project_name: str = "my-project", mappings=None):
    store = MagicMock()
    store.list_channel_mappings_for_project.return_value = mappings or []
    project = MagicMock()
    project.name = project_name
    store.get_project.return_value = project
    return store


def _make_mapping(transport: str = "telegram", channel_id: str = "123"):
    m = MagicMock()
    m.transport = transport
    m.channel_id = channel_id
    return m


# ===================================================================
# Notification formatting
# ===================================================================


class TestFormat:
    def test_basic_format(self):
        run = _make_run()
        dispatcher = NotificationDispatcher(store=MagicMock())
        result = dispatcher._format(run, "my-project", RunStatus.COMPLETED)
        assert "my-project" in result
        assert "Completed" in result
        assert run.id[:8] in result

    def test_cost_included(self):
        run = _make_run(cost_usd=3.45)
        dispatcher = NotificationDispatcher(store=MagicMock())
        result = dispatcher._format(run, "proj", RunStatus.COMPLETED)
        assert "$3.45" in result

    def test_loops_included(self):
        run = _make_run(loop_count=5)
        dispatcher = NotificationDispatcher(store=MagicMock())
        result = dispatcher._format(run, "proj", RunStatus.COMPLETED)
        assert "5 loops" in result

    def test_error_message_on_failure(self):
        run = _make_run(status=RunStatus.FAILED, error_message="Connection refused")
        dispatcher = NotificationDispatcher(store=MagicMock())
        result = dispatcher._format(run, "proj", RunStatus.FAILED)
        assert "Connection refused" in result

    def test_error_message_truncated(self):
        long_error = "x" * 200
        run = _make_run(status=RunStatus.FAILED, error_message=long_error)
        dispatcher = NotificationDispatcher(store=MagicMock())
        result = dispatcher._format(run, "proj", RunStatus.FAILED)
        assert "..." in result

    def test_long_prompt_truncated(self):
        long_prompt = "a" * 200
        run = _make_run(prompt=long_prompt)
        dispatcher = NotificationDispatcher(store=MagicMock())
        result = dispatcher._format(run, "proj", RunStatus.COMPLETED)
        assert "..." in result
        assert len(result) < len(long_prompt) + 200

    def test_all_status_labels_defined(self):
        for status in _NOTIFY_STATUSES:
            assert status in _STATUS_LABELS

    def test_no_cost_or_loops(self):
        run = _make_run(cost_usd=None, loop_count=0)
        dispatcher = NotificationDispatcher(store=MagicMock())
        result = dispatcher._format(run, "proj", RunStatus.COMPLETED)
        assert "$" not in result
        assert "loops" not in result


# ===================================================================
# Notification dispatch logic
# ===================================================================


class TestNotify:
    @pytest.mark.asyncio
    async def test_skips_non_notify_status(self):
        store = _make_mock_store()
        dispatcher = NotificationDispatcher(store=store)
        run = _make_run(status=RunStatus.RUNNING)
        await dispatcher.notify(run, RunStatus.PENDING, RunStatus.RUNNING)
        store.list_channel_mappings_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_same_status(self):
        store = _make_mock_store()
        dispatcher = NotificationDispatcher(store=store)
        run = _make_run(status=RunStatus.COMPLETED)
        await dispatcher.notify(run, RunStatus.COMPLETED, RunStatus.COMPLETED)
        store.list_channel_mappings_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_no_transports(self):
        store = _make_mock_store()
        dispatcher = NotificationDispatcher(store=store, transports={})
        run = _make_run()
        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.COMPLETED)
        store.list_channel_mappings_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_no_mappings(self):
        store = _make_mock_store(mappings=[])
        transport = AsyncMock()
        dispatcher = NotificationDispatcher(store=store, transports={"telegram": transport})
        run = _make_run()
        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.COMPLETED)
        transport.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_to_mapped_transport(self):
        mapping = _make_mapping("telegram", "chat-456")
        store = _make_mock_store(mappings=[mapping])
        transport = AsyncMock()
        dispatcher = NotificationDispatcher(store=store, transports={"telegram": transport})
        run = _make_run()

        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.COMPLETED)
        transport.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sends_to_multiple_transports(self):
        m1 = _make_mapping("telegram", "chat-1")
        m2 = _make_mapping("discord", "chat-2")
        store = _make_mock_store(mappings=[m1, m2])
        t_telegram = AsyncMock()
        t_discord = AsyncMock()
        dispatcher = NotificationDispatcher(
            store=store,
            transports={"telegram": t_telegram, "discord": t_discord},
        )
        run = _make_run()

        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.COMPLETED)
        t_telegram.send.assert_awaited_once()
        t_discord.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_unmapped_transport(self):
        mapping = _make_mapping("slack", "chan-1")
        store = _make_mock_store(mappings=[mapping])
        transport = AsyncMock()
        dispatcher = NotificationDispatcher(store=store, transports={"telegram": transport})
        run = _make_run()

        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.COMPLETED)
        transport.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_transport_send_failure_doesnt_raise(self):
        mapping = _make_mapping("telegram", "chat-1")
        store = _make_mock_store(mappings=[mapping])
        transport = AsyncMock()
        transport.send = AsyncMock(side_effect=Exception("Network error"))
        dispatcher = NotificationDispatcher(store=store, transports={"telegram": transport})
        run = _make_run()

        # Should not raise
        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_review_status_triggers_notification(self):
        mapping = _make_mapping("telegram", "chat-1")
        store = _make_mock_store(mappings=[mapping])
        transport = AsyncMock()
        dispatcher = NotificationDispatcher(store=store, transports={"telegram": transport})
        run = _make_run(status=RunStatus.REVIEW)

        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.REVIEW)
        transport.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_status_triggers_notification(self):
        mapping = _make_mapping("telegram", "chat-1")
        store = _make_mock_store(mappings=[mapping])
        transport = AsyncMock()
        dispatcher = NotificationDispatcher(store=store, transports={"telegram": transport})
        run = _make_run(status=RunStatus.FAILED)

        await dispatcher.notify(run, RunStatus.RUNNING, RunStatus.FAILED)
        transport.send.assert_awaited_once()
