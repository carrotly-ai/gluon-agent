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
from datetime import datetime
from pathlib import Path

from gluon.agent import AgentMessage, AgentResult, GluonAgent
from gluon.git_manager import GitManager
from gluon.models import ExecutionRun, RunStatus
from gluon.store import DEFAULT_LOG_PATH, GluonStore


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
    ) -> ExecutionRun:
        """
        Submit a task for execution.

        Args:
            project_id: Project to run task on
            prompt: Task prompt
            wait: If True, wait for completion. If False, return immediately.
            initiator: Who started the run (e.g., "cli", "telegram:12345")
            claude_session_id: Optional Claude SDK session ID to resume from

        Returns:
            ExecutionRun with current status
        """
        # Create run record
        run = self.store.create_run(project_id, prompt, initiator=initiator)
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

        # Setup logging
        log_dir = self._get_log_dir(run.id)
        stdout_path = log_dir / "stdout.log"
        stderr_path = log_dir / "stderr.log"
        messages_path = log_dir / "messages.jsonl"

        # Update run status
        run.mark_running(pid=os.getpid(), log_path=log_dir)
        self.store.update_run(run)

        try:
            # Open log files
            with (
                open(stdout_path, "w") as stdout_file,
                open(stderr_path, "w") as stderr_file,
                open(messages_path, "w") as messages_file,
            ):
                # Execute via agent (pass claude_session_id for resume if set)
                async for item in self.agent.execute(
                    working_dir=project.path,
                    prompt=run.prompt,
                    resume_session_id=run.claude_session_id,
                ):
                    if isinstance(item, AgentMessage):
                        # Log message
                        msg_dict = {
                            "timestamp": datetime.now().isoformat(),
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
                        # Log final result
                        result_dict = {
                            "timestamp": datetime.now().isoformat(),
                            "type": "result",
                            "session_id": item.claude_session_id,
                            "cost_usd": item.total_cost_usd,
                            "input_tokens": item.input_tokens,
                            "output_tokens": item.output_tokens,
                            "model_used": item.model_used,
                            "turns": item.total_turns,
                            "success": item.success,
                            "error": item.error,
                        }
                        messages_file.write(json.dumps(result_dict) + "\n")

                        # Update run with Claude session ID for future resume
                        run.claude_session_id = item.claude_session_id
                        # Store cost tracking data
                        run.cost_usd = item.total_cost_usd
                        run.input_tokens = item.input_tokens
                        run.output_tokens = item.output_tokens
                        run.model_used = item.model_used

                        # Determine working path (worktree or project)
                        working_path = (
                            Path(run.worktree_path) if run.worktree_path else project.path
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

                        # For worktree runs: push branch and create PR
                        if run.use_worktree and run.branch_name and item.success:
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
        if not run or not run.log_path:
            return {"stdout": "", "stderr": "", "messages": ""}

        log_dir = run.log_path
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
