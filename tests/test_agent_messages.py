"""Tests for agent.py message handling: stop_reason + typed task messages.

Verifies that GluonAgent.execute() correctly yields AgentMessage/AgentResult
for ResultMessage (stop_reason), TaskStartedMessage, TaskProgressMessage,
TaskNotificationMessage, and HookEventMessage from the SDK.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import (
    HookEventMessage,
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
)
from claude_agent_sdk.types import DeferredToolUse, TaskUsage

from gluon.agent import AgentMessage, AgentResult, GluonAgent

# ---------------------------------------------------------------------------
# Helper: run the agent execute loop with mock messages
# ---------------------------------------------------------------------------


async def _collect_from_execute(sdk_messages: list) -> list[AgentMessage | AgentResult]:
    """Patch GluonAgent internals and collect all yielded items."""

    # Build a mock async iterator for client.receive_response()
    async def mock_receive_response():
        for msg in sdk_messages:
            yield msg

    # Mock the SDK client context manager
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = mock_receive_response
    mock_client.get_mcp_status = AsyncMock(return_value={"mcpServers": []})

    mock_client_cls = MagicMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    # Create agent with real __init__ but minimal config
    with patch("gluon.agent.find_claude_cli", return_value=Path("/usr/local/bin/claude")):
        agent = GluonAgent(model="sonnet", max_thinking_tokens=0)

    items: list[AgentMessage | AgentResult] = []

    with (
        patch("gluon.agent.ClaudeSDKClient", mock_client_cls),
        patch("gluon.agent.ClaudeAgentOptions"),
        patch("gluon.agent.find_mcp_config", return_value=None),
    ):
        async for item in agent.execute(Path("/tmp"), "test prompt"):
            items.append(item)

    return items


def _make_result_message(stop_reason: str | None = "end_turn") -> ResultMessage:
    return ResultMessage(
        subtype="result",
        duration_ms=1000,
        duration_api_ms=800,
        is_error=False,
        num_turns=3,
        session_id="sess-123",
        stop_reason=stop_reason,
        total_cost_usd=0.5,
        usage={"input_tokens": 100, "output_tokens": 50},
        result="Done",
    )


# ===========================================================================
# Test 1a: stop_reason
# ===========================================================================


class TestStopReason:
    @pytest.mark.asyncio
    async def test_stop_reason_captured_from_result_message(self):
        msgs = [_make_result_message(stop_reason="end_turn")]
        items = await _collect_from_execute(msgs)

        result_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "result"]
        assert len(result_msgs) == 1
        assert result_msgs[0].metadata["stop_reason"] == "end_turn"

        agent_results = [i for i in items if isinstance(i, AgentResult)]
        assert len(agent_results) == 1
        assert agent_results[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stop_reason_none_when_not_set(self):
        msgs = [_make_result_message(stop_reason=None)]
        items = await _collect_from_execute(msgs)

        agent_results = [i for i in items if isinstance(i, AgentResult)]
        assert len(agent_results) == 1
        assert agent_results[0].stop_reason is None

    @pytest.mark.asyncio
    async def test_stop_reason_max_turns(self):
        msgs = [_make_result_message(stop_reason="max_turns")]
        items = await _collect_from_execute(msgs)

        result_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "result"]
        assert result_msgs[0].metadata["stop_reason"] == "max_turns"

        agent_results = [i for i in items if isinstance(i, AgentResult)]
        assert agent_results[0].stop_reason == "max_turns"


# ===========================================================================
# Test 1a-bis: error-type ResultMessage must not report success (WS-1)
# ===========================================================================


class TestResultMessageError:
    @pytest.mark.asyncio
    async def test_error_result_message_marks_failure(self):
        """An is_error=True ResultMessage must yield AgentResult(success=False)."""
        msg = _make_result_message()
        msg.is_error = True
        msg.result = "error_during_execution"
        items = await _collect_from_execute([msg])

        agent_results = [i for i in items if isinstance(i, AgentResult)]
        assert len(agent_results) == 1
        assert agent_results[0].success is False
        assert agent_results[0].error == "error_during_execution"

    @pytest.mark.asyncio
    async def test_successful_result_message_stays_success(self):
        items = await _collect_from_execute([_make_result_message()])
        agent_results = [i for i in items if isinstance(i, AgentResult)]
        assert agent_results[0].success is True
        assert agent_results[0].error is None


# ===========================================================================
# Test 1b: typed task messages
# ===========================================================================


class TestTypedTaskMessages:
    @pytest.mark.asyncio
    async def test_task_started_message_yielded(self):
        msgs = [
            TaskStartedMessage(
                subtype="task_started",
                data={},
                task_id="t1",
                description="Exploring codebase",
                uuid="u1",
                session_id="s1",
                task_type="explore",
                tool_use_id="tu1",
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        task_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "task_started"]
        assert len(task_msgs) == 1
        assert "Task started:" in task_msgs[0].content
        assert task_msgs[0].metadata["task_id"] == "t1"
        assert task_msgs[0].metadata["session_id"] == "s1"
        assert task_msgs[0].metadata["task_type"] == "explore"
        assert task_msgs[0].metadata["tool_use_id"] == "tu1"

    @pytest.mark.asyncio
    async def test_task_progress_message_yielded(self):
        msgs = [
            TaskProgressMessage(
                subtype="task_progress",
                data={},
                task_id="t1",
                description="Reading files",
                uuid="u2",
                session_id="s1",
                usage=TaskUsage(total_tokens=100, tool_uses=5, duration_ms=1000),
                last_tool_name="Read",
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        task_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "task_progress"]
        assert len(task_msgs) == 1
        assert task_msgs[0].content == "Reading files"
        assert task_msgs[0].metadata["task_id"] == "t1"
        assert task_msgs[0].metadata["last_tool_name"] == "Read"
        assert task_msgs[0].metadata["usage"]["total_tokens"] == 100

    @pytest.mark.asyncio
    async def test_task_progress_message_none_usage(self):
        msgs = [
            TaskProgressMessage(
                subtype="task_progress",
                data={},
                task_id="t1",
                description="Working",
                uuid="u2",
                session_id="s1",
                usage=None,
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        task_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "task_progress"]
        assert len(task_msgs) == 1
        assert task_msgs[0].metadata["usage"] is None

    @pytest.mark.asyncio
    async def test_task_notification_message_completed(self):
        msgs = [
            TaskNotificationMessage(
                subtype="task_notification",
                data={},
                task_id="t1",
                status="completed",
                summary="Found 3 files",
                output_file="/tmp/out",
                uuid="u3",
                session_id="s1",
                usage=TaskUsage(total_tokens=200, tool_uses=10, duration_ms=5000),
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        task_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "task_notification"]
        assert len(task_msgs) == 1
        assert "Task completed:" in task_msgs[0].content
        assert task_msgs[0].metadata["task_id"] == "t1"
        assert task_msgs[0].metadata["status"] == "completed"
        assert task_msgs[0].metadata["output_file"] == "/tmp/out"
        assert task_msgs[0].metadata["usage"]["total_tokens"] == 200

    @pytest.mark.asyncio
    async def test_task_notification_message_failed(self):
        msgs = [
            TaskNotificationMessage(
                subtype="task_notification",
                data={},
                task_id="t1",
                status="failed",
                summary="Error occurred",
                output_file="",
                uuid="u3",
                session_id="s1",
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        task_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "task_notification"]
        assert len(task_msgs) == 1
        assert "Task failed:" in task_msgs[0].content

    @pytest.mark.asyncio
    async def test_task_notification_message_none_usage(self):
        msgs = [
            TaskNotificationMessage(
                subtype="task_notification",
                data={},
                task_id="t1",
                status="completed",
                summary="Done",
                output_file="",
                uuid="u3",
                session_id="s1",
                usage=None,
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        task_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "task_notification"]
        assert len(task_msgs) == 1
        assert task_msgs[0].metadata["usage"] is None


# ===========================================================================
# SDK 0.1.74: HookEventMessage streaming
# ===========================================================================


class TestHookEventMessages:
    @pytest.mark.asyncio
    async def test_hook_event_yields_agent_message(self):
        """HookEventMessage should yield AgentMessage with type='hook_event'."""
        msgs = [
            HookEventMessage(
                subtype="hook_event",
                data={"tool_name": "Bash", "input": {"command": "ls"}},
                hook_event_name="PreToolUse",
                session_id="sess-456",
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        hook_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "hook_event"]
        assert len(hook_msgs) == 1
        assert hook_msgs[0].content == "PreToolUse"
        assert hook_msgs[0].metadata["hook_event_name"] == "PreToolUse"
        assert hook_msgs[0].metadata["data"]["tool_name"] == "Bash"
        assert hook_msgs[0].metadata["session_id"] == "sess-456"

    @pytest.mark.asyncio
    async def test_hook_event_not_treated_as_system_message(self):
        """HookEventMessage should not be handled by the SystemMessage branch."""
        msgs = [
            HookEventMessage(
                subtype="init",
                data={"session_id": "should-not-override"},
                hook_event_name="Stop",
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        hook_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "hook_event"]
        system_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "system"]
        assert len(hook_msgs) == 1
        assert len(system_msgs) == 0


# ===========================================================================
# SDK 0.1.74: deferred_tool_use on ResultMessage
# ===========================================================================


class TestDeferredToolUse:
    @pytest.mark.asyncio
    async def test_deferred_tool_use_surfaced_in_result_metadata(self):
        """When ResultMessage has deferred_tool_use, it should appear in result metadata."""
        result_msg = ResultMessage(
            subtype="result",
            duration_ms=500,
            duration_api_ms=400,
            is_error=False,
            num_turns=1,
            session_id="sess-789",
            stop_reason="deferred_tool_use",
            total_cost_usd=0.1,
            usage={"input_tokens": 50, "output_tokens": 20},
            result="Deferred",
            deferred_tool_use=DeferredToolUse(
                id="tool-1",
                name="Bash",
                input={"command": "rm -rf /"},
            ),
        )
        items = await _collect_from_execute([result_msg])

        result_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "result"]
        assert len(result_msgs) == 1
        deferred = result_msgs[0].metadata["deferred_tool_use"]
        assert deferred is not None
        assert deferred["id"] == "tool-1"
        assert deferred["name"] == "Bash"
        assert deferred["input"] == {"command": "rm -rf /"}

    @pytest.mark.asyncio
    async def test_no_deferred_tool_use_is_none(self):
        """When no deferred_tool_use, the metadata field should be None."""
        items = await _collect_from_execute([_make_result_message()])

        result_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "result"]
        assert len(result_msgs) == 1
        assert result_msgs[0].metadata["deferred_tool_use"] is None


# ===========================================================================
# SDK 0.2.82: RateLimitEvent streaming
# ===========================================================================


class TestRateLimitEvent:
    @pytest.mark.asyncio
    async def test_rate_limit_event_yields_agent_message(self):
        """RateLimitEvent should yield AgentMessage with type='rate_limit'."""
        from claude_agent_sdk import RateLimitEvent, RateLimitInfo

        msgs = [
            RateLimitEvent(
                rate_limit_info=RateLimitInfo(
                    status="allowed_warning",
                    resets_at=1700000000,
                    rate_limit_type="five_hour",
                    utilization=0.85,
                    raw={"status": "allowed_warning"},
                ),
                uuid="rl-1",
                session_id="sess-rl",
            ),
            _make_result_message(),
        ]
        items = await _collect_from_execute(msgs)

        rl_msgs = [i for i in items if isinstance(i, AgentMessage) and i.type == "rate_limit"]
        assert len(rl_msgs) == 1
        assert rl_msgs[0].metadata["status"] == "allowed_warning"
        assert rl_msgs[0].metadata["rate_limit_type"] == "five_hour"
        assert rl_msgs[0].metadata["utilization"] == 0.85
        assert rl_msgs[0].metadata["resets_at"] == 1700000000
