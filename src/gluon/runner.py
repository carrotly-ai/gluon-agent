"""Background task runner for Gluon Agent.

Manages subprocess execution, log capture, and process lifecycle for
long-running Claude Code tasks.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from gluon.agent import AgentMessage, AgentResult, GluonAgent
from gluon.git_manager import GitManager
from gluon.image_storage import ImageStorageService
from gluon.models import ExecutionRun, RunStatus, SupervisionConfig
from gluon.ralph_manager import RalphManager
from gluon.resume_coordinator import ResumeCoordinator
from gluon.store import DEFAULT_LOG_PATH, GluonStore
from gluon.worktree import (
    WorktreeConfig,
    WorktreeError,
    WorktreeManager,
    branch_exists,
    is_git_repository,
    recreate_worktree,
)

logger = logging.getLogger(__name__)


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

        # Supervision coordinator (lazy initialized)
        self._supervisor: "ResumeCoordinator | None" = None

        # Ensure log directory exists
        self.config.log_path.mkdir(parents=True, exist_ok=True)

    def _get_log_dir(self, run_id: str) -> Path:
        """Get log directory for a run."""
        log_dir = self.config.log_path / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def get_log_path(self, run_id: str) -> Path | None:
        """Get log directory path for a run, if it exists.

        Used by external code (e.g., WebSocket log polling) to find log files.
        Returns None if the log directory doesn't exist.
        """
        log_dir = self.config.log_path / run_id
        return log_dir if log_dir.exists() else None

    async def submit(
        self,
        project_id: str,
        prompt: str,
        wait: bool = False,
        initiator: str | None = None,
        claude_session_id: str | None = None,
        use_worktree: bool = False,
        model: str | None = None,
        ralph_enabled: bool = False,
        max_loops: int = 50,
        max_calls_per_hour: int = 100,
        max_cost_usd: float | None = None,
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
            ralph_enabled: Enable ralph loop mode for autonomous execution
            max_loops: Maximum loop iterations (ralph mode only)
            max_calls_per_hour: Maximum API calls per hour (ralph mode only)
            max_cost_usd: Optional cost cap in USD (ralph mode only)

        Returns:
            ExecutionRun with current status
        """
        # Create run record
        run = self.store.create_run(
            project_id,
            prompt,
            initiator=initiator,
            use_worktree=use_worktree,
            model=model,
            ralph_enabled=ralph_enabled,
            max_loops=max_loops,
            max_calls_per_hour=max_calls_per_hour,
            max_cost_usd=max_cost_usd,
        )
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
                # Worktree path is missing - try to recreate from branch if it exists
                if run.branch_name:
                    project = self.store.get_project(run.project_id)
                    if project:
                        repo_path = project.expanded_path
                        # Check if branch still exists
                        if await branch_exists(repo_path, run.branch_name):
                            # Branch exists - recreate the worktree
                            try:
                                await recreate_worktree(
                                    repo_path=repo_path,
                                    worktree_path=wt_path,
                                    branch_name=run.branch_name,
                                    copy_patterns=WorktreeConfig().copy_patterns,
                                )
                                # Worktree successfully recreated, continue with resume
                            except WorktreeError as e:
                                raise ValueError(f"Failed to recreate worktree for run {run_id[:8]}: {e}") from e
                        else:
                            raise ValueError(
                                f"Worktree for run {run_id[:8]} no longer exists at {wt_path}. "
                                f"Branch '{run.branch_name}' has been deleted or merged."
                            )
                    else:
                        raise ValueError(
                            f"Worktree for run {run_id[:8]} no longer exists at {wt_path}. "
                            f"Project {run.project_id} not found."
                        )
                else:
                    raise ValueError(
                        f"Worktree for run {run_id[:8]} no longer exists at {wt_path}. "
                        "Cannot resume - no branch name recorded."
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
            f"\n\n{'=' * 60}\n"
            f"RESUMED - Attempt #{run.resume_count}\n"
            f"Prompt: {run.prompt[:100]}{'...' if len(run.prompt) > 100 else ''}\n"
            f"Time: {run.last_resumed_at.isoformat() if run.last_resumed_at else 'N/A'}\n"
            f"{'=' * 60}\n\n"
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
        progress_path = log_dir / "progress.json"
        tokens_path = log_dir / "tokens.json"

        # Progress tracking for WebSocket streaming
        turn_count = 0
        tool_count = 0
        start_time = time.time()

        # Update run status
        run.mark_running(pid=os.getpid(), log_path=log_dir)
        self.store.update_run(run)

        # Ralph mode: use RalphManager for autonomous loop execution
        if run.ralph_enabled:
            await self._run_ralph_loop(run, working_dir, worktree_manager)
            return

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
                            turn_count += 1
                        elif item.type == "tool_use":
                            tool_count += 1
                        elif item.type == "error":
                            stderr_file.write(item.content + "\n")
                            stderr_file.flush()

                            # Check for context overflow - trigger auto-recovery
                            metadata = item.metadata or {}
                            if metadata.get("exception") == "ContextOverflowError":
                                stdout_file.write("\n⚠️ Context overflow detected - initiating auto-recovery...\n")
                                stdout_file.flush()

                                # Extract recovery state and attempt recovery
                                recovery_result = await self._handle_context_overflow_recovery(
                                    run=run,
                                    working_dir=working_dir,
                                    stdout_file=stdout_file,
                                    stderr_file=stderr_file,
                                    messages_file=messages_file,
                                    progress_path=progress_path,
                                    tokens_path=tokens_path,
                                    start_time=start_time,
                                )

                                if recovery_result:
                                    # Recovery succeeded - mark as review (not failed)
                                    run.mark_review()
                                    self.store.update_run(run)
                                    return  # Exit without marking as failed

                        # Update progress.json for WebSocket streaming
                        progress_data = {
                            "turns": turn_count,
                            "tool_calls": tool_count,
                            "elapsed_seconds": round(time.time() - start_time, 1),
                        }
                        progress_path.write_text(json.dumps(progress_data))

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

                        # Update tokens.json for WebSocket streaming
                        tokens_data = {
                            "input_tokens": run.input_tokens or 0,
                            "output_tokens": run.output_tokens or 0,
                            "estimated_cost_usd": run.cost_usd or 0,
                        }
                        tokens_path.write_text(json.dumps(tokens_data))

                        # Final progress update
                        turn_count = item.total_turns or turn_count
                        progress_data = {
                            "turns": turn_count,
                            "tool_calls": tool_count,
                            "elapsed_seconds": round(time.time() - start_time, 1),
                        }
                        progress_path.write_text(json.dumps(progress_data))

                        # Determine working path (worktree or project)
                        working_path = Path(run.worktree_path) if run.worktree_path else project.expanded_path

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
                            run.mark_review()  # All tasks go to REVIEW first
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

    # ========== Supervision Control ==========

    async def start_supervisor(self, poll_interval: int = 30) -> None:
        """Start the background supervision coordinator.

        Args:
            poll_interval: Seconds between polling cycles
        """
        if self._supervisor and self._supervisor.is_running:
            logger.warning("Supervisor already running")
            return

        self._supervisor = ResumeCoordinator(
            store=self.store,
            runner=self,
            poll_interval=poll_interval,
        )
        await self._supervisor.start()
        logger.info(f"Started supervision with {poll_interval}s poll interval")

    async def stop_supervisor(self) -> None:
        """Stop the background supervision coordinator."""
        if self._supervisor:
            await self._supervisor.stop()
            self._supervisor = None
            logger.info("Stopped supervision")

    @property
    def supervisor(self) -> ResumeCoordinator | None:
        """Get the supervision coordinator instance."""
        return self._supervisor

    @property
    def supervisor_running(self) -> bool:
        """Check if supervisor is running."""
        return self._supervisor is not None and self._supervisor.is_running

    async def evaluate_supervision(self, run_id: str) -> dict | None:
        """Manually trigger supervision evaluation for a run.

        Args:
            run_id: ID of run to evaluate

        Returns:
            Supervision status dict, or None if run not found
        """
        run = self.store.get_run(run_id)
        if not run:
            return None

        if not self._supervisor:
            # Create temporary coordinator for one-off evaluation
            coordinator = ResumeCoordinator(store=self.store, runner=self)
            decision = await coordinator.evaluate_run(run, trigger="manual")
            return {
                "run_id": run_id,
                "decision": "resume" if decision.should_resume else "skip",
                "reason": decision.reason,
                "wait_seconds": decision.wait_seconds,
            }
        else:
            decision = await self._supervisor.evaluate_run(run, trigger="manual")
            return {
                "run_id": run_id,
                "decision": "resume" if decision.should_resume else "skip",
                "reason": decision.reason,
                "wait_seconds": decision.wait_seconds,
            }

    def _extract_recovery_state(self, run: ExecutionRun) -> dict:
        """
        Extract recoverable state from a failed run.

        Parses the run's messages.jsonl log to find:
        - Completed TODO items
        - Last successful operations
        - Any progress markers

        Args:
            run: The failed run to extract state from

        Returns:
            Dict with recovery information including:
            - run_id: Original run ID
            - project_id: Project ID
            - original_prompt: Original prompt
            - branch_name: Git branch (if using worktree)
            - worktree_path: Worktree path (if using worktree)
            - source_branch: Source branch (if using worktree)
            - completed_work: List of completed task descriptions
            - last_tool_used: Name of last tool that was called
            - total_cost_usd: Cost accumulated so far
        """
        recovery = {
            "run_id": run.id,
            "project_id": run.project_id,
            "original_prompt": run.prompt,
            "branch_name": run.branch_name,
            "worktree_path": run.worktree_path,
            "source_branch": run.source_branch,
            "completed_work": [],
            "last_tool_used": None,
            "total_cost_usd": run.cost_usd or 0,
        }

        # Parse messages.jsonl for progress
        if run.log_path:
            messages_file = Path(run.log_path) / "messages.jsonl"
            if messages_file.exists():
                recovery["completed_work"] = self._parse_completed_tasks(messages_file)
                recovery["last_tool_used"] = self._get_last_tool_used(messages_file)

        return recovery

    def _parse_completed_tasks(self, messages_path: Path) -> list[str]:
        """
        Parse messages.jsonl to extract completed TODO items.

        Args:
            messages_path: Path to messages.jsonl file

        Returns:
            List of completed task descriptions
        """
        completed = []

        try:
            with open(messages_path) as f:
                for line in f:
                    try:
                        msg = json.loads(line)
                        # Look for TodoWrite tool results with completed tasks
                        if msg.get("type") == "tool_use":
                            metadata = msg.get("metadata", {})
                            tool = metadata.get("tool", "")
                            if tool == "TodoWrite":
                                input_data = metadata.get("input", {})
                                todos = input_data.get("todos", [])
                                for todo in todos:
                                    if todo.get("status") == "completed":
                                        completed.append(todo.get("content", ""))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass  # Return empty list if parsing fails

        return completed

    def _get_last_tool_used(self, messages_path: Path) -> str | None:
        """
        Get the name of the last tool that was called.

        Args:
            messages_path: Path to messages.jsonl file

        Returns:
            Tool name or None
        """
        last_tool = None

        try:
            with open(messages_path) as f:
                for line in f:
                    try:
                        msg = json.loads(line)
                        if msg.get("type") == "tool_use":
                            metadata = msg.get("metadata", {})
                            last_tool = metadata.get("tool")
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        return last_tool

    async def _run_ralph_loop(
        self,
        run: ExecutionRun,
        working_dir: Path,
        worktree_manager: WorktreeManager | None,
    ) -> None:
        """Execute a run in ralph loop mode.

        Uses RalphManager to orchestrate autonomous loop execution
        with circuit breaker, completion detection, and rate limiting.
        """
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"Starting ralph loop for run {run.id[:8]}")

        # Setup logging
        log_dir = self._get_log_dir(run.id)
        stdout_path = log_dir / "stdout.log"

        try:
            # Write ralph mode header to log
            with open(stdout_path, "a") as f:
                f.write(f"\n{'=' * 60}\n")
                f.write("RALPH MODE ENABLED - Autonomous Loop Execution\n")
                f.write(f"Max iterations: {run.max_loops}\n")
                f.write(f"Max calls/hour: {run.max_calls_per_hour}\n")
                if run.max_cost_usd:
                    f.write(f"Cost cap: ${run.max_cost_usd:.2f}\n")
                f.write(f"{'=' * 60}\n\n")

            # Create agent with model
            agent = GluonAgent(model=run.model) if run.model else self.agent

            # Create and execute ralph manager
            manager = RalphManager(
                run=run,
                agent=agent,
                store=self.store,
                working_dir=working_dir,
            )

            updated_run = await manager.execute_loop()

            # Log completion
            with open(stdout_path, "a") as f:
                f.write(f"\n{'=' * 60}\n")
                f.write("RALPH LOOP COMPLETED\n")
                f.write(f"Status: {updated_run.status.value}\n")
                f.write(f"Iterations: {updated_run.loop_count}/{updated_run.max_loops}\n")
                if updated_run.completion_reason:
                    f.write(f"Reason: {updated_run.completion_reason}\n")
                if updated_run.cost_usd:
                    f.write(f"Total cost: ${updated_run.cost_usd:.4f}\n")
                f.write(f"{'=' * 60}\n")

            # Auto-commit and PR creation handled in RalphManager

        except Exception as e:
            logger.error(f"Ralph loop failed: {e}")
            run.mark_failed(str(e))
            self.store.update_run(run)

            with open(stdout_path, "a") as f:
                f.write(f"\n\nRALPH LOOP ERROR: {e}\n")

    async def _handle_context_overflow_recovery(
        self,
        run: ExecutionRun,
        working_dir: Path,
        stdout_file,
        stderr_file,
        messages_file,
        progress_path: Path,
        tokens_path: Path,
        start_time: float,
    ) -> bool:
        """
        Handle context overflow by initiating auto-recovery.

        Args:
            run: The execution run that hit context overflow
            working_dir: Working directory for the run
            stdout_file: Open file handle for stdout logging
            stderr_file: Open file handle for stderr logging
            messages_file: Open file handle for messages.jsonl
            progress_path: Path to progress.json
            tokens_path: Path to tokens.json
            start_time: Start time for elapsed calculation

        Returns:
            True if recovery succeeded, False otherwise
        """
        from gluon.models import utc_now

        try:
            # Update recovery tracking on the run
            run.recovery_count += 1
            run.last_recovery_at = utc_now()
            self.store.update_run(run)

            # Extract recovery state
            recovery_state = self._extract_recovery_state(run)

            # Write recovery marker to logs
            recovery_marker = {
                "timestamp": datetime.now(UTC).isoformat(),
                "type": "system",
                "subtype": "recovery",
                "content": f"=== Context Overflow Recovery #{run.recovery_count} ===",
                "recovery_attempt": run.recovery_count,
                "completed_work": recovery_state.get("completed_work", []),
            }
            messages_file.write(json.dumps(recovery_marker) + "\n")
            messages_file.flush()

            stdout_file.write(f"\n{'=' * 50}\n")
            stdout_file.write(f"CONTEXT OVERFLOW RECOVERY - Attempt #{run.recovery_count}\n")
            stdout_file.write(f"Completed tasks found: {len(recovery_state.get('completed_work', []))}\n")
            stdout_file.write(f"{'=' * 50}\n\n")
            stdout_file.flush()

            # Create agent for recovery (use same model as original run)
            recovery_agent = GluonAgent(model=run.model) if run.model else self.agent

            # Execute recovery with fresh context
            turn_count = 0
            tool_count = 0

            async for item in recovery_agent.resume_with_fresh_context(
                recovery_state=recovery_state,
                working_dir=working_dir,
            ):
                if isinstance(item, AgentMessage):
                    # Log message
                    msg_dict = {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "type": item.type,
                        "content": item.content,
                        "metadata": item.metadata,
                        "recovery_session": True,
                    }
                    messages_file.write(json.dumps(msg_dict) + "\n")
                    messages_file.flush()

                    # Also write text to stdout
                    if item.type == "text":
                        stdout_file.write(item.content + "\n")
                        stdout_file.flush()
                        turn_count += 1
                    elif item.type == "tool_use":
                        tool_count += 1
                    elif item.type == "error":
                        stderr_file.write(item.content + "\n")
                        stderr_file.flush()

                        # Check for another context overflow (don't recursively recover)
                        metadata = item.metadata or {}
                        if metadata.get("exception") == "ContextOverflowError":
                            stderr_file.write("\n❌ Context overflow during recovery - manual intervention required\n")
                            stderr_file.flush()
                            return False

                    # Update progress.json
                    progress_data = {
                        "turns": turn_count,
                        "tool_calls": tool_count,
                        "elapsed_seconds": round(time.time() - start_time, 1),
                        "recovery_attempt": run.recovery_count,
                    }
                    progress_path.write_text(json.dumps(progress_data))

                elif isinstance(item, AgentResult):
                    # Update run with new session ID from recovery
                    run.claude_session_id = item.claude_session_id

                    # Accumulate cost from recovery session
                    run.cost_usd = (run.cost_usd or 0) + (item.total_cost_usd or 0)
                    run.input_tokens = (run.input_tokens or 0) + (item.input_tokens or 0)
                    run.output_tokens = (run.output_tokens or 0) + (item.output_tokens or 0)
                    run.model_used = item.model_used

                    # Update tokens.json
                    tokens_data = {
                        "input_tokens": run.input_tokens or 0,
                        "output_tokens": run.output_tokens or 0,
                        "estimated_cost_usd": run.cost_usd or 0,
                    }
                    tokens_path.write_text(json.dumps(tokens_data))

                    self.store.update_run(run)

                    if item.success:
                        stdout_file.write("\n✅ Recovery completed successfully\n")
                        stdout_file.flush()
                        return True
                    else:
                        stderr_file.write(f"\n❌ Recovery failed: {item.error}\n")
                        stderr_file.flush()
                        return False

            return False  # No result received

        except Exception as e:
            stderr_file.write(f"\n❌ Recovery error: {e}\n")
            stderr_file.flush()
            return False


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
