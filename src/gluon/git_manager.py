"""Git synchronization manager for Gluon Agent."""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from gluon.models import (
    CommitFileSnapshot,
    CommitSnapshot,
    FileChangeSnapshot,
    GitStatus,
    GitSyncResult,
    Project,
    utc_now,
)
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

    def __init__(self, store: GluonStore, workspace_id: str | None = None):
        self.store = store
        self.workspace_id = workspace_id
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

    def _get_git_author_config(self) -> list[str]:
        """
        Get git author config flags for commit commands.

        Resolution order:
        1. Workspace setting override (if workspace_id set)
        2. Global settings DB (git_user_name, git_user_email)
        3. Environment variables (GIT_USER_NAME, GIT_USER_EMAIL)
        4. Empty list (use system git config)
        """
        config_args: list[str] = []

        # Use resolve_setting for workspace > global resolution
        name = self.store.resolve_setting("git_user_name", "", self.workspace_id)
        email = self.store.resolve_setting("git_user_email", "", self.workspace_id)

        # Fall back to environment variables
        if not name:
            name = os.getenv("GIT_USER_NAME", "")
        if not email:
            email = os.getenv("GIT_USER_EMAIL", "")

        # Build config args if we have values
        if name:
            config_args.extend(["-c", f"user.name={name}"])
        if email:
            config_args.extend(["-c", f"user.email={email}"])

        return config_args

    async def _run_git_with_author(self, cwd: Path, *args: str, check: bool = False) -> tuple[int, str, str]:
        """Run a git command with author config injected (for commits)."""
        author_config = self._get_git_author_config()
        # Insert author config after 'git' command
        full_args = author_config + list(args)
        return await self._run_git(cwd, *full_args, check=check)

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
        Returns dict with: number, url, state (open/merged/closed/draft), mergeable
        """
        if not branch:
            branch = await self._get_branch(path)
            if not branch:
                return None

        try:
            # Use gh pr view to get PR info for this branch
            # Include mergeable and mergeStateStatus for conflict detection
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "pr",
                "view",
                branch,
                "--json",
                "number,url,state,isDraft,mergeable,mergeStateStatus",
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

            # Get mergeable status (MERGEABLE, CONFLICTING, UNKNOWN)
            mergeable = data.get("mergeable", "UNKNOWN")

            return {
                "number": data.get("number"),
                "url": data.get("url"),
                "status": state,
                "mergeable": mergeable,
            }
        except (FileNotFoundError, Exception):
            return None

    # ========== Status Operations ==========

    async def refresh_status(self, project: Project) -> GitStatus:
        """Fetch and update git status for a single project."""
        path = project.expanded_path

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

        path = project.expanded_path

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

                # Commit (with author config)
                commit_msg = f"{GIT_COMMIT_PREFIX} auto-commit before task"
                rc, _, stderr = await self._run_git_with_author(path, "commit", "-m", commit_msg)
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
            if rc != 0 and "untracked working tree files would be overwritten" in stderr:
                # Untracked files conflict with incoming changes — stash and retry
                logger.info("Stashing untracked files that conflict with pull")
                src, _, _ = await self._run_git(
                    path, "stash", "push", "--include-untracked", "-m", "gluon-pull-conflict"
                )
                if src == 0:
                    rc, _, stderr = await self._run_git(path, "pull", "--ff-only")
                    # Restore stash (untracked files may have been superseded by pull)
                    await self._run_git(path, "stash", "pop")
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

        path = project.expanded_path

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

        rc, _, stderr = await self._run_git_with_author(path, "commit", "-m", full_message)
        if rc != 0:
            if "nothing to commit" in stderr:
                return GitSyncResult.ok("none", "No changes to commit")
            # Provide actionable error for author identity issues
            if "Author identity unknown" in stderr:
                return GitSyncResult.fail(
                    "Git author not configured. Set git_user_name and git_user_email "
                    "in Settings > Preferences, or set GIT_USER_NAME and GIT_USER_EMAIL "
                    "environment variables."
                )
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

    async def auto_commit_changes(
        self,
        path: Path,
        message: str | None = None,
        run_id: str | None = None,
    ) -> dict:
        """
        Auto-commit any uncommitted changes in the working directory.

        This is a safety net to ensure work is not lost if the agent
        doesn't commit before exiting.

        Args:
            path: Repository path (worktree or main repo)
            message: Optional commit message (auto-generated if not provided)
            run_id: Optional run ID for commit message context

        Returns:
            dict with: committed (bool), files_count (int), message (str)
        """
        result = {
            "committed": False,
            "files_count": 0,
            "message": "",
        }

        # Check if git repo
        if not await self._is_git_repo(path):
            result["message"] = "Not a git repository"
            return result

        # Check for uncommitted changes
        uncommitted = await self._get_uncommitted_count(path)
        if uncommitted == 0:
            result["message"] = "No uncommitted changes"
            return result

        # Stage all changes
        rc, _, stderr = await self._run_git(path, "add", "-A")
        if rc != 0:
            result["message"] = f"Failed to stage changes: {stderr}"
            return result

        # Generate commit message if not provided
        if not message:
            message = "chore: auto-commit changes from gluon agent"
            if run_id:
                message += f"\n\nRun ID: {run_id}"

        # Commit (with author config)
        rc, _, stderr = await self._run_git_with_author(path, "commit", "-m", message)
        if rc != 0:
            if "nothing to commit" in stderr:
                result["message"] = "No changes to commit after staging"
                return result
            # Provide actionable error for author identity issues
            if "Author identity unknown" in stderr:
                result["message"] = (
                    "Git author not configured. Set git_user_name and git_user_email "
                    "in Settings > Preferences, or set GIT_USER_NAME and GIT_USER_EMAIL "
                    "environment variables."
                )
                return result
            result["message"] = f"Failed to commit: {stderr}"
            return result

        result["committed"] = True
        result["files_count"] = uncommitted
        result["message"] = f"Auto-committed {uncommitted} file(s)"
        logger.info(f"Auto-committed {uncommitted} changes in {path}")

        return result

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
        result: dict[str, int | str | bool | None] = {
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
        rc, _, stderr = await self._run_git(project_path, "push", "-u", remote, branch_name)
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
                "gh",
                "pr",
                "create",
                "--title",
                pr_title,
                "--body",
                pr_body,
                "--head",
                branch_name,
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

    async def capture_run_git_info(self, project_path: Path) -> dict[str, int | str | None]:
        """
        Capture git info for a completed run.
        Returns dict with branch_name, git_commit_sha, pr_number, pr_url, pr_status.
        """
        result: dict[str, int | str | None] = {
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

    # ========== Commits and File Changes ==========

    async def get_commit_count(
        self,
        path: Path,
        branch_name: str,
        base_branch: str = "main",
    ) -> int:
        """Get count of commits on branch since diverging from base (lightweight)."""
        if not await self._is_git_repo(path):
            return 0

        # Get merge base
        rc, merge_base, _ = await self._run_git(path, "merge-base", base_branch, branch_name)
        if rc != 0:
            return 0

        # Count commits
        rc, stdout, _ = await self._run_git(path, "rev-list", "--count", f"{merge_base.strip()}..{branch_name}")
        if rc == 0:
            try:
                return int(stdout.strip())
            except ValueError:
                pass
        return 0

    async def get_file_count(
        self,
        path: Path,
        branch_name: str,
        base_branch: str = "main",
    ) -> int:
        """Get count of files changed on branch since diverging from base (lightweight)."""
        if not await self._is_git_repo(path):
            return 0

        # Get merge base
        rc, merge_base, _ = await self._run_git(path, "merge-base", base_branch, branch_name)
        if rc != 0:
            return 0

        # Count files changed (using --numstat and counting lines)
        rc, stdout, _ = await self._run_git(path, "diff", "--numstat", f"{merge_base.strip()}..{branch_name}")
        if rc == 0 and stdout.strip():
            return len([line for line in stdout.strip().split("\n") if line.strip()])
        return 0

    async def get_branch_commits(
        self,
        path: Path,
        branch_name: str | None = None,
        base_branch: str = "main",
    ) -> list[dict]:
        """
        Get commits on the branch since it diverged from base.

        Args:
            path: Path to the repository
            branch_name: Branch to get commits from (default: current branch)
            base_branch: Base branch to compare against (default: main)

        Returns:
            List of commit dicts with sha, message, author, author_email, date
        """
        if not await self._is_git_repo(path):
            return []

        if not branch_name:
            branch_name = await self._get_branch(path)
            if not branch_name:
                return []

        # Get merge base to find where branches diverged
        rc, merge_base, _ = await self._run_git(path, "merge-base", base_branch, branch_name)
        if rc != 0:
            # No common ancestor - get all commits on branch
            merge_base = ""

        # Get commits from merge base to HEAD
        if merge_base:
            commit_range = f"{merge_base}..HEAD"
        else:
            commit_range = "HEAD"

        # Format: sha|subject|author name|author email|date ISO
        rc, stdout, _ = await self._run_git(
            path,
            "log",
            commit_range,
            "--format=%H|%s|%an|%ae|%cI",
            "--reverse",
        )
        if rc != 0 or not stdout:
            return []

        commits = []
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 4)
            if len(parts) >= 5:
                commits.append(
                    {
                        "sha": parts[0],
                        "message": parts[1],
                        "author": parts[2],
                        "author_email": parts[3],
                        "date": parts[4],
                    }
                )
        return commits

    async def get_changed_files(
        self,
        path: Path,
        branch_name: str | None = None,
        base_branch: str = "main",
    ) -> list[dict]:
        """
        Get files changed on the branch since it diverged from base.

        Args:
            path: Path to the repository
            branch_name: Branch to get changes from (default: current branch)
            base_branch: Base branch to compare against (default: main)

        Returns:
            List of file change dicts with file_path, additions, deletions, change_type
        """
        if not await self._is_git_repo(path):
            return []

        if not branch_name:
            branch_name = await self._get_branch(path)
            if not branch_name:
                return []

        # Get merge base to find where branches diverged
        rc, merge_base, _ = await self._run_git(path, "merge-base", base_branch, branch_name)
        if rc != 0:
            # No common ancestor - compare to empty tree
            merge_base = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # empty tree sha

        # Get diff stats with --numstat for accurate line counts
        rc, stdout, _ = await self._run_git(
            path,
            "diff",
            "--numstat",
            merge_base,
            "HEAD",
        )
        if rc != 0:
            return []

        files = []
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) >= 3:
                additions = int(parts[0]) if parts[0] != "-" else 0
                deletions = int(parts[1]) if parts[1] != "-" else 0
                file_path = parts[2]

                # Determine change type
                if additions > 0 and deletions == 0:
                    change_type = "added"
                elif deletions > 0 and additions == 0:
                    change_type = "deleted"
                elif additions == deletions == 0:
                    change_type = "renamed"
                else:
                    change_type = "modified"

                files.append(
                    {
                        "file_path": file_path,
                        "additions": additions,
                        "deletions": deletions,
                        "change_type": change_type,
                    }
                )
        return files

    async def get_commit_detail(
        self,
        path: Path,
        sha: str,
    ) -> dict:
        """
        Get detailed information for a specific commit.

        Args:
            path: Path to the repository
            sha: Commit SHA to get details for

        Returns:
            Dict with sha, message (full), author, author_email, date, files (list of changes)
        """
        if not await self._is_git_repo(path):
            return {}

        # Get full commit message (subject + body)
        rc, stdout, _ = await self._run_git(
            path,
            "log",
            "-1",
            "--format=%H|%an|%ae|%cI%n%B",
            sha,
        )
        if rc != 0 or not stdout:
            return {}

        lines = stdout.split("\n", 1)
        if not lines:
            return {}

        # Parse header line
        header_parts = lines[0].split("|", 3)
        if len(header_parts) < 4:
            return {}

        # Full message is everything after the header
        full_message = lines[1].strip() if len(lines) > 1 else ""

        # Get files changed in this commit
        rc, files_stdout, _ = await self._run_git(
            path,
            "diff-tree",
            "--no-commit-id",
            "--numstat",
            "-r",
            sha,
        )

        files = []
        if rc == 0 and files_stdout:
            for line in files_stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t", 2)
                if len(parts) >= 3:
                    additions = int(parts[0]) if parts[0] != "-" else 0
                    deletions = int(parts[1]) if parts[1] != "-" else 0
                    file_path = parts[2]

                    # Determine change type based on additions/deletions
                    if additions > 0 and deletions == 0:
                        change_type = "added"
                    elif deletions > 0 and additions == 0:
                        change_type = "deleted"
                    elif additions == deletions == 0:
                        change_type = "renamed"
                    else:
                        change_type = "modified"

                    files.append(
                        {
                            "file_path": file_path,
                            "additions": additions,
                            "deletions": deletions,
                            "change_type": change_type,
                        }
                    )

        return {
            "sha": header_parts[0],
            "message": full_message,
            "author": header_parts[1],
            "author_email": header_parts[2],
            "date": header_parts[3],
            "files": files,
        }

    async def get_file_diff(
        self,
        path: Path,
        file_path: str,
        branch_name: str | None = None,
        base_branch: str = "main",
    ) -> dict:
        """
        Get unified diff for a specific file.

        Args:
            path: Path to the repository
            file_path: Path to the file within the repo
            branch_name: Branch to get diff from (default: current branch)
            base_branch: Base branch to compare against (default: main)

        Returns:
            Dict with file_path, diff (unified diff string), additions, deletions
        """
        if not await self._is_git_repo(path):
            return {"file_path": file_path, "diff": "", "additions": 0, "deletions": 0}

        if not branch_name:
            branch_name = await self._get_branch(path)
            if not branch_name:
                return {"file_path": file_path, "diff": "", "additions": 0, "deletions": 0}

        # Get merge base to find where branches diverged
        rc, merge_base, _ = await self._run_git(path, "merge-base", base_branch, branch_name)
        if rc != 0:
            # No common ancestor - compare to empty tree
            merge_base = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

        # Get unified diff for the specific file
        rc, diff_stdout, _ = await self._run_git(
            path,
            "diff",
            "--unified=3",
            merge_base,
            "HEAD",
            "--",
            file_path,
        )

        # Count additions and deletions from diff
        additions = 0
        deletions = 0
        if diff_stdout:
            for line in diff_stdout.split("\n"):
                if line.startswith("+") and not line.startswith("+++"):
                    additions += 1
                elif line.startswith("-") and not line.startswith("---"):
                    deletions += 1

        return {
            "file_path": file_path,
            "diff": diff_stdout if rc == 0 else "",
            "additions": additions,
            "deletions": deletions,
        }

    async def capture_branch_snapshots(
        self,
        path: Path,
        run_id: str,
        branch_name: str | None,
        base_branch: str = "main",
    ) -> tuple[list[CommitSnapshot], list[FileChangeSnapshot], list[CommitFileSnapshot]]:
        """
        Capture complete branch state as snapshots before merge/deletion.

        This method creates persistent records of commits and file changes
        that can be retrieved even after the branch is merged or deleted.

        Args:
            path: Path to the repository
            run_id: ID of the execution run to associate snapshots with
            branch_name: Branch to capture (required)
            base_branch: Base branch to compare against (default: main)

        Returns:
            Tuple of (commit_snapshots, file_change_snapshots, commit_file_snapshots)
            Returns empty lists if branch doesn't exist or has no changes.
        """
        if not branch_name:
            return [], [], []

        if not await self._is_git_repo(path):
            return [], [], []

        # Get all commits on branch
        commits = await self.get_branch_commits(path, branch_name, base_branch)
        if not commits:
            return [], [], []

        # Get all changed files
        files = await self.get_changed_files(path, branch_name, base_branch)

        # Get per-commit file details for detailed view
        commit_files_map: dict[str, list[dict]] = {}
        for commit in commits:
            detail = await self.get_commit_detail(path, commit["sha"])
            commit_files_map[commit["sha"]] = detail.get("files", [])

        # Build snapshot objects
        now = utc_now()
        commit_snapshots: list[CommitSnapshot] = []
        for i, c in enumerate(commits):
            # Parse date - commits return ISO format string
            commit_date = datetime.fromisoformat(c["date"])
            commit_snapshots.append(
                CommitSnapshot(
                    run_id=run_id,
                    sha=c["sha"],
                    message=c["message"],
                    author=c["author"],
                    author_email=c.get("author_email", ""),
                    date=commit_date,
                    ordinal=i + 1,
                    created_at=now,
                )
            )

        file_change_snapshots: list[FileChangeSnapshot] = [
            FileChangeSnapshot(
                run_id=run_id,
                file_path=f["file_path"],
                change_type=f["change_type"],
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                created_at=now,
            )
            for f in files
        ]

        # Per-commit files (link to commit snapshots)
        commit_file_snapshots: list[CommitFileSnapshot] = []
        for commit_snap in commit_snapshots:
            for f in commit_files_map.get(commit_snap.sha, []):
                commit_file_snapshots.append(
                    CommitFileSnapshot(
                        commit_snapshot_id=commit_snap.id,
                        file_path=f["file_path"],
                        change_type=f.get("change_type", "modified"),
                        additions=f.get("additions", 0),
                        deletions=f.get("deletions", 0),
                    )
                )

        logger.info(
            f"Captured snapshots for run {run_id}: "
            f"{len(commit_snapshots)} commits, "
            f"{len(file_change_snapshots)} files, "
            f"{len(commit_file_snapshots)} commit-file links"
        )

        return commit_snapshots, file_change_snapshots, commit_file_snapshots

    # ========== Local Merge Operation ==========

    async def merge_branch_locally(
        self,
        project_path: Path,
        branch_name: str,
        base_branch: str = "main",
        push_after_merge: bool = True,
    ) -> dict:
        """
        Merge a feature branch into the base branch locally and optionally push.
        GitHub will automatically close the PR when the merge is pushed.

        Args:
            project_path: Path to the repository (main repo, not worktree)
            branch_name: The feature branch to merge
            base_branch: The base branch to merge into (default: main)
            push_after_merge: Whether to push after merge (default: True)

        Returns:
            dict with: success, message, merged_commit_sha, error,
                       has_conflicts, conflicting_files
        """
        result = {
            "success": False,
            "message": "",
            "merged_commit_sha": None,
            "error": None,
            "has_conflicts": False,
            "conflicting_files": [],
        }

        # Check if git repo
        if not await self._is_git_repo(project_path):
            result["error"] = "Not a git repository"
            return result

        # Pre-flight: detect and recover from broken merge state
        # (unmerged paths left over from a previous failed merge)
        rc, unmerged_out, _ = await self._run_git(project_path, "diff", "--name-only", "--diff-filter=U")
        if rc == 0 and unmerged_out.strip():
            logger.warning("Detected unmerged paths from a previous failed merge, resetting to clean state")
            await self._run_git(project_path, "merge", "--abort")
            # If merge --abort didn't clear it (no MERGE_HEAD), force-reset
            _, still_unmerged, _ = await self._run_git(project_path, "diff", "--name-only", "--diff-filter=U")
            if still_unmerged.strip():
                await self._run_git(project_path, "reset", "HEAD")
                await self._run_git(project_path, "checkout", "--", ".")

        # Check for uncommitted changes and stash if needed
        # Use --include-untracked so untracked files don't block pull/merge
        uncommitted = await self._get_uncommitted_count(project_path)
        stashed = False
        if uncommitted > 0:
            logger.info(f"Stashing {uncommitted} uncommitted changes before merge")
            rc, _, stderr = await self._run_git(
                project_path, "stash", "push", "--include-untracked", "-m", f"gluon-merge-{branch_name}"
            )
            if rc != 0:
                result["error"] = f"Failed to stash uncommitted changes: {stderr}"
                return result
            stashed = True

        # Ensure we're on the base branch
        current_branch = await self._get_branch(project_path)
        if current_branch != base_branch:
            # Checkout base branch
            rc, _, stderr = await self._run_git(project_path, "checkout", base_branch)
            if rc != 0:
                # Restore stash if we created one
                if stashed:
                    await self._run_git(project_path, "stash", "pop")
                result["error"] = f"Failed to checkout {base_branch}: {stderr}"
                return result

        # Pull latest changes on base branch (only if tracking info exists)
        # Check if the base branch has an upstream configured
        rc, upstream, _ = await self._run_git(project_path, "rev-parse", "--abbrev-ref", f"{base_branch}@{{upstream}}")
        has_upstream = rc == 0 and upstream.strip()

        if has_upstream:
            rc, _, stderr = await self._run_git(project_path, "pull", "--ff-only")
            if rc != 0:
                # If fast-forward fails, try a regular pull
                rc, _, stderr = await self._run_git(project_path, "pull")
                if rc != 0:
                    # Restore stash if we created one
                    if stashed:
                        await self._run_git(project_path, "stash", "pop")
                    result["error"] = f"Failed to pull latest {base_branch}: {stderr}"
                    return result
        else:
            logger.debug(f"No upstream tracking for {base_branch}, skipping pull")

        # Merge the feature branch (use author config from settings for merge commit)
        rc, stdout, stderr = await self._run_git_with_author(project_path, "merge", branch_name, "--no-edit")
        if rc != 0:
            # Merge conflict - get list of conflicting files before aborting
            conflict_rc, conflict_stdout, _ = await self._run_git(
                project_path, "diff", "--name-only", "--diff-filter=U"
            )
            if conflict_rc == 0 and conflict_stdout.strip():
                conflicting_files = [f.strip() for f in conflict_stdout.strip().split("\n") if f.strip()]
                result["has_conflicts"] = True
                result["conflicting_files"] = conflicting_files
                result["error"] = (
                    f"Merge conflicts in {len(conflicting_files)} file(s): {', '.join(conflicting_files[:5])}"
                )
                if len(conflicting_files) > 5:
                    result["error"] += f" (and {len(conflicting_files) - 5} more)"
            else:
                result["error"] = f"Merge failed: {stderr}"

            # Abort the merge and verify cleanup
            await self._run_git(project_path, "merge", "--abort")
            # Verify no unmerged paths remain (abort can sometimes leave debris)
            rc_check, unmerged, _ = await self._run_git(project_path, "diff", "--name-only", "--diff-filter=U")
            if rc_check == 0 and unmerged.strip():
                logger.warning("Unmerged paths remain after merge --abort, resetting index")
                await self._run_git(project_path, "reset", "HEAD")
                await self._run_git(project_path, "checkout", "--", ".")
            # Restore stash if we created one
            if stashed:
                await self._run_git(project_path, "stash", "pop")
            return result

        # Get the merge commit SHA
        merge_sha = await self._get_commit_sha(project_path)

        # Push the merge to remote (if requested and has remote)
        remote, _ = await self._get_remote(project_path)
        pushed = False
        if push_after_merge and remote:
            rc, _, stderr = await self._run_git(project_path, "push")
            if rc != 0:
                # Restore stash if we created one
                if stashed:
                    await self._run_git(project_path, "stash", "pop")
                result["error"] = f"Failed to push merge: {stderr}"
                return result
            pushed = True

            # Delete remote branch after successful push
            await self._run_git(project_path, "push", remote, "--delete", branch_name)

        # Delete local feature branch
        await self._run_git(project_path, "branch", "-d", branch_name)

        # Restore stash after successful merge
        if stashed:
            logger.info("Restoring stashed changes after merge")
            await self._run_git(project_path, "stash", "pop")

        result["success"] = True
        if pushed:
            result["message"] = f"Successfully merged {branch_name} into {base_branch} and pushed"
        else:
            result["message"] = f"Successfully merged {branch_name} into {base_branch} (local only)"
        result["merged_commit_sha"] = merge_sha
        logger.info(f"Merged branch {branch_name} into {base_branch} (pushed: {pushed})")
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

    # ========== Advanced Git Operations ==========

    async def _detect_conflict_state(self, path: Path) -> dict[str, bool | str | int | list[str] | None]:
        """
        Detect if there's a rebase or merge in progress with conflicts.

        Returns dict with:
            is_rebase_in_progress, is_merge_in_progress, conflict_operation,
            conflicted_files, rebase_current_step, rebase_total_steps
        """
        result: dict[str, bool | str | int | list[str] | None] = {
            "is_rebase_in_progress": False,
            "is_merge_in_progress": False,
            "conflict_operation": None,
            "conflicted_files": [],
            "rebase_current_step": None,
            "rebase_total_steps": None,
        }

        git_dir = path / ".git"
        if not git_dir.exists():
            # Could be a worktree - find actual git dir
            rc, stdout, _ = await self._run_git(path, "rev-parse", "--git-dir")
            if rc == 0 and stdout:
                git_dir = Path(stdout) if Path(stdout).is_absolute() else path / stdout

        # Check for rebase in progress
        rebase_merge = git_dir / "rebase-merge"
        rebase_apply = git_dir / "rebase-apply"

        if rebase_merge.exists() or rebase_apply.exists():
            result["is_rebase_in_progress"] = True
            result["conflict_operation"] = "rebase"

            # Get rebase progress
            rebase_dir = rebase_merge if rebase_merge.exists() else rebase_apply
            msgnum_file = rebase_dir / "msgnum"
            end_file = rebase_dir / "end"

            if msgnum_file.exists() and end_file.exists():
                try:
                    result["rebase_current_step"] = int(msgnum_file.read_text().strip())
                    result["rebase_total_steps"] = int(end_file.read_text().strip())
                except (ValueError, OSError):
                    pass

        # Check for merge in progress
        merge_head = git_dir / "MERGE_HEAD"
        if merge_head.exists():
            result["is_merge_in_progress"] = True
            result["conflict_operation"] = "merge"

        # Check for cherry-pick in progress
        cherry_pick_head = git_dir / "CHERRY_PICK_HEAD"
        if cherry_pick_head.exists():
            result["conflict_operation"] = "cherry_pick"

        # Get conflicted files
        rc, stdout, _ = await self._run_git(path, "diff", "--name-only", "--diff-filter=U")
        if rc == 0 and stdout:
            result["conflicted_files"] = [f.strip() for f in stdout.split("\n") if f.strip()]

        return result

    async def detect_conflicts(self, path: Path) -> list[dict[str, str | int]]:
        """
        Get detailed information about conflicted files.

        Returns list of dicts with: file_path, conflict_markers_count
        """
        conflicts: list[dict[str, str | int]] = []

        # Get list of conflicted files
        rc, stdout, _ = await self._run_git(path, "diff", "--name-only", "--diff-filter=U")
        if rc != 0 or not stdout:
            return conflicts

        for file_path in stdout.strip().split("\n"):
            if not file_path:
                continue

            full_path = path / file_path
            marker_count = 0

            if full_path.exists():
                try:
                    content = full_path.read_text()
                    marker_count = content.count("<<<<<<<")
                except (OSError, UnicodeDecodeError):
                    pass

            conflicts.append(
                {
                    "file_path": file_path,
                    "conflict_markers_count": marker_count,
                }
            )

        return conflicts

    async def get_conflict_diff(self, path: Path, file_path: str) -> dict:
        """
        Get 3-way diff for a conflicted file.

        Returns dict with: base, ours, theirs content (or None if not available)
        """
        result = {
            "file_path": file_path,
            "base": None,
            "ours": None,
            "theirs": None,
            "merged": None,
        }

        # Get current (merged with conflicts) content
        full_path = path / file_path
        if full_path.exists():
            try:
                result["merged"] = full_path.read_text()
            except (OSError, UnicodeDecodeError):
                pass

        # Get base version (common ancestor)
        rc, stdout, _ = await self._run_git(path, "show", f":1:{file_path}")
        if rc == 0:
            result["base"] = stdout

        # Get ours version (HEAD)
        rc, stdout, _ = await self._run_git(path, "show", f":2:{file_path}")
        if rc == 0:
            result["ours"] = stdout

        # Get theirs version (incoming)
        rc, stdout, _ = await self._run_git(path, "show", f":3:{file_path}")
        if rc == 0:
            result["theirs"] = stdout

        return result

    async def resolve_conflict(self, path: Path, file_path: str, resolution: str) -> dict:
        """
        Resolve a conflict by choosing ours, theirs, or marking as resolved.

        Args:
            path: Repository path
            file_path: Path to conflicted file
            resolution: "ours", "theirs", or "resolved" (manual resolution done)

        Returns dict with: success, message
        """
        result = {"success": False, "message": ""}

        if resolution == "ours":
            rc, _, stderr = await self._run_git(path, "checkout", "--ours", file_path)
            if rc != 0:
                result["message"] = f"Failed to checkout ours: {stderr}"
                return result
        elif resolution == "theirs":
            rc, _, stderr = await self._run_git(path, "checkout", "--theirs", file_path)
            if rc != 0:
                result["message"] = f"Failed to checkout theirs: {stderr}"
                return result

        # Stage the resolved file
        rc, _, stderr = await self._run_git(path, "add", file_path)
        if rc != 0:
            result["message"] = f"Failed to stage resolved file: {stderr}"
            return result

        result["success"] = True
        result["message"] = f"Resolved {file_path} using {resolution}"
        return result

    # ========== Rebase Operations ==========

    async def rebase_branch(self, path: Path, onto_branch: str) -> dict:
        """
        Start a rebase onto another branch.

        Returns dict with: success, message, conflicts (list of files if conflict)
        """
        result = {"success": False, "message": "", "conflicts": []}

        # Check for existing operation
        conflict_state = await self._detect_conflict_state(path)
        if conflict_state["is_rebase_in_progress"] or conflict_state["is_merge_in_progress"]:
            result["message"] = f"Operation already in progress: {conflict_state['conflict_operation']}"
            return result

        rc, stdout, stderr = await self._run_git(path, "rebase", onto_branch)
        if rc != 0:
            # Check if it's a conflict
            if "conflict" in stderr.lower() or "could not apply" in stderr.lower():
                conflict_state = await self._detect_conflict_state(path)
                result["conflicts"] = conflict_state["conflicted_files"]
                result["message"] = f"Rebase conflict: {len(result['conflicts'])} file(s)"
            else:
                result["message"] = f"Rebase failed: {stderr}"
            return result

        result["success"] = True
        result["message"] = "Rebase completed successfully"
        return result

    async def rebase_continue(self, path: Path) -> dict:
        """
        Continue a rebase after resolving conflicts.

        Returns dict with: success, message, conflicts (list if more conflicts)
        """
        result = {"success": False, "message": "", "conflicts": []}

        rc, stdout, stderr = await self._run_git(path, "rebase", "--continue")
        if rc != 0:
            if "conflict" in stderr.lower() or "could not apply" in stderr.lower():
                conflict_state = await self._detect_conflict_state(path)
                result["conflicts"] = conflict_state["conflicted_files"]
                result["message"] = f"More conflicts: {len(result['conflicts'])} file(s)"
            else:
                result["message"] = f"Rebase continue failed: {stderr}"
            return result

        result["success"] = True
        result["message"] = "Rebase continued successfully"
        return result

    async def rebase_abort(self, path: Path) -> dict:
        """Abort an in-progress rebase."""
        result = {"success": False, "message": ""}

        rc, _, stderr = await self._run_git(path, "rebase", "--abort")
        if rc != 0:
            result["message"] = f"Rebase abort failed: {stderr}"
            return result

        result["success"] = True
        result["message"] = "Rebase aborted"
        return result

    async def rebase_skip(self, path: Path) -> dict:
        """Skip the current commit during rebase."""
        result = {"success": False, "message": "", "conflicts": []}

        rc, _, stderr = await self._run_git(path, "rebase", "--skip")
        if rc != 0:
            if "conflict" in stderr.lower():
                conflict_state = await self._detect_conflict_state(path)
                result["conflicts"] = conflict_state["conflicted_files"]
                result["message"] = f"More conflicts after skip: {len(result['conflicts'])} file(s)"
            else:
                result["message"] = f"Rebase skip failed: {stderr}"
            return result

        result["success"] = True
        result["message"] = "Skipped commit"
        return result

    # ========== Force Push Operations ==========

    async def check_force_push_needed(self, path: Path, branch: str | None = None) -> dict:
        """
        Check if a force push would be needed to push the current branch.

        Returns dict with: needed, commits_to_delete, reason
        """
        result = {"needed": False, "commits_to_delete": 0, "reason": ""}

        if not branch:
            branch = await self._get_branch(path)
            if not branch:
                result["reason"] = "Not on a branch"
                return result

        remote, _ = await self._get_remote(path)
        if not remote:
            result["reason"] = "No remote configured"
            return result

        # Fetch latest
        await self._run_git(path, "fetch", remote, "--quiet")

        # Check if remote branch exists
        rc, _, _ = await self._run_git(path, "rev-parse", "--verify", f"{remote}/{branch}")
        if rc != 0:
            result["reason"] = "Remote branch doesn't exist (new branch)"
            return result

        # Get commits that would be deleted on remote
        # These are commits in remote that are not ancestors of local
        rc, stdout, _ = await self._run_git(path, "rev-list", f"HEAD..{remote}/{branch}", "--count")
        if rc == 0 and stdout:
            try:
                commits_to_delete = int(stdout.strip())
                if commits_to_delete > 0:
                    result["needed"] = True
                    result["commits_to_delete"] = commits_to_delete
                    result["reason"] = f"Would delete {commits_to_delete} commit(s) from remote"
            except ValueError:
                pass

        return result

    async def force_push(self, path: Path, branch: str | None = None, force_with_lease: bool = True) -> dict:
        """
        Force push to remote.

        Args:
            path: Repository path
            branch: Branch to push (default: current)
            force_with_lease: Use --force-with-lease for safety (default: True)

        Returns dict with: success, message
        """
        result = {"success": False, "message": ""}

        if not branch:
            branch = await self._get_branch(path)
            if not branch:
                result["message"] = "Not on a branch"
                return result

        remote, _ = await self._get_remote(path)
        if not remote:
            result["message"] = "No remote configured"
            return result

        force_flag = "--force-with-lease" if force_with_lease else "--force"
        rc, _, stderr = await self._run_git(path, "push", force_flag, remote, branch)
        if rc != 0:
            result["message"] = f"Force push failed: {stderr}"
            return result

        result["success"] = True
        result["message"] = f"Force pushed {branch} to {remote}"
        return result

    # ========== Branch Management ==========

    async def list_branches(self, path: Path, remote: bool = False) -> list[dict[str, str | bool | int | None]]:
        """
        List branches in the repository.

        Returns list of dicts with: name, is_current, upstream, ahead, behind
        """
        if not await self._is_git_repo(path):
            return []

        branches: list[dict[str, str | bool | int | None]] = []

        # Get format for branch listing
        format_str = "%(refname:short)|%(HEAD)|%(upstream:short)|%(upstream:track)"
        args = ["branch", f"--format={format_str}"]
        if remote:
            args.append("-r")
        else:
            args.append("-a")

        rc, stdout, _ = await self._run_git(path, *args)
        if rc != 0 or not stdout:
            return branches

        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                name = parts[0].strip()
                is_current = parts[1].strip() == "*"
                upstream = parts[2].strip() if len(parts) > 2 else None
                track_info = parts[3].strip() if len(parts) > 3 else ""

                # Parse ahead/behind from track info like "[ahead 2, behind 1]"
                ahead = 0
                behind = 0
                if track_info:
                    import re

                    ahead_match = re.search(r"ahead (\d+)", track_info)
                    behind_match = re.search(r"behind (\d+)", track_info)
                    if ahead_match:
                        ahead = int(ahead_match.group(1))
                    if behind_match:
                        behind = int(behind_match.group(1))

                branches.append(
                    {
                        "name": name,
                        "is_current": is_current,
                        "upstream": upstream if upstream else None,
                        "ahead": ahead,
                        "behind": behind,
                    }
                )

        return branches

    async def rename_branch(self, path: Path, old_name: str, new_name: str) -> dict:
        """Rename a branch."""
        result = {"success": False, "message": ""}

        rc, _, stderr = await self._run_git(path, "branch", "-m", old_name, new_name)
        if rc != 0:
            result["message"] = f"Failed to rename branch: {stderr}"
            return result

        result["success"] = True
        result["message"] = f"Renamed {old_name} to {new_name}"
        return result

    async def delete_branch(self, path: Path, branch: str, force: bool = False, remote: bool = False) -> dict:
        """Delete a branch (local or remote)."""
        result = {"success": False, "message": ""}

        if remote:
            remote_name, _ = await self._get_remote(path)
            if not remote_name:
                result["message"] = "No remote configured"
                return result
            rc, _, stderr = await self._run_git(path, "push", remote_name, "--delete", branch)
        else:
            flag = "-D" if force else "-d"
            rc, _, stderr = await self._run_git(path, "branch", flag, branch)

        if rc != 0:
            result["message"] = f"Failed to delete branch: {stderr}"
            return result

        result["success"] = True
        location = "remote" if remote else "local"
        result["message"] = f"Deleted {location} branch {branch}"
        return result

    async def change_base_branch(self, path: Path, feature_branch: str, new_base: str) -> dict:
        """
        Change the base of a feature branch by rebasing onto a new base.

        This is equivalent to: git rebase --onto new_base old_base feature_branch
        """
        result = {"success": False, "message": "", "conflicts": []}

        # Get current branch to restore later
        current = await self._get_branch(path)

        # Checkout feature branch
        rc, _, stderr = await self._run_git(path, "checkout", feature_branch)
        if rc != 0:
            result["message"] = f"Failed to checkout {feature_branch}: {stderr}"
            return result

        # Find merge base (old base)
        rc, old_base, _ = await self._run_git(path, "merge-base", feature_branch, new_base)
        if rc != 0:
            result["message"] = "Could not find common ancestor"
            # Restore original branch
            if current:
                await self._run_git(path, "checkout", current)
            return result

        # Rebase onto new base
        rc, _, stderr = await self._run_git(path, "rebase", "--onto", new_base, old_base.strip(), feature_branch)
        if rc != 0:
            if "conflict" in stderr.lower():
                conflict_state = await self._detect_conflict_state(path)
                result["conflicts"] = conflict_state["conflicted_files"]
                result["message"] = f"Rebase conflict: {len(result['conflicts'])} file(s)"
            else:
                result["message"] = f"Rebase failed: {stderr}"
            return result

        result["success"] = True
        result["message"] = f"Rebased {feature_branch} onto {new_base}"
        return result

    # ========== PR Comment and Check Run Methods ==========

    async def get_pr_comments(self, path: Path, pr_number: int) -> list[dict]:
        """
        Fetch PR comments (both issue comments and review comments).

        Args:
            path: Repository path
            pr_number: PR number to get comments for

        Returns:
            List of dicts with: id, author, body, created_at, path (for review comments)
        """
        comments: list[dict] = []

        try:
            # Get issue-style comments using gh pr view
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "comments",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0:
                import json as json_module

                data = json_module.loads(stdout.decode())
                for c in data.get("comments", []):
                    comments.append(
                        {
                            "id": c.get("id"),
                            "author": c.get("author", {}).get("login", "unknown"),
                            "body": c.get("body", ""),
                            "created_at": c.get("createdAt"),
                            "path": None,  # Issue comments don't have file paths
                            "type": "issue",
                        }
                    )

            # Get review comments (inline code comments) using gh api
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
                "--jq",
                ".[] | {id: .id, author: .user.login, body: .body, created_at: .created_at, path: .path, line: .line}",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout.strip():
                import json as json_module

                for line in stdout.decode().strip().split("\n"):
                    if line.strip():
                        try:
                            c = json_module.loads(line)
                            comments.append(
                                {
                                    "id": c.get("id"),
                                    "author": c.get("author", "unknown"),
                                    "body": c.get("body", ""),
                                    "created_at": c.get("created_at"),
                                    "path": c.get("path"),
                                    "line": c.get("line"),
                                    "type": "review",
                                }
                            )
                        except json_module.JSONDecodeError:
                            pass

        except (FileNotFoundError, Exception) as e:
            logger.warning(f"Error fetching PR comments: {e}")

        # Sort by ID (ascending) for consistent processing order
        comments.sort(key=lambda x: x.get("id") or 0)
        return comments

    async def get_check_runs(self, path: Path, commit_sha: str) -> list[dict]:
        """
        Fetch check runs for a commit.

        Args:
            path: Repository path
            commit_sha: Commit SHA to get check runs for

        Returns:
            List of dicts with: id, name, status, conclusion, details_url, output_title, output_summary
        """
        check_runs: list[dict] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/commits/{commit_sha}/check-runs",
                "--jq",
                ".check_runs[] | {id: .id, name: .name, status: .status, conclusion: .conclusion, "
                "details_url: .details_url, output_title: .output.title, output_summary: .output.summary, "
                "output_text: .output.text}",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout.strip():
                import json as json_module

                for line in stdout.decode().strip().split("\n"):
                    if line.strip():
                        try:
                            check = json_module.loads(line)
                            check_runs.append(check)
                        except json_module.JSONDecodeError:
                            pass

        except (FileNotFoundError, Exception) as e:
            logger.warning(f"Error fetching check runs: {e}")

        return check_runs

    async def get_failed_checks(
        self,
        path: Path,
        commit_sha: str,
        filter_names: list[str] | None = None,
    ) -> list[dict]:
        """
        Get only failed check runs, optionally filtering by name pattern.

        Args:
            path: Repository path
            commit_sha: Commit SHA to check
            filter_names: List of name patterns to filter (case-insensitive).
                          Default: ["vercel", "build", "deploy", "lint", "test"]

        Returns:
            List of failed check run dicts
        """
        if filter_names is None:
            filter_names = ["vercel", "build", "deploy", "lint", "test"]

        all_checks = await self.get_check_runs(path, commit_sha)
        failed_checks: list[dict] = []

        for check in all_checks:
            # Only consider completed checks
            if check.get("status") != "completed":
                continue

            # Only consider failures (not neutral, cancelled, skipped, etc.)
            if check.get("conclusion") not in ("failure", "timed_out"):
                continue

            # Apply name filter if provided
            name = check.get("name", "").lower()
            if filter_names:
                if not any(pattern.lower() in name for pattern in filter_names):
                    continue

            failed_checks.append(check)

        return failed_checks

    async def post_pr_comment(self, path: Path, pr_number: int, body: str) -> bool:
        """
        Post a comment on a PR.

        Args:
            path: Repository path
            pr_number: PR number to comment on
            body: Comment body text

        Returns:
            True if comment was posted successfully
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "pr",
                "comment",
                str(pr_number),
                "--body",
                body,
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.warning(f"Failed to post PR comment: {stderr.decode()}")
                return False

            logger.info(f"Posted comment on PR #{pr_number}")
            return True

        except (FileNotFoundError, Exception) as e:
            logger.warning(f"Error posting PR comment: {e}")
            return False
