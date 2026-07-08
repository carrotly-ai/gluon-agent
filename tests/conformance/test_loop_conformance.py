"""Loop-engine conformance scenarios (Phase D).

Each scenario drives a REAL primitive against deterministic fake-worker
outcomes and maps to a session-audit finding. Written to be RED before its fix
and GREEN after — the harness is how we know the Phase D fixes hold and can't
silently regress.

Cross-process scenarios use ``multiprocessing`` and real git repos; the rest
drive the real store / LoopManager in-process. No Claude calls anywhere.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import subprocess
import time
from pathlib import Path

from gluon.loop_integration import _project_lock_key, integrate_run_branch
from gluon.loop_manager import LoopManager
from gluon.models import AgentLoop, ExecutionRun, LoopStatus, RunStatus, WorkQueueStatus
from gluon.store import GluonStore
from gluon.work_queue import WorkQueueManager

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@gluon.dev")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "base")
    return root


def _worktree_branch(repo: Path, run_id: str, filename: str, content: str) -> Path:
    branch = f"gluon-task/{run_id}"
    wt = repo.parent / f"wt-{run_id}"
    _git(repo, "worktree", "add", "-b", branch, str(wt), "main")
    (wt / filename).write_text(content)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", f"work {run_id}")
    return wt


def _run(run_id: str, project_id: str, wt: Path, source_branch: str = "main") -> ExecutionRun:
    return ExecutionRun(
        id=run_id,
        project_id=project_id,
        prompt="iter",
        use_worktree=True,
        branch_name=f"gluon-task/{run_id}",
        source_branch=source_branch,
        worktree_path=str(wt),
    )


def _project(store: GluonStore, path: Path, name: str = "p"):
    ws = store.create_workspace(f"w-{name}", path.parent)
    return store.create_project(name=name, path=path, workspace_id=ws.id)


def _loop(store: GluonStore, project_id: str, **kw) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="ship it", **kw)


# ===========================================================================
# Audit #1 — cross-process merge-back lock (deterministic filename)
# ===========================================================================


def _emit_lock_key(path_str: str, q) -> None:
    # Runs in a SEPARATE process (fresh PYTHONHASHSEED). The OLD implementation
    # used builtin hash() → each process produced a different key → the lock
    # excluded nothing. sha256 must be identical across processes.
    q.put(_project_lock_key(Path(path_str)))


def test_lock_key_is_stable_across_processes() -> None:
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    path = "/home/gluon/workspaces/some-project"
    procs = [ctx.Process(target=_emit_lock_key, args=(path, q)) for _ in range(3)]
    for p in procs:
        p.start()
    keys = {q.get(timeout=30) for _ in procs}
    for p in procs:
        p.join(timeout=30)
    assert len(keys) == 1, f"lock key differs across processes: {keys}"
    # And it equals the in-process value — the same file is locked everywhere.
    assert keys.pop() == _project_lock_key(Path(path))


def _hold_lock(repo_str: str, hold_s: float, out_path: str) -> None:
    from gluon.loop_integration import _acquire_project_lock

    handle = _acquire_project_lock(Path(repo_str))
    Path(out_path).write_text(f"{time.time()}")  # acquired-at
    time.sleep(hold_s)
    handle.close()


def test_lock_serializes_across_processes(tmp_path: Path) -> None:
    # Two processes contend for the same project's integration lock. With the
    # deterministic filename they lock the SAME file, so the second blocks until
    # the first releases.
    repo = _init_repo(tmp_path / "repo")
    ctx = mp.get_context("spawn")
    a_out = str(tmp_path / "a.txt")
    b_out = str(tmp_path / "b.txt")
    a = ctx.Process(target=_hold_lock, args=(str(repo), 1.0, a_out))
    a.start()
    time.sleep(0.2)  # ensure A grabs first
    b = ctx.Process(target=_hold_lock, args=(str(repo), 0.0, b_out))
    b.start()
    a.join(timeout=30)
    b.join(timeout=30)
    a_at = float(Path(a_out).read_text())
    b_at = float(Path(b_out).read_text())
    # B could only acquire after A released (~1s later): serialized, not both-at-once.
    assert b_at - a_at >= 0.8, f"lock did not serialize: A@{a_at} B@{b_at}"


def _merge_worker(repo_str: str, run_id: str, filename: str) -> None:
    repo = Path(repo_str)
    _worktree_branch(repo, run_id, filename, f"from {run_id}\n")
    run = _run(run_id, "proj", repo.parent / f"wt-{run_id}")
    asyncio.run(integrate_run_branch(repo, run))


def test_concurrent_integrations_do_not_corrupt(tmp_path: Path) -> None:
    # Three sibling worktree tasks integrate distinct files into one checkout
    # from separate processes. Serialized by the lock → all three land, no
    # corruption, checkout clean.
    repo = _init_repo(tmp_path / "repo")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_merge_worker, args=(str(repo), f"run{i:04d}", f"f{i}.txt")) for i in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    assert _git(repo, "status", "--porcelain") == ""  # no half-applied merge
    for i in range(3):
        assert (repo / f"f{i}.txt").exists(), f"f{i}.txt lost to a merge collision"


# ===========================================================================
# Audit #2 — work-item transitions must not silently miss items past a window
# ===========================================================================


def test_mark_completed_finds_item_past_20_row_window(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    # Crowd the oldest-20 window with terminal loop-priority items.
    for i in range(30):
        it = temp_store.enqueue_work(project_id=proj.id, prompt=f"old terminal item number {i}", priority=5)
        wq = WorkQueueManager(temp_store)
        wq.mark_running(it.id, f"r{i}")
        wq.mark_completed(it.id)
    # A fresh item sorts AFTER the 30 terminal ones (created_at) → outside a
    # LIMIT-20 scan. The OLD list-scan mark_* would silently no-op here.
    target = temp_store.enqueue_work(project_id=proj.id, prompt="the freshly created target item", priority=5)
    wq = WorkQueueManager(temp_store)
    wq.mark_running(target.id, "r-target")
    assert temp_store.get_work_item(target.id).status == WorkQueueStatus.RUNNING  # type: ignore[union-attr]
    wq.mark_completed(target.id)
    assert temp_store.get_work_item(target.id).status == WorkQueueStatus.COMPLETED  # type: ignore[union-attr]


def test_reconcile_settles_orphaned_running_item(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj")
    loop = _loop(temp_store, proj.id)
    item = temp_store.enqueue_work(project_id=proj.id, prompt="a task orphaned in RUNNING state", loop_id=loop.id)
    run = temp_store.create_run(project_id=proj.id, prompt="x", loop_id=loop.id)
    run.status = RunStatus.COMPLETED
    temp_store.update_run(run)
    # Simulate the crash-orphan: item stuck RUNNING, linked run already terminal.
    wq = WorkQueueManager(temp_store)
    wq.mark_running(item.id, run.id)
    # Force it to look orphaned (mark_running set claimed_by=run.id already).
    settled = temp_store.reconcile_orphaned_work_items()
    assert settled >= 1
    assert temp_store.get_work_item(item.id).status == WorkQueueStatus.COMPLETED  # type: ignore[union-attr]


# ===========================================================================
# Audit #3 — a dependent is not claimable until its dep's item is COMPLETED
# ===========================================================================


def test_dependent_blocked_while_dep_item_not_completed(temp_store: GluonStore, tmp_path: Path) -> None:
    # The protective property D2.3 relies on: keeping the dep item RUNNING during
    # integration keeps the dependent unclaimable. So while dep is RUNNING (not
    # yet COMPLETED — integration still in flight), claim_work must NOT hand out
    # the dependent.
    proj = _project(temp_store, tmp_path / "proj", name="dep")
    a = temp_store.enqueue_work(project_id=proj.id, prompt="dependency task A long enough", priority=5)
    b = temp_store.enqueue_work(
        project_id=proj.id, prompt="dependent task B needs A first", priority=5, depends_on=[a.id]
    )
    wq = WorkQueueManager(temp_store)
    # A is claimed and RUNNING (its integration would happen before COMPLETED).
    claimed = temp_store.claim_work(proj.id)
    assert claimed is not None and claimed.id == a.id
    wq.mark_running(a.id, "r-a")
    # B must be unclaimable: its dep is not COMPLETED.
    assert temp_store.claim_work(proj.id) is None
    # Only after A is COMPLETED (post-integration) does B become claimable.
    wq.mark_completed(a.id)
    nxt = temp_store.claim_work(proj.id)
    assert nxt is not None and nxt.id == b.id


# ===========================================================================
# Audit #4 — stall detection must not fire while a sibling is in flight
# ===========================================================================


def _seed_consumed(store: GluonStore, loop: AgentLoop) -> None:
    seed = next(i for i in store.list_work_items(project_id=loop.project_id, limit=200) if i.loop_id == loop.id)
    wq = WorkQueueManager(store)
    wq.mark_running(seed.id, "r-seed")
    wq.mark_completed(seed.id)


def test_no_stall_while_sibling_running(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj", name="par")
    loop = _loop(temp_store, proj.id, use_worktree=True, max_stalls=1)
    _seed_consumed(temp_store, loop)
    # Two independent sibling tasks; both dispatched (RUNNING), none pending.
    t1 = temp_store.enqueue_work(project_id=proj.id, prompt="sibling task one runs in parallel", loop_id=loop.id)
    t2 = temp_store.enqueue_work(project_id=proj.id, prompt="sibling task two runs in parallel", loop_id=loop.id)
    wq = WorkQueueManager(temp_store)
    wq.mark_running(t1.id, "r-t1")
    wq.mark_running(t2.id, "r-t2")
    # t1 completes; t2 still RUNNING. on_run_completed for t1's run must NOT
    # treat the loop as stalled (t2 is in flight) — no spurious continuation,
    # no stall bump.
    run1 = temp_store.create_run(project_id=proj.id, prompt="t1", loop_id=loop.id, initiator=f"queue:{t1.id}")
    run1.status = RunStatus.COMPLETED
    temp_store.update_run(run1)
    wq.mark_completed(t1.id)  # production finalizes after advancement; emulate settled item
    asyncio.run(LoopManager(temp_store).on_run_completed(run1))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    assert after.status == LoopStatus.RUNNING
    assert after.stall_count == 0
    # No continuation was injected while a sibling was live.
    conts = [
        i
        for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=200)
        if i.loop_id == loop.id and i.source == "continuation"
    ]
    assert conts == []


def test_count_active_excludes_current_run(temp_store: GluonStore, tmp_path: Path) -> None:
    proj = _project(temp_store, tmp_path / "proj", name="excl")
    loop = _loop(temp_store, proj.id, use_worktree=True)
    item = temp_store.enqueue_work(project_id=proj.id, prompt="the only task, mid-completion", loop_id=loop.id)
    wq = WorkQueueManager(temp_store)
    wq.mark_running(item.id, "r-self")
    # Counting active items while excluding the run that owns the only in-flight
    # item yields 0 — the loop's own finishing iteration must not keep it "alive".
    assert temp_store.count_active_loop_items(loop.id, exclude_run_id="r-self") == 0
    assert temp_store.count_active_loop_items(loop.id) == 1


# ===========================================================================
# Terminal-state invariant + watch-budget bound
# ===========================================================================


def test_watch_loop_does_not_reseed_when_budget_exhausted(temp_store: GluonStore, tmp_path: Path) -> None:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj = _project(temp_store, proj_dir, name="wb")
    # max_iterations=1: after the seed run advances the counter to 1, the loop is
    # budget-exhausted and must PAUSE at the budget check — before the watch
    # reseed branch (which would otherwise fire on the always-0 `true` watch).
    loop = _loop(temp_store, proj.id, watch_cmd="true", max_iterations=1)
    _seed_consumed(temp_store, loop)
    run = temp_store.create_run(project_id=proj.id, prompt="surveyor", loop_id=loop.id)
    run.status = RunStatus.COMPLETED
    run.cost_usd = 0.01
    temp_store.update_run(run)
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    assert after.status == LoopStatus.PAUSED  # budget, not an unbounded watch reseed
    watch_seeds = [
        i
        for i in temp_store.list_work_items(project_id=proj.id, limit=200)
        if i.loop_id == loop.id and "WATCH TRIGGER" in i.prompt
    ]
    assert watch_seeds == []
