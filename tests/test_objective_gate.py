"""Tests for Step 2 (I1 objective gate): run_gate helper + the demotion seam in
RalphManager (_apply_objective_gate). For *gated* runs the objective gate is the
authority; *gateless* runs are unchanged (non-regression).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from gluon.gate import GateResult, run_gate
from gluon.models import ExecutionRun, RunStatus
from gluon.ralph_manager import RalphManager


def _manager(tmp: Path, *, verify_cmd: str | None = None) -> RalphManager:
    run = ExecutionRun(
        id="r1",
        project_id="p",
        prompt="do it",
        status=RunStatus.RUNNING,
        ralph_enabled=True,
        max_loops=10,
        verify_cmd=verify_cmd,
    )
    return RalphManager(run=run, agent=MagicMock(), store=MagicMock(), working_dir=tmp, log_dir=tmp)


# --- run_gate helper ---


def test_run_gate_pass(tmp_path: Path) -> None:
    res = run_gate("exit 0", cwd=tmp_path)
    assert res.passed is True
    assert res.exit_code == 0


def test_run_gate_fail(tmp_path: Path) -> None:
    res = run_gate("exit 3", cwd=tmp_path)
    assert res.passed is False
    assert res.exit_code == 3


def test_run_gate_captures_output(tmp_path: Path) -> None:
    res = run_gate("echo hello && exit 1", cwd=tmp_path)
    assert res.passed is False
    assert "hello" in res.output


def test_run_gate_timeout(tmp_path: Path) -> None:
    res = run_gate("sleep 5", cwd=tmp_path, timeout=1)
    assert res.passed is False
    assert "timed out" in res.output


# --- _apply_objective_gate (the demotion seam) ---


def test_gateless_returns_unchanged_and_skips_gate(tmp_path: Path) -> None:
    """Non-regression: a gateless run never runs the gate and is unchanged."""
    mgr = _manager(tmp_path, verify_cmd=None)
    with patch("gluon.ralph_manager.run_gate") as gate:
        out = mgr._apply_objective_gate(True, "self-report done")
    assert out == (True, "self-report done")
    gate.assert_not_called()


def test_gated_exit_blocked_when_gate_fails(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, verify_cmd="pytest")
    with patch("gluon.ralph_manager.run_gate", return_value=GateResult(False, 1, "FAILED test_x")):
        should_exit, _reason = mgr._apply_objective_gate(True, "self-report done")
    assert should_exit is False  # gate is authority — don't exit on self-report
    assert mgr._last_gate_failure == "FAILED test_x"


def test_gated_exit_allowed_when_gate_passes(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, verify_cmd="pytest")
    mgr._last_gate_failure = "stale failure"
    with patch("gluon.ralph_manager.run_gate", return_value=GateResult(True, 0, "ok")):
        should_exit, reason = mgr._apply_objective_gate(True, "self-report done")
    assert should_exit is True
    assert "verify_cmd passed" in reason
    assert mgr._last_gate_failure is None


def test_gate_skipped_until_self_report_says_done(tmp_path: Path) -> None:
    """The gate only runs when self-report would exit — not every iteration."""
    mgr = _manager(tmp_path, verify_cmd="pytest")
    with patch("gluon.ralph_manager.run_gate") as gate:
        out = mgr._apply_objective_gate(False, "")
    assert out == (False, "")
    gate.assert_not_called()


# --- feedback into the next iteration's prompt ---


def test_gate_failure_injected_into_loop_prompt(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, verify_cmd="pytest")
    mgr._last_gate_failure = "AssertionError: boom"
    prompt = mgr._build_loop_prompt(2)
    assert "OBJECTIVE GATE FAILED" in prompt
    assert "AssertionError: boom" in prompt
