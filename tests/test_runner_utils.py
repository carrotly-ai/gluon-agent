"""Tests for runner utility functions: format_duration, format_run_status,
_parse_completed_tasks, _get_last_tool_used."""

import json

import pytest

from gluon.models import RunStatus
from gluon.runner import TaskRunner, format_duration, format_run_status

# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_none(self):
        assert format_duration(None) == "-"

    def test_sub_second(self):
        assert format_duration(0.5) == "0.5s"

    def test_seconds(self):
        assert format_duration(45.0) == "45.0s"

    def test_minutes_and_seconds(self):
        assert format_duration(90.0) == "1m 30s"

    def test_hours_and_minutes(self):
        assert format_duration(3661.0) == "1h 1m"

    def test_exact_minute(self):
        assert format_duration(60.0) == "1m 0s"

    def test_exact_hour(self):
        assert format_duration(3600.0) == "1h 0m"


# ---------------------------------------------------------------------------
# format_run_status
# ---------------------------------------------------------------------------


class TestFormatRunStatus:
    def test_pending(self):
        assert format_run_status(RunStatus.PENDING) == ("⏳", "yellow")

    def test_running(self):
        assert format_run_status(RunStatus.RUNNING) == ("🔄", "blue")

    def test_completed(self):
        assert format_run_status(RunStatus.COMPLETED) == ("✅", "green")

    def test_failed(self):
        assert format_run_status(RunStatus.FAILED) == ("❌", "red")

    def test_cancelled(self):
        assert format_run_status(RunStatus.CANCELLED) == ("🚫", "dim")

    def test_review_falls_to_default(self):
        assert format_run_status(RunStatus.REVIEW) == ("❓", "white")

    def test_running_with_healthy(self):
        from gluon.runner import RunHealth

        emoji, color = format_run_status(RunStatus.RUNNING, RunHealth.HEALTHY)
        assert emoji == "🟢"
        assert color == "green"

    def test_running_with_slow(self):
        from gluon.runner import RunHealth

        emoji, color = format_run_status(RunStatus.RUNNING, RunHealth.SLOW)
        assert emoji == "🟡"
        assert color == "yellow"

    def test_running_with_stalled(self):
        from gluon.runner import RunHealth

        emoji, color = format_run_status(RunStatus.RUNNING, RunHealth.STALLED)
        assert emoji == "🔴"
        assert color == "red"

    def test_non_running_ignores_health(self):
        from gluon.runner import RunHealth

        emoji, color = format_run_status(RunStatus.COMPLETED, RunHealth.STALLED)
        assert emoji == "✅"
        assert color == "green"


# ---------------------------------------------------------------------------
# _parse_completed_tasks
# ---------------------------------------------------------------------------


class TestParseCompletedTasks:
    @pytest.fixture
    def runner(self, store):
        return TaskRunner(store=store)

    def test_empty_file(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        p.write_text("")
        assert runner._parse_completed_tasks(p) == []

    def test_completed_todo_entries(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        msg = {
            "type": "tool_use",
            "metadata": {
                "tool": "TodoWrite",
                "input": {
                    "todos": [
                        {"content": "task1", "status": "completed"},
                        {"content": "task2", "status": "completed"},
                    ]
                },
            },
        }
        p.write_text(json.dumps(msg) + "\n")
        assert runner._parse_completed_tasks(p) == ["task1", "task2"]

    def test_non_completed_todos_excluded(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        msg = {
            "type": "tool_use",
            "metadata": {
                "tool": "TodoWrite",
                "input": {"todos": [{"content": "wip", "status": "in_progress"}]},
            },
        }
        p.write_text(json.dumps(msg) + "\n")
        assert runner._parse_completed_tasks(p) == []

    def test_only_todowrite_tool(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        lines = [
            json.dumps({"type": "tool_use", "metadata": {"tool": "Read"}}),
            json.dumps(
                {
                    "type": "tool_use",
                    "metadata": {
                        "tool": "TodoWrite",
                        "input": {"todos": [{"content": "done", "status": "completed"}]},
                    },
                }
            ),
        ]
        p.write_text("\n".join(lines) + "\n")
        assert runner._parse_completed_tasks(p) == ["done"]

    def test_malformed_json_skipped(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        p.write_text("not json\n{bad\n")
        assert runner._parse_completed_tasks(p) == []

    def test_nonexistent_file(self, runner, tmp_path):
        p = tmp_path / "missing.jsonl"
        assert runner._parse_completed_tasks(p) == []


# ---------------------------------------------------------------------------
# _get_last_tool_used
# ---------------------------------------------------------------------------


class TestGetLastToolUsed:
    @pytest.fixture
    def runner(self, store):
        return TaskRunner(store=store)

    def test_empty_file(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        p.write_text("")
        assert runner._get_last_tool_used(p) is None

    def test_returns_last_tool(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        lines = [
            json.dumps({"type": "tool_use", "metadata": {"tool": "Read"}}),
            json.dumps({"type": "text", "content": "hello"}),
            json.dumps({"type": "tool_use", "metadata": {"tool": "Edit"}}),
            json.dumps({"type": "tool_use", "metadata": {"tool": "Bash"}}),
        ]
        p.write_text("\n".join(lines) + "\n")
        assert runner._get_last_tool_used(p) == "Bash"

    def test_no_tool_use_entries(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        p.write_text(json.dumps({"type": "text", "content": "hi"}) + "\n")
        assert runner._get_last_tool_used(p) is None

    def test_malformed_json(self, runner, tmp_path):
        p = tmp_path / "messages.jsonl"
        p.write_text("bad json\n")
        assert runner._get_last_tool_used(p) is None
