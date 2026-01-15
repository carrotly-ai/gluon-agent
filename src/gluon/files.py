"""File scanner for project file autocomplete (@mentions).

Scans whitelisted directories within a project for files and directories,
with caching to avoid repeated disk I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

# Default directories to scan (whitelisted for performance)
DEFAULT_SCAN_PATHS = [
    ".",  # Root files only
    "src",
    "tests",
    "scripts",
    "docs",
    "public",
    "e2e",
    "lib",
    "app",  # Next.js app router
    "pages",  # Next.js pages router
    "components",
    "hooks",
    "utils",
    "services",
    "api",
    "config",
    "types",
    # Monorepo / workspace patterns
    "web-ui",
    "web-ui/src",
    "packages",
    "apps",
    "frontend",
    "backend",
    "client",
    "server",
]

# Directories/patterns to always exclude
ALWAYS_EXCLUDE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    ".next",
    ".nuxt",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    "out",
    ".output",
    "coverage",
    ".coverage",
    ".idea",
    ".vscode",
    ".turbo",
    ".vercel",
    ".cache",
    "target",  # Rust
    "vendor",  # Go
}

# File patterns to always exclude
ALWAYS_EXCLUDE_PATTERNS = {
    "*.pyc",
    "*.pyo",
    "*.egg-info",
    "*.log",
    ".DS_Store",
    "Thumbs.db",
    "*.swp",
    "*.swo",
    ".env*",
}


@dataclass
class ProjectFile:
    """A file or directory in a project."""

    path: str  # Relative path from project root
    type: str  # "file" or "directory"


@dataclass
class FileCache:
    """Cached file list for a project."""

    files: list[ProjectFile]
    timestamp: float
    project_path: str


# Global cache: project_id -> FileCache
_file_cache: dict[str, FileCache] = {}
CACHE_TTL_SECONDS = 30.0


def _should_exclude_dir(name: str) -> bool:
    """Check if a directory should be excluded."""
    return name in ALWAYS_EXCLUDE_DIRS


def _should_exclude_file(name: str) -> bool:
    """Check if a file should be excluded."""
    for pattern in ALWAYS_EXCLUDE_PATTERNS:
        if fnmatch(name, pattern):
            return True
    return False


def _scan_directory(
    base_path: Path,
    relative_base: str,
    max_depth: int = 4,
    current_depth: int = 0,
) -> list[ProjectFile]:
    """Recursively scan a directory for files.

    Args:
        base_path: Absolute path to scan
        relative_base: Relative path prefix for results
        max_depth: Maximum recursion depth
        current_depth: Current recursion depth
    """
    results: list[ProjectFile] = []

    if not base_path.exists() or not base_path.is_dir():
        return results

    if current_depth > max_depth:
        return results

    try:
        for entry in sorted(base_path.iterdir()):
            name = entry.name

            if entry.is_dir():
                if _should_exclude_dir(name):
                    continue

                rel_path = f"{relative_base}/{name}" if relative_base else name
                results.append(ProjectFile(path=rel_path, type="directory"))

                # Recurse into directory
                results.extend(
                    _scan_directory(
                        entry,
                        rel_path,
                        max_depth,
                        current_depth + 1,
                    )
                )
            elif entry.is_file():
                if _should_exclude_file(name):
                    continue

                rel_path = f"{relative_base}/{name}" if relative_base else name
                results.append(ProjectFile(path=rel_path, type="file"))

    except PermissionError:
        pass  # Skip directories we can't read

    return results


def scan_project_files(
    project_path: Path,
    scan_paths: list[str] | None = None,
) -> list[ProjectFile]:
    """Scan whitelisted directories in a project for files.

    Args:
        project_path: Absolute path to project root
        scan_paths: List of paths to scan (relative to project root).
                   If None, uses DEFAULT_SCAN_PATHS.

    Returns:
        Sorted list of ProjectFile objects
    """
    if scan_paths is None:
        scan_paths = DEFAULT_SCAN_PATHS

    all_files: list[ProjectFile] = []
    seen_paths: set[str] = set()

    for scan_path in scan_paths:
        if scan_path == ".":
            # Root level: only scan immediate files, not directories
            try:
                for entry in project_path.iterdir():
                    if entry.is_file() and not _should_exclude_file(entry.name):
                        if entry.name not in seen_paths:
                            seen_paths.add(entry.name)
                            all_files.append(ProjectFile(path=entry.name, type="file"))
            except PermissionError:
                pass
        else:
            target_path = project_path / scan_path
            if target_path.exists() and target_path.is_dir():
                # Add the directory itself
                if scan_path not in seen_paths:
                    seen_paths.add(scan_path)
                    all_files.append(ProjectFile(path=scan_path, type="directory"))

                # Scan contents
                for pf in _scan_directory(target_path, scan_path):
                    if pf.path not in seen_paths:
                        seen_paths.add(pf.path)
                        all_files.append(pf)

    # Sort: directories first, then alphabetically
    all_files.sort(key=lambda f: (f.type != "directory", f.path.lower()))

    return all_files


def get_project_files(
    project_id: str,
    project_path: Path,
    prefix: str = "",
    limit: int = 50,
    force_refresh: bool = False,
) -> tuple[list[ProjectFile], bool]:
    """Get files for a project with caching and filtering.

    Args:
        project_id: Unique project identifier (for caching)
        project_path: Absolute path to project root
        prefix: Filter files starting with this path prefix
        limit: Maximum number of results to return
        force_refresh: Force cache refresh

    Returns:
        Tuple of (filtered file list, truncated flag)
    """
    global _file_cache

    cache_entry = _file_cache.get(project_id)
    now = time.time()

    # Check if cache is valid
    if (
        cache_entry is not None
        and not force_refresh
        and (now - cache_entry.timestamp) < CACHE_TTL_SECONDS
        and cache_entry.project_path == str(project_path)
    ):
        files = cache_entry.files
    else:
        # Scan and cache
        files = scan_project_files(project_path)
        _file_cache[project_id] = FileCache(
            files=files,
            timestamp=now,
            project_path=str(project_path),
        )

    # Filter by prefix
    if prefix:
        prefix_lower = prefix.lower()
        files = [f for f in files if f.path.lower().startswith(prefix_lower)]

    # Check if truncated and limit
    truncated = len(files) > limit
    files = files[:limit]

    return files, truncated


def clear_cache(project_id: str | None = None) -> None:
    """Clear file cache.

    Args:
        project_id: If provided, only clear cache for this project.
                   If None, clear entire cache.
    """
    global _file_cache

    if project_id is None:
        _file_cache.clear()
    elif project_id in _file_cache:
        del _file_cache[project_id]
