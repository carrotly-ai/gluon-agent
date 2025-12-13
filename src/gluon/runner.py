"""Background task runner for Gluon Agent.

Manages subprocess execution, log capture, and process lifecycle for
long-running Claude Code tasks.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gluon.agent import AgentMessage, AgentResult, GluonAgent
from gluon.git_manager import GitManager
from gluon.image_storage import ImageStorageService
from gluon.models import ExecutionRun, RunStatus
from gluon.store import DEFAULT_LOG_PATH, GluonStore
from gluon.worktree import WorktreeError, WorktreeManager, is_git_repository


@dataclass
class RunnerConfig:
    """Configuration for the task runner."""

    max_concurrent: int = 16  # Max parallel runs
    log_path: Path = DEFAULT_LOG_PATH


class TaskRunner:
    """
    Manages background execution of Claude Code tasks.

    Handles subprocess spawning, log capture, status tracking,
    and process lifecycle management.
    """

    def __init__(
        self,
        store: GluonStore | None = None,
        agent: GluonAgent | None = None,
        config: RunnerConfig | None = None,
    ):
        self.store = store or GluonStore()
        self.agent = agent or GluonAgent()
        self.config = config or RunnerConfig()
        self.git_manager = GitManager(self.store)
        self.image_service = ImageStorageService(self.store)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._active_tasks: dict[str, asyncio.Task] = {}

        # Ensure log directory exists
        self.config.log_path.mkdir(parents=True, exist_ok=True)

    def _get_log_dir(self, run_id: str) -> Path:
        """Get log directory for a run."""
        log_dir = self.config.log_path / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    async def submit(
        self,
        project_id: str,
        prompt: str,
        wait: bool = False,
        initiator: str | None = None,
        claude_session_id: str | None = None,
        use_worktree: bool = False,
        model: str | None = None,
    ) -> ExecutionRun:
        """
        Submit a task for execution.

        Args:
            project_id: Project to run task on
            prompt: Task prompt
            wait: If True, wait for completion. If False, return immediately.
            initiator: Who started the run (e.g., "cli", "telegram:12345")
            claude_session_id: Optional Claude SDK session ID to resume from
            use_worktree: Execute in isolated Git worktree (default: False)
            model: Model to use (e.g., "haiku", "claude-haiku-4.5", or full Bedrock ID)

        Returns:
            ExecutionRun with current status
        """
        # Create run record
        run = self.store.create_run(project_id, prompt, initiator=initiator, use_worktree=use_worktree, model=model)
        run.claude_session_id = claude_session_id  # Set for resume

        if wait:
            # Execute synchronously
            await self._execute_run(run)
            # Refresh from DB
            return self.store.get_run(run.id) or run
        else:
            # Execute in background subprocess
            self._spawn_background_process(run)
            return run

    async def resume_in_place(
        self,
        run_id: str,
        new_prompt: str,
        wait: bool = False,
        initiator: str | None = None,
    ) -> ExecutionRun:
        """
        Resume an existing run in-place (same run ID, same worktree).

        This continues the Claude session with a new prompt while preserving:
        - The same run ID
        - The same worktree and branch (if use_worktree=True)
        - The same log directory (logs are appended)
        - Accumulated cost tracking

        Args:
            run_id: ID of the run to resume
            new_prompt: New prompt to continue with
            wait: If True, wait for completion. If False, return immediately.
            initiator: Who started the resume (e.g., "web:resume")

        Returns:
            The same ExecutionRun with updated status

        Raises:
            ValueError: If run not found or cannot be resumed
        """
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        if not run.is_resumable:
            raise ValueError(
                f"Run {run_id[:8]} cannot be resumed (status={run.status.value}, "
                f"has_session={run.claude_session_id is not None})"
            )

        # Validate worktree still exists if this was a worktree run
        if run.use_worktree and run.worktree_path:
            wt_path = Path(run.worktree_path)
            if not wt_path.exists():
                raise ValueError(
                    f"Worktree for run {run_id[:8]} no longer exists at {wt_path}. "
                    "Cannot resume - the branch may have been merged or deleted."
                )

        # Prepare run for resume (resets status, increments resume_count)
        run.prepare_for_resume(new_prompt)
        if initiator:
            run.initiator = initiator

        # Write resume marker to log files
        self._write_resume_marker(run)

        # Update in database
        self.store.update_run(run)

        if wait:
            # Execute synchronously
            await self._execute_run(run)
            # Refresh from DB
            return self.store.get_run(run.id) or run
        else:
            # Execute in background subprocess
            self._spawn_background_process(run)
            return run

    def _write_resume_marker(self, run: ExecutionRun) -> None:
        """Write a resume marker to log files for visual separation."""
        if not run.log_path:
            return

        log_dir = Path(run.log_path)
        if not log_dir.exists():
            return

        marker = (
            f"\n\n{'='*60}\n"
            f"RESUMED - Attempt #{run.resume_count}\n"
            f"Prompt: {run.prompt[:100]}{'...' if len(run.prompt) > 100 else ''}\n"
            f"Time: {run.last_resumed_at.isoformat() if run.last_resumed_at else 'N/A'}\n"
            f"{'='*60}\n\n"
        )

        # Append to stdout.log
        stdout_path = log_dir / "stdout.log"
        if stdout_path.exists():
            with open(stdout_path, "a") as f:
                f.write(marker)

        # Append resume marker to messages.jsonl
        messages_path = log_dir / "messages.jsonl"
        if messages_path.exists():
            import json
            resume_msg = {
                "type": "system",
                "subtype": "resume",
                "content": f"=== Resume #{run.resume_count} ===",
                "resume_attempt": run.resume_count,
                "prompt": run.prompt,
                "timestamp": run.last_resumed_at.isoformat() if run.last_resumed_at else None,
            }
            with open(messages_path, "a") as f:
                f.write(json.dumps(resume_msg) + "\n")

    def _spawn_background_process(self, run: ExecutionRun) -> None:
        """Spawn a detached subprocess to execute the run."""
        # Use the same Python interpreter to run the worker
        cmd = [
            sys.executable,
            "-m",
            "gluon.runner",
            "--run-id",
            run.id,
        ]

        # Spawn detached process
        # On Unix, use start_new_session to detach from terminal
        # Redirect stdout/stderr to /dev/null since we capture logs ourselves
        with open(os.devnull, "w") as devnull:
            proc = subprocess.Popen(
                cmd,
                stdout=devnull,
                stderr=devnull,
                stdin=devnull,
                start_new_session=True,
            )
            # Store PID in run record
            run.mark_running(pid=proc.pid, log_path=self._get_log_dir(run.id))
            self.store.update_run(run)

    async def _execute_run(self, run: ExecutionRun) -> None:
        """Execute a run with semaphore control."""
        async with self._semaphore:
            await self._run_task(run)

    async def _run_task(self, run: ExecutionRun) -> None:
        """Execute the actual task."""
        # Get project
        project = self.store.get_project(run.project_id)
        if not project:
            run.mark_failed(f"Project not found: {run.project_id}")
            self.store.update_run(run)
            return

        # Determine working directory (main project or worktree)
        working_dir = project.expanded_path
        worktree_manager: WorktreeManager | None = None
        is_resumed = run.resume_count > 0

        # Create worktree if requested and project is a git repo
        # For resumed runs, reuse existing worktree if it exists
        if run.use_worktree:
            if is_resumed and run.worktree_path and Path(run.worktree_path).exists():
                # Reuse existing worktree for resumed run
                working_dir = Path(run.worktree_path)
                worktree_manager = WorktreeManager(project.expanded_path)
                worktree_manager.worktree_path = working_dir
                worktree_manager.branch_name = run.branch_name
            elif await is_git_repository(project.expanded_path):
                worktree_run_id = run.id[:8]
                worktree_manager = WorktreeManager(project.expanded_path)
                try:
                    working_dir = await worktree_manager.create(worktree_run_id)
                    run.worktree_path = str(working_dir)
                    # Get the branch name from the worktree (format: gluon-{run_id})
                    run.branch_name = f"gluon-{worktree_run_id}"
                    self.store.update_run(run)
                except WorktreeError:
                    # Log warning but continue with main directory
                    run.use_worktree = False
                    worktree_manager = None

        # Setup logging
        log_dir = self._get_log_dir(run.id)
        stdout_path = log_dir / "stdout.log"
        stderr_path = log_dir / "stderr.log"
        messages_path = log_dir / "messages.jsonl"

        # Update run status
        run.mark_running(pid=os.getpid(), log_path=log_dir)
        self.store.update_run(run)

        # Get image paths for multimodal prompt (base64 encoded, not file copies)
        image_paths: list[Path] = []
        try:
            images = self.image_service.list_images_for_run(run.id)
            image_paths = [img.full_path for img in images if img.full_path.exists()]
        except Exception:
            pass  # Continue without images if retrieval fails

        try:
            # Open log files (append mode for resumed runs, write for new runs)
            log_mode = "a" if is_resumed else "w"
            with (
                open(stdout_path, log_mode) as stdout_file,
                open(stderr_path, log_mode) as stderr_file,
                open(messages_path, log_mode) as messages_file,
            ):
                # Log any attached images
                if image_paths:
                    stdout_file.write(f"Attached {len(image_paths)} image(s) to prompt:\n")
                    for img_path in image_paths:
                        stdout_file.write(f"  - {img_path.name}\n")
                    stdout_file.write("\n")
                    stdout_file.flush()

                # Build prompt with worktree context if applicable
                effective_prompt = run.prompt
                if run.use_worktree and run.branch_name:
                    worktree_context = f"""[WORKTREE CONTEXT]
You are working in an isolated git worktree on branch '{run.branch_name}'.
This worktree is temporary and may be cleaned up after your session.

IMPORTANT: Commit all your changes before completing your task.
Use descriptive commit messages summarizing what was done.
The system will auto-commit any uncommitted changes as a safety net,
but explicit commits with good messages are preferred.
[END WORKTREE CONTEXT]

"""
                    effective_prompt = worktree_context + run.prompt
                    stdout_file.write(f"Working in worktree on branch: {run.branch_name}\n\n")
                    stdout_file.flush()

                # Create agent with the model specified for this run
                agent = GluonAgent(model=run.model) if run.model else self.agent

                # Execute via agent with images as base64 content blocks
                async for item in agent.execute(
                    working_dir=working_dir,
                    prompt=effective_prompt,
                    resume_session_id=run.claude_session_id,
                    images=image_paths if image_paths else None,
                ):
                    if isinstance(item, AgentMessage):
                        # Log message
                        msg_dict = {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "type": item.type,
                            "content": item.content,
                            "metadata": item.metadata,
                        }
                        messages_file.write(json.dumps(msg_dict) + "\n")
                        messages_file.flush()

                        # Also write text to stdout
                        if item.type == "text":
                            stdout_file.write(item.content + "\n")
                            stdout_file.flush()
                        elif item.type == "error":
                            stderr_file.write(item.content + "\n")
                            stderr_file.flush()

                    elif isinstance(item, AgentResult):
                        # AgentResult summary - update run record (don't write to messages.jsonl
                        # since AgentMessage type="result" already logged the completion)
                        # Update run with Claude session ID for future resume
                        run.claude_session_id = item.claude_session_id
                        # Store cost tracking data (accumulate for resumed runs)
                        if is_resumed and run.cost_usd is not None:
                            run.cost_usd = (run.cost_usd or 0) + (item.total_cost_usd or 0)
                            run.input_tokens = (run.input_tokens or 0) + (item.input_tokens or 0)
                            run.output_tokens = (run.output_tokens or 0) + (item.output_tokens or 0)
                        else:
                            run.cost_usd = item.total_cost_usd
                            run.input_tokens = item.input_tokens
                            run.output_tokens = item.output_tokens
                        run.model_used = item.model_used

                        # Determine working path (worktree or project)
                        working_path = (
                            Path(run.worktree_path) if run.worktree_path else project.expanded_path
                        )

                        # Capture git info (branch, commit, PR) after task completion
                        try:
                            git_info = await self.git_manager.capture_run_git_info(working_path)
                            run.branch_name = git_info.get("branch_name")
                            run.git_commit_sha = git_info.get("git_commit_sha")
                            run.pr_number = git_info.get("pr_number")
                            run.pr_url = git_info.get("pr_url")
                            run.pr_status = git_info.get("pr_status")
                        except Exception as git_err:
                            # Don't fail the run if git capture fails
                            stderr_file.write(f"Warning: Failed to capture git info: {git_err}\n")

                        # For worktree runs: auto-commit any uncommitted changes, then push and create PR
                        auto_create_pr = self.store.get_setting("auto_create_pr", "true") == "true"
                        if run.use_worktree and run.branch_name and item.success:
                            # Safety net: auto-commit any uncommitted changes
                            try:
                                prompt_preview = run.prompt[:60]
                                ellipsis = "..." if len(run.prompt) > 60 else ""
                                commit_msg = (
                                    f"chore: {prompt_preview}{ellipsis}\n\n"
                                    f"Auto-committed by Gluon Agent\nRun ID: {run.id}"
                                )
                                commit_result = await self.git_manager.auto_commit_changes(
                                    path=working_path,
                                    message=commit_msg,
                                    run_id=run.id,
                                )
                                if commit_result.get("committed"):
                                    stdout_file.write(f"\n✓ Auto-committed {commit_result['files_count']} file(s)\n")
                                    stdout_file.flush()
                            except Exception as commit_err:
                                stderr_file.write(f"Warning: Auto-commit failed: {commit_err}\n")
                                stderr_file.flush()

                            # Push branch and create PR if enabled
                            if auto_create_pr:
                                try:
                                    pr_result = await self.git_manager.push_branch_and_create_pr(
                                        project_path=working_path,
                                        branch_name=run.branch_name,
                                        prompt=run.prompt,
                                        run_id=run.id,
                                    )
                                    if pr_result.get("pushed"):
                                        stdout_file.write(f"\n✓ Pushed branch {run.branch_name} to remote\n")
                                    if pr_result.get("pr_url"):
                                        run.pr_number = pr_result.get("pr_number")
                                        run.pr_url = pr_result.get("pr_url")
                                        run.pr_status = pr_result.get("pr_status")
                                        stdout_file.write(f"✓ Created PR: {run.pr_url}\n")
                                    elif pr_result.get("error"):
                                        stderr_file.write(f"Warning: PR creation: {pr_result['error']}\n")
                                    stdout_file.flush()
                                except Exception as pr_err:
                                    stderr_file.write(f"Warning: Failed to push/create PR: {pr_err}\n")
                                    stderr_file.flush()

                        if item.success:
                            run.mark_completed(exit_code=0)
                        else:
                            run.mark_failed(item.error or "Unknown error", exit_code=1)

        except asyncio.CancelledError:
            run.mark_cancelled()
            raise
        except Exception as e:
            run.mark_failed(str(e), exit_code=1)
        finally:
            self.store.update_run(run)
            # Clean up active task tracking
            if run.id in self._active_tasks:
                del self._active_tasks[run.id]

    async def cancel(self, run_id: str) -> bool:
        """
        Cancel a running task.

        Args:
            run_id: Run ID to cancel

        Returns:
            True if cancelled, False if not found or not running
        """
        run = self.store.get_run(run_id)
        if not run:
            return False

        if not run.is_active:
            return False

        # Cancel asyncio task if we have it
        if run_id in self._active_tasks:
            self._active_tasks[run_id].cancel()
            try:
                await self._active_tasks[run_id]
            except asyncio.CancelledError:
                pass
            return True

        # Try to kill by PID if running in another process
        if run.pid and run.status == RunStatus.RUNNING:
            try:
                os.kill(run.pid, signal.SIGTERM)
                run.mark_cancelled()
                self.store.update_run(run)
                return True
            except (ProcessLookupError, PermissionError):
                # Process already gone or can't kill
                pass

        return False

    def get_logs(self, run_id: str, tail: int | None = None) -> dict[str, str]:
        """
        Get logs for a run.

        Args:
            run_id: Run ID
            tail: If set, only return last N lines

        Returns:
            Dict with stdout, stderr, and messages content
        """
        run = self.store.get_run(run_id)
        if not run:
            return {"stdout": "", "stderr": "", "messages": ""}

        # Construct log path from run ID to handle Docker path translation
        # (stored absolute paths like /Users/x/.gluon don't work in container)
        log_dir = Path.home() / ".gluon" / "logs" / run.id
        result = {}

        for name in ["stdout", "stderr", "messages"]:
            ext = "jsonl" if name == "messages" else "log"
            path = log_dir / f"{name}.{ext}"
            if path.exists():
                content = path.read_text()
                if tail:
                    lines = content.splitlines()
                    content = "\n".join(lines[-tail:])
                result[name] = content
            else:
                result[name] = ""

        return result

    async def tail_logs(
        self,
        run_id: str,
        stream: str = "stdout",
    ) -> AsyncIterator[str]:
        """
        Tail logs for a running task.

        Args:
            run_id: Run ID
            stream: Which stream to tail (stdout, stderr, messages)

        Yields:
            New log lines as they appear
        """
        run = self.store.get_run(run_id)
        if not run or not run.log_path:
            return

        ext = "jsonl" if stream == "messages" else "log"
        log_path = run.log_path / f"{stream}.{ext}"

        # Wait for file to exist
        while not log_path.exists():
            if not run.is_active:
                return
            await asyncio.sleep(0.1)
            run = self.store.get_run(run_id) or run

        # Tail the file
        with open(log_path) as f:
            while True:
                line = f.readline()
                if line:
                    yield line.rstrip("\n")
                else:
                    # Check if run is still active
                    run = self.store.get_run(run_id) or run
                    if not run.is_active:
                        # Read any remaining content
                        remaining = f.read()
                        if remaining:
                            for line in remaining.splitlines():
                                yield line
                        break
                    await asyncio.sleep(0.1)

    def refresh_run_status(self, run: ExecutionRun) -> ExecutionRun:
        """
        Refresh run status by checking if process is still alive.

        Args:
            run: Run to check

        Returns:
            Updated run
        """
        if run.status != RunStatus.RUNNING or not run.pid:
            return run

        try:
            # Check if process exists (signal 0 doesn't kill)
            os.kill(run.pid, 0)
        except ProcessLookupError:
            # Process is gone - mark as completed (assume success if no error)
            run.mark_completed(exit_code=0)
            self.store.update_run(run)
        except PermissionError:
            # Can't check - leave as is
            pass

        return run

    def refresh_all_runs(self) -> list[ExecutionRun]:
        """
        Refresh status of all active runs.

        Returns:
            List of all active runs after refresh
        """
        active_runs = self.store.list_active_runs()
        for run in active_runs:
            self.refresh_run_status(run)
        return self.store.list_active_runs()


# Utility functions for CLI


def format_duration(seconds: float | None) -> str:
    """Format duration in human-readable form."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def format_run_status(status: RunStatus) -> tuple[str, str]:
    """Return (emoji, color) for run status."""
    return {
        RunStatus.PENDING: ("⏳", "yellow"),
        RunStatus.RUNNING: ("🔄", "blue"),
        RunStatus.COMPLETED: ("✅", "green"),
        RunStatus.FAILED: ("❌", "red"),
        RunStatus.CANCELLED: ("🚫", "dim"),
    }.get(status, ("❓", "white"))


def _run_worker(run_id: str) -> None:
    """Worker entry point for subprocess execution."""
    import anyio

    store = GluonStore()
    runner = TaskRunner(store=store)

    run = store.get_run(run_id)
    if not run:
        print(f"Run not found: {run_id}", file=sys.stderr)
        sys.exit(1)

    async def _execute():
        await runner._execute_run(run)

    anyio.run(_execute)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gluon background task worker")
    parser.add_argument("--run-id", required=True, help="Run ID to execute")
    args = parser.parse_args()

    _run_worker(args.run_id)
