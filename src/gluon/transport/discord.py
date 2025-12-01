"""Discord transport implementation."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

try:
    import discord
    from discord.ext import commands

    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False

from gluon.bot_core import GluonBotCore
from gluon.core import ProjectNotFoundError
from gluon.transport.base import Transport, TransportContext, TransportResponse
from gluon.transport.capabilities import DISCORD_CAPS, TransportCapabilities

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DiscordTransport(Transport):
    """Discord bot transport implementation.

    Features:
    - Channel-to-project mapping (hybrid: auto-match by name, prompt to link)
    - @mention handling for commands
    - Native Discord threads for run output
    - Message editing for final status summary
    """

    def __init__(
        self,
        token: str,
        guild_id: int,
        bot_core: GluonBotCore,
        allowed_users: list[int] | None = None,
    ):
        """Initialize Discord transport.

        Args:
            token: Discord bot token
            guild_id: Discord guild (server) ID to operate in
            bot_core: Bot core instance for business logic
            allowed_users: List of allowed Discord user IDs
        """
        if not DISCORD_AVAILABLE:
            raise ImportError("discord.py is not installed. Install with: pip install 'gluon-agent[discord]'")

        self.token = token
        self.guild_id = guild_id
        self.bot_core = bot_core
        self._allowed_users: set[str] | None = None
        if allowed_users:
            self._allowed_users = {f"discord:{uid}" for uid in allowed_users}

        # Discord client setup
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self._setup_events()

        # Channel-to-project explicit mappings (loaded from DB)
        self._channel_project_map: dict[int, str] = {}

    @property
    def name(self) -> str:
        return "discord"

    @property
    def capabilities(self) -> TransportCapabilities:
        return DISCORD_CAPS

    def _setup_events(self) -> None:
        """Set up Discord event handlers."""

        @self.bot.event
        async def on_ready():
            logger.info(f"Discord bot logged in as {self.bot.user}")
            # Load channel mappings from DB
            self._load_channel_mappings()

        @self.bot.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

    def _load_channel_mappings(self) -> None:
        """Load channel-to-project mappings from database."""
        mappings = self.bot_core.store.list_channel_mappings("discord")
        self._channel_project_map = {int(m.channel_id): m.project_name for m in mappings}
        logger.info(f"Loaded {len(self._channel_project_map)} Discord channel mappings")

    def _make_context(
        self,
        message: discord.Message,
        thread_id: str | None = None,
    ) -> TransportContext:
        """Create TransportContext from Discord message."""
        # Resolve project from channel
        project_hint = self._resolve_project(message.channel)

        return TransportContext(
            transport="discord",
            user_id=f"discord:{message.author.id}",
            chat_id=str(message.channel.id),
            thread_id=thread_id,
            project_hint=project_hint,
            message_id=str(message.id),
            raw_data={"message": message},
        )

    def _resolve_project(self, channel: discord.abc.Messageable) -> str | None:
        """Resolve project name from channel context.

        Strategy (hybrid):
        1. Check explicit mapping first (from DB)
        2. Try auto-matching channel name to project name
        3. Return None if no match (caller should prompt to link)
        """
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None

        channel_id = channel.id

        # 1. Check explicit mapping
        if channel_id in self._channel_project_map:
            return self._channel_project_map[channel_id]

        # 2. Try auto-matching channel name
        channel_name = channel.name.lower().replace("-", "_").replace(" ", "_")
        try:
            project = self.bot_core.orchestrator.get_project(channel_name)
            return project.name
        except ProjectNotFoundError:
            pass

        # 3. If this is a thread, check parent channel
        if isinstance(channel, discord.Thread) and channel.parent:
            parent_id = channel.parent.id
            if parent_id in self._channel_project_map:
                return self._channel_project_map[parent_id]

            parent_name = channel.parent.name.lower().replace("-", "_")
            try:
                project = self.bot_core.orchestrator.get_project(parent_name)
                return project.name
            except ProjectNotFoundError:
                pass

        return None

    def is_authorized(self, user_id: str | int) -> bool:
        """Check if user is authorized."""
        if self._allowed_users is None:
            return True

        if isinstance(user_id, int):
            user_id = f"discord:{user_id}"

        return user_id in self._allowed_users

    async def send(
        self,
        ctx: TransportContext,
        response: TransportResponse,
    ) -> str:
        """Send a message to Discord."""
        text = self.truncate_text(response.text)

        # Get channel
        channel_id = int(response.thread_id or ctx.thread_id or ctx.chat_id)
        channel = self.bot.get_channel(channel_id)

        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                logger.warning(f"Failed to fetch channel {channel_id}: {e}")
                raise

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise ValueError(f"Cannot send to channel type: {type(channel)}")

        try:
            msg = await channel.send(text)
            return str(msg.id)
        except Exception as e:
            logger.warning(f"Failed to send message: {e}")
            raise

    async def edit(
        self,
        ctx: TransportContext,
        message_id: str,
        response: TransportResponse,
    ) -> bool:
        """Edit an existing Discord message."""
        text = self.truncate_text(response.text)

        channel_id = int(ctx.chat_id)
        channel = self.bot.get_channel(channel_id)

        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return False

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False

        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(content=text)
            return True
        except Exception as e:
            logger.warning(f"Failed to edit message: {e}")
            return False

    async def send_typing(self, ctx: TransportContext) -> None:
        """Send typing indicator."""
        channel_id = int(ctx.chat_id)
        channel = self.bot.get_channel(channel_id)

        if channel and isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                await channel.typing()
            except Exception as e:
                logger.debug(f"Failed to send typing: {e}")

    async def create_thread(
        self,
        ctx: TransportContext,
        name: str,
        message_id: str | None = None,
    ) -> str:
        """Create a Discord thread.

        If message_id is provided, creates thread attached to that message.
        Otherwise creates a standalone thread.
        """
        channel_id = int(ctx.chat_id)
        channel = self.bot.get_channel(channel_id)

        if not channel:
            channel = await self.bot.fetch_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Can only create threads in text channels")

        try:
            if message_id:
                # Create thread from message
                message = await channel.fetch_message(int(message_id))
                thread = await message.create_thread(
                    name=name[:100],  # Discord thread name limit
                    auto_archive_duration=1440,  # 24 hours
                )
            else:
                # Create standalone thread
                thread = await channel.create_thread(
                    name=name[:100],
                    auto_archive_duration=1440,
                )
            return str(thread.id)
        except Exception as e:
            logger.warning(f"Failed to create thread: {e}")
            raise

    async def start(self) -> None:
        """Start the Discord bot."""
        # Recover stale runs
        self.bot_core.recover_stale_runs("discord")

        # Start git background sync
        await self.bot_core.git_manager.start_background_sync()

        logger.info("Starting Discord transport...")
        await self.bot.start(self.token)

    async def stop(self) -> None:
        """Stop the Discord bot."""
        await self.bot_core.git_manager.stop_background_sync()
        await self.bot.close()
        logger.info("Discord transport stopped")

    async def _handle_message(self, message: discord.Message) -> None:
        """Handle incoming Discord messages."""
        # Ignore own messages
        if message.author == self.bot.user:
            return

        # Check if this is a thread reply (for session resume)
        is_thread = isinstance(message.channel, discord.Thread)
        has_mention = self.bot.user in message.mentions

        # Thread replies without @mention trigger resume
        if is_thread and not has_mention:
            await self._handle_thread_reply(message)
            return

        # Regular messages require @mention
        if not has_mention:
            return

        # Extract text (strip mention)
        text = message.content
        text = re.sub(rf"<@!?{self.bot.user.id}>", "", text).strip()

        if not text:
            return

        ctx = self._make_context(message)

        # Check authorization
        if not self.is_authorized(ctx.user_id):
            await message.reply("You are not authorized to use this bot.")
            return

        # Handle special commands
        if text.lower().startswith("link "):
            await self._handle_link_command(message, text[5:].strip())
            return

        if text.lower() == "projects":
            await self._handle_projects_command(message)
            return

        if text.lower() == "runs":
            await self._handle_runs_command(message)
            return

        if text.lower() == "status":
            await self._handle_status_command(message)
            return

        if text.lower().startswith("cancel"):
            await self._handle_cancel_command(message, text[6:].strip())
            return

        # Check for project context
        project_name = ctx.project_hint
        if not project_name:
            await self._prompt_to_link(message)
            return

        # This is a task request
        await self._handle_task_request(message, ctx, project_name, text)

    async def _prompt_to_link(self, message: discord.Message) -> None:
        """Prompt user to link channel to a project."""
        projects = self.bot_core.orchestrator.list_projects()
        if not projects:
            await message.reply(
                "No projects registered. Use the CLI to add projects:\n`gluon project add <name> <path>`"
            )
            return

        names = ", ".join(f"`{p.name}`" for p in projects[:10])
        more = f"... and {len(projects) - 10} more" if len(projects) > 10 else ""

        await message.reply(
            f"**No project linked to this channel.**\n\n"
            f"Use `@{self.bot.user.name} link <project>` to connect.\n"
            f"Available projects: {names}{more}\n\n"
            f"Or rename this channel to match a project name."
        )

    async def _handle_link_command(self, message: discord.Message, project_name: str) -> None:
        """Handle the link command to map channel to project."""
        if not project_name:
            await message.reply("Usage: `link <project_name>`")
            return

        try:
            project = self.bot_core.orchestrator.get_project(project_name)
        except ProjectNotFoundError:
            await message.reply(f"Project `{project_name}` not found.")
            return

        channel_id = message.channel.id

        # Save to DB
        self.bot_core.store.create_channel_mapping("discord", str(channel_id), project.id, project.name)

        # Update local cache
        self._channel_project_map[channel_id] = project.name

        await message.reply(f"✅ Channel linked to project `{project.name}`.\nYou can now @mention me with tasks!")

    async def _handle_projects_command(self, message: discord.Message) -> None:
        """Handle the projects command."""
        text = self.bot_core.format_projects_list()
        await message.reply(text[:2000])

    async def _handle_runs_command(self, message: discord.Message) -> None:
        """Handle the runs command."""
        initiator = f"discord:{message.author.id}"
        text = self.bot_core.format_runs_list(initiator=initiator)
        await message.reply(text[:2000])

    async def _handle_status_command(self, message: discord.Message) -> None:
        """Handle the status command."""
        text = self.bot_core.format_status()
        await message.reply(text[:2000])

    async def _handle_cancel_command(self, message: discord.Message, run_id_arg: str) -> None:
        """Handle the cancel command."""
        from gluon.models import RunStatus

        user_id = f"discord:{message.author.id}"

        if run_id_arg:
            run = self.bot_core.store.get_run_by_short_id(run_id_arg) or self.bot_core.store.get_run(run_id_arg)
            if not run:
                await message.reply(f"Run not found: {run_id_arg}")
                return
            if not run.is_active:
                await message.reply(f"Run `{run.id[:8]}` is not active (status: {run.status.value})")
                return
        else:
            user_runs = self.bot_core.store.list_runs(
                initiator=user_id,
                statuses=[RunStatus.PENDING, RunStatus.RUNNING],
                limit=1,
            )
            if not user_runs:
                await message.reply("No active runs to cancel.")
                return
            run = user_runs[0]

        await self.bot_core.cancel_task(run.id)
        run.mark_cancelled()
        self.bot_core.store.update_run(run)
        await message.reply(f"✅ Cancelled run `{run.id[:8]}`")

    async def _handle_thread_reply(self, message: discord.Message) -> None:
        """Handle a reply in a task thread to resume the session."""
        thread_id = str(message.channel.id)
        prompt = message.content.strip()

        if not prompt:
            return

        # Check authorization
        user_id = f"discord:{message.author.id}"
        if not self.is_authorized(user_id):
            await message.reply("You are not authorized to use this bot.")
            return

        # Look up the run by thread_id
        run = self.bot_core.store.get_run_by_thread_id(thread_id)
        if not run or not run.session_id:
            # No run found or no session to resume - treat as new task if project linked
            ctx = self._make_context(message, thread_id=thread_id)
            project_name = ctx.project_hint
            if project_name:
                await self._handle_task_request(message, ctx, project_name, prompt)
            return

        # Get project for the run
        project = self.bot_core.store.get_project(run.project_id)
        if not project:
            await message.reply("Project not found for this session.")
            return

        # Check capacity
        if self.bot_core.is_at_capacity():
            await message.reply(
                f"Max concurrent runs ({self.bot_core._semaphore._value}) reached.\n"
                "Use `runs` to see active runs or `cancel` to stop one."
            )
            return

        # Create new run for the resume
        new_run = self.bot_core.store.create_run(
            project.id,
            prompt,
            initiator=user_id,
        )
        new_run.thread_id = thread_id
        self.bot_core.store.update_run(new_run)

        # Send acknowledgment
        await message.reply(f"🔄 **Resuming session** on `{project.name}`\nRun: `{new_run.id[:8]}`")

        ctx = self._make_context(message, thread_id=thread_id)

        async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
            return await self.send(ctx, response)

        # Execute resume task
        async def execute_resume():
            try:
                await self.bot_core.execute_task(
                    ctx=ctx,
                    run=new_run,
                    project_name=project.name,
                    send_callback=send_callback,
                    force_new_session=False,  # Resume existing session
                    session_id=run.session_id,  # Use previous session
                )

                # Update run status
                run_updated = self.bot_core.store.get_run(new_run.id)
                if run_updated:
                    emoji = "✅" if run_updated.status.value == "completed" else "❌"
                    await message.channel.send(f"{emoji} Resume complete - `{new_run.id[:8]}`")
            except Exception:
                logger.exception("Resume task failed")

        task = asyncio.create_task(execute_resume())
        self.bot_core.register_task(new_run.id, task)

    async def _handle_task_request(
        self,
        message: discord.Message,
        ctx: TransportContext,
        project_name: str,
        prompt: str,
    ) -> None:
        """Handle a task execution request."""
        user_id = ctx.user_id

        if self.bot_core.is_at_capacity():
            await message.reply(
                f"Max concurrent runs ({self.bot_core._semaphore._value}) reached.\n"
                "Use `runs` to see active runs or `cancel` to stop one."
            )
            return

        # Create run record
        run = self.bot_core.store.create_run(
            self.bot_core.orchestrator.get_project(project_name).id,
            prompt,
            initiator=user_id,
        )

        # Send initial message (will be edited with final summary)
        initial_msg = await message.reply(
            f"🚀 **Starting task** on `{project_name}`\nRun: `{run.id[:8]}`\nStatus: Running..."
        )

        # Create thread attached to initial message
        thread_name = f"Run {run.id[:8]}: {prompt[:40]}..."
        try:
            thread_id = await self.create_thread(ctx, thread_name, str(initial_msg.id))
            # Save thread_id to run for resume detection
            if thread_id:
                run.thread_id = thread_id
                self.bot_core.store.update_run(run)
        except Exception as e:
            logger.warning(f"Failed to create thread: {e}")
            thread_id = None

        # Update context with thread
        task_ctx = TransportContext(
            transport="discord",
            user_id=user_id,
            chat_id=ctx.chat_id,
            thread_id=thread_id,
            project_hint=project_name,
            message_id=str(initial_msg.id),
        )

        async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
            return await self.send(ctx, response)

        async def create_thread_callback(ctx: TransportContext, name: str, msg_id: str | None) -> str | None:
            # Thread already created above
            return thread_id

        # Custom execution with edit on completion
        async def execute_with_edit():
            try:
                await self.bot_core.execute_task(
                    ctx=task_ctx,
                    run=run,
                    project_name=project_name,
                    send_callback=send_callback,
                    force_new_session=True,
                    initial_msg_id=str(initial_msg.id),
                    create_thread_callback=create_thread_callback,
                )

                # Edit original message with final summary
                run_updated = self.bot_core.store.get_run(run.id)
                if run_updated:
                    emoji = "✅" if run_updated.status.value == "completed" else "❌"
                    await self.edit(
                        task_ctx,
                        str(initial_msg.id),
                        TransportResponse(
                            text=(
                                f"{emoji} **{project_name}** - `{run.id[:8]}`\n"
                                f"_{prompt[:60]}{'...' if len(prompt) > 60 else ''}_"
                            )
                        ),
                    )
            except Exception:
                logger.exception("Task execution failed")

        # Run task in background
        task = asyncio.create_task(execute_with_edit())
        self.bot_core.register_task(run.id, task)


async def run_discord_transport(
    token: str,
    guild_id: int,
    bot_core: GluonBotCore,
    allowed_users: list[int] | None = None,
) -> None:
    """Run the Discord transport until interrupted.

    Args:
        token: Discord bot token
        guild_id: Discord guild (server) ID
        bot_core: Bot core instance
        allowed_users: List of allowed Discord user IDs
    """
    transport = DiscordTransport(token, guild_id, bot_core, allowed_users)
    await transport.start()
