"""Repo-local binding constraints for loops (loop-hardening Phase F1).

An operator drops a ``.gluon/constraints.md`` (or legacy ``loop-constraints.md``)
into a project. Two layers, matching the loop-engineering convention but with
teeth — Gluon does not merely *ask* an agent to obey:

- **Prompt layer:** the constraints text is injected verbatim into every loop
  iteration prompt at dispatch (see runner ``_submit_from_queue_item``), so the
  agent sees the rules on every run.
- **Mechanical layer:** the parsed path DENYLIST is enforced by the harness —
  worktree merge-back refuses to integrate a branch that touches a denylisted
  path (``loop_integration``), independent of whether the agent read the file.
  The default denylist applies even when no constraints file exists, so secrets
  are protected by default (this also closes audit finding #9: merge-back's
  ``git add -A`` could otherwise commit ``.env`` onto the source branch).

Deterministic + dependency-free; safe to call on every dispatch/merge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

# Applied for EVERY loop even with no constraints file present. Secrets/creds
# must never be integrated by an autonomous loop.
DEFAULT_DENYLIST: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "secrets/**",
    "auth/**",
    "payments/**",
    "credentials/**",
    "**/*.key",
    "**/*.pem",
)

_CONSTRAINTS_FILENAMES = (".gluon/constraints.md", "loop-constraints.md")
_DENYLIST_HEADING_HINTS = ("path", "denylist", "deny list", "never edit", "never touch", "forbidden")


@dataclass(frozen=True)
class LoopConstraints:
    text: str = ""  # full markdown, injected into prompts ("" when no file)
    denylist: tuple[str, ...] = DEFAULT_DENYLIST
    source: str | None = None  # which file it came from, or None for defaults

    @property
    def has_file(self) -> bool:
        return self.source is not None


def _extract_denylist_globs(text: str) -> list[str]:
    """Pull path globs out of the constraints markdown.

    Best-effort: collect bullet lines that live under a path/denylist-flavored
    heading and that contain a backticked token or a glob-ish path. We look for
    backticked tokens first (the loop-engineering convention writes
    `` `.env` ``, `` `secrets/` ``), else fall back to bare path-looking tokens.
    """
    globs: list[str] = []
    in_path_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or line.startswith("##"):
            in_path_section = any(h in line.lower() for h in _DENYLIST_HEADING_HINTS)
            continue
        if not in_path_section or not line.startswith(("-", "*")):
            continue
        # Collect every backticked token on the bullet line.
        for token in re.findall(r"`([^`]+)`", line):
            token = token.strip().strip(",")
            if not token or token.startswith("--") or " " in token:
                continue
            # A directory like `secrets/` becomes a recursive glob.
            if token.endswith("/"):
                globs.append(token + "**")
            else:
                globs.append(token)
    return globs


def load_constraints(project_path: Path) -> LoopConstraints:
    """Load a project's loop constraints, or the safe defaults if none exist."""
    for name in _CONSTRAINTS_FILENAMES:
        candidate = project_path / name
        try:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8", errors="replace")
            else:
                continue
        except OSError:
            continue
        extra = _extract_denylist_globs(text)
        # File-declared globs EXTEND the defaults — an operator can add, not
        # subtract, the secret-protecting floor.
        denylist = tuple(dict.fromkeys((*DEFAULT_DENYLIST, *extra)))
        return LoopConstraints(text=text.strip(), denylist=denylist, source=name)
    return LoopConstraints()


def path_matches_denylist(rel_path: str, denylist: tuple[str, ...]) -> bool:
    """True if a repo-relative path matches any denylist glob."""
    p = rel_path.strip()
    if p.startswith("./"):  # strip a leading "./" prefix WITHOUT eating a leading dot (.env!)
        p = p[2:]
    name = PurePosixPath(p).name
    for pattern in denylist:
        if fnmatch(p, pattern):
            return True
        # A bare pattern with no slash also matches by basename at any depth
        # (`.env` should catch `config/.env`).
        if "/" not in pattern and fnmatch(name, pattern):
            return True
        # `**/foo` should also match a top-level `foo` (fnmatch's `**/` needs a
        # slash), so `**/*.key` catches `id_rsa.key` too.
        if pattern.startswith("**/") and fnmatch(name, pattern[3:]):
            return True
        # `dir/**` matches the directory's contents at any depth.
        if pattern.endswith("/**"):
            base = pattern[:-3]
            if p == base or p.startswith(base + "/"):
                return True
    return False


def denylisted_paths(rel_paths: list[str], denylist: tuple[str, ...]) -> list[str]:
    """Subset of ``rel_paths`` that hit the denylist."""
    return [p for p in rel_paths if path_matches_denylist(p, denylist)]


_PROMPT_HEADER = "[PROJECT LOOP CONSTRAINTS — BINDING, read before every action]"


def constraints_prompt_block(project_path: Path) -> str:
    """A prompt block to inject into loop iterations, or "" when there is no
    constraints file (the default denylist is enforced mechanically regardless,
    so an empty block is correct — nothing extra to tell the agent)."""
    c = load_constraints(project_path)
    if not c.text:
        return ""
    return f"\n\n{_PROMPT_HEADER}\n{c.text}\n[END CONSTRAINTS — violations are blocked by the harness]\n"


__all__ = [
    "DEFAULT_DENYLIST",
    "LoopConstraints",
    "load_constraints",
    "path_matches_denylist",
    "denylisted_paths",
    "constraints_prompt_block",
]
