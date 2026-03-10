"""Tests for SDK Session Browser endpoints.

Covers:
- GET /api/sdk-sessions (list sessions)
- GET /api/sdk-sessions/{session_id} (session detail with messages)
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from gluon.models import ExecutionRun
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Mock SDK types
# ---------------------------------------------------------------------------


@dataclass
class MockSDKSessionInfo:
    session_id: str = "sess-001"
    summary: str = "Test session"
    last_modified: int = 1710000000
    file_size: int = 4096
    custom_title: str | None = None
    first_prompt: str | None = "Hello"
    git_branch: str | None = "main"
    cwd: str | None = "/tmp/project"


@dataclass
class MockSessionMessage:
    type: str = "user"
    uuid: str = "msg-001"
    session_id: str = "sess-001"
    message: object = None
    parent_tool_use_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_run(
    store: GluonStore,
    project_id: str,
    *,
    claude_session_id: str | None = None,
) -> ExecutionRun:
    run = store.create_run(project_id=project_id, prompt="test", initiator="test")
    if claude_session_id:
        run.claude_session_id = claude_session_id
        store.update_run(run)
    return run


# ===========================================================================
# GET /api/sdk-sessions
# ===========================================================================


class TestListSDKSessions:
    def test_list_sessions_returns_data(self, temp_store, api_client):
        client, _ = api_client

        mock_sessions = [
            MockSDKSessionInfo(session_id="sess-001", summary="Session A"),
            MockSDKSessionInfo(session_id="sess-002", summary="Session B"),
        ]

        with patch("claude_agent_sdk.list_sessions", return_value=mock_sessions):
            resp = client.get("/api/sdk-sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["session_id"] == "sess-001"
        assert data[1]["session_id"] == "sess-002"
        assert data[0]["summary"] == "Session A"

    def test_list_sessions_empty(self, api_client):
        client, _ = api_client

        with patch("claude_agent_sdk.list_sessions", return_value=[]):
            resp = client.get("/api/sdk-sessions")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sessions_with_linked_runs(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, claude_session_id="sess-123")
        client, _ = api_client

        mock_sessions = [MockSDKSessionInfo(session_id="sess-123", summary="Linked")]

        with patch("claude_agent_sdk.list_sessions", return_value=mock_sessions):
            resp = client.get("/api/sdk-sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert run.id in data[0]["linked_run_ids"]

    def test_list_sessions_no_linked_runs(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, claude_session_id="sess-other")
        client, _ = api_client

        mock_sessions = [MockSDKSessionInfo(session_id="sess-999", summary="Unlinked")]
        with patch("claude_agent_sdk.list_sessions", return_value=mock_sessions):
            resp = client.get("/api/sdk-sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["linked_run_ids"] == []

    def test_list_sessions_directory_param(self, api_client):
        client, _ = api_client

        mock_list = MagicMock(return_value=[])
        with patch("claude_agent_sdk.list_sessions", mock_list):
            resp = client.get("/api/sdk-sessions?directory=/tmp/proj")

        assert resp.status_code == 200
        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs.get("directory") == "/tmp/proj"

    def test_list_sessions_limit_param(self, api_client):
        client, _ = api_client

        mock_list = MagicMock(return_value=[])
        with patch("claude_agent_sdk.list_sessions", mock_list):
            resp = client.get("/api/sdk-sessions?limit=10")

        assert resp.status_code == 200
        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs.get("limit") == 10

    def test_list_sessions_sdk_error_returns_empty(self, api_client):
        client, _ = api_client

        with patch("claude_agent_sdk.list_sessions", side_effect=Exception("SDK error")):
            resp = client.get("/api/sdk-sessions")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sessions_store_error_handled(self, temp_store, api_client):
        """Unprotected store.list_runs() should not crash the endpoint (bug fix test)."""
        client, _ = api_client

        mock_sessions = [MockSDKSessionInfo(session_id="sess-001")]

        with (
            patch("claude_agent_sdk.list_sessions", return_value=mock_sessions),
            patch.object(temp_store, "list_runs", side_effect=Exception("DB error")),
        ):
            resp = client.get("/api/sdk-sessions")

        # After the bug fix, this should return 200 with empty linked_run_ids
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["linked_run_ids"] == []


# ===========================================================================
# GET /api/sdk-sessions/{session_id}
# ===========================================================================


class TestGetSDKSession:
    def test_get_session_detail(self, api_client):
        client, _ = api_client

        mock_session = MockSDKSessionInfo(session_id="sess-001", summary="Detail test")
        mock_messages = [
            MockSessionMessage(type="user", uuid="m1", session_id="sess-001"),
            MockSessionMessage(type="assistant", uuid="m2", session_id="sess-001"),
            MockSessionMessage(type="user", uuid="m3", session_id="sess-001"),
        ]

        with (
            patch("claude_agent_sdk.list_sessions", return_value=[mock_session]),
            patch("claude_agent_sdk.get_session_messages", return_value=mock_messages),
        ):
            resp = client.get("/api/sdk-sessions/sess-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session"]["session_id"] == "sess-001"
        assert len(data["messages"]) == 3
        assert data["total_messages"] == 3

    def test_get_session_not_found(self, api_client):
        client, _ = api_client

        with patch("claude_agent_sdk.list_sessions", return_value=[MockSDKSessionInfo(session_id="sess-other")]):
            resp = client.get("/api/sdk-sessions/sess-nonexistent")

        assert resp.status_code == 404

    def test_get_session_messages_have_correct_fields(self, api_client):
        client, _ = api_client

        mock_session = MockSDKSessionInfo(session_id="sess-001")
        mock_messages = [
            MockSessionMessage(type="user", uuid="m1", session_id="sess-001", parent_tool_use_id=None),
            MockSessionMessage(type="assistant", uuid="m2", session_id="sess-001", parent_tool_use_id="tu1"),
        ]

        with (
            patch("claude_agent_sdk.list_sessions", return_value=[mock_session]),
            patch("claude_agent_sdk.get_session_messages", return_value=mock_messages),
        ):
            resp = client.get("/api/sdk-sessions/sess-001")

        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert msgs[0]["type"] == "user"
        assert msgs[0]["uuid"] == "m1"
        assert msgs[0]["session_id"] == "sess-001"
        assert msgs[0]["parent_tool_use_id"] is None
        assert msgs[1]["parent_tool_use_id"] == "tu1"

    def test_get_session_with_linked_runs(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, claude_session_id="sess-001")
        client, _ = api_client

        mock_session = MockSDKSessionInfo(session_id="sess-001")
        with (
            patch("claude_agent_sdk.list_sessions", return_value=[mock_session]),
            patch("claude_agent_sdk.get_session_messages", return_value=[]),
        ):
            resp = client.get("/api/sdk-sessions/sess-001")

        assert resp.status_code == 200
        assert run.id in resp.json()["session"]["linked_run_ids"]

    def test_get_session_sdk_error_returns_500(self, api_client):
        client, _ = api_client

        with (
            patch("claude_agent_sdk.list_sessions", return_value=[MockSDKSessionInfo(session_id="sess-001")]),
            patch("claude_agent_sdk.get_session_messages", side_effect=Exception("Read error")),
        ):
            resp = client.get("/api/sdk-sessions/sess-001")

        assert resp.status_code == 500

    def test_get_session_limit_and_offset(self, api_client):
        client, _ = api_client

        mock_session = MockSDKSessionInfo(session_id="sess-001")
        mock_get_msgs = MagicMock(return_value=[])
        with (
            patch("claude_agent_sdk.list_sessions", return_value=[mock_session]),
            patch("claude_agent_sdk.get_session_messages", mock_get_msgs),
        ):
            resp = client.get("/api/sdk-sessions/sess-001?limit=10&offset=5")

        assert resp.status_code == 200
        mock_get_msgs.assert_called_once()
        _, kwargs = mock_get_msgs.call_args
        assert kwargs.get("limit") == 10
        assert kwargs.get("offset") == 5
