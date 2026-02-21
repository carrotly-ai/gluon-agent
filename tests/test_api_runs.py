"""Integration tests for run API endpoints.

Covers:
- Archive / unarchive
- PR status updates
- Pending questions (get, answer)
- List / get / create / cancel / resume runs
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from gluon.models import ExecutionRun, PendingQuestion, QuestionStatus, RunStatus, TodoSnapshot
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_run(
    store: GluonStore,
    project_id: str,
    *,
    prompt: str = "test task",
    status: RunStatus = RunStatus.COMPLETED,
    cost_usd: float | None = None,
) -> ExecutionRun:
    """Insert a run and optionally set its status."""
    run = store.create_run(project_id=project_id, prompt=prompt, initiator="test")
    if status != RunStatus.PENDING:
        run.status = status
        if cost_usd is not None:
            run.cost_usd = cost_usd
        store.update_run(run)
    return run


def _seed_question(
    store: GluonStore,
    run_id: str,
    *,
    question_text: str = "Pick a colour",
    status: QuestionStatus = QuestionStatus.PENDING,
) -> PendingQuestion:
    q = PendingQuestion(
        run_id=run_id,
        question_index=0,
        question_text=question_text,
        header="Colour",
        options=[
            {"label": "Red", "description": "Red colour"},
            {"label": "Blue", "description": "Blue colour"},
        ],
    )
    if status != QuestionStatus.PENDING:
        q.status = status
        q.selected_labels = ["Red"]
        q.answer_source = "user"
    store.create_pending_question(q)
    return q


# ===================================================================
# Tier 1: Store-only endpoints (real store, patched ws_manager)
# ===================================================================


class TestArchiveEndpoints:
    """POST /api/runs/{id}/archive and /api/runs/{id}/unarchive."""

    def test_archive_run(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, mock_ws = api_client

        resp = client.post(f"/api/runs/{run.id}/archive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["archived"] is True

    def test_archive_run_not_found(self, api_client):
        client, _ = api_client
        resp = client.post("/api/runs/nonexistent-id/archive")
        assert resp.status_code == 404

    def test_unarchive_run(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, mock_ws = api_client

        # Archive first
        client.post(f"/api/runs/{run.id}/archive")
        # Unarchive
        resp = client.post(f"/api/runs/{run.id}/unarchive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["archived"] is False

    def test_unarchive_run_not_found(self, api_client):
        client, _ = api_client
        resp = client.post("/api/runs/nonexistent-id/unarchive")
        assert resp.status_code == 404

    def test_archive_broadcasts_ws_update(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, mock_ws = api_client

        client.post(f"/api/runs/{run.id}/archive")
        mock_ws.broadcast_run_update.assert_called()

    def test_unarchive_broadcasts_ws_update(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, mock_ws = api_client

        client.post(f"/api/runs/{run.id}/archive")
        mock_ws.reset_mock()
        client.post(f"/api/runs/{run.id}/unarchive")
        mock_ws.broadcast_run_update.assert_called()


class TestPRStatusEndpoint:
    """POST /api/runs/{id}/pr-status."""

    def test_update_pr_status_merged(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.post(f"/api/runs/{run.id}/pr-status?pr_status=merged")
        assert resp.status_code == 200
        assert resp.json()["pr_status"] == "merged"

    def test_update_pr_status_all_valid(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        client, _ = api_client
        for status in ("open", "merged", "closed", "draft"):
            run = _seed_run(temp_store, project.id, prompt=f"run-{status}")
            resp = client.post(f"/api/runs/{run.id}/pr-status?pr_status={status}")
            assert resp.status_code == 200
            assert resp.json()["pr_status"] == status

    def test_update_pr_status_invalid(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.post(f"/api/runs/{run.id}/pr-status?pr_status=invalid")
        assert resp.status_code == 400

    def test_update_pr_status_not_found(self, api_client):
        client, _ = api_client
        resp = client.post("/api/runs/nonexistent-id/pr-status?pr_status=merged")
        assert resp.status_code == 404


class TestQuestionsEndpoints:
    """GET /api/runs/{id}/questions and POST /api/questions/{id}/answer."""

    def test_get_questions_empty(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.get(f"/api/runs/{run.id}/questions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["questions"] == []
        assert data["has_pending"] is False

    def test_get_questions_with_pending(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        _seed_question(temp_store, run.id)
        client, _ = api_client

        resp = client.get(f"/api/runs/{run.id}/questions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["questions"]) == 1
        assert data["has_pending"] is True
        assert data["questions"][0]["status"] == "pending"

    def test_get_questions_not_found(self, api_client):
        client, _ = api_client
        resp = client.get("/api/runs/nonexistent-id/questions")
        assert resp.status_code == 404

    def test_answer_question(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        q = _seed_question(temp_store, run.id)
        client, mock_ws = api_client

        resp = client.post(
            f"/api/questions/{q.id}/answer",
            json={"selected_labels": ["Red"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "answered"
        assert data["selected_labels"] == ["Red"]

    def test_answer_question_not_found(self, api_client):
        client, _ = api_client
        resp = client.post(
            "/api/questions/nonexistent-id/answer",
            json={"selected_labels": ["Red"]},
        )
        assert resp.status_code == 404

    def test_answer_already_answered(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        q = _seed_question(temp_store, run.id, status=QuestionStatus.ANSWERED)
        client, _ = api_client

        resp = client.post(
            f"/api/questions/{q.id}/answer",
            json={"selected_labels": ["Blue"]},
        )
        assert resp.status_code == 400

    def test_answer_empty_labels(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        q = _seed_question(temp_store, run.id)
        client, _ = api_client

        resp = client.post(
            f"/api/questions/{q.id}/answer",
            json={"selected_labels": []},
        )
        assert resp.status_code == 400


# ===================================================================
# Tier 2: Runner-dependent endpoints (mocked runner + orchestrator)
# ===================================================================


class TestListRuns:
    """GET /api/runs."""

    def test_list_runs_empty(self, api_client_with_mocks):
        client, mock_runner, _ = api_client_with_mocks

        resp = client.get("/api/runs")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_runner.refresh_all_runs.assert_called_once()

    def test_list_runs_returns_runs(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, prompt="task A")
        _seed_run(temp_store, project.id, prompt="task B")
        client, mock_runner, _ = api_client_with_mocks

        resp = client.get("/api/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_list_runs_filter_by_project(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, prompt="yes")
        client, _, _ = api_client_with_mocks

        resp = client.get(f"/api/runs?project_id={project.id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_runs_filter_by_status(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
        _seed_run(temp_store, project.id, status=RunStatus.FAILED)
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/runs?status=completed")
        assert resp.status_code == 200
        assert all(r["status"] == "completed" for r in resp.json())

    def test_list_runs_invalid_status(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/runs?status=invalid")
        assert resp.status_code == 400

    def test_list_runs_respects_limit(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        for i in range(10):
            _seed_run(temp_store, project.id, prompt=f"task {i}")
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/runs?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) <= 5


class TestGetRun:
    """GET /api/runs/{id}."""

    def test_get_run_detail(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _, _ = api_client_with_mocks

        resp = client.get(f"/api/runs/{run.id}?refresh_pr=false")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == run.id
        assert data["prompt"] == "test task"

    def test_get_run_by_short_id(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        short_id = run.id[:8]
        client, _, _ = api_client_with_mocks

        resp = client.get(f"/api/runs/{short_id}?refresh_pr=false")
        assert resp.status_code == 200
        assert resp.json()["id"] == run.id

    def test_get_run_not_found(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/runs/nonexistent-run-id?refresh_pr=false")
        assert resp.status_code == 404


class TestCreateRun:
    """POST /api/runs."""

    def test_create_run(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        client, mock_runner, mock_ws = api_client_with_mocks

        # Mock runner.submit to return a real run from the store
        created_run = _seed_run(temp_store, project.id, prompt="new task")
        mock_runner.submit = AsyncMock(return_value=created_run)

        resp = client.post(
            "/api/runs",
            json={"project_name": "test-project", "prompt": "new task"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["prompt"] == "new task"
        mock_ws.broadcast_run_created.assert_called()

    def test_create_run_project_not_found(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.post(
            "/api/runs",
            json={"project_name": "nonexistent", "prompt": "go"},
        )
        assert resp.status_code == 404

    def test_create_run_missing_fields(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.post("/api/runs", json={})
        assert resp.status_code == 422

    def test_create_run_broadcasts_ws(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        client, mock_runner, mock_ws = api_client_with_mocks
        created_run = _seed_run(temp_store, project.id, prompt="ws test")
        mock_runner.submit = AsyncMock(return_value=created_run)

        client.post(
            "/api/runs",
            json={"project_name": "test-project", "prompt": "ws test"},
        )
        mock_ws.broadcast_run_created.assert_called_once()


class TestCancelRun:
    """POST /api/runs/{id}/cancel."""

    def test_cancel_active_run(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, mock_runner, mock_ws = api_client_with_mocks

        # Mock cancel to update the store (mimic real behaviour)
        async def _mock_cancel(run_id):
            r = temp_store.get_run(run_id)
            if r:
                r.status = RunStatus.CANCELLED
                temp_store.update_run(r)
            return True

        mock_runner.cancel = AsyncMock(side_effect=_mock_cancel)

        resp = client.post(f"/api/runs/{run.id}/cancel")
        assert resp.status_code == 200
        mock_runner.cancel.assert_called()

    def test_cancel_non_active_run(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
        client, _, _ = api_client_with_mocks

        resp = client.post(f"/api/runs/{run.id}/cancel")
        assert resp.status_code == 400

    def test_cancel_run_not_found(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.post("/api/runs/nonexistent-id/cancel")
        assert resp.status_code == 404


class TestResumeRun:
    """POST /api/runs/{id}/resume."""

    def test_resume_run(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
        run.claude_session_id = "session-abc"
        temp_store.update_run(run)
        client, mock_runner, mock_ws = api_client_with_mocks

        # Mock resume_in_place to return the updated run
        run.status = RunStatus.RUNNING
        run.resume_count = 1
        mock_runner.resume_in_place = AsyncMock(return_value=run)

        resp = client.post(
            f"/api/runs/{run.id}/resume",
            json={"prompt": "continue the work"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run.id

    def test_resume_run_not_found(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.post(
            "/api/runs/nonexistent-id/resume",
            json={"prompt": "go"},
        )
        assert resp.status_code == 404

    def test_resume_run_validation_error(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
        client, mock_runner, _ = api_client_with_mocks

        mock_runner.resume_in_place = AsyncMock(side_effect=ValueError("Cannot resume: no session"))

        resp = client.post(
            f"/api/runs/{run.id}/resume",
            json={"prompt": "go"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Todo Tracking
# ---------------------------------------------------------------------------


class TestTodosEndpoint:
    """Tests for GET /api/runs/{run_id}/todos."""

    def test_get_todos_empty(self, temp_store, project_with_path, api_client):
        """Should return empty response when no snapshots exist."""
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.get(f"/api/runs/{run.id}/todos")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run.id
        assert data["todos"] == []
        assert data["todo_count"] == 0
        assert data["captured_at"] is None

    def test_get_todos_with_data(self, temp_store, project_with_path, api_client):
        """Should return latest snapshot data when snapshots exist."""
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        todos = [
            {"content": "Fix bug", "status": "completed", "activeForm": "Fixing bug"},
            {"content": "Add tests", "status": "in_progress", "activeForm": "Adding tests"},
            {"content": "Update docs", "status": "pending", "activeForm": "Updating docs"},
        ]
        snapshot = TodoSnapshot.from_tool_input(run.id, todos)
        temp_store.save_todo_snapshot(snapshot)

        resp = client.get(f"/api/runs/{run.id}/todos")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run.id
        assert len(data["todos"]) == 3
        assert data["todo_count"] == 3
        assert data["completed_count"] == 1
        assert data["in_progress_count"] == 1
        assert data["pending_count"] == 1
        assert data["captured_at"] is not None
        # Verify individual items
        assert data["todos"][0]["content"] == "Fix bug"
        assert data["todos"][0]["status"] == "completed"
        assert data["todos"][0]["active_form"] == "Fixing bug"

    def test_get_todos_nonexistent_run(self, temp_store, api_client):
        """Should return 404 for nonexistent run."""
        client, _ = api_client
        resp = client.get("/api/runs/nonexistent-id/todos")
        assert resp.status_code == 404
