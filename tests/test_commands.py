"""Unit tests for slash command scanning and parsing.

Tests frontmatter parsing, command/skill directory scanning,
caching, and search.
"""

from __future__ import annotations

from gluon.commands import (
    CommandCache,
    SlashCommand,
    parse_frontmatter,
    scan_commands_directory,
    scan_skills_directory,
)

# ===================================================================
# parse_frontmatter
# ===================================================================


class TestParseFrontmatter:
    def test_basic_frontmatter(self):
        content = """---
description: Do something useful
argument-hint: <name>
---
Body content here.
"""
        result = parse_frontmatter(content)
        assert result["description"] == "Do something useful"
        assert result["argument-hint"] == "<name>"

    def test_no_frontmatter(self):
        content = "Just some plain text with no frontmatter."
        assert parse_frontmatter(content) == {}

    def test_empty_frontmatter(self):
        content = """---

---
Body.
"""
        assert parse_frontmatter(content) == {}

    def test_quoted_values(self):
        content = """---
description: "Quoted value"
name: 'single quoted'
---
"""
        result = parse_frontmatter(content)
        assert result["description"] == "Quoted value"
        assert result["name"] == "single quoted"

    def test_multiline_body_not_captured(self):
        content = """---
description: First line only
---
This is body content.
extra: not a frontmatter key
"""
        result = parse_frontmatter(content)
        assert "extra" not in result
        assert result["description"] == "First line only"

    def test_colon_in_value(self):
        content = """---
description: Key: value with colon
---
"""
        result = parse_frontmatter(content)
        assert result["description"] == "Key: value with colon"


# ===================================================================
# scan_commands_directory
# ===================================================================


class TestScanCommandsDirectory:
    def test_scan_empty_directory(self, tmp_path):
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        assert scan_commands_directory(commands_dir) == []

    def test_scan_nonexistent_directory(self, tmp_path):
        assert scan_commands_directory(tmp_path / "nope") == []

    def test_scan_with_commands(self, tmp_path):
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()

        (commands_dir / "deploy.md").write_text(
            """---
description: Deploy the app
argument-hint: <env>
---
Deploy to the specified environment.
"""
        )
        (commands_dir / "test.md").write_text(
            """---
description: Run test suite
---
Run all tests.
"""
        )
        result = scan_commands_directory(commands_dir)
        assert len(result) == 2
        names = {c.name for c in result}
        assert names == {"deploy", "test"}
        deploy = next(c for c in result if c.name == "deploy")
        assert deploy.type == "command"
        assert deploy.argument_hint == "<env>"

    def test_skips_readme(self, tmp_path):
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "README.md").write_text("# Commands\nThis is a readme.")
        (commands_dir / "real.md").write_text("---\ndescription: A real command\n---\n")

        result = scan_commands_directory(commands_dir)
        assert len(result) == 1
        assert result[0].name == "real"

    def test_skips_no_description(self, tmp_path):
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "nodesc.md").write_text("---\nname: nodesc\n---\nNo description.")

        result = scan_commands_directory(commands_dir)
        assert len(result) == 0

    def test_handles_malformed_file(self, tmp_path):
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        # A binary file with .md extension
        (commands_dir / "bad.md").write_bytes(b"\x00\x01\x02\x03")
        # A good command
        (commands_dir / "good.md").write_text("---\ndescription: Good command\n---\n")

        result = scan_commands_directory(commands_dir)
        assert len(result) == 1
        assert result[0].name == "good"


# ===================================================================
# scan_skills_directory
# ===================================================================


class TestScanSkillsDirectory:
    def test_scan_empty_directory(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        assert scan_skills_directory(skills_dir) == []

    def test_scan_nonexistent_directory(self, tmp_path):
        assert scan_skills_directory(tmp_path / "nope") == []

    def test_scan_with_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_a = skills_dir / "my-skill"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text(
            """---
name: my-skill
description: A custom skill
---
Skill content.
"""
        )

        result = scan_skills_directory(skills_dir)
        assert len(result) == 1
        assert result[0].name == "my-skill"
        assert result[0].type == "skill"
        assert result[0].description == "A custom skill"

    def test_falls_back_to_dir_name(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "fallback-name"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
description: Uses directory name
---
"""
        )

        result = scan_skills_directory(skills_dir)
        assert len(result) == 1
        assert result[0].name == "fallback-name"

    def test_skips_file_in_skills_dir(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "not-a-dir.md").write_text("Not a skill directory")

        result = scan_skills_directory(skills_dir)
        assert len(result) == 0

    def test_skips_dir_without_skill_md(self, tmp_path):
        skills_dir = tmp_path / "skills"
        (skills_dir / "orphan").mkdir(parents=True)
        (skills_dir / "orphan" / "README.md").write_text("Not a SKILL.md")

        result = scan_skills_directory(skills_dir)
        assert len(result) == 0


# ===================================================================
# CommandCache
# ===================================================================


class TestCommandCache:
    def test_empty_cache_invalid(self):
        cache = CommandCache()
        assert cache.is_valid() is False

    def test_fresh_cache_valid(self):
        import time

        cache = CommandCache(
            commands=[SlashCommand("test", "command", "Desc")],
            timestamp=time.time(),
        )
        assert cache.is_valid() is True

    def test_expired_cache_invalid(self):
        import time

        cache = CommandCache(
            commands=[SlashCommand("test", "command", "Desc")],
            timestamp=time.time() - 120,  # 2 minutes ago, TTL is 60s
        )
        assert cache.is_valid() is False
