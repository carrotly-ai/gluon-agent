"""Tests for the Agent model and store CRUD (Theme B Phase 1)."""

from pathlib import Path

import pytest

from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "agents.db")


def _make_workspace(store: GluonStore, tmp_path: Path, name: str = "ws"):
    ws_path = tmp_path / name
    ws_path.mkdir(exist_ok=True)
    return store.create_workspace(name, ws_path)


def test_create_agent_minimal(tmp_path):
    store = _make_store(tmp_path)
    ws = _make_workspace(store, tmp_path)

    agent = store.create_agent(ws.id, "researcher")

    assert agent.id
    assert agent.workspace_id == ws.id
    assert agent.name == "researcher"
    assert agent.role == "worker"
    assert agent.is_active is True
    assert agent.max_concurrent_runs == 1
    assert agent.monthly_budget_usd is None


def test_create_agent_with_all_options(tmp_path):
    store = _make_store(tmp_path)
    ws = _make_workspace(store, tmp_path)

    agent = store.create_agent(
        ws.id,
        "senior-eng",
        description="Owns the checkout service",
        role="engineer",
        monthly_budget_usd=100.0,
        max_concurrent_runs=3,
    )

    assert agent.description == "Owns the checkout service"
    assert agent.role == "engineer"
    assert agent.monthly_budget_usd == 100.0
    assert agent.max_concurrent_runs == 3


def test_agent_name_unique_per_workspace(tmp_path):
    import sqlite3

    store = _make_store(tmp_path)
    ws = _make_workspace(store, tmp_path)

    store.create_agent(ws.id, "dup")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_agent(ws.id, "dup")


def test_agent_name_can_repeat_across_workspaces(tmp_path):
    store = _make_store(tmp_path)
    ws1 = _make_workspace(store, tmp_path, "ws1")
    ws2 = _make_workspace(store, tmp_path, "ws2")

    a1 = store.create_agent(ws1.id, "shared-name")
    a2 = store.create_agent(ws2.id, "shared-name")

    assert a1.id != a2.id
    assert a1.workspace_id != a2.workspace_id


def test_get_agent_by_id_and_name(tmp_path):
    store = _make_store(tmp_path)
    ws = _make_workspace(store, tmp_path)
    agent = store.create_agent(ws.id, "finder")

    by_id = store.get_agent(agent.id)
    assert by_id is not None
    assert by_id.name == "finder"

    by_name = store.get_agent_by_name(ws.id, "finder")
    assert by_name is not None
    assert by_name.id == agent.id

    assert store.get_agent("nonexistent") is None
    assert store.get_agent_by_name(ws.id, "missing") is None


def test_list_agents_filters(tmp_path):
    store = _make_store(tmp_path)
    ws1 = _make_workspace(store, tmp_path, "ws1")
    ws2 = _make_workspace(store, tmp_path, "ws2")

    a1 = store.create_agent(ws1.id, "a1")
    a2 = store.create_agent(ws1.id, "a2")
    store.create_agent(ws2.id, "a3")

    # Deactivate one agent
    a2.is_active = False
    store.update_agent(a2)

    all_ws1 = store.list_agents(workspace_id=ws1.id)
    assert len(all_ws1) == 2

    active_ws1 = store.list_agents(workspace_id=ws1.id, is_active=True)
    assert len(active_ws1) == 1
    assert active_ws1[0].id == a1.id

    inactive_ws1 = store.list_agents(workspace_id=ws1.id, is_active=False)
    assert len(inactive_ws1) == 1
    assert inactive_ws1[0].id == a2.id

    all_agents = store.list_agents()
    assert len(all_agents) == 3


def test_update_agent(tmp_path):
    store = _make_store(tmp_path)
    ws = _make_workspace(store, tmp_path)
    agent = store.create_agent(ws.id, "updater")

    agent.description = "Now has a description"
    agent.monthly_budget_usd = 50.0
    agent.max_concurrent_runs = 2
    store.update_agent(agent)

    fresh = store.get_agent(agent.id)
    assert fresh is not None
    assert fresh.description == "Now has a description"
    assert fresh.monthly_budget_usd == 50.0
    assert fresh.max_concurrent_runs == 2
    assert fresh.updated_at >= agent.created_at


def test_delete_agent_nulls_run_fk(tmp_path):
    store = _make_store(tmp_path)
    ws = _make_workspace(store, tmp_path)
    agent = store.create_agent(ws.id, "deletable")

    # Create a project + run, link to this agent
    project_path = tmp_path / "proj"
    project_path.mkdir(exist_ok=True)
    project = store.create_project("proj", project_path)
    run = store.create_run(project_id=project.id, prompt="task")
    run.agent_id = agent.id
    store.update_run(run)

    # Verify linkage before delete
    fresh_run = store.get_run(run.id)
    assert fresh_run is not None
    assert fresh_run.agent_id == agent.id

    # Delete the agent
    deleted = store.delete_agent(agent.id)
    assert deleted is True
    assert store.get_agent(agent.id) is None

    # Run should still exist, with agent_id cleared
    post_delete_run = store.get_run(run.id)
    assert post_delete_run is not None
    assert post_delete_run.agent_id is None


def test_delete_agent_cascade_from_workspace(tmp_path):
    store = _make_store(tmp_path)
    ws = _make_workspace(store, tmp_path)
    agent = store.create_agent(ws.id, "cascade-me")

    # Deleting the workspace should cascade-delete the agent
    store.delete_workspace(ws.id)

    assert store.get_agent(agent.id) is None


def test_delete_missing_agent_returns_false(tmp_path):
    store = _make_store(tmp_path)
    assert store.delete_agent("doesnotexist") is False
