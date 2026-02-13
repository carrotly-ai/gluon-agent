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
        """Send notification to all channels mapped to this run's project.

        Skips non-interesting transitions (e.g., PENDING -> RUNNING).
        """
        if new_status not in _NOTIFY_STATUSES:
            return

        if old_status == new_status:
            return

        if not self.transports:
            return

        mappings = self.store.list_channel_mappings_for_project(run.project_id)
        if not mappings:
            return

        project = self.store.get_project(run.project_id)
        project_name = project.name if project else run.project_id[:8]

        text = self._format(run, project_name, new_status)

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

        # Cost/turns info
        parts: list[str] = []
        if run.cost_usd is not None:
            parts.append(f"${run.cost_usd:.2f}")
        if run.loop_count:
            parts.append(f"{run.loop_count} loops")
        if parts:
            lines.append(" · ".join(parts))

        # Error info for failures
        if new_status == RunStatus.FAILED and run.error_message:
            error_preview = run.error_message[:120]
            if len(run.error_message) > 120:
                error_preview += "..."
            lines.append(f"Error: {error_preview}")

        return "\n".join(lines)
