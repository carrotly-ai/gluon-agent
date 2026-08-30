"""Worktree merge-back for loop tasks (loop-first pivot Phase B).

Parallel loop tasks execute in isolated worktrees, each on a branch
(``gluon-task/<run_id>``) cut from the project's checked-out branch. Without
integration, one task's output is invisible to every sibling and to any later
verification task — live-observed in Phase A validation: three parallel tasks
each wrote a file into its own worktree, and the verify task (a fresh worktree)
found none of them.

This module merges a completed loop-task's branch back into the project's
source branch, so later tasks (whose worktrees branch from the updated HEAD)
build on integrated state:

- Residual uncommitted work in the task's worktree is auto-committed first
  (agents usually commit; this is the safety net).
- The merge runs in the MAIN checkout under a per-project file lock
  (``fcntl.flock``) — sibling workers are separate OS processes, so only a
  cross-process lock prevents concurrent merges from corrupting the index.
- Fail-safe posture: a conflict aborts the merge cleanly and reports
  ``conflict`` (the loop spawns an agent task to resolve it); a moved HEAD
  (user switched branches mid-loop) reports ``branch_moved`` rather than
  merging into the wrong branch; errors never raise into the advancement seam.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from gluon.models import ExecutionRun

logger = logging.getLogger(__name__)

_LOCK_DIR = Path.home() / ".gluon" / "locks"

# Outcomes that mean "the branch's work is now (or already was) in the source
# branch" — safe for later tasks to build on.
INTEGRATED_STATUSES = frozenset({"merged", "up_to_date", "no_changes"})


@dataclass(frozen=True)
class IntegrationResult:
    status: str  # merged | up_to_date | no_changes | conflict | branch_moved | skipped | error
    detail: str = ""


async def _git(cwd: Path | str, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip(), err.decode(errors="replace").strip()


def _project_lock_key(project_path: Path) -> str:
    """Stable, cross-process filename token for a project's integration lock.

    MUST be deterministic across separate OS processes: loop iterations run as
    detached ``python -m gluon.runner`` subprocesses, so two siblings need to
    compute the SAME lock filename to serialize their merges. Python's builtin
    ``hash(str)`` is per-process randomized (PYTHONHASHSEED) and would give each
    subprocess a different filename — a lock that excludes nothing (audit
    finding #1). SHA-256 is stable across processes.
    """
    return hashlib.sha256(str(project_path).encode()).hexdigest()[:16]


def _acquire_project_lock(project_path: Path):
    """Blocking flock scoped to the project — released by closing the handle."""
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = _LOCK_DIR / f"integrate-{_project_lock_key(project_path)}.lock"
    handle = open(lock_file, "w")  # noqa: SIM115 — held past this frame, closed by caller
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


async def integrate_run_branch(project_path: Path, run: ExecutionRun) -> IntegrationResult:
    """Merge a completed worktree loop-task's branch into the source branch.

    Never raises — every failure mode returns a typed status so the loop
    advancement seam can decide (conflict → spawn resolution task; error →
    log and continue; the loop's budgets bound any recovery work).
    """
    if not (run.use_worktree and run.branch_name and run.worktree_path):
        return IntegrationResult("skipped", "not a worktree run")
    wt = Path(run.worktree_path)
    if not wt.exists():
        return IntegrationResult("skipped", "worktree path missing")

    try:
        # 0. Resolve the effective source branch. run.source_branch is normally
        #    set at worktree creation, but be defensive: if it's missing or a
        #    detached "HEAD", fall back to the project's current branch (the
        #    branch was cut from the project checkout). Using a literal "HEAD"
        #    as the rev-list base would resolve INSIDE the worktree to the task
        #    branch itself → always "0 commits" → work silently dropped as
        #    no_changes (audit finding #8).
        source_branch = run.source_branch
        if not source_branch or source_branch == "HEAD":
            rc, cur, _ = await _git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
            source_branch = cur if (rc == 0 and cur and cur != "HEAD") else None
        if not source_branch:
            return IntegrationResult(
                "error",
                "cannot resolve a named source branch (detached HEAD?) — refusing to guess a merge target",
            )

        # 1. Safety net: commit any residual uncommitted work in the worktree.
        rc, out, _ = await _git(wt, "status", "--porcelain")
        if rc == 0 and out:
            await _git(wt, "add", "-A")
            rc, _, err = await _git(wt, "commit", "-m", f"loop task work (run {run.id[:8]})")
            if rc != 0 and "nothing to commit" not in err.lower():
                return IntegrationResult("error", f"auto-commit failed: {err[:200]}")

        # 2. Anything to integrate at all? (source_branch ref is visible in the
        #    worktree — worktrees share the repo's git dir.)
        rc, out, _ = await _git(wt, "rev-list", "--count", f"{source_branch}..{run.branch_name}")
        if rc == 0 and out == "0":
            return IntegrationResult("no_changes", "branch has no commits beyond source")

        # 2b. Constraints denylist (Phase F1): NEVER integrate a branch that
        #     touches a denylisted path (secrets/creds by default, plus any
        #     project .gluon/constraints.md paths). This is the mechanical layer
        #     — it holds even if the agent ignored the injected constraints text,
        #     and it stops the auto-commit safety net from carrying an .env onto
        #     the source branch (audit finding #9). Work stays on the branch; the
        #     loop pauses for a human (on_run_completed treats this like
        #     branch_moved/error).
        from gluon.loop_constraints import denylisted_paths, load_constraints

        denylist = load_constraints(project_path).denylist
        rc, changed, _ = await _git(wt, "diff", "--name-only", f"{source_branch}..{run.branch_name}")
        if rc == 0 and changed:
            hits = denylisted_paths(changed.splitlines(), denylist)
            if hits:
                return IntegrationResult(
                    "denylist_violation",
                    f"branch touches denylisted path(s): {', '.join(hits[:5])}"
                    + (f" (+{len(hits) - 5} more)" if len(hits) > 5 else ""),
                )

        # 3. Merge in the main checkout, under the cross-process project lock.
        lock = await asyncio.to_thread(_acquire_project_lock, project_path)
        try:
            rc, current, _ = await _git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
            if rc != 0:
                return IntegrationResult("error", "cannot resolve project HEAD")
            if current != source_branch:
                return IntegrationResult(
                    "branch_moved",
                    f"project is on '{current}', task branched from '{source_branch}' — not merging",
                )

            rc, out, err = await _git(project_path, "merge", "--no-edit", run.branch_name)
            if rc == 0:
                if "already up to date" in (out + err).lower():
                    return IntegrationResult("up_to_date", "")
                logger.info("Integrated loop task branch %s into %s (run %s)", run.branch_name, current, run.id[:8])
                return IntegrationResult("merged", out[:200])

            # Conflict (or other merge failure): leave the checkout pristine.
            await _git(project_path, "merge", "--abort")
            return IntegrationResult("conflict", (err or out)[:400])
        finally:
            lock.close()
    except Exception as e:  # never raise into the advancement seam
        logger.warning("Integration of run %s failed: %s", run.id[:8], e, exc_info=True)
        return IntegrationResult("error", str(e)[:200])
