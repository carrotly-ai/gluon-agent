"""Telegram bot interface for Gluon Agent."""

import asyncio
import logging
import os
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
from gluon.chat_agent import GluonChatAgent
from gluon.core import Orchestrator, ProjectNotFoundError
from gluon.models_config import ModelTier

logger = logging.getLogger(__name__)


class GluonBot:
    """Telegram bot for interacting with Gluon Agent."""

    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
    ):
        """
        Initialize the Gluon Telegram bot.

        Args:
            token: Telegram bot token from @BotFather
            allowed_users: List of Telegram user IDs allowed to use the bot.
                          If None, all users are allowed (not recommended for production).
        """
        self.token = token
        self.allowed_users = allowed_users
        self.orchestrator = Orchestrator()
        self.chat_agent = GluonChatAgent(self.orchestrator)
        self.app: Application | None = None
        # Track active tasks per user
        self._active_tasks: dict[int, asyncio.Task[Any]] = {}
        # Enable/disable natural language mode
        self.nl_mode_enabled = True

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
            "/status - Show overall status\n"
            "/cancel - Cancel current task\n"
            "/help - Show this message",
            parse_mode="Markdown",
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        if not update.message:
            return
        await self.start(update, context)

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
            safe_path = p.path.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
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

        # Check if user already has an active task
        if user_id in self._active_tasks and not self._active_tasks[user_id].done():
            await update.message.reply_text("You have an active task running. Use /cancel to stop it first.")
            return

        try:
            self.orchestrator.get_project(project_name)
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        await update.message.reply_text(
            f"Starting task on `{project_name}`...\nPrompt: _{prompt[:100]}{'...' if len(prompt) > 100 else ''}_",
            parse_mode="Markdown",
        )

        # Run the task in background
        task = asyncio.create_task(self._execute_task(update, project_name, prompt, force_new=False))
        self._active_tasks[user_id] = task

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
                "Usage: /resume <project> [prompt]\nExample: /resume myapp Also add logging"
            )
            return

        project_name = context.args[0]
        prompt = " ".join(context.args[1:]) if len(context.args) > 1 else None

        # Check if user already has an active task
        if user_id in self._active_tasks and not self._active_tasks[user_id].done():
            await update.message.reply_text("You have an active task running. Use /cancel to stop it first.")
            return

        try:
            project = self.orchestrator.get_project(project_name)
            session = self.orchestrator.get_resumable_session(project)
            if not session or not session.claude_session_id:
                await update.message.reply_text(
                    f"No resumable session for `{project_name}`.\nUse /run to start a new session.",
                    parse_mode="Markdown",
                )
                return
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        await update.message.reply_text(f"Resuming session on `{project_name}`...", parse_mode="Markdown")

        # Run the task in background
        task = asyncio.create_task(
            self._execute_task(update, project_name, prompt or "Continue from where you left off.", force_new=False)
        )
        self._active_tasks[user_id] = task

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel command."""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        if user_id in self._active_tasks and not self._active_tasks[user_id].done():
            self._active_tasks[user_id].cancel()
            del self._active_tasks[user_id]
            await update.message.reply_text("Task cancelled.")
        else:
            await update.message.reply_text("No active task to cancel.")

    async def _execute_task(
        self,
        update: Update,
        project_name: str,
        prompt: str,
        force_new: bool,
        model: ModelTier | str | None = None,
    ) -> None:
        """Execute a Gluon task and stream updates to Telegram."""
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

                    elif item.type == "tool_use":
                        # Notify about tool usage
                        tool_name = item.metadata.get("tool", "unknown") if item.metadata else "unknown"
                        try:
                            await update.message.reply_text(f"🔧 Using: {tool_name}")
                        except Exception:
                            pass

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

        # Show typing indicator
        await update.message.chat.send_action("typing")

        try:
            # Use chat agent to interpret the message
            response = await self.chat_agent.chat(message_text)

            # Send the response text
            if response.text:
                # Split long messages
                text = response.text[:4000]
                await update.message.reply_text(text, parse_mode="Markdown")

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
        self.app.add_handler(CommandHandler("cancel", self.cancel))

        # Handle plain text messages
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        return self.app

    async def run_polling(self) -> None:
        """Run the bot with polling."""
        app = self.build_application()
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
