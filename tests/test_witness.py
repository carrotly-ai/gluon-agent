"""Tests for the witness pattern (LLM-based health classification)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.models import (
    ExecutionRun,
    HealthClassification,
    RecoveryAction,
    RunStatus,
    WitnessDecision,
    utc_now,
)
from gluon.store import GluonStore
from gluon.witness import WitnessClassifier


def _make_store(tmp_path):
    return GluonStore(db_path=tmp_path / "test.db")


def _make_run() -> ExecutionRun:
    run = ExecutionRun(
        project_id="proj1",
        prompt="test task",
        status=RunStatus.RUNNING,
    )
    run.started_at = utc_now()
    return run


@pytest.mark.anyio
async def test_classify_with_mock_haiku(tmp_path):
    store = _make_store(tmp_path)
    witness = WitnessClassifier(store)

    run = _make_run()
    log_path = tmp_path / "logs"
    log_path.mkdir()
    run_log = log_path / run.id
    run_log.mkdir()
    (run_log / "messages.jsonl").write_text(json.dumps({"type": "text", "content": "Working on task..."}) + "\n")

    mock_response = {"classification": "STUCK", "confidence": 0.9, "reasoning": "Repeating same action"}
    with patch.object(witness, "_invoke_haiku", new_callable=AsyncMock, return_value=mock_response):
        decision = await witness.classify(run, log_path)

    assert decision.classification == HealthClassification.STUCK
    assert decision.confidence == 0.9
    assert decision.action == RecoveryAction.RESTART


def test_suggest_action_zombie():
    store = MagicMock()
    witness = WitnessClassifier(store)
    assert witness.suggest_action(HealthClassification.ZOMBIE, 0.9) == RecoveryAction.RESTART


def test_suggest_action_stuck():
    store = MagicMock()
    witness = WitnessClassifier(store)
    assert witness.suggest_action(HealthClassification.STUCK, 0.8) == RecoveryAction.RESTART


def test_suggest_action_looping():
    store = MagicMock()
    witness = WitnessClassifier(store)
    assert witness.suggest_action(HealthClassification.LOOPING, 0.7) == RecoveryAction.NUDGE


def test_suggest_action_slow():
    store = MagicMock()
    witness = WitnessClassifier(store)
    assert witness.suggest_action(HealthClassification.SLOW, 0.9) == RecoveryAction.NONE


def test_suggest_action_healthy():
    store = MagicMock()
    witness = WitnessClassifier(store)
    assert witness.suggest_action(HealthClassification.HEALTHY, 1.0) == RecoveryAction.NONE


def test_suggest_action_low_confidence():
    store = MagicMock()
    witness = WitnessClassifier(store)
    # Low confidence should always return NONE regardless of classification
    assert witness.suggest_action(HealthClassification.ZOMBIE, 0.3) == RecoveryAction.NONE


@pytest.mark.anyio
async def test_read_recent_output(tmp_path):
    store = _make_store(tmp_path)
    witness = WitnessClassifier(store)

    run = _make_run()
    log_path = tmp_path / "logs"
    run_log = log_path / run.id
    run_log.mkdir(parents=True)

    lines = [json.dumps({"type": "text", "content": f"Line {i}"}) for i in range(50)]
    (run_log / "messages.jsonl").write_text("\n".join(lines))

    result = await witness._read_recent_output(run, log_path, max_lines=10)
    # Should only contain last 10 lines
    assert "Line 49" in result
    assert "Line 0" not in result


@pytest.mark.anyio
async def test_cost_guard_skips_recent_decision(tmp_path):
    store = _make_store(tmp_path)
    witness = WitnessClassifier(store)

    run = _make_run()

    # Record a recent decision
    recent = WitnessDecision(
        run_id=run.id,
        classification=HealthClassification.HEALTHY,
        confidence=0.9,
    )
    store.record_witness_decision(recent)

    log_path = tmp_path / "logs"
    log_path.mkdir()

    # Should return cached decision without calling LLM
    with patch.object(witness, "_invoke_haiku", new_callable=AsyncMock) as mock_haiku:
        decision = await witness.classify(run, log_path)
        mock_haiku.assert_not_called()

    assert decision.id == recent.id


@pytest.mark.anyio
async def test_execute_action_restart(tmp_path):
    store = _make_store(tmp_path)
    witness = WitnessClassifier(store)

    run = _make_run()

    runner = MagicMock()
    runner.cancel_run = MagicMock()
    runner.submit = AsyncMock(return_value=_make_run())

    await witness.execute_action(run, RecoveryAction.RESTART, runner, None)
    runner.cancel_run.assert_called_once_with(run.id)
    runner.submit.assert_called_once()


@pytest.mark.anyio
async def test_execute_action_escalate(tmp_path):
    store = _make_store(tmp_path)
    witness = WitnessClassifier(store)

    run = _make_run()

    notifier = MagicMock()
    notifier.notify = AsyncMock()

    await witness.execute_action(run, RecoveryAction.ESCALATE, MagicMock(), notifier)
    notifier.notify.assert_called_once()
