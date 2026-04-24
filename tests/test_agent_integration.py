"""Integration tests: Agent linkage into Orchestrator.execute / TaskRunner.submit,
monthly spend aggregation, and budget enforcement (Theme B Phase 1+4)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gluon.core import (
    AgentAmbiguousError,
    AgentNotFoundError,
    BudgetExceededError,
    Orchestrator,
)
from gluon.runner import RunnerConfig, TaskRunner
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "integration.db")


def _make_workspace_with_project(store: GluonStore, tmp_path: Path, ws_name: str = "ws"):
    ws_path = tmp_path / ws_name
    ws_path.mkdir(exist_ok=True)
    workspace = store.create_workspace(ws_name, ws_path)
    proj_path = ws_path / "proj"
    proj_path.mkdir(exist_ok=True)
    project = store.create_project("proj", proj_path, workspace_id=workspace.id)
    return workspace, project


def _make_orchestrator(store: GluonStore) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch.store = store  # type: ignore[attr-defined]
    return orch


# ========== resolve_agent ==========


def test_resolve_agent_returns_none_when_no_input_and_no_workspace(tmp_path):
    store = _make_store(tmp_path)
    orch = _make_orchestrator(store)
    assert orch.resolve_agent(None, None) is None


def test_resolve_agent_auto_selects_lone_active_agent(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "solo")
    orch = _make_orchestrator(store)

    assert orch.resolve_agent(None, ws.id) == agent.id


def test_resolve_agent_returns_none_when_multiple_active(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    store.create_agent(ws.id, "a1")
    store.create_agent(ws.id, "a2")
    orch = _make_orchestrator(store)

    # Auto-resolve refuses to guess when there's ambiguity
    assert orch.resolve_agent(None, ws.id) is None


def test_resolve_agent_skips_inactive_agents_in_auto_select(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    active = store.create_agent(ws.id, "active")
    inactive = store.create_agent(ws.id, "inactive")
    inactive.is_active = False
    store.update_agent(inactive)

    orch = _make_orchestrator(store)
    assert orch.resolve_agent(None, ws.id) == active.id


def test_resolve_agent_by_name(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "by-name")
    orch = _make_orchestrator(store)

    assert orch.resolve_agent("by-name", ws.id) == agent.id


def test_resolve_agent_by_id_prefix(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "prefix")
    orch = _make_orchestrator(store)

    prefix = agent.id[:8]
    assert orch.resolve_agent(prefix, ws.id) == agent.id


def test_resolve_agent_not_found_raises(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    orch = _make_orchestrator(store)

    with pytest.raises(AgentNotFoundError):
        orch.resolve_agent("nonexistent", ws.id)


def test_resolve_agent_ambiguous_prefix_raises(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    # Create two agents whose IDs happen to share an arbitrary prefix pattern
    a1 = store.create_agent(ws.id, "shared1")
    a2 = store.create_agent(ws.id, "shared2")

    # Manually reach in and rewrite the IDs to share a prefix — normally UUIDs
    # won't collide, but ambiguity detection should still work when they do.
    with store._get_conn() as conn:
        conn.execute("UPDATE agents SET id = ? WHERE id = ?", ("dupprefix-aaaa", a1.id))
        conn.execute("UPDATE agents SET id = ? WHERE id = ?", ("dupprefix-bbbb", a2.id))

    orch = _make_orchestrator(store)
    with pytest.raises(AgentAmbiguousError):
        orch.resolve_agent("dupprefix", ws.id)


# ========== Budget enforcement ==========


def test_enforce_agent_budget_noop_when_no_budget(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "no-cap")
    orch = _make_orchestrator(store)

    # Should not raise
    orch._enforce_agent_budget(agent.id)


def test_enforce_agent_budget_raises_when_over(tmp_path):
    store = _make_store(tmp_path)
    ws, proj = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "capped", monthly_budget_usd=10.0)

    # Create a run this month with cost over the cap
    run = store.create_run(project_id=proj.id, prompt="expensive", agent_id=agent.id)
    run.cost_usd = 12.50
    store.update_run(run)

    orch = _make_orchestrator(store)
    with pytest.raises(BudgetExceededError) as exc_info:
        orch._enforce_agent_budget(agent.id)

    assert exc_info.value.agent_name == "capped"
    assert exc_info.value.spent == 12.50
    assert exc_info.value.budget == 10.0


def test_enforce_agent_budget_passes_when_under(tmp_path):
    store = _make_store(tmp_path)
    ws, proj = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "under-cap", monthly_budget_usd=10.0)

    run = store.create_run(project_id=proj.id, prompt="cheap", agent_id=agent.id)
    run.cost_usd = 3.0
    store.update_run(run)

    orch = _make_orchestrator(store)
    # Should not raise
    orch._enforce_agent_budget(agent.id)


def test_monthly_spend_excludes_previous_month(tmp_path):
    """Spend from a prior month must not count against the current month's budget."""
    store = _make_store(tmp_path)
    ws, proj = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "fresh-month", monthly_budget_usd=5.0)

    # Back-date a run to last month
    run = store.create_run(project_id=proj.id, prompt="old", agent_id=agent.id)
    run.cost_usd = 10.0
    # Rewind created_at to ~35 days ago
    past = datetime.now(UTC) - timedelta(days=35)
    with store._get_conn() as conn:
        conn.execute("UPDATE execution_runs SET created_at = ? WHERE id = ?", (past.isoformat(), run.id))
    store.update_run(run)

    orch = _make_orchestrator(store)
    # This month's spend should be 0 → budget check passes
    orch._enforce_agent_budget(agent.id)


def test_monthly_spend_sums_multiple_runs(tmp_path):
    store = _make_store(tmp_path)
    ws, proj = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "multi-run", monthly_budget_usd=20.0)

    for cost in (2.0, 3.5, 4.5):
        r = store.create_run(project_id=proj.id, prompt=f"task ${cost}", agent_id=agent.id)
        r.cost_usd = cost
        store.update_run(r)

    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = store.get_agent_monthly_spend(agent.id, month_start)
    assert total == pytest.approx(10.0, rel=1e-6)


def test_monthly_spend_isolates_per_agent(tmp_path):
    """Spend on one agent must not leak into another's aggregate."""
    store = _make_store(tmp_path)
    ws, proj = _make_workspace_with_project(store, tmp_path)
    a1 = store.create_agent(ws.id, "a1")
    a2 = store.create_agent(ws.id, "a2")

    r1 = store.create_run(project_id=proj.id, prompt="one", agent_id=a1.id)
    r1.cost_usd = 5.0
    store.update_run(r1)

    r2 = store.create_run(project_id=proj.id, prompt="two", agent_id=a2.id)
    r2.cost_usd = 7.0
    store.update_run(r2)

    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assert store.get_agent_monthly_spend(a1.id, month_start) == pytest.approx(5.0)
    assert store.get_agent_monthly_spend(a2.id, month_start) == pytest.approx(7.0)


# ========== TaskRunner.submit with agent_id ==========


@pytest.mark.anyio
async def test_runner_submit_links_agent_id_and_enforces_budget(tmp_path):
    store = _make_store(tmp_path)
    ws, proj = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "runner-linked", monthly_budget_usd=100.0)

    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))

    # Prevent the run from actually starting — patch the async task creation
    async def _fake_run_task(run, *args, **kwargs):
        return None

    runner._run_task = _fake_run_task  # type: ignore[method-assign]

    run = await runner.submit(
        project_id=proj.id,
        prompt="hello agent",
        wait=False,
        initiator="test",
        agent_id=agent.id,
    )

    # Run should be linked to the agent
    fetched = store.get_run(run.id)
    assert fetched is not None
    assert fetched.agent_id == agent.id

    # Agent's last_active_at should be touched
    refreshed_agent = store.get_agent(agent.id)
    assert refreshed_agent is not None
    assert refreshed_agent.last_active_at is not None


@pytest.mark.anyio
async def test_runner_submit_blocks_over_budget(tmp_path):
    store = _make_store(tmp_path)
    ws, proj = _make_workspace_with_project(store, tmp_path)
    agent = store.create_agent(ws.id, "over-budget", monthly_budget_usd=1.0)

    # Seed one run at the cap
    past_run = store.create_run(project_id=proj.id, prompt="prior", agent_id=agent.id)
    past_run.cost_usd = 1.5
    store.update_run(past_run)

    runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
    runner._run_task = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(BudgetExceededError):
        await runner.submit(
            project_id=proj.id,
            prompt="should-be-blocked",
            agent_id=agent.id,
            initiator="test",
        )

    # No new run should have been created
    runs = store.list_runs(project_id=proj.id)
    assert all(r.id == past_run.id for r in runs), "submit should not create a run on budget block"
