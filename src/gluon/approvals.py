"""Approval gate logic — tool-call classification + PreToolUse hook (Theme D1).

Two pieces:
  1. `classify_tool_call(policy, tool_name, tool_input)` — returns
     (needs_approval, reason). Pure function, easily unit-testable.
  2. `_make_approval_hook(store, run_id, policy, notifier)` — builds a
     PreToolUse hook that creates a PendingApproval record, posts a
     notification, and polls the store until the approval is decided.

The hook blocks the inner tool call via asyncio.sleep — the SDK is waiting
on our async return. Keep the wait under the API-side timeout (~10 min);
default is APPROVAL_TIMEOUT_SECS=300.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import HookContext, HookInput, PreToolUseHookInput
from claude_agent_sdk.types import AsyncHookJSONOutput, SyncHookJSONOutput

from gluon.models import (
    APPROVAL_POLL_INTERVAL_SECS,
    APPROVAL_TIMEOUT_SECS,
    ApprovalPolicy,
    ApprovalStatus,
    utc_now,
)

if TYPE_CHECKING:
    from gluon.notifier import NotificationDispatcher
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool classifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalDecision:
    """Result of classifying a single tool call."""

    needs_approval: bool
    reason: str  # Empty string when no approval needed


# Patterns that always trigger approval under CAREFUL policy. These are
# intentionally broad — false positives are safer than false negatives.
_CAREFUL_BASH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+[^;]*-[rRf]", re.IGNORECASE), "rm with recursive/force flags"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f", re.IGNORECASE), "rm -rf"),
    (re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r", re.IGNORECASE), "rm -fr"),
    (re.compile(r"git\s+push\s+[^;]*--force", re.IGNORECASE), "git push --force"),
    (re.compile(r"git\s+push\s+[^;]*-f\b", re.IGNORECASE), "git push -f"),
    (re.compile(r"git\s+reset\s+[^;]*--hard", re.IGNORECASE), "git reset --hard"),
    (re.compile(r"git\s+clean\s+[^;]*-f", re.IGNORECASE), "git clean -f"),
    (re.compile(r"git\s+branch\s+[^;]*-D\b"), "git branch -D (force delete)"),
    (re.compile(r"\bnpm\s+publish\b", re.IGNORECASE), "npm publish"),
    (re.compile(r"\byarn\s+publish\b", re.IGNORECASE), "yarn publish"),
    (re.compile(r"\bpnpm\s+publish\b", re.IGNORECASE), "pnpm publish"),
    (re.compile(r"\buv\s+publish\b", re.IGNORECASE), "uv publish"),
    (re.compile(r"\bpip\s+install\b", re.IGNORECASE), "pip install"),
    (re.compile(r"\bcurl\s+[^;]*\|\s*(?:sh|bash|zsh|sudo)", re.IGNORECASE), "curl | shell"),
    (re.compile(r"\bwget\s+[^;]*\|\s*(?:sh|bash|zsh|sudo)", re.IGNORECASE), "wget | shell"),
    (re.compile(r"\bsudo\b", re.IGNORECASE), "sudo"),
    (re.compile(r"\bchmod\s+[^;]*777", re.IGNORECASE), "chmod 777"),
    (re.compile(r"\bdd\s+if=", re.IGNORECASE), "dd command"),
    (re.compile(r"\bmkfs\.", re.IGNORECASE), "mkfs filesystem format"),
    (re.compile(r"\bgh\s+pr\s+merge\b", re.IGNORECASE), "gh pr merge"),
    (re.compile(r"\bdocker\s+(?:rm|rmi)\s+[^;]*-f", re.IGNORECASE), "docker rm/rmi with force"),
    (re.compile(r"\bkubectl\s+delete\b", re.IGNORECASE), "kubectl delete"),
    (re.compile(r"\bterraform\s+(?:apply|destroy)\b", re.IGNORECASE), "terraform apply/destroy"),
    (re.compile(r">\s*/etc/", re.IGNORECASE), "write to /etc/"),
    (re.compile(r">\s*/dev/sda", re.IGNORECASE), "write to disk device"),
]


def classify_tool_call(
    policy: ApprovalPolicy,
    tool_name: str,
    tool_input: dict[str, Any] | None,
) -> ApprovalDecision:
    """Decide whether a tool call needs human approval under the given policy.

    PERMISSIVE — never needs approval.
    CAREFUL    — gate known-destructive Bash commands and file deletes.
    PARANOID   — gate all Bash, Write, Edit, NotebookEdit. (Read-only tools
                 like Read, Glob, Grep, WebFetch are not gated.)

    Args:
        policy: The run's approval policy
        tool_name: SDK tool name ("Bash", "Write", "Edit", etc.)
        tool_input: Tool arguments dict. May be empty or None.

    Returns:
        ApprovalDecision(needs_approval, reason).
    """
    if policy == ApprovalPolicy.PERMISSIVE:
        return ApprovalDecision(False, "")

    tool_input = tool_input or {}

    # Both policies gate Bash when risky; PARANOID gates all Bash.
    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if policy == ApprovalPolicy.PARANOID:
            return ApprovalDecision(True, "PARANOID: all Bash commands require approval")
        # CAREFUL: scan for known-destructive patterns
        for pattern, label in _CAREFUL_BASH_PATTERNS:
            if pattern.search(command):
                return ApprovalDecision(True, f"CAREFUL: matched destructive pattern — {label}")
        return ApprovalDecision(False, "")

    # Write / Edit / NotebookEdit — PARANOID gates all; CAREFUL gates nothing
    # here (these usually aren't destructive, and most workflows need them).
    # Operators wanting tighter gates can use PARANOID.
    if tool_name in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        if policy == ApprovalPolicy.PARANOID:
            return ApprovalDecision(True, f"PARANOID: all writes require approval — {tool_name}")
        return ApprovalDecision(False, "")

    # All other tools (Read, Glob, Grep, WebFetch, Task, TaskCreate, TaskUpdate, MCP tools, etc.)
    # are not gated. Operators who want to gate MCP tools specifically can use
    # PARANOID — it catches Bash. Explicit per-MCP gating is a future enhancement.
    return ApprovalDecision(False, "")


# ---------------------------------------------------------------------------
# PreToolUse hook
# ---------------------------------------------------------------------------


async def _wait_for_approval_decision(
    store: GluonStore,
    approval_id: str,
    timeout_secs: int = APPROVAL_TIMEOUT_SECS,
    poll_interval: int = APPROVAL_POLL_INTERVAL_SECS,
) -> ApprovalStatus:
    """Poll the store until the approval is decided or timeout elapses.

    Returns the final status. On timeout, marks the approval EXPIRED and
    returns EXPIRED (the hook should then deny the tool call).
    """
    deadline = utc_now() + timedelta(seconds=timeout_secs)
    while utc_now() < deadline:
        approval = store.get_approval(approval_id)
        if approval is None:
            # Shouldn't happen — we just created it
            logger.warning("Approval %s vanished during wait", approval_id[:8])
            return ApprovalStatus.EXPIRED
        if approval.status != ApprovalStatus.PENDING:
            return approval.status
        await asyncio.sleep(poll_interval)

    # Timed out — expire it
    store.decide_approval(
        approval_id,
        status=ApprovalStatus.EXPIRED,
        decided_by="system:timeout",
        decision_reason=f"Timed out after {timeout_secs}s without a decision",
    )
    return ApprovalStatus.EXPIRED


def _make_approval_hook(
    store: GluonStore,
    run_id: str,
    policy: ApprovalPolicy,
    notifier: NotificationDispatcher | None = None,
    timeout_secs: int = APPROVAL_TIMEOUT_SECS,
    message_callback: Callable[[dict[str, Any]], None] | None = None,
):
    """Build a PreToolUse hook that gates tool calls under the given policy.

    The hook classifies each tool call, creates a PendingApproval record for
    risky calls, posts a notification, and waits for a decision. It returns:
      - `{}` (allow) for calls that don't need approval or when granted
      - `{"hookSpecificOutput": {..., "permissionDecision": "deny",
         "permissionDecisionReason": "..."}}` when denied/expired

    The caller should add this hook alongside existing PreToolUse hooks.
    """

    async def on_pre_tool_use(
        input_data: PreToolUseHookInput | HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput | AsyncHookJSONOutput:
        raw_tool_name = input_data.get("tool_name", "unknown")
        tool_name = raw_tool_name if isinstance(raw_tool_name, str) else str(raw_tool_name)
        tool_input_raw = input_data.get("tool_input", {}) or {}
        tool_input: dict[str, Any] = (
            tool_input_raw if isinstance(tool_input_raw, dict) else {"_raw": str(tool_input_raw)}
        )

        decision = classify_tool_call(policy, tool_name, tool_input)
        if not decision.needs_approval:
            return {}

        # Resolve tool_use_id: prefer the arg passed in, fall back to input_data
        resolved_tool_use_id: str | None
        if tool_use_id is not None:
            resolved_tool_use_id = tool_use_id
        else:
            candidate = input_data.get("tool_use_id")
            resolved_tool_use_id = candidate if isinstance(candidate, str) else None

        # Create a pending approval
        try:
            approval = store.create_approval(
                run_id=run_id,
                tool_name=tool_name,
                classification_reason=decision.reason,
                tool_input=tool_input,
                tool_use_id=resolved_tool_use_id,
                timeout_at=utc_now() + timedelta(seconds=timeout_secs),
            )
        except Exception:
            logger.exception("Failed to create approval record; allowing tool call")
            return {}

        logger.info(
            "approval_requested",
            extra={
                "run_id": run_id[:8],
                "approval_id": approval.id[:8],
                "tool": tool_name,
                "reason": decision.reason,
            },
        )

        # Emit a message into messages.jsonl so the dashboard sees it live
        if message_callback is not None:
            try:
                message_callback(
                    {
                        "timestamp": utc_now().isoformat(),
                        "type": "approval_requested",
                        "content": f"{tool_name}: {decision.reason}",
                        "metadata": {
                            "approval_id": approval.id,
                            "tool_name": tool_name,
                            "reason": decision.reason,
                        },
                    }
                )
            except Exception:
                logger.debug("Approval message callback failed", exc_info=True)

        # Rich transport notifications (Telegram/Discord interactive buttons) are
        # a follow-up enhancement. For now, the approval_requested event is
        # visible in messages.jsonl (via message_callback above) and queryable
        # via `gluon approvals list` / the web API.
        # The `notifier` parameter is retained for future use.

        # Wait for the decision
        final_status = await _wait_for_approval_decision(store, approval.id, timeout_secs=timeout_secs)

        if final_status == ApprovalStatus.GRANTED:
            logger.info(
                "approval_granted",
                extra={"run_id": run_id[:8], "approval_id": approval.id[:8]},
            )
            return {}

        # DENIED or EXPIRED
        refreshed = store.get_approval(approval.id)
        reason = (
            refreshed.decision_reason if refreshed and refreshed.decision_reason else f"Approval {final_status.value}"
        )
        logger.info(
            "approval_denied",
            extra={
                "run_id": run_id[:8],
                "approval_id": approval.id[:8],
                "status": final_status.value,
                "reason": reason,
            },
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"[approval-gate] {reason}",
            }
        }

    return on_pre_tool_use


__all__ = [
    "APPROVAL_POLL_INTERVAL_SECS",
    "APPROVAL_TIMEOUT_SECS",
    "ApprovalDecision",
    "classify_tool_call",
    "_make_approval_hook",
    "_wait_for_approval_decision",
]
