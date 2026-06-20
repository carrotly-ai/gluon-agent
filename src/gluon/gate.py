"""Objective gate for loop-engineering (I1).

A loop's "done" should be decided by a deterministic check (tests/lint/build exit
0), not the agent's self-report. ``run_gate`` runs a shell command in a directory
and reports whether it passed (exit 0). It is the reusable primitive the ralph
loop uses to override premature self-declared completion for *gated* runs.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Default per-gate timeout. A gate command (e.g. the test suite) must finish within
# this or it is treated as not-passed. Override with GLUON_GATE_TIMEOUT_SECS.
DEFAULT_GATE_TIMEOUT_SECS = int(os.environ.get("GLUON_GATE_TIMEOUT_SECS", "600"))

# Keep only the tail of the gate output for logs/feedback so a noisy command
# (thousands of lines) doesn't blow up the prompt or the DB.
_MAX_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class GateResult:
    """Outcome of running an objective gate command."""

    passed: bool
    exit_code: int
    output: str


def _tail(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return "…(truncated)…\n" + text[-limit:]


def run_gate(
    cmd: str,
    cwd: str | Path,
    timeout: int = DEFAULT_GATE_TIMEOUT_SECS,
) -> GateResult:
    """Run ``cmd`` (a shell command) in ``cwd`` and report pass/fail.

    Passed iff the command exits 0 within ``timeout`` seconds. A timeout or a
    spawn failure is reported as ``passed=False`` (never raises) — a gate that
    can't run must not be mistaken for a gate that passed.
    """
    try:
        proc = subprocess.run(
            cmd,
            shell=True,  # noqa: S602 — operator-supplied verify command, run intentionally
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return GateResult(passed=False, exit_code=124, output=f"gate timed out after {timeout}s: {cmd}")
    except OSError as e:
        return GateResult(passed=False, exit_code=127, output=f"gate failed to start: {e}")

    combined = (proc.stdout or "") + (proc.stderr or "")
    return GateResult(passed=proc.returncode == 0, exit_code=proc.returncode, output=_tail(combined))
