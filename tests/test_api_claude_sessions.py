"""Tests for the Claude Session Explorer (Theme C4).

Covers:
- ``GET /api/projects/{project_id}/claude-sessions``
- ``GET /api/projects/{project_id}/claude-sessions/{session_id}``
- ``GET /api/projects/{project_id}/claude-sessions/{session_id}/messages``
- ``gluon claude-sessions {list,show,messages}`` CLI sub-commands

The Claude Agent SDK is mocked throughout so the tests do not touch
``~/.claude/projects``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from gluon import cli as gluon_cli
from gluon.models import Workspace

# ---------------------------------------------------------------------------
# Mock SDK types
# ---------------------------------------------------------------------------


@dataclass
class MockSDKSessionInfo:
    session_id: str = "11111111-1111-1111-1111-111111111111"
    summary: str = "Example session"
    last_modified: int = 1_710_000_000_000
    file_size: int | None = 4096
    tag: str | None = None
    created_at: int | None = 1_709_000_000_000
    git_branch: str | None = "main"
    cwd: str | None = "/tmp/project"
    first_prompt: str | None = "hello"
    custom_title: str | None = None


@dataclass
class MockSessionMessage:
    type: str = "user"
    uuid: str = "msg-001"
    session_id: str = "11111111-1111-1111-1111-111111111111"
    message: Any = field(default_factory=lambda: {"role": "user", "content": "hello"})
    parent_tool_use_id: None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(temp_store, tmp_path):
    """Create a workspace and project with a real on-disk path."""
    ws: Workspace = temp_store.create_workspace("ws-c4", str(tmp_path))
    project_dir = tmp_path / "c4-project"
    project_dir.mkdir()
    project = temp_store.create_project(
        name="c4-project",
        path=str(project_dir),
        workspace_id=ws.id,
    )
    return project, project_dir


# ===========================================================================
# API: GET /api/projects/{project_id}/claude-sessions
# ===========================================================================


class TestListClaudeSessions:
    def test_returns_session_list(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        mock_sessions = [
            MockSDKSessionInfo(session_id="aaaaaaaa-1111-1111-1111-111111111111", summary="A"),
            MockSDKSessionInfo(session_id="bbbbbbbb-2222-2222-2222-222222222222", summary="B"),
        ]
        with patch("claude_agent_sdk.list_sessions", return_value=mock_sessions):
            resp = client.get(f"/api/projects/{project.id}/claude-sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["project_dir"].endswith("c4-project")
        assert len(body["sessions"]) == 2
        assert body["sessions"][0]["session_id"].startswith("aaaaaaaa")
        assert body["sessions"][0]["summary"] == "A"
        assert body["sessions"][0]["last_modified_ms"] == 1_710_000_000_000
        assert body["sessions"][0]["created_at_ms"] == 1_709_000_000_000

    def test_returns_empty_when_sdk_returns_none(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        with patch("claude_agent_sdk.list_sessions", return_value=[]):
            resp = client.get(f"/api/projects/{project.id}/claude-sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"] == []
        assert body["total"] == 0

    def test_404_for_unknown_project(self, api_client):
        client, _ = api_client
        resp = client.get("/api/projects/does-not-exist/claude-sessions")
        assert resp.status_code == 404

    def test_forwards_limit_and_offset_to_sdk(self, temp_store, api_client, tmp_path):
        project, project_dir = _make_project(temp_store, tmp_path)
        client, _ = api_client

        mock_list = MagicMock(return_value=[])
        with patch("claude_agent_sdk.list_sessions", mock_list):
            resp = client.get(
                f"/api/projects/{project.id}/claude-sessions?limit=25&offset=10",
            )

        assert resp.status_code == 200
        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs["limit"] == 25
        assert kwargs["offset"] == 10
        assert kwargs["include_worktrees"] is True
        # Directory must be the project's expanded path.
        assert kwargs["directory"] == str(project_dir)

    def test_sdk_runtime_exception_returns_empty_list(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        with patch("claude_agent_sdk.list_sessions", side_effect=RuntimeError("disk error")):
            resp = client.get(f"/api/projects/{project.id}/claude-sessions")

        # Graceful degradation — operator can still use the dashboard.
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_sdk_import_error_returns_503(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        # Simulate the SDK being uninstalled by removing the module from
        # sys.modules and blocking re-import via the import machinery.
        saved = sys.modules.pop("claude_agent_sdk", None)
        try:
            orig_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

            def _blocking_import(name, *args, **kwargs):
                if name == "claude_agent_sdk":
                    raise ImportError("claude_agent_sdk not installed")
                return orig_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_blocking_import):
                resp = client.get(f"/api/projects/{project.id}/claude-sessions")
        finally:
            if saved is not None:
                sys.modules["claude_agent_sdk"] = saved

        assert resp.status_code == 503


# ===========================================================================
# API: GET /api/projects/{project_id}/claude-sessions/{session_id}
# ===========================================================================


class TestGetClaudeSession:
    def test_returns_session_detail(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        info = MockSDKSessionInfo(
            session_id="cccccccc-3333-3333-3333-333333333333",
            summary="Detail view",
            custom_title="My run",
            tag="release",
        )
        with patch("claude_agent_sdk.get_session_info", return_value=info):
            resp = client.get(
                f"/api/projects/{project.id}/claude-sessions/cccccccc-3333-3333-3333-333333333333",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"].startswith("cccccccc")
        assert body["summary"] == "Detail view"
        assert body["custom_title"] == "My run"
        assert body["tag"] == "release"

    def test_returns_404_when_sdk_returns_none(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        with patch("claude_agent_sdk.get_session_info", return_value=None):
            resp = client.get(f"/api/projects/{project.id}/claude-sessions/missing-session")

        assert resp.status_code == 404

    def test_returns_404_when_sdk_raises(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        with patch("claude_agent_sdk.get_session_info", side_effect=RuntimeError("boom")):
            resp = client.get(f"/api/projects/{project.id}/claude-sessions/anything")

        assert resp.status_code == 404

    def test_404_for_unknown_project(self, api_client):
        client, _ = api_client
        resp = client.get("/api/projects/does-not-exist/claude-sessions/abc")
        assert resp.status_code == 404


# ===========================================================================
# API: GET /api/projects/{project_id}/claude-sessions/{session_id}/messages
# ===========================================================================


class TestGetClaudeSessionMessages:
    def test_returns_messages(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        messages = [
            MockSessionMessage(
                type="user",
                uuid="m1",
                message={"role": "user", "content": "first prompt"},
            ),
            MockSessionMessage(
                type="assistant",
                uuid="m2",
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "answer"}],
                },
            ),
        ]
        with patch("claude_agent_sdk.get_session_messages", return_value=messages):
            resp = client.get(
                f"/api/projects/{project.id}/claude-sessions/sess-1/messages",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "sess-1"
        assert body["total"] == 2
        assert body["has_more"] is False
        assert body["messages"][0]["type"] == "user"
        assert body["messages"][0]["message"] == "first prompt"
        # Content-block shape is flattened to plain text.
        assert body["messages"][1]["message"] == "answer"

    def test_has_more_flag_set_when_extra_row_returned(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        # Request limit=2, but SDK returns 3 (limit+1) to signal more data.
        msgs = [MockSessionMessage(type="user", uuid=f"m{i}", message={"content": f"msg {i}"}) for i in range(3)]
        with patch("claude_agent_sdk.get_session_messages", return_value=msgs):
            resp = client.get(
                f"/api/projects/{project.id}/claude-sessions/s/messages?limit=2",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["messages"]) == 2
        assert body["has_more"] is True

    def test_pagination_arguments_forwarded(self, temp_store, api_client, tmp_path):
        project, project_dir = _make_project(temp_store, tmp_path)
        client, _ = api_client

        mock_get = MagicMock(return_value=[])
        with patch("claude_agent_sdk.get_session_messages", mock_get):
            resp = client.get(
                f"/api/projects/{project.id}/claude-sessions/s/messages?limit=10&offset=5",
            )

        assert resp.status_code == 200
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        # limit is bumped by +1 to compute has_more cheaply.
        assert kwargs["limit"] == 11
        assert kwargs["offset"] == 5
        assert kwargs["directory"] == str(project_dir)

    def test_messages_returns_empty_on_sdk_error(self, temp_store, api_client, tmp_path):
        project, _ = _make_project(temp_store, tmp_path)
        client, _ = api_client

        with patch("claude_agent_sdk.get_session_messages", side_effect=OSError("eof")):
            resp = client.get(
                f"/api/projects/{project.id}/claude-sessions/s/messages",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["messages"] == []
        assert body["total"] == 0
        assert body["has_more"] is False

    def test_messages_404_for_unknown_project(self, api_client):
        client, _ = api_client
        resp = client.get("/api/projects/does-not-exist/claude-sessions/s/messages")
        assert resp.status_code == 404


# ===========================================================================
# CLI: gluon claude-sessions
# ===========================================================================


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_project(tmp_path, monkeypatch):
    """Register a project in an isolated Gluon DB and make the CLI use it."""
    from gluon.store import GluonStore

    db_path = tmp_path / "gluon.db"
    store = GluonStore(db_path)
    ws = store.create_workspace("cli-ws", str(tmp_path))
    pdir = tmp_path / "cli-project"
    pdir.mkdir()
    project = store.create_project(
        name="cli-project",
        path=str(pdir),
        workspace_id=ws.id,
    )

    # Route `get_orchestrator()` through our isolated store.
    from gluon.core import Orchestrator

    monkeypatch.setattr(
        gluon_cli,
        "get_orchestrator",
        lambda: Orchestrator(store=store),
    )
    return project, pdir


class TestCliClaudeSessions:
    def test_list_renders_table(self, cli_runner: CliRunner, cli_project):
        project, _ = cli_project
        sessions = [
            MockSDKSessionInfo(
                session_id="ddddeeee-4444-4444-4444-444444444444",
                summary="Cli listing",
                git_branch="feat/test",
            ),
        ]
        with patch("claude_agent_sdk.list_sessions", return_value=sessions):
            result = cli_runner.invoke(
                gluon_cli.app,
                ["claude-sessions", "list", project.name, "--limit", "5"],
            )

        assert result.exit_code == 0, result.output
        assert "ddddeeee" in result.output
        assert "feat/test" in result.output
        # Summary text may wrap across lines in the rich table; check both words.
        assert "Cli" in result.output
        assert "listing" in result.output

    def test_list_json_output(self, cli_runner: CliRunner, cli_project):
        project, _ = cli_project
        sessions = [MockSDKSessionInfo(session_id="eeeeffff-5555-5555-5555-555555555555")]
        with patch("claude_agent_sdk.list_sessions", return_value=sessions):
            result = cli_runner.invoke(
                gluon_cli.app,
                ["claude-sessions", "list", project.name, "--json"],
            )

        assert result.exit_code == 0, result.output
        assert "eeeeffff-5555" in result.output
        assert "session_id" in result.output

    def test_list_unknown_project_exits_nonzero(self, cli_runner: CliRunner, cli_project):
        with patch("claude_agent_sdk.list_sessions", return_value=[]):
            result = cli_runner.invoke(
                gluon_cli.app,
                ["claude-sessions", "list", "does-not-exist"],
            )
        assert result.exit_code != 0

    def test_show_renders_panel(self, cli_runner: CliRunner, cli_project):
        project, _ = cli_project
        info = MockSDKSessionInfo(
            session_id="ffffffff-6666-6666-6666-666666666666",
            summary="Show panel",
            custom_title="Important",
        )
        with patch("claude_agent_sdk.get_session_info", return_value=info):
            result = cli_runner.invoke(
                gluon_cli.app,
                ["claude-sessions", "show", project.name, info.session_id],
            )

        assert result.exit_code == 0, result.output
        assert "ffffffff" in result.output
        assert "Important" in result.output

    def test_show_missing_session_exits_nonzero(self, cli_runner: CliRunner, cli_project):
        project, _ = cli_project
        with patch("claude_agent_sdk.get_session_info", return_value=None):
            result = cli_runner.invoke(
                gluon_cli.app,
                ["claude-sessions", "show", project.name, "missing"],
            )
        assert result.exit_code != 0

    def test_messages_renders_conversation(self, cli_runner: CliRunner, cli_project):
        project, _ = cli_project
        messages = [
            MockSessionMessage(
                type="user",
                uuid="u1",
                message={"role": "user", "content": "hi"},
            ),
            MockSessionMessage(
                type="assistant",
                uuid="a1",
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello there"}],
                },
            ),
        ]
        with patch("claude_agent_sdk.get_session_messages", return_value=messages):
            result = cli_runner.invoke(
                gluon_cli.app,
                [
                    "claude-sessions",
                    "messages",
                    project.name,
                    "abc-session",
                    "--limit",
                    "5",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "USER" in result.output
        assert "ASSISTANT" in result.output
        assert "hello there" in result.output

    def test_messages_caps_long_body(self, cli_runner: CliRunner, cli_project):
        project, _ = cli_project
        long_body = "x" * 2000
        messages = [
            MockSessionMessage(
                type="user",
                uuid="long-msg",
                message={"content": long_body},
            )
        ]
        with patch("claude_agent_sdk.get_session_messages", return_value=messages):
            result = cli_runner.invoke(
                gluon_cli.app,
                ["claude-sessions", "messages", project.name, "s"],
            )

        assert result.exit_code == 0, result.output
        # 500-char preview cap + ellipsis marker.
        assert "…" in result.output
        # 2000 x's were sent, but only ~500 rendered.
        assert result.output.count("x") < 700
