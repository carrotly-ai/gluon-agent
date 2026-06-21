"""Characterization tests for the shared finalize-path helper (#161).

`_run_task` and `_run_ralph_loop` both build a safety-net auto-commit message
for worktree runs. That construction was duplicated and differed only by the
label; it is now the pure `_auto_commit_message` helper. These tests PIN the
exact bytes both call sites previously produced, so the extraction is provably
behavior-identical.

NOTE: the rest of the two finalization tails diverge on several axes (output
writer, git-info capture, gate/draft handling, the `item.success` guard,
inline `store.update_run`, and the structural position relative to blueprint
re-validation) and are NOT folded into a shared helper — see issue #161. They
also aren't reachable by a unit test in their current inline form (embedded in
the ~900-line run methods with no seam), which is exactly why the broader merge
stays a deliberate, human-led design call.
"""

from __future__ import annotations

from gluon.runner import _auto_commit_message


def test_default_label_matches_run_task_format():
    """Byte-identical to the inline message _run_task used to build."""
    prompt, run_id = "fix the flaky test", "run-abc123"
    expected = f"chore: {prompt}\n\nAuto-committed by Gluon Agent\nRun ID: {run_id}"
    assert _auto_commit_message(prompt, run_id) == expected


def test_ralph_label_matches_run_ralph_loop_format():
    """Byte-identical to the inline message _run_ralph_loop used to build."""
    prompt, run_id = "iterate until green", "run-def456"
    expected = f"chore: {prompt}\n\nAuto-committed by Gluon Agent (Ralph Loop)\nRun ID: {run_id}"
    assert _auto_commit_message(prompt, run_id, "Gluon Agent (Ralph Loop)") == expected


def test_no_ellipsis_at_or_below_60_chars():
    prompt = "x" * 60  # exactly 60 — no ellipsis
    msg = _auto_commit_message(prompt, "run-1")
    assert msg == f"chore: {'x' * 60}\n\nAuto-committed by Gluon Agent\nRun ID: run-1"
    assert "..." not in msg.split("\n", 1)[0]


def test_truncates_and_ellipsizes_past_60_chars():
    prompt = "y" * 100
    msg = _auto_commit_message(prompt, "run-2")
    # First line is "chore: " + first 60 chars + "..."
    assert msg.startswith(f"chore: {'y' * 60}...\n\n")
    assert msg.endswith("Auto-committed by Gluon Agent\nRun ID: run-2")


def test_run_id_and_prompt_embedded():
    msg = _auto_commit_message("do thing", "RUN-XYZ")
    assert "do thing" in msg
    assert "Run ID: RUN-XYZ" in msg
