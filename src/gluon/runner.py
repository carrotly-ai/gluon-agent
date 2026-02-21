"""Background task runner for Gluon Agent.

Manages subprocess execution, log capture, and process lifecycle for
long-running Claude Code tasks.
"""

import asyncio
import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gluon.agent import AgentMessage, AgentResult, GluonAgent
from gluon.agent_hooks import ScreenshotCollector, TodoCollector
from gluon.git_manager import GitManager
from gluon.image_storage import ImageStorageService
from gluon.models import ExecutionRun, PendingQuestion, QuestionStatus, RunStatus, SupervisionConfig, utc_now
from gluon.notifier import NotificationDispatcher
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
        notifier: NotificationDispatcher | None = None,
    ):
        self.store = store or GluonStore()
        self.agent = agent or GluonAgent()
        self.config = config or RunnerConfig()
        self.notifier = notifier
        self.git_manager = GitManager(self.store)
        self.image_service = ImageStorageService(self.store)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._active_tasks: dict[str, asyncio.Task] = {}

        # Per-run follow-up queues (process-local, used by multi-turn execute loop)
        self._active_queues: dict[str, asyncio.Queue[str]] = {}

        # Supervision coordinator (lazy initialized)
        self._supervisor: ResumeCoordinator | None = None

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

    def _set_git_identity_env_vars(self) -> None:
        """Set git identity environment variables from settings.

        Git environment variables override ALL git config (system, global, local .git/config).
        This ensures all commits made by Claude SDK subprocess use the configured identity.

        Priority order (highest first):
        1. GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL env vars (we set these)
        2. -c user.name=X command-line flags
        3. .git/config (local/project)
        4. ~/.gitconfig (global)
        5. /etc/gitconfig (system)
        """
        git_author_name = self.store.get_setting("git_user_name", "")
        git_author_email = self.store.get_setting("git_user_email", "")

        if git_author_name:
            os.environ["GIT_AUTHOR_NAME"] = git_author_name
            os.environ["GIT_COMMITTER_NAME"] = git_author_name
        if git_author_email:
            os.environ["GIT_AUTHOR_EMAIL"] = git_author_email
            os.environ["GIT_COMMITTER_EMAIL"] = git_author_email

    async def _question_handler(self, run_id: str, questions: list[dict]) -> dict[str, str]:
        """Handle AskUserQuestion tool calls by storing questions and waiting for answers.

        This handler:
        1. Stores each question in the database as PendingQuestion
        2. Broadcasts to WebSocket subscribers so UI can show the modal
        3. Waits for user answer (polling with timeout)
        4. Returns answers dict mapping question header -> selected answer

        Args:
            run_id: The ExecutionRun ID
            questions: List of question dicts from AskUserQuestion tool

        Returns:
            Dict mapping question header to selected answer string
        """
        from gluon.web.websocket import ws_manager

        answers: dict[str, str] = {}
        question_ids: list[str] = []

        # Store each question in database
        for idx, q in enumerate(questions):
            pending = PendingQuestion(
                run_id=run_id,
                question_index=idx,
                question_text=q.get("question", ""),
                header=q.get("header", "Question"),
                options=[
                    {"label": opt.get("label", ""), "description": opt.get("description", "")}
                    for opt in q.get("options", [])
                ],
                multi_select=q.get("multiSelect", False),
                expires_at=utc_now() + timedelta(seconds=300),  # 5 min timeout
            )
            self.store.create_pending_question(pending)
            question_ids.append(pending.id)
            logger.info(f"Created pending question {pending.id[:8]} for run {run_id[:8]}: {pending.header}")

        # Broadcast to WebSocket so UI can show modal
        await ws_manager.broadcast_pending_questions(run_id, questions, question_ids)

        # Wait for answers (polling with timeout)
        timeout = 300  # 5 minutes
        poll_interval = 0.5
        elapsed = 0.0

        while elapsed < timeout:
            all_answered = True

            for qid in question_ids:
                q = self.store.get_pending_question(qid)
                if not q:
                    continue

                if q.status == QuestionStatus.PENDING:
                    all_answered = False
                elif q.status in (QuestionStatus.ANSWERED, QuestionStatus.AUTO_ANSWERED):
                    # Collect answer (use first selected label)
                    answers[q.header] = q.selected_labels[0] if q.selected_labels else ""

            if all_answered:
                logger.info(f"All questions answered for run {run_id[:8]}")
                return answers

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout: auto-answer remaining questions with first/recommended option
        logger.warning(f"Question timeout for run {run_id[:8]}, auto-answering remaining")
        for qid in question_ids:
            q = self.store.get_pending_question(qid)
            if not q:
                continue

            if q.status == QuestionStatus.PENDING:
                q.auto_answer(source="timeout_auto")
                self.store.update_pending_question(q)
                answers[q.header] = q.selected_labels[0] if q.selected_labels else ""
                logger.info(f"Auto-answered question {qid[:8]}: {q.header} = {answers[q.header]}")

        return answers

    async def _auto_answer_handler(self, run_id: str, questions: list[dict]) -> dict[str, str]:
        """Auto-answer handler for Ralph loops - immediately selects recommended option.

        Ralph loops run autonomously without user interaction, so questions are
        auto-answered immediately with the recommended option (or first option).

        Args:
            run_id: The ExecutionRun ID
            questions: List of question dicts from AskUserQuestion tool

        Returns:
            Dict mapping question header to selected answer string
        """
        answers: dict[str, str] = {}

        for q in questions:
            header = q.get("header", "Question")
            options = q.get("options", [])

            # Find recommended option (contains "(Recommended)")
            selected = None
            for opt in options:
                label = opt.get("label", "")
                if "(Recommended)" in label or "(recommended)" in label:
                    selected = label
                    break

            # Fall back to first option
            if not selected and options:
                selected = options[0].get("label", "")

            answers[header] = selected or ""
            logger.info(f"Ralph auto-answered for run {run_id[:8]}: {header} = {selected}")

        return answers

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
        profile: str | None = None,
        thinking_budget: str | None = None,
        force_planning: bool | None = None,
        agent_teams: bool | None = None,
        model_transition: str | None = None,
        effort: str | None = None,
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
            profile: Task profile (quick/standard/deep/planning)
            thinking_budget: Override thinking budget (none/low/medium/high/ultrathink)
            force_planning: Override planning mode (True = plan before executing)

        Returns:
            ExecutionRun with current status
        """
        # Resolve task options from profile and overrides
        from gluon.models import resolve_task_options

        task_options = resolve_task_options(
            profile=profile,
            model=model,
            thinking_budget=thinking_budget,
            max_budget_usd=max_cost_usd,  # Use max_cost_usd as budget override
            force_planning=force_planning,
            effort=effort,
        )

        # Determine cost limit:
        # - If user provided explicit cost limit, use it
        # - If Ralph enabled with no explicit limit, use high default ($1000 or env var)
        # - Otherwise use profile's budget
        default_ralph_cost = float(os.environ.get("DEFAULT_RALPH_COST_LIMIT", "1000.0"))
        if max_cost_usd is not None:
            effective_cost_limit = max_cost_usd
        elif ralph_enabled:
            effective_cost_limit = default_ralph_cost
        else:
            effective_cost_limit = task_options["max_budget_usd"]

        # Create run record with resolved model
        run = self.store.create_run(
            project_id,
            prompt,
            initiator=initiator,
            use_worktree=use_worktree,
            model=task_options["model"],
            ralph_enabled=ralph_enabled,
            max_loops=max_loops,
            max_calls_per_hour=max_calls_per_hour,
            max_cost_usd=effective_cost_limit,
        )
        run.claude_session_id = claude_session_id  # Set for resume

        # Store profile options in run metadata for _run_task to use
        if run.metadata is None:
            run.metadata = {}
        run.metadata["profile"] = profile or "standard"
        run.metadata["max_thinking_tokens"] = task_options["max_thinking_tokens"]
        run.metadata["max_turns"] = task_options["max_turns"]
        run.metadata["force_planning"] = task_options["force_planning"]
        if task_options.get("effort"):
            run.metadata["effort"] = task_options["effort"]
        if agent_teams is not None:
            run.metadata["agent_teams"] = agent_teams
        if model_transition:
            run.metadata["model_transition"] = model_transition
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

            # Write user's follow-up prompt as a visible message
            # Skip auto-resume prompts (they start with system-generated text)
            if run.prompt and not run.prompt.startswith("[SUPERVISION"):
                user_msg = {
                    "type": "user",
                    "content": run.prompt,
                    "timestamp": run.last_resumed_at.isoformat() if run.last_resumed_at else None,
                }
                with open(messages_path, "a") as f:
                    f.write(json.dumps(user_msg) + "\n")

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

        # Build environment with git identity settings
        # This ensures subprocess inherits configured git author identity
        env = os.environ.copy()
        git_author_name = self.store.get_setting("git_user_name", "")
        git_author_email = self.store.get_setting("git_user_email", "")
        if git_author_name:
            env["GIT_AUTHOR_NAME"] = git_author_name
            env["GIT_COMMITTER_NAME"] = git_author_name
        if git_author_email:
            env["GIT_AUTHOR_EMAIL"] = git_author_email
            env["GIT_COMMITTER_EMAIL"] = git_author_email

        # Browser session isolation per run
        env["AGENT_BROWSER_SESSION"] = f"gluon-{run.id[:8]}"

        # Allocate a dev port for agent-browser / dev server usage
        run_meta = run.metadata or {}
        dev_port = str(run_meta.get("dev_port") or random.randint(3100, 3999))
        env["GLUON_DEV_PORT"] = dev_port

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
                env=env,
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
        old_status = run.status

        # Set git identity environment variables from settings
        # This ensures ALL git commits (by Gluon OR Claude SDK) use configured identity
        self._set_git_identity_env_vars()

        # Browser session isolation per run
        os.environ["AGENT_BROWSER_SESSION"] = f"gluon-{run.id[:8]}"

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
                # Wire up question handler with run_id bound
                from functools import partial

                question_handler = partial(self._question_handler, run.id)

                # Get profile options from run metadata (set by submit())
                metadata = run.metadata or {}
                max_thinking_tokens = metadata.get("max_thinking_tokens")
                max_turns = metadata.get("max_turns")
                force_planning = metadata.get("force_planning", False)

                # Read sandbox setting (default enabled for security)
                sandbox_enabled = self.store.get_setting("sandbox_enabled", "true") == "true"
                # Per-task override from metadata, fall back to global setting
                agent_teams_override = metadata.get("agent_teams")
                agent_teams_enabled = (
                    agent_teams_override
                    if agent_teams_override is not None
                    else self.store.get_setting("agent_teams_enabled", "false") == "true"
                )

                # SDK 0.1.35 feature settings
                extended_context = self.store.get_setting("extended_context_enabled", "false") == "true"
                file_checkpointing = self.store.get_setting("file_checkpointing_enabled", "false") == "true"
                disallowed_tools_json = self.store.get_setting("disallowed_tools", "[]")
                try:
                    disallowed_tools = json.loads(disallowed_tools_json)
                except (json.JSONDecodeError, TypeError):
                    disallowed_tools = []
                model_transition = metadata.get("model_transition")
                effort = metadata.get("effort")

                # Vercel CLI integration (optional)
                vercel_cli_enabled = self.store.get_setting("vercel_cli_enabled", "false") == "true"
                vercel_token = self.store.get_setting("vercel_token", "") or os.environ.get("VERCEL_TOKEN") or None

                agent = GluonAgent(
                    model=run.model or self.agent.model,
                    question_handler=question_handler,
                    run_id=run.id,
                    max_thinking_tokens=max_thinking_tokens,
                    max_turns=max_turns,
                    max_budget_usd=run.max_cost_usd,
                    force_planning=force_planning,
                    sandbox_enabled=sandbox_enabled,
                    agent_teams_enabled=agent_teams_enabled,
                    extended_context_enabled=extended_context,
                    file_checkpointing_enabled=file_checkpointing,
                    disallowed_tools=disallowed_tools or None,
                    model_transition=model_transition,
                    effort=effort,
                    vercel_cli_enabled=vercel_cli_enabled,
                    vercel_token=vercel_token,
                )

                # Create screenshot collector for intercepting agent-browser screenshots
                def _screenshot_message_writer(msg: dict) -> None:
                    messages_file.write(json.dumps(msg) + "\n")
                    messages_file.flush()

                screenshot_collector = ScreenshotCollector(
                    run_id=run.id,
                    working_dir=working_dir,
                    image_service=self.image_service,
                    store=self.store,
                    message_callback=_screenshot_message_writer,
                )

                # Create todo collector for mirroring TodoWrite calls to the store
                todo_collector = TodoCollector(
                    run_id=run.id,
                    store=self.store,
                    message_callback=_screenshot_message_writer,
                )

                # Allocate a dev port for agent-browser / dev server usage
                dev_port = str(metadata.get("dev_port") or random.randint(3100, 3999))
                os.environ["GLUON_DEV_PORT"] = dev_port

                # Create follow-up queue so the multi-turn loop can
                # process in-session follow-ups without spawning subprocesses
                follow_up_queue: asyncio.Queue[str] = asyncio.Queue()
                self._active_queues[run.id] = follow_up_queue

                # Drain any DB-queued messages that arrived before we started
                self._drain_db_queue_into(run, follow_up_queue)

                # Background task: poll DB for follow-ups queued by the web API
                db_poller = asyncio.create_task(self._poll_db_followups(run.id, follow_up_queue))

                # Execute via agent with images as base64 content blocks
                try:
                    async for item in agent.execute(
                        working_dir=working_dir,
                        prompt=effective_prompt,
                        resume_session_id=run.claude_session_id,
                        images=image_paths if image_paths else None,
                        follow_up_queue=follow_up_queue,
                        screenshot_collector=screenshot_collector,
                        notification_callback=_screenshot_message_writer,
                        todo_collector=todo_collector,
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
                                    recovery_result = await self._handle_auto_recovery(
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
                            # Don't overwrite a good session ID with None from a failed resume
                            if item.claude_session_id:
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

                            # For worktree runs: auto-commit uncommitted changes, push and create PR
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
                                        stdout_file.write(
                                            f"\n✓ Auto-committed {commit_result['files_count']} file(s)\n"
                                        )
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

                            # Capture commit/file snapshots before status change
                            # This preserves data even after branch merge/deletion
                            if run.branch_name and not run.changes_snapshotted:
                                try:
                                    commits, files, commit_files = await self.git_manager.capture_branch_snapshots(
                                        path=working_path,
                                        run_id=run.id,
                                        branch_name=run.branch_name,
                                        base_branch=run.source_branch or "main",
                                    )
                                    if commits or files:
                                        self.store.save_run_snapshots(run.id, commits, files, commit_files)
                                        run.changes_snapshotted = True
                                        run.snapshot_at = utc_now()
                                        stdout_file.write(
                                            f"\n✓ Captured {len(commits)} commits, {len(files)} files for persistence\n"
                                        )
                                        stdout_file.flush()
                                except Exception as snap_err:
                                    stderr_file.write(f"Warning: Failed to capture snapshots: {snap_err}\n")
                                    stderr_file.flush()

                            if item.success:
                                run.mark_review()  # All tasks go to REVIEW first
                                # Disable supervision for completed tasks to prevent restart loop
                                if run.supervision_config is None:
                                    run.supervision_config = SupervisionConfig()
                                run.supervision_config.enabled = False
                                if not run.completion_reason:
                                    run.completion_reason = "Task completed successfully"
                            else:
                                # If this was a resume attempt, try fresh-start recovery
                                # before giving up. Recovery creates a new session in the
                                # same worktree with a summary of previous progress.
                                recovered = False
                                if is_resumed and run.recovery_count < 2:
                                    try:
                                        stdout_file.write(
                                            f"\n⚠️ Resume failed: {item.error}\nAttempting fresh-start recovery...\n"
                                        )
                                        stdout_file.flush()
                                        recovered = await self._handle_auto_recovery(
                                            run=run,
                                            working_dir=working_dir,
                                            stdout_file=stdout_file,
                                            stderr_file=stderr_file,
                                            messages_file=messages_file,
                                            progress_path=progress_path,
                                            tokens_path=tokens_path,
                                            start_time=start_time,
                                        )
                                    except Exception as recovery_err:
                                        stderr_file.write(f"Recovery failed: {recovery_err}\n")
                                        stderr_file.flush()

                                if recovered:
                                    run.mark_review()
                                    self.store.update_run(run)
                                    return
                                else:
                                    run.mark_failed(item.error or "Unknown error", exit_code=1)
                finally:
                    # Stop DB poller and clean up queue
                    db_poller.cancel()
                    try:
                        await db_poller
                    except asyncio.CancelledError:
                        pass
                    self._active_queues.pop(run.id, None)

        except asyncio.CancelledError:
            run.mark_cancelled()
            raise
        except Exception as e:
            run.mark_failed(str(e), exit_code=1)
        finally:
            # Kill any dev server left on GLUON_DEV_PORT to prevent orphaned processes
            dev_port = os.environ.get("GLUON_DEV_PORT")
            if dev_port:
                try:
                    subprocess.run(
                        ["fuser", "-k", f"{dev_port}/tcp"],
                        capture_output=True,
                        timeout=5,
                    )
                except Exception:
                    pass

            self.store.update_run(run)

            # Notify mapped channels of status change
            if self.notifier and run.status != old_status:
                try:
                    await self.notifier.notify(run, old_status, run.status)
                except Exception:
                    logger.debug("Notification dispatch failed", exc_info=True)

            # Clean up active task tracking
            if run.id in self._active_tasks:
                del self._active_tasks[run.id]
            # Ensure queue is removed even on unexpected exit
            self._active_queues.pop(run.id, None)

        # Check for queued follow-up message and auto-resume if present
        await self._handle_queued_followup(run)

    def _drain_db_queue_into(self, run: ExecutionRun, queue: asyncio.Queue[str]) -> None:
        """Move any DB-queued messages into the asyncio queue (one-shot).

        Called at task start so messages queued before the execute loop began
        are available immediately without waiting for the next poll cycle.
        """
        refreshed = self.store.get_run(run.id)
        if not refreshed or not refreshed.queued_messages:
            return

        for msg in list(refreshed.queued_messages):
            queue.put_nowait(msg.message)
        refreshed.queued_messages.clear()
        self.store.update_run(refreshed)
        logger.info(
            "Drained %d queued message(s) into follow-up queue for run %s",
            queue.qsize(),
            run.id[:8],
        )

    async def _poll_db_followups(self, run_id: str, queue: asyncio.Queue[str], interval: float = 2.0) -> None:
        """Background coroutine that polls the DB for follow-ups queued by the web API.

        The web API writes to `run.queued_messages` in the database.  This
        poller drains those messages into the asyncio queue so the multi-turn
        execute loop can pick them up without blocking.

        Runs until cancelled (by the finally block in _run_task).
        """
        while True:
            await asyncio.sleep(interval)
            try:
                run = self.store.get_run(run_id)
                if not run or not run.queued_messages:
                    continue
                for msg in list(run.queued_messages):
                    await queue.put(msg.message)
                run.queued_messages.clear()
                self.store.update_run(run)
                logger.info("Polled %d follow-up(s) for run %s", queue.qsize(), run_id[:8])
            except Exception:
                logger.debug("DB follow-up poll error for run %s", run_id[:8], exc_info=True)

    async def _handle_queued_followup(self, run: ExecutionRun) -> None:
        """Fallback: check for queued follow-up messages and auto-resume if present.

        This is called after task completion (normal or ralph loop) as a safety net.
        Most follow-ups are now handled in-session by the multi-turn execute loop
        and the DB poller.  This handles any that remain (e.g. queued after the
        execute loop exited but before the run was marked complete).
        """
        # Refresh run from database to get latest state
        run = self.store.get_run(run.id)
        if not run:
            return

        if not run.queued_messages:
            return

        # Process all queued messages sequentially
        while run.queued_messages:
            # Pop the first message from the queue
            msg = run.queued_messages.pop(0)
            self.store.update_run(run)  # Persist queue change

            logger.info(f"Auto-resuming run {run.id[:8]} with queued message {msg.id}")

            try:
                await self.resume_in_place(
                    run_id=run.id,
                    new_prompt=msg.message,
                    wait=True,  # Execute inline (already in worker process)
                    initiator=f"auto:queued_{msg.id}",
                )
            except Exception as e:
                logger.error(f"Auto-resume failed for run {run.id[:8]}: {e}")
                # Don't re-raise - best-effort processing
                break

            # Refresh run for next iteration
            run = self.store.get_run(run.id)
            if not run or run.status not in (RunStatus.COMPLETED, RunStatus.REVIEW):
                break  # Stop if task failed/cancelled

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

        If the process has terminated unexpectedly, attempt to salvage any
        uncommitted work in the worktree before marking as failed.

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
            # Process is gone — re-read from DB to check if task completed
            # between our check (avoids race where task finishes + process exits
            # before we can observe both)
            fresh_run = self.store.get_run(run.id)
            if fresh_run and fresh_run.status != RunStatus.RUNNING:
                return fresh_run  # Task completed normally, process exited after DB update

            # Process is gone unexpectedly - attempt to salvage uncommitted work
            logger.warning(
                f"Process for run {run.id[:8]} terminated unexpectedly (pid={run.pid}, worktree={run.worktree_path})"
            )

            # Try to salvage any uncommitted changes
            salvage_result = self._salvage_uncommitted_work_sync(run)

            # Mark as failed with appropriate message
            if salvage_result.get("salvaged"):
                error_msg = (
                    f"Process terminated unexpectedly. Salvaged {salvage_result['files_count']} uncommitted file(s)."
                )
            else:
                error_msg = "Process terminated unexpectedly"
                if salvage_result.get("error"):
                    error_msg += f": {salvage_result['error']}"

            run.mark_failed(error_msg, exit_code=-1)
            self.store.update_run(run)
        except PermissionError:
            # Can't check - leave as is
            pass

        return run

    def _salvage_uncommitted_work_sync(self, run: ExecutionRun) -> dict[str, Any]:
        """
        Emergency salvage of uncommitted files when process terminates unexpectedly.

        This is a synchronous method that uses subprocess.run directly to avoid
        async/sync context issues. Called from refresh_run_status() which may be
        invoked from both sync (CLI) and async (API) contexts.

        Args:
            run: The run whose worktree may have uncommitted changes

        Returns:
            Dict with salvage results:
            - salvaged: bool - whether files were salvaged
            - files_count: int - number of files committed
            - pushed: bool - whether push succeeded
            - error: str | None - error message if salvage failed
        """
        if not run.use_worktree or not run.worktree_path:
            return {"salvaged": False, "error": "Not a worktree run"}

        worktree_path = Path(run.worktree_path)
        if not worktree_path.exists():
            return {"salvaged": False, "error": "Worktree no longer exists"}

        try:
            # Check for uncommitted changes using git status --porcelain
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if status_result.returncode != 0:
                return {"salvaged": False, "error": f"git status failed: {status_result.stderr}"}

            # Count uncommitted files
            uncommitted_files = [line for line in status_result.stdout.strip().split("\n") if line.strip()]
            if not uncommitted_files:
                return {"salvaged": False, "error": "No uncommitted changes to salvage"}

            files_count = len(uncommitted_files)
            logger.info(f"Found {files_count} uncommitted file(s) to salvage for run {run.id[:8]}")

            # Stage all changes
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if add_result.returncode != 0:
                return {"salvaged": False, "error": f"git add failed: {add_result.stderr}"}

            # Commit with salvage message using configured git identity
            prompt_preview = run.prompt[:50] if run.prompt else "unknown task"
            commit_msg = (
                f"SALVAGE: {prompt_preview}...\n\nAuto-salvaged after unexpected process termination\nRun ID: {run.id}"
            )

            # Build commit command with author flag if configured
            commit_cmd = ["git", "commit", "-m", commit_msg]
            git_author_name = self.store.get_setting("git_user_name", "")
            git_author_email = self.store.get_setting("git_user_email", "")
            if git_author_name and git_author_email:
                commit_cmd.extend(["--author", f"{git_author_name} <{git_author_email}>"])

            commit_result = subprocess.run(
                commit_cmd,
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if commit_result.returncode != 0:
                return {"salvaged": False, "error": f"git commit failed: {commit_result.stderr}"}

            logger.warning(f"Salvaged {files_count} uncommitted file(s) for run {run.id[:8]}")

            # Try to push if we have a branch name
            pushed = False
            if run.branch_name:
                push_result = subprocess.run(
                    ["git", "push", "-u", "origin", run.branch_name],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if push_result.returncode == 0:
                    pushed = True
                    logger.info(f"Pushed salvaged commit for run {run.id[:8]}")
                else:
                    logger.warning(f"Failed to push salvaged commit for run {run.id[:8]}: {push_result.stderr}")

            return {
                "salvaged": True,
                "files_count": files_count,
                "pushed": pushed,
            }

        except subprocess.TimeoutExpired:
            return {"salvaged": False, "error": "Git operation timed out"}
        except Exception as e:
            logger.error(f"Failed to salvage uncommitted work for run {run.id[:8]}: {e}")
            return {"salvaged": False, "error": str(e)}

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
            "failure_reason": run.error_message,
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

        old_status = run.status

        # Set git identity environment variables from settings
        # This ensures ALL git commits (by Gluon OR Claude SDK) use configured identity
        self._set_git_identity_env_vars()

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
            # Ralph loops use auto-answer handler (no user interaction)
            from functools import partial

            auto_handler = partial(self._auto_answer_handler, run.id)
            sandbox_enabled = self.store.get_setting("sandbox_enabled", "true") == "true"
            ralph_metadata = run.metadata or {}
            agent_teams_override = ralph_metadata.get("agent_teams")
            agent_teams_enabled = (
                agent_teams_override
                if agent_teams_override is not None
                else self.store.get_setting("agent_teams_enabled", "false") == "true"
            )
            # Vercel CLI integration (optional)
            vercel_cli_enabled = self.store.get_setting("vercel_cli_enabled", "false") == "true"
            vercel_token = self.store.get_setting("vercel_token", "") or os.environ.get("VERCEL_TOKEN") or None

            agent = GluonAgent(
                model=run.model or self.agent.model,
                question_handler=auto_handler,
                run_id=run.id,
                sandbox_enabled=sandbox_enabled,
                agent_teams_enabled=agent_teams_enabled,
                vercel_cli_enabled=vercel_cli_enabled,
                vercel_token=vercel_token,
            )

            # Create and execute ralph manager
            manager = RalphManager(
                run=run,
                agent=agent,
                store=self.store,
                working_dir=working_dir,
                log_dir=log_dir,
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

            # Git operations for worktree runs (same as regular task completion)
            project = self.store.get_project(run.project_id)
            if project and updated_run.status == RunStatus.REVIEW:
                working_path = Path(run.worktree_path) if run.worktree_path else project.expanded_path

                # Capture git info
                try:
                    git_info = await self.git_manager.capture_run_git_info(working_path)
                    updated_run.branch_name = git_info.get("branch_name") or updated_run.branch_name
                    updated_run.git_commit_sha = git_info.get("git_commit_sha")
                except Exception as git_err:
                    with open(stdout_path, "a") as f:
                        f.write(f"Warning: Failed to capture git info: {git_err}\n")

                # Auto-commit and create PR for worktree runs
                auto_create_pr = self.store.get_setting("auto_create_pr", "true") == "true"
                if updated_run.use_worktree and updated_run.branch_name:
                    # Auto-commit uncommitted changes
                    try:
                        prompt_preview = updated_run.prompt[:60]
                        ellipsis = "..." if len(updated_run.prompt) > 60 else ""
                        commit_msg = (
                            f"chore: {prompt_preview}{ellipsis}\n\n"
                            f"Auto-committed by Gluon Agent (Ralph Loop)\nRun ID: {updated_run.id}"
                        )
                        commit_result = await self.git_manager.auto_commit_changes(
                            path=working_path,
                            message=commit_msg,
                            run_id=updated_run.id,
                        )
                        if commit_result.get("committed"):
                            with open(stdout_path, "a") as f:
                                f.write(f"✓ Auto-committed {commit_result['files_count']} file(s)\n")
                    except Exception as commit_err:
                        with open(stdout_path, "a") as f:
                            f.write(f"Warning: Auto-commit failed: {commit_err}\n")

                    # Push and create PR
                    if auto_create_pr:
                        try:
                            pr_result = await self.git_manager.push_branch_and_create_pr(
                                project_path=working_path,
                                branch_name=updated_run.branch_name,
                                prompt=updated_run.prompt,
                                run_id=updated_run.id,
                            )
                            if pr_result.get("pushed"):
                                with open(stdout_path, "a") as f:
                                    f.write(f"✓ Pushed branch {updated_run.branch_name} to remote\n")
                            if pr_result.get("pr_url"):
                                updated_run.pr_number = pr_result.get("pr_number")
                                updated_run.pr_url = pr_result.get("pr_url")
                                updated_run.pr_status = pr_result.get("pr_status")
                                with open(stdout_path, "a") as f:
                                    f.write(f"✓ Created PR: {updated_run.pr_url}\n")
                                self.store.update_run(updated_run)
                            elif pr_result.get("error"):
                                with open(stdout_path, "a") as f:
                                    f.write(f"Warning: PR creation: {pr_result['error']}\n")
                        except Exception as pr_err:
                            with open(stdout_path, "a") as f:
                                f.write(f"Warning: Failed to push/create PR: {pr_err}\n")

                # Capture commit/file snapshots before finishing
                if updated_run.branch_name and not updated_run.changes_snapshotted:
                    try:
                        commits, files, commit_files = await self.git_manager.capture_branch_snapshots(
                            path=working_path,
                            run_id=updated_run.id,
                            branch_name=updated_run.branch_name,
                            base_branch=updated_run.source_branch or "main",
                        )
                        if commits or files:
                            self.store.save_run_snapshots(updated_run.id, commits, files, commit_files)
                            updated_run.changes_snapshotted = True
                            updated_run.snapshot_at = utc_now()
                            self.store.update_run(updated_run)
                            with open(stdout_path, "a") as f:
                                f.write(f"✓ Captured {len(commits)} commits, {len(files)} files for persistence\n")
                    except Exception as snap_err:
                        with open(stdout_path, "a") as f:
                            f.write(f"Warning: Failed to capture snapshots: {snap_err}\n")

        except Exception as e:
            logger.error(f"Ralph loop failed: {e}")
            run.mark_failed(str(e))
            self.store.update_run(run)

            with open(stdout_path, "a") as f:
                f.write(f"\n\nRALPH LOOP ERROR: {e}\n")

        # Notify mapped channels of status change
        final_run = locals().get("updated_run", run)
        if self.notifier and final_run.status != old_status:
            try:
                await self.notifier.notify(final_run, old_status, final_run.status)
            except Exception:
                logger.debug("Notification dispatch failed (ralph)", exc_info=True)

        # Check for queued follow-up message and auto-resume if present
        # Use updated_run if available (success path), otherwise use run (error path)
        await self._handle_queued_followup(final_run)

    async def _handle_auto_recovery(
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
        Handle automatic recovery by starting a fresh session with progress summary.

        Triggered by context overflow or resume/fork failures. Extracts completed
        work from logs, creates a new agent, and calls resume_with_fresh_context()
        with a summary prompt so the fresh session can continue where the previous
        one left off.

        Args:
            run: The execution run that needs recovery
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
            sandbox_enabled = self.store.get_setting("sandbox_enabled", "true") == "true"
            recovery_metadata = run.metadata or {}
            agent_teams_override = recovery_metadata.get("agent_teams")
            agent_teams_enabled = (
                agent_teams_override
                if agent_teams_override is not None
                else self.store.get_setting("agent_teams_enabled", "false") == "true"
            )
            # Vercel CLI integration (optional)
            vercel_cli_enabled = self.store.get_setting("vercel_cli_enabled", "false") == "true"
            vercel_token = self.store.get_setting("vercel_token", "") or os.environ.get("VERCEL_TOKEN") or None

            recovery_agent = (
                GluonAgent(
                    model=run.model,
                    sandbox_enabled=sandbox_enabled,
                    agent_teams_enabled=agent_teams_enabled,
                    vercel_cli_enabled=vercel_cli_enabled,
                    vercel_token=vercel_token,
                )
                if run.model
                else self.agent
            )

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
                    # Don't overwrite a good session ID with None from a failed attempt
                    if item.claude_session_id:
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
