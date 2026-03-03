"""Unit tests for pre_hydration.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from gluon.pre_hydration import HydrationContext, _read_readme_hint, format_context, hydrate


class TestReadmeHint:
    def test_reads_claude_md(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text("# My Project\nThis is a test project.")
        hint = _read_readme_hint(tmp_path)
        assert hint is not None
        assert "My Project" in hint

    def test_reads_readme_md_fallback(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Readme\nHello world.")
        hint = _read_readme_hint(tmp_path)
        assert hint is not None
        assert "Readme" in hint

    def test_claude_md_takes_precedence(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text("Claude instructions")
        (tmp_path / "README.md").write_text("Readme content")
        hint = _read_readme_hint(tmp_path)
        assert hint == "Claude instructions"

    def test_returns_none_when_no_readme(self, tmp_path: Path):
        assert _read_readme_hint(tmp_path) is None

    def test_truncates_long_content(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("x" * 500)
        hint = _read_readme_hint(tmp_path, max_chars=100)
        assert hint is not None
        assert len(hint) == 100

    def test_returns_none_for_empty_file(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("")
        assert _read_readme_hint(tmp_path) is None


class TestFormatContext:
    def test_basic_format(self):
        ctx = HydrationContext(
            git_log="abc123 initial commit",
            git_status="M src/main.py",
            project_type="python",
        )
        result = format_context(ctx)
        assert "[PROJECT CONTEXT]" in result
        assert "[END PROJECT CONTEXT]" in result
        assert "python" in result
        assert "abc123 initial commit" in result
        assert "M src/main.py" in result

    def test_includes_readme_hint(self):
        ctx = HydrationContext(
            git_log="(no git history)",
            git_status="(clean)",
            project_type="node",
            readme_hint="# My App",
        )
        result = format_context(ctx)
        assert "# My App" in result
        assert "Project hints:" in result

    def test_includes_last_failure(self):
        ctx = HydrationContext(
            git_log="(no git history)",
            git_status="(clean)",
            project_type="unknown",
            last_failure="SyntaxError: unexpected EOF",
        )
        result = format_context(ctx)
        assert "Previous attempt failed with:" in result
        assert "SyntaxError" in result

    def test_truncates_long_failure(self):
        ctx = HydrationContext(
            git_log="",
            git_status="",
            project_type="python",
            last_failure="x" * 3000,
        )
        result = format_context(ctx)
        # Failure should be truncated to 2000 chars
        assert len(ctx.last_failure) == 3000
        # The formatted result should contain the truncated version
        assert "x" * 2000 in result
        assert "x" * 2001 not in result


@pytest.mark.asyncio
class TestHydrate:
    async def test_hydrate_in_git_repo(self, tmp_path: Path):
        """Test hydration in an actual git repo."""
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        (tmp_path / "README.md").write_text("# Test")

        ctx = await hydrate(tmp_path)
        assert ctx.project_type == "unknown"  # no pyproject.toml or package.json
        assert ctx.readme_hint is not None
        assert ctx.last_failure is None

    async def test_hydrate_non_git_dir(self, tmp_path: Path):
        """Hydration should still work in non-git directories."""
        ctx = await hydrate(tmp_path)
        assert ctx.git_log == "(no git history)"
        assert ctx.git_status == "(clean)"

    async def test_hydrate_with_last_error(self, tmp_path: Path):
        ctx = await hydrate(tmp_path, last_error="test failure")
        assert ctx.last_failure == "test failure"

    async def test_hydrate_detects_python(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        ctx = await hydrate(tmp_path)
        assert ctx.project_type == "python"
