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
    autonomy: str = "L3"  # L1 report-only / L2 assisted (plan approval) / L3 unattended
    model: str | None = None  # Judgment model (surveyor / verifier / fixes)
    executor_model: str | None = None  # Cheaper model for mechanical fan-out tasks
    agent_verifier_model: str | None = None  # Cross-family judge model for the verifier
    watch_cmd: str | None = None  # Event-reactive loop: re-seed from this command when idle
    # SKU operating metadata (loop-hardening Phase F4) — informational; powers
    # `gluon loop cost`, create-time defaults, and cost-anomaly alarming. Does
    # not affect execution.
    cadence: str | None = None  # e.g. "5m-15m", "1d" — how often the pattern is meant to run
    risk: str | None = None  # low | medium | high
    human_gates: list[str] = Field(default_factory=list)  # named gates (security, payments, ...)
    week_one_autonomy: str | None = None  # suggested starting autonomy (usually L1)
    cost_model: dict[str, float] = Field(default_factory=dict)  # tokens_noop/report/action, suggested_daily_cap


def lint_loop_formula(t: "FormulaTemplate") -> list[dict[str, str]]:
    """Static design-rubric checks for a loop formula (loop-hardening Phase F9).

    Ported from the loop-engineering/Looper anti-pattern rubrics, adapted to our
    schema. Returns a list of ``{severity, check, message}``; ``error`` means the
    loop won't behave as it reads, ``warning`` is design coaching. Only meaningful
    for ``kind == "loop"`` templates.
    """
    findings: list[dict[str, str]] = []

    def add(sev: str, check: str, msg: str) -> None:
        findings.append({"severity": sev, "check": check, "message": msg})

    if t.kind != "loop":
        return findings

    # all-vibe verification: neither a deterministic gate nor an independent
    # verifier — nothing objective can block a false completion.
    if not (t.verify_cmd and t.verify_cmd.strip()) and not t.agent_verifier and not t.agent_verifier_model:
        add(
            "warning",
            "all-vibe-verification",
            "no verify_cmd AND no agent_verifier — completion rests on the agent's word alone; "
            "add a programmatic gate or enable an independent verifier",
        )

    # missing budget cap: an unattended loop with no cost ceiling can run away.
    if t.max_cost_usd is None:
        add("warning", "no-cost-cap", "no max_cost_usd — set a spend ceiling so the loop can't run away")

    # missing iteration cap sanity (schema defaults to 20, but 0/negative is a footgun).
    if t.max_iterations is not None and t.max_iterations < 1:
        add("error", "bad-max-iterations", f"max_iterations must be >= 1 (got {t.max_iterations})")

    # unattended default without a verifier: L3 with no independent check is the
    # "L3 before L1 quality" anti-pattern.
    rendered_autonomy = (t.autonomy or "L3").strip()
    if rendered_autonomy == "L3" and not t.agent_verifier and not t.agent_verifier_model and not t.verify_cmd:
        add(
            "warning",
            "unattended-without-verifier",
            "autonomy L3 (unattended) with no verifier or gate — start at L1/L2 and earn L3 on evidence",
        )

    # same-family verifier (best-effort family sniff on the model id prefix).
    if t.agent_verifier_model and t.model:

        def _family(m: str) -> str:
            m = m.lower()
            for fam in ("claude", "opus", "sonnet", "haiku", "gpt", "codex", "gemini", "qwen", "llama", "deepseek"):
                if fam in m:
                    return "anthropic" if fam in ("claude", "opus", "sonnet", "haiku") else fam
            return m

        if _family(t.agent_verifier_model) == _family(t.model):
            add(
                "warning",
                "same-family-verifier",
                f"verifier model ({t.agent_verifier_model}) shares a family with the host ({t.model}); "
                "a different family closes self-grading blind spots",
            )

    # unresolved placeholder in a non-templated field that should have been a variable.
    for fieldname, value in (("objective", t.objective), ("watch_cmd", t.watch_cmd), ("verify_cmd", t.verify_cmd)):
        if value and "{{" in value:
            declared = {v.name for v in t.variables}
            used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", value))
            missing = used - declared
            if missing:
                add(
                    "error",
                    "undeclared-placeholder",
                    f"{fieldname} references undeclared variable(s) {sorted(missing)} — "
                    "add them to `variables` or they will render empty",
                )

    return findings


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
            autonomy=str(data.get("autonomy", "L3")),  # may be templated; rendered at run
            model=data.get("model"),
            executor_model=data.get("executor_model"),
            agent_verifier_model=data.get("agent_verifier_model"),
            watch_cmd=data.get("watch_cmd"),  # may be templated; rendered at run
            cadence=data.get("cadence"),
            risk=data.get("risk"),
            human_gates=list(data.get("human_gates", []) or []),
            week_one_autonomy=data.get("week_one_autonomy"),
            cost_model={k: float(v) for k, v in (data.get("cost_model") or {}).items()},
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
