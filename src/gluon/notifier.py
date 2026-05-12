"""Run status change notification dispatcher.

Sends notifications to mapped Telegram/Discord channels when runs
transition to terminal or actionable states (REVIEW, FAILED, CANCELLED, COMPLETED).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gluon.models import ExecutionRun, RunStatus
from gluon.transport import TransportContext, TransportResponse

if TYPE_CHECKING:
    from gluon.store import GluonStore
    from gluon.transport.base import Transport

logger = logging.getLogger(__name__)

# Only notify on these terminal/actionable states
_NOTIFY_STATUSES = frozenset(
    {
        RunStatus.REVIEW,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.COMPLETED,
    }
)

_STATUS_LABELS: dict[RunStatus, tuple[str, str]] = {
    RunStatus.REVIEW: ("🔍", "Ready for Review"),
    RunStatus.COMPLETED: ("✅", "Completed"),
    RunStatus.FAILED: ("❌", "Failed"),
    RunStatus.CANCELLED: ("🚫", "Cancelled"),
}


class NotificationDispatcher:
    """Dispatches run status notifications to mapped transport channels."""

    def __init__(
        self,
        store: GluonStore,
        transports: dict[str, Transport] | None = None,
    ):
        self.store = store
        self.transports: dict[str, Transport] = transports or {}

    async def notify(
        self,
        run: ExecutionRun,
        old_status: RunStatus,
        new_status: RunStatus,
    ) -> None:
        """Send notification to channels mapped to this run.

        Routing:
          1. If the run was originated from a transport channel (per the
             message_run_map), post into that channel as a reply to the
             status message — keeps the whole task threaded together.
          2. Then post into every project-wide ChannelMapping that isn't
             the same (transport, channel) we already posted to. This lets
             multiple subscriber channels still get notified.

        Skips non-interesting transitions (e.g., PENDING -> RUNNING).
        """
        if new_status not in _NOTIFY_STATUSES:
            return

        if old_status == new_status:
            return

        if not self.transports:
            return

        project = self.store.get_project(run.project_id)
        project_name = project.name if project else run.project_id[:8]
        text = self._format(run, project_name, new_status)

        delivered: set[tuple[str, str]] = set()

        # 1. Origin channel — reply to the status message for context
        origin = self.store.find_message_run_map_by_run(run.id)
        if origin is not None:
            transport = self.transports.get(origin.transport)
            if transport is not None:
                try:
                    ctx = TransportContext(
                        transport=origin.transport,
                        user_id="gluon:system",
                        chat_id=origin.chat_id,
                    )
                    await transport.send(
                        ctx,
                        TransportResponse(
                            text=text,
                            parse_mode="markdown",
                            reply_to_id=origin.message_id,
                        ),
                    )
                    delivered.add((origin.transport, origin.chat_id))
                except Exception:
                    logger.debug(
                        "Failed to send origin notification to %s:%s",
                        origin.transport,
                        origin.chat_id,
                        exc_info=True,
                    )

        # 2. Project-wide subscribers
        mappings = self.store.list_channel_mappings_for_project(run.project_id)
        for mapping in mappings:
            if (mapping.transport, mapping.channel_id) in delivered:
                continue
            transport = self.transports.get(mapping.transport)
            if not transport:
                continue
            try:
                ctx = TransportContext(
                    transport=mapping.transport,
                    user_id="gluon:system",
                    chat_id=mapping.channel_id,
                )
                await transport.send(ctx, TransportResponse(text=text, parse_mode="markdown"))
                delivered.add((mapping.transport, mapping.channel_id))
            except Exception:
                logger.debug(
                    "Failed to send notification to %s:%s",
                    mapping.transport,
                    mapping.channel_id,
                    exc_info=True,
                )

    def _format(
        self,
        run: ExecutionRun,
        project_name: str,
        new_status: RunStatus,
    ) -> str:
        emoji, label = _STATUS_LABELS.get(new_status, ("❓", new_status.value))

        prompt_preview = run.prompt[:80]
        if len(run.prompt) > 80:
            prompt_preview += "..."

        lines = [
            f"{emoji} **{project_name}** — {label} (`{run.id[:8]}`)",
            f"> {prompt_preview}",
        ]

        # Cost/turns/duration/model info
        parts: list[str] = []
        if run.cost_usd is not None:
            parts.append(f"${run.cost_usd:.2f}")
        if run.duration_seconds:
            parts.append(self._format_duration(run.duration_seconds))
        if run.model_used:
            # Show short model name (last segment, truncated)
            parts.append(run.model_used.split(".")[-1][:20])
        if run.loop_count:
            parts.append(f"{run.loop_count} loops")
        if parts:
            lines.append(" · ".join(parts))

        # Chain context if applicable
        if run.metadata and run.metadata.get("chain_id"):
            step_name = run.metadata.get("step_name", "")
            if step_name:
                lines.append(f"Chain step: {step_name}")

        # Error info for failures
        if new_status == RunStatus.FAILED and run.error_message:
            error_preview = run.error_message[:120]
            if len(run.error_message) > 120:
                error_preview += "..."
            lines.append(f"Error: {error_preview}")

        return "\n".join(lines)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"

    async def notify_chain_completed(
        self,
        chain_id: str,
        chain_name: str,
        project_id: str,
        total_steps: int,
        completed_steps: int,
    ) -> None:
        """Send notification when an entire task chain completes."""
        if not self.transports:
            return

        mappings = self.store.list_channel_mappings_for_project(project_id)
        if not mappings:
            return

        project = self.store.get_project(project_id)
        project_name = project.name if project else project_id[:8]

        text = (
            f"🔗 **{project_name}** — Chain '{chain_name}' completed\n  {completed_steps}/{total_steps} steps completed"
        )

        for mapping in mappings:
            transport = self.transports.get(mapping.transport)
            if not transport:
                continue
            try:
                ctx = TransportContext(
                    transport=mapping.transport,
                    user_id="gluon:system",
                    chat_id=mapping.channel_id,
                )
                await transport.send(ctx, TransportResponse(text=text, parse_mode="markdown"))
            except Exception:
                logger.debug(
                    "Failed to send chain notification to %s:%s",
                    mapping.transport,
                    mapping.channel_id,
                    exc_info=True,
                )
