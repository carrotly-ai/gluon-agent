"""Tests for TodoWrite PostToolUse mirror hook."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gluon.agent_hooks import TodoCollector, _make_todo_mirror_hook
from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path: Path) -> GluonStore:
    return GluonStore(tmp_path / "test.db")


@pytest.fixture
def run_id(store: GluonStore, tmp_path: Path) -> str:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = store.create_project("test", project_dir)
    run = store.create_run(project.id, "test task")
    return run.id


@pytest.fixture
def collector(run_id: str, store: GluonStore) -> TodoCollector:
    return TodoCollector(
        run_id=run_id,
        store=store,
        message_callback=None,
    )


SAMPLE_TODOS = [
    {"content": "Fix the bug", "status": "completed", "activeForm": "Fixing the bug"},
    {"content": "Add tests", "status": "in_progress", "activeForm": "Adding tests"},
    {"content": "Update docs", "status": "pending", "activeForm": "Updating docs"},
]


class TestTodoMirrorHook:
    """Tests for the PostToolUse mirror hook that captures TodoWrite calls."""

    @pytest.mark.asyncio
    async def test_hook_ignores_non_todowrite(self, collector: TodoCollector, store: GluonStore):
        """Hook should return empty dict for non-TodoWrite tools."""
        hook = _make_todo_mirror_hook(collector)

        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_use_id": "tu_1"},
            "tu_1",
            {"signal": None},
        )

        assert result == {}
        assert store.get_latest_todo_snapshot(collector.run_id) is None

    @pytest.mark.asyncio
    async def test_hook_captures_todowrite(self, collector: TodoCollector, store: GluonStore):
        """Hook should persist a TodoSnapshot when TodoWrite is called."""
        hook = _make_todo_mirror_hook(collector)

        result = await hook(
            {"tool_name": "TodoWrite", "tool_input": {"todos": SAMPLE_TODOS}, "tool_use_id": "tu_2"},
            "tu_2",
            {"signal": None},
        )

        assert result == {}
        snapshot = store.get_latest_todo_snapshot(collector.run_id)
        assert snapshot is not None
        assert len(snapshot.todos) == 3
        assert snapshot.todos[0]["content"] == "Fix the bug"

    @pytest.mark.asyncio
    async def test_hook_computes_counts(self, collector: TodoCollector, store: GluonStore):
        """Hook should correctly compute completed/in_progress/pending counts."""
        hook = _make_todo_mirror_hook(collector)

        await hook(
            {"tool_name": "TodoWrite", "tool_input": {"todos": SAMPLE_TODOS}, "tool_use_id": "tu_3"},
            "tu_3",
            {"signal": None},
        )

        snapshot = store.get_latest_todo_snapshot(collector.run_id)
        assert snapshot is not None
        assert snapshot.todo_count == 3
        assert snapshot.completed_count == 1
        assert snapshot.in_progress_count == 1
        assert snapshot.pending_count == 1

    @pytest.mark.asyncio
    async def test_hook_writes_message_callback(self, run_id: str, store: GluonStore):
        """Hook should call message_callback with structured log entry."""
        callback = MagicMock()
        collector = TodoCollector(run_id=run_id, store=store, message_callback=callback)
        hook = _make_todo_mirror_hook(collector)

        await hook(
            {"tool_name": "TodoWrite", "tool_input": {"todos": SAMPLE_TODOS}, "tool_use_id": "tu_4"},
            "tu_4",
            {"signal": None},
        )

        callback.assert_called_once()
        msg = callback.call_args[0][0]
        assert msg["type"] == "todos_updated"
        assert msg["content"] == "1/3 completed"
        assert msg["metadata"]["todo_count"] == 3
        assert msg["metadata"]["completed_count"] == 1

    @pytest.mark.asyncio
    async def test_hook_returns_empty_dict(self, collector: TodoCollector):
        """Hook must always return empty dict (read-only, no output modification)."""
        hook = _make_todo_mirror_hook(collector)

        result = await hook(
            {"tool_name": "TodoWrite", "tool_input": {"todos": SAMPLE_TODOS}, "tool_use_id": "tu_5"},
            "tu_5",
            {"signal": None},
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_hook_handles_empty_todos(self, collector: TodoCollector, store: GluonStore):
        """Hook should handle an empty todos array gracefully."""
        hook = _make_todo_mirror_hook(collector)

        await hook(
            {"tool_name": "TodoWrite", "tool_input": {"todos": []}, "tool_use_id": "tu_6"},
            "tu_6",
            {"signal": None},
        )

        snapshot = store.get_latest_todo_snapshot(collector.run_id)
        assert snapshot is not None
        assert snapshot.todo_count == 0

    @pytest.mark.asyncio
    async def test_hook_handles_missing_todos_key(self, collector: TodoCollector, store: GluonStore):
        """Hook should handle tool_input without todos key gracefully."""
        hook = _make_todo_mirror_hook(collector)

        result = await hook(
            {"tool_name": "TodoWrite", "tool_input": {}, "tool_use_id": "tu_7"},
            "tu_7",
            {"signal": None},
        )

        assert result == {}
        # Empty list → still creates a snapshot with 0 items
        snapshot = store.get_latest_todo_snapshot(collector.run_id)
        assert snapshot is not None
        assert snapshot.todo_count == 0
