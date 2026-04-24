"""Tests for the approval gate subsystem (Theme D1)."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gluon.approvals import (
    ApprovalDecision,
    _make_approval_hook,
    _wait_for_approval_decision,
    classify_tool_call,
)
from gluon.models import (
    ApprovalPolicy,
    ApprovalStatus,
)
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "approvals.db")


def _make_project(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    return store.create_project("proj", proj_path)


# ========== Classifier ==========


def test_permissive_never_gates():
    for tool in ("Bash", "Write", "Edit", "NotebookEdit", "MultiEdit", "Read", "Glob"):
        decision = classify_tool_call(ApprovalPolicy.PERMISSIVE, tool, {"command": "rm -rf /"})
        assert decision == ApprovalDecision(False, "")


def test_paranoid_gates_all_bash():
    decision = classify_tool_call(ApprovalPolicy.PARANOID, "Bash", {"command": "ls"})
    assert decision.needs_approval is True
    assert "PARANOID" in decision.reason


def test_paranoid_gates_all_writes():
    for tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        decision = classify_tool_call(ApprovalPolicy.PARANOID, tool, {"file_path": "/tmp/x"})
        assert decision.needs_approval is True
        assert "PARANOID" in decision.reason


def test_paranoid_ignores_read_only_tools():
    for tool in ("Read", "Glob", "Grep", "WebFetch"):
        decision = classify_tool_call(ApprovalPolicy.PARANOID, tool, {})
        assert decision.needs_approval is False


def test_careful_gates_rm_rf():
    for cmd in ("rm -rf /tmp/foo", "rm -fr /tmp/foo", "rm -rf -v /tmp", "rm -Rf /tmp"):
        decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": cmd})
        assert decision.needs_approval is True, f"should gate: {cmd}"
        assert "rm" in decision.reason.lower()


def test_careful_gates_git_force_push():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": "git push origin main --force"})
    assert decision.needs_approval is True
    assert "force" in decision.reason.lower()


def test_careful_gates_git_push_f_shorthand():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": "git push origin feature -f"})
    assert decision.needs_approval is True


def test_careful_gates_git_reset_hard():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": "git reset --hard HEAD~5"})
    assert decision.needs_approval is True
    assert "reset" in decision.reason.lower()


def test_careful_gates_npm_publish():
    for cmd in ("npm publish", "yarn publish", "pnpm publish", "uv publish"):
        decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": cmd})
        assert decision.needs_approval is True, f"should gate: {cmd}"


def test_careful_gates_sudo():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": "sudo apt-get update"})
    assert decision.needs_approval is True
    assert "sudo" in decision.reason.lower()


def test_careful_gates_pipe_to_shell():
    decision = classify_tool_call(
        ApprovalPolicy.CAREFUL,
        "Bash",
        {"command": "curl -fsSL https://install.sh | bash"},
    )
    assert decision.needs_approval is True


def test_careful_gates_chmod_777():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": "chmod 777 /etc/hosts"})
    assert decision.needs_approval is True


def test_careful_gates_terraform_apply_destroy():
    for cmd in ("terraform apply", "terraform destroy"):
        decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": cmd})
        assert decision.needs_approval is True, f"should gate: {cmd}"


def test_careful_gates_gh_pr_merge():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": "gh pr merge 123 --squash"})
    assert decision.needs_approval is True


def test_careful_allows_safe_bash():
    for cmd in ("ls -la", "cat README.md", "git status", "pytest", "echo hello"):
        decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {"command": cmd})
        assert decision.needs_approval is False, f"should not gate: {cmd}"


def test_careful_does_not_gate_writes():
    """CAREFUL is about destructive Bash — writes/edits pass through.
    Users who want to gate writes should use PARANOID."""
    for tool in ("Write", "Edit", "NotebookEdit"):
        decision = classify_tool_call(ApprovalPolicy.CAREFUL, tool, {"file_path": "/tmp/x"})
        assert decision.needs_approval is False


def test_careful_handles_missing_command_key():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", {})
    assert decision.needs_approval is False


def test_careful_handles_none_tool_input():
    decision = classify_tool_call(ApprovalPolicy.CAREFUL, "Bash", None)
    assert decision.needs_approval is False


# ========== Store CRUD ==========


def test_create_and_get_approval(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="test")

    approval = store.create_approval(
        run_id=run.id,
        tool_name="Bash",
        classification_reason="rm -rf",
        tool_input={"command": "rm -rf /tmp/foo"},
        tool_use_id="toolu_123",
    )

    assert approval.id
    assert approval.status == ApprovalStatus.PENDING
    assert approval.tool_input == {"command": "rm -rf /tmp/foo"}
    assert approval.tool_use_id == "toolu_123"

    fetched = store.get_approval(approval.id)
    assert fetched is not None
    assert fetched.id == approval.id

    # 8-char prefix lookup
    prefix_fetched = store.get_approval(approval.id[:8])
    assert prefix_fetched is not None
    assert prefix_fetched.id == approval.id


def test_get_approval_returns_none_for_unknown(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_approval("missing") is None


def test_list_approvals_filters(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run1 = store.create_run(project_id=project.id, prompt="a")
    run2 = store.create_run(project_id=project.id, prompt="b")

    a1 = store.create_approval(run_id=run1.id, tool_name="Bash", classification_reason="x")
    a2 = store.create_approval(run_id=run1.id, tool_name="Write", classification_reason="y")
    store.create_approval(run_id=run2.id, tool_name="Bash", classification_reason="z")

    # Decide one to change the count of PENDING
    store.decide_approval(a2.id, status=ApprovalStatus.GRANTED, decided_by="test")

    run1_approvals = store.list_approvals(run_id=run1.id)
    assert len(run1_approvals) == 2

    pending = store.list_approvals(status=ApprovalStatus.PENDING)
    assert len(pending) == 2  # a1 + a3 still pending
    assert a1.id in {a.id for a in pending}

    granted = store.list_approvals(status=ApprovalStatus.GRANTED)
    assert len(granted) == 1
    assert granted[0].id == a2.id


def test_decide_approval_happy_path(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    approval = store.create_approval(run_id=run.id, tool_name="Bash", classification_reason="x")

    updated = store.decide_approval(
        approval.id,
        status=ApprovalStatus.GRANTED,
        decided_by="cli",
        decision_reason="Looks safe",
    )
    assert updated is not None
    assert updated.status == ApprovalStatus.GRANTED
    assert updated.decided_by == "cli"
    assert updated.decision_reason == "Looks safe"
    assert updated.decided_at is not None


def test_decide_approval_idempotent_on_already_decided(tmp_path):
    """Deciding an already-decided approval should not overwrite the earlier decision."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    approval = store.create_approval(run_id=run.id, tool_name="Bash", classification_reason="x")

    first = store.decide_approval(approval.id, status=ApprovalStatus.GRANTED, decided_by="cli", decision_reason="first")
    assert first is not None

    second = store.decide_approval(
        approval.id, status=ApprovalStatus.DENIED, decided_by="cli", decision_reason="second"
    )
    assert second is not None
    # Must still reflect the first decision
    assert second.status == ApprovalStatus.GRANTED
    assert second.decision_reason == "first"


def test_expire_stale_approvals(tmp_path):
    from datetime import timedelta

    from gluon.models import utc_now

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    past = utc_now() - timedelta(minutes=10)
    future = utc_now() + timedelta(minutes=10)

    stale = store.create_approval(
        run_id=run.id,
        tool_name="Bash",
        classification_reason="x",
        timeout_at=past,
    )
    fresh = store.create_approval(
        run_id=run.id,
        tool_name="Bash",
        classification_reason="y",
        timeout_at=future,
    )
    no_timeout = store.create_approval(
        run_id=run.id,
        tool_name="Bash",
        classification_reason="z",
    )

    count = store.expire_stale_approvals()
    assert count == 1

    assert store.get_approval(stale.id).status == ApprovalStatus.EXPIRED  # type: ignore[union-attr]
    assert store.get_approval(fresh.id).status == ApprovalStatus.PENDING  # type: ignore[union-attr]
    assert store.get_approval(no_timeout.id).status == ApprovalStatus.PENDING  # type: ignore[union-attr]


def test_approvals_cascade_on_run_delete(tmp_path):
    """Deleting a run should cascade-delete its pending approvals."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    approval = store.create_approval(run_id=run.id, tool_name="Bash", classification_reason="x")

    # Delete the run directly via SQL (no public delete_run API)
    with store._get_conn() as conn:
        conn.execute("DELETE FROM execution_runs WHERE id = ?", (run.id,))

    assert store.get_approval(approval.id) is None


# ========== Wait helper ==========


@pytest.mark.anyio
async def test_wait_returns_status_when_decided_fast(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    approval = store.create_approval(run_id=run.id, tool_name="Bash", classification_reason="x")

    # Decide the approval after a tiny delay
    async def _grant_soon():
        await asyncio.sleep(0.05)
        store.decide_approval(approval.id, status=ApprovalStatus.GRANTED, decided_by="test")

    grant_task = asyncio.create_task(_grant_soon())
    try:
        status = await _wait_for_approval_decision(store, approval.id, timeout_secs=5, poll_interval=1)
    finally:
        await grant_task

    assert status == ApprovalStatus.GRANTED


@pytest.mark.anyio
async def test_wait_returns_expired_on_timeout(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    approval = store.create_approval(run_id=run.id, tool_name="Bash", classification_reason="x")

    # Use a very short timeout so the test runs fast
    status = await _wait_for_approval_decision(store, approval.id, timeout_secs=1, poll_interval=1)

    assert status == ApprovalStatus.EXPIRED
    refreshed = store.get_approval(approval.id)
    assert refreshed is not None
    assert refreshed.status == ApprovalStatus.EXPIRED
    assert refreshed.decided_by == "system:timeout"


# ========== PreToolUse hook ==========


@pytest.mark.anyio
async def test_hook_allows_when_classifier_says_safe(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    hook = _make_approval_hook(store, run.id, ApprovalPolicy.CAREFUL)

    result = await hook(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        "toolu_x",
        MagicMock(),
    )
    # Empty dict = allow (no permissionDecision field)
    assert "hookSpecificOutput" not in result

    # No approval record should have been created
    pending = store.list_approvals(run_id=run.id)
    assert len(pending) == 0


@pytest.mark.anyio
async def test_hook_blocks_and_times_out_for_risky_call(tmp_path):
    """A risky call with no external decider will time out and return deny."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    hook = _make_approval_hook(
        store,
        run.id,
        ApprovalPolicy.CAREFUL,
        timeout_secs=1,  # tiny so the test runs fast
    )

    result = await hook(
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/foo"}},
        "toolu_x",
        MagicMock(),
    )

    # Should have created an approval that's now EXPIRED
    approvals = store.list_approvals(run_id=run.id)
    assert len(approvals) == 1
    assert approvals[0].status == ApprovalStatus.EXPIRED

    # Hook should return a deny decision
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    assert "approval-gate" in result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


@pytest.mark.anyio
async def test_hook_allows_when_decider_grants(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    hook = _make_approval_hook(store, run.id, ApprovalPolicy.CAREFUL, timeout_secs=5)

    # Background task that grants the approval shortly after creation
    async def _grant_soon():
        await asyncio.sleep(0.1)
        approvals = store.list_approvals(run_id=run.id, status=ApprovalStatus.PENDING)
        assert approvals, "expected a pending approval"
        store.decide_approval(
            approvals[0].id,
            status=ApprovalStatus.GRANTED,
            decided_by="test",
            decision_reason="looks fine",
        )

    grant_task = asyncio.create_task(_grant_soon())
    try:
        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/foo"}},
            "toolu_x",
            MagicMock(),
        )
    finally:
        await grant_task

    # Allow returns an empty dict (no deny)
    assert "hookSpecificOutput" not in result

    # Approval should be GRANTED
    approvals = store.list_approvals(run_id=run.id)
    assert len(approvals) == 1
    assert approvals[0].status == ApprovalStatus.GRANTED


@pytest.mark.anyio
async def test_hook_denies_when_decider_denies(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    hook = _make_approval_hook(store, run.id, ApprovalPolicy.CAREFUL, timeout_secs=5)

    async def _deny_soon():
        await asyncio.sleep(0.1)
        approvals = store.list_approvals(run_id=run.id, status=ApprovalStatus.PENDING)
        store.decide_approval(
            approvals[0].id,
            status=ApprovalStatus.DENIED,
            decided_by="test",
            decision_reason="no way",
        )

    deny_task = asyncio.create_task(_deny_soon())
    try:
        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "git push --force"}},
            "toolu_x",
            MagicMock(),
        )
    finally:
        await deny_task

    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    assert "no way" in result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


@pytest.mark.anyio
async def test_hook_emits_message_callback_on_approval_request(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    messages_received: list = []

    def _callback(msg: dict) -> None:
        messages_received.append(msg)

    hook = _make_approval_hook(
        store,
        run.id,
        ApprovalPolicy.CAREFUL,
        timeout_secs=1,
        message_callback=_callback,
    )

    await hook(
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp"}},
        "toolu_x",
        MagicMock(),
    )

    assert len(messages_received) == 1
    assert messages_received[0]["type"] == "approval_requested"
    assert "rm" in messages_received[0]["content"].lower()
    assert messages_received[0]["metadata"]["tool_name"] == "Bash"


# ========== ExecutionRun integration ==========


def test_run_approval_policy_persists(tmp_path):
    """Creating a run with an approval_policy should read back correctly."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    run = store.create_run(
        project_id=project.id,
        prompt="x",
        approval_policy=ApprovalPolicy.CAREFUL,
    )
    assert run.approval_policy == ApprovalPolicy.CAREFUL

    fresh = store.get_run(run.id)
    assert fresh is not None
    assert fresh.approval_policy == ApprovalPolicy.CAREFUL


def test_run_approval_policy_defaults_to_permissive(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    run = store.create_run(project_id=project.id, prompt="x")
    assert run.approval_policy == ApprovalPolicy.PERMISSIVE
