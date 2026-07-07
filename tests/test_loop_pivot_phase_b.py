"""Tests for the loop-first pivot Phase B (docs/design/loop-first-pivot.md).

Two seams:

- B1 — worktree merge-back integration (``loop_integration.integrate_run_branch``):
  a completed loop-task's branch is merged back into the project's source
  branch so siblings and later verification build on integrated state. Every
  failure mode returns a typed status and never raises. Exercised against REAL
  temporary git repositories (with worktrees), not mocks — the whole point is
  that git plumbing behaves.

- B2 — autonomy ladder / plan checkpoint (``LoopManager.on_run_completed``):
  an L1/L2 loop PAUSES after the surveyor authors the plan (human approves
  before execution); an L3 loop runs straight through. Plus autonomy validation
  at every creation entry point.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from gluon.loop_integration import INTEGRATED_STATUSES, IntegrationResult, integrate_run_branch
from gluon.loop_manager import LoopManager
from gluon.models import AgentLoop, ExecutionRun, LoopStatus, RunStatus
from gluon.store import GluonStore
from gluon.work_queue import WorkQueueManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(root: Path) -> Path:
    """A git repo on branch ``main`` with one commit. Returns the checkout path."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@gluon.dev")
    _git(root, "config", "user.name", "Gluon Test")
    (root / "README.md").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "base")
    return root


def _worktree_branch(repo: Path, run_id: str, *, files: dict[str, str] | None = None, commit: bool = True) -> Path:
    """Create a ``gluon-task/<run_id>`` worktree off main, optionally with work.

    Returns the worktree path. When ``commit`` is False the files are left
    uncommitted (exercises the auto-commit safety net).
    """
    branch = f"gluon-task/{run_id}"
    wt = repo.parent / f"wt-{run_id}"
    _git(repo, "worktree", "add", "-b", branch, str(wt), "main")
    for name, content in (files or {}).items():
        (wt / name).write_text(content)
    if files and commit:
        _git(wt, "add", "-A")
        _git(wt, "commit", "-m", f"task work {run_id}")
    return wt


def _run(run_id: str, project_id: str, wt: Path | None, *, source_branch: str = "main") -> ExecutionRun:
    return ExecutionRun(
        id=run_id,
        project_id=project_id,
        prompt="iter",
        use_worktree=wt is not None,
        branch_name=f"gluon-task/{run_id}" if wt is not None else None,
        source_branch=source_branch,
        worktree_path=str(wt) if wt is not None else None,
    )


def _project(store: GluonStore, path: Path, name: str = "p"):
    ws = store.create_workspace(f"w-{name}", path.parent)
    return store.create_project(name=name, path=path, workspace_id=ws.id)


def _make_loop(store: GluonStore, project_id: str, **kwargs) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="Ship the campaign", **kwargs)


def _seed_item(store: GluonStore, loop: AgentLoop):
    """The surveyor (seed) work item auto-enqueued by create_loop."""
    items = [i for i in store.list_work_items(project_id=loop.project_id, limit=100) if i.loop_id == loop.id]
    seed = [i for i in items if i.source == "seed"]
    assert len(seed) == 1
    return seed[0]


# ===========================================================================
# B1 — worktree merge-back integration (real git)
# ===========================================================================


def test_integrate_merges_task_branch_into_source(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    wt = _worktree_branch(repo, "run0001", files={"feature.txt": "from task\n"})
    run = _run("run0001", "proj", wt)

    result = asyncio.run(integrate_run_branch(repo, run))

    assert result.status == "merged", result.detail
    # The task's file is now on the project's main branch.
    assert (repo / "feature.txt").read_text() == "from task\n"


def test_integrate_auto_commits_residual_worktree_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    # Agent left work uncommitted — the safety net must commit it, then merge.
    wt = _worktree_branch(repo, "run0002", files={"draft.txt": "uncommitted\n"}, commit=False)
    run = _run("run0002", "proj", wt)

    result = asyncio.run(integrate_run_branch(repo, run))

    assert result.status == "merged", result.detail
    assert (repo / "draft.txt").read_text() == "uncommitted\n"


def test_integrate_no_changes_when_branch_has_no_new_commits(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    wt = _worktree_branch(repo, "run0003")  # branch off main, no work
    run = _run("run0003", "proj", wt)

    result = asyncio.run(integrate_run_branch(repo, run))

    assert result.status == "no_changes"
    assert result.status in INTEGRATED_STATUSES


def test_integrate_reports_conflict_and_leaves_checkout_pristine(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    # Task edits README...
    wt = _worktree_branch(repo, "run0004", files={"README.md": "task version\n"})
    run = _run("run0004", "proj", wt)
    # ...meanwhile main diverges on the same file → conflicting merge.
    (repo / "README.md").write_text("main version\n")
    _git(repo, "commit", "-am", "diverge main")

    result = asyncio.run(integrate_run_branch(repo, run))

    assert result.status == "conflict"
    assert result.status not in INTEGRATED_STATUSES
    # Merge was aborted — the checkout is clean, on main, with main's content.
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "README.md").read_text() == "main version\n"


def test_integrate_branch_moved_when_project_on_different_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    wt = _worktree_branch(repo, "run0005", files={"feature.txt": "x\n"})
    run = _run("run0005", "proj", wt)
    # User switched the checkout to another branch mid-loop.
    _git(repo, "checkout", "-b", "elsewhere")

    result = asyncio.run(integrate_run_branch(repo, run))

    assert result.status == "branch_moved"
    assert result.status not in INTEGRATED_STATUSES


def test_integrate_skips_non_worktree_run(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    run = _run("run0006", "proj", None)  # not a worktree run
    result = asyncio.run(integrate_run_branch(repo, run))
    assert result.status == "skipped"


def test_integrate_skips_when_worktree_path_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    run = _run("run0007", "proj", tmp_path / "does-not-exist")
    result = asyncio.run(integrate_run_branch(repo, run))
    assert result.status == "skipped"


def test_integrate_never_raises_on_garbage_project(tmp_path: Path) -> None:
    # project_path is not a git repo at all — must degrade to a typed status.
    not_a_repo = tmp_path / "empty"
    not_a_repo.mkdir()
    wt = not_a_repo / "wt"
    wt.mkdir()
    run = _run("run0008", "proj", wt)
    result = asyncio.run(integrate_run_branch(not_a_repo, run))
    assert isinstance(result, IntegrationResult)
    assert result.status in {"error", "no_changes", "skipped"}


# ===========================================================================
# B2 — autonomy ladder: plan checkpoint pause
# ===========================================================================


def _seed_run_completed(store: GluonStore, loop: AgentLoop) -> ExecutionRun:
    """Mirror production: the seed item is claimed + closed, and its run
    (linked via initiator ``queue:<seed_id>``) reaches COMPLETED — then
    on_run_completed fires."""
    seed = _seed_item(store, loop)
    wq = WorkQueueManager(store)
    wq.mark_running(seed.id, "r-seed")
    wq.mark_completed(seed.id)
    run = store.create_run(
        project_id=loop.project_id,
        prompt="surveyor",
        loop_id=loop.id,
        initiator=f"queue:{seed.id}",
    )
    run.status = RunStatus.COMPLETED
    run.cost_usd = 0.05
    store.update_run(run)
    return run


@pytest.mark.parametrize("autonomy", ["L1", "L2"])
def test_l1_l2_loop_pauses_after_surveyor_authors_plan(temp_store: GluonStore, tmp_path: Path, autonomy: str) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, autonomy=autonomy)
    run = _seed_run_completed(temp_store, loop)
    # Surveyor authored one follow-up task (the "plan").
    temp_store.enqueue_work(
        project_id=proj.id,
        prompt="execute the first planned unit of work here",
        loop_id=loop.id,
        source="task_spawn",
    )

    asyncio.run(LoopManager(temp_store).on_run_completed(run))

    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    assert after.status == LoopStatus.PAUSED
    assert "plan ready for review" in (after.status_reason or "")


def test_l3_loop_does_not_pause_at_plan(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, autonomy="L3")
    run = _seed_run_completed(temp_store, loop)
    temp_store.enqueue_work(
        project_id=proj.id,
        prompt="execute the first planned unit of work here",
        loop_id=loop.id,
        source="task_spawn",
    )

    asyncio.run(LoopManager(temp_store).on_run_completed(run))

    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    # Unattended: stays RUNNING to execute the plan (pending work keeps it alive).
    assert after.status == LoopStatus.RUNNING


def test_l1_loop_without_authored_plan_does_not_pause_for_review(temp_store: GluonStore, tmp_path: Path) -> None:
    # Surveyor enqueued nothing → no plan to review. The plan-checkpoint clause
    # requires pending>0; with zero pending the loop falls through to stall
    # detection, not a spurious "plan ready" pause.
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, autonomy="L1")
    run = _seed_run_completed(temp_store, loop)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))

    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    assert "plan ready for review" not in (after.status_reason or "")


# ===========================================================================
# B2 — autonomy validation at creation
# ===========================================================================


@pytest.mark.parametrize("bad", ["L4", "l0", "high", "  "])
def test_create_loop_rejects_invalid_autonomy(temp_store: GluonStore, tmp_path: Path, bad: str) -> None:
    # Any non-empty value outside {L1,L2,L3} (case-insensitive) is rejected —
    # including whitespace-only, which is truthy so it does NOT fall back to L3.
    proj = _project(temp_store, tmp_path / "proj")
    with pytest.raises(ValueError, match="autonomy"):
        _make_loop(temp_store, proj.id, autonomy=bad)


def test_create_loop_empty_autonomy_defaults_to_l3(temp_store: GluonStore, tmp_path: Path) -> None:
    # Only a truly empty string is falsy → normalized to the L3 default.
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, autonomy="")
    assert loop.autonomy == "L3"


@pytest.mark.parametrize("good,expected", [("l1", "L1"), ("L2", "L2"), ("l3", "L3")])
def test_create_loop_normalizes_autonomy_case(temp_store: GluonStore, tmp_path: Path, good: str, expected: str) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    loop = _make_loop(temp_store, proj.id, autonomy=good)
    assert loop.autonomy == expected
    # Persisted + round-tripped through the store.
    assert temp_store.get_agent_loop(loop.id).autonomy == expected  # type: ignore[union-attr]
