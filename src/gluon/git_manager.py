"""Git synchronization manager for Gluon Agent."""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from gluon.models import GitStatus, GitSyncResult, Project
from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Environment configuration
GIT_ENABLED = os.getenv("GLUON_GIT_ENABLED", "true").lower() == "true"
GIT_SYNC_INTERVAL = int(os.getenv("GLUON_GIT_SYNC_INTERVAL", "300"))
GIT_AUTO_COMMIT = os.getenv("GLUON_GIT_AUTO_COMMIT", "true").lower() == "true"
GIT_AUTO_PUSH = os.getenv("GLUON_GIT_AUTO_PUSH", "true").lower() == "true"
GIT_COMMIT_PREFIX = os.getenv("GLUON_GIT_COMMIT_PREFIX", "gluon:")


class GitManager:
    """Manages git synchronization for all projects."""

    def __init__(self, store: GluonStore):
        self.store = store
        self._sync_task: asyncio.Task | None = None
        self._sync_interval = GIT_SYNC_INTERVAL
        self._stop_event = asyncio.Event()

    # ========== Git Command Helpers ==========

    async def _run_git(self, cwd: Path, *args: str, check: bool = False) -> tuple[int, str, str]:
        """Run a git command and return (returncode, stdout, stderr)."""
        cmd = ["git", *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            returncode = proc.returncode or 0
            stdout_str = stdout.decode().strip()
            stderr_str = stderr.decode().strip()

            if check and returncode != 0:
                logger.warning(f"git {args[0]} failed in {cwd}: {stderr_str}")

            return returncode, stdout_str, stderr_str
        except FileNotFoundError:
            return 1, "", "git command not found"
        except Exception as e:
            return 1, "", str(e)

    async def _is_git_repo(self, path: Path) -> bool:
        """Check if path is inside a git repository."""
        rc, _, _ = await self._run_git(path, "rev-parse", "--git-dir")
        return rc == 0

    async def _get_branch(self, path: Path) -> str | None:
        """Get current branch name."""
        rc, stdout, _ = await self._run_git(path, "branch", "--show-current")
        return stdout if rc == 0 and stdout else None

    async def _get_remote(self, path: Path) -> tuple[str | None, str | None]:
        """Get remote name and URL for the current branch."""
        # Get the tracking remote for current branch
        rc, remote, _ = await self._run_git(path, "config", "--get", "branch.$(git branch --show-current).remote")
        if rc != 0:
            # Fallback to origin if it exists
            rc, remotes, _ = await self._run_git(path, "remote")
            if rc == 0 and "origin" in remotes.split("\n"):
                remote = "origin"
            else:
                return None, None

        # Get remote URL
        rc, url, _ = await self._run_git(path, "remote", "get-url", remote)
        return (remote, url) if rc == 0 else (remote, None)

    async def _get_uncommitted_count(self, path: Path) -> int:
        """Count uncommitted changes (staged + unstaged + untracked)."""
        rc, stdout, _ = await self._run_git(path, "status", "--porcelain")
        if rc != 0:
            return 0
        return len([line for line in stdout.split("\n") if line.strip()])

    async def _get_ahead_behind(self, path: Path, remote: str | None) -> tuple[int, int]:
        """Get commits ahead/behind remote tracking branch."""
        if not remote:
            return 0, 0

        # Get current branch
        branch = await self._get_branch(path)
        if not branch:
            return 0, 0

        # Get ahead/behind counts
        rc, stdout, _ = await self._run_git(path, "rev-list", "--left-right", "--count", f"{remote}/{branch}...HEAD")
        if rc != 0:
            return 0, 0

        try:
            parts = stdout.split()
            if len(parts) >= 2:
                behind = int(parts[0])
                ahead = int(parts[1])
                return ahead, behind
        except (ValueError, IndexError):
            pass
        return 0, 0

    async def _get_last_commit_time(self, path: Path) -> datetime | None:
        """Get timestamp of the last commit."""
        rc, stdout, _ = await self._run_git(path, "log", "-1", "--format=%cI")
        if rc == 0 and stdout:
            try:
                return datetime.fromisoformat(stdout)
            except ValueError:
                pass
        return None

    async def _get_commit_sha(self, path: Path) -> str | None:
        """Get the current HEAD commit SHA."""
        rc, stdout, _ = await self._run_git(path, "rev-parse", "HEAD")
        return stdout if rc == 0 and stdout else None

    async def _get_pr_info(self, path: Path, branch: str | None = None) -> dict | None:
        """
        Get PR info for the current branch using GitHub CLI.
        Returns dict with: number, url, state (open/merged/closed/draft)
        """
        if not branch:
            branch = await self._get_branch(path)
            if not branch:
                return None

        try:
            # Use gh pr view to get PR info for this branch
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "view", branch, "--json", "number,url,state,isDraft",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return None

            import json as json_module

            data = json_module.loads(stdout.decode())
            # Map state to our pr_status values
            state = data.get("state", "").lower()
            if data.get("isDraft"):
                state = "draft"
            elif state == "merged":
                state = "merged"
            elif state == "closed":
                state = "closed"
            else:
                state = "open"

            return {
                "number": data.get("number"),
                "url": data.get("url"),
                "status": state,
            }
        except (FileNotFoundError, Exception):
            return None

    # ========== Status Operations ==========

    async def refresh_status(self, project: Project) -> GitStatus:
        """Fetch and update git status for a single project."""
        path = project.path

        # Check if git repo
        is_repo = await self._is_git_repo(path)
        if not is_repo:
            status = GitStatus(is_git_repo=False)
            self.store.update_git_status(project.id, status)
            return status

        # Gather status info
        branch = await self._get_branch(path)
        remote, remote_url = await self._get_remote(path)
        uncommitted = await self._get_uncommitted_count(path)
        last_commit = await self._get_last_commit_time(path)

        # Fetch from remote to get accurate ahead/behind
        if remote:
            await self._run_git(path, "fetch", remote, "--quiet")

        ahead, behind = await self._get_ahead_behind(path, remote)

        status = GitStatus(
            is_git_repo=True,
            branch=branch,
            remote=remote,
            remote_url=remote_url,
            has_uncommitted=uncommitted > 0,
            uncommitted_count=uncommitted,
            commits_ahead=ahead,
            commits_behind=behind,
            last_fetch_at=datetime.now() if remote else None,
            last_commit_at=last_commit,
        )

        # Cache in database
        self.store.update_git_status(project.id, status)
        logger.debug(f"Updated git status for {project.name}: {status}")
        return status

    async def refresh_all_statuses(self) -> dict[str, GitStatus]:
        """Fetch and update git status for all projects."""
        projects = self.store.list_projects()
        results: dict[str, GitStatus] = {}

        for project in projects:
            try:
                status = await self.refresh_status(project)
                results[project.id] = status
            except Exception as e:
                logger.error(f"Failed to refresh git status for {project.name}: {e}")

        return results

    def get_cached_status(self, project: Project) -> GitStatus | None:
        """Get cached git status from database (no git operations)."""
        return self.store.get_git_status(project.id)

    # ========== Sync Operations ==========

    async def pre_task_sync(self, project: Project) -> GitSyncResult:
        """
        Prepare project for task execution:
        1. If uncommitted changes -> auto-commit
        2. Fetch from remote
        3. If behind remote -> fast-forward (fail if diverged)
        """
        if not GIT_ENABLED:
            return GitSyncResult.skip("Git sync disabled")

        path = project.path

        # Check if git repo
        if not await self._is_git_repo(path):
            return GitSyncResult.skip("Not a git repository")

        actions = []
        files_committed = 0

        # Step 1: Auto-commit uncommitted changes
        if GIT_AUTO_COMMIT:
            uncommitted = await self._get_uncommitted_count(path)
            if uncommitted > 0:
                # Stage all changes
                rc, _, stderr = await self._run_git(path, "add", "-A")
                if rc != 0:
                    return GitSyncResult.fail(f"Failed to stage changes: {stderr}")

                # Commit
                commit_msg = f"{GIT_COMMIT_PREFIX} auto-commit before task"
                rc, _, stderr = await self._run_git(path, "commit", "-m", commit_msg)
                if rc != 0 and "nothing to commit" not in stderr:
                    return GitSyncResult.fail(f"Failed to commit: {stderr}")

                if rc == 0:
                    files_committed = uncommitted
                    actions.append("commit")
                    logger.info(f"Auto-committed {uncommitted} changes in {project.name}")

        # Step 2: Fetch from remote
        remote, _ = await self._get_remote(path)
        if remote:
            rc, _, stderr = await self._run_git(path, "fetch", remote, "--quiet")
            if rc != 0:
                logger.warning(f"Failed to fetch from {remote}: {stderr}")
                # Continue anyway - might work on stale code

        # Step 3: Check if behind and fast-forward
        ahead, behind = await self._get_ahead_behind(path, remote)

        if behind > 0:
            if ahead > 0:
                # Diverged - cannot auto-resolve
                return GitSyncResult.fail(
                    f"Branch has diverged: {ahead} commits ahead, {behind} behind. Manual merge required."
                )

            # Fast-forward
            rc, _, stderr = await self._run_git(path, "pull", "--ff-only")
            if rc != 0:
                return GitSyncResult.fail(f"Failed to fast-forward: {stderr}")

            actions.append("pull")
            logger.info(f"Fast-forwarded {behind} commits in {project.name}")

        # Update status in DB
        await self.refresh_status(project)

        if not actions:
            return GitSyncResult.ok("none", "Already up to date")

        action_str = "+".join(actions)
        msg_parts = []
        if files_committed > 0:
            msg_parts.append(f"committed {files_committed} files")
        if behind > 0:
            msg_parts.append(f"pulled {behind} commits")

        return GitSyncResult.ok(
            action_str,
            ", ".join(msg_parts).capitalize(),
            files_committed=files_committed,
            commits_pulled=behind,
        )

    async def post_task_sync(
        self,
        project: Project,
        commit_message: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> GitSyncResult:
        """
        Finalize after task completion:
        1. Stage all changes
        2. Commit with message
        3. Push to remote
        """
        if not GIT_ENABLED:
            return GitSyncResult.skip("Git sync disabled")

        path = project.path

        # Check if git repo
        if not await self._is_git_repo(path):
            return GitSyncResult.skip("Not a git repository")

        # Check for changes
        uncommitted = await self._get_uncommitted_count(path)
        if uncommitted == 0:
            return GitSyncResult.ok("none", "No changes to commit")

        actions = []
        files_committed = 0
        commits_pushed = 0

        # Step 1: Stage all changes
        rc, _, stderr = await self._run_git(path, "add", "-A")
        if rc != 0:
            return GitSyncResult.fail(f"Failed to stage changes: {stderr}")

        # Step 2: Commit with detailed message
        full_message = f"{GIT_COMMIT_PREFIX} {commit_message}\n\n"
        full_message += "Auto-committed by Gluon Agent\n"
        if session_id:
            full_message += f"Session: {session_id}\n"
        if run_id:
            full_message += f"Run: {run_id}\n"

        rc, _, stderr = await self._run_git(path, "commit", "-m", full_message)
        if rc != 0:
            if "nothing to commit" in stderr:
                return GitSyncResult.ok("none", "No changes to commit")
            return GitSyncResult.fail(f"Failed to commit: {stderr}")

        files_committed = uncommitted
        actions.append("commit")
        logger.info(f"Committed {files_committed} changes in {project.name}")

        # Step 3: Push to remote
        if GIT_AUTO_PUSH:
            remote, _ = await self._get_remote(path)
            if remote:
                rc, _, stderr = await self._run_git(path, "push")
                if rc != 0:
                    # Try pull --rebase then push again
                    logger.warning(f"Push failed, attempting pull --rebase: {stderr}")
                    rc2, _, stderr2 = await self._run_git(path, "pull", "--rebase")
                    if rc2 != 0:
                        return GitSyncResult.fail(f"Push rejected and rebase failed: {stderr2}")

                    rc3, _, stderr3 = await self._run_git(path, "push")
                    if rc3 != 0:
                        return GitSyncResult.fail(f"Push failed after rebase: {stderr3}")

                commits_pushed = 1  # At least the one we just made
                actions.append("push")
                logger.info(f"Pushed changes to {remote} in {project.name}")

                # Update last_push_at
                status = self.get_cached_status(project)
                if status:
                    status.last_push_at = datetime.now()
                    self.store.update_git_status(project.id, status)

        # Update status in DB
        await self.refresh_status(project)

        action_str = "+".join(actions)
        msg_parts = [f"committed {files_committed} files"]
        if commits_pushed > 0:
            msg_parts.append("pushed to remote")

        return GitSyncResult.ok(
            action_str,
            ", ".join(msg_parts).capitalize(),
            files_committed=files_committed,
            commits_pushed=commits_pushed,
        )

    # ========== Worktree Finalization ==========

    async def push_branch_and_create_pr(
        self,
        project_path: Path,
        branch_name: str,
        prompt: str,
        run_id: str,
    ) -> dict:
        """
        Push branch to remote and create a PR for worktree runs.

        Args:
            project_path: Path to the worktree/project
            branch_name: The branch name to push
            prompt: The original task prompt (used for PR title)
            run_id: The run ID (used in PR body)

        Returns:
            dict with pr_number, pr_url, pr_status (or None values if failed)
        """
        result = {
            "pr_number": None,
            "pr_url": None,
            "pr_status": None,
            "pushed": False,
            "error": None,
        }

        # Check if git repo
        if not await self._is_git_repo(project_path):
            result["error"] = "Not a git repository"
            return result

        # Get remote
        remote, _ = await self._get_remote(project_path)
        if not remote:
            result["error"] = "No remote configured"
            return result

        # Push branch to remote (set upstream)
        rc, _, stderr = await self._run_git(
            project_path, "push", "-u", remote, branch_name
        )
        if rc != 0:
            result["error"] = f"Failed to push: {stderr}"
            logger.warning(f"Failed to push branch {branch_name}: {stderr}")
            return result

        result["pushed"] = True
        logger.info(f"Pushed branch {branch_name} to {remote}")

        # Create PR using GitHub CLI
        try:
            # Generate PR title from prompt (first 60 chars)
            pr_title = prompt[:60] + ("..." if len(prompt) > 60 else "")

            # Create PR body
            pr_body = f"""## Summary
{prompt}

---
🤖 Auto-generated by Gluon Agent
Run ID: `{run_id}`
"""
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "create",
                "--title", pr_title,
                "--body", pr_body,
                "--head", branch_name,
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr_bytes = await proc.communicate()

            if proc.returncode != 0:
                stderr_str = stderr_bytes.decode().strip()
                # Check if PR already exists
                if "already exists" in stderr_str.lower():
                    logger.info(f"PR already exists for branch {branch_name}")
                else:
                    result["error"] = f"Failed to create PR: {stderr_str}"
                    logger.warning(f"Failed to create PR: {stderr_str}")
            else:
                # Parse PR URL from stdout
                pr_url = stdout.decode().strip()
                result["pr_url"] = pr_url
                # Extract PR number from URL
                if "/pull/" in pr_url:
                    try:
                        result["pr_number"] = int(pr_url.split("/pull/")[-1])
                    except ValueError:
                        pass
                result["pr_status"] = "open"
                logger.info(f"Created PR: {pr_url}")

        except FileNotFoundError:
            result["error"] = "GitHub CLI (gh) not installed"
            logger.warning("GitHub CLI (gh) not found, skipping PR creation")
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"Error creating PR: {e}")

        # If we didn't create a PR, try to get existing PR info
        if not result["pr_number"]:
            pr_info = await self._get_pr_info(project_path, branch_name)
            if pr_info:
                result["pr_number"] = pr_info.get("number")
                result["pr_url"] = pr_info.get("url")
                result["pr_status"] = pr_info.get("status")

        return result

    # ========== Run Git Capture ==========

    async def capture_run_git_info(self, project_path: Path) -> dict:
        """
        Capture git info for a completed run.
        Returns dict with branch_name, git_commit_sha, pr_number, pr_url, pr_status.
        """
        result = {
            "branch_name": None,
            "git_commit_sha": None,
            "pr_number": None,
            "pr_url": None,
            "pr_status": None,
        }

        # Check if git repo
        if not await self._is_git_repo(project_path):
            return result

        # Get branch
        result["branch_name"] = await self._get_branch(project_path)

        # Get commit SHA
        result["git_commit_sha"] = await self._get_commit_sha(project_path)

        # Get PR info if branch exists
        if result["branch_name"]:
            pr_info = await self._get_pr_info(project_path, result["branch_name"])
            if pr_info:
                result["pr_number"] = pr_info.get("number")
                result["pr_url"] = pr_info.get("url")
                result["pr_status"] = pr_info.get("status")

        return result

    # ========== Background Sync ==========

    async def start_background_sync(self, interval_seconds: int | None = None) -> None:
        """Start background fetch loop for all projects."""
        if not GIT_ENABLED:
            logger.info("Git sync disabled, not starting background sync")
            return

        if self._sync_task is not None:
            logger.warning("Background sync already running")
            return

        if interval_seconds:
            self._sync_interval = interval_seconds

        self._stop_event.clear()
        self._sync_task = asyncio.create_task(self._background_sync_loop())
        logger.info(f"Started background git sync (interval: {self._sync_interval}s)")

    async def stop_background_sync(self) -> None:
        """Stop background fetch loop."""
        if self._sync_task is None:
            return

        self._stop_event.set()
        self._sync_task.cancel()
        try:
            await self._sync_task
        except asyncio.CancelledError:
            pass
        self._sync_task = None
        logger.info("Stopped background git sync")

    async def _background_sync_loop(self) -> None:
        """Background loop that periodically fetches all projects."""
        while not self._stop_event.is_set():
            try:
                # Wait for interval or stop event
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._sync_interval,
                    )
                    break  # Stop event was set
                except TimeoutError:
                    pass  # Timeout - time to sync

                # Refresh all project statuses
                logger.debug("Running background git sync")
                statuses = await self.refresh_all_statuses()

                # Log warnings for projects that need attention
                for project_id, status in statuses.items():
                    if status.is_diverged:
                        project = self.store.get_project(project_id)
                        name = project.name if project else project_id
                        logger.warning(
                            f"Project {name} has diverged: {status.commits_ahead} ahead, {status.commits_behind} behind"
                        )
                    elif status.commits_behind > 0:
                        project = self.store.get_project(project_id)
                        name = project.name if project else project_id
                        logger.info(f"Project {name} is {status.commits_behind} commits behind")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in background git sync: {e}")
                # Continue running despite errors
