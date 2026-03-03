"""Pre-hydration: gather deterministic project context before agent starts.

Collects git state, project type, and readme hints via async subprocess
calls and formats them as a markdown block to prepend to the agent prompt.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from gluon.project_detector import detect_project_type

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 5  # seconds per git command


@dataclass
class HydrationContext:
    git_log: str
    git_status: str
    project_type: str
    readme_hint: str | None = None
    last_failure: str | None = None


async def _run_cmd(cmd: list[str], cwd: Path) -> str:
    """Run a subprocess and return stdout, or empty string on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_SUBPROCESS_TIMEOUT)
        return stdout.decode().strip() if stdout else ""
    except (TimeoutError, FileNotFoundError, OSError) as e:
        logger.debug("Hydration command %s failed: %s", cmd, e)
        return ""


def _read_readme_hint(project_path: Path, max_chars: int = 300) -> str | None:
    """Read first N chars of CLAUDE.md or README.md."""
    for name in ("CLAUDE.md", "README.md"):
        readme = project_path / name
        if readme.exists():
            try:
                text = readme.read_text(encoding="utf-8")[:max_chars]
                return text.strip() or None
            except OSError:
                continue
    return None


async def hydrate(working_dir: Path, last_error: str | None = None) -> HydrationContext:
    """Gather project context via async subprocess calls.

    Target: <3 seconds total (commands run in parallel).
    """
    git_log_task = _run_cmd(["git", "log", "--oneline", "-10"], working_dir)
    git_status_task = _run_cmd(["git", "status", "--short"], working_dir)

    git_log, git_status = await asyncio.gather(git_log_task, git_status_task)

    project_type = detect_project_type(working_dir)
    readme_hint = _read_readme_hint(working_dir)

    return HydrationContext(
        git_log=git_log or "(no git history)",
        git_status=git_status or "(clean)",
        project_type=project_type.value,
        readme_hint=readme_hint,
        last_failure=last_error,
    )


def format_context(ctx: HydrationContext) -> str:
    """Format hydration context as a markdown block to prepend to prompt."""
    parts = [
        "[PROJECT CONTEXT]",
        f"Project type: {ctx.project_type}",
        "",
        "Recent commits:",
        ctx.git_log,
        "",
        "Working tree status:",
        ctx.git_status,
    ]

    if ctx.readme_hint:
        parts.extend(["", "Project hints:", ctx.readme_hint])

    if ctx.last_failure:
        # Truncate to keep prompt reasonable
        failure_text = ctx.last_failure[:2000]
        parts.extend(["", "Previous attempt failed with:", failure_text])

    parts.append("[END PROJECT CONTEXT]")
    return "\n".join(parts)
