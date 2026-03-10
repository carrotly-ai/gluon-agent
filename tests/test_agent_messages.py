"""Tests for agent.py message handling: stop_reason + typed task messages.

Verifies that GluonAgent.execute() correctly yields AgentMessage/AgentResult
for ResultMessage (stop_reason), TaskStartedMessage, TaskProgressMessage,
and TaskNotificationMessage from the SDK.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import (
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
)
from claude_agent_sdk.types import TaskUsage

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
