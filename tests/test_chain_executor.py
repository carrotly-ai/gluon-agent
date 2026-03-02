"""Tests for ChainExecutor: DAG validation, step dispatch, and reactive completion."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.chain_executor import ChainExecutor
from gluon.models import (
    ChainStatus,
    RunStatus,
    StepStatus,
    TaskChain,
    TaskProfile,
    TaskStep,
    utc_now,
)
from gluon.store import GluonStore


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    return project_dir


def _make_chain(project_id: str, steps: list[TaskStep] | None = None) -> TaskChain:
    chain = TaskChain(
        project_id=project_id,
        name="test-chain",
        description="A test chain",
    )
    if steps:
        chain.steps = steps
    return chain


def _make_step(chain_id: str, name: str, depends_on: list[str] | None = None) -> TaskStep:
    return TaskStep(
        chain_id=chain_id,
        name=name,
        prompt=f"Do the {name} step",
        depends_on=depends_on or [],
    )


# ===================================================================
# DAG Validation
# ===================================================================


class TestValidateChain:
    def test_valid_linear_chain(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        s1 = _make_step(chain.id, "plan")
        s2 = _make_step(chain.id, "implement", depends_on=[s1.id])
        s3 = _make_step(chain.id, "test", depends_on=[s2.id])
        chain.steps = [s1, s2, s3]

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert errors == []

    def test_valid_diamond_dag(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        s1 = _make_step(chain.id, "plan")
        s2 = _make_step(chain.id, "impl-a", depends_on=[s1.id])
        s3 = _make_step(chain.id, "impl-b", depends_on=[s1.id])
        s4 = _make_step(chain.id, "test", depends_on=[s2.id, s3.id])
        chain.steps = [s1, s2, s3, s4]

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert errors == []

    def test_empty_chain_error(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        chain.steps = []

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert any("no steps" in e.lower() for e in errors)

    def test_missing_dependency_error(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        s1 = _make_step(chain.id, "implement", depends_on=["nonexistent-id"])
        chain.steps = [s1]

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert len(errors) == 1
        assert "unknown step ID" in errors[0]

    def test_cycle_detection(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        s1 = _make_step(chain.id, "step-a")
        s2 = _make_step(chain.id, "step-b")
        # Create cycle: s1 -> s2 -> s1
        s1.depends_on = [s2.id]
        s2.depends_on = [s1.id]
        chain.steps = [s1, s2]

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert any("cycle" in e.lower() for e in errors)

    def test_self_dependency_cycle(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        s1 = _make_step(chain.id, "self-dep")
        s1.depends_on = [s1.id]
        chain.steps = [s1]

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert any("cycle" in e.lower() for e in errors)

    def test_three_node_cycle(self, store: GluonStore, project_path: Path):
        """A → B → C → A should be detected as a cycle."""
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        sa = _make_step(chain.id, "a")
        sb = _make_step(chain.id, "b")
        sc = _make_step(chain.id, "c")
        sa.depends_on = [sc.id]
        sb.depends_on = [sa.id]
        sc.depends_on = [sb.id]
        chain.steps = [sa, sb, sc]

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert any("cycle" in e.lower() for e in errors)

    def test_no_dependencies_valid(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = _make_chain(project.id)
        s1 = _make_step(chain.id, "parallel-a")
        s2 = _make_step(chain.id, "parallel-b")
        chain.steps = [s1, s2]

        executor = ChainExecutor(store=store, runner=MagicMock())
        errors = executor.validate_chain(chain)
        assert errors == []


# ===================================================================
# Store: Chain + Step CRUD
# ===================================================================


class TestStoreChainCRUD:
    def test_create_and_get_chain(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="my-chain",
            description="Test chain",
        )
        created = store.create_chain(chain)
        assert created.id == chain.id

        retrieved = store.get_chain(chain.id)
        assert retrieved is not None
        assert retrieved.name == "my-chain"
        assert retrieved.status == ChainStatus.PENDING

    def test_get_nonexistent_chain(self, store: GluonStore):
        assert store.get_chain("nonexistent") is None

    def test_list_chains(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        c1 = TaskChain(project_id=project.id, name="chain-1")
        c2 = TaskChain(project_id=project.id, name="chain-2")
        store.create_chain(c1)
        store.create_chain(c2)

        chains = store.list_chains(project_id=project.id)
        assert len(chains) == 2

    def test_list_chains_filter_by_status(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        c1 = TaskChain(project_id=project.id, name="chain-pending")
        c2 = TaskChain(project_id=project.id, name="chain-running", status=ChainStatus.RUNNING)
        store.create_chain(c1)
        store.create_chain(c2)

        pending = store.list_chains(status=ChainStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].name == "chain-pending"

    def test_chain_use_worktree_roundtrip(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="wt-chain", use_worktree=True)
        store.create_chain(chain)

        retrieved = store.get_chain(chain.id)
        assert retrieved is not None
        assert retrieved.use_worktree is True

    def test_chain_initiator_roundtrip(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="init-chain", initiator="telegram:12345")
        store.create_chain(chain)

        retrieved = store.get_chain(chain.id)
        assert retrieved is not None
        assert retrieved.initiator == "telegram:12345"

    def test_chain_description_roundtrip(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="desc-chain",
            description="Add Clerk authentication to the app",
        )
        store.create_chain(chain)

        retrieved = store.get_chain(chain.id)
        assert retrieved is not None
        assert retrieved.description == "Add Clerk authentication to the app"

    def test_list_chains_no_filters(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        c1 = TaskChain(project_id=project.id, name="chain-1")
        c2 = TaskChain(project_id=project.id, name="chain-2", status=ChainStatus.RUNNING)
        store.create_chain(c1)
        store.create_chain(c2)

        all_chains = store.list_chains()
        assert len(all_chains) >= 2

    def test_update_chain(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="updatable")
        store.create_chain(chain)

        chain.status = ChainStatus.RUNNING
        chain.started_at = utc_now()
        store.update_chain(chain)

        retrieved = store.get_chain(chain.id)
        assert retrieved is not None
        assert retrieved.status == ChainStatus.RUNNING
        assert retrieved.started_at is not None


class TestStoreStepCRUD:
    def test_create_and_get_step(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        step = TaskStep(chain_id=chain.id, name="plan", prompt="Plan the work")
        created = store.create_step(step)
        assert created.id == step.id

        retrieved = store.get_step(step.id)
        assert retrieved is not None
        assert retrieved.name == "plan"
        assert retrieved.status == StepStatus.PENDING

    def test_get_nonexistent_step(self, store: GluonStore):
        assert store.get_step("nonexistent") is None

    def test_list_steps(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="plan", prompt="Plan")
        s2 = TaskStep(chain_id=chain.id, name="implement", prompt="Implement")
        store.create_step(s1)
        store.create_step(s2)

        steps = store.list_steps(chain.id)
        assert len(steps) == 2

    def test_update_step(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        step = TaskStep(chain_id=chain.id, name="plan", prompt="Plan")
        store.create_step(step)

        step.status = StepStatus.RUNNING
        step.started_at = utc_now()
        store.update_step(step)

        retrieved = store.get_step(step.id)
        assert retrieved is not None
        assert retrieved.status == StepStatus.RUNNING

    def test_step_depends_on_roundtrip(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="plan", prompt="Plan")
        store.create_step(s1)
        s2 = TaskStep(
            chain_id=chain.id,
            name="implement",
            prompt="Impl",
            depends_on=[s1.id],
        )
        store.create_step(s2)

        retrieved = store.get_step(s2.id)
        assert retrieved is not None
        assert retrieved.depends_on == [s1.id]

    def test_step_error_message_roundtrip(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        step = TaskStep(chain_id=chain.id, name="failing", prompt="Fail")
        store.create_step(step)
        step.status = StepStatus.FAILED
        step.error_message = "Connection refused"
        step.completed_at = utc_now()
        store.update_step(step)

        retrieved = store.get_step(step.id)
        assert retrieved is not None
        assert retrieved.error_message == "Connection refused"
        assert retrieved.completed_at is not None

    def test_step_profile_roundtrip(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        step = TaskStep(
            chain_id=chain.id,
            name="review",
            prompt="Review code",
            profile=TaskProfile.REVIEW,
        )
        store.create_step(step)

        retrieved = store.get_step(step.id)
        assert retrieved is not None
        assert retrieved.profile == TaskProfile.REVIEW


# ===================================================================
# get_ready_steps
# ===================================================================


class TestGetReadySteps:
    def test_all_independent_steps_ready(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="a", prompt="A")
        s2 = TaskStep(chain_id=chain.id, name="b", prompt="B")
        store.create_step(s1)
        store.create_step(s2)

        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 2

    def test_blocked_steps_not_ready(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="plan", prompt="Plan")
        store.create_step(s1)
        s2 = TaskStep(chain_id=chain.id, name="implement", prompt="Impl", depends_on=[s1.id])
        store.create_step(s2)

        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 1
        assert ready[0].id == s1.id

    def test_dependency_completed_unblocks(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="plan", prompt="Plan")
        store.create_step(s1)
        s2 = TaskStep(chain_id=chain.id, name="implement", prompt="Impl", depends_on=[s1.id])
        store.create_step(s2)

        # Complete s1
        s1.status = StepStatus.COMPLETED
        store.update_step(s1)

        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 1
        assert ready[0].id == s2.id

    def test_running_step_not_ready(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="running", prompt="Already running")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 0

    def test_diamond_dependency(self, store: GluonStore, project_path: Path):
        """Test diamond DAG: A -> B, A -> C, B+C -> D."""
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        sa = TaskStep(chain_id=chain.id, name="a", prompt="A")
        store.create_step(sa)
        sb = TaskStep(chain_id=chain.id, name="b", prompt="B", depends_on=[sa.id])
        store.create_step(sb)
        sc = TaskStep(chain_id=chain.id, name="c", prompt="C", depends_on=[sa.id])
        store.create_step(sc)
        sd = TaskStep(chain_id=chain.id, name="d", prompt="D", depends_on=[sb.id, sc.id])
        store.create_step(sd)

        # Initially only A is ready
        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 1
        assert ready[0].id == sa.id

        # Complete A → B and C become ready
        sa.status = StepStatus.COMPLETED
        store.update_step(sa)
        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 2
        ready_ids = {s.id for s in ready}
        assert sb.id in ready_ids
        assert sc.id in ready_ids

        # Complete B only → D still blocked (needs C)
        sb.status = StepStatus.COMPLETED
        store.update_step(sb)
        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 1
        assert ready[0].id == sc.id

        # Complete C → D becomes ready
        sc.status = StepStatus.COMPLETED
        store.update_step(sc)
        ready = store.get_ready_steps(chain.id)
        assert len(ready) == 1
        assert ready[0].id == sd.id


# ===================================================================
# ChainExecutor: start_chain
# ===================================================================


class TestChainExecutorStartChain:
    @pytest.mark.asyncio
    async def test_start_chain_dispatches_ready_steps(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="plan", prompt="Plan the work")
        store.create_step(s1)

        # Create a real run in the store so FK constraints are satisfied
        real_run = store.create_run(project.id, "Plan the work")
        mock_runner = AsyncMock()
        mock_runner.submit = AsyncMock(return_value=real_run)

        executor = ChainExecutor(store=store, runner=mock_runner)
        await executor.start_chain(chain.id)

        updated_chain = store.get_chain(chain.id)
        assert updated_chain is not None
        assert updated_chain.status == ChainStatus.RUNNING
        mock_runner.submit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_nonexistent_chain_raises(self, store: GluonStore):
        executor = ChainExecutor(store=store, runner=MagicMock())
        with pytest.raises(ValueError, match="Chain not found"):
            await executor.start_chain("nonexistent")

    @pytest.mark.asyncio
    async def test_start_invalid_chain_raises(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="empty-chain")
        store.create_chain(chain)
        # No steps → invalid

        executor = ChainExecutor(store=store, runner=MagicMock())
        with pytest.raises(ValueError, match="Invalid chain"):
            await executor.start_chain(chain.id)


# ===================================================================
# ChainExecutor: on_step_completed
# ===================================================================


class TestChainExecutorOnStepCompleted:
    @pytest.mark.asyncio
    async def test_step_completed_marks_step(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="only-step", prompt="Do it")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.on_step_completed(chain.id, s1.id)

        updated = store.get_step(s1.id)
        assert updated is not None
        assert updated.status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_all_steps_complete_marks_chain_completed(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="test-chain",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="only-step", prompt="Do it")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.on_step_completed(chain.id, s1.id)

        updated_chain = store.get_chain(chain.id)
        assert updated_chain is not None
        assert updated_chain.status == ChainStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_nonexistent_step_no_crash(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        store.create_chain(chain)

        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.on_step_completed(chain.id, "nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_reactive_dispatch_next_step(self, store: GluonStore, project_path: Path):
        """Completing step 1 should auto-dispatch step 2 (core F6 pattern)."""
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="reactive-chain",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="plan", prompt="Plan")
        store.create_step(s1)
        s2 = TaskStep(chain_id=chain.id, name="implement", prompt="Implement", depends_on=[s1.id])
        store.create_step(s2)

        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        # Mock runner returns a real run for FK satisfaction
        real_run = store.create_run(project.id, "Implement")
        mock_runner = AsyncMock()
        mock_runner.submit = AsyncMock(return_value=real_run)

        executor = ChainExecutor(store=store, runner=mock_runner)
        await executor.on_step_completed(chain.id, s1.id)

        # s2 should have been dispatched
        mock_runner.submit.assert_awaited_once()
        updated_s2 = store.get_step(s2.id)
        assert updated_s2 is not None
        assert updated_s2.status == StepStatus.RUNNING

    @pytest.mark.asyncio
    async def test_chain_completion_notifies(self, store: GluonStore, project_path: Path):
        """Chain completion should call notifier.notify_chain_completed."""
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="notify-chain",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="only-step", prompt="Do it")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        notifier = AsyncMock()
        executor = ChainExecutor(store=store, runner=MagicMock(), notifier=notifier)
        await executor.on_step_completed(chain.id, s1.id)

        notifier.notify_chain_completed.assert_awaited_once()
        call_kwargs = notifier.notify_chain_completed.call_args[1]
        assert call_kwargs["chain_name"] == "notify-chain"
        assert call_kwargs["total_steps"] == 1
        assert call_kwargs["completed_steps"] == 1

    @pytest.mark.asyncio
    async def test_chain_completion_notifier_failure_swallowed(self, store: GluonStore, project_path: Path):
        """If notifier raises, chain should still be marked completed."""
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="fail-notify-chain",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="only-step", prompt="Do it")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        notifier = AsyncMock()
        notifier.notify_chain_completed = AsyncMock(side_effect=Exception("Network error"))
        executor = ChainExecutor(store=store, runner=MagicMock(), notifier=notifier)
        await executor.on_step_completed(chain.id, s1.id)

        updated = store.get_chain(chain.id)
        assert updated is not None
        assert updated.status == ChainStatus.COMPLETED


# ===================================================================
# ChainExecutor: on_step_failed
# ===================================================================


class TestChainExecutorOnStepFailed:
    @pytest.mark.asyncio
    async def test_step_failed_marks_chain_failed(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="test-chain",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="failing-step", prompt="Will fail")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.on_step_failed(chain.id, s1.id, "Something went wrong")

        updated_step = store.get_step(s1.id)
        assert updated_step is not None
        assert updated_step.status == StepStatus.FAILED
        assert updated_step.error_message == "Something went wrong"

        updated_chain = store.get_chain(chain.id)
        assert updated_chain is not None
        assert updated_chain.status == ChainStatus.FAILED


# ===================================================================
# ChainExecutor: cancel_chain
# ===================================================================


class TestChainExecutorCancelChain:
    @pytest.mark.asyncio
    async def test_cancel_marks_chain_cancelled(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="test-chain",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="pending-step", prompt="Not started")
        store.create_step(s1)

        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.cancel_chain(chain.id)

        updated_chain = store.get_chain(chain.id)
        assert updated_chain is not None
        assert updated_chain.status == ChainStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_marks_pending_steps_skipped(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="cancel-chain",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="running", prompt="Running step")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        store.update_step(s1)

        s2 = TaskStep(chain_id=chain.id, name="pending", prompt="Pending step", depends_on=[s1.id])
        store.create_step(s2)

        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.cancel_chain(chain.id)

        updated_s1 = store.get_step(s1.id)
        assert updated_s1 is not None
        assert updated_s1.status == StepStatus.SKIPPED
        assert updated_s1.completed_at is not None

        updated_s2 = store.get_step(s2.id)
        assert updated_s2 is not None
        assert updated_s2.status == StepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_cancel_running_step_cancels_linked_run(self, store: GluonStore, project_path: Path):
        """A RUNNING step with a linked run should have its run cancelled."""
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="cancel-linked",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        # Create a run that the step is linked to
        linked_run = store.create_run(project.id, "linked task")
        linked_run.status = RunStatus.RUNNING
        store.update_run(linked_run)

        s1 = TaskStep(chain_id=chain.id, name="running", prompt="In progress")
        store.create_step(s1)
        s1.status = StepStatus.RUNNING
        s1.run_id = linked_run.id
        store.update_step(s1)

        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.cancel_chain(chain.id)

        # The linked run should be cancelled
        updated_run = store.get_run(linked_run.id)
        assert updated_run is not None
        assert updated_run.status == RunStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_chain(self, store: GluonStore):
        executor = ChainExecutor(store=store, runner=MagicMock())
        await executor.cancel_chain("nonexistent")  # Should not raise


# ===================================================================
# ChainExecutor: _build_step_prompt
# ===================================================================


class TestBuildStepPrompt:
    def test_includes_step_prompt(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="test-chain",
            description="Build a feature",
        )
        step = TaskStep(chain_id=chain.id, name="plan", prompt="Create the plan")
        chain.steps = [step]

        executor = ChainExecutor(store=store, runner=MagicMock())
        prompt = executor._build_step_prompt(chain, step)

        assert "Create the plan" in prompt
        assert "plan" in prompt.lower()

    def test_includes_chain_description(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="test-chain",
            description="Build user authentication",
        )
        step = TaskStep(chain_id=chain.id, name="plan", prompt="Plan it")
        chain.steps = [step]

        executor = ChainExecutor(store=store, runner=MagicMock())
        prompt = executor._build_step_prompt(chain, step)

        assert "Build user authentication" in prompt

    def test_includes_completed_step_summaries(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="test-chain")
        s1 = TaskStep(chain_id=chain.id, name="plan", prompt="Plan the work", status=StepStatus.COMPLETED)
        s2 = TaskStep(chain_id=chain.id, name="implement", prompt="Implement it")
        chain.steps = [s1, s2]

        executor = ChainExecutor(store=store, runner=MagicMock())
        prompt = executor._build_step_prompt(chain, s2)

        assert "Completed Steps" in prompt
        assert "plan" in prompt

    def test_no_description(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="no-desc", description=None)
        step = TaskStep(chain_id=chain.id, name="plan", prompt="Plan it")
        chain.steps = [step]

        executor = ChainExecutor(store=store, runner=MagicMock())
        prompt = executor._build_step_prompt(chain, step)

        assert "Chain Context" not in prompt
        assert "Plan it" in prompt

    def test_no_completed_steps(self, store: GluonStore, project_path: Path):
        project = store.create_project("test", project_path)
        chain = TaskChain(project_id=project.id, name="fresh")
        step = TaskStep(chain_id=chain.id, name="first", prompt="Do first thing")
        chain.steps = [step]

        executor = ChainExecutor(store=store, runner=MagicMock())
        prompt = executor._build_step_prompt(chain, step)

        assert "Completed Steps" not in prompt
        assert "Do first thing" in prompt


# ===================================================================
# ChainExecutor: _dispatch_ready_steps error handling
# ===================================================================


class TestDispatchReadyStepsErrors:
    @pytest.mark.asyncio
    async def test_runner_submit_failure_marks_step_and_chain_failed(self, store: GluonStore, project_path: Path):
        """If runner.submit raises, step and chain should be marked FAILED."""
        project = store.create_project("test", project_path)
        chain = TaskChain(
            project_id=project.id,
            name="dispatch-fail",
            status=ChainStatus.RUNNING,
        )
        store.create_chain(chain)

        s1 = TaskStep(chain_id=chain.id, name="will-fail", prompt="Submit fails")
        store.create_step(s1)

        mock_runner = AsyncMock()
        mock_runner.submit = AsyncMock(side_effect=RuntimeError("No Claude process available"))

        executor = ChainExecutor(store=store, runner=mock_runner)
        await executor._dispatch_ready_steps(chain.id)

        updated_step = store.get_step(s1.id)
        assert updated_step is not None
        assert updated_step.status == StepStatus.FAILED
        assert "No Claude process available" in (updated_step.error_message or "")

        updated_chain = store.get_chain(chain.id)
        assert updated_chain is not None
        assert updated_chain.status == ChainStatus.FAILED
