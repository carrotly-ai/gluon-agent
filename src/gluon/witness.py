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
    RecoveryAction,
    WitnessDecision,
    utc_now,
)

if TYPE_CHECKING:
    from gluon.notifier import NotificationDispatcher
    from gluon.runner import TaskRunner
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

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
        try:
            from gluon.llm_provider import get_provider
            from gluon.models_config import ModelTier

            provider = get_provider()
            client = provider.create_api_client()
            model_id = provider.get_model_id(ModelTier.HAIKU)

            response = await client.messages.create(
                model=model_id,
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
            logger.info("Witness suggests nudge for run %s (not yet implemented)", run.id[:8])
