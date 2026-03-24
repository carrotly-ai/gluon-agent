"""Git worktree management for isolated task execution."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WorktreeConfig:
    """Configuration for worktree behavior."""

    base_dir: Path | None = None  # Default: /tmp/gluon-worktrees/
    branch_prefix: str = "gluon-task"
    auto_commit: bool = True
    auto_merge: bool = False  # Merge changes back to source branch
    cleanup_on_error: bool = True
    copy_env_files: bool = True  # Copy .env* files to worktree
    copy_patterns: list[str] = field(
        default_factory=lambda: [
            ".env*",  # Root level env files
            "**/.env*",  # Env files in subdirectories
            ".npmrc",  # npm config
            "**/.npmrc",  # npm config in subdirectories
            "local.settings.json",  # Azure Functions config
        ]
    )


@dataclass
class WorktreeResult:
    """Result of a worktree operation."""

    success: bool
    path: Path | None = None
    branch: str | None = None
    message: str = ""
    error: str | None = None


class WorktreeError(Exception):
    """Raised when a worktree operation fails."""

    pass


class WorktreeManager:
    """Manages temporary Git worktrees for isolated task execution."""

    def __init__(self, repo_path: Path, config: WorktreeConfig | None = None):
        """
        Initialize worktree manager for a repository.

        Args:
            repo_path: Path to the git repository
            config: Optional configuration for worktree behavior
        """
        self.repo_path = Path(repo_path).resolve()
        self.config = config or WorktreeConfig()
        self.worktree_path: Path | None = None
        self.branch_name: str | None = None
        self._source_branch: str | None = None
        self._is_git_repo: bool | None = None

    @property
    def source_branch(self) -> str | None:
        """The branch that was checked out when the worktree was created."""
        return self._source_branch

    async def create(self, run_id: str) -> Path:
        """
        Create a new worktree for the given run.

        Args:
            run_id: Unique identifier for the run (used in path and branch name)

        Returns:
            Path to the created worktree

        Raises:
            WorktreeError: If worktree creation fails or repo is not a git repo
        """
        # Check if this is a git repo
        if not await self._is_git_repository():
            raise WorktreeError(f"Not a git repository: {self.repo_path}")

        # Determine base directory
        base = self.config.base_dir or Path("/tmp/gluon-worktrees")
        base.mkdir(parents=True, exist_ok=True)

        # Create unique worktree path and branch name
        self.worktree_path = base / f"wt-{run_id}"
        self.branch_name = f"{self.config.branch_prefix}/{run_id}"

        # Get current branch for later reference
        try:
            self._source_branch = await self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        except RuntimeError as e:
            raise WorktreeError(f"Failed to get current branch: {e}") from e

        # Clean up any existing worktree at this path
        if self.worktree_path.exists():
            logger.warning(f"Worktree path already exists, cleaning up: {self.worktree_path}")
            shutil.rmtree(self.worktree_path, ignore_errors=True)

        # Create worktree with new branch from HEAD
        try:
            await self._run_git(
                "worktree",
                "add",
                "-b",
                self.branch_name,
                str(self.worktree_path),
                "HEAD",
            )
        except RuntimeError:
            # Try without creating a new branch if it already exists
            try:
                await self._run_git("branch", "-D", self.branch_name)
            except RuntimeError:
                pass
            await self._run_git(
                "worktree",
                "add",
                "-b",
                self.branch_name,
                str(self.worktree_path),
                "HEAD",
            )

        logger.info(f"Created worktree at {self.worktree_path} (branch: {self.branch_name})")

        # Copy environment and config files
        if self.config.copy_env_files:
            await self._copy_config_files()

        return self.worktree_path

    async def cleanup(self, commit_changes: bool | None = None) -> WorktreeResult:
        """
        Remove worktree and optionally commit/merge changes.

        Args:
            commit_changes: Whether to commit changes before cleanup.
                           If None, uses config.auto_commit.

        Returns:
            WorktreeResult with details of the cleanup operation
        """
        if not self.worktree_path:
            return WorktreeResult(success=True, message="No worktree to clean up")

        should_commit = commit_changes if commit_changes is not None else self.config.auto_commit
        result = WorktreeResult(success=True, path=self.worktree_path, branch=self.branch_name)

        try:
            # Commit any changes in the worktree
            if should_commit:
                committed = await self._commit_changes()
                if committed:
                    result.message = f"Committed changes to {self.branch_name}"

                    # Optionally merge back to source branch
                    if self.config.auto_merge:
                        try:
                            await self._merge_to_source()
                            result.message += f", merged to {self._source_branch}"
                        except RuntimeError as e:
                            # Don't fail cleanup on merge failure
                            result.message += f" (merge to {self._source_branch} failed: {e})"
                            logger.warning(f"Merge failed, keeping branch {self.branch_name}: {e}")

            # Remove the worktree
            await self._run_git("worktree", "remove", str(self.worktree_path), "--force")

            # Delete the task branch if it was merged
            if self.config.auto_merge and self.branch_name:
                try:
                    await self._run_git("branch", "-D", self.branch_name)
                except RuntimeError:
                    pass  # Branch may already be deleted or needed

            logger.info(f"Cleaned up worktree at {self.worktree_path}")
            self.worktree_path = None

        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error(f"Worktree cleanup failed: {e}")

            if self.config.cleanup_on_error:
                # Force remove even on error
                if self.worktree_path and self.worktree_path.exists():
                    shutil.rmtree(self.worktree_path, ignore_errors=True)
                try:
                    await self._run_git("worktree", "prune")
                except RuntimeError:
                    pass
                self.worktree_path = None

        return result

    async def _is_git_repository(self) -> bool:
        """Check if the repo_path is a valid git repository."""
        if self._is_git_repo is not None:
            return self._is_git_repo

        try:
            await self._run_git("rev-parse", "--git-dir")
            self._is_git_repo = True
        except RuntimeError:
            self._is_git_repo = False

        return self._is_git_repo

    async def _commit_changes(self) -> bool:
        """
        Commit any changes in the worktree.

        Returns:
            True if changes were committed, False if nothing to commit
        """
        if not self.worktree_path:
            return False

        # Check for changes
        status = await self._run_git_in_worktree("status", "--porcelain")
        if not status.strip():
            return False

        # Stage all changes
        await self._run_git_in_worktree("add", "-A")

        # Commit with descriptive message
        commit_msg = f"gluon: task changes [{self.branch_name}]"
        await self._run_git_in_worktree("commit", "-m", commit_msg)

        logger.info(f"Committed changes in worktree: {self.worktree_path}")
        return True

    async def _merge_to_source(self) -> None:
        """Merge worktree branch back to source branch."""
        if not self._source_branch or not self.branch_name:
            return

        # Checkout source branch in main repo
        await self._run_git("checkout", self._source_branch)

        # Merge the task branch
        await self._run_git("merge", self.branch_name, "--no-edit")

        logger.info(f"Merged {self.branch_name} into {self._source_branch}")

    async def _copy_config_files(self) -> None:
        """Copy environment and config files from main repo to worktree."""
        if not self.worktree_path:
            return

        copied_files: list[str] = []

        for pattern in self.config.copy_patterns:
            # Handle glob patterns
            if "*" in pattern:
                for src_file in self.repo_path.glob(pattern):
                    if src_file.is_file():
                        # Preserve relative path for subdirectory files
                        rel_path = src_file.relative_to(self.repo_path)
                        dest = self.worktree_path / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dest)
                        copied_files.append(str(rel_path))
            else:
                # Direct file path
                src_file = self.repo_path / pattern
                if src_file.is_file():
                    dest = self.worktree_path / pattern
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest)
                    copied_files.append(pattern)

        if copied_files:
            logger.debug(f"Copied config files to worktree: {', '.join(copied_files)}")

    async def _run_git(self, *args: str) -> str:
        """
        Run git command in the main repository.

        Args:
            *args: Git command arguments

        Returns:
            Command stdout as string

        Raises:
            RuntimeError: If git command fails
        """
        return await self._run_git_cmd(self.repo_path, *args)

    async def _run_git_in_worktree(self, *args: str) -> str:
        """
        Run git command in the worktree.

        Args:
            *args: Git command arguments

        Returns:
            Command stdout as string

        Raises:
            RuntimeError: If git command fails
        """
        if not self.worktree_path:
            raise RuntimeError("No worktree path set")
        return await self._run_git_cmd(self.worktree_path, *args)

    async def _run_git_cmd(self, cwd: Path, *args: str) -> str:
        """
        Run a git command in the specified directory.

        Args:
            cwd: Working directory for the command
            *args: Git command arguments

        Returns:
            Command stdout as string

        Raises:
            RuntimeError: If git command fails
        """
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            cmd_str = f"git {' '.join(args)}"
            error_msg = stderr.decode().strip() or f"Command failed with exit code {proc.returncode}"
            raise RuntimeError(f"Git command failed: {cmd_str}\n{error_msg}")

        return stdout.decode().strip()


async def is_git_repository(path: Path) -> bool:
    """
    Check if a path is within a git repository.

    Args:
        path: Path to check

    Returns:
        True if path is in a git repository
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--git-dir",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False


async def branch_exists(repo_path: Path, branch_name: str) -> bool:
    """
    Check if a branch exists in the repository.

    Args:
        repo_path: Path to the git repository
        branch_name: Name of the branch to check

    Returns:
        True if branch exists
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--verify",
            f"refs/heads/{branch_name}",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False


async def recreate_worktree(
    repo_path: Path,
    worktree_path: Path,
    branch_name: str,
    copy_patterns: list[str] | None = None,
) -> bool:
    """
    Recreate a worktree from an existing branch.

    This is used when a worktree directory was lost (e.g., container restart)
    but the branch still exists in the repository.

    Args:
        repo_path: Path to the main git repository
        worktree_path: Path where the worktree should be created
        branch_name: Name of the existing branch to attach
        copy_patterns: Optional patterns for config files to copy

    Returns:
        True if worktree was successfully recreated

    Raises:
        WorktreeError: If recreation fails
    """
    logger.info(f"Recreating worktree at {worktree_path} from branch {branch_name}")

    # Verify branch exists
    if not await branch_exists(repo_path, branch_name):
        raise WorktreeError(f"Branch {branch_name} does not exist in repository")

    # Clean up any stale worktree references
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "prune",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass  # Non-fatal

    # Ensure parent directory exists
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove if somehow exists (shouldn't, but be safe)
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)

    # Create worktree from existing branch (no -b flag)
    proc = await asyncio.create_subprocess_exec(
        "git",
        "worktree",
        "add",
        str(worktree_path),
        branch_name,
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or "Unknown error"
        raise WorktreeError(f"Failed to recreate worktree: {error_msg}")

    logger.info(f"Successfully recreated worktree at {worktree_path}")

    # Copy config files if patterns provided
    if copy_patterns:
        for pattern in copy_patterns:
            if "*" in pattern:
                for src_file in repo_path.glob(pattern):
                    if src_file.is_file():
                        rel_path = src_file.relative_to(repo_path)
                        dest = worktree_path / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dest)
            else:
                src_file = repo_path / pattern
                if src_file.is_file():
                    dest = worktree_path / pattern
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest)

    return True
