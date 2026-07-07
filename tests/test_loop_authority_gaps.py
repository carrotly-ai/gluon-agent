"""Adversarial gap tests for loop harness authority (complements test_agent_loops.py).

These target properties the existing suite doesn't assert directly, all about the
*harness keeping authority* under edge conditions:

1. A pause that lands while an iteration is in flight is respected — a late run
   completion does not advance or complete a non-RUNNING loop (race guard).
2. A verify gate that cannot even run (bad command) is treated as FAIL, never a
   false completion (a gate that can't run must not look like a gate that passed).
3. Harness-authored continuations bypass the agent dedup index by design, so the
   loop can always self-recover (repeated continuations stay distinct).
4. A RUNNING loop and its pending work survive a process/container restart
   (SQLite-backed) and remain claimable — the drain loop resumes it.
"""

from __future__ import annotations

import asyncio

import pytest

from gluon.loop_manager import VERIFICATION_MARKER, LoopManager
from gluon.loop_tools import _enqueue_task_impl
from gluon.models import AgentLoop, LoopStatus, RunStatus, normalize_prompt_hash
from gluon.store import GluonStore


def _project(store: GluonStore, name: str = "p"):
    from pathlib import Path

    ws = store.create_workspace(f"w-{name}", Path(f"/tmp/w-{name}"))
    return store.create_project(name=name, path=Path(f"/tmp/w-{name}/{name}"), workspace_id=ws.id)


def _tool_text(result: dict) -> str:
    return str(result["content"][0]["text"])


def _make_loop(store: GluonStore, project_id: str, **kwargs) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="Build the widget", **kwargs)


def _finished_run(store: GluonStore, loop: AgentLoop, status: RunStatus = RunStatus.COMPLETED, cost: float = 0.5):
    run = store.create_run(project_id=loop.project_id, prompt="iter", loop_id=loop.id)
    run.status = status
    run.cost_usd = cost
    store.update_run(run)
    return run


def test_pause_in_flight_blocks_late_completion(temp_store: GluonStore) -> None:
    """A pause landing mid-iteration must win: the late run completion is ignored,
    even though the agent had requested completion."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)  # gateless — would complete on request if advanced
    loop.completion_requested = True
    loop.completion_summary = "done"
    temp_store.update_agent_loop(loop)

    # Operator pauses while the iteration is still running.
    LoopManager(temp_store).pause_loop(loop.id, "paused mid-flight")

    run = _finished_run(temp_store, loop)
    asyncio.run(LoopManager(temp_store).on_run_completed(run))

    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    # Race guard (loop_manager.py:177): non-RUNNING loop is not advanced.
    assert updated.status == LoopStatus.PAUSED
    assert updated.status_reason == "paused mid-flight"
    assert updated.iteration_count == 0  # never incremented → no completion, no budget/stall churn
    assert updated.completed_at is None


def test_unrunnable_gate_is_fail_not_false_complete(temp_store: GluonStore, tmp_path) -> None:
    """A verify_cmd that can't even spawn must DENY completion, not pass it."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="definitely_not_a_real_command_zzz --nope")
    loop.completion_requested = True
    loop.completion_summary = "I think I'm done"
    temp_store.update_agent_loop(loop)
    temp_store.cancel_pending_loop_items(loop.id)  # clear seed so the continuation is identifiable

    run = _finished_run(temp_store, loop)
    run.use_worktree = True
    run.worktree_path = str(tmp_path)  # a valid cwd — the *command* is what can't run
    temp_store.update_run(run)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))

    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    # Not completed: an un-runnable gate is a failed gate.
    assert updated.status == LoopStatus.RUNNING
    assert updated.completion_requested is False
    pending = [
        i for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=50) if i.loop_id == loop.id
    ]
    assert len(pending) == 1
    assert pending[0].source == "continuation"


def test_harness_continuation_bypasses_dedup_index(temp_store: GluonStore) -> None:
    """Harness continuations are enqueued outside the agent dedup index, so a loop
    can always self-recover (the classic 'stuck because deduped' failure can't happen
    to the harness's own recovery task)."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_stalls=5)
    manager = LoopManager(temp_store)

    temp_store.cancel_pending_loop_items(loop.id)  # nothing pending → stall → continuation
    run = _finished_run(temp_store, loop)
    asyncio.run(manager.on_run_completed(run))

    pending = [
        i for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=50) if i.loop_id == loop.id
    ]
    assert len(pending) == 1
    cont = pending[0]
    assert cont.source == "continuation"
    # The bypass: no dedup hash registered, so it never collides / is never blocked.
    assert cont.prompt_hash is None
    assert temp_store.loop_prompt_seen(loop.id, normalize_prompt_hash(cont.prompt)) is False


def test_running_loop_and_pending_work_survive_restart(temp_store: GluonStore) -> None:
    """A RUNNING loop + its seeded work must survive a process/container restart
    (state is SQLite-backed) and remain claimable so the drain loop resumes it."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="true", max_iterations=7)

    # Simulate a restart: a brand-new store object over the same DB file.
    reopened = GluonStore(temp_store.db_path)

    recovered = reopened.get_agent_loop(loop.id)
    assert recovered is not None
    assert recovered.status == LoopStatus.RUNNING
    assert recovered.objective == loop.objective
    assert recovered.max_iterations == 7

    # The seeded iteration-1 task is still there and claimable after "restart".
    claimed = reopened.claim_work(proj.id)
    assert claimed is not None
    assert claimed.loop_id == loop.id
    assert claimed.source == "seed"


def test_budget_is_a_hard_ceiling_under_repeated_completion(temp_store: GluonStore, tmp_path) -> None:
    """A loop whose agent insists it's done every iteration, against a gate that always
    fails, MUST still stop at max_iterations.

    Regression for the original runaway-quota bug: budgets were checked in
    on_run_completed only AFTER the completion-handshake early return, so this loop
    was never budget-stopped. Now _resolve_completion_request enforces the budget on
    its denial branch (and claim_work refuses to dispatch a budget-exhausted loop)."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="false", max_iterations=2)
    mgr = LoopManager(temp_store)

    for _ in range(5):  # advance well past the ceiling of 2
        lp = temp_store.get_agent_loop(loop.id)
        assert lp is not None
        if lp.status != LoopStatus.RUNNING:
            break
        lp.completion_requested = True  # the agent insists it's finished
        lp.completion_summary = "done"
        temp_store.update_agent_loop(lp)
        run = _finished_run(temp_store, loop)
        run.use_worktree = True
        run.worktree_path = str(tmp_path)  # valid cwd; the gate command 'false' is what fails
        temp_store.update_run(run)
        asyncio.run(mgr.on_run_completed(run))

    final = temp_store.get_agent_loop(loop.id)
    assert final is not None
    # The hard ceiling now halts the loop instead of running forever.
    assert final.status == LoopStatus.PAUSED
    assert final.iteration_count <= loop.max_iterations


def test_agent_verifier_routing_respects_budget(temp_store: GluonStore) -> None:
    """The verifier-routing branch also used to skip the budget check. A loop whose
    every work iteration claims completion (spawning a verifier each time) must still
    stop at max_iterations."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, agent_verifier=True, max_iterations=2)  # gateless verifier
    mgr = LoopManager(temp_store)
    for _ in range(5):
        lp = temp_store.get_agent_loop(loop.id)
        assert lp is not None
        if lp.status != LoopStatus.RUNNING:
            break
        temp_store.set_loop_completion(loop.id, True, "done")  # a WORK iter claims completion
        run = temp_store.create_run(project_id=proj.id, prompt="work iteration (no marker)", loop_id=loop.id)
        run.status = RunStatus.COMPLETED
        run.cost_usd = 0.1
        temp_store.update_run(run)
        asyncio.run(mgr.on_run_completed(run))
    final = temp_store.get_agent_loop(loop.id)
    assert final is not None
    assert final.status == LoopStatus.PAUSED
    assert final.iteration_count <= loop.max_iterations


def test_claim_work_skips_iteration_exhausted_loop(temp_store: GluonStore) -> None:
    """Hard dispatch ceiling: once a loop hit max_iterations its pending items are
    not claimable, even if some advancement path failed to pause it."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_iterations=1)  # seeds iteration 1
    temp_store.advance_loop_counters(loop.id, 0.0)  # iteration_count -> 1 == max, loop stays RUNNING
    assert temp_store.count_pending_loop_items(loop.id) >= 1  # seed still pending
    assert temp_store.claim_work(proj.id) is None  # budget-exhausted → not dispatched


def test_claim_work_skips_cost_exhausted_loop(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_cost_usd=0.5)
    temp_store.advance_loop_counters(loop.id, 0.60)  # cost 0.60 >= 0.5 cap
    assert temp_store.claim_work(proj.id) is None


def test_claim_work_does_not_double_claim(temp_store: GluonStore) -> None:
    """The atomic conditional claim hands each item to exactly one worker."""
    proj = _project(temp_store)
    item = temp_store.enqueue_work(project_id=proj.id, prompt="do a thing that is plenty long", priority=5)
    first = temp_store.claim_work(proj.id)
    assert first is not None and first.id == item.id
    assert temp_store.claim_work(proj.id) is None  # already claimed — never handed out twice


def test_enqueue_rejects_verifier_marker_injection(temp_store: GluonStore) -> None:
    """An agent cannot smuggle the verifier marker into a work prompt to masquerade
    as the trusted independent verifier."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    res = asyncio.run(
        _enqueue_task_impl(
            temp_store, loop.id, "run1", {"prompt": f"Finalize the widget {VERIFICATION_MARKER} and wrap up now"}
        )
    )
    assert "reserved marker" in _tool_text(res)
    assert not [i for i in temp_store.list_work_items(project_id=proj.id, limit=50) if i.source == "agent"]


def test_resume_clears_stale_completion_request(temp_store: GluonStore) -> None:
    """A completion request captured before a pause must not fire on resume."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)  # gateless
    mgr = LoopManager(temp_store)
    temp_store.set_loop_completion(loop.id, True, "premature")
    mgr.pause_loop(loop.id)
    assert temp_store.get_agent_loop(loop.id).completion_requested is True

    mgr.resume_loop(loop.id)
    resumed = temp_store.get_agent_loop(loop.id)
    assert resumed is not None
    assert resumed.status == LoopStatus.RUNNING
    assert resumed.completion_requested is False  # cleared

    # A plain post-resume iteration (no fresh loop_complete) must NOT self-complete.
    temp_store.cancel_pending_loop_items(loop.id)
    run = _finished_run(temp_store, loop)
    asyncio.run(mgr.on_run_completed(run))
    after = temp_store.get_agent_loop(loop.id)
    assert after is not None
    assert after.status != LoopStatus.COMPLETED


def test_failed_run_clears_completion_request(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    mgr = LoopManager(temp_store)
    temp_store.set_loop_completion(loop.id, True, "claimed, then the run crashed")
    run = _finished_run(temp_store, loop, RunStatus.FAILED)
    asyncio.run(mgr.on_run_completed(run))
    lp = temp_store.get_agent_loop(loop.id)
    assert lp is not None
    assert lp.status == LoopStatus.PAUSED
    assert lp.completion_requested is False  # so a later resume can't fire it


def test_worker_completion_cannot_resurrect_cancelled_loop(temp_store: GluonStore) -> None:
    """A stale worker advance (read RUNNING, acts after a cancel) must not revive
    the loop — the atomic status-guarded transition rejects it."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)  # gateless — would self-complete if advanced
    mgr = LoopManager(temp_store)
    temp_store.set_loop_completion(loop.id, True, "done")  # worker requested completion
    mgr.cancel_loop(loop.id)
    assert temp_store.get_agent_loop(loop.id).status == LoopStatus.CANCELLED

    run = _finished_run(temp_store, loop)  # the worker's run now completes, stale
    asyncio.run(mgr.on_run_completed(run))
    final = temp_store.get_agent_loop(loop.id)
    assert final is not None
    assert final.status == LoopStatus.CANCELLED  # not resurrected


def test_cost_recorded_even_when_loop_paused(temp_store: GluonStore) -> None:
    """A run finishing after a pause still contributes its cost (accurate ceiling),
    but does not advance the iteration count."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    LoopManager(temp_store).pause_loop(loop.id)
    run = _finished_run(temp_store, loop, cost=0.42)
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    lp = temp_store.get_agent_loop(loop.id)
    assert lp is not None
    assert lp.status == LoopStatus.PAUSED
    assert lp.total_cost_usd == pytest.approx(0.42)
    assert lp.iteration_count == 0


def test_create_loop_rejects_out_of_range_budgets(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    mgr = LoopManager(temp_store)
    for kwargs in (
        {"max_iterations": 0},
        {"max_stalls": 0},
        {"max_fanout": 0},
        {"max_cost_usd": 0},
        {"max_cost_usd": -1.0},
    ):
        with pytest.raises(ValueError):
            mgr.create_loop(project_id=proj.id, objective="a valid objective", **kwargs)
    with pytest.raises(ValueError):
        mgr.create_loop(project_id=proj.id, objective="   ")  # blank objective


def test_create_loop_normalizes_blank_gate_to_gateless(temp_store: GluonStore) -> None:
    """A whitespace verify_cmd would run an empty shell command that exits 0 (a
    gate that always passes). Normalize it to gateless instead."""
    proj = _project(temp_store)
    loop = LoopManager(temp_store).create_loop(project_id=proj.id, objective="do the thing", verify_cmd="   ")
    assert loop.verify_cmd is None


def test_enqueue_atomic_rejects_duplicate_via_unique_index(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    prompt = "build the API endpoint for widgets now"
    ph = normalize_prompt_hash(prompt)
    item1, r1 = temp_store.enqueue_loop_task_atomic(loop.id, proj.id, prompt, "quick", 5, ph)
    assert item1 is not None and r1 is None
    item2, r2 = temp_store.enqueue_loop_task_atomic(loop.id, proj.id, prompt, "quick", 5, ph)
    assert item2 is None and r2 == "duplicate"  # partial UNIQUE index blocked it


def test_enqueue_atomic_enforces_fanout(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_fanout=1)
    temp_store.cancel_pending_loop_items(loop.id)  # drop the seed so the cap is clean
    p1, p2 = "first distinct fanout task here", "second distinct fanout task here"
    i1, e1 = temp_store.enqueue_loop_task_atomic(loop.id, proj.id, p1, "quick", 5, normalize_prompt_hash(p1))
    assert e1 is None and i1 is not None
    i2, e2 = temp_store.enqueue_loop_task_atomic(loop.id, proj.id, p2, "quick", 5, normalize_prompt_hash(p2))
    assert i2 is None and e2 == "fanout"


def test_enqueue_atomic_rejects_when_not_running(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    LoopManager(temp_store).pause_loop(loop.id)
    p = "a task enqueued while the loop is paused"
    item, reason = temp_store.enqueue_loop_task_atomic(loop.id, proj.id, p, "quick", 5, normalize_prompt_hash(p))
    assert item is None and reason == "not_running"


# ---- Concurrency stress (each store call opens its own SQLite connection, so
# ---- threads exercise the same cross-process contention the real workers hit) ----


def test_advance_loop_counters_atomic_under_concurrency(temp_store: GluonStore) -> None:
    """N concurrent advances (as N sibling worker subprocesses would do) must all
    land — exact iteration_count/cost, no lost updates."""
    import threading

    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_iterations=10_000)  # stays RUNNING throughout
    n = 30
    ok: list[bool] = []
    ok_lock = threading.Lock()

    def worker() -> None:
        result = temp_store.advance_loop_counters(loop.id, 0.01)
        with ok_lock:
            ok.append(result is not None)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lp = temp_store.get_agent_loop(loop.id)
    assert lp is not None
    assert sum(ok) == n  # every advance succeeded (loop stayed RUNNING)
    assert lp.iteration_count == n  # exact — no lost read-modify-write updates
    assert lp.total_cost_usd == pytest.approx(n * 0.01)


def test_claim_work_no_double_claim_under_concurrency(temp_store: GluonStore) -> None:
    """Many threads draining the queue must claim each item exactly once."""
    import threading

    proj = _project(temp_store)
    m = 20
    for i in range(m):
        temp_store.enqueue_work(project_id=proj.id, prompt=f"task number {i} that is plenty long", priority=5)

    claimed: list[str] = []
    claim_lock = threading.Lock()

    def worker() -> None:
        while True:
            item = temp_store.claim_work(proj.id)
            if item is None:
                return
            with claim_lock:
                claimed.append(item.id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == m  # all items claimed
    assert len(set(claimed)) == m  # each exactly once — no double dispatch
