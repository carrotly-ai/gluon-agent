"""Task chain executor with DAG-based dependency resolution.

Manages multi-step task chains where steps can depend on other steps.
Uses reactive dispatch: when a step completes, the next ready steps
are automatically dispatched.
"""

import logging
from typing import TYPE_CHECKING

from gluon.models import ChainStatus, StepStatus, TaskChain, TaskStep, utc_now

if TYPE_CHECKING:
    from gluon.notifier import NotificationDispatcher
    from gluon.runner import TaskRunner
    from gluon.store import GluonStore
    from gluon.web.websocket import WebSocketManager

logger = logging.getLogger(__name__)


class ChainExecutor:
    """Executes task chains with DAG-based dependency resolution."""

    def __init__(
        self,
        store: "GluonStore",
        runner: "TaskRunner",
        notifier: "NotificationDispatcher | None" = None,
        ws_manager: "WebSocketManager | None" = None,
    ):
        self.store = store
        self.runner = runner
        self.notifier = notifier
        self.ws_manager = ws_manager

    def validate_chain(self, chain: TaskChain) -> list[str]:
        """Validate DAG: check for cycles, missing deps. Returns error list."""
        errors: list[str] = []
        step_ids = {s.id for s in chain.steps}

        # Check for missing dependencies
        for step in chain.steps:
            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    errors.append(f"Step '{step.name}' depends on unknown step ID '{dep_id}'")

        # Check for cycles via topological sort
        if not errors:
            visited: set[str] = set()
            in_stack: set[str] = set()
            step_map = {s.id: s for s in chain.steps}

            def has_cycle(step_id: str) -> bool:
                if step_id in in_stack:
                    return True
                if step_id in visited:
                    return False
                visited.add(step_id)
                in_stack.add(step_id)
                step = step_map.get(step_id)
                if step:
                    for dep_id in step.depends_on:
                        if has_cycle(dep_id):
                            return True
                in_stack.discard(step_id)
                return False

            for s in chain.steps:
                if has_cycle(s.id):
                    errors.append("Dependency cycle detected in chain")
                    break

        # Check for empty chain
        if not chain.steps:
            errors.append("Chain has no steps")

        return errors

    async def start_chain(self, chain_id: str) -> None:
        """Start a chain by dispatching all initially-ready steps."""
        chain = self.store.get_chain(chain_id)
        if not chain:
            raise ValueError(f"Chain not found: {chain_id}")

        errors = self.validate_chain(chain)
        if errors:
            raise ValueError(f"Invalid chain: {'; '.join(errors)}")

        chain.status = ChainStatus.RUNNING
        chain.started_at = utc_now()
        self.store.update_chain(chain)
        logger.info("Started chain %s (%s) with %d steps", chain.id, chain.name, len(chain.steps))

        try:
            from gluon.activity_log import ActivityLogger

            ActivityLogger(self.store).log(
                actor=chain.id,
                action="chain_started",
                message=chain.name,
                metadata={"project_id": chain.project_id, "step_count": len(chain.steps)},
            )
        except Exception:
            pass

        await self._dispatch_ready_steps(chain_id)

    async def on_step_completed(self, chain_id: str, step_id: str) -> None:
        """Called when a step's run completes. Dispatches next ready steps."""
        step = self.store.get_step(step_id)
        if not step:
            return

        step.status = StepStatus.COMPLETED
        step.completed_at = utc_now()
        self.store.update_step(step)
        logger.info("Step %s (%s) completed in chain %s", step.id, step.name, chain_id)

        # Broadcast step completion progress
        if self.ws_manager:
            try:
                chain = self.store.get_chain(chain_id)
                if chain and chain.run_id:
                    all_steps = self.store.list_steps(chain_id)
                    step_index = next((i for i, s in enumerate(all_steps) if s.id == step.id), 0)
                    await self.ws_manager.broadcast_step_progress(
                        run_id=chain.run_id,
                        step_name=step.name,
                        step_index=step_index,
                        total_steps=len(all_steps),
                        step_status="completed",
                    )
            except Exception:
                logger.debug("Failed to broadcast step completion", exc_info=True)

        try:
            from gluon.activity_log import ActivityLogger

            ActivityLogger(self.store).log(
                actor=chain_id,
                action="step_completed",
                result="success",
                message=step.name,
                metadata={"step_id": step.id, "chain_id": chain_id},
            )
        except Exception:
            pass

        # Check if chain is complete
        all_steps = self.store.list_steps(chain_id)
        if all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in all_steps):
            chain = self.store.get_chain(chain_id)
            if chain:
                chain.status = ChainStatus.COMPLETED
                chain.completed_at = utc_now()
                self.store.update_chain(chain)
                logger.info("Chain %s (%s) completed", chain.id, chain.name)

                if self.notifier:
                    completed = sum(1 for s in all_steps if s.status == StepStatus.COMPLETED)
                    try:
                        await self.notifier.notify_chain_completed(
                            chain_id=chain.id,
                            chain_name=chain.name,
                            project_id=chain.project_id,
                            total_steps=len(all_steps),
                            completed_steps=completed,
                        )
                    except Exception:
                        logger.debug("Chain completion notification failed", exc_info=True)
            return

        await self._dispatch_ready_steps(chain_id)

    async def on_step_failed(self, chain_id: str, step_id: str, error: str) -> None:
        """Called when a step's run fails. Marks chain as failed."""
        step = self.store.get_step(step_id)
        if not step:
            return

        step.status = StepStatus.FAILED
        step.error_message = error
        step.completed_at = utc_now()
        self.store.update_step(step)
        logger.warning("Step %s (%s) failed in chain %s: %s", step.id, step.name, chain_id, error)

        try:
            from gluon.activity_log import ActivityLogger

            ActivityLogger(self.store).log(
                actor=chain_id,
                action="step_failed",
                result="failed",
                message=f"{step.name}: {error[:200]}",
                metadata={"step_id": step.id, "chain_id": chain_id},
            )
        except Exception:
            pass

        chain = self.store.get_chain(chain_id)
        if chain:
            chain.status = ChainStatus.FAILED
            chain.completed_at = utc_now()
            self.store.update_chain(chain)
            logger.info("Chain %s (%s) marked FAILED due to step %s", chain.id, chain.name, step.name)

    async def cancel_chain(self, chain_id: str) -> None:
        """Cancel a chain and all its running steps."""
        chain = self.store.get_chain(chain_id)
        if not chain:
            return

        steps = self.store.list_steps(chain_id)
        for step in steps:
            if step.status in (StepStatus.PENDING, StepStatus.READY, StepStatus.RUNNING):
                was_running = step.status == StepStatus.RUNNING
                step.status = StepStatus.SKIPPED
                step.completed_at = utc_now()
                self.store.update_step(step)

                # Cancel linked run if it was running
                if step.run_id and was_running:
                    run = self.store.get_run(step.run_id)
                    if run and run.is_active:
                        run.mark_cancelled()
                        self.store.update_run(run)

        chain.status = ChainStatus.CANCELLED
        chain.completed_at = utc_now()
        self.store.update_chain(chain)
        logger.info("Cancelled chain %s (%s)", chain.id, chain.name)

    async def _dispatch_ready_steps(self, chain_id: str) -> None:
        """Find and dispatch all steps whose dependencies are met.

        Uses a unified run model: the first step creates a new ExecutionRun,
        and subsequent steps resume the same run via resume_in_place(). This
        produces a single Kanban card for the entire formula.
        """
        ready = self.store.get_ready_steps(chain_id)
        if not ready:
            return

        chain = self.store.get_chain(chain_id)
        if not chain:
            return

        for step in ready:
            step.status = StepStatus.RUNNING
            step.started_at = utc_now()
            self.store.update_step(step)

            prompt = self._build_step_prompt(chain, step)
            had_run_id = chain.run_id is not None
            try:
                if chain.run_id:
                    # Subsequent step: resume the existing run with the new prompt
                    run = await self.runner.resume_in_place(
                        run_id=chain.run_id,
                        new_prompt=prompt,
                        wait=True,
                        initiator=f"chain:{chain.id}:step:{step.name}",
                        fresh_session=True,
                    )
                else:
                    # First step: create a new run
                    from gluon.models import resolve_task_options

                    task_options = resolve_task_options(profile=step.profile.value)
                    model = task_options["model"]

                    run = await self.runner.submit(
                        project_id=chain.project_id,
                        prompt=prompt,
                        model=model,
                        use_worktree=chain.use_worktree,
                        initiator=chain.initiator or f"chain:{chain.id}",
                        profile=step.profile.value,
                    )
                    # Track run_id on chain for subsequent steps
                    chain.run_id = run.id
                    self.store.update_chain(chain)

                # Link run to chain/step
                run.chain_id = chain.id
                run.step_id = step.id
                if run.metadata is None:
                    run.metadata = {}
                run.metadata["chain_id"] = chain.id
                run.metadata["step_name"] = step.name
                run.metadata["profile"] = step.profile.value
                self.store.update_run(run)

                step.run_id = run.id
                self.store.update_step(step)
                logger.info(
                    "Dispatched step %s (%s) as run %s (resume=%s)",
                    step.id,
                    step.name,
                    run.id[:8],
                    had_run_id,
                )

                # Broadcast to WebSocket so Kanban updates in real-time
                if self.ws_manager:
                    try:
                        project = self.store.get_project(chain.project_id)
                        if project:
                            if not had_run_id:
                                await self.ws_manager.broadcast_run_created(run, project.name)
                            else:
                                await self.ws_manager.broadcast_run_update(run, project.name)
                            # Broadcast step progress
                            all_steps = self.store.list_steps(chain_id)
                            step_index = next((i for i, s in enumerate(all_steps) if s.id == step.id), 0)
                            await self.ws_manager.broadcast_step_progress(
                                run_id=run.id,
                                step_name=step.name,
                                step_index=step_index,
                                total_steps=len(all_steps),
                                step_status="running",
                            )
                    except Exception:
                        logger.debug("Failed to broadcast formula step", exc_info=True)
            except Exception as e:
                step.status = StepStatus.FAILED
                step.error_message = str(e)
                step.completed_at = utc_now()
                self.store.update_step(step)
                logger.error("Failed to dispatch step %s: %s", step.name, e)

                # Fail the chain
                chain.status = ChainStatus.FAILED
                chain.completed_at = utc_now()
                self.store.update_chain(chain)

    def _build_step_prompt(self, chain: TaskChain, step: TaskStep) -> str:
        """Build prompt with context from completed steps."""
        parts: list[str] = []

        # Chain context
        if chain.description:
            parts.append(f"## Chain Context\n{chain.description}\n")

        # Completed step summaries
        completed_steps = [s for s in chain.steps if s.status == StepStatus.COMPLETED]
        if completed_steps:
            parts.append("## Completed Steps")
            for cs in completed_steps:
                parts.append(f"- **{cs.name}**: {cs.prompt[:100]}")
            parts.append("")

        # Current step
        parts.append(f"## Current Step: {step.name}\n{step.prompt}")

        return "\n".join(parts)
