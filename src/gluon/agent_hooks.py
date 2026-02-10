"""SDK hook implementations for structured tool-use logging.

Provides PreToolUse and PostToolUse hooks that emit structured log events
for every tool call made by Claude Code agents. These hooks are wired into
ClaudeAgentOptions.hooks by GluonAgent._build_options().
"""

import logging
from typing import Any

from claude_agent_sdk import (
    HookContext,
    HookInput,
    HookMatcher,
)
from claude_agent_sdk.types import AsyncHookJSONOutput, SyncHookJSONOutput

logger = logging.getLogger(__name__)


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


def build_hooks() -> dict[str, list[Any]]:
    """Build hooks dict for ClaudeAgentOptions.hooks."""
    return {
        "PreToolUse": [HookMatcher(hooks=[log_pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[log_post_tool_use])],
    }
