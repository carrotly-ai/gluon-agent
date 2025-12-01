"""Telegram bot interface for Gluon Agent."""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from gluon.agent import AgentMessage, AgentResult
from gluon.chat_agent import ChatMessage, GluonChatAgent
from gluon.core import Orchestrator, ProjectNotFoundError
from gluon.git_manager import GitManager
from gluon.models import ExecutionRun, RunStatus
from gluon.models_config import ModelTier
from gluon.runner import TaskRunner, format_duration, format_run_status
from gluon.store import GluonStore

logger = logging.getLogger(__name__)


class GluonBot:
    """Telegram bot for interacting with Gluon Agent."""

    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
        max_concurrent: int = 16,
    ):
        """
        Initialize the Gluon Telegram bot.

        Args:
            token: Telegram bot token from @BotFather
            allowed_users: List of Telegram user IDs allowed to use the bot.
                          If None, all users are allowed (not recommended for production).
            max_concurrent: Maximum concurrent tasks across all users.
        """
        self.token = token
        self.allowed_users = allowed_users
        self.store = GluonStore()
        self.git_manager = GitManager(store=self.store)
        self.orchestrator = Orchestrator(store=self.store, git_manager=self.git_manager)
        self.runner = TaskRunner(store=self.store)
        self.chat_agent = GluonChatAgent(self.orchestrator)
        self.app: Application | None = None
        # Track active asyncio tasks by run_id (for cancellation within this process)
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        # Global concurrency limit
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # Enable/disable natural language mode
        self.nl_mode_enabled = True
        # Message history per user for context (last 10 messages)
        self._message_history: dict[int, list[ChatMessage]] = {}
        self._max_history_per_user = 10

    def _is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized to use the bot."""
        if self.allowed_users is None:
            return True
        return user_id in self.allowed_users

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.effective_user or not update.message:
            return

        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text(
                f"You are not authorized to use this bot.\nYour user ID is: {update.effective_user.id}"
            )
            return

        await update.message.reply_text(
            "Welcome to Gluon Agent! 🔧\n\n"
            "You can chat naturally or use commands:\n\n"
            "**Natural language examples:**\n"
            '• "Show me my projects"\n'
            '• "Run a task on myapp to fix the login bug"\n'
            '• "Resume the last session on nextjs-demo"\n'
            '• "What\'s the status?"\n\n'
            "**Commands:**\n"
            "/projects - List registered projects\n"
            "/sessions [project] - List sessions\n"
            "/run <project> <prompt> - Run a task\n"
            "/resume <project> [prompt] - Resume last session\n"
            "/runs - List your background runs\n"
            "/status - Show overall status\n"
            "/cancel [run\\_id] - Cancel a run (or latest)\n"
            "/clear - Clear chat history\n"
            "/help - Show this message",
            parse_mode="Markdown",
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        if not update.message:
            return
        await self.start(update, context)

    async def clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /clear command to clear chat history."""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        # Clear message history for this user
        if user_id in self._message_history:
            del self._message_history[user_id]

        await update.message.reply_text("Chat history cleared.")

    async def projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /projects command."""
        if not update.effective_user or not update.message:
            return

        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        projects = self.orchestrator.list_projects()

        if not projects:
            await update.message.reply_text("No projects registered.\nUse CLI: `gluon project add <name> <path>`")
            return

        lines = ["**Projects:**\n"]
        for p in projects:
            sessions = self.orchestrator.list_sessions(p.name)
            # Escape path to avoid Markdown parsing issues
            safe_path = str(p.path).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
            lines.append(f"- `{p.name}` ({len(sessions)} sessions)")
            lines.append(f"  {safe_path}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def sessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /sessions command."""
        if not update.effective_user or not update.message:
            return

        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        project_name = context.args[0] if context.args else None

        try:
            session_list = self.orchestrator.list_sessions(project_name)
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        if not session_list:
            await update.message.reply_text("No sessions found.")
            return

        lines = [f"**Sessions{f' for {project_name}' if project_name else ''}:**\n"]

        # Build project lookup
        project_lookup: dict[str, str] = {}
        if not project_name:
            for p in self.orchestrator.list_projects():
                project_lookup[p.id] = p.name

        for s in session_list[:10]:  # Limit to 10 most recent
            project_display = project_lookup.get(s.project_id, "") if not project_name else ""
            status_emoji = {
                "active": "🟢",
                "paused": "🟡",
                "completed": "🔵",
                "failed": "🔴",
            }.get(s.status.value, "⚪")

            line = f"{status_emoji} "
            if project_display:
                line += f"`{project_display}` "
            line += f"${s.total_cost_usd:.4f} | {s.total_turns} turns"
            if s.last_prompt:
                line += f"\n   _{s.last_prompt[:50]}{'...' if len(s.last_prompt) > 50 else ''}_"
            lines.append(line)

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not update.effective_user or not update.message:
            return

        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        status_info = self.orchestrator.status()

        text = (
            f"**Gluon Status**\n\n"
            f"Projects: {status_info['total_projects']}\n"
            f"Active Sessions: {status_info['active_sessions']}\n"
        )

        if status_info["projects"]:
            text += "\n**Projects:**\n"
            for p in status_info["projects"]:
                text += f"- `{p['name']}`: {p['sessions']} sessions\n"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def run(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /run command."""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /run <project> <prompt>\nExample: /run myapp Fix the login bug")
            return

        project_name = context.args[0]
        prompt = " ".join(context.args[1:])

        try:
            project = self.orchestrator.get_project(project_name)
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        # Check global concurrency limit
        active_runs = self.store.list_active_runs()
        if len(active_runs) >= self._semaphore._value:
            await update.message.reply_text(
                f"Max concurrent runs ({self._semaphore._value}) reached.\n"
                "Use /runs to see active runs or /cancel to stop one."
            )
            return

        # Create run record
        initiator = f"telegram:{user_id}"
        run = self.store.create_run(project.id, prompt, initiator=initiator)

        # Send initial message and capture its ID for threading
        start_msg = await update.message.reply_text(
            f"🚀 Task started: `{run.id[:8]}`\n"
            f"Project: `{project_name}`\n"
            f"Prompt: _{prompt[:80]}{'...' if len(prompt) > 80 else ''}_\n\n"
            f"Use /runs to check status",
            parse_mode="Markdown",
        )

        # Run the task in background, threading replies to start_msg
        task = asyncio.create_task(
            self._execute_task_with_runner(
                update, run, project_name, thread_msg_id=start_msg.message_id
            )
        )
        self._active_tasks[run.id] = task

    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /resume command."""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /resume <project> [session\\_id] [prompt]\n"
                "Examples:\n"
                "  /resume myapp\n"
                "  /resume myapp 36bac5aa\n"
                "  /resume myapp 36bac5aa Also add logging",
                parse_mode="Markdown",
            )
            return

        project_name = context.args[0]

        try:
            project = self.orchestrator.get_project(project_name)
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        # Check if second arg looks like a session/run ID (4-36 hex chars)
        session = None
        id_arg = None
        prompt_start_idx = 1

        if len(context.args) > 1:
            potential_id = context.args[1]
            # IDs are hex UUIDs - check if it looks like one
            if 4 <= len(potential_id) <= 36 and all(
                c in "0123456789abcdef-" for c in potential_id.lower()
            ):
                # User is attempting to specify an ID - track this even if not found
                id_arg = potential_id
                prompt_start_idx = 2

                # First try as a session ID
                session = self.store.get_session_by_short_id(potential_id, project.id)
                if not session:
                    # Try as a run ID - get the session from the run
                    run_lookup = self.store.get_run_by_short_id(potential_id)
                    if run_lookup and run_lookup.session_id:
                        session = self.store.get_session(run_lookup.session_id)
                        if session and session.project_id != project.id:
                            session = None  # Wrong project

        # If no specific session requested (id_arg is None), get the latest resumable one
        if not session and not id_arg:
            session = self.orchestrator.get_resumable_session(project)

        if not session or not session.claude_session_id:
            if id_arg:
                await update.message.reply_text(
                    f"Session/run `{id_arg}` not found for `{project_name}`.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    f"No resumable session for `{project_name}`.\nUse /run to start a new session.",
                    parse_mode="Markdown",
                )
            return

        prompt = (
            " ".join(context.args[prompt_start_idx:])
            if len(context.args) > prompt_start_idx
            else "Continue from where you left off."
        )

        # Check global concurrency limit
        active_runs = self.store.list_active_runs()
        if len(active_runs) >= self._semaphore._value:
            await update.message.reply_text(
                f"Max concurrent runs ({self._semaphore._value}) reached.\n"
                "Use /runs to see active runs or /cancel to stop one."
            )
            return

        # Create run record
        initiator = f"telegram:{user_id}"
        run = self.store.create_run(project.id, prompt, initiator=initiator)

        # Send initial message and capture its ID for threading
        start_msg = await update.message.reply_text(
            f"🔄 Resuming session: `{session.id[:8]}`\n"
            f"Project: `{project_name}`\n"
            f"Run: `{run.id[:8]}`\n"
            f"Use /runs to check status",
            parse_mode="Markdown",
        )

        # Run the task in background (force_new_session=False to resume existing session)
        task = asyncio.create_task(
            self._execute_task_with_runner(
                update, run, project_name, force_new_session=False, thread_msg_id=start_msg.message_id
            )
        )
        self._active_tasks[run.id] = task

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel command."""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        run_id_arg = context.args[0] if context.args else None

        if run_id_arg:
            # Cancel specific run by ID
            run = self.store.get_run_by_short_id(run_id_arg) or self.store.get_run(run_id_arg)
            if not run:
                await update.message.reply_text(f"Run not found: {run_id_arg}")
                return

            if not run.is_active:
                await update.message.reply_text(f"Run `{run.id[:8]}` is not active (status: {run.status.value})")
                return

            # Cancel asyncio task if we have it
            if run.id in self._active_tasks and not self._active_tasks[run.id].done():
                self._active_tasks[run.id].cancel()
                try:
                    await self._active_tasks[run.id]
                except asyncio.CancelledError:
                    pass
                del self._active_tasks[run.id]

            # Update run status
            run.mark_cancelled()
            self.store.update_run(run)
            await update.message.reply_text(f"✅ Cancelled run `{run.id[:8]}`")
        else:
            # Cancel user's latest active run
            initiator = f"telegram:{user_id}"
            user_runs = self.store.list_runs(
                initiator=initiator, statuses=[RunStatus.PENDING, RunStatus.RUNNING], limit=1
            )

            if not user_runs:
                await update.message.reply_text(
                    "No active runs to cancel.\nUse `/cancel <run_id>` to cancel a specific run."
                )
                return

            run = user_runs[0]
            if run.id in self._active_tasks and not self._active_tasks[run.id].done():
                self._active_tasks[run.id].cancel()
                try:
                    await self._active_tasks[run.id]
                except asyncio.CancelledError:
                    pass
                del self._active_tasks[run.id]

            run.mark_cancelled()
            self.store.update_run(run)
            await update.message.reply_text(f"✅ Cancelled run `{run.id[:8]}`")

    async def _execute_task_with_runner(
        self,
        update: Update,
        run: ExecutionRun,
        project_name: str,
        model: ModelTier | str | None = None,
        force_new_session: bool = True,
        thread_msg_id: int | None = None,
    ) -> None:
        """Execute a Gluon task with run tracking and stream updates to Telegram.

        Args:
            thread_msg_id: If provided, all progress messages will reply to this message,
                          creating a visual thread for this run's output.
        """
        if not update.message:
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        if not chat_id:
            return

        result: AgentResult | None = None
        message_buffer: list[str] = []
        last_update_time = 0.0

        async def send_update(text: str, parse_mode: str | None = None) -> None:
            """Send a message, threading to start message if available."""
            try:
                if thread_msg_id and self.app:
                    await self.app.bot.send_message(
                        chat_id=chat_id,
                        text=text[:4096],
                        reply_to_message_id=thread_msg_id,
                        parse_mode=parse_mode,
                    )
                else:
                    await update.message.reply_text(text[:4096], parse_mode=parse_mode)  # type: ignore
            except Exception as e:
                logger.warning(f"Failed to send update: {e}")

        # Mark run as running
        import os
        from pathlib import Path

        log_dir = Path.home() / ".gluon" / "logs" / run.id
        log_dir.mkdir(parents=True, exist_ok=True)
        run.mark_running(pid=os.getpid(), log_path=log_dir)
        self.store.update_run(run)

        try:
            async with self._semaphore:
                execution = self.orchestrator.execute(
                    project_name, run.prompt, force_new_session=force_new_session, model=model
                )
                async for item in execution:
                    if isinstance(item, AgentMessage):
                        if item.type == "text" and item.content:
                            message_buffer.append(item.content)
                            current_time = asyncio.get_event_loop().time()
                            if current_time - last_update_time > 2.0 and message_buffer:
                                text = "\n".join(message_buffer[-3:])
                                if len(text) > 4000:
                                    text = text[-4000:]
                                await send_update(text)
                                message_buffer.clear()
                                last_update_time = current_time

                    elif isinstance(item, AgentResult):
                        result = item

                # Send remaining buffered messages
                if message_buffer:
                    text = "\n".join(message_buffer[-5:])
                    if len(text) > 4000:
                        text = text[-4000:]
                    await send_update(text)

                # Update run status and send summary
                if result:
                    run.session_id = result.session_id  # Link to Gluon session (not claude_session_id)
                    if result.success:
                        run.mark_completed(exit_code=0)
                        summary = (
                            f"✅ **Complete** (`{run.id[:8]}`)\n"
                            f"Cost: ${result.total_cost_usd:.4f}\n"
                            f"Turns: {result.total_turns}"
                        )
                    else:
                        run.mark_failed(result.error or "Unknown error", exit_code=1)
                        summary = f"❌ **Failed** (`{run.id[:8]}`): {result.error}"
                    await send_update(summary, parse_mode="Markdown")

        except asyncio.CancelledError:
            run.mark_cancelled()
            await send_update(f"Task `{run.id[:8]}` was cancelled.")
        except Exception as e:
            logger.exception("Task execution failed")
            run.mark_failed(str(e), exit_code=1)
            await send_update(f"❌ Error (`{run.id[:8]}`): {e}")
        finally:
            self.store.update_run(run)
            if run.id in self._active_tasks:
                del self._active_tasks[run.id]

    async def _execute_task(
        self,
        update: Update,
        project_name: str,
        prompt: str,
        force_new: bool,
        model: ModelTier | str | None = None,
    ) -> None:
        """Execute a Gluon task and stream updates to Telegram (legacy, no run tracking)."""
        if not update.message:
            return

        result: AgentResult | None = None
        message_buffer: list[str] = []
        last_update_time = 0.0

        try:
            async for item in self.orchestrator.execute(project_name, prompt, force_new, model=model):
                if isinstance(item, AgentMessage):
                    if item.type == "text" and item.content:
                        # Buffer text messages
                        message_buffer.append(item.content)

                        # Send updates periodically (every 2 seconds) to avoid rate limits
                        current_time = asyncio.get_event_loop().time()
                        if current_time - last_update_time > 2.0 and message_buffer:
                            text = "\n".join(message_buffer[-3:])  # Last 3 messages
                            if len(text) > 4000:
                                text = text[-4000:]
                            try:
                                await update.message.reply_text(text[:4096])
                            except Exception as e:
                                logger.warning(f"Failed to send update: {e}")
                            message_buffer.clear()
                            last_update_time = current_time

                elif isinstance(item, AgentResult):
                    result = item

            # Send any remaining buffered messages
            if message_buffer:
                text = "\n".join(message_buffer[-5:])
                if len(text) > 4000:
                    text = text[-4000:]
                try:
                    await update.message.reply_text(text[:4096])
                except Exception as e:
                    logger.warning(f"Failed to send final update: {e}")

            # Send result summary
            if result:
                if result.success:
                    summary = f"✅ **Complete**\nCost: ${result.total_cost_usd:.4f}\nTurns: {result.total_turns}"
                else:
                    summary = f"❌ **Failed**: {result.error}"

                await update.message.reply_text(summary, parse_mode="Markdown")

        except asyncio.CancelledError:
            await update.message.reply_text("Task was cancelled.")
        except Exception as e:
            logger.exception("Task execution failed")
            await update.message.reply_text(f"❌ Error: {e}")

    async def runs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /runs command to list execution runs."""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        # Parse options
        show_all = context.args and context.args[0] == "all"
        initiator = None if show_all else f"telegram:{user_id}"

        # Get runs
        runs_list = self.store.list_runs(initiator=initiator, limit=10)

        if not runs_list:
            if show_all:
                await update.message.reply_text("No runs found.")
            else:
                await update.message.reply_text(
                    "No runs found.\nUse `/runs all` to see all runs.",
                    parse_mode="Markdown",
                )
            return

        # Build project lookup
        projects = self.store.list_projects()
        project_lookup = {p.id: p.name for p in projects}

        lines = ["**Recent Runs:**\n" if show_all else "**Your Runs:**\n"]

        for run in runs_list:
            emoji, _ = format_run_status(run.status)
            proj_name = project_lookup.get(run.project_id, run.project_id[:8])
            duration = format_duration(run.duration_seconds) if run.duration_seconds else "-"
            prompt_preview = run.prompt[:25] + "..." if len(run.prompt) > 25 else run.prompt

            line = f"{emoji} `{run.id[:8]}` | {proj_name}"
            line += f"\n   _{prompt_preview}_ ({duration})"
            lines.append(line)

        # Add count of active runs
        active_runs = self.store.list_active_runs()
        if active_runs:
            lines.append(f"\n**{len(active_runs)}** run(s) currently active")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    def _add_to_history(self, user_id: int, role: str, text: str) -> None:
        """Add a message to user's conversation history."""
        if user_id not in self._message_history:
            self._message_history[user_id] = []

        self._message_history[user_id].append(ChatMessage(role=role, text=text))

        # Trim to max history
        if len(self._message_history[user_id]) > self._max_history_per_user:
            self._message_history[user_id] = self._message_history[user_id][-self._max_history_per_user :]

    def _get_history(self, user_id: int) -> list[ChatMessage]:
        """Get conversation history for a user."""
        return self._message_history.get(user_id, [])

    def _extract_run_info_from_message(self, text: str) -> tuple[str | None, str | None]:
        """
        Extract run ID and project name from a bot message (completion/status messages).

        Returns:
            Tuple of (run_id, project_name) - either may be None if not found
        """
        run_id = None
        project_name = None

        # Pattern: "✅ Complete (abc12345)" or "❌ Failed (abc12345)"
        complete_match = re.search(r"[✅❌]\s*\*?\*?(?:Complete|Failed)\*?\*?\s*\(`?([a-f0-9]{8})`?\)", text)
        if complete_match:
            run_id = complete_match.group(1)

        # Pattern: "Run: abc12345" or "Run: `abc12345`"
        run_match = re.search(r"Run:\s*`?([a-f0-9]{8})`?", text)
        if run_match:
            run_id = run_match.group(1)

        # Pattern: "Task started: abc12345" or "Task `abc12345`"
        task_match = re.search(r"Task\s*(?:started:)?\s*`?([a-f0-9]{8})`?", text)
        if task_match and not run_id:
            run_id = task_match.group(1)

        # Pattern: "Project: myapp" or "Project: `myapp`"
        project_match = re.search(r"Project:\s*`?([a-zA-Z0-9_/-]+)`?", text)
        if project_match:
            project_name = project_match.group(1)

        return run_id, project_name

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle plain text messages using natural language understanding."""
        if not update.effective_user or not update.message or not update.message.text:
            return

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            return

        if not self.nl_mode_enabled:
            await update.message.reply_text(
                "Natural language mode is disabled. Use commands:\n"
                "/run <project> <prompt> - Run a task\n"
                "/help - Show all commands"
            )
            return

        # Check if user already has an active task
        if user_id in self._active_tasks and not self._active_tasks[user_id].done():
            await update.message.reply_text("You have an active task running. Use /cancel to stop it first.")
            return

        message_text = update.message.text

        # Extract reply context if user is replying to a message
        reply_context: str | None = None
        if update.message.reply_to_message and update.message.reply_to_message.text:
            reply_context = update.message.reply_to_message.text

            # Check if replying to a bot message with run info - auto-resume
            run_id, project_name_hint = self._extract_run_info_from_message(reply_context)
            if run_id:
                # Try to find the session from the run
                run = self.store.get_run_by_short_id(run_id)
                if run and run.session_id:
                    session = self.store.get_session(run.session_id)
                    if session and session.claude_session_id:
                        # Get project from the run (more reliable than parsing message)
                        project = self.store.get_project(run.project_id)
                        if project:
                            # Use project name from message hint or look it up
                            project_name = project_name_hint or project.name

                            # Check concurrency limit
                            active_runs = self.store.list_active_runs()
                            if len(active_runs) >= self._semaphore._value:
                                await update.message.reply_text(
                                    f"Max concurrent runs ({self._semaphore._value}) reached.\n"
                                    "Use /runs to see active runs or /cancel to stop one."
                                )
                                return

                            # Create run and resume
                            initiator = f"telegram:{user_id}"
                            new_run = self.store.create_run(project.id, message_text, initiator=initiator)

                            # Send initial message and capture its ID for threading
                            start_msg = await update.message.reply_text(
                                f"🔄 Resuming from run `{run_id}`\n"
                                f"Project: `{project_name}`\n"
                                f"New run: `{new_run.id[:8]}`",
                                parse_mode="Markdown",
                            )

                            task = asyncio.create_task(
                                self._execute_task_with_runner(
                                    update,
                                    new_run,
                                    project_name,
                                    force_new_session=False,
                                    thread_msg_id=start_msg.message_id,
                                )
                            )
                            self._active_tasks[new_run.id] = task
                            return

        # Get conversation history for this user
        history = self._get_history(user_id)

        # Show typing indicator
        await update.message.chat.send_action("typing")

        try:
            # Use chat agent to interpret the message with context
            response = await self.chat_agent.chat(
                message_text,
                history=history,
                reply_context=reply_context,
            )

            # Store user message in history
            self._add_to_history(user_id, "user", message_text)

            # Send the response text
            response_text = ""
            if response.text:
                # Split long messages
                response_text = response.text[:4000]
                await update.message.reply_text(response_text, parse_mode="Markdown")

            # Store assistant response in history
            if response_text:
                self._add_to_history(user_id, "assistant", response_text)

            # Check if there's a pending task to execute
            pending_task = self.chat_agent.get_pending_task()
            if pending_task:
                self.chat_agent.clear_pending_task()

                if pending_task["action"] == "run_task":
                    task = asyncio.create_task(
                        self._execute_task(
                            update,
                            pending_task["project_name"],
                            pending_task["prompt"],
                            force_new=False,
                            model=pending_task.get("model"),
                        )
                    )
                    self._active_tasks[user_id] = task

                elif pending_task["action"] == "resume_session":
                    task = asyncio.create_task(
                        self._execute_task(
                            update,
                            pending_task["project_name"],
                            pending_task["prompt"],
                            force_new=False,
                            model=pending_task.get("model"),
                        )
                    )
                    self._active_tasks[user_id] = task

        except Exception as e:
            logger.exception("Error processing message")
            await update.message.reply_text(f"Error: {e}")

    def _recover_stale_runs(self) -> None:
        """Mark stale runs from previous bot instances as failed."""
        active_runs = self.store.list_active_runs()
        telegram_runs = [r for r in active_runs if r.initiator and r.initiator.startswith("telegram:")]

        if telegram_runs:
            logger.info(f"Recovering {len(telegram_runs)} stale Telegram run(s) from previous instance")
            for run in telegram_runs:
                run.mark_failed("Bot restarted - run interrupted", exit_code=1)
                self.store.update_run(run)
                logger.info(f"Marked run {run.id[:8]} as failed (bot restart)")

    def build_application(self) -> Application:
        """Build the Telegram application with handlers."""
        self.app = Application.builder().token(self.token).build()

        # Add command handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("projects", self.projects))
        self.app.add_handler(CommandHandler("sessions", self.sessions))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("run", self.run))
        self.app.add_handler(CommandHandler("resume", self.resume))
        self.app.add_handler(CommandHandler("runs", self.runs))
        self.app.add_handler(CommandHandler("cancel", self.cancel))
        self.app.add_handler(CommandHandler("clear", self.clear))

        # Handle plain text messages
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        return self.app

    async def run_polling(self) -> None:
        """Run the bot with polling."""
        app = self.build_application()

        # Recover stale runs from previous bot instances
        self._recover_stale_runs()

        # Start background git sync
        await self.git_manager.start_background_sync()

        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)  # type: ignore

        logger.info("Bot started. Press Ctrl+C to stop.")

        # Keep running until interrupted
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            # Stop background git sync
            await self.git_manager.stop_background_sync()
            await app.updater.stop()  # type: ignore
            await app.stop()
            await app.shutdown()


def run_bot(token: str | None = None, allowed_users: list[int] | None = None) -> None:
    """
    Run the Gluon Telegram bot.

    Args:
        token: Telegram bot token. If not provided, reads from GLUON_TELEGRAM_TOKEN env var.
        allowed_users: List of authorized Telegram user IDs.
                      If not provided, reads from GLUON_TELEGRAM_USERS env var (comma-separated).
    """
    # Load .env.local for AWS Bedrock configuration
    env_path = Path(__file__).parent.parent.parent / ".env.local"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment from {env_path}")

    # Get token
    bot_token = token or os.environ.get("GLUON_TELEGRAM_TOKEN")
    if not bot_token:
        raise ValueError(
            "Telegram bot token required. Set GLUON_TELEGRAM_TOKEN environment variable or pass token parameter."
        )

    # Get allowed users
    if allowed_users is None:
        users_env = os.environ.get("GLUON_TELEGRAM_USERS", "")
        if users_env:
            allowed_users = [int(u.strip()) for u in users_env.split(",") if u.strip()]

    # Setup logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    bot = GluonBot(token=bot_token, allowed_users=allowed_users)
    asyncio.run(bot.run_polling())
