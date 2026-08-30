"""Tests for the loop-first pivot Phase A (docs/design/loop-first-pivot.md).

Covers the work-graph substrate: dependency edges + ready-set dispatch (A1),
within-project parallel claiming (A2), task_spawn semantics — depends_on +
task-level verify gates with fix continuations (A3), the survey/decompose seed
(A4), and loop lifecycle notifications (A5). Includes the edge cases: failed
dependencies cascade-cancel dependents (no silent deadlock), dangling deps
fail closed, and task-gate fix cycles respect budgets.
"""

from __future__ import annotations

import asyncio

import pytest

from gluon.loop_manager import _SEED_PROMPT_TEMPLATE, LoopManager
from gluon.loop_tools import _enqueue_task_impl
from gluon.models import AgentLoop, LoopStatus, RunStatus, WorkQueueStatus, normalize_prompt_hash
from gluon.store import GluonStore
from gluon.work_queue import WorkQueueManager


def _project(store: GluonStore, name: str = "p"):
    from pathlib import Path

    ws = store.create_workspace(f"w-{name}", Path(f"/tmp/w-{name}"))
    return store.create_project(name=name, path=Path(f"/tmp/w-{name}/{name}"), workspace_id=ws.id)


def _make_loop(store: GluonStore, project_id: str, **kwargs) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="Build the widget", **kwargs)


def _tool_text(result: dict) -> str:
    return str(result["content"][0]["text"])


def _finished_run(
    store: GluonStore,
    loop: AgentLoop,
    status: RunStatus = RunStatus.COMPLETED,
    cost: float = 0.1,
    initiator: str | None = None,
):
    run = store.create_run(project_id=loop.project_id, prompt="iter", loop_id=loop.id, initiator=initiator)
    run.status = status
    run.cost_usd = cost
    store.update_run(run)
    return run


def _dispatch_and_complete(store: GluonStore, project_id: str, item_id: str) -> None:
    """Mirror production: the item is claimed, run, and closed out by
    _finalize_queue_item BEFORE LoopManager.on_run_completed fires. Marks the
    specific item directly (claim_work would grab whichever item ranks highest,
    e.g. a previously-spawned fix task)."""
    wq = WorkQueueManager(store)
    wq.mark_running(item_id, "r-task")
    wq.mark_completed(item_id)


# ---------------------------------------------------------------------------
# A1 — dependency edges + ready-set dispatch
# ---------------------------------------------------------------------------


def test_dependent_item_not_claimable_until_dep_completes(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    a = temp_store.enqueue_work(project_id=proj.id, prompt="task A quite long enough", priority=5)
    b = temp_store.enqueue_work(project_id=proj.id, prompt="task B needs A first indeed", priority=1, depends_on=[a.id])
    # B has HIGHER priority but is not ready — A must be claimed first.
    first = temp_store.claim_work(proj.id)
    assert first is not None and first.id == a.id
    assert temp_store.claim_work(proj.id) is None  # B blocked (A only claimed)

    wq = WorkQueueManager(temp_store)
    wq.mark_running(a.id, "r1")
    wq.mark_completed(a.id)
    second = temp_store.claim_work(proj.id)
    assert second is not None and second.id == b.id  # ready after A completes


def test_dangling_dependency_fails_closed(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    temp_store.enqueue_work(project_id=proj.id, prompt="ghost-dep task long enough", depends_on=["nonexistent"])
    assert temp_store.claim_work(proj.id) is None  # never claimable


def test_diamond_dependency_readiness(temp_store: GluonStore) -> None:
    """A ← B, A ← C, D ← {B, C}: D only ready after BOTH B and C complete."""
    proj = _project(temp_store)
    wq = WorkQueueManager(temp_store)
    a = temp_store.enqueue_work(project_id=proj.id, prompt="diamond root task A long", priority=5)
    b = temp_store.enqueue_work(project_id=proj.id, prompt="diamond left task B long", depends_on=[a.id])
    c = temp_store.enqueue_work(project_id=proj.id, prompt="diamond right task C long", depends_on=[a.id])
    d = temp_store.enqueue_work(project_id=proj.id, prompt="diamond join task D long", depends_on=[b.id, c.id])

    for expected in (a,):
        got = temp_store.claim_work(proj.id)
        assert got is not None and got.id == expected.id
        wq.mark_running(got.id, "r")
        wq.mark_completed(got.id)

    got_b = temp_store.claim_work(proj.id)
    got_c = temp_store.claim_work(proj.id)
    assert {got_b.id, got_c.id} == {b.id, c.id}  # both ready in parallel
    assert temp_store.claim_work(proj.id) is None  # D not ready yet
    for item in (got_b, got_c):
        wq.mark_running(item.id, "r")
        wq.mark_completed(item.id)
    got_d = temp_store.claim_work(proj.id)
    assert got_d is not None and got_d.id == d.id


def test_failed_dependency_cascade_cancels_transitively(temp_store: GluonStore) -> None:
    """A fails → B (dep A) cancelled → C (dep B) cancelled too (fixpoint)."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    temp_store.cancel_pending_loop_items(loop.id)  # drop seed
    a = temp_store.enqueue_work(project_id=proj.id, prompt="root that will fail long", loop_id=loop.id)
    b = temp_store.enqueue_work(
        project_id=proj.id, prompt="child of failing root long", loop_id=loop.id, depends_on=[a.id]
    )
    c = temp_store.enqueue_work(
        project_id=proj.id, prompt="grandchild of failing long", loop_id=loop.id, depends_on=[b.id]
    )

    wq = WorkQueueManager(temp_store)
    claimed = temp_store.claim_work(proj.id)
    assert claimed.id == a.id
    wq.mark_running(a.id, "r1")
    wq.mark_failed(a.id, "boom")

    cancelled = temp_store.cancel_dead_loop_items(loop.id)
    assert cancelled == 2  # B and C, transitively
    assert temp_store.get_work_item(b.id).status == WorkQueueStatus.CANCELLED
    assert temp_store.get_work_item(c.id).status == WorkQueueStatus.CANCELLED


def test_enqueue_atomic_rejects_bad_dependencies(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    other = _make_loop(temp_store, _project(temp_store, "q").id)
    other_items = temp_store.list_work_items(project_id=other.project_id, status="pending")
    foreign_dep = other_items[0].id  # the other loop's seed

    for deps in (["missing-id"], [foreign_dep]):
        item, reject = temp_store.enqueue_loop_task_atomic(
            loop.id,
            proj.id,
            f"task with bad dep {deps} long",
            "quick",
            5,
            normalize_prompt_hash(f"bad {deps}"),
            depends_on=deps,
        )
        assert item is None and reject == "bad_dependency"


# ---------------------------------------------------------------------------
# A2 — within-project parallel claiming
# ---------------------------------------------------------------------------


def test_parallel_only_claims_only_worktree_loop_items(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    serial_loop = _make_loop(temp_store, proj.id)  # use_worktree=False; seeds one item
    assert temp_store.claim_work(proj.id, parallel_only=True) is None  # not parallel-safe
    # A worktree loop's items ARE parallel-claimable
    wt_loop = LoopManager(temp_store).create_loop(project_id=proj.id, objective="parallel objective", use_worktree=True)
    got = temp_store.claim_work(proj.id, parallel_only=True)
    assert got is not None and got.loop_id == wt_loop.id
    # sanity: the serial loop's seed is still claimable in normal mode
    got2 = temp_store.claim_work(proj.id)
    assert got2 is not None and got2.loop_id == serial_loop.id


def test_parallel_claim_still_respects_dependencies(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = LoopManager(temp_store).create_loop(project_id=proj.id, objective="obj", use_worktree=True)
    temp_store.cancel_pending_loop_items(loop.id)
    a = temp_store.enqueue_work(project_id=proj.id, prompt="parallel root task long", loop_id=loop.id)
    temp_store.enqueue_work(
        project_id=proj.id, prompt="parallel dependent task long", loop_id=loop.id, depends_on=[a.id]
    )
    first = temp_store.claim_work(proj.id, parallel_only=True)
    assert first is not None and first.id == a.id
    assert temp_store.claim_work(proj.id, parallel_only=True) is None  # dependent blocked


# ---------------------------------------------------------------------------
# A3 — task_spawn: depends_on + verify_cmd through the tool; task gates
# ---------------------------------------------------------------------------


def test_tool_enqueue_with_depends_on_and_gate(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    r1 = asyncio.run(_enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": "first independent unit of work"}))
    assert "Task enqueued (id " in _tool_text(r1)
    dep_id = _tool_text(r1).split("Task enqueued (id ")[1].split(",")[0].strip()

    r2 = asyncio.run(
        _enqueue_task_impl(
            temp_store,
            loop.id,
            "run1",
            {"prompt": "second unit depending on the first", "depends_on": [dep_id], "verify_cmd": "true"},
        )
    )
    txt = _tool_text(r2)
    assert "Depends on:" in txt and "Task gate:" in txt
    items = [i for i in temp_store.list_work_items(project_id=proj.id, limit=50) if i.source == "agent"]
    dependent = next(i for i in items if i.depends_on)
    assert dependent.depends_on == [dep_id]
    assert dependent.verify_cmd == "true"


def test_tool_enqueue_rejects_unknown_dependency(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    r = asyncio.run(
        _enqueue_task_impl(
            temp_store, loop.id, "run1", {"prompt": "task with a phantom dependency", "depends_on": ["nope"]}
        )
    )
    assert "invalid depends_on" in _tool_text(r)


def test_task_gate_failure_spawns_fix_and_keeps_loop_running(temp_store: GluonStore, tmp_path) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    temp_store.cancel_pending_loop_items(loop.id)
    item = temp_store.enqueue_work(
        project_id=proj.id, prompt="a gated task that will fail its gate", loop_id=loop.id, verify_cmd="false"
    )
    _dispatch_and_complete(temp_store, proj.id, item.id)
    run = _finished_run(temp_store, loop, initiator=f"queue:{item.id}")
    run.use_worktree = True
    run.worktree_path = str(tmp_path)
    temp_store.update_run(run)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))

    lp = temp_store.get_agent_loop(loop.id)
    assert lp.status == LoopStatus.RUNNING  # fix cycle, not a pause
    pending = [
        i for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=50) if i.loop_id == loop.id
    ]
    assert len(pending) == 1
    fix = pending[0]
    assert fix.source == "continuation"
    assert "TASK GATE FAILED" in fix.prompt
    assert fix.verify_cmd == "false"  # fix judged by the same gate


def test_task_gate_pass_no_fix_task(temp_store: GluonStore, tmp_path) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    item = temp_store.enqueue_work(
        project_id=proj.id, prompt="a gated task that passes its gate", loop_id=loop.id, verify_cmd="true"
    )
    run = _finished_run(temp_store, loop, initiator=f"queue:{item.id}")
    run.use_worktree = True
    run.worktree_path = str(tmp_path)
    temp_store.update_run(run)
    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    pending = [
        i for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=50) if i.loop_id == loop.id
    ]
    assert not any("TASK GATE FAILED" in i.prompt for i in pending)


def test_task_gate_failure_demotes_completion_claim(temp_store: GluonStore, tmp_path) -> None:
    """An iteration that failed its own task gate cannot complete the loop."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)  # gateless loop — claim would otherwise complete it
    temp_store.cancel_pending_loop_items(loop.id)
    item = temp_store.enqueue_work(
        project_id=proj.id, prompt="gated task claiming completion", loop_id=loop.id, verify_cmd="false"
    )
    _dispatch_and_complete(temp_store, proj.id, item.id)
    temp_store.set_loop_completion(loop.id, True, "done!")
    run = _finished_run(temp_store, loop, initiator=f"queue:{item.id}")
    run.use_worktree = True
    run.worktree_path = str(tmp_path)
    temp_store.update_run(run)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    lp = temp_store.get_agent_loop(loop.id)
    assert lp.status == LoopStatus.RUNNING  # NOT completed off a failed task
    assert lp.completion_requested is False  # claim demoted


def test_task_gate_fix_cycle_respects_budget(temp_store: GluonStore, tmp_path) -> None:
    """Repeated task-gate failures cannot outrun max_iterations."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_iterations=2)
    temp_store.cancel_pending_loop_items(loop.id)
    mgr = LoopManager(temp_store)
    for _ in range(4):
        lp = temp_store.get_agent_loop(loop.id)
        if lp.status != LoopStatus.RUNNING:
            break
        item = temp_store.enqueue_work(
            project_id=proj.id,
            prompt=f"gated failing task iter {lp.iteration_count} x",
            loop_id=loop.id,
            verify_cmd="false",
        )
        _dispatch_and_complete(temp_store, proj.id, item.id)
        run = _finished_run(temp_store, loop, initiator=f"queue:{item.id}")
        run.use_worktree = True
        run.worktree_path = str(tmp_path)
        temp_store.update_run(run)
        asyncio.run(mgr.on_run_completed(run))
    final = temp_store.get_agent_loop(loop.id)
    assert final.status == LoopStatus.PAUSED
    assert final.iteration_count <= loop.max_iterations


def test_stall_path_cascades_dead_items_first(temp_store: GluonStore) -> None:
    """Pending-but-dead items must not mask a stall (silent-deadlock edge)."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_stalls=5)
    temp_store.cancel_pending_loop_items(loop.id)
    dead_parent = temp_store.enqueue_work(project_id=proj.id, prompt="parent that fails soon long", loop_id=loop.id)
    temp_store.enqueue_work(
        project_id=proj.id, prompt="dependent of the failed parent", loop_id=loop.id, depends_on=[dead_parent.id]
    )
    wq = WorkQueueManager(temp_store)
    temp_store.claim_work(proj.id)
    wq.mark_running(dead_parent.id, "r1")
    wq.mark_failed(dead_parent.id, "boom")

    run = _finished_run(temp_store, loop)  # a completing sibling advances the loop
    asyncio.run(LoopManager(temp_store).on_run_completed(run))

    lp = temp_store.get_agent_loop(loop.id)
    assert lp.status == LoopStatus.RUNNING
    assert lp.stall_count == 1  # dead dependent was cascaded away → stall detected
    pending = [
        i for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=50) if i.loop_id == loop.id
    ]
    assert len(pending) == 1 and pending[0].source == "continuation"  # re-plan task


# ---------------------------------------------------------------------------
# A4 — survey/decompose seed
# ---------------------------------------------------------------------------


def test_seed_prompt_is_survey_and_decompose() -> None:
    seed = _SEED_PROMPT_TEMPLATE.format(objective="Fix all the bugs")
    for marker in ("SURVEY", "DECOMPOSE", "depends_on", "verify_cmd", "PARALLEL", "gh issue list"):
        assert marker in seed, f"seed prompt lost its {marker} instruction"


# ---------------------------------------------------------------------------
# A5 — loop lifecycle notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_loop_event_sends_decision_card(temp_store: GluonStore) -> None:
    from gluon.notifier import NotificationDispatcher

    proj = _project(temp_store)
    temp_store.create_channel_mapping(
        transport="telegram", channel_id="123", project_id=proj.id, project_name=proj.name
    )

    sent: list[str] = []

    class FakeTransport:
        async def send(self, ctx, resp):
            sent.append(resp.text)

    dispatcher = NotificationDispatcher(temp_store, transports={"telegram": FakeTransport()})
    await dispatcher.notify_loop_event(
        project_id=proj.id,
        objective="Clear all the PRs",
        status="paused",
        reason="max_iterations reached (5/5)",
        loop_id="abcdef123456",
        iteration_count=5,
        max_iterations=5,
        total_cost_usd=1.25,
    )
    assert len(sent) == 1
    card = sent[0]
    assert "PAUSED" in card and "Clear all the PRs" in card
    assert "max_iterations reached" in card
    assert "gluon loop resume abcdef123456" in card


@pytest.mark.asyncio
async def test_loop_event_subscriber_routes_to_notifier(temp_store: GluonStore) -> None:
    from gluon.events.subscribers import _make_loop_event_notifier
    from gluon.events.types import EventCategory, GluonEvent
    from gluon.notifier import NotificationDispatcher

    proj = _project(temp_store)
    temp_store.create_channel_mapping(transport="telegram", channel_id="99", project_id=proj.id, project_name=proj.name)

    sent: list[str] = []

    class FakeTransport:
        async def send(self, ctx, resp):
            sent.append(resp.text)

    notifier = NotificationDispatcher(temp_store, transports={"telegram": FakeTransport()})
    subscriber = _make_loop_event_notifier(temp_store, notifier)
    event = GluonEvent(
        type="loop.completed",
        category=EventCategory.EXECUTION,
        project_id=proj.id,
        data={
            "loop_id": "deadbeef0000",
            "objective": "Ship the feature",
            "status": "completed",
            "reason": "objective met; verify_cmd passed",
            "iteration_count": 3,
            "max_iterations": 20,
            "total_cost_usd": 0.42,
        },
    )
    await subscriber(event)
    assert len(sent) == 1
    assert "COMPLETED" in sent[0] and "Ship the feature" in sent[0]
