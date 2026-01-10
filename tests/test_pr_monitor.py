"""Tests for PR Monitoring Service."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.git_manager import GitManager
from gluon.models import ExecutionRun, RunStatus
from gluon.pr_monitor import (
    MAX_AUTO_RESUMES,
    PRMonitorService,
    TRIGGER_PATTERNS,
)
from gluon.runner import TaskRunner
from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path: Path):
    """Create a temporary store for testing."""
    db_path = tmp_path / "test.db"
    return GluonStore(db_path=db_path)


@pytest.fixture
def git_manager(store: GluonStore):
    """Create a GitManager instance."""
    return GitManager(store=store)


@pytest.fixture
def runner(store: GluonStore):
    """Create a TaskRunner instance."""
    return TaskRunner(store=store)


@pytest.fixture
def pr_monitor(store: GluonStore, runner: TaskRunner, git_manager: GitManager):
    """Create a PRMonitorService instance."""
    return PRMonitorService(store=store, runner=runner, git_manager=git_manager)


@pytest.fixture
def project(store: GluonStore, tmp_path: Path):
    """Create a test project."""
    project_path = tmp_path / "test-project"
    project_path.mkdir()
    return store.create_project("test-project", project_path)


@pytest.fixture
def run_with_pr(store: GluonStore, project):
    """Create a test run with PR info."""
    run = store.create_run(project.id, "Test prompt")
    run.status = RunStatus.REVIEW
    run.pr_number = 123
    run.pr_url = "https://github.com/test/repo/pull/123"
    run.pr_status = "open"
    run.branch_name = "gluon-task/abc123"
    run.git_commit_sha = "abc123def456"
    run.claude_session_id = "session-123"
    store.update_run(run)
    return run


class TestCommentTriggerDetection:
    """Test comment trigger detection logic."""

    def test_trigger_patterns_match(self, pr_monitor: PRMonitorService):
        """Test that @gluon and /gluon patterns trigger correctly."""
        # @gluon mention
        comment1 = {"body": "Hey @gluon can you fix this?", "author": "reviewer"}
        assert pr_monitor._is_comment_triggered(comment1) is True

        # /gluon command
        comment2 = {"body": "/gluon fix the type error", "author": "reviewer"}
        assert pr_monitor._is_comment_triggered(comment2) is True

        # Case insensitive
        comment3 = {"body": "@GLUON please update", "author": "reviewer"}
        assert pr_monitor._is_comment_triggered(comment3) is True

    def test_non_trigger_comments_ignored(self, pr_monitor: PRMonitorService):
        """Test that regular comments don't trigger."""
        comment1 = {"body": "LGTM!", "author": "reviewer"}
        assert pr_monitor._is_comment_triggered(comment1) is False

        comment2 = {"body": "Please fix the type errors", "author": "reviewer"}
        assert pr_monitor._is_comment_triggered(comment2) is False

        comment3 = {"body": "Great work!", "author": "reviewer"}
        assert pr_monitor._is_comment_triggered(comment3) is False

    def test_bot_comments_ignored(self, pr_monitor: PRMonitorService):
        """Test that bot's own comments are ignored."""
        comment1 = {"body": "@gluon test", "author": "gluon-agent"}
        assert pr_monitor._is_comment_triggered(comment1) is False

        comment2 = {"body": "/gluon fix", "author": "gluon-bot"}
        assert pr_monitor._is_comment_triggered(comment2) is False

        comment3 = {"body": "@gluon help", "author": "github-actions[bot]"}
        assert pr_monitor._is_comment_triggered(comment3) is False


class TestShouldMonitorRun:
    """Test run monitoring eligibility."""

    def test_review_status_monitored(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun):
        """Test that REVIEW status runs are monitored."""
        run_with_pr.status = RunStatus.REVIEW
        assert pr_monitor.should_monitor_run(run_with_pr) is True

    def test_completed_status_monitored(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that COMPLETED status runs with open PRs are monitored."""
        run_with_pr.status = RunStatus.COMPLETED
        store.update_run(run_with_pr)
        assert pr_monitor.should_monitor_run(run_with_pr) is True

    def test_running_status_not_monitored(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that RUNNING status runs are not monitored."""
        run_with_pr.status = RunStatus.RUNNING
        store.update_run(run_with_pr)
        assert pr_monitor.should_monitor_run(run_with_pr) is False

    def test_closed_pr_not_monitored(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that runs with closed PRs are not monitored."""
        run_with_pr.pr_status = "closed"
        store.update_run(run_with_pr)
        assert pr_monitor.should_monitor_run(run_with_pr) is False

    def test_merged_pr_not_monitored(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that runs with merged PRs are not monitored."""
        run_with_pr.pr_status = "merged"
        store.update_run(run_with_pr)
        assert pr_monitor.should_monitor_run(run_with_pr) is False

    def test_disabled_auto_resume_not_monitored(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that runs with auto_resume_enabled=False are not monitored."""
        run_with_pr.auto_resume_enabled = False
        store.update_run(run_with_pr)
        assert pr_monitor.should_monitor_run(run_with_pr) is False

    def test_max_resumes_reached_not_monitored(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that runs at max auto-resumes are not monitored."""
        run_with_pr.auto_resume_count = MAX_AUTO_RESUMES
        store.update_run(run_with_pr)
        assert pr_monitor.should_monitor_run(run_with_pr) is False


class TestCheckPRComments:
    """Test PR comment checking."""

    @pytest.mark.asyncio
    async def test_returns_triggered_comment(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun):
        """Test that triggered comments are returned."""
        mock_comments = [
            {"id": 1, "body": "Looks good", "author": "reviewer1"},
            {"id": 2, "body": "@gluon fix the type error", "author": "reviewer2"},
            {"id": 3, "body": "Thanks!", "author": "reviewer1"},
        ]

        with patch.object(pr_monitor.git_manager, "get_pr_comments", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_comments

            result = await pr_monitor.check_pr_comments(run_with_pr)

            assert result is not None
            assert result["id"] == 2
            assert "@gluon" in result["body"]

    @pytest.mark.asyncio
    async def test_filters_already_processed(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that already-processed comments are filtered out."""
        run_with_pr.last_comment_id = 2
        store.update_run(run_with_pr)

        mock_comments = [
            {"id": 1, "body": "@gluon fix", "author": "reviewer1"},
            {"id": 2, "body": "@gluon fix", "author": "reviewer2"},
            {"id": 3, "body": "Thanks!", "author": "reviewer1"},
        ]

        with patch.object(pr_monitor.git_manager, "get_pr_comments", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_comments

            result = await pr_monitor.check_pr_comments(run_with_pr)

            # Comment 3 doesn't have trigger, so should return None
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_no_pr(self, pr_monitor: PRMonitorService, store: GluonStore, project):
        """Test that None is returned for runs without PR."""
        run = store.create_run(project.id, "Test prompt")
        run.pr_number = None
        store.update_run(run)

        result = await pr_monitor.check_pr_comments(run)
        assert result is None


class TestCheckCIFailures:
    """Test CI failure checking."""

    @pytest.mark.asyncio
    async def test_returns_failures(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun):
        """Test that CI failures are returned."""
        mock_failures = [
            {"name": "Vercel", "conclusion": "failure", "output_summary": "Build failed"},
            {"name": "lint", "conclusion": "failure", "output_summary": "Lint errors"},
        ]

        with patch.object(pr_monitor.git_manager, "get_failed_checks", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_failures

            result = await pr_monitor.check_ci_failures(run_with_pr)

            assert result is not None
            assert len(result) == 2
            assert result[0]["name"] == "Vercel"

    @pytest.mark.asyncio
    async def test_skips_same_commit(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that same commit is not rechecked."""
        run_with_pr.last_check_sha = run_with_pr.git_commit_sha
        store.update_run(run_with_pr)

        result = await pr_monitor.check_ci_failures(run_with_pr)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_no_failures(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun):
        """Test that None is returned when no failures."""
        with patch.object(pr_monitor.git_manager, "get_failed_checks", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            result = await pr_monitor.check_ci_failures(run_with_pr)
            assert result is None


class TestAutoResumeForComment:
    """Test auto-resume for comment functionality."""

    @pytest.mark.asyncio
    async def test_resumes_with_comment_context(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that resume includes comment context in prompt."""
        comment = {
            "id": 100,
            "body": "@gluon fix the type error in utils.py",
            "author": "reviewer",
            "path": "src/utils.py",
            "line": 42,
        }

        with patch.object(pr_monitor.runner, "resume_in_place", new_callable=AsyncMock) as mock_resume:
            mock_resume.return_value = run_with_pr

            result = await pr_monitor.auto_resume_for_comment(run_with_pr, comment)

            assert result is not None
            mock_resume.assert_called_once()

            # Check prompt contains comment context
            call_args = mock_resume.call_args
            prompt = call_args.kwargs["new_prompt"]
            assert "@reviewer" in prompt
            assert "type error" in prompt
            assert "src/utils.py" in prompt
            assert "(line 42)" in prompt

    @pytest.mark.asyncio
    async def test_updates_tracking_fields(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that tracking fields are updated."""
        comment = {"id": 100, "body": "@gluon fix", "author": "reviewer"}

        with patch.object(pr_monitor.runner, "resume_in_place", new_callable=AsyncMock) as mock_resume:
            mock_resume.return_value = run_with_pr

            await pr_monitor.auto_resume_for_comment(run_with_pr, comment)

            # Check tracking was updated
            updated_run = store.get_run(run_with_pr.id)
            assert updated_run.last_comment_id == 100
            assert updated_run.auto_resume_count == 1


class TestAutoResumeForCIFailure:
    """Test auto-resume for CI failure functionality."""

    @pytest.mark.asyncio
    async def test_resumes_with_failure_context(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that resume includes failure context in prompt."""
        failures = [
            {
                "name": "Build",
                "conclusion": "failure",
                "output_title": "Build failed",
                "output_summary": "TypeScript errors found",
                "output_text": "Error in src/index.ts",
                "details_url": "https://example.com/logs",
            }
        ]

        with patch.object(pr_monitor.runner, "resume_in_place", new_callable=AsyncMock) as mock_resume:
            mock_resume.return_value = run_with_pr

            result = await pr_monitor.auto_resume_for_ci_failure(run_with_pr, failures)

            assert result is not None
            mock_resume.assert_called_once()

            # Check prompt contains failure context
            call_args = mock_resume.call_args
            prompt = call_args.kwargs["new_prompt"]
            assert "Build" in prompt
            assert "TypeScript errors" in prompt
            assert "https://example.com/logs" in prompt

    @pytest.mark.asyncio
    async def test_uses_vercel_prompt_for_vercel(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that Vercel-specific prompt is used for Vercel failures."""
        failures = [
            {
                "name": "Vercel Preview Deployment",
                "conclusion": "failure",
                "output_summary": "Build Error: Cannot find module",
                "output_text": "",
                "details_url": "https://vercel.com/logs",
            }
        ]

        with patch.object(pr_monitor.runner, "resume_in_place", new_callable=AsyncMock) as mock_resume:
            mock_resume.return_value = run_with_pr

            await pr_monitor.auto_resume_for_ci_failure(run_with_pr, failures)

            call_args = mock_resume.call_args
            prompt = call_args.kwargs["new_prompt"]
            # Vercel prompt should mention common issues
            assert "TypeScript type errors" in prompt or "Missing imports" in prompt

    @pytest.mark.asyncio
    async def test_updates_tracking_fields(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun, store: GluonStore):
        """Test that tracking fields are updated."""
        failures = [{"name": "Build", "conclusion": "failure"}]

        with patch.object(pr_monitor.runner, "resume_in_place", new_callable=AsyncMock) as mock_resume:
            mock_resume.return_value = run_with_pr

            await pr_monitor.auto_resume_for_ci_failure(run_with_pr, failures)

            # Check tracking was updated
            updated_run = store.get_run(run_with_pr.id)
            assert updated_run.last_check_sha == run_with_pr.git_commit_sha
            assert updated_run.auto_resume_count == 1


class TestPostPRComment:
    """Test PR comment posting."""

    @pytest.mark.asyncio
    async def test_posts_comment(self, pr_monitor: PRMonitorService, run_with_pr: ExecutionRun):
        """Test that comment is posted via git_manager."""
        with patch.object(pr_monitor.git_manager, "post_pr_comment", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = True

            result = await pr_monitor.post_pr_comment(run_with_pr, "Test message")

            assert result is True
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_no_pr(self, pr_monitor: PRMonitorService, store: GluonStore, project):
        """Test that False is returned for runs without PR."""
        run = store.create_run(project.id, "Test prompt")
        run.pr_number = None
        store.update_run(run)

        result = await pr_monitor.post_pr_comment(run, "Test message")
        assert result is False
