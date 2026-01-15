"""Slash command scanner for Claude Code commands and skills."""

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache TTL in seconds
CACHE_TTL_SECONDS = 60


@dataclass
class SlashCommand:
    """A Claude Code slash command or skill."""

    name: str
    type: str  # "command" or "skill"
    description: str
    argument_hint: str = ""


@dataclass
class CommandCache:
    """Cache for slash commands with TTL."""

    commands: list[SlashCommand] = field(default_factory=list)
    timestamp: float = 0.0

    def is_valid(self) -> bool:
        """Check if cache is still valid."""
        return time.time() - self.timestamp < CACHE_TTL_SECONDS


# Global cache instance
_cache = CommandCache()


def parse_frontmatter(content: str) -> dict[str, str]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Full markdown file content

    Returns:
        Dict with frontmatter fields (description, argument-hint, etc.)
    """
    # Match YAML frontmatter between --- markers
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    frontmatter = {}
    for line in match.group(1).split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            frontmatter[key] = value

    return frontmatter


def scan_commands_directory(commands_dir: Path) -> list[SlashCommand]:
    """Scan a commands directory for .md files.

    Args:
        commands_dir: Path to ~/.claude/commands/ or similar

    Returns:
        List of SlashCommand objects
    """
    commands: list[SlashCommand] = []

    if not commands_dir.exists():
        return commands

    for md_file in commands_dir.glob("*.md"):
        # Skip README files
        if md_file.name.lower() == "readme.md":
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
            frontmatter = parse_frontmatter(content)

            # Command name is filename without .md extension
            name = md_file.stem
            description = frontmatter.get("description", "")
            argument_hint = frontmatter.get("argument-hint", "")

            if description:  # Only include commands with descriptions
                commands.append(
                    SlashCommand(
                        name=name,
                        type="command",
                        description=description,
                        argument_hint=argument_hint,
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to parse command file {md_file}: {e}")

    return commands


def scan_skills_directory(skills_dir: Path) -> list[SlashCommand]:
    """Scan a skills directory for SKILL.md files in subdirectories.

    Args:
        skills_dir: Path to ~/.claude/skills/ or similar

    Returns:
        List of SlashCommand objects
    """
    commands: list[SlashCommand] = []

    if not skills_dir.exists():
        return commands

    for subdir in skills_dir.iterdir():
        if not subdir.is_dir():
            continue

        skill_file = subdir / "SKILL.md"
        if not skill_file.exists():
            continue

        try:
            content = skill_file.read_text(encoding="utf-8")
            frontmatter = parse_frontmatter(content)

            # Skill name can be from frontmatter 'name' or directory name
            name = frontmatter.get("name", subdir.name)
            description = frontmatter.get("description", "")
            argument_hint = frontmatter.get("argument-hint", "")

            if description:  # Only include skills with descriptions
                commands.append(
                    SlashCommand(
                        name=name,
                        type="skill",
                        description=description,
                        argument_hint=argument_hint,
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to parse skill file {skill_file}: {e}")

    return commands


def get_slash_commands(force_refresh: bool = False) -> list[SlashCommand]:
    """Get all available slash commands and skills.

    Scans ~/.claude/commands/ and ~/.claude/skills/ directories.
    Results are cached for 60 seconds.

    Args:
        force_refresh: If True, bypass cache and rescan directories

    Returns:
        List of SlashCommand objects sorted by name
    """
    global _cache

    if not force_refresh and _cache.is_valid():
        return _cache.commands

    commands: list[SlashCommand] = []
    claude_dir = Path.home() / ".claude"

    # Scan user commands
    commands_dir = claude_dir / "commands"
    commands.extend(scan_commands_directory(commands_dir))

    # Scan user skills
    skills_dir = claude_dir / "skills"
    commands.extend(scan_skills_directory(skills_dir))

    # Sort by name
    commands.sort(key=lambda c: c.name)

    # Update cache
    _cache = CommandCache(commands=commands, timestamp=time.time())

    logger.debug(f"Scanned {len(commands)} slash commands/skills")
    return commands


def search_commands(query: str) -> list[SlashCommand]:
    """Search commands by name prefix.

    Args:
        query: Search query (matched against command name prefix)

    Returns:
        List of matching SlashCommand objects
    """
    commands = get_slash_commands()
    query_lower = query.lower()

    return [c for c in commands if c.name.lower().startswith(query_lower)]
