"""Tests for Git worktree management."""

import asyncio
from pathlib import Path

import pytest

from gluon.worktree import WorktreeConfig, WorktreeError, WorktreeManager, is_git_repository


async def run_git(path: Path, *args: str) -> tuple[int, str, str]:
    """Run git command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


async def init_git_repo(path: Path) -> None:
    """Initialize a git repository at the given path."""
    # Initialize repo
    await run_git(path, "init")

    # Configure git user for commits (required for commits)
    await run_git(path, "config", "user.email", "test@example.com")
    await run_git(path, "config", "user.name", "Test User")

    # Create initial commit (required for worktrees to work)
    readme = path / "README.md"
    readme.write_text("# Test Project\n")
    await run_git(path, "add", "-A")
    returncode, stdout, stderr = await run_git(path, "commit", "-m", "Initial commit")
    if returncode != 0:
        raise RuntimeError(f"Failed to create initial commit: {stderr}")


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()
    await init_git_repo(repo_path)
    return repo_path


@pytest.fixture
def worktree_base(tmp_path: Path) -> Path:
    """Create a temporary directory for worktrees."""
    wt_path = tmp_path / "worktrees"
    wt_path.mkdir()
    return wt_path


class TestWorktreeManager:
    """Tests for WorktreeManager."""

    @pytest.mark.asyncio
    async def test_create_worktree(self, git_repo: Path, worktree_base: Path):
        """Test creating a worktree."""
        config = WorktreeConfig(base_dir=worktree_base)
        manager = WorktreeManager(git_repo, config)

        worktree_path = await manager.create("test-run-123")

        assert worktree_path.exists()
        assert worktree_path.is_dir()
        assert (worktree_path / "README.md").exists()
        assert manager.branch_name == "gluon-task/test-run-123"

    @pytest.mark.asyncio
    async def test_create_worktree_copies_env_files(self, git_repo: Path, worktree_base: Path):
        """Test that .env files are copied to worktree."""
        # Create .env file in repo
        env_file = git_repo / ".env"
        env_file.write_text("SECRET=test123")
        env_local = git_repo / ".env.local"
        env_local.write_text("LOCAL=value")

        config = WorktreeConfig(base_dir=worktree_base, copy_env_files=True)
        manager = WorktreeManager(git_repo, config)

        worktree_path = await manager.create("test-run-456")

        # Check .env files were copied
        assert (worktree_path / ".env").exists()
        assert (worktree_path / ".env").read_text() == "SECRET=test123"
        assert (worktree_path / ".env.local").exists()
        assert (worktree_path / ".env.local").read_text() == "LOCAL=value"

    @pytest.mark.asyncio
    async def test_cleanup_worktree(self, git_repo: Path, worktree_base: Path):
        """Test cleaning up a worktree."""
        config = WorktreeConfig(base_dir=worktree_base, auto_commit=False)
        manager = WorktreeManager(git_repo, config)

        worktree_path = await manager.create("test-run-789")
        assert worktree_path.exists()

        result = await manager.cleanup()

        assert result.success
        assert not worktree_path.exists()

    @pytest.mark.asyncio
    async def test_cleanup_commits_changes(self, git_repo: Path, worktree_base: Path):
        """Test that cleanup commits changes when auto_commit is True."""
        config = WorktreeConfig(base_dir=worktree_base, auto_commit=True)
        manager = WorktreeManager(git_repo, config)

        worktree_path = await manager.create("test-run-commit")

        # Make changes in worktree
        new_file = worktree_path / "new_file.txt"
        new_file.write_text("Hello from worktree")

        result = await manager.cleanup()

        assert result.success
        assert "Committed" in result.message

    @pytest.mark.asyncio
    async def test_cleanup_no_changes(self, git_repo: Path, worktree_base: Path):
        """Test cleanup when there are no changes to commit."""
        config = WorktreeConfig(base_dir=worktree_base, auto_commit=True)
        manager = WorktreeManager(git_repo, config)

        await manager.create("test-run-nochange")
        # Don't make any changes

        result = await manager.cleanup()

        assert result.success

    @pytest.mark.asyncio
    async def test_not_git_repo_raises_error(self, tmp_path: Path, worktree_base: Path):
        """Test that creating worktree in non-git repo raises error."""
        non_git_dir = tmp_path / "not-a-repo"
        non_git_dir.mkdir()

        config = WorktreeConfig(base_dir=worktree_base)
        manager = WorktreeManager(non_git_dir, config)

        with pytest.raises(WorktreeError, match="Not a git repository"):
            await manager.create("test-run")

    @pytest.mark.asyncio
    async def test_custom_branch_prefix(self, git_repo: Path, worktree_base: Path):
        """Test using a custom branch prefix."""
        config = WorktreeConfig(base_dir=worktree_base, branch_prefix="my-tasks")
        manager = WorktreeManager(git_repo, config)

        await manager.create("run-abc")

        assert manager.branch_name == "my-tasks/run-abc"

    @pytest.mark.asyncio
    async def test_cleanup_on_error(self, git_repo: Path, worktree_base: Path):
        """Test that cleanup_on_error forces removal."""
        config = WorktreeConfig(base_dir=worktree_base, cleanup_on_error=True)
        manager = WorktreeManager(git_repo, config)

        worktree_path = await manager.create("test-run-error")
        assert worktree_path.exists()

        # Simulate worktree path being set and cleanup being called
        # even after directory manually removed
        result = await manager.cleanup()

        # Should succeed without raising
        assert result.success or not worktree_path.exists()

    @pytest.mark.asyncio
    async def test_copy_custom_patterns(self, git_repo: Path, worktree_base: Path):
        """Test copying files matching custom patterns."""
        # Create custom config file
        custom_file = git_repo / "local.settings.json"
        custom_file.write_text('{"key": "value"}')

        config = WorktreeConfig(
            base_dir=worktree_base,
            copy_patterns=[".env*", "local.settings.json"],
        )
        manager = WorktreeManager(git_repo, config)

        worktree_path = await manager.create("test-custom")

        assert (worktree_path / "local.settings.json").exists()
        assert (worktree_path / "local.settings.json").read_text() == '{"key": "value"}'


class TestIsGitRepository:
    """Tests for is_git_repository helper function."""

    @pytest.mark.asyncio
    async def test_is_git_repo_true(self, git_repo: Path):
        """Test that is_git_repository returns True for git repos."""
        result = await is_git_repository(git_repo)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_git_repo_false(self, tmp_path: Path):
        """Test that is_git_repository returns False for non-git dirs."""
        non_git_dir = tmp_path / "regular-dir"
        non_git_dir.mkdir()

        result = await is_git_repository(non_git_dir)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_git_repo_nonexistent(self, tmp_path: Path):
        """Test is_git_repository with nonexistent path."""
        result = await is_git_repository(tmp_path / "nonexistent")
        assert result is False


class TestWorktreeConfig:
    """Tests for WorktreeConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = WorktreeConfig()

        assert config.base_dir is None
        assert config.branch_prefix == "gluon-task"
        assert config.auto_commit is True
        assert config.auto_merge is False
        assert config.cleanup_on_error is True
        assert config.copy_env_files is True
        assert ".env*" in config.copy_patterns

    def test_custom_config(self):
        """Test custom configuration."""
        config = WorktreeConfig(
            base_dir=Path("/custom/path"),
            branch_prefix="custom",
            auto_commit=False,
            auto_merge=True,
        )

        assert config.base_dir == Path("/custom/path")
        assert config.branch_prefix == "custom"
        assert config.auto_commit is False
        assert config.auto_merge is True
