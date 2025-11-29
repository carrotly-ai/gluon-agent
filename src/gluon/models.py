"""Pydantic models for Gluon Agent."""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    """Status of a Claude Code session."""

    ACTIVE = "active"  # Currently running
    PAUSED = "paused"  # Can be resumed
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Error occurred


# Project markers - files that indicate a directory is a project
PROJECT_MARKERS = [
    "package.json",      # Node.js/JavaScript
    "pyproject.toml",    # Python (modern)
    "setup.py",          # Python (legacy)
    "Cargo.toml",        # Rust
    "go.mod",            # Go
    "pom.xml",           # Java/Maven
    "build.gradle",      # Java/Gradle
    "Gemfile",           # Ruby
    "composer.json",     # PHP
    "mix.exs",           # Elixir
    "pubspec.yaml",      # Dart/Flutter
    ".git",              # Any git repo
]


class Workspace(BaseModel):
    """A workspace directory containing multiple projects."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str  # Human-readable name (unique)
    path: Path  # Absolute path to workspace directory
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    scan_depth: int = 1  # How deep to scan for projects (1 = immediate children)
    auto_discover: bool = True  # Whether to auto-discover projects
    ignore_patterns: list[str] = Field(default_factory=lambda: [".*", "node_modules", "__pycache__", "venv", ".venv"])

    def model_post_init(self, __context: Any) -> None:
        """Ensure path is absolute."""
        if not self.path.is_absolute():
            self.path = self.path.resolve()

    def scan_for_projects(self) -> list[Path]:
        """Scan workspace for project directories."""
        projects: list[Path] = []

        if not self.path.exists() or not self.path.is_dir():
            return projects

        for item in self.path.iterdir():
            # Skip ignored patterns
            if any(item.match(pattern) for pattern in self.ignore_patterns):
                continue

            if not item.is_dir():
                continue

            # Check if it looks like a project
            if self._is_project(item):
                projects.append(item)

        return sorted(projects)

    def _is_project(self, path: Path) -> bool:
        """Check if a directory looks like a project."""
        for marker in PROJECT_MARKERS:
            if (path / marker).exists():
                return True
        return False


class Project(BaseModel):
    """A registered project that can be managed by Gluon."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str  # Human-readable name (unique)
    path: Path  # Absolute path to project directory
    workspace_id: str | None = None  # FK to Workspace (None = standalone project)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] | None = None  # Optional extra data

    def model_post_init(self, __context: Any) -> None:
        """Ensure path is absolute."""
        if not self.path.is_absolute():
            self.path = self.path.resolve()

    @property
    def is_workspace_managed(self) -> bool:
        """Check if this project is managed by a workspace."""
        return self.workspace_id is not None


class Session(BaseModel):
    """A Claude Code session associated with a project."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str  # FK to Project
    claude_session_id: str | None = None  # Session ID from Claude SDK (for resume)
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_prompt: str | None = None  # Last user prompt
    total_cost_usd: float = 0.0
    total_turns: int = 0

    def mark_paused(self) -> None:
        """Mark session as paused (can be resumed)."""
        self.status = SessionStatus.PAUSED
        self.updated_at = datetime.now()

    def mark_completed(self) -> None:
        """Mark session as completed."""
        self.status = SessionStatus.COMPLETED
        self.updated_at = datetime.now()

    def mark_failed(self) -> None:
        """Mark session as failed."""
        self.status = SessionStatus.FAILED
        self.updated_at = datetime.now()

    def add_cost(self, cost: float) -> None:
        """Add to total cost."""
        self.total_cost_usd += cost
        self.updated_at = datetime.now()

    def increment_turns(self) -> None:
        """Increment turn count."""
        self.total_turns += 1
        self.updated_at = datetime.now()
