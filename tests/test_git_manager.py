"""Tests for GitManager."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gluon.git_manager import GitManager, _is_valid_ref
from gluon.models import GitStatus, GitSyncResult, Project
from gluon.store import GluonStore


class TestRefValidation:
    """Security: git ref/branch names must be validated before hitting subprocess."""

    def test_valid_refs_accepted(self):
        for name in ("main", "feature/foo", "v1.2.3", "release-2026", "a/b/c", "gluon-abc123"):
            assert _is_valid_ref(name), name

    def test_dangerous_refs_rejected(self):
        for name in ("-D", "--upload-pack=evil", "..", "a..b", "", "foo bar", "$(rm -rf /)", "-rf", "a;b"):
            assert not _is_valid_ref(name), name

    @pytest.mark.asyncio
    async def test_delete_branch_rejects_option_like_name(self, git_manager, tmp_path):
        with patch.object(git_manager, "_run_git", new=AsyncMock()) as mock_git:
            res = await git_manager.delete_branch(tmp_path, "--force")
            assert res["success"] is False
            assert "Invalid" in res["message"]
            mock_git.assert_not_called()  # never reached git

    @pytest.mark.asyncio
    async def test_rebase_rejects_option_like_name(self, git_manager, tmp_path):
        with patch.object(git_manager, "_run_git", new=AsyncMock()) as mock_git:
            res = await git_manager.rebase_branch(tmp_path, "-x")
            assert res["success"] is False
            mock_git.assert_not_called()


@pytest.fixture
def git_manager(store: GluonStore):
    """Create a GitManager instance."""
    return GitManager(store=store)


@pytest.fixture
def project(store: GluonStore, tmp_path: Path):
    """Create a test project."""
    project_path = tmp_path / "test-project"
    project_path.mkdir()
    return store.create_project("test-project", project_path)


class TestGitStatus:
    """Test GitStatus model."""

    def test_is_diverged(self):
        """Test is_diverged property."""
        status = GitStatus(is_git_repo=True, commits_ahead=1, commits_behind=2)
        assert status.is_diverged is True

        status2 = GitStatus(is_git_repo=True, commits_ahead=1, commits_behind=0)
        assert status2.is_diverged is False

    def test_is_clean(self):
        """Test is_clean property."""
        status = GitStatus(is_git_repo=True, has_uncommitted=False, commits_ahead=0, commits_behind=0)
        assert status.is_clean is True

        status2 = GitStatus(is_git_repo=True, has_uncommitted=True)
        assert status2.is_clean is False

    def test_needs_pull(self):
        """Test needs_pull property."""
        status = GitStatus(is_git_repo=True, commits_behind=2, commits_ahead=0)
        assert status.needs_pull is True

        status2 = GitStatus(is_git_repo=True, commits_behind=2, commits_ahead=1)
        assert status2.needs_pull is False

    def test_needs_push(self):
        """Test needs_push property."""
        status = GitStatus(is_git_repo=True, commits_ahead=2, commits_behind=0)
        assert status.needs_push is True


class TestGitSyncResult:
    """Test GitSyncResult model."""

    def test_ok_result(self):
        """Test successful result creation."""
        result = GitSyncResult.ok("commit+push", "Changes pushed", files_committed=5)
        assert result.success is True
        assert result.action == "commit+push"
        assert result.message == "Changes pushed"
        assert result.files_committed == 5

    def test_fail_result(self):
        """Test failure result creation."""
        result = GitSyncResult.fail("Network error")
        assert result.success is False
        assert result.error == "Network error"

    def test_skip_result(self):
        """Test skip result creation."""
        result = GitSyncResult.skip("Not a git repo")
        assert result.success is True
        assert result.action == "none"


class TestGitManagerHelpers:
    """Test GitManager helper methods."""

    @pytest.mark.asyncio
    async def test_is_git_repo_true(self, git_manager: GitManager, tmp_path: Path):
        """Test _is_git_repo returns True for git repos."""
        with patch.object(git_manager, "_run_git", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, ".git", "")
            result = await git_manager._is_git_repo(tmp_path)
            assert result is True

    @pytest.mark.asyncio
    async def test_is_git_repo_false(self, git_manager: GitManager, tmp_path: Path):
        """Test _is_git_repo returns False for non-git dirs."""
        with patch.object(git_manager, "_run_git", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (128, "", "fatal: not a git repository")
            result = await git_manager._is_git_repo(tmp_path)
            assert result is False

    @pytest.mark.asyncio
    async def test_get_branch(self, git_manager: GitManager, tmp_path: Path):
        """Test _get_branch returns current branch."""
        with patch.object(git_manager, "_run_git", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "main", "")
            result = await git_manager._get_branch(tmp_path)
            assert result == "main"

    @pytest.mark.asyncio
    async def test_get_uncommitted_count(self, git_manager: GitManager, tmp_path: Path):
        """Test _get_uncommitted_count counts changes."""
        with patch.object(git_manager, "_run_git", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, " M file1.py\n?? file2.py\nA  file3.py", "")
            result = await git_manager._get_uncommitted_count(tmp_path)
            assert result == 3


class TestPreTaskSync:
    """Test pre-task sync behavior."""

    @pytest.mark.asyncio
    async def test_skip_non_git_repo(self, git_manager: GitManager, project: Project):
        """Test pre_task_sync skips non-git repos."""
        with patch.object(git_manager, "_is_git_repo", new_callable=AsyncMock) as mock_is_git:
            mock_is_git.return_value = False
            result = await git_manager.pre_task_sync(project)
            assert result.success is True
            assert result.action == "none"

    @pytest.mark.asyncio
    async def test_already_up_to_date(self, git_manager: GitManager, project: Project):
        """Test pre_task_sync when already up to date."""
        with patch.object(git_manager, "_is_git_repo", new_callable=AsyncMock) as mock_is_git:
            mock_is_git.return_value = True

            with patch.object(git_manager, "_get_uncommitted_count", new_callable=AsyncMock) as mock_uncommitted:
                mock_uncommitted.return_value = 0

                with patch.object(git_manager, "_get_remote", new_callable=AsyncMock) as mock_remote:
                    mock_remote.return_value = ("origin", "https://github.com/test/repo")

                    with patch.object(git_manager, "_run_git", new_callable=AsyncMock) as mock_run:
                        mock_run.return_value = (0, "", "")

                        with patch.object(
                            git_manager, "_get_ahead_behind", new_callable=AsyncMock
                        ) as mock_ahead_behind:
                            mock_ahead_behind.return_value = (0, 0)

                            with patch.object(git_manager, "refresh_status", new_callable=AsyncMock):
                                result = await git_manager.pre_task_sync(project)
                                assert result.success is True
                                assert result.action == "none"

    @pytest.mark.asyncio
    async def test_fail_on_diverged(self, git_manager: GitManager, project: Project):
        """Test pre_task_sync fails when branches have diverged."""
        with patch.object(git_manager, "_is_git_repo", new_callable=AsyncMock) as mock_is_git:
            mock_is_git.return_value = True

            with patch.object(git_manager, "_get_uncommitted_count", new_callable=AsyncMock) as mock_uncommitted:
                mock_uncommitted.return_value = 0

                with patch.object(git_manager, "_get_remote", new_callable=AsyncMock) as mock_remote:
                    mock_remote.return_value = ("origin", "https://github.com/test/repo")

                    with patch.object(git_manager, "_run_git", new_callable=AsyncMock) as mock_run:
                        mock_run.return_value = (0, "", "")

                        with patch.object(
                            git_manager, "_get_ahead_behind", new_callable=AsyncMock
                        ) as mock_ahead_behind:
                            mock_ahead_behind.return_value = (2, 3)  # Both ahead and behind

                            result = await git_manager.pre_task_sync(project)
                            assert result.success is False
                            assert "diverged" in result.error.lower()


class TestPostTaskSync:
    """Test post-task sync behavior."""

    @pytest.mark.asyncio
    async def test_skip_non_git_repo(self, git_manager: GitManager, project: Project):
        """Test post_task_sync skips non-git repos."""
        with patch.object(git_manager, "_is_git_repo", new_callable=AsyncMock) as mock_is_git:
            mock_is_git.return_value = False
            result = await git_manager.post_task_sync(project, "test commit")
            assert result.success is True
            assert result.action == "none"

    @pytest.mark.asyncio
    async def test_no_changes_to_commit(self, git_manager: GitManager, project: Project):
        """Test post_task_sync when no changes."""
        with patch.object(git_manager, "_is_git_repo", new_callable=AsyncMock) as mock_is_git:
            mock_is_git.return_value = True

            with patch.object(git_manager, "_get_uncommitted_count", new_callable=AsyncMock) as mock_uncommitted:
                mock_uncommitted.return_value = 0

                result = await git_manager.post_task_sync(project, "test commit")
                assert result.success is True
                assert result.action == "none"


class TestGitStatusCRUD:
    """Test git status storage in database."""

    def test_update_and_get_git_status(self, store: GluonStore, project: Project):
        """Test storing and retrieving git status."""
        status = GitStatus(
            is_git_repo=True,
            branch="main",
            remote="origin",
            remote_url="https://github.com/test/repo",
            has_uncommitted=True,
            uncommitted_count=3,
            commits_ahead=2,
            commits_behind=1,
            last_fetch_at=datetime.now(),
        )

        store.update_git_status(project.id, status)
        retrieved = store.get_git_status(project.id)

        assert retrieved is not None
        assert retrieved.is_git_repo is True
        assert retrieved.branch == "main"
        assert retrieved.remote == "origin"
        assert retrieved.uncommitted_count == 3
        assert retrieved.commits_ahead == 2
        assert retrieved.commits_behind == 1

    def test_get_git_status_nonexistent(self, store: GluonStore):
        """Test getting git status for nonexistent project."""
        status = store.get_git_status("nonexistent-id")
        assert status is None
