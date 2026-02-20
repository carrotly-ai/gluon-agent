"""Integration tests for the follow-up message queue endpoints.

Covers:
- POST /api/runs/{id}/queue-followup
- PUT  /api/runs/{id}/queue/{msg_id}
- DELETE /api/runs/{id}/queue/{msg_id}
- DELETE /api/runs/{id}/queue  (clear all)
"""

from __future__ import annotations

from gluon.models import RunStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_run(store, project_id, *, status=RunStatus.RUNNING):
    run = store.create_run(project_id=project_id, prompt="task", initiator="test")
    if status != RunStatus.PENDING:
        run.status = status
        store.update_run(run)
    return run


# ===================================================================
# Queue Follow-up
# ===================================================================


class TestQueueFollowup:
    """POST /api/runs/{id}/queue-followup."""

    def test_queue_followup_running(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, mock_ws = api_client

        resp = client.post(
            f"/api/runs/{run.id}/queue-followup",
            json={"message": "also fix the tests"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "queued"
        assert data["message"] == "also fix the tests"
        assert data["message_id"] is not None

    def test_queue_followup_not_found(self, api_client):
        client, _ = api_client
        resp = client.post(
            "/api/runs/nonexistent-id/queue-followup",
            json={"message": "hello"},
        )
        assert resp.status_code == 404

    def test_queue_followup_persisted(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        client.post(
            f"/api/runs/{run.id}/queue-followup",
            json={"message": "msg 1"},
        )

        # Verify in store
        refreshed = temp_store.get_run(run.id)
        assert refreshed is not None
        assert len(refreshed.queued_messages) == 1
        assert refreshed.queued_messages[0].message == "msg 1"

    def test_queue_followup_not_running_returns_resume_now(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
        client, _ = api_client

        resp = client.post(
            f"/api/runs/{run.id}/queue-followup",
            json={"message": "hello"},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "resume_now"

    def test_multiple_followups_ordered(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        client.post(f"/api/runs/{run.id}/queue-followup", json={"message": "first"})
        client.post(f"/api/runs/{run.id}/queue-followup", json={"message": "second"})
        client.post(f"/api/runs/{run.id}/queue-followup", json={"message": "third"})

        refreshed = temp_store.get_run(run.id)
        assert refreshed is not None
        msgs = [m.message for m in refreshed.queued_messages]
        assert msgs == ["first", "second", "third"]

    def test_queue_followup_broadcasts_ws(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, mock_ws = api_client

        client.post(
            f"/api/runs/{run.id}/queue-followup",
            json={"message": "ws check"},
        )
        mock_ws.broadcast_run_update.assert_called()


# ===================================================================
# Edit Queued Message
# ===================================================================


class TestEditQueuedMessage:
    """PUT /api/runs/{id}/queue/{msg_id}."""

    def test_edit_queued_message(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        # Queue a message first
        resp = client.post(
            f"/api/runs/{run.id}/queue-followup",
            json={"message": "original"},
        )
        msg_id = resp.json()["message_id"]

        # Edit it
        resp = client.put(
            f"/api/runs/{run.id}/queue/{msg_id}",
            json={"message": "updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "updated"

    def test_edit_queued_message_not_found(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        resp = client.put(
            f"/api/runs/{run.id}/queue/nonexistent-msg",
            json={"message": "updated"},
        )
        assert resp.status_code == 404

    def test_edit_queued_message_run_not_found(self, api_client):
        client, _ = api_client
        resp = client.put(
            "/api/runs/nonexistent-id/queue/msg-id",
            json={"message": "updated"},
        )
        assert resp.status_code == 404


# ===================================================================
# Delete Queued Message
# ===================================================================


class TestDeleteQueuedMessage:
    """DELETE /api/runs/{id}/queue/{msg_id}."""

    def test_delete_queued_message(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        resp = client.post(
            f"/api/runs/{run.id}/queue-followup",
            json={"message": "to delete"},
        )
        msg_id = resp.json()["message_id"]

        resp = client.delete(f"/api/runs/{run.id}/queue/{msg_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify gone
        refreshed = temp_store.get_run(run.id)
        assert refreshed is not None
        assert len(refreshed.queued_messages) == 0

    def test_delete_queued_message_not_found(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        resp = client.delete(f"/api/runs/{run.id}/queue/nonexistent-msg")
        assert resp.status_code == 404

    def test_delete_queued_message_run_not_found(self, api_client):
        client, _ = api_client
        resp = client.delete("/api/runs/nonexistent-id/queue/msg-id")
        assert resp.status_code == 404


# ===================================================================
# Clear Queue
# ===================================================================


class TestClearQueue:
    """DELETE /api/runs/{id}/queue."""

    def test_clear_queue(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        # Queue multiple messages
        client.post(f"/api/runs/{run.id}/queue-followup", json={"message": "a"})
        client.post(f"/api/runs/{run.id}/queue-followup", json={"message": "b"})

        resp = client.delete(f"/api/runs/{run.id}/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cleared"] is True
        assert data["count"] == 2

        # Verify all gone
        refreshed = temp_store.get_run(run.id)
        assert refreshed is not None
        assert len(refreshed.queued_messages) == 0

    def test_clear_empty_queue(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        client, _ = api_client

        resp = client.delete(f"/api/runs/{run.id}/queue")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_clear_queue_run_not_found(self, api_client):
        client, _ = api_client
        resp = client.delete("/api/runs/nonexistent-id/queue")
        assert resp.status_code == 404
