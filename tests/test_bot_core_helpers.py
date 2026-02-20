"""Tests for GluonBotCore utility methods: is_authorized, extract_run_info_from_message,
resolve_project, formatting, recovery, task registration, and history."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from gluon.bot_core import GluonBotCore
from gluon.core import ProjectNotFoundError
from gluon.models import Project, RunStatus


@pytest.fixture
def bot_core():
    """Create a GluonBotCore with mocked dependencies."""
    with (
        patch("gluon.bot_core.GluonStore"),
        patch("gluon.bot_core.Orchestrator") as mock_orch_cls,
        patch("gluon.bot_core.GitManager"),
        patch("gluon.bot_core.NotificationDispatcher"),
        patch("gluon.bot_core.GluonChatAgent"),
    ):
        store = MagicMock()
        orchestrator = MagicMock()
        mock_orch_cls.return_value = orchestrator
        core = GluonBotCore(store=store, orchestrator=orchestrator)
        return core


# ========== is_authorized ==========


class TestIsAuthorized:
    def test_allows_all_when_none(self, bot_core: GluonBotCore):
        assert bot_core.is_authorized("telegram:123", None) is True

    def test_allows_matching_user(self, bot_core: GluonBotCore):
        allowed = {"telegram:123", "telegram:456"}
        assert bot_core.is_authorized("telegram:123", allowed) is True

    def test_rejects_non_matching_user(self, bot_core: GluonBotCore):
        allowed = {"telegram:123", "telegram:456"}
        assert bot_core.is_authorized("telegram:789", allowed) is False


# ========== extract_run_info_from_message ==========


class TestExtractRunInfoFromMessage:
    def test_complete_pattern(self, bot_core: GluonBotCore):
        text = "✅ **Complete** (`abcd1234`)"
        run_id, project = bot_core.extract_run_info_from_message(text)
        assert run_id == "abcd1234"
        assert project is None

    def test_failed_pattern(self, bot_core: GluonBotCore):
        text = "❌ **Failed** (`deadbeef`)"
        run_id, project = bot_core.extract_run_info_from_message(text)
        assert run_id == "deadbeef"

    def test_run_colon_pattern(self, bot_core: GluonBotCore):
        text = "Run: abc12345"
        run_id, _ = bot_core.extract_run_info_from_message(text)
        assert run_id == "abc12345"

    def test_task_started_pattern(self, bot_core: GluonBotCore):
        text = "Task started: abc12345"
        run_id, _ = bot_core.extract_run_info_from_message(text)
        assert run_id == "abc12345"

    def test_project_pattern(self, bot_core: GluonBotCore):
        text = "Project: myapp"
        _, project = bot_core.extract_run_info_from_message(text)
        assert project == "myapp"

    def test_combined_run_and_project(self, bot_core: GluonBotCore):
        text = "✅ **Complete** (`abcd1234`)\nProject: my-project"
        run_id, project = bot_core.extract_run_info_from_message(text)
        assert run_id == "abcd1234"
        assert project == "my-project"

    def test_no_match(self, bot_core: GluonBotCore):
        text = "Just a regular message"
        run_id, project = bot_core.extract_run_info_from_message(text)
        assert run_id is None
        assert project is None

    def test_prefers_complete_match_over_task_match(self, bot_core: GluonBotCore):
        text = "✅ **Complete** (`abcd1234`)\nTask started: deadbeef"
        run_id, _ = bot_core.extract_run_info_from_message(text)
        # complete_match sets run_id first; task_match only sets if run_id is None
        assert run_id == "abcd1234"

    def test_backtick_wrapped_ids(self, bot_core: GluonBotCore):
        text = "Run: `abcd1234`"
        run_id, _ = bot_core.extract_run_info_from_message(text)
        assert run_id == "abcd1234"


# ========== resolve_project ==========


class TestResolveProject:
    def test_direct_hint_found(self, bot_core: GluonBotCore):
        bot_core.orchestrator.get_project.return_value = Project(
            name="myapp", path="/tmp/myapp"
        )
        result = bot_core.resolve_project("myapp")
        assert result == "myapp"

    def test_hint_not_found_channel_matches(self, bot_core: GluonBotCore):
        # First call for hint fails, second call for channel_name succeeds
        bot_core.orchestrator.get_project.side_effect = [
            ProjectNotFoundError("nope"),
            Project(name="my_app", path="/tmp/my_app"),
        ]
        result = bot_core.resolve_project("nope", channel_name="my-app")
        assert result == "my_app"

    def test_both_fail_returns_none(self, bot_core: GluonBotCore):
        bot_core.orchestrator.get_project.side_effect = ProjectNotFoundError("not found")
        result = bot_core.resolve_project("bad", channel_name="also-bad")
        assert result is None

    def test_hint_preferred_over_channel(self, bot_core: GluonBotCore):
        bot_core.orchestrator.get_project.return_value = Project(
            name="myapp", path="/tmp/myapp"
        )
        result = bot_core.resolve_project("myapp", channel_name="other-channel")
        assert result == "myapp"
        # Only called once because hint succeeded
        assert bot_core.orchestrator.get_project.call_count == 1


# ========== format_projects_list ==========


class TestFormatProjectsList:
    def test_no_projects(self, bot_core: GluonBotCore):
        bot_core.orchestrator.list_projects.return_value = []
        result = bot_core.format_projects_list()
        assert "No projects registered" in result

    def test_with_projects(self, bot_core: GluonBotCore):
        bot_core.orchestrator.list_projects.return_value = [
            Project(name="app1", path="/tmp/app1"),
            Project(name="app2", path="/tmp/app2"),
        ]
        bot_core.orchestrator.list_sessions.return_value = [MagicMock()]
        result = bot_core.format_projects_list()
        assert "`app1`" in result
        assert "`app2`" in result
        assert "1 sessions" in result

    def test_filter_term(self, bot_core: GluonBotCore):
        bot_core.orchestrator.list_projects.return_value = [
            Project(name="web-app", path="/tmp/web"),
            Project(name="api-server", path="/tmp/api"),
        ]
        bot_core.orchestrator.list_sessions.return_value = []
        result = bot_core.format_projects_list(filter_term="web")
        assert "`web-app`" in result
        assert "api-server" not in result

    def test_filter_no_matches(self, bot_core: GluonBotCore):
        bot_core.orchestrator.list_projects.return_value = [
            Project(name="app1", path="/tmp/app1"),
        ]
        result = bot_core.format_projects_list(filter_term="nonexistent")
        assert "No projects matching" in result

    def test_limit_truncation(self, bot_core: GluonBotCore):
        projects = [Project(name=f"proj-{i}", path=f"/tmp/proj-{i}") for i in range(25)]
        bot_core.orchestrator.list_projects.return_value = projects
        bot_core.orchestrator.list_sessions.return_value = []
        result = bot_core.format_projects_list(limit=5)
        assert "and 20 more projects" in result


# ========== format_runs_list ==========


class TestFormatRunsList:
    def test_no_runs(self, bot_core: GluonBotCore):
        bot_core.store.list_runs.return_value = []
        result = bot_core.format_runs_list()
        assert "No runs found." in result

    def test_no_runs_with_initiator(self, bot_core: GluonBotCore):
        bot_core.store.list_runs.return_value = []
        result = bot_core.format_runs_list(initiator="telegram:123")
        assert "No runs found." in result
        assert "/runs all" in result

    def test_with_runs(self, bot_core: GluonBotCore):
        run = MagicMock()
        run.id = "abcdef12"
        run.project_id = "proj-1"
        run.status = RunStatus.COMPLETED
        run.duration_seconds = 120.0
        run.prompt = "fix the bug"
        bot_core.store.list_runs.return_value = [run]
        bot_core.store.list_projects.return_value = [
            MagicMock(id="proj-1", name="my-project"),
        ]
        bot_core.store.list_active_runs.return_value = []
        result = bot_core.format_runs_list()
        assert "Recent Runs" in result
        assert "abcdef12" in result
        assert "my-project" in result

    def test_initiator_header(self, bot_core: GluonBotCore):
        run = MagicMock()
        run.id = "abcdef12"
        run.project_id = "proj-1"
        run.status = RunStatus.RUNNING
        run.duration_seconds = None
        run.prompt = "test"
        bot_core.store.list_runs.return_value = [run]
        bot_core.store.list_projects.return_value = []
        bot_core.store.list_active_runs.return_value = [run]
        result = bot_core.format_runs_list(initiator="telegram:123")
        assert "Your Runs" in result
        assert "1** run(s) currently active" in result


# ========== format_status ==========


class TestFormatStatus:
    def test_returns_status_string(self, bot_core: GluonBotCore):
        bot_core.orchestrator.status.return_value = {
            "total_projects": 3,
            "active_sessions": 1,
            "projects": [{"name": "app1", "sessions": 2}],
        }
        result = bot_core.format_status()
        assert "Projects: 3" in result
        assert "Active Sessions: 1" in result
        assert "`app1`" in result


# ========== recover_stale_runs ==========


class TestRecoverStaleRuns:
    def test_no_active_runs(self, bot_core: GluonBotCore):
        bot_core.store.list_active_runs.return_value = []
        assert bot_core.recover_stale_runs("telegram") == 0

    def test_recovers_matching_transport(self, bot_core: GluonBotCore):
        run = MagicMock()
        run.initiator = "telegram:123"
        run.id = "abc12345"
        bot_core.store.list_active_runs.return_value = [run]
        count = bot_core.recover_stale_runs("telegram")
        assert count == 1
        run.mark_failed.assert_called_once()
        bot_core.store.update_run.assert_called_once_with(run)

    def test_skips_other_transport(self, bot_core: GluonBotCore):
        run = MagicMock()
        run.initiator = "discord:456"
        run.id = "abc12345"
        bot_core.store.list_active_runs.return_value = [run]
        count = bot_core.recover_stale_runs("telegram")
        assert count == 0
        run.mark_failed.assert_not_called()


# ========== task registration ==========


class TestTaskRegistration:
    def test_register_and_get(self, bot_core: GluonBotCore):
        task = MagicMock(spec=asyncio.Task)
        bot_core.register_task("run-1", task)
        assert bot_core.get_task("run-1") is task

    def test_unregister(self, bot_core: GluonBotCore):
        task = MagicMock(spec=asyncio.Task)
        bot_core.register_task("run-1", task)
        bot_core.unregister_task("run-1")
        assert bot_core.get_task("run-1") is None

    def test_unregister_nonexistent_no_error(self, bot_core: GluonBotCore):
        bot_core.unregister_task("nope")  # should not raise

    def test_get_nonexistent_returns_none(self, bot_core: GluonBotCore):
        assert bot_core.get_task("nope") is None


# ========== history ==========


class TestHistory:
    def test_add_to_history(self, bot_core: GluonBotCore):
        bot_core.add_to_history("telegram:123", "user", "hello")
        bot_core.store.create_chat_history.assert_called_once_with(
            "telegram:123", "telegram", "user", "hello"
        )

    def test_get_history(self, bot_core: GluonBotCore):
        entry = MagicMock()
        entry.role = "user"
        entry.text = "hello"
        bot_core.store.get_chat_history.return_value = [entry]
        history = bot_core.get_history("telegram:123")
        assert len(history) == 1
        assert history[0].role == "user"
        assert history[0].text == "hello"

    def test_clear_history(self, bot_core: GluonBotCore):
        bot_core.clear_history("telegram:123")
        bot_core.store.clear_chat_history.assert_called_once_with("telegram:123")
