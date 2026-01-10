"""PR Monitoring Service for auto-resume on comments and CI failures."""

import logging
import re

from gluon.git_manager import GitManager
from gluon.models import ExecutionRun, RunStatus
from gluon.runner import TaskRunner
from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Maximum number of auto-resumes per run
MAX_AUTO_RESUMES = 5

# Patterns that trigger auto-resume in comments
TRIGGER_PATTERNS = [
    r"@gluon\b",  # Mention @gluon
    r"/gluon\b",  # Command /gluon
]

# Prompt templates for auto-resume
COMMENT_RESUME_PROMPT = """
A reviewer has left feedback on PR #{pr_number}:

**Comment by @{author}:**
{comment_body}

{file_context}

Please address this feedback by implementing the requested changes.
After making changes, commit and push to update the PR.
"""

CI_FAILURE_RESUME_PROMPT = """
CI check "{check_name}" has failed on PR #{pr_number}.

**Failure Summary:**
{output_title}

**Details:**
{output_summary}

{output_text}

Please investigate and fix the issue. After fixing, commit and push to update the PR.

Build log URL: {details_url}
"""

VERCEL_FAILURE_PROMPT = """
Vercel deployment has failed on PR #{pr_number}.

**Build Error:**
{output_summary}

{output_text}

Please fix the build error. Common issues include:
- TypeScript type errors
- Missing imports
- Build configuration issues

After fixing, commit and push to trigger a new deployment.

Details: {details_url}
"""


class PRMonitorService:
    """
    Monitors PRs for actionable comments and CI failures.

    Checks for:
    1. Comments containing @gluon or /gluon triggers
    2. CI/CD failures (especially Vercel builds)

    When detected, resumes the corresponding run with context.
    """

    def __init__(self, store: GluonStore, runner: TaskRunner, git_manager: GitManager):
        self.store = store
        self.runner = runner
        self.git_manager = git_manager

    async def check_pr_comments(self, run: ExecutionRun) -> dict | None:
        """
        Check for new actionable comments since last_comment_id.

        Args:
            run: ExecutionRun to check comments for

        Returns:
            Actionable comment dict or None if no new triggered comments
        """
        if not run.pr_number:
            return None

        project = self.store.get_project(run.project_id)
        if not project:
            return None

        # Get all comments
        comments = await self.git_manager.get_pr_comments(project.expanded_path, run.pr_number)

        # Filter to comments after last_comment_id
        last_id = run.last_comment_id or 0
        new_comments = [c for c in comments if (c.get("id") or 0) > last_id]

        # Find first triggered comment
        for comment in new_comments:
            if self._is_comment_triggered(comment):
                return comment

        return None

    def _is_comment_triggered(self, comment: dict) -> bool:
        """
        Determine if a comment triggers auto-resume.

        Criteria:
        - Contains @gluon or /gluon
        - NOT from the bot itself (author != "gluon-agent", "gluon-bot", etc.)
        """
        body = comment.get("body", "")
        author = comment.get("author", "").lower()

        # Ignore bot's own comments
        if any(bot in author for bot in ["gluon", "bot", "github-actions"]):
            return False

        # Check for trigger patterns
        for pattern in TRIGGER_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                return True

        return False

    async def check_ci_failures(self, run: ExecutionRun) -> list[dict] | None:
        """
        Check for CI failures on current head commit.

        Args:
            run: ExecutionRun to check CI for

        Returns:
            List of failure dicts or None if no failures
        """
        if not run.git_commit_sha:
            return None

        project = self.store.get_project(run.project_id)
        if not project:
            return None

        # Don't recheck the same commit
        if run.last_check_sha == run.git_commit_sha:
            return None

        # Get failed checks
        failures = await self.git_manager.get_failed_checks(project.expanded_path, run.git_commit_sha)

        if failures:
            return failures

        return None

    async def auto_resume_for_comment(self, run: ExecutionRun, comment: dict) -> ExecutionRun | None:
        """
        Resume the run with context about the comment.

        Args:
            run: ExecutionRun to resume
            comment: Comment dict that triggered resume

        Returns:
            Updated ExecutionRun or None if resume failed
        """
        # Build file context if it's a review comment
        file_context = ""
        if comment.get("path"):
            file_context = f"\n**File:** `{comment['path']}`"
            if comment.get("line"):
                file_context += f" (line {comment['line']})"

        # Generate prompt
        prompt = COMMENT_RESUME_PROMPT.format(
            pr_number=run.pr_number,
            author=comment.get("author", "unknown"),
            comment_body=comment.get("body", ""),
            file_context=file_context,
        ).strip()

        try:
            # Update tracking before resume
            run.last_comment_id = comment.get("id")
            run.auto_resume_count = (run.auto_resume_count or 0) + 1
            self.store.update_run(run)

            # Resume the run
            updated_run = await self.runner.resume_in_place(
                run_id=run.id,
                new_prompt=prompt,
                wait=False,
                initiator="pr-monitor:comment",
            )

            logger.info(f"Auto-resumed run {run.id[:8]} for comment from @{comment.get('author')}")
            return updated_run

        except ValueError as e:
            logger.warning(f"Failed to auto-resume run {run.id[:8]}: {e}")
            return None

    async def auto_resume_for_ci_failure(self, run: ExecutionRun, failures: list[dict]) -> ExecutionRun | None:
        """
        Resume the run with CI failure details.

        Args:
            run: ExecutionRun to resume
            failures: List of failed check dicts

        Returns:
            Updated ExecutionRun or None if resume failed
        """
        if not failures:
            return None

        # Use the first failure for the prompt (usually the most important)
        failure = failures[0]
        check_name = failure.get("name", "Unknown")

        # Use Vercel-specific prompt if applicable
        is_vercel = "vercel" in check_name.lower()

        if is_vercel:
            prompt = VERCEL_FAILURE_PROMPT.format(
                pr_number=run.pr_number,
                output_summary=failure.get("output_summary") or "No summary available",
                output_text=failure.get("output_text") or "",
                details_url=failure.get("details_url") or "N/A",
            ).strip()
        else:
            prompt = CI_FAILURE_RESUME_PROMPT.format(
                check_name=check_name,
                pr_number=run.pr_number,
                output_title=failure.get("output_title") or "No title",
                output_summary=failure.get("output_summary") or "No summary available",
                output_text=failure.get("output_text") or "",
                details_url=failure.get("details_url") or "N/A",
            ).strip()

        try:
            # Update tracking before resume
            run.last_check_sha = run.git_commit_sha
            run.auto_resume_count = (run.auto_resume_count or 0) + 1
            self.store.update_run(run)

            # Resume the run
            updated_run = await self.runner.resume_in_place(
                run_id=run.id,
                new_prompt=prompt,
                wait=False,
                initiator=f"pr-monitor:ci-{check_name}",
            )

            logger.info(f"Auto-resumed run {run.id[:8]} for CI failure: {check_name}")
            return updated_run

        except ValueError as e:
            logger.warning(f"Failed to auto-resume run {run.id[:8]}: {e}")
            return None

    async def post_pr_comment(self, run: ExecutionRun, message: str) -> bool:
        """
        Post a comment on the PR.

        Args:
            run: ExecutionRun with PR info
            message: Comment body text

        Returns:
            True if comment was posted successfully
        """
        if not run.pr_number:
            return False

        project = self.store.get_project(run.project_id)
        if not project:
            return False

        return await self.git_manager.post_pr_comment(project.expanded_path, run.pr_number, message)

    async def post_pr_completion_comment(self, run: ExecutionRun) -> bool:
        """
        Post a comment when changes have been pushed.

        Args:
            run: ExecutionRun with PR info

        Returns:
            True if comment was posted successfully
        """
        message = f"Changes pushed. Please review the updates.\n\n*Auto-resume #{run.auto_resume_count} by Gluon Agent*"
        return await self.post_pr_comment(run, message)

    def should_monitor_run(self, run: ExecutionRun) -> bool:
        """
        Check if a run should be monitored for PR events.

        Criteria:
        - Status is REVIEW or COMPLETED
        - Has open PR
        - Auto-resume is enabled
        - Haven't hit max auto-resumes
        """
        if run.status not in (RunStatus.REVIEW, RunStatus.COMPLETED):
            return False

        if run.pr_status != "open":
            return False

        if not run.auto_resume_enabled:
            return False

        if (run.auto_resume_count or 0) >= MAX_AUTO_RESUMES:
            return False

        return True
