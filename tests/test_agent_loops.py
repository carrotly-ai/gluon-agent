"""Tests for agent loops (loop-engineering Phase 2 — docs/design/agent-loops.md).

Covers: the AgentLoop model + store CRUD, loop-aware queue behavior
(claim filtering, dedup, cancel-pending), LoopManager advancement (completion
handshake / gate authority / budgets / stall detection), the gluon-loop MCP
tool impls, the /api/loops routes, and non-regression for ordinary queue items
and runs (all loop fields default to None → unchanged behavior).
"""

from __future__ import annotations

import asyncio

import pytest

from gluon.loop_manager import LoopManager
from gluon.loop_tools import _complete_impl, _enqueue_task_impl, _status_impl
from gluon.models import (
    AgentLoop,
    LoopStatus,
    RunStatus,
    WorkQueueStatus,
    normalize_prompt_hash,
)
from gluon.store import GluonStore


def _project(store: GluonStore, name: str = "p"):
    ws = store.create_workspace(f"w-{name}", f"/tmp/w-{name}")
    return store.create_project(name=name, path=f"/tmp/w-{name}/{name}", workspace_id=ws.id)


def _make_loop(store: GluonStore, project_id: str, **kwargs) -> AgentLoop:
    return LoopManager(store).create_loop(project_id=project_id, objective="Build the widget", **kwargs)


def _tool_text(result: dict) -> str:
    return str(result["content"][0]["text"])


# ========== model + store CRUD ==========


def test_agent_loop_round_trip(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = AgentLoop(
        project_id=proj.id,
        objective="Ship the feature",
        verify_cmd="uv run pytest",
        max_iterations=5,
        max_cost_usd=12.5,
    )
    temp_store.create_agent_loop(loop)

    fetched = temp_store.get_agent_loop(loop.id)
    assert fetched is not None
    assert fetched.objective == "Ship the feature"
    assert fetched.verify_cmd == "uv run pytest"
    assert fetched.status == LoopStatus.RUNNING
    assert fetched.max_iterations == 5
    assert fetched.max_cost_usd == 12.5

    fetched.status = LoopStatus.PAUSED
    fetched.status_reason = "testing"
    fetched.iteration_count = 3
    fetched.total_cost_usd = 1.25
    temp_store.update_agent_loop(fetched)
    again = temp_store.get_agent_loop(loop.id)
    assert again is not None
    assert again.status == LoopStatus.PAUSED
    assert again.status_reason == "testing"
    assert again.iteration_count == 3
    assert again.total_cost_usd == 1.25


def test_list_agent_loops_filters(temp_store: GluonStore) -> None:
    proj_a = _project(temp_store, "a")
    proj_b = _project(temp_store, "b")
    loop_a = _make_loop(temp_store, proj_a.id)
    _make_loop(temp_store, proj_b.id)

    assert len(temp_store.list_agent_loops()) == 2
    assert [lp.id for lp in temp_store.list_agent_loops(project_id=proj_a.id)] == [loop_a.id]
    LoopManager(temp_store).pause_loop(loop_a.id, "test")
    assert [lp.id for lp in temp_store.list_agent_loops(status="paused")] == [loop_a.id]


def test_budget_exhausted_helper() -> None:
    loop = AgentLoop(project_id="p", objective="o", max_iterations=2)
    assert loop.budget_exhausted() is None
    loop.iteration_count = 2
    assert "max_iterations" in (loop.budget_exhausted() or "")
    loop.iteration_count = 0
    loop.max_cost_usd = 1.0
    loop.total_cost_usd = 1.5
    assert "cost budget" in (loop.budget_exhausted() or "")


def test_run_loop_id_round_trip(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    run = temp_store.create_run(project_id=proj.id, prompt="t", loop_id="loop123")
    fetched = temp_store.get_run(run.id)
    assert fetched is not None
    assert fetched.loop_id == "loop123"


def test_enqueue_work_loop_fields_round_trip(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    item = temp_store.enqueue_work(
        project_id=proj.id,
        prompt="do the thing",
        loop_id="loopX",
        source="agent",
        prompt_hash=normalize_prompt_hash("do the thing"),
    )
    items = temp_store.list_work_items(project_id=proj.id)
    fetched = next(i for i in items if i.id == item.id)
    assert fetched.loop_id == "loopX"
    assert fetched.source == "agent"
    assert fetched.prompt_hash == normalize_prompt_hash("do the thing")


def test_non_loop_queue_items_unchanged(temp_store: GluonStore) -> None:
    """Non-regression: ordinary enqueue/claim behavior is untouched."""
    proj = _project(temp_store)
    item = temp_store.enqueue_work(project_id=proj.id, prompt="plain task")
    assert item.loop_id is None
    assert item.source is None
    claimed = temp_store.claim_work(proj.id)
    assert claimed is not None
    assert claimed.id == item.id
    assert claimed.status == WorkQueueStatus.CLAIMED


def test_claim_work_skips_non_running_loops(temp_store: GluonStore) -> None:
    """A paused loop's tasks are inert but preserved."""
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)  # seeds one PENDING item
    LoopManager(temp_store).pause_loop(loop.id, "test pause")

    assert temp_store.claim_work(proj.id) is None  # inert
    assert temp_store.count_pending_loop_items(loop.id) == 1  # preserved

    LoopManager(temp_store).resume_loop(loop.id)
    claimed = temp_store.claim_work(proj.id)
    assert claimed is not None
    assert claimed.loop_id == loop.id


def test_cancel_pending_loop_items(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)  # 1 seed
    temp_store.enqueue_work(project_id=proj.id, prompt="extra", loop_id=loop.id, source="agent")
    other = temp_store.enqueue_work(project_id=proj.id, prompt="not in loop")

    assert temp_store.cancel_pending_loop_items(loop.id) == 2
    assert temp_store.count_pending_loop_items(loop.id) == 0
    # Non-loop item untouched
    items = temp_store.list_work_items(project_id=proj.id, status="pending")
    assert [i.id for i in items] == [other.id]


# ========== LoopManager ==========


def test_create_loop_seeds_iteration_one(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="true")
    items = temp_store.list_work_items(project_id=proj.id, status="pending")
    assert len(items) == 1
    seed = items[0]
    assert seed.loop_id == loop.id
    assert seed.source == "seed"
    assert "Build the widget" in seed.prompt
    assert "iteration 1" in seed.prompt


def _finished_run(temp_store: GluonStore, loop: AgentLoop, status: RunStatus = RunStatus.COMPLETED, cost: float = 0.5):
    run = temp_store.create_run(project_id=loop.project_id, prompt="iter", loop_id=loop.id)
    run.status = status
    run.cost_usd = cost
    if status == RunStatus.FAILED:
        run.error_message = "boom"
    temp_store.update_run(run)
    return run


def test_on_run_completed_failed_run_pauses_loop(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    run = _finished_run(temp_store, loop, RunStatus.FAILED)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.status == LoopStatus.PAUSED
    assert "failed" in (updated.status_reason or "")
    assert updated.iteration_count == 1
    assert updated.total_cost_usd == pytest.approx(0.5)


def test_on_run_completed_gateless_completion(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)  # gateless
    loop.completion_requested = True
    loop.completion_summary = "done"
    temp_store.update_agent_loop(loop)
    run = _finished_run(temp_store, loop)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.status == LoopStatus.COMPLETED
    assert updated.completed_at is not None
    # Seed task cancelled on completion
    assert temp_store.count_pending_loop_items(loop.id) == 0


def test_on_run_completed_gate_pass_completes(temp_store: GluonStore, tmp_path) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="true")
    loop.completion_requested = True
    temp_store.update_agent_loop(loop)
    run = _finished_run(temp_store, loop)
    run.use_worktree = True
    run.worktree_path = str(tmp_path)  # gate cwd
    temp_store.update_run(run)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.status == LoopStatus.COMPLETED
    assert "verify_cmd passed" in (updated.status_reason or "")


def test_on_run_completed_gate_fail_denies_completion(temp_store: GluonStore, tmp_path) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="false")
    loop.completion_requested = True
    loop.completion_summary = "premature"
    temp_store.update_agent_loop(loop)
    # Clear the seed so the continuation is identifiable
    temp_store.cancel_pending_loop_items(loop.id)
    run = _finished_run(temp_store, loop)
    run.use_worktree = True
    run.worktree_path = str(tmp_path)
    temp_store.update_run(run)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    # Denied: still running, request demoted, gate feedback in a continuation
    assert updated.status == LoopStatus.RUNNING
    assert updated.completion_requested is False
    assert updated.completion_summary is None
    pending = [
        i for i in temp_store.list_work_items(project_id=proj.id, status="pending", limit=50) if i.loop_id == loop.id
    ]
    assert len(pending) == 1
    assert pending[0].source == "continuation"
    assert "DENIED" in pending[0].prompt
    assert "false" in pending[0].prompt  # the verify_cmd is named in the feedback


def test_on_run_completed_budget_pause(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_iterations=1)
    run = _finished_run(temp_store, loop)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.status == LoopStatus.PAUSED
    assert "max_iterations" in (updated.status_reason or "")


def test_on_run_completed_stall_then_pause(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_stalls=1)
    manager = LoopManager(temp_store)

    # Stall 1: nothing pending (drop the seed), no completion → continuation
    temp_store.cancel_pending_loop_items(loop.id)
    run1 = _finished_run(temp_store, loop)
    asyncio.run(manager.on_run_completed(run1))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.status == LoopStatus.RUNNING
    assert updated.stall_count == 1
    assert temp_store.count_pending_loop_items(loop.id) == 1  # the continuation

    # Stall 2 (> max_stalls=1): pause
    temp_store.cancel_pending_loop_items(loop.id)
    run2 = _finished_run(temp_store, loop)
    asyncio.run(manager.on_run_completed(run2))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.status == LoopStatus.PAUSED
    assert "stalled" in (updated.status_reason or "")


def test_on_run_completed_pending_work_resets_stalls(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    loop.stall_count = 1
    temp_store.update_agent_loop(loop)
    # Seed is still pending → progress
    run = _finished_run(temp_store, loop)

    asyncio.run(LoopManager(temp_store).on_run_completed(run))
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.status == LoopStatus.RUNNING
    assert updated.stall_count == 0


def test_resume_reseeds_continuation_when_empty(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    manager = LoopManager(temp_store)
    temp_store.cancel_pending_loop_items(loop.id)
    manager.pause_loop(loop.id, "test")

    resumed = manager.resume_loop(loop.id)
    assert resumed is not None
    assert resumed.status == LoopStatus.RUNNING
    assert temp_store.count_pending_loop_items(loop.id) == 1


def test_cancel_loop_drops_pending(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    cancelled = LoopManager(temp_store).cancel_loop(loop.id)
    assert cancelled is not None
    assert cancelled.status == LoopStatus.CANCELLED
    assert temp_store.count_pending_loop_items(loop.id) == 0


# ========== gluon-loop MCP tool impls ==========


def test_enqueue_tool_happy_path(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    result = asyncio.run(
        _enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": "Implement the API endpoint for widgets"})
    )
    assert "Task enqueued" in _tool_text(result)
    items = [i for i in temp_store.list_work_items(project_id=proj.id, limit=50) if i.source == "agent"]
    assert len(items) == 1
    assert items[0].loop_id == loop.id
    assert items[0].prompt_hash == normalize_prompt_hash("Implement the API endpoint for widgets")


def test_enqueue_tool_rejects_duplicates(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    prompt = "Implement the API endpoint for widgets"
    asyncio.run(_enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": prompt}))
    # Same prompt, different whitespace/case → still a duplicate
    result = asyncio.run(_enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": f"  {prompt.upper()}  "}))
    assert "duplicate" in _tool_text(result).lower()


def test_enqueue_tool_enforces_fanout_cap(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, max_fanout=2)  # seed occupies 1 slot
    r1 = asyncio.run(_enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": "First distinct follow-up task here"}))
    assert "Task enqueued" in _tool_text(r1)
    r2 = asyncio.run(_enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": "Second distinct follow-up task here"}))
    assert "fan-out cap" in _tool_text(r2)


def test_enqueue_tool_rejects_non_running_loop(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    LoopManager(temp_store).pause_loop(loop.id, "budget")
    result = asyncio.run(
        _enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": "A task for a paused loop, rejected"})
    )
    assert "paused" in _tool_text(result)


def test_enqueue_tool_rejects_short_prompt(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    result = asyncio.run(_enqueue_task_impl(temp_store, loop.id, "run1", {"prompt": "fix it"}))
    assert "too short" in _tool_text(result)


def test_complete_tool_sets_request(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="true")
    result = asyncio.run(_complete_impl(temp_store, loop.id, "run1", {"summary": "All done"}))
    assert "REQUESTED" in _tool_text(result)
    updated = temp_store.get_agent_loop(loop.id)
    assert updated is not None
    assert updated.completion_requested is True
    assert updated.completion_summary == "All done"


def test_complete_tool_requires_summary(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id)
    result = asyncio.run(_complete_impl(temp_store, loop.id, "run1", {}))
    assert "summary is required" in _tool_text(result)


def test_status_tool_reports_state(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="uv run pytest", max_cost_usd=10.0)
    text = _tool_text(asyncio.run(_status_impl(temp_store, loop.id, "run1", {})))
    assert loop.id in text
    assert "Build the widget" in text
    assert "uv run pytest" in text
    assert "seed" in text  # pending seed task listed


# ========== /api/loops routes ==========


def test_api_create_and_get_loop(api_client_with_mocks, temp_store: GluonStore) -> None:
    # Mocked runner: the create endpoint's kick_queue_drain must not dispatch,
    # so the seed stays PENDING and pending_tasks is deterministic.
    client, _mock_runner, _ = api_client_with_mocks
    proj = _project(temp_store)

    resp = client.post(
        "/api/loops",
        json={
            "project_name": proj.name,
            "objective": "Ship the widget",
            "verify_cmd": "uv run pytest",
            "max_iterations": 7,
        },
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["objective"] == "Ship the widget"
    assert body["readiness"] == "gated"
    assert body["status"] == "running"
    assert body["max_iterations"] == 7
    assert body["pending_tasks"] == 1  # the seed
    assert body["project_name"] == proj.name

    detail = client.get(f"/api/loops/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == body["id"]

    listing = client.get("/api/loops")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_api_create_loop_unknown_project(api_client_with_mocks) -> None:
    client, _mock_runner, _ = api_client_with_mocks
    resp = client.post("/api/loops", json={"project_name": "nope", "objective": "x"})
    assert resp.status_code == 404


def test_api_pause_resume_cancel(api_client_with_mocks, temp_store: GluonStore) -> None:
    client, _mock_runner, _ = api_client_with_mocks
    proj = _project(temp_store)
    loop_id = client.post("/api/loops", json={"project_name": proj.name, "objective": "obj"}).json()["id"]

    paused = client.post(f"/api/loops/{loop_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/loops/{loop_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"

    cancelled = client.post(f"/api/loops/{loop_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # Terminal loops can't be re-cancelled or resumed
    assert client.post(f"/api/loops/{loop_id}/cancel").status_code == 400
    assert client.post(f"/api/loops/{loop_id}/resume").status_code == 400

    missing = client.get("/api/loops/doesnotexist")
    assert missing.status_code == 404


def test_api_queue_response_exposes_loop_fields(api_client_with_mocks, temp_store: GluonStore) -> None:
    client, _mock_runner, _ = api_client_with_mocks
    proj = _project(temp_store)
    loop_id = client.post("/api/loops", json={"project_name": proj.name, "objective": "obj"}).json()["id"]

    resp = client.get("/api/queue")
    assert resp.status_code == 200
    items = resp.json()["items"]
    seed = next(i for i in items if i["loop_id"] == loop_id)
    assert seed["source"] == "seed"


# ========== worker-agent injection (agent.py) ==========


def test_build_options_injects_loop_tools_and_prompt(temp_store: GluonStore, tmp_path, monkeypatch) -> None:
    """A run with loop_id gets the gluon-loop MCP server, tool allowlist
    entries, and the iteration contract in the system prompt append."""
    from gluon.agent import GluonAgent
    from gluon.loop_tools import LOOP_TOOL_NAMES

    # No external MCP config → restricted allowed_tools path (deterministic
    # regardless of the host's ~/.claude/.mcp.json).
    monkeypatch.setattr("gluon.agent.find_mcp_config", lambda working_dir=None: None)

    proj = _project(temp_store)
    loop = _make_loop(temp_store, proj.id, verify_cmd="uv run pytest")
    run = temp_store.create_run(project_id=proj.id, prompt="iterate", loop_id=loop.id)

    agent = GluonAgent(store=temp_store, run_id=run.id)
    options = agent._build_options(working_dir=tmp_path)

    assert isinstance(options.mcp_servers, dict)
    assert "gluon-loop" in options.mcp_servers
    for name in LOOP_TOOL_NAMES:
        assert name in options.allowed_tools
    append = options.system_prompt["append"]
    assert "AGENT LOOP — Iteration 1" in append
    assert "Build the widget" in append
    assert "uv run pytest" in append


def test_build_options_non_loop_run_unchanged(temp_store: GluonStore, tmp_path) -> None:
    """Non-regression: runs without loop_id get no loop server or prompt."""
    from gluon.agent import GluonAgent

    proj = _project(temp_store)
    run = temp_store.create_run(project_id=proj.id, prompt="plain")

    agent = GluonAgent(store=temp_store, run_id=run.id)
    options = agent._build_options(working_dir=tmp_path)

    if isinstance(options.mcp_servers, dict):
        assert "gluon-loop" not in options.mcp_servers
    assert "AGENT LOOP" not in options.system_prompt["append"]
