"""Integration tests for notification API endpoints.

Covers:
- GET /api/notifications
- GET /api/notifications?unread_only=true
- POST /api/notifications/{id}/read
- POST /api/notifications/read-all
"""

from __future__ import annotations

from gluon.models import Notification, NotificationSeverity, NotificationType


class TestListNotifications:
    """GET /api/notifications endpoint tests."""

    def test_list_notifications_empty(self, api_client):
        """GET /api/notifications with no notifications returns empty list."""
        client, _ = api_client
        resp = client.get("/api/notifications")

        assert resp.status_code == 200
        data = resp.json()
        assert data["notifications"] == []
        assert data["unread_count"] == 0

    def test_list_notifications_returns_correct_shape(self, api_client, temp_store):
        """GET /api/notifications returns correct response shape."""
        client, _ = api_client

        # Create a notification
        notif = Notification(
            workspace_id="ws-1",
            project_id="proj-1",
            run_id="run-1",
            type=NotificationType.COMPLETION,
            severity=NotificationSeverity.SUCCESS,
            title="Run completed",
            message="Task finished successfully",
        )
        temp_store.create_notification(notif)

        resp = client.get("/api/notifications")
        assert resp.status_code == 200

        data = resp.json()
        assert "notifications" in data
        assert "unread_count" in data
        assert isinstance(data["notifications"], list)
        assert isinstance(data["unread_count"], int)

        assert len(data["notifications"]) == 1
        n = data["notifications"][0]

        # Check all response fields
        assert n["id"] == notif.id
        assert n["workspace_id"] == "ws-1"
        assert n["project_id"] == "proj-1"
        assert n["run_id"] == "run-1"
        assert n["type"] == "completion"
        assert n["severity"] == "success"
        assert n["title"] == "Run completed"
        assert n["message"] == "Task finished successfully"
        assert n["read"] is False
        assert "created_at" in n
        assert n["read_at"] is None

    def test_list_notifications_with_multiple(self, api_client, temp_store):
        """GET /api/notifications returns multiple notifications."""
        client, _ = api_client

        # Create 3 notifications
        for i in range(3):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Run {i} completed",
            )
            temp_store.create_notification(notif)

        resp = client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["notifications"]) == 3
        assert data["unread_count"] == 3

    def test_list_notifications_respects_limit(self, api_client, temp_store):
        """GET /api/notifications respects limit parameter."""
        client, _ = api_client

        # Create 10 notifications
        for i in range(10):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Run {i}",
            )
            temp_store.create_notification(notif)

        # Default limit is 50, should return all 10
        resp = client.get("/api/notifications")
        assert len(resp.json()["notifications"]) == 10

        # With limit=5
        resp = client.get("/api/notifications?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notifications"]) == 5

    def test_list_notifications_filters_by_workspace(self, api_client, temp_store):
        """GET /api/notifications filters by workspace_id."""
        client, _ = api_client

        # Create notifications for different workspaces
        for ws_id in ["ws-1", "ws-2"]:
            for i in range(2):
                notif = Notification(
                    workspace_id=ws_id,
                    project_id="proj-1",
                    run_id=f"run-{ws_id}-{i}",
                    type=NotificationType.COMPLETION,
                    severity=NotificationSeverity.SUCCESS,
                    title=f"Notif {ws_id}-{i}",
                )
                temp_store.create_notification(notif)

        # Query ws-1 only
        resp = client.get("/api/notifications?workspace_id=ws-1")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["notifications"]) == 2
        assert all(n["workspace_id"] == "ws-1" for n in data["notifications"])


class TestListNotificationsUnreadOnly:
    """GET /api/notifications?unread_only=true endpoint tests."""

    def test_unread_only_filters_to_unread(self, api_client, temp_store):
        """GET /api/notifications?unread_only=true returns only unread."""
        client, _ = api_client

        # Create 2 unread and 2 read notifications
        for i in range(2):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-unread-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Unread {i}",
                read=False,
            )
            temp_store.create_notification(notif)

        for i in range(2):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-read-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Read {i}",
                read=True,
            )
            temp_store.create_notification(notif)

        # Get all
        resp = client.get("/api/notifications")
        assert len(resp.json()["notifications"]) == 4

        # Get unread only
        resp = client.get("/api/notifications?unread_only=true")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["notifications"]) == 2
        assert all(not n["read"] for n in data["notifications"])
        assert data["unread_count"] == 2

    def test_unread_only_with_all_read(self, api_client, temp_store):
        """GET /api/notifications?unread_only=true with all read returns empty."""
        client, _ = api_client

        # Create only read notifications
        for i in range(3):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Read {i}",
                read=True,
            )
            temp_store.create_notification(notif)

        resp = client.get("/api/notifications?unread_only=true")
        assert resp.status_code == 200
        data = resp.json()

        assert data["notifications"] == []
        assert data["unread_count"] == 0

    def test_unread_only_filters_by_workspace_and_unread(self, api_client, temp_store):
        """GET /api/notifications?unread_only=true&workspace_id=X filters both."""
        client, _ = api_client

        # Create mixed notifications across workspaces
        for ws_id in ["ws-1", "ws-2"]:
            for i in range(2):
                # Unread for ws-1, read for ws-2
                is_read = ws_id == "ws-2"
                notif = Notification(
                    workspace_id=ws_id,
                    project_id="proj-1",
                    run_id=f"run-{ws_id}-{i}",
                    type=NotificationType.COMPLETION,
                    severity=NotificationSeverity.SUCCESS,
                    title=f"Notif {ws_id}-{i}",
                    read=is_read,
                )
                temp_store.create_notification(notif)

        resp = client.get("/api/notifications?unread_only=true&workspace_id=ws-1")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["notifications"]) == 2
        assert all(not n["read"] for n in data["notifications"])
        assert all(n["workspace_id"] == "ws-1" for n in data["notifications"])


class TestMarkNotificationRead:
    """POST /api/notifications/{id}/read endpoint tests."""

    def test_mark_notification_read_success(self, api_client, temp_store):
        """POST /api/notifications/{id}/read marks notification as read."""
        client, _ = api_client

        # Create an unread notification
        notif = Notification(
            workspace_id="ws-1",
            project_id="proj-1",
            run_id="run-1",
            type=NotificationType.COMPLETION,
            severity=NotificationSeverity.SUCCESS,
            title="Run completed",
            read=False,
        )
        notif = temp_store.create_notification(notif)

        # Verify it's unread
        assert not notif.read
        assert temp_store.get_unread_count() == 1

        # Mark as read
        resp = client.post(f"/api/notifications/{notif.id}/read")
        assert resp.status_code == 200

        # Verify response
        data = resp.json()
        assert data["id"] == notif.id
        assert data["read"] is True
        assert data["read_at"] is not None

        # Verify in store
        updated = temp_store.mark_notification_read(notif.id)
        assert updated is not None
        assert updated.read is True

    def test_mark_notification_read_nonexistent_id(self, api_client):
        """POST /api/notifications/{id}/read with nonexistent ID returns 404."""
        client, _ = api_client

        resp = client.post("/api/notifications/nonexistent-id/read")
        assert resp.status_code == 404

        data = resp.json()
        assert "detail" in data or "error" in data

    def test_mark_notification_read_idempotent(self, api_client, temp_store):
        """POST /api/notifications/{id}/read can be called multiple times safely."""
        client, _ = api_client

        # Create notification
        notif = Notification(
            workspace_id="ws-1",
            project_id="proj-1",
            run_id="run-1",
            type=NotificationType.COMPLETION,
            severity=NotificationSeverity.SUCCESS,
            title="Run completed",
            read=False,
        )
        notif = temp_store.create_notification(notif)

        # Mark as read twice
        resp1 = client.post(f"/api/notifications/{notif.id}/read")
        assert resp1.status_code == 200
        assert resp1.json()["read"] is True

        resp2 = client.post(f"/api/notifications/{notif.id}/read")
        assert resp2.status_code == 200
        assert resp2.json()["read"] is True

        # Verify only one is in DB
        all_notifs = temp_store.list_notifications()
        assert len(all_notifs) == 1

    def test_mark_notification_read_response_fields(self, api_client, temp_store):
        """POST /api/notifications/{id}/read returns all notification fields."""
        client, _ = api_client

        # Create notification with all fields
        notif = Notification(
            workspace_id="ws-1",
            project_id="proj-1",
            run_id="run-1",
            session_id="session-1",
            type=NotificationType.QUESTION,
            severity=NotificationSeverity.WARNING,
            title="Input required",
            message="Please provide input",
            metadata={"question_ids": ["q-1", "q-2"]},
            read=False,
        )
        notif = temp_store.create_notification(notif)

        resp = client.post(f"/api/notifications/{notif.id}/read")
        assert resp.status_code == 200

        data = resp.json()
        assert data["id"] == notif.id
        assert data["workspace_id"] == "ws-1"
        assert data["project_id"] == "proj-1"
        assert data["run_id"] == "run-1"
        assert data["session_id"] == "session-1"
        assert data["type"] == "question"
        assert data["severity"] == "warning"
        assert data["title"] == "Input required"
        assert data["message"] == "Please provide input"
        assert data["metadata"] == {"question_ids": ["q-1", "q-2"]}
        assert data["read"] is True
        assert data["read_at"] is not None


class TestMarkAllNotificationsRead:
    """POST /api/notifications/read-all endpoint tests."""

    def test_mark_all_read_success(self, api_client, temp_store):
        """POST /api/notifications/read-all marks all as read."""
        client, _ = api_client

        # Create 3 unread notifications
        for i in range(3):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Run {i}",
                read=False,
            )
            temp_store.create_notification(notif)

        assert temp_store.get_unread_count() == 3

        # Mark all as read
        resp = client.post("/api/notifications/read-all")
        assert resp.status_code == 200

        # Verify response
        data = resp.json()
        assert data["marked_read"] == 3

        # Verify in store
        assert temp_store.get_unread_count() == 0

    def test_mark_all_read_with_no_unread(self, api_client, temp_store):
        """POST /api/notifications/read-all with all already read returns 0."""
        client, _ = api_client

        # Create 2 read notifications
        for i in range(2):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Run {i}",
                read=True,
            )
            temp_store.create_notification(notif)

        resp = client.post("/api/notifications/read-all")
        assert resp.status_code == 200

        data = resp.json()
        assert data["marked_read"] == 0

    def test_mark_all_read_with_empty(self, api_client):
        """POST /api/notifications/read-all with no notifications returns 0."""
        client, _ = api_client

        resp = client.post("/api/notifications/read-all")
        assert resp.status_code == 200

        data = resp.json()
        assert data["marked_read"] == 0

    def test_mark_all_read_partial(self, api_client, temp_store):
        """POST /api/notifications/read-all marks only unread as read."""
        client, _ = api_client

        # Create mix of read and unread
        for i in range(2):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-unread-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Unread {i}",
                read=False,
            )
            temp_store.create_notification(notif)

        for i in range(3):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-read-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Read {i}",
                read=True,
            )
            temp_store.create_notification(notif)

        assert temp_store.get_unread_count() == 2

        resp = client.post("/api/notifications/read-all")
        assert resp.status_code == 200

        data = resp.json()
        assert data["marked_read"] == 2

        # Verify all are now read
        assert temp_store.get_unread_count() == 0

    def test_mark_all_read_by_workspace(self, api_client, temp_store):
        """POST /api/notifications/read-all?workspace_id=X marks only workspace as read."""
        client, _ = api_client

        # Create notifications for different workspaces
        for ws_id in ["ws-1", "ws-2"]:
            for i in range(2):
                notif = Notification(
                    workspace_id=ws_id,
                    project_id="proj-1",
                    run_id=f"run-{ws_id}-{i}",
                    type=NotificationType.COMPLETION,
                    severity=NotificationSeverity.SUCCESS,
                    title=f"Notif {ws_id}-{i}",
                    read=False,
                )
                temp_store.create_notification(notif)

        assert temp_store.get_unread_count() == 4
        assert temp_store.get_unread_count(workspace_id="ws-1") == 2
        assert temp_store.get_unread_count(workspace_id="ws-2") == 2

        # Mark all in ws-1 as read
        resp = client.post("/api/notifications/read-all?workspace_id=ws-1")
        assert resp.status_code == 200

        data = resp.json()
        assert data["marked_read"] == 2

        # Verify ws-1 has 0 unread, ws-2 still has 2
        assert temp_store.get_unread_count(workspace_id="ws-1") == 0
        assert temp_store.get_unread_count(workspace_id="ws-2") == 2

    def test_mark_all_read_verifies_state_after(self, api_client, temp_store):
        """Verify unread_count decreases after mark all read."""
        client, _ = api_client

        # Create 5 unread notifications
        for i in range(5):
            notif = Notification(
                workspace_id="ws-1",
                project_id="proj-1",
                run_id=f"run-{i}",
                type=NotificationType.COMPLETION,
                severity=NotificationSeverity.SUCCESS,
                title=f"Run {i}",
                read=False,
            )
            temp_store.create_notification(notif)

        # Verify unread count is 5
        resp_before = client.get("/api/notifications")
        assert resp_before.json()["unread_count"] == 5

        # Mark all as read
        client.post("/api/notifications/read-all")

        # Verify unread count is now 0
        resp_after = client.get("/api/notifications")
        assert resp_after.json()["unread_count"] == 0

        # Verify all notifications are marked read
        resp_list = client.get("/api/notifications")
        for notif in resp_list.json()["notifications"]:
            assert notif["read"] is True
