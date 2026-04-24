"""Witness Pattern: LLM-based health classification for smarter recovery.

Uses a lightweight LLM (Haiku) to classify the health of running agent
processes and suggest recovery actions.
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gluon.models import (
    ExecutionRun,
    HealthClassification,
    QueuedMessage,
    RecoveryAction,
    WitnessDecision,
    utc_now,
)

if TYPE_CHECKING:
    from gluon.notifier import NotificationDispatcher
    from gluon.runner import TaskRunner
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Minimum interval between NUDGE actions for the same run.
# Avoids flooding a stuck run with repeated course-correction messages.
NUDGE_COOLDOWN_SECS = 900  # 15 minutes

# Injected when the witness classifies a run as LOOPING. Asks the agent to
# stop, reassess, and either pivot or pause for review.
LOOPING_NUDGE_PROMPT = """[SYSTEM NUDGE from the Gluon witness]

Gluon has detected that you may be stuck in a retry/error loop. Before taking \
any further action, stop and answer these questions:

1. What specific error or obstacle keeps recurring?
2. Is there a fundamentally different approach that would sidestep it entirely?
3. Have you already tried variations of the current approach that failed the same way?

If you can answer (1) and (2) clearly, proceed with the new approach.
If you cannot, write your analysis to `NUDGE_ANALYSIS.md` and stop for human review.

Do not retry the current failing approach."""

WITNESS_PROMPT = """Classify the health of this Claude Code agent run.

Recent output (last 30 lines):
{context}

Run info: started {elapsed} ago, status={status}

Classify as exactly one of:
- HEALTHY: Making normal progress
- SLOW: Working but slower than expected
- STUCK: No meaningful progress, repeating actions
- LOOPING: Caught in a retry/error loop
- NEEDS_CONTEXT_RESET: Context window likely exhausted
- ZOMBIE: Process dead or unresponsive

Respond as JSON: {{"classification": "...", "confidence": 0.0-1.0, "reasoning": "..."}}"""

# Cost guard: min interval between witness calls per run
WITNESS_MIN_INTERVAL_SECS = 300  # 5 minutes


class WitnessClassifier:
    """LLM-based health classifier for running agent processes."""

    def __init__(self, store: "GluonStore"):
        self.store = store

    async def classify(self, run: ExecutionRun, log_path: Path) -> WitnessDecision:
        """Read recent output, classify via Haiku, return decision."""
        # Cost guard: skip if recent decision exists
        latest = self.store.get_latest_witness_decision(run.id)
        if latest:
            age = (utc_now() - latest.timestamp).total_seconds()
            if age < WITNESS_MIN_INTERVAL_SECS:
                logger.debug("Skipping witness for %s: recent decision %ds ago", run.id[:8], age)
                return latest

        context = await self._read_recent_output(run, log_path)
        elapsed = "unknown"
        if run.started_at:
            elapsed_secs = (utc_now() - run.started_at).total_seconds()
            elapsed = f"{elapsed_secs / 60:.0f} minutes"

        prompt = WITNESS_PROMPT.format(
            context=context,
            elapsed=elapsed,
            status=run.status.value,
        )

        try:
            result = await self._invoke_haiku(prompt)
            classification = HealthClassification(result.get("classification", "healthy").lower())
            confidence = float(result.get("confidence", 0.5))
            reasoning = result.get("reasoning", "")
        except Exception:
            logger.debug("Witness LLM call failed, defaulting to HEALTHY", exc_info=True)
            classification = HealthClassification.HEALTHY
            confidence = 0.0
            reasoning = "LLM call failed, defaulting to healthy"

        action = self.suggest_action(classification, confidence)

        decision = WitnessDecision(
            run_id=run.id,
            classification=classification,
            confidence=confidence,
            reasoning=reasoning,
            action=action,
        )
        self.store.record_witness_decision(decision)
        return decision

    async def _read_recent_output(self, run: ExecutionRun, log_path: Path, max_lines: int = 30) -> str:
        """Read last N lines from messages.jsonl."""
        messages_file = log_path / run.id / "messages.jsonl"
        if not messages_file.exists():
            return "(no output available)"

        try:
            lines = messages_file.read_text().strip().splitlines()
            recent = lines[-max_lines:]
            # Extract message content from JSONL
            output_parts: list[str] = []
            for line in recent:
                try:
                    msg = json.loads(line)
                    content = msg.get("content", msg.get("message", str(msg.get("type", ""))))
                    if isinstance(content, str):
                        output_parts.append(content[:200])
                except json.JSONDecodeError:
                    output_parts.append(line[:200])
            return "\n".join(output_parts[-max_lines:])
        except Exception:
            return "(failed to read output)"

    async def _invoke_haiku(self, prompt: str) -> dict:
        """Call Haiku for classification. Returns parsed JSON dict."""
        import os

        try:
            import anthropic

            client = anthropic.AsyncAnthropicBedrock(
                aws_region=os.getenv("AWS_REGION", "us-east-1"),
            )
            response = await client.messages.create(
                model="anthropic.claude-haiku-4-5-20251001-v1:0",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text  # type: ignore[union-attr]
            result: dict[str, Any] = json.loads(text)
            return result
        except ImportError:
            raise RuntimeError("anthropic package not installed")

    def suggest_action(self, classification: HealthClassification, confidence: float) -> RecoveryAction:
        """Map classification to recovery action."""
        if confidence < 0.5:
            return RecoveryAction.NONE

        mapping = {
            HealthClassification.HEALTHY: RecoveryAction.NONE,
            HealthClassification.SLOW: RecoveryAction.NONE,
            HealthClassification.STUCK: RecoveryAction.RESTART,
            HealthClassification.LOOPING: RecoveryAction.NUDGE,
            HealthClassification.NEEDS_CONTEXT_RESET: RecoveryAction.RESTART,
            HealthClassification.ZOMBIE: RecoveryAction.RESTART,
        }
        return mapping.get(classification, RecoveryAction.NONE)

    async def execute_action(
        self,
        run: ExecutionRun,
        action: RecoveryAction,
        runner: "TaskRunner",
        notifier: "NotificationDispatcher | None",
    ) -> None:
        """Execute the suggested recovery action."""
        if action == RecoveryAction.NONE:
            return

        if action == RecoveryAction.ESCALATE:
            if notifier:
                try:
                    from gluon.models import RunStatus

                    await notifier.notify(run, run.status, RunStatus.FAILED)
                except Exception:
                    logger.debug("Escalation notification failed", exc_info=True)

        elif action == RecoveryAction.RESTART:
            try:
                # Cancel the stuck run
                await runner.cancel(run.id)
                # Resubmit with same parameters
                await runner.submit(
                    project_id=run.project_id,
                    prompt=run.prompt or "",
                    model=run.model,
                    initiator=f"witness:restart:{run.id[:8]}",
                    profile=run.metadata.get("profile", "standard") if run.metadata else "standard",
                )
                logger.info("Witness restarted run %s", run.id[:8])
            except Exception:
                logger.debug("Witness restart failed", exc_info=True)

        elif action == RecoveryAction.NUDGE:
            await self._send_nudge(run, runner)

    async def _send_nudge(self, run: ExecutionRun, runner: "TaskRunner") -> None:
        """Inject a course-correction message into a live run's follow-up queue.

        Respects NUDGE_COOLDOWN_SECS to avoid repeatedly nudging the same run.
        Uses the same queued-message mechanism as user-submitted follow-ups,
        so the message gets picked up by the run's multi-turn execute loop.
        """
        if self._recent_nudge_exists(run.id):
            logger.info("Witness NUDGE for run %s suppressed by cooldown", run.id[:8])
            self._record_nudge_outcome(run.id, sent=False, reason="cooldown")
            return

        try:
            refreshed = self.store.get_run(run.id)
            if refreshed is None:
                logger.debug("Witness NUDGE: run %s not found", run.id[:8])
                return

            refreshed.queued_messages.append(QueuedMessage(message=LOOPING_NUDGE_PROMPT))
            self.store.update_run(refreshed)

            active_queue = getattr(runner, "_active_queues", {}).get(run.id)
            if active_queue is not None:
                try:
                    active_queue.put_nowait(LOOPING_NUDGE_PROMPT)
                except Exception:
                    logger.debug("Witness NUDGE: direct queue put failed; DB poller will retry", exc_info=True)

            logger.info("Witness sent NUDGE to run %s", run.id[:8])
            self._record_nudge_outcome(run.id, sent=True, reason="looping_detected")
        except Exception:
            logger.debug("Witness NUDGE failed for run %s", run.id[:8], exc_info=True)
            self._record_nudge_outcome(run.id, sent=False, reason="error")

    def _recent_nudge_exists(self, run_id: str) -> bool:
        """True if a NUDGE action was recorded for this run within the cooldown window."""
        decisions = self.store.list_witness_decisions(run_id, limit=10)
        now = utc_now()
        for d in decisions:
            if d.action != RecoveryAction.NUDGE:
                continue
            if d.action_result != "nudge_sent":
                continue
            age = (now - d.timestamp).total_seconds()
            if age < NUDGE_COOLDOWN_SECS:
                return True
        return False

    def _record_nudge_outcome(self, run_id: str, *, sent: bool, reason: str) -> None:
        """Append a witness decision recording the nudge outcome."""
        decision = WitnessDecision(
            run_id=run_id,
            classification=HealthClassification.LOOPING,
            confidence=1.0,
            reasoning=f"NUDGE outcome: {reason}",
            action=RecoveryAction.NUDGE,
            action_result="nudge_sent" if sent else f"nudge_skipped:{reason}",
        )
        try:
            self.store.record_witness_decision(decision)
        except Exception:
            logger.debug("Failed to record nudge outcome", exc_info=True)
