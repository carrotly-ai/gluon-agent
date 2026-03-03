"""Executes workflow formulas by creating and starting task chains."""

import logging
from typing import TYPE_CHECKING

from gluon.formulas import FormulaTemplate, render_prompt, resolve_variables
from gluon.models import TaskChain, TaskProfile, TaskStep

if TYPE_CHECKING:
    from gluon.chain_executor import ChainExecutor
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


class FormulaExecutor:
    """Creates and starts task chains from formula templates."""

    def __init__(self, store: "GluonStore", chain_executor: "ChainExecutor"):
        self.store = store
        self.chain_executor = chain_executor

    async def execute(
        self,
        template: FormulaTemplate,
        project_id: str,
        variables: dict[str, str],
        initiator: str | None = None,
    ) -> str:
        """Create and start a TaskChain from template. Returns chain_id."""
        # 1. Resolve variables
        resolved = resolve_variables(template, variables)

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
        return chain.id
