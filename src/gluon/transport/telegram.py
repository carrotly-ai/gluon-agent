"""Telegram transport implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from gluon.bot_core import GluonBotCore
from gluon.transport.base import Transport, TransportContext, TransportResponse
from gluon.transport.capabilities import TELEGRAM_CAPS, TransportCapabilities

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TelegramTransport(Transport):
    """Telegram bot transport implementation.

    Wraps python-telegram-bot library to provide Transport interface.
    """

    def __init__(
        self,
        token: str,
        bot_core: GluonBotCore,
        allowed_users: list[int] | None = None,
    ):
        """Initialize Telegram transport.

        Args:
            token: Telegram bot token from @BotFather
            bot_core: Bot core instance for business logic
            allowed_users: List of allowed Telegram user IDs
        """
        self.token = token
        self.bot_core = bot_core
        self._allowed_users: set[str] | None = None
        if allowed_users:
            self._allowed_users = {f"telegram:{uid}" for uid in allowed_users}

        self.app: Application | None = None

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def capabilities(self) -> TransportCapabilities:
        return TELEGRAM_CAPS

    def _make_context(
        self,
        update: Update,
        message_id: int | None = None,
    ) -> TransportContext:
        """Create TransportContext from Telegram Update."""
        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id if update.effective_chat else 0

        return TransportContext(
            transport="telegram",
            user_id=f"telegram:{user_id}",
            chat_id=str(chat_id),
            message_id=str(message_id) if message_id else None,
            raw_data={"update": update},
        )

    def is_authorized(self, user_id: str | int) -> bool:
        """Check if user is authorized."""
        if self._allowed_users is None:
            return True

        # Handle both formats
        if isinstance(user_id, int):
            user_id = f"telegram:{user_id}"

        return user_id in self._allowed_users

    async def send(
        self,
        ctx: TransportContext,
        response: TransportResponse,
    ) -> str:
        """Send a message to Telegram."""
        if not self.app:
            raise RuntimeError("Transport not started")

        chat_id = int(ctx.chat_id)
        text = self.truncate_text(response.text)

        # Determine parse mode
        parse_mode = None
        if response.parse_mode == "markdown":
            parse_mode = "Markdown"
        elif response.parse_mode == "html":
            parse_mode = "HTML"

        # Threading via reply_to_message_id
        reply_to = None
        if response.reply_to_id:
            reply_to = int(response.reply_to_id)
        elif response.thread_id:
            # In Telegram, thread_id is the message to reply to
            reply_to = int(response.thread_id)

        try:
            msg = await self.app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to,
            )
            return str(msg.message_id)
        except Exception as e:
            logger.warning(f"Failed to send message: {e}")
            # Try without parse mode if markdown fails
            if parse_mode:
                try:
                    msg = await self.app.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_to_message_id=reply_to,
                    )
                    return str(msg.message_id)
                except Exception:
                    pass
            raise

    async def edit(
        self,
        ctx: TransportContext,
        message_id: str,
        response: TransportResponse,
    ) -> bool:
        """Edit an existing Telegram message."""
        if not self.app:
            return False

        chat_id = int(ctx.chat_id)
        text = self.truncate_text(response.text)

        parse_mode = None
        if response.parse_mode == "markdown":
            parse_mode = "Markdown"
        elif response.parse_mode == "html":
            parse_mode = "HTML"

        try:
            await self.app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to edit message: {e}")
            return False

    async def send_typing(self, ctx: TransportContext) -> None:
        """Send typing indicator."""
        if not self.app:
            return

        try:
            await self.app.bot.send_chat_action(
                chat_id=int(ctx.chat_id),
                action="typing",
            )
        except Exception as e:
            logger.debug(f"Failed to send typing: {e}")

    async def create_thread(
        self,
        ctx: TransportContext,
        name: str,
        message_id: str | None = None,
    ) -> str:
        """Create a 'thread' in Telegram.

        Telegram doesn't have native threads, so we use the message_id
        as the thread anchor for reply chains.
        """
        # In Telegram, we just return the message_id to reply to
        return message_id or ctx.message_id or ""

    async def start(self) -> None:
        """Start the Telegram bot."""
        self.app = Application.builder().token(self.token).build()
        self._register_handlers()

        # Recover stale runs
        self.bot_core.recover_stale_runs("telegram")

        # Start git background sync
        await self.bot_core.git_manager.start_background_sync()

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        logger.info("Telegram transport started")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self.app:
            # Stop git sync
            await self.bot_core.git_manager.stop_background_sync()

            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            logger.info("Telegram transport stopped")

    def _register_handlers(self) -> None:
        """Register Telegram command and message handlers."""
        if not self.app:
            return

        self.app.add_handler(CommandHandler("start", self._handle_start))
        self.app.add_handler(CommandHandler("help", self._handle_start))
        self.app.add_handler(CommandHandler("projects", self._handle_projects))
        self.app.add_handler(CommandHandler("sessions", self._handle_sessions))
        self.app.add_handler(CommandHandler("status", self._handle_status))
        self.app.add_handler(CommandHandler("run", self._handle_run))
        self.app.add_handler(CommandHandler("resume", self._handle_resume))
        self.app.add_handler(CommandHandler("runs", self._handle_runs))
        self.app.add_handler(CommandHandler("cancel", self._handle_cancel))
        self.app.add_handler(CommandHandler("clear", self._handle_clear))

        # Natural language handler
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    # ========== Command Handlers ==========

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help commands."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        if not self.is_authorized(ctx.user_id):
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
            "/projects [filter] - List projects (filter by name)\n"
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

    async def _handle_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /projects command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        if not self.is_authorized(ctx.user_id):
            await update.message.reply_text("Not authorized.")
            return

        filter_term = context.args[0] if context.args else None
        text = self.bot_core.format_projects_list(filter_term)

        if len(text) > 4000:
            text = text[:4000] + "\n..."

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_sessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /sessions command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        if not self.is_authorized(ctx.user_id):
            await update.message.reply_text("Not authorized.")
            return

        from gluon.core import ProjectNotFoundError

        project_name = context.args[0] if context.args else None

        try:
            session_list = self.bot_core.orchestrator.list_sessions(project_name)
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        if not session_list:
            await update.message.reply_text("No sessions found.")
            return

        lines = [f"**Sessions{f' for {project_name}' if project_name else ''}:**\n"]

        project_lookup: dict[str, str] = {}
        if not project_name:
            for p in self.bot_core.orchestrator.list_projects():
                project_lookup[p.id] = p.name

        for s in session_list[:10]:
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

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        if not self.is_authorized(ctx.user_id):
            await update.message.reply_text("Not authorized.")
            return

        text = self.bot_core.format_status()
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_run(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /run command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        user_id = ctx.user_id

        if not self.is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /run <project> <prompt>\nExample: /run myapp Fix the login bug")
            return

        project_name = context.args[0]
        prompt = " ".join(context.args[1:])

        from gluon.core import ProjectNotFoundError

        try:
            project = self.bot_core.orchestrator.get_project(project_name)
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        if self.bot_core.is_at_capacity():
            await update.message.reply_text(
                f"Max concurrent runs ({self.bot_core._semaphore._value}) reached.\n"
                "Use /runs to see active runs or /cancel to stop one."
            )
            return

        # Create run record
        run = self.bot_core.store.create_run(project.id, prompt, initiator=user_id)

        # Send initial message
        start_msg = await update.message.reply_text(
            f"🚀 Task started: `{run.id[:8]}`\n"
            f"Project: `{project_name}`\n"
            f"Prompt: _{prompt[:80]}{'...' if len(prompt) > 80 else ''}_\n\n"
            f"Use /runs to check status",
            parse_mode="Markdown",
        )

        # Create send callback
        async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
            return await self.send(ctx, response)

        # Run task in background
        task = asyncio.create_task(
            self.bot_core.execute_task(
                ctx=self._make_context(update, start_msg.message_id),
                run=run,
                project_name=project_name,
                send_callback=send_callback,
                force_new_session=True,
                initial_msg_id=str(start_msg.message_id),
            )
        )
        self.bot_core.register_task(run.id, task)

    async def _handle_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /resume command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        user_id = ctx.user_id

        if not self.is_authorized(user_id):
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

        from gluon.core import ProjectNotFoundError

        project_name = context.args[0]

        try:
            project = self.bot_core.orchestrator.get_project(project_name)
        except ProjectNotFoundError as e:
            await update.message.reply_text(f"Error: {e}")
            return

        # Check for session/run ID
        session = None
        id_arg = None
        prompt_start_idx = 1

        if len(context.args) > 1:
            potential_id = context.args[1]
            if 4 <= len(potential_id) <= 36 and all(c in "0123456789abcdef-" for c in potential_id.lower()):
                id_arg = potential_id
                prompt_start_idx = 2

                session = self.bot_core.store.get_session_by_short_id(potential_id, project.id)
                if not session:
                    run_lookup = self.bot_core.store.get_run_by_short_id(potential_id)
                    if run_lookup and run_lookup.session_id:
                        session = self.bot_core.store.get_session(run_lookup.session_id)
                        if session and session.project_id != project.id:
                            session = None

        if not session and not id_arg:
            session = self.bot_core.orchestrator.get_resumable_session(project)

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

        if self.bot_core.is_at_capacity():
            await update.message.reply_text(
                f"Max concurrent runs ({self.bot_core._semaphore._value}) reached.\n"
                "Use /runs to see active runs or /cancel to stop one."
            )
            return

        run = self.bot_core.store.create_run(project.id, prompt, initiator=user_id)

        start_msg = await update.message.reply_text(
            f"🔄 Resuming session: `{session.id[:8]}`\n"
            f"Project: `{project_name}`\n"
            f"Run: `{run.id[:8]}`\n"
            f"Use /runs to check status",
            parse_mode="Markdown",
        )

        async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
            return await self.send(ctx, response)

        task = asyncio.create_task(
            self.bot_core.execute_task(
                ctx=self._make_context(update, start_msg.message_id),
                run=run,
                project_name=project_name,
                send_callback=send_callback,
                force_new_session=False,
                initial_msg_id=str(start_msg.message_id),
            )
        )
        self.bot_core.register_task(run.id, task)

    async def _handle_runs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /runs command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        if not self.is_authorized(ctx.user_id):
            await update.message.reply_text("Not authorized.")
            return

        show_all = context.args and context.args[0] == "all"
        initiator = None if show_all else ctx.user_id

        text = self.bot_core.format_runs_list(initiator=initiator)
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        user_id = ctx.user_id

        if not self.is_authorized(user_id):
            await update.message.reply_text("Not authorized.")
            return

        from gluon.models import RunStatus

        run_id_arg = context.args[0] if context.args else None

        if run_id_arg:
            run = self.bot_core.store.get_run_by_short_id(run_id_arg) or self.bot_core.store.get_run(run_id_arg)
            if not run:
                await update.message.reply_text(f"Run not found: {run_id_arg}")
                return

            if not run.is_active:
                await update.message.reply_text(f"Run `{run.id[:8]}` is not active (status: {run.status.value})")
                return
        else:
            user_runs = self.bot_core.store.list_runs(
                initiator=user_id,
                statuses=[RunStatus.PENDING, RunStatus.RUNNING],
                limit=1,
            )

            if not user_runs:
                await update.message.reply_text(
                    "No active runs to cancel.\nUse `/cancel <run_id>` to cancel a specific run."
                )
                return

            run = user_runs[0]

        # Cancel the task
        await self.bot_core.cancel_task(run.id)
        run.mark_cancelled()
        self.bot_core.store.update_run(run)
        await update.message.reply_text(f"✅ Cancelled run `{run.id[:8]}`")

    async def _handle_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /clear command."""
        if not update.effective_user or not update.message:
            return

        ctx = self._make_context(update)
        if not self.is_authorized(ctx.user_id):
            await update.message.reply_text("Not authorized.")
            return

        self.bot_core.clear_history(ctx.user_id)
        await update.message.reply_text("Chat history cleared.")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle natural language messages."""
        if not update.effective_user or not update.message or not update.message.text:
            return

        ctx = self._make_context(update)

        if not self.is_authorized(ctx.user_id):
            return

        if not self.bot_core.nl_mode_enabled:
            await update.message.reply_text(
                "Natural language mode is disabled. Use commands:\n"
                "/run <project> <prompt> - Run a task\n"
                "/help - Show all commands"
            )
            return

        message_text = update.message.text

        # Extract reply context
        reply_context: str | None = None
        if update.message.reply_to_message and update.message.reply_to_message.text:
            reply_context = update.message.reply_to_message.text

            # Check for auto-resume from reply
            run_id, project_name_hint = self.bot_core.extract_run_info_from_message(reply_context)
            if run_id:
                run = self.bot_core.store.get_run_by_short_id(run_id)
                if run and run.session_id:
                    session = self.bot_core.store.get_session(run.session_id)
                    if session and session.claude_session_id:
                        project = self.bot_core.store.get_project(run.project_id)
                        if project:
                            project_name = project_name_hint or project.name

                            if self.bot_core.is_at_capacity():
                                await update.message.reply_text(
                                    "Max concurrent runs reached.\n"
                                    "Use /runs to see active runs or /cancel to stop one."
                                )
                                return

                            new_run = self.bot_core.store.create_run(project.id, message_text, initiator=ctx.user_id)

                            start_msg = await update.message.reply_text(
                                f"🔄 Resuming from run `{run_id}`\n"
                                f"Project: `{project_name}`\n"
                                f"New run: `{new_run.id[:8]}`",
                                parse_mode="Markdown",
                            )

                            async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
                                return await self.send(ctx, response)

                            task = asyncio.create_task(
                                self.bot_core.execute_task(
                                    ctx=self._make_context(update, start_msg.message_id),
                                    run=new_run,
                                    project_name=project_name,
                                    send_callback=send_callback,
                                    force_new_session=False,
                                    initial_msg_id=str(start_msg.message_id),
                                )
                            )
                            self.bot_core.register_task(new_run.id, task)
                            return

        # Show typing indicator
        await self.send_typing(ctx)

        # Send callback
        async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
            return await self.send(ctx, response)

        # Process through chat agent
        pending_task = await self.bot_core.process_natural_language(
            ctx,
            message_text,
            send_callback,
            reply_context,
        )

        # Handle pending task if any
        if pending_task:
            self.bot_core.chat_agent.clear_pending_task()

            if pending_task["action"] in ("run_task", "resume_session"):
                project_name = pending_task["project_name"]
                prompt = pending_task["prompt"]
                model = pending_task.get("model")

                from gluon.core import ProjectNotFoundError

                try:
                    project = self.bot_core.orchestrator.get_project(project_name)
                except ProjectNotFoundError:
                    return

                run = self.bot_core.store.create_run(project.id, prompt, initiator=ctx.user_id)

                task = asyncio.create_task(
                    self.bot_core.execute_task(
                        ctx=ctx,
                        run=run,
                        project_name=project_name,
                        send_callback=send_callback,
                        model=model,
                        force_new_session=(pending_task["action"] == "run_task"),
                    )
                )
                self.bot_core.register_task(run.id, task)


async def run_telegram_transport(
    token: str,
    bot_core: GluonBotCore,
    allowed_users: list[int] | None = None,
) -> None:
    """Run the Telegram transport until interrupted.

    Args:
        token: Telegram bot token
        bot_core: Bot core instance
        allowed_users: List of allowed Telegram user IDs
    """
    transport = TelegramTransport(token, bot_core, allowed_users)
    await transport.start()

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await transport.stop()
