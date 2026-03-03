"""Project type detection and tool command resolution.

Detects project type from filesystem markers and returns appropriate
lint/test/format commands. No LLM calls — purely deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ProjectType(str, Enum):
    PYTHON = "python"
    NODE = "node"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ToolCommands:
    lint: str | None = None
    test: str | None = None
    format: str | None = None


_COMMANDS: dict[ProjectType, ToolCommands] = {
    ProjectType.PYTHON: ToolCommands(
        lint="ruff check .",
        test="uv run pytest",
        format="ruff format --check .",
    ),
    ProjectType.NODE: ToolCommands(
        lint="npx biome check .",
        test="bun test",
        format=None,
    ),
    ProjectType.UNKNOWN: ToolCommands(),
}


def detect_project_type(project_path: Path) -> ProjectType:
    """Detect project type from filesystem markers.

    Checks pyproject.toml -> PYTHON, package.json -> NODE, else UNKNOWN.
    """
    if (project_path / "pyproject.toml").exists():
        return ProjectType.PYTHON
    if (project_path / "package.json").exists():
        return ProjectType.NODE
    return ProjectType.UNKNOWN


def get_tool_commands(
    project_type: ProjectType,
    *,
    lint_override: str | None = None,
    test_override: str | None = None,
) -> ToolCommands:
    """Return lint/test commands for a project type.

    Overrides take precedence over auto-detected defaults.
    """
    defaults = _COMMANDS.get(project_type, ToolCommands())
    return ToolCommands(
        lint=lint_override or defaults.lint,
        test=test_override or defaults.test,
        format=defaults.format,
    )
