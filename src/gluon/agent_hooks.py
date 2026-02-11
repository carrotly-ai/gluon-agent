"""SDK hook implementations for structured tool-use logging and team lifecycle tracking.

Provides PreToolUse and PostToolUse hooks that emit structured log events
for every tool call made by Claude Code agents. Additionally provides
SubagentStart/SubagentStop hooks that track agent team member lifecycle
to prevent premature session termination.

These hooks are wired into ClaudeAgentOptions.hooks by GluonAgent._build_options().
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    HookContext,
    HookInput,
    HookMatcher,
)
from claude_agent_sdk.types import AsyncHookJSONOutput, SyncHookJSONOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subagent (team member) lifecycle tracker
# ---------------------------------------------------------------------------


@dataclass
class SubagentTracker:
    """Tracks active subagent count for agent team lifecycle.

    When an agent spawns team members via the Task tool, each member fires
    SubagentStart on creation and SubagentStop on completion.  This tracker
    maintains a count so the execute() loop can wait for all teammates to
    finish before tearing down the SDK session.
    """

    _count: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    all_done: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        # Initially "all done" (no subagents running)
        self.all_done.set()

    async def increment(self) -> None:
        async with self._lock:
            self._count += 1
            self.all_done.clear()
            logger.info("subagent_started", extra={"active_subagents": self._count})

    async def decrement(self) -> None:
        async with self._lock:
            self._count = max(0, self._count - 1)
            if self._count == 0:
                self.all_done.set()
            logger.info("subagent_stopped", extra={"active_subagents": self._count})

    @property
    def active_count(self) -> int:
        return self._count


# ---------------------------------------------------------------------------
# Hook callbacks
# ---------------------------------------------------------------------------


async def log_pre_tool_use(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> SyncHookJSONOutput | AsyncHookJSONOutput:
    """Log tool call before execution."""
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})

    input_keys = list(tool_input.keys()) if isinstance(tool_input, dict) else []
    logger.info(
        "sdk_tool_call_start",
        extra={
            "tool": tool_name,
            "tool_use_id": tool_use_id or input_data.get("tool_use_id"),
            "input_keys": input_keys,
        },
    )
    return {}


async def log_post_tool_use(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> SyncHookJSONOutput | AsyncHookJSONOutput:
    """Log tool call after execution."""
    tool_name = input_data.get("tool_name", "unknown")

    logger.info(
        "sdk_tool_call_end",
        extra={
            "tool": tool_name,
            "tool_use_id": tool_use_id or input_data.get("tool_use_id"),
        },
    )
    return {}


def _make_on_subagent_start(tracker: SubagentTracker):
    """Create a SubagentStart hook callback bound to *tracker*."""

    async def on_subagent_start(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput | AsyncHookJSONOutput:
        agent_id = input_data.get("agent_id", "unknown")
        agent_type = input_data.get("agent_type", "unknown")
        logger.info(
            "sdk_subagent_start",
            extra={"agent_id": agent_id, "agent_type": agent_type},
        )
        await tracker.increment()
        return {}

    return on_subagent_start


def _make_on_subagent_stop(tracker: SubagentTracker):
    """Create a SubagentStop hook callback bound to *tracker*."""

    async def on_subagent_stop(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput | AsyncHookJSONOutput:
        agent_id = input_data.get("agent_id", "unknown")
        agent_type = input_data.get("agent_type", "unknown")
        logger.info(
            "sdk_subagent_stop",
            extra={"agent_id": agent_id, "agent_type": agent_type},
        )
        await tracker.decrement()
        return {}

    return on_subagent_stop


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_hooks(tracker: SubagentTracker | None = None) -> dict[str, list[Any]]:
    """Build hooks dict for ClaudeAgentOptions.hooks.

    Args:
        tracker: Optional SubagentTracker for agent team lifecycle tracking.
                 When provided, SubagentStart and SubagentStop hooks are
                 registered so execute() can wait for all teammates to finish.
    """
    hooks: dict[str, list[Any]] = {
        "PreToolUse": [HookMatcher(hooks=[log_pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[log_post_tool_use])],
    }

    if tracker is not None:
        hooks["SubagentStart"] = [HookMatcher(hooks=[_make_on_subagent_start(tracker)])]
        hooks["SubagentStop"] = [HookMatcher(hooks=[_make_on_subagent_stop(tracker)])]

    return hooks
