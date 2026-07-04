"""Executes formulas: workflow templates become task chains; loop templates
become agent loops (loop-engineering Phase 2 — docs/design/agent-loops.md)."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gluon.formulas import FormulaTemplate, render_prompt, resolve_variables
from gluon.models import TaskChain, TaskProfile, TaskStep

if TYPE_CHECKING:
    from gluon.chain_executor import ChainExecutor
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormulaRunOutcome:
    """What instantiating a formula produced."""

    kind: str  # "workflow" | "loop"
    chain_id: str | None = None  # kind == "workflow"
    loop_id: str | None = None  # kind == "loop"
    step_count: int = 0  # steps for workflows; 1 (the seed) for loops


class FormulaExecutor:
    """Instantiates formula templates: task chains or agent loops by kind."""

    def __init__(self, store: "GluonStore", chain_executor: "ChainExecutor"):
        self.store = store
        self.chain_executor = chain_executor

    async def execute(
        self,
        template: FormulaTemplate,
        project_id: str,
        variables: dict[str, str],
        initiator: str | None = None,
    ) -> FormulaRunOutcome:
        """Instantiate a formula for a project. Routes by ``template.kind``."""
        # 1. Resolve variables
        resolved = resolve_variables(template, variables)

        # Loop templates: render the objective and create an AgentLoop; the
        # queue machinery dispatches the seed like any loop (no chain).
        if template.kind == "loop":
            from gluon.loop_manager import LoopManager

            # verify_cmd may be templated too (e.g. "{{verify}}"); an empty
            # render means gateless.
            rendered_verify = render_prompt(template.verify_cmd, resolved) if template.verify_cmd else None
            loop = LoopManager(self.store).create_loop(
                project_id=project_id,
                objective=render_prompt(template.objective or "", resolved),
                verify_cmd=rendered_verify or None,
                agent_verifier=template.agent_verifier,
                profile=template.profile,
                use_worktree=template.use_worktree,
                max_iterations=template.max_iterations,
                max_cost_usd=template.max_cost_usd,
                initiator=initiator or f"formula:{template.name}",
            )
            logger.info(
                "Formula '%s' created agent loop %s for project %s",
                template.name,
                loop.id,
                project_id,
            )
            return FormulaRunOutcome(kind="loop", loop_id=loop.id, step_count=1)

        # 2. Create the chain
        chain = TaskChain(
            project_id=project_id,
            name=f"formula:{template.name}",
            description=template.description,
            use_worktree=template.use_worktree,
            initiator=initiator,
        )
        chain = self.store.create_chain(chain)

        # 3. Create steps with rendered prompts and dependency mapping
        # Map step ID (from template) -> actual TaskStep ID (generated)
        step_id_map: dict[str, str] = {}

        for step_def in template.steps:
            rendered_prompt = render_prompt(step_def.prompt, resolved)
            depends_on = [step_id_map[dep] for dep in step_def.depends_on if dep in step_id_map]

            # Map profile string to TaskProfile enum
            try:
                profile = TaskProfile(step_def.profile)
            except ValueError:
                profile = TaskProfile.STANDARD

            step = TaskStep(
                chain_id=chain.id,
                name=step_def.name,
                prompt=rendered_prompt,
                depends_on=depends_on,
                profile=profile,
            )
            step = self.store.create_step(step)
            step_id_map[step_def.id] = step.id

        # 4. Start the chain
        # Reload chain with steps for validation
        chain.steps = self.store.list_steps(chain.id)
        await self.chain_executor.start_chain(chain.id)

        logger.info(
            "Formula '%s' started chain %s with %d steps for project %s",
            template.name,
            chain.id,
            len(template.steps),
            project_id,
        )
        return FormulaRunOutcome(kind="workflow", chain_id=chain.id, step_count=len(template.steps))
