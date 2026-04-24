"""Tests for OrchestratorTask + TaskComment CRUD and atomic checkout (Theme B Phase 3)."""

import threading
from pathlib import Path

import pytest

from gluon.core import TaskLockedError, TaskNotFoundError
from gluon.models import TaskStatus, utc_now
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "tasks.db")


def _make_project(store: GluonStore, tmp_path: Path, ws_name: str = "ws", proj_name: str = "proj"):
    ws_path = tmp_path / ws_name
    ws_path.mkdir(exist_ok=True)
    workspace = store.create_workspace(ws_name, ws_path)
    proj_path = ws_path / proj_name
    proj_path.mkdir(exist_ok=True)
    project = store.create_project(proj_name, proj_path, workspace_id=workspace.id)
    return workspace, project


def _make_run(store: GluonStore, project_id: str, prompt: str = "test"):
    return store.create_run(project_id=project_id, prompt=prompt)


# ========== Basic CRUD ==========


def test_create_task_minimal(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)

    task = store.create_task(project.id, "Fix the bug")

    assert task.id
    assert task.project_id == project.id
    assert task.title == "Fix the bug"
    assert task.status == TaskStatus.BACKLOG
    assert task.priority == 5
    assert task.assigned_agent_id is None
    assert task.created_by == "cli"


def test_create_task_with_assignment_sets_assigned_status(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "researcher")

    task = store.create_task(project.id, "Research", priority=9, assigned_agent_id=agent.id)

    assert task.status == TaskStatus.ASSIGNED
    assert task.assigned_agent_id == agent.id
    assert task.priority == 9


def test_create_task_with_assigned_files(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)

    task = store.create_task(project.id, "Refactor API", assigned_files=["src/api/**/*.py", "tests/test_api.py"])

    fresh = store.get_task(task.id)
    assert fresh is not None
    assert fresh.assigned_files == ["src/api/**/*.py", "tests/test_api.py"]


def test_get_task_by_prefix(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)
    task = store.create_task(project.id, "Prefix lookup")

    # 8-char prefix should resolve
    fetched = store.get_task(task.id[:8])
    assert fetched is not None
    assert fetched.id == task.id


def test_get_task_returns_none_for_unknown(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_task("doesnotexist") is None


def test_list_tasks_filters(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "agent-a")

    store.create_task(project.id, "Low pri", priority=1)
    store.create_task(project.id, "High pri", priority=10)
    store.create_task(project.id, "Assigned", priority=5, assigned_agent_id=agent.id)

    # Order: priority DESC, created_at ASC
    all_tasks = store.list_tasks(project_id=project.id)
    assert len(all_tasks) == 3
    assert [t.priority for t in all_tasks] == [10, 5, 1]

    # Filter by agent
    assigned_tasks = store.list_tasks(agent_id=agent.id)
    assert len(assigned_tasks) == 1
    assert assigned_tasks[0].title == "Assigned"

    # Filter by status
    backlog_tasks = store.list_tasks(status=TaskStatus.BACKLOG)
    assert len(backlog_tasks) == 2


def test_update_task(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)
    task = store.create_task(project.id, "Original")

    task.title = "Updated"
    task.priority = 9
    task.description = "Now with description"
    store.update_task(task)

    fresh = store.get_task(task.id)
    assert fresh is not None
    assert fresh.title == "Updated"
    assert fresh.priority == 9
    assert fresh.description == "Now with description"


def test_delete_task_removes_comments(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)
    task = store.create_task(project.id, "Doomed")
    store.add_task_comment(task.id, "A comment", author_label="cli")
    store.add_task_comment(task.id, "Another", author_label="cli")

    deleted = store.delete_task(task.id)

    assert deleted is True
    assert store.get_task(task.id) is None
    assert store.list_task_comments(task.id) == []


# ========== Atomic checkout ==========


def test_checkout_task_locks_and_assigns(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "checker")
    task = store.create_task(project.id, "Checkout me")
    run = _make_run(store, project.id)

    locked = store.checkout_task(task.id, agent_id=agent.id, run_id=run.id)

    assert locked.status == TaskStatus.IN_PROGRESS
    assert locked.assigned_agent_id == agent.id
    assert locked.execution_run_id == run.id
    assert locked.execution_locked_at is not None


def test_checkout_task_raises_when_already_locked(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    a1 = store.create_agent(ws.id, "first")
    a2 = store.create_agent(ws.id, "second")
    task = store.create_task(project.id, "Contested")
    r1 = _make_run(store, project.id, "run-1")
    r2 = _make_run(store, project.id, "run-2")

    store.checkout_task(task.id, agent_id=a1.id, run_id=r1.id)

    with pytest.raises(TaskLockedError) as exc_info:
        store.checkout_task(task.id, agent_id=a2.id, run_id=r2.id)

    # The task should still be owned by the first agent
    assert exc_info.value.locked_by_run_id == r1.id
    fresh = store.get_task(task.id)
    assert fresh is not None
    assert fresh.assigned_agent_id == a1.id
    assert fresh.execution_run_id == r1.id


def test_checkout_task_stale_lock_is_reclaimable(tmp_path):
    """After TASK_LOCK_TTL_SECS, a lock can be overridden by another checkout."""
    from gluon.models import TASK_LOCK_TTL_SECS

    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    a1 = store.create_agent(ws.id, "first")
    a2 = store.create_agent(ws.id, "second")
    task = store.create_task(project.id, "Stale lock")
    r1 = _make_run(store, project.id, "run-1")
    r2 = _make_run(store, project.id, "run-2")

    store.checkout_task(task.id, agent_id=a1.id, run_id=r1.id)

    # Forcibly rewind the lock timestamp to simulate a stale lock
    from datetime import timedelta

    past = utc_now() - timedelta(seconds=TASK_LOCK_TTL_SECS + 60)
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE orchestrator_tasks SET execution_locked_at = ? WHERE id = ?",
            (past.isoformat(), task.id),
        )

    # Second checkout should succeed (stale lock reclaimed)
    reclaimed = store.checkout_task(task.id, agent_id=a2.id, run_id=r2.id)
    assert reclaimed.assigned_agent_id == a2.id
    assert reclaimed.execution_run_id == r2.id


def test_checkout_task_not_found_raises(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(TaskNotFoundError):
        store.checkout_task("nope", agent_id=None, run_id="some-run")


def test_release_task_clears_lock_and_sets_status(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "worker")
    task = store.create_task(project.id, "Release me")
    run = _make_run(store, project.id)

    store.checkout_task(task.id, agent_id=agent.id, run_id=run.id)
    released = store.release_task(task.id, TaskStatus.DONE)

    assert released.status == TaskStatus.DONE
    assert released.execution_locked_at is None
    assert released.execution_run_id is None
    assert released.completed_at is not None


def test_release_task_not_done_does_not_stamp_completed_at(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "worker")
    task = store.create_task(project.id, "Back to review")
    run = _make_run(store, project.id)

    store.checkout_task(task.id, agent_id=agent.id, run_id=run.id)
    released = store.release_task(task.id, TaskStatus.REVIEW)

    assert released.status == TaskStatus.REVIEW
    assert released.completed_at is None


def test_release_task_not_found_raises(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(TaskNotFoundError):
        store.release_task("nope", TaskStatus.DONE)


def test_checkout_same_run_can_rewrite_its_own_lock(tmp_path):
    """A checkout with the same run ID should still fail if lock is fresh —
    this prevents double-execution even of the same run."""
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "worker")
    task = store.create_task(project.id, "Duplicate call")
    run = _make_run(store, project.id)

    store.checkout_task(task.id, agent_id=agent.id, run_id=run.id)

    # Second call within TTL should raise — protects against concurrent reentry
    with pytest.raises(TaskLockedError):
        store.checkout_task(task.id, agent_id=agent.id, run_id=run.id)


# ========== Agent inbox ==========


def test_get_agent_inbox_returns_assigned_and_in_progress(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "busy")

    # BACKLOG — not assigned to anyone, shouldn't appear
    store.create_task(project.id, "Backlog task")
    # ASSIGNED to agent
    t_assigned = store.create_task(project.id, "Assigned task", priority=3, assigned_agent_id=agent.id)
    # IN_PROGRESS for agent
    t_in_prog = store.create_task(project.id, "In progress", priority=9, assigned_agent_id=agent.id)
    run = _make_run(store, project.id)
    store.checkout_task(t_in_prog.id, agent_id=agent.id, run_id=run.id)
    # DONE — shouldn't appear
    t_done = store.create_task(project.id, "Done task", assigned_agent_id=agent.id)
    store.release_task(t_done.id, TaskStatus.DONE)

    inbox = store.get_agent_inbox(agent.id)

    # Priority DESC — in_progress (9) before assigned (3)
    assert len(inbox) == 2
    assert inbox[0].id == t_in_prog.id
    assert inbox[1].id == t_assigned.id


def test_get_agent_inbox_empty_when_no_tasks(tmp_path):
    store = _make_store(tmp_path)
    ws, _ = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "idle")

    assert store.get_agent_inbox(agent.id) == []


# ========== Comments ==========


def test_add_and_list_task_comments(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)
    task = store.create_task(project.id, "Chatty task")

    c1 = store.add_task_comment(task.id, "First comment", author_label="alice")
    c2 = store.add_task_comment(task.id, "Second comment", author_label="bob")

    comments = store.list_task_comments(task.id)
    assert len(comments) == 2
    assert comments[0].id == c1.id
    assert comments[0].content == "First comment"
    assert comments[0].author_label == "alice"
    assert comments[1].id == c2.id
    assert comments[1].author_label == "bob"


def test_task_comments_cascade_delete(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)
    task = store.create_task(project.id, "Has comments")
    store.add_task_comment(task.id, "A")
    store.add_task_comment(task.id, "B")

    store.delete_task(task.id)
    # Comments should be gone via CASCADE
    assert store.list_task_comments(task.id) == []


def test_task_comment_by_agent(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "commenter")
    task = store.create_task(project.id, "Agent-driven task")

    store.add_task_comment(
        task.id,
        "Blocked on deployment approval",
        author_agent_id=agent.id,
        author_label=agent.name,
    )

    comments = store.list_task_comments(task.id)
    assert len(comments) == 1
    assert comments[0].author_agent_id == agent.id
    assert comments[0].content == "Blocked on deployment approval"


# ========== Cascade behavior ==========


def test_project_delete_cascades_tasks(tmp_path):
    store = _make_store(tmp_path)
    _, project = _make_project(store, tmp_path)
    task = store.create_task(project.id, "Will be orphaned")

    store.delete_project(project.id)
    assert store.get_task(task.id) is None


def test_agent_delete_nulls_task_assignment(tmp_path):
    store = _make_store(tmp_path)
    ws, project = _make_project(store, tmp_path)
    agent = store.create_agent(ws.id, "departing")
    task = store.create_task(project.id, "Orphan agent", assigned_agent_id=agent.id)

    store.delete_agent(agent.id)
    fresh = store.get_task(task.id)
    assert fresh is not None
    assert fresh.assigned_agent_id is None


# ========== Error subclass behavior ==========


def test_concurrent_checkout_exactly_one_wins(tmp_path):
    """Fire two threads at the same task simultaneously; exactly one must succeed."""
    store_path = tmp_path / "contend.db"
    store_seed = GluonStore(db_path=store_path)
    ws, project = _make_project(store_seed, tmp_path)
    a1 = store_seed.create_agent(ws.id, "first")
    a2 = store_seed.create_agent(ws.id, "second")
    task = store_seed.create_task(project.id, "Racy task")
    r1 = _make_run(store_seed, project.id, "r1")
    r2 = _make_run(store_seed, project.id, "r2")

    start_barrier = threading.Barrier(2)
    results: dict[str, Exception | str] = {}

    def _checkout(name: str, agent_id: str, run_id: str):
        # Each thread uses its own store connection (SQLite doesn't share cursors across threads)
        local_store = GluonStore(db_path=store_path)
        start_barrier.wait()  # Release both threads at once
        try:
            locked = local_store.checkout_task(task.id, agent_id=agent_id, run_id=run_id)
            results[name] = f"locked by {locked.execution_run_id}"
        except Exception as e:
            results[name] = e

    t1 = threading.Thread(target=_checkout, args=("A", a1.id, r1.id))
    t2 = threading.Thread(target=_checkout, args=("B", a2.id, r2.id))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # Exactly one succeeded
    successes = [k for k, v in results.items() if isinstance(v, str)]
    failures = [k for k, v in results.items() if isinstance(v, TaskLockedError)]
    assert len(successes) == 1, f"expected 1 success, got {results}"
    assert len(failures) == 1, f"expected 1 lock failure, got {results}"

    # Verify the DB reflects exactly one winner
    fresh = store_seed.get_task(task.id)
    assert fresh is not None
    assert fresh.execution_run_id in (r1.id, r2.id)


def test_task_locked_error_has_useful_fields():
    err = TaskLockedError(
        task_id="abc123def456",
        locked_by_run_id="run9876543210",
        age_seconds=42.5,
    )
    assert err.task_id == "abc123def456"
    assert err.locked_by_run_id == "run9876543210"
    assert err.age_seconds == 42.5
    assert "abc123de" in str(err)  # ID truncated to 8 chars
    assert "42s" in str(err)
