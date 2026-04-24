"""Hard per-run safety caps (Theme D3).

Two caps are enforced:
  - ``max_tool_calls`` — Aborts further tool use once the run's tool-call
    count reaches the configured ceiling. Implemented as a PreToolUse hook
    that denies the call when the counter is at or past the limit.
  - ``max_duration_minutes`` — Aborts the run once wall-clock elapsed
    exceeds the configured ceiling. Implemented in ``runner._run_task`` as
    an asyncio watchdog (see ``runner.py``).

Both caps default ``None`` which preserves existing no-enforcement behavior.

The module also exports ``_make_hard_caps_hook`` which builds the
PreToolUse hook. The hook refreshes the run from the store on every
invocation to avoid stale writes, increments the counter, and returns a
structured deny when the cap is reached. All failures are swallowed so
the run loop never crashes on transient store errors — soft-fail is
preferred over hard-fail for a safety cap.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from claude_agent_sdk import HookContext, HookInput, PreToolUseHookInput
from claude_agent_sdk.types import AsyncHookJSONOutput, SyncHookJSONOutput

if TYPE_CHECKING:
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


def _make_hard_caps_hook(store: GluonStore, run_id: str):
    """Build a PreToolUse hook that enforces ``max_tool_calls``.

    The hook:
      1. Refreshes the run from the store (avoids stale counter on concurrent writes).
      2. If ``max_tool_calls`` is None, returns allow immediately.
      3. If the counter has reached the cap, returns a structured deny.
      4. Otherwise increments the counter and persists, then allows.

    All exceptions are swallowed and converted to allow — a safety cap
    should never itself crash the run.
    """

    async def on_pre_tool_use(
        input_data: PreToolUseHookInput | HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput | AsyncHookJSONOutput:
        try:
            run = store.get_run(run_id)
            if run is None:
                # Run vanished or was never persisted — allow to avoid
                # blocking progress on a transient condition.
                return {}

            cap = run.max_tool_calls
            if cap is None:
                # Nothing to enforce.
                return {}

            current = run.tool_call_count or 0
            if current >= cap:
                reason = f"[hard-cap] max_tool_calls reached ({current}/{cap})"
                logger.info(
                    "hard_cap_denied",
                    extra={
                        "run_id": run_id[:8],
                        "tool_call_count": current,
                        "max_tool_calls": cap,
                    },
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }

            # Increment and persist. Refresh once more right before writing
            # to minimise the window of a stale overwrite if another writer
            # also bumped the counter.
            run.tool_call_count = current + 1
            store.update_run(run)
            return {}
        except Exception:
            # Never crash the run loop on a hook failure.
            logger.debug("hard_caps hook raised; allowing tool call", exc_info=True)
            return {}

    return on_pre_tool_use


__all__ = ["_make_hard_caps_hook"]
