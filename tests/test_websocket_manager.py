"""Unit tests for WebSocketManager.

All tests are async and use AsyncMock WebSocket stubs.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.models import CircuitState, RunStatus
from gluon.web.websocket import WebSocketManager


def _make_ws() -> AsyncMock:
    """Create a mock WebSocket with send_json and accept methods."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    ws.accept = AsyncMock()
    return ws


def _make_run(**overrides) -> MagicMock:
    """Create a mock ExecutionRun for broadcast tests."""
    defaults = dict(
        id="run-001",
        project_id="proj-001",
        status=RunStatus.RUNNING,
        prompt="test task",
        initiator="test",
        created_at=MagicMock(isoformat=MagicMock(return_value="2026-01-01T00:00:00")),
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        error_message=None,
        cost_usd=None,
        use_worktree=False,
        branch_name=None,
        pr_number=None,
        pr_url=None,
        pr_status=None,
        pr_mergeable=None,
        archived=False,
        ralph_enabled=False,
        loop_count=0,
        max_loops=50,
        circuit_state=CircuitState.CLOSED,
        completion_confidence=0.0,
        completion_reason=None,
    )
    defaults.update(overrides)
    run = MagicMock(**defaults)
    # Ensure .value returns enum value for status and circuit_state
    run.status = defaults["status"]
    run.circuit_state = defaults["circuit_state"]
    return run


# ===================================================================
# Connection Lifecycle
# ===================================================================


class TestConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_connect_adds_to_set(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        assert ws in mgr.connections
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_set(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.disconnect(ws)
        assert ws not in mgr.connections

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_log_subscriptions(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.subscribe_logs(ws, "run-1")
        await mgr.disconnect(ws)
        assert "run-1" not in mgr.log_subscriptions

    @pytest.mark.asyncio
    async def test_multiple_connects(self):
        mgr = WebSocketManager()
        ws1, ws2, ws3 = _make_ws(), _make_ws(), _make_ws()
        await mgr.connect(ws1)
        await mgr.connect(ws2)
        await mgr.connect(ws3)
        assert len(mgr.connections) == 3

    @pytest.mark.asyncio
    async def test_disconnect_nonconnected_no_error(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.disconnect(ws)  # Should not raise
        assert len(mgr.connections) == 0


# ===================================================================
# Subscription Management
# ===================================================================


class TestSubscriptions:
    @pytest.mark.asyncio
    async def test_subscribe_logs(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.subscribe_logs(ws, "run-1")
        assert ws in mgr.log_subscriptions["run-1"]

    @pytest.mark.asyncio
    async def test_unsubscribe_logs(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.subscribe_logs(ws, "run-1")
        await mgr.unsubscribe_logs(ws, "run-1")
        assert "run-1" not in mgr.log_subscriptions

    @pytest.mark.asyncio
    async def test_unsubscribe_last_removes_key(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.subscribe_logs(ws, "run-1")
        await mgr.unsubscribe_logs(ws, "run-1")
        assert "run-1" not in mgr.log_subscriptions

    @pytest.mark.asyncio
    async def test_subscribe_multiple_runs(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.subscribe_logs(ws, "run-1")
        await mgr.subscribe_logs(ws, "run-2")
        assert ws in mgr.log_subscriptions["run-1"]
        assert ws in mgr.log_subscriptions["run-2"]

    @pytest.mark.asyncio
    async def test_unsubscribe_nonsubscribed_no_error(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.unsubscribe_logs(ws, "run-never")  # Should not raise


# ===================================================================
# Broadcasting
# ===================================================================


class TestBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self):
        mgr = WebSocketManager()
        ws1, ws2 = _make_ws(), _make_ws()
        await mgr.connect(ws1)
        await mgr.connect(ws2)

        msg = {"type": "test", "data": "hello"}
        await mgr.broadcast(msg)

        ws1.send_json.assert_awaited_once_with(msg)
        ws2.send_json.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self):
        mgr = WebSocketManager()
        await mgr.broadcast({"type": "test"})  # Should not raise

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_client(self):
        mgr = WebSocketManager()
        ws_ok = _make_ws()
        ws_bad = _make_ws()
        ws_bad.send_json = AsyncMock(side_effect=Exception("disconnected"))
        await mgr.connect(ws_ok)
        await mgr.connect(ws_bad)

        await mgr.broadcast({"type": "test"})

        # Good client got the message
        ws_ok.send_json.assert_awaited_once()
        # Bad client removed
        assert ws_bad not in mgr.connections

    @pytest.mark.asyncio
    async def test_send_to_subscribers_only(self):
        mgr = WebSocketManager()
        ws_sub = _make_ws()
        ws_other = _make_ws()
        await mgr.connect(ws_sub)
        await mgr.connect(ws_other)
        await mgr.subscribe_logs(ws_sub, "run-1")

        msg = {"type": "log_line", "run_id": "run-1", "line": "hello"}
        await mgr._send_to_subscribers("run-1", msg)

        ws_sub.send_json.assert_awaited_once_with(msg)
        ws_other.send_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_to_subscribers_no_subscribers(self):
        mgr = WebSocketManager()
        await mgr._send_to_subscribers("run-nonexistent", {"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_run_update(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)

        run = _make_run()
        await mgr.broadcast_run_update(run, "my-project")

        ws.send_json.assert_awaited_once()
        msg = ws.send_json.call_args[0][0]
        assert msg["type"] == "run_updated"
        assert msg["run"]["id"] == "run-001"

    @pytest.mark.asyncio
    async def test_broadcast_run_created(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)

        run = _make_run()
        await mgr.broadcast_run_created(run, "my-project")

        ws.send_json.assert_awaited_once()
        msg = ws.send_json.call_args[0][0]
        assert msg["type"] == "run_created"

    @pytest.mark.asyncio
    async def test_broadcast_question_answered(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)

        await mgr.broadcast_question_answered("run-1", "q-123")

        # broadcast_question_answered calls broadcast and _send_to_subscribers
        assert ws.send_json.await_count >= 1
        calls = [c[0][0] for c in ws.send_json.call_args_list]
        answered_msgs = [c for c in calls if c.get("type") == "question_answered"]
        assert len(answered_msgs) >= 1
        assert answered_msgs[0]["question_id"] == "q-123"


# ===================================================================
# Client Message Handling
# ===================================================================


class TestHandleClientMessage:
    @pytest.mark.asyncio
    async def test_subscribe_logs_message(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)

        await mgr.handle_client_message(ws, json.dumps({"type": "subscribe_logs", "run_id": "run-1"}))

        assert ws in mgr.log_subscriptions.get("run-1", set())
        # Should have sent a "subscribed" ack
        ws.send_json.assert_awaited()
        ack = ws.send_json.call_args[0][0]
        assert ack["type"] == "subscribed"

    @pytest.mark.asyncio
    async def test_unsubscribe_logs_message(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)
        await mgr.subscribe_logs(ws, "run-1")

        await mgr.handle_client_message(ws, json.dumps({"type": "unsubscribe_logs", "run_id": "run-1"}))

        assert "run-1" not in mgr.log_subscriptions
        # Should have sent an "unsubscribed" ack
        ack = ws.send_json.call_args[0][0]
        assert ack["type"] == "unsubscribed"

    @pytest.mark.asyncio
    async def test_ping_pong(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)

        await mgr.handle_client_message(ws, json.dumps({"type": "ping"}))

        ws.send_json.assert_awaited()
        pong = ws.send_json.call_args[0][0]
        assert pong["type"] == "pong"

    @pytest.mark.asyncio
    async def test_unknown_type_no_error(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)

        await mgr.handle_client_message(ws, json.dumps({"type": "unknown_message_type"}))
        # No error, no send
        ws.send_json.assert_not_awaited()


# ===================================================================
# Error Resilience
# ===================================================================


class TestErrorResilience:
    @pytest.mark.asyncio
    async def test_failed_send_doesnt_block_others(self):
        mgr = WebSocketManager()
        ws_ok = _make_ws()
        ws_bad = _make_ws()
        ws_bad.send_json = AsyncMock(side_effect=Exception("boom"))
        await mgr.connect(ws_ok)
        await mgr.connect(ws_bad)

        await mgr.broadcast({"type": "test"})

        ws_ok.send_json.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_client_removed_on_broadcast(self):
        mgr = WebSocketManager()
        ws_bad = _make_ws()
        ws_bad.send_json = AsyncMock(side_effect=Exception("gone"))
        await mgr.connect(ws_bad)

        await mgr.broadcast({"type": "test"})
        assert ws_bad not in mgr.connections

    @pytest.mark.asyncio
    async def test_invalid_json_no_crash(self):
        mgr = WebSocketManager()
        ws = _make_ws()
        await mgr.connect(ws)

        await mgr.handle_client_message(ws, "not valid json{{{")
        # No error raised, no send
        ws.send_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subscriber_failure_cleanup(self):
        mgr = WebSocketManager()
        ws_ok = _make_ws()
        ws_bad = _make_ws()
        ws_bad.send_json = AsyncMock(side_effect=Exception("disconnected"))
        await mgr.connect(ws_ok)
        await mgr.connect(ws_bad)
        await mgr.subscribe_logs(ws_ok, "run-1")
        await mgr.subscribe_logs(ws_bad, "run-1")

        await mgr._send_to_subscribers("run-1", {"type": "log_line", "line": "hi"})

        ws_ok.send_json.assert_awaited_once()
        # Bad client should be cleaned up from both connections and subscriptions
        assert ws_bad not in mgr.connections
        assert ws_bad not in mgr.log_subscriptions.get("run-1", set())
