"""Tests for the background-subagent wait/synthesize fix in GluonAgent.execute().

Background (Agent-tool) subagents run detached: the SDK returns the lead's
ResultMessage while they're still working and reports completion later via
terminal Task* messages. execute() must not tear the run down at the main
ResultMessage while tasks are outstanding — it must drain the stream until they
reach a terminal status, then nudge the lead to synthesize. This is independent
of the experimental agent_teams flag.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import (
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
)
from claude_agent_sdk.types import TaskUsage

from gluon.agent import AgentMessage, AgentResult, GluonAgent, _is_terminal_task_message

# --------------------------------------------------------------------------- #
# Message factories
# --------------------------------------------------------------------------- #


def _result(stop_reason: str = "end_turn", result: str = "done") -> ResultMessage:
    return ResultMessage(
        subtype="result",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="s1",
        stop_reason=stop_reason,
        total_cost_usd=0.01,
        usage={"input_tokens": 1, "output_tokens": 1},
        result=result,
    )


def _started(task_id: str = "t1") -> TaskStartedMessage:
    return TaskStartedMessage(
        subtype="task_started",
        data={},
        task_id=task_id,
        description="bg work",
        uuid="u",
        session_id="s1",
        task_type="general",
        tool_use_id="tu",
    )


def _progress(task_id: str = "t1") -> TaskProgressMessage:
    return TaskProgressMessage(
        subtype="task_progress",
        data={},
        task_id=task_id,
        description="working",
        uuid="u",
        session_id="s1",
        usage=TaskUsage(total_tokens=1, tool_uses=1, duration_ms=1),
        last_tool_name="Bash",
    )


def _notif(task_id: str = "t1", status: str = "completed") -> TaskNotificationMessage:
    return TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id=task_id,
        status=status,
        summary="ok",
        output_file="",
        uuid="u",
        session_id="s1",
    )


def _updated(task_id: str = "t1", status: str | None = "completed") -> TaskUpdatedMessage:
    return TaskUpdatedMessage(
        subtype="task_updated",
        data={},
        task_id=task_id,
        patch={"status": status} if status else {},
        status=status,
        session_id="s1",
        uuid="u",
    )


# --------------------------------------------------------------------------- #
# Harness: multi-turn receive_response + a receive_messages drain stream
# --------------------------------------------------------------------------- #


async def _run(turns: list[list], drain, timeout: float | None = None) -> list:
    """Drive execute() with a mock client.

    turns: message-lists, one consumed per receive_response() call (per turn).
    drain: a list of drain messages OR a zero-arg callable returning an async
           iterator (use a blocking one to exercise the timeout path).
    """
    turn_iter = iter(turns)

    def receive_response():
        try:
            msgs = next(turn_iter)
        except StopIteration:
            msgs = [_result()]

        async def gen():
            for m in msgs:
                yield m

        return gen()

    def receive_messages():
        if callable(drain):
            return drain()

        async def gen():
            for m in drain:
                yield m

        return gen()

    client = AsyncMock()
    client.query = AsyncMock()
    client.receive_response = receive_response
    client.receive_messages = receive_messages
    client.get_mcp_status = AsyncMock(return_value={"mcpServers": []})

    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("gluon.agent.find_claude_cli", return_value=Path("/usr/local/bin/claude")):
        agent = GluonAgent(model="sonnet", max_thinking_tokens=0)

    patches = [
        patch("gluon.agent.ClaudeSDKClient", client_cls),
        patch("gluon.agent.ClaudeAgentOptions"),
        patch("gluon.agent.find_mcp_config", return_value=None),
    ]
    if timeout is not None:
        patches.append(patch.object(GluonAgent, "_TEAM_WAIT_TIMEOUT", timeout))

    items: list = []
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        async for item in agent.execute(Path("/tmp"), "prompt"):
            items.append(item)
    return items, client


def _types(items) -> list[str]:
    return [i.type for i in items if isinstance(i, AgentMessage)]


def _has_system(items, content: str) -> bool:
    """The fix emits system-typed markers whose *content* names the event."""
    return any(isinstance(i, AgentMessage) and i.type == "system" and i.content == content for i in items)


# --------------------------------------------------------------------------- #
# _is_terminal_task_message
# --------------------------------------------------------------------------- #


def test_is_terminal_task_message_notification():
    assert _is_terminal_task_message(_notif(status="completed")) is True
    assert _is_terminal_task_message(_notif(status="failed")) is True
    assert _is_terminal_task_message(_notif(status="stopped")) is True


def test_is_terminal_task_message_updated_terminal_and_not():
    assert _is_terminal_task_message(_updated(status="completed")) is True
    assert _is_terminal_task_message(_updated(status="killed")) is True
    assert _is_terminal_task_message(_updated(status="in_progress")) is False
    # status carried only in patch (message-level status is None)
    m = _updated(status=None)
    m.patch = {"status": "completed"}
    assert _is_terminal_task_message(m) is True


def test_is_terminal_task_message_non_task():
    assert _is_terminal_task_message(_result()) is False
    assert _is_terminal_task_message(_started()) is False


# --------------------------------------------------------------------------- #
# execute() wait + synthesize behavior
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_background_task_awaited_then_synthesized():
    """TaskStarted at turn 1 → run must NOT end; drain until terminal, then nudge
    a synthesis turn. Final result comes from the synthesis turn."""
    turns = [
        [_started("t1"), _result(result="I'll report when the subagent finishes")],  # main turn
        [_result(result="Subagent output synthesized")],  # synthesis turn
    ]
    drain = [_progress("t1"), _updated("t1", "completed")]  # task reaches terminal
    items, client = await _run(turns, drain)

    assert _has_system(items, "awaiting_background_tasks")  # Check 0 engaged
    assert _has_system(items, "team_synthesis")  # synthesis was nudged
    # the synthesis nudge was actually sent to the model
    assert client.query.await_count >= 2  # initial prompt + synthesis nudge
    synth_calls = [c for c in client.query.await_args_list if "synthesize" in str(c).lower()]
    assert synth_calls, "expected a synthesis nudge query"
    # final result reflects the synthesis turn, not the premature main-turn text
    results = [i for i in items if isinstance(i, AgentResult)]
    assert len(results) == 1
    assert results[0].success is True


@pytest.mark.asyncio
async def test_terminal_via_notification_also_clears():
    """A background task cleared via a terminal TaskNotificationMessage (not just
    TaskUpdated) must also release the wait."""
    turns = [[_started("t1"), _result()], [_result(result="synth")]]
    drain = [_notif("t1", "completed")]
    items, client = await _run(turns, drain)
    assert _has_system(items, "team_synthesis")


@pytest.mark.asyncio
async def test_no_background_tasks_completes_normally():
    """Regression: a run with no background tasks must NOT drain or nudge — it
    completes at the main ResultMessage exactly as before the fix."""
    turns = [[_result(result="plain answer")]]
    items, client = await _run(turns, drain=[])
    assert not _has_system(items, "awaiting_background_tasks")
    assert not _has_system(items, "team_synthesis")
    # only the initial prompt query — no synthesis nudge
    assert client.query.await_count == 1
    results = [i for i in items if isinstance(i, AgentResult)]
    assert len(results) == 1 and results[0].success is True


@pytest.mark.asyncio
async def test_two_tasks_both_awaited_before_synthesis():
    """Two outstanding tasks → the wait holds until BOTH terminate."""
    turns = [[_started("t1"), _started("t2"), _result()], [_result(result="synth")]]
    drain = [_progress("t1"), _updated("t1", "completed"), _progress("t2"), _notif("t2", "completed")]
    items, client = await _run(turns, drain)
    assert _has_system(items, "team_synthesis")


@pytest.mark.asyncio
async def test_wait_times_out_and_ends_without_synthesis():
    """A background task that never terminates must not hang the run: after the
    timeout the run ends WITHOUT a synthesis nudge."""

    def blocking_drain():
        async def gen():
            yield _progress("t1")  # some progress, but no terminal ever
            await asyncio.Event().wait()  # block forever (asyncio.timeout cancels it)

        return gen()

    turns = [[_started("t1"), _result()]]
    items, client = await _run(turns, blocking_drain, timeout=0.05)

    assert _has_system(items, "awaiting_background_tasks")
    assert not _has_system(items, "team_synthesis")  # no synthesis against wedged tasks
    synth_calls = [c for c in client.query.await_args_list if "synthesize" in str(c).lower()]
    assert not synth_calls
    results = [i for i in items if isinstance(i, AgentResult)]
    assert len(results) == 1  # run still finished cleanly
