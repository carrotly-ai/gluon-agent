"""Unit tests for project_detector.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from gluon.project_detector import (
    ProjectType,
    ToolCommands,
    detect_project_type,
    get_autofix_command,
    get_tool_commands,
)


class TestDetectProjectType:
    def test_python_project(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        assert detect_project_type(tmp_path) == ProjectType.PYTHON

    def test_node_project(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "foo"}')
        assert detect_project_type(tmp_path) == ProjectType.NODE

    def test_unknown_project(self, tmp_path: Path):
        assert detect_project_type(tmp_path) == ProjectType.UNKNOWN

    def test_python_takes_precedence_over_node(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}")
        assert detect_project_type(tmp_path) == ProjectType.PYTHON


class TestGetToolCommands:
    def test_python_defaults(self):
        cmds = get_tool_commands(ProjectType.PYTHON)
        assert cmds.lint == "ruff check ."
        assert cmds.test == "uv run pytest"

    def test_node_defaults(self):
        cmds = get_tool_commands(ProjectType.NODE)
        assert cmds.lint == "npx biome check ."
        assert cmds.test == "bun test"

    def test_unknown_returns_none(self):
        cmds = get_tool_commands(ProjectType.UNKNOWN)
        assert cmds.lint is None
        assert cmds.test is None

    def test_lint_override(self):
        cmds = get_tool_commands(ProjectType.PYTHON, lint_override="mypy .")
        assert cmds.lint == "mypy ."
        assert cmds.test == "uv run pytest"  # default preserved

    def test_test_override(self):
        cmds = get_tool_commands(ProjectType.NODE, test_override="npm test")
        assert cmds.lint == "npx biome check ."
        assert cmds.test == "npm test"

    def test_both_overrides(self):
        cmds = get_tool_commands(ProjectType.UNKNOWN, lint_override="eslint .", test_override="jest")
        assert cmds.lint == "eslint ."
        assert cmds.test == "jest"

    def test_frozen_dataclass(self):
        cmds = ToolCommands(lint="x", test="y")
        with pytest.raises(AttributeError):
            cmds.lint = "z"  # type: ignore[misc]


class TestGetAutofixCommand:
    def test_python_autofix(self):
        cmd = get_autofix_command(ProjectType.PYTHON)
        assert cmd is not None
        assert "ruff format" in cmd
        assert "ruff check" in cmd
        assert "--fix" in cmd

    def test_node_autofix(self):
        cmd = get_autofix_command(ProjectType.NODE)
        assert cmd is not None
        assert "biome" in cmd
        assert "--write" in cmd

    def test_unknown_no_autofix(self):
        cmd = get_autofix_command(ProjectType.UNKNOWN)
        assert cmd is None
