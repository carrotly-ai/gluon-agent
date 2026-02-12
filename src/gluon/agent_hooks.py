"""SDK hook implementations for structured tool-use logging and team lifecycle tracking.

Provides PreToolUse and PostToolUse hooks that emit structured log events
for every tool call made by Claude Code agents. Additionally provides
SubagentStart/SubagentStop hooks that track agent team member lifecycle
to prevent premature session termination.

These hooks are wired into ClaudeAgentOptions.hooks by GluonAgent._build_options().
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    HookContext,
    HookInput,
    HookMatcher,
)
from claude_agent_sdk.types import AsyncHookJSONOutput, SyncHookJSONOutput

if TYPE_CHECKING:
    from gluon.image_storage import ImageStorageService
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Pattern to match `agent-browser screenshot <path>` commands
_SCREENSHOT_RE = re.compile(
    r"agent-browser\s+screenshot\s+(?:--\S+\s+)*(.+\.(?:png|jpg|jpeg))",
    re.IGNORECASE,
)


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

    async def reset(self) -> None:
        """Reset tracker to initial state (no active subagents).

        Called after team synthesis to clear stale counts from nested/orphaned
        subagents whose SubagentStop hooks never fired.
        """
        async with self._lock:
            self._count = 0
            self.all_done.set()

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
# Screenshot interception
# ---------------------------------------------------------------------------


@dataclass
class ScreenshotCollector:
    """Intercepts agent-browser screenshot commands and saves output as run attachments."""

    run_id: str
    working_dir: Path
    image_service: ImageStorageService
    store: GluonStore
    message_callback: Callable[[dict[str, Any]], None] | None = None
    _collected: list[str] = field(default_factory=list)  # image IDs

    @property
    def collected_ids(self) -> list[str]:
        return list(self._collected)


def _make_screenshot_interceptor(collector: ScreenshotCollector):
    """Create a PostToolUse hook that intercepts agent-browser screenshot output."""

    async def on_post_bash(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput | AsyncHookJSONOutput:
        tool_name = input_data.get("tool_name", "")
        if tool_name != "Bash":
            return {}

        command = ""
        tool_input = input_data.get("tool_input", {})
        if isinstance(tool_input, dict):
            command = tool_input.get("command", "")

        match = _SCREENSHOT_RE.search(command)
        if not match:
            return {}

        file_path_str = match.group(1).strip().strip("'\"")
        screenshot_path = Path(file_path_str)

        # Resolve relative paths against working directory
        if not screenshot_path.is_absolute():
            screenshot_path = collector.working_dir / screenshot_path

        # Strategy 1: Read file from disk (primary)
        if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
            try:
                data = screenshot_path.read_bytes()
                original_name = screenshot_path.name
                mime = "image/png" if original_name.endswith(".png") else "image/jpeg"
                image = collector.image_service.save_image(data, original_name, mime)
                collector.store.attach_image_to_run(collector.run_id, image.id, source="screenshot")
                collector._collected.append(image.id)
                logger.info(
                    "screenshot_collected",
                    extra={
                        "run_id": collector.run_id[:8],
                        "image_id": image.id[:8],
                        "path": str(screenshot_path),
                        "size": len(data),
                    },
                )
                if collector.message_callback:
                    collector.message_callback(
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "type": "screenshot",
                            "content": original_name,
                            "metadata": {
                                "image_id": image.id,
                                "original_name": original_name,
                                "size_bytes": len(data),
                            },
                        }
                    )
            except Exception:
                logger.warning("Failed to collect screenshot from disk", exc_info=True)
            return {}

        # Strategy 2: Parse base64 from tool response JSON
        tool_response = input_data.get("tool_response", "")
        if isinstance(tool_response, str) and tool_response.strip():
            try:
                resp = json.loads(tool_response)
                b64_data = None
                if isinstance(resp, dict):
                    b64_data = (resp.get("data") or {}).get("base64") if isinstance(resp.get("data"), dict) else None
                if b64_data:
                    data = base64.b64decode(b64_data)
                    original_name = Path(file_path_str).name
                    mime = "image/png" if original_name.endswith(".png") else "image/jpeg"
                    image = collector.image_service.save_image(data, original_name, mime)
                    collector.store.attach_image_to_run(collector.run_id, image.id, source="screenshot")
                    collector._collected.append(image.id)
                    logger.info(
                        "screenshot_collected_base64",
                        extra={
                            "run_id": collector.run_id[:8],
                            "image_id": image.id[:8],
                            "size": len(data),
                        },
                    )
                    if collector.message_callback:
                        collector.message_callback(
                            {
                                "timestamp": datetime.now(UTC).isoformat(),
                                "type": "screenshot",
                                "content": original_name,
                                "metadata": {
                                    "image_id": image.id,
                                    "original_name": original_name,
                                    "size_bytes": len(data),
                                },
                            }
                        )
            except (json.JSONDecodeError, Exception):
                logger.debug("No base64 screenshot data in tool response")

        return {}

    return on_post_bash


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_hooks(
    tracker: SubagentTracker | None = None,
    screenshot_collector: ScreenshotCollector | None = None,
) -> dict[str, list[Any]]:
    """Build hooks dict for ClaudeAgentOptions.hooks.

    Args:
        tracker: Optional SubagentTracker for agent team lifecycle tracking.
                 When provided, SubagentStart and SubagentStop hooks are
                 registered so execute() can wait for all teammates to finish.
        screenshot_collector: Optional ScreenshotCollector for intercepting
                 agent-browser screenshot commands and saving them as attachments.
    """
    post_tool_hooks: list[Any] = [log_post_tool_use]
    if screenshot_collector is not None:
        post_tool_hooks.append(_make_screenshot_interceptor(screenshot_collector))

    hooks: dict[str, list[Any]] = {
        "PreToolUse": [HookMatcher(hooks=[log_pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=post_tool_hooks)],
    }

    if tracker is not None:
        hooks["SubagentStart"] = [HookMatcher(hooks=[_make_on_subagent_start(tracker)])]
        hooks["SubagentStop"] = [HookMatcher(hooks=[_make_on_subagent_stop(tracker)])]

    return hooks
