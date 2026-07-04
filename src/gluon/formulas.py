"""Workflow formula templates with variable substitution.

Formulas are reusable YAML workflow definitions that create TaskChains.
Supports variable substitution, DAG-based step dependencies, and
discovery from multiple search paths.
"""

import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FormulaVariable(BaseModel):
    """A variable declaration in a formula template."""

    name: str
    type: str = "string"
    required: bool = False
    default: str | None = None
    help: str | None = None


class FormulaStepDef(BaseModel):
    """A step definition in a formula template."""

    id: str
    name: str
    prompt: str  # Contains {{var}} placeholders
    depends_on: list[str] = Field(default_factory=list)
    profile: str = "standard"


class FormulaTemplate(BaseModel):
    """A reusable formula template.

    Two kinds (loop-engineering Phase 2 follow-up):
    - ``workflow`` (default): a DAG of steps instantiated as a TaskChain.
    - ``loop``: an agent-loop template — ``objective`` (with ``{{var}}``
      placeholders) plus loop config, instantiated as an AgentLoop
      (docs/design/agent-loops.md).
    """

    name: str
    kind: str = "workflow"  # "workflow" | "loop"
    description: str | None = None
    variables: list[FormulaVariable] = Field(default_factory=list)
    steps: list[FormulaStepDef] = Field(default_factory=list)
    use_worktree: bool = True
    source_path: Path | None = None
    # Loop-template fields (kind == "loop" only)
    objective: str | None = None  # Contains {{var}} placeholders
    verify_cmd: str | None = None
    agent_verifier: bool = False
    max_iterations: int = 20
    max_cost_usd: float | None = None
    profile: str = "standard"  # Profile for loop iterations


class FormulaLoader:
    """Discovers and loads formula templates from multiple search paths."""

    SEARCH_PATHS = ["~/.gluon/formulas", ".gluon/formulas"]
    BUILTIN_PATH = Path(__file__).parent / "formulas"

    @classmethod
    def discover(cls) -> list[FormulaTemplate]:
        """Discover all available formula templates."""
        templates: list[FormulaTemplate] = []
        seen_names: set[str] = set()

        # User paths first (can override builtins)
        for search_path in cls.SEARCH_PATHS:
            expanded = Path(search_path).expanduser()
            if expanded.is_dir():
                for f in sorted(expanded.glob("*.yml")):
                    try:
                        template = cls.load_from_file(f)
                        if template.name not in seen_names:
                            seen_names.add(template.name)
                            templates.append(template)
                    except Exception:
                        logger.debug("Failed to load formula %s", f, exc_info=True)

        # Builtin templates
        if cls.BUILTIN_PATH.is_dir():
            for f in sorted(cls.BUILTIN_PATH.glob("*.yml")):
                try:
                    template = cls.load_from_file(f)
                    if template.name not in seen_names:
                        seen_names.add(template.name)
                        templates.append(template)
                except Exception:
                    logger.debug("Failed to load builtin formula %s", f, exc_info=True)

        return templates

    @classmethod
    def load(cls, name: str) -> FormulaTemplate | None:
        """Load a formula template by name."""
        for template in cls.discover():
            if template.name == name:
                return template
        return None

    @classmethod
    def load_from_file(cls, path: Path) -> FormulaTemplate:
        """Load a formula template from a YAML file."""
        data = yaml.safe_load(path.read_text())

        variables = [FormulaVariable(**v) for v in data.get("variables", [])]
        steps = [FormulaStepDef(**s) for s in data.get("steps", [])]

        return FormulaTemplate(
            name=data["name"],
            kind=data.get("kind", "workflow"),
            description=data.get("description"),
            variables=variables,
            steps=steps,
            use_worktree=data.get("use_worktree", True),
            source_path=path,
            objective=data.get("objective"),
            verify_cmd=data.get("verify_cmd"),
            agent_verifier=bool(data.get("agent_verifier", False)),
            max_iterations=int(data.get("max_iterations", 20)),
            max_cost_usd=(float(data["max_cost_usd"]) if data.get("max_cost_usd") is not None else None),
            profile=data.get("profile", "standard"),
        )


def validate_formula(template: FormulaTemplate) -> list[str]:
    """Validate template: check cycles, missing deps, required vars. Returns errors."""
    errors: list[str] = []

    if template.kind not in ("workflow", "loop"):
        errors.append(f"Unknown formula kind '{template.kind}' (expected 'workflow' or 'loop')")
        return errors

    # Loop templates: an objective instead of steps (docs/design/agent-loops.md)
    if template.kind == "loop":
        if not (template.objective or "").strip():
            errors.append("Loop formula requires an 'objective'")
        if template.steps:
            errors.append("Loop formula must not define 'steps' (the agent authors iterations)")
        if template.max_iterations < 1:
            errors.append("max_iterations must be >= 1")
        return errors

    step_ids = {s.id for s in template.steps}

    # Check for empty steps
    if not template.steps:
        errors.append("Formula has no steps")
        return errors

    # Check for missing dependencies
    for step in template.steps:
        for dep_id in step.depends_on:
            if dep_id not in step_ids:
                errors.append(f"Step '{step.id}' depends on unknown step '{dep_id}'")

    # Check for cycles
    if not errors:
        visited: set[str] = set()
        in_stack: set[str] = set()
        step_map = {s.id: s for s in template.steps}

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

        for s in template.steps:
            if has_cycle(s.id):
                errors.append("Dependency cycle detected in formula")
                break

    # Check for duplicate step IDs
    if len(step_ids) != len(template.steps):
        errors.append("Duplicate step IDs found")

    return errors


def resolve_variables(template: FormulaTemplate, provided: dict[str, str]) -> dict[str, str]:
    """Merge provided vars with defaults, validate required. Raises ValueError."""
    resolved: dict[str, str] = {}

    for var in template.variables:
        if var.name in provided:
            resolved[var.name] = provided[var.name]
        elif var.default is not None:
            resolved[var.name] = var.default
        elif var.required:
            raise ValueError(f"Required variable '{var.name}' not provided")

    return resolved


def render_prompt(prompt: str, variables: dict[str, str]) -> str:
    """Replace {{var}} placeholders in prompt text."""
    result = prompt
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    # Remove any unreplaced optional placeholders
    result = re.sub(r"\{\{[^}]+\}\}", "", result)
    return result.strip()
