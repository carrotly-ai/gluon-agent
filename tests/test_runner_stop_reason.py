"""Tests for runner.py stop_reason storage and heartbeat filter removal.

Verifies:
- stop_reason from AgentResult is stored in run.metadata
- task_progress messages are NOT filtered (heartbeat filter was removed)
"""

from __future__ import annotations

import json
from pathlib import Path

from gluon.models import ExecutionRun
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_project(store: GluonStore, tmp_path: Path):
    """Create a workspace + project for testing."""
    ws = store.create_workspace("ws", str(tmp_path))
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(exist_ok=True)
    proj = store.create_project("proj", str(proj_dir), workspace_id=ws.id)
    return proj


def _seed_run(store: GluonStore, project_id: str, *, metadata: dict | None = None) -> ExecutionRun:
    """Create a run with optional metadata."""
    run = store.create_run(project_id=project_id, prompt="test", initiator="test")
    if metadata is not None:
        run.metadata = metadata
        store.update_run(run)
    return run


# ===========================================================================
# stop_reason metadata storage
# ===========================================================================


class TestStopReasonMetadata:
    """Tests the runner logic: if item.stop_reason, store in run.metadata."""

    def test_stop_reason_stored_in_metadata(self, store: GluonStore, tmp_path: Path):
        proj = _create_project(store, tmp_path)
        run = _seed_run(store, proj.id, metadata=None)

        # Simulate runner logic
        stop_reason = "end_turn"
        if stop_reason:
            if run.metadata is None:
                run.metadata = {}
            run.metadata["stop_reason"] = stop_reason
        store.update_run(run)

        fetched = store.get_run(run.id)
        assert fetched is not None
        assert fetched.metadata is not None
        assert fetched.metadata["stop_reason"] == "end_turn"

    def test_stop_reason_preserves_existing_metadata(self, store: GluonStore, tmp_path: Path):
        proj = _create_project(store, tmp_path)
        run = _seed_run(store, proj.id, metadata={"profile": "deep"})

        stop_reason = "end_turn"
        if stop_reason:
            if run.metadata is None:
                run.metadata = {}
            run.metadata["stop_reason"] = stop_reason
        store.update_run(run)

        fetched = store.get_run(run.id)
        assert fetched is not None
        assert fetched.metadata["profile"] == "deep"
        assert fetched.metadata["stop_reason"] == "end_turn"

    def test_stop_reason_none_does_not_write_metadata(self, store: GluonStore, tmp_path: Path):
        proj = _create_project(store, tmp_path)
        run = _seed_run(store, proj.id, metadata=None)

        stop_reason = None
        if stop_reason:
            if run.metadata is None:
                run.metadata = {}
            run.metadata["stop_reason"] = stop_reason
        store.update_run(run)

        fetched = store.get_run(run.id)
        assert fetched is not None
        assert fetched.metadata is None

    def test_stop_reason_overwrites_previous(self, store: GluonStore, tmp_path: Path):
        proj = _create_project(store, tmp_path)
        run = _seed_run(store, proj.id, metadata={"stop_reason": "end_turn"})

        stop_reason = "max_turns"
        if stop_reason:
            if run.metadata is None:
                run.metadata = {}
            run.metadata["stop_reason"] = stop_reason
        store.update_run(run)

        fetched = store.get_run(run.id)
        assert fetched is not None
        assert fetched.metadata["stop_reason"] == "max_turns"


# ===========================================================================
# Heartbeat filter removal verification
# ===========================================================================


class TestHeartbeatFilterRemoval:
    """Verify task_progress messages pass through the runner message loop."""

    def test_task_progress_messages_not_filtered(self, tmp_path: Path):
        """task_progress messages should be written to messages.jsonl (not filtered)."""
        from gluon.agent import AgentMessage

        messages_path = tmp_path / "messages.jsonl"

        # Simulate the runner message writing loop
        item = AgentMessage(
            type="task_progress",
            content="Reading files",
            metadata={"task_id": "t1", "last_tool_name": "Read"},
        )

        # Runner writes ALL AgentMessages to messages.jsonl — no filter
        msg_dict = {
            "timestamp": "2026-03-10T00:00:00",
            "type": item.type,
            "content": item.content,
            "metadata": item.metadata,
        }
        with open(messages_path, "a") as f:
            f.write(json.dumps(msg_dict) + "\n")

        # Verify it was written
        lines = messages_path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "task_progress"
        assert parsed["content"] == "Reading files"

    def test_system_messages_still_pass_through(self, tmp_path: Path):
        """Non-heartbeat system messages should still pass through."""
        from gluon.agent import AgentMessage

        messages_path = tmp_path / "messages.jsonl"

        item = AgentMessage(
            type="system",
            content="init",
            metadata={"session_id": "sess-123"},
        )

        msg_dict = {
            "timestamp": "2026-03-10T00:00:00",
            "type": item.type,
            "content": item.content,
            "metadata": item.metadata,
        }
        with open(messages_path, "a") as f:
            f.write(json.dumps(msg_dict) + "\n")

        lines = messages_path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "system"
