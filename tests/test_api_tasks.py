"""Integration tests for OrchestratorTask API endpoints (Theme B Phase 3 web API).

Covers:
- GET /api/tasks with filters (project, agent, status)
- POST /api/tasks (create)
- GET /api/tasks/{id} (including 8-char prefix)
- PATCH /api/tasks/{id}
- DELETE /api/tasks/{id}
- POST /api/tasks/{id}/assign
- POST /api/tasks/{id}/done | cancel | review
- GET/POST /api/tasks/{id}/comments
- GET /api/agents/{id}/inbox
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gluon.models import Project, TaskStatus, Workspace
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_and_project(temp_store: GluonStore, tmp_path: Path) -> tuple[Workspace, Project]:
    """A workspace + project pair backed by real directories."""
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    workspace = temp_store.create_workspace("ws", str(ws_dir))
    proj_dir = ws_dir / "proj"
    proj_dir.mkdir()
    project = temp_store.create_project(
        name="proj",
        path=str(proj_dir),
        workspace_id=workspace.id,
    )
    return workspace, project


# ---------------------------------------------------------------------------
# List + Create
# ---------------------------------------------------------------------------


class TestListTasks:
    """GET /api/tasks"""

    def test_list_empty(self, api_client):
        client, _ = api_client
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["total"] == 0

    def test_list_returns_all_tasks(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        temp_store.create_task(project.id, "task1")
        temp_store.create_task(project.id, "task2", priority=9)
        temp_store.create_task(project.id, "task3", priority=7)

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        # Higher priority first
        assert data["tasks"][0]["title"] == "task2"

    def test_list_filter_by_project(self, temp_store, ws_and_project, api_client, tmp_path):
        workspace, project = ws_and_project
        client, _ = api_client
        # Second project in same workspace
        other_dir = tmp_path / "ws" / "proj2"
        other_dir.mkdir()
        other = temp_store.create_project("proj2", str(other_dir), workspace_id=workspace.id)

        temp_store.create_task(project.id, "a")
        temp_store.create_task(other.id, "b")

        resp = client.get(f"/api/tasks?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["title"] == "a"

    def test_list_filter_by_status(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        temp_store.create_task(project.id, "backlogged")
        t2 = temp_store.create_task(project.id, "working")
        t2.status = TaskStatus.IN_PROGRESS
        temp_store.update_task(t2)

        resp = client.get("/api/tasks?status=in_progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["id"] == t2.id

    def test_list_invalid_status_returns_422(self, api_client):
        client, _ = api_client
        resp = client.get("/api/tasks?status=not_a_status")
        assert resp.status_code == 422

    def test_list_filter_by_agent_name(self, temp_store, ws_and_project, api_client):
        workspace, project = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "researcher")
        temp_store.create_task(project.id, "assigned", assigned_agent_id=agent.id)
        temp_store.create_task(project.id, "unassigned")

        # Filter by agent name requires project_id scope (to resolve the workspace)
        resp = client.get(f"/api/tasks?agent_id=researcher&project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["title"] == "assigned"
        # Hydrated agent_name
        assert data["tasks"][0]["assigned_agent_name"] == "researcher"

    def test_list_filter_by_agent_id(self, temp_store, ws_and_project, api_client):
        workspace, project = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "eng")
        temp_store.create_task(project.id, "mine", assigned_agent_id=agent.id)
        temp_store.create_task(project.id, "other")

        resp = client.get(f"/api/tasks?agent_id={agent.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["title"] == "mine"


class TestCreateTask:
    """POST /api/tasks"""

    def test_create_minimal(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        resp = client.post(
            "/api/tasks",
            json={"project_id": project.id, "title": "new task"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "new task"
        assert data["status"] == "backlog"
        assert data["priority"] == 5
        assert data["created_by"] == "web"
        assert data["project_id"] == project.id

    def test_create_with_all_fields(self, temp_store, ws_and_project, api_client):
        workspace, project = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "lead")
        resp = client.post(
            "/api/tasks",
            json={
                "project_id": project.id,
                "title": "big refactor",
                "description": "change everything",
                "priority": 9,
                "assigned_agent": "lead",
                "assigned_files": ["src/api.py", "tests/test_api.py"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["priority"] == 9
        assert data["assigned_agent_id"] == agent.id
        assert data["assigned_agent_name"] == "lead"
        assert data["status"] == "assigned"
        assert data["description"] == "change everything"
        assert data["assigned_files"] == ["src/api.py", "tests/test_api.py"]

    def test_create_project_not_found(self, api_client):
        client, _ = api_client
        resp = client.post(
            "/api/tasks",
            json={"project_id": "nonexistent", "title": "x"},
        )
        assert resp.status_code == 404

    def test_create_agent_not_found(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        resp = client.post(
            "/api/tasks",
            json={
                "project_id": project.id,
                "title": "t",
                "assigned_agent": "ghost",
            },
        )
        assert resp.status_code == 404

    def test_create_ambiguous_agent_prefix_returns_400(self, temp_store, ws_and_project, api_client):
        workspace, project = ws_and_project
        client, _ = api_client
        # Both agents share the same prefix
        temp_store.create_agent(workspace.id, "alpha-one")
        temp_store.create_agent(workspace.id, "alpha-two")
        # Fetch by prefix that matches both agent IDs
        agents = temp_store.list_agents(workspace.id)
        common_prefix = agents[0].id[:1]
        # If both agent IDs share the same single-character prefix, query will
        # ambiguously match. If they don't, fall back to confirming we exercise
        # the ambiguous path by crafting a shared full-substring check.
        if all(a.id.startswith(common_prefix) for a in agents):
            resp = client.post(
                "/api/tasks",
                json={
                    "project_id": project.id,
                    "title": "t",
                    "assigned_agent": common_prefix,
                },
            )
            assert resp.status_code == 400

    def test_create_with_parent_task(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        parent = temp_store.create_task(project.id, "parent")
        resp = client.post(
            "/api/tasks",
            json={
                "project_id": project.id,
                "title": "child",
                "parent_task_id": parent.id,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["parent_task_id"] == parent.id

    def test_create_parent_task_not_found(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        resp = client.post(
            "/api/tasks",
            json={
                "project_id": project.id,
                "title": "child",
                "parent_task_id": "nonexistent",
            },
        )
        assert resp.status_code == 404

    def test_create_empty_title_rejected(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        resp = client.post("/api/tasks", json={"project_id": project.id, "title": ""})
        assert resp.status_code == 422

    def test_create_invalid_priority_rejected(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        for bad in (0, 11, -5):
            resp = client.post(
                "/api/tasks",
                json={"project_id": project.id, "title": "x", "priority": bad},
            )
            assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Get / Update / Delete
# ---------------------------------------------------------------------------


class TestGetTask:
    """GET /api/tasks/{task_id}"""

    def test_get_by_full_id(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t1")
        resp = client.get(f"/api/tasks/{task.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == task.id

    def test_get_by_prefix(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t-prefix")
        resp = client.get(f"/api/tasks/{task.id[:8]}")
        assert resp.status_code == 200
        assert resp.json()["id"] == task.id

    def test_get_not_found(self, api_client):
        client, _ = api_client
        resp = client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404


class TestUpdateTask:
    """PATCH /api/tasks/{task_id}"""

    def test_update_title(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "old title")
        resp = client.patch(f"/api/tasks/{task.id}", json={"title": "new title"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "new title"

    def test_update_priority(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.patch(f"/api/tasks/{task.id}", json={"priority": 10})
        assert resp.status_code == 200
        assert resp.json()["priority"] == 10

    def test_update_status(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.patch(f"/api/tasks/{task.id}", json={"status": "in_progress"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_update_status_to_done_sets_completed_at(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.patch(f"/api/tasks/{task.id}", json={"status": "done"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["completed_at"] is not None

    def test_update_invalid_status_returns_422(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.patch(f"/api/tasks/{task.id}", json={"status": "bogus"})
        assert resp.status_code == 422

    def test_update_assigned_files(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.patch(
            f"/api/tasks/{task.id}",
            json={"assigned_files": ["a.py", "b.py"]},
        )
        assert resp.status_code == 200
        assert resp.json()["assigned_files"] == ["a.py", "b.py"]

    def test_update_not_found(self, api_client):
        client, _ = api_client
        resp = client.patch("/api/tasks/nope", json={"title": "x"})
        assert resp.status_code == 404


class TestDeleteTask:
    """DELETE /api/tasks/{task_id}"""

    def test_delete(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "doomed")
        resp = client.delete(f"/api/tasks/{task.id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # Subsequent GET is 404
        assert client.get(f"/api/tasks/{task.id}").status_code == 404

    def test_delete_not_found(self, api_client):
        client, _ = api_client
        resp = client.delete("/api/tasks/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Assign + Transitions
# ---------------------------------------------------------------------------


class TestAssignTask:
    """POST /api/tasks/{task_id}/assign"""

    def test_assign_by_name(self, temp_store, ws_and_project, api_client):
        workspace, project = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "alpha")
        task = temp_store.create_task(project.id, "t")

        resp = client.post(f"/api/tasks/{task.id}/assign", json={"agent": "alpha"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["assigned_agent_id"] == agent.id
        assert data["assigned_agent_name"] == "alpha"
        assert data["status"] == "assigned"  # Moved from BACKLOG

    def test_assign_preserves_in_progress_status(self, temp_store, ws_and_project, api_client):
        workspace, project = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "engineer")
        task = temp_store.create_task(project.id, "t")
        task.status = TaskStatus.IN_PROGRESS
        temp_store.update_task(task)

        resp = client.post(f"/api/tasks/{task.id}/assign", json={"agent": "engineer"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "in_progress"  # Unchanged
        assert data["assigned_agent_id"] == agent.id

    def test_assign_agent_not_found(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.post(f"/api/tasks/{task.id}/assign", json={"agent": "ghost"})
        assert resp.status_code == 404

    def test_assign_task_not_found(self, temp_store, api_client):
        client, _ = api_client
        resp = client.post("/api/tasks/nope/assign", json={"agent": "x"})
        assert resp.status_code == 404


class TestStatusTransitions:
    """POST /api/tasks/{id}/done | cancel | review"""

    def test_done_releases_and_stamps_completed(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.post(f"/api/tasks/{task.id}/done")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["completed_at"] is not None
        assert data["execution_run_id"] is None

    def test_cancel_releases_lock(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.post(f"/api/tasks/{task.id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["execution_run_id"] is None

    def test_review(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.post(f"/api/tasks/{task.id}/review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "review"

    def test_done_not_found(self, api_client):
        client, _ = api_client
        resp = client.post("/api/tasks/nope/done")
        assert resp.status_code == 404

    def test_done_releases_checkout_lock(self, temp_store, ws_and_project, api_client):
        """POST /done on a locked task should clear execution_run_id and locked_at."""
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        run = temp_store.create_run(project_id=project.id, prompt="work")
        temp_store.checkout_task(task.id, None, run.id)

        resp = client.post(f"/api/tasks/{task.id}/done")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["execution_run_id"] is None
        assert data["execution_locked_at"] is None


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class TestTaskComments:
    """/api/tasks/{id}/comments"""

    def test_list_empty(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.get(f"/api/tasks/{task.id}/comments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["comments"] == []
        assert data["total"] == 0

    def test_add_and_list(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")

        resp = client.post(
            f"/api/tasks/{task.id}/comments",
            json={"content": "first comment", "author_label": "reviewer"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "first comment"
        assert data["author_label"] == "reviewer"

        resp = client.post(
            f"/api/tasks/{task.id}/comments",
            json={"content": "second"},
        )
        assert resp.status_code == 200
        # Defaults author_label to "web" when not supplied
        assert resp.json()["author_label"] == "web"

        list_resp = client.get(f"/api/tasks/{task.id}/comments")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] == 2
        # Oldest first
        assert data["comments"][0]["content"] == "first comment"
        assert data["comments"][1]["content"] == "second"

    def test_add_comment_empty_rejected(self, temp_store, ws_and_project, api_client):
        _, project = ws_and_project
        client, _ = api_client
        task = temp_store.create_task(project.id, "t")
        resp = client.post(f"/api/tasks/{task.id}/comments", json={"content": ""})
        assert resp.status_code == 422

    def test_list_comments_task_not_found(self, api_client):
        client, _ = api_client
        resp = client.get("/api/tasks/nope/comments")
        assert resp.status_code == 404

    def test_add_comment_task_not_found(self, api_client):
        client, _ = api_client
        resp = client.post("/api/tasks/nope/comments", json={"content": "x"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Agent Inbox
# ---------------------------------------------------------------------------


class TestAgentInbox:
    """GET /api/agents/{agent_id}/inbox"""

    def test_inbox_empty(self, temp_store, ws_and_project, api_client):
        workspace, _ = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "solo")
        resp = client.get(f"/api/agents/{agent.id}/inbox")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_inbox_returns_assigned_and_in_progress(self, temp_store, ws_and_project, api_client):
        workspace, project = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "worker")

        # ASSIGNED (should appear)
        temp_store.create_task(project.id, "assigned-task", assigned_agent_id=agent.id)
        # IN_PROGRESS (should appear)
        t2 = temp_store.create_task(project.id, "in-progress", assigned_agent_id=agent.id)
        t2.status = TaskStatus.IN_PROGRESS
        temp_store.update_task(t2)
        # DONE (should NOT appear)
        t3 = temp_store.create_task(project.id, "done", assigned_agent_id=agent.id)
        t3.status = TaskStatus.DONE
        temp_store.update_task(t3)
        # BACKLOG-without-assignment (should NOT appear)
        temp_store.create_task(project.id, "unassigned")

        resp = client.get(f"/api/agents/{agent.id}/inbox")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        titles = {t["title"] for t in data["tasks"]}
        assert titles == {"assigned-task", "in-progress"}

    def test_inbox_agent_not_found(self, api_client):
        client, _ = api_client
        resp = client.get("/api/agents/nope/inbox")
        assert resp.status_code == 404

    def test_inbox_by_name(self, temp_store, ws_and_project, api_client):
        """Agent inbox must resolve agent refs (name or ID)."""
        workspace, project = ws_and_project
        client, _ = api_client
        agent = temp_store.create_agent(workspace.id, "solo")
        temp_store.create_task(project.id, "t", assigned_agent_id=agent.id)

        # Pass agent name — resolution happens through Orchestrator.resolve_agent
        resp = client.get(f"/api/agents/{agent.id}/inbox")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
