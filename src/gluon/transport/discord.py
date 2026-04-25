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
    from gluon.models import PendingApproval
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


# Button custom_id format: "approval:<decision>:<uuid>". Discord's custom_id
# limit is 100 chars, which comfortably fits a decision keyword + UUID.
_APPROVAL_CUSTOM_ID_PREFIX = "approval"


def _truncate(text: str, limit: int = 300) -> str:
    """Trim text with an ellipsis for embed fields."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_approval_embed(approval: PendingApproval):
    """Format a PendingApproval as a rich Discord embed.

    Returns a discord.Embed ready to send. Raises if discord.py isn't installed.
    """
    if not DISCORD_AVAILABLE:
        raise RuntimeError("discord.py not installed")

    embed = discord.Embed(
        title="🔒 Approval needed",
        description=_truncate(approval.classification_reason, 500),
        color=0xFFA500,  # orange — pending action
    )
    embed.add_field(name="Run", value=f"`{approval.run_id[:8]}`", inline=True)
    embed.add_field(name="Tool", value=f"`{approval.tool_name}`", inline=True)
    embed.add_field(name="Approval", value=f"`{approval.id[:8]}`", inline=True)

    # Render tool input — prioritize Bash commands
    if isinstance(approval.tool_input, dict):
        command = approval.tool_input.get("command")
        if command:
            embed.add_field(
                name="Command",
                value=f"```\n{_truncate(str(command), 1000)}\n```",
                inline=False,
            )
        else:
            # Show key summary for Write/Edit/etc
            pieces = []
            for key in ("file_path", "path", "url"):
                if key in approval.tool_input:
                    pieces.append(f"**{key}**: `{_truncate(str(approval.tool_input[key]), 100)}`")
            if pieces:
                embed.add_field(name="Input", value="\n".join(pieces), inline=False)

    embed.set_footer(text="Reply with a button below to approve or deny.")
    return embed


def _build_approval_view(
    approval_id: str,
    store: GluonStore,
    is_authorized: callable,
):
    """Build a persistent ApprovalView for this approval.

    `is_authorized(user_id: str) -> bool` is the transport's auth check.
    The view has timeout=None so buttons survive bot restarts, provided the
    bot re-registers the view on startup with `bot.add_view(...)`.
    """
    if not DISCORD_AVAILABLE:
        raise RuntimeError("discord.py not installed")

    from gluon.models import ApprovalStatus

    class ApprovalView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)

        @discord.ui.button(
            label="✅ Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"{_APPROVAL_CUSTOM_ID_PREFIX}:grant:{approval_id}",
        )
        async def approve(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button,
        ) -> None:
            await _handle_approval_decision(
                interaction=interaction,
                store=store,
                approval_id=approval_id,
                status=ApprovalStatus.GRANTED,
                is_authorized=is_authorized,
            )

        @discord.ui.button(
            label="❌ Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"{_APPROVAL_CUSTOM_ID_PREFIX}:deny:{approval_id}",
        )
        async def deny(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button,
        ) -> None:
            await _handle_approval_decision(
                interaction=interaction,
                store=store,
                approval_id=approval_id,
                status=ApprovalStatus.DENIED,
                is_authorized=is_authorized,
            )

    return ApprovalView()


async def _handle_approval_decision(
    *,
    interaction,
    store: GluonStore,
    approval_id: str,
    status,
    is_authorized: callable,
) -> None:
    """Shared callback logic for Approve/Deny button presses."""
    from gluon.models import ApprovalStatus

    user_id = f"discord:{interaction.user.id}"

    if not is_authorized(user_id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    approval = store.get_approval(approval_id)
    if approval is None:
        await interaction.response.send_message(
            f"Approval {approval_id[:8]} not found.",
            ephemeral=True,
        )
        return

    if approval.status != ApprovalStatus.PENDING:
        # Already decided — just update UI
        await interaction.response.send_message(
            f"Already {approval.status.value} by {approval.decided_by or 'unknown'}.",
            ephemeral=True,
        )
        await _edit_approval_message(interaction, approval, decided_by_note=True)
        return

    discord_user_id_int = int(interaction.user.id)
    decided_by = f"discord:{discord_user_id_int}"

    # D5 Phase 4 — if the Discord user is bound to a Gluon User, record
    # the attribution. Falls back to None for unlinked chats, mirroring
    # how the web layer treats the SYSTEM_USER.
    decided_by_user_id: str | None = None
    decision_attribution = interaction.user.display_name
    linked = store.get_user_by_discord_id(discord_user_id_int)
    if linked is not None:
        decided_by_user_id = linked.id
        decision_attribution = f"{interaction.user.display_name} (@{linked.username})"

    updated = store.decide_approval(
        approval_id,
        status=status,
        decided_by=decided_by,
        decided_by_user_id=decided_by_user_id,
        decision_reason=f"Via Discord by {decision_attribution}",
    )
    if updated is None:
        await interaction.response.send_message("Approval vanished after click.", ephemeral=True)
        return

    verb = "Approved" if status == ApprovalStatus.GRANTED else "Denied"
    await interaction.response.send_message(f"{verb}.", ephemeral=True)
    await _edit_approval_message(interaction, updated, decided_by_note=False)


async def _edit_approval_message(interaction, approval, *, decided_by_note: bool) -> None:
    """Edit the original approval message to show the decision + remove buttons."""
    from gluon.models import ApprovalStatus

    try:
        embed = format_approval_embed(approval)
        if approval.status == ApprovalStatus.GRANTED:
            embed.color = 0x2ECC71  # green
            embed.title = "✅ Approved"
        elif approval.status == ApprovalStatus.DENIED:
            embed.color = 0xE74C3C  # red
            embed.title = "❌ Denied"
        elif approval.status == ApprovalStatus.EXPIRED:
            embed.color = 0x95A5A6  # grey
            embed.title = "⏱️ Expired"

        footer_text = (
            f"Decided by {approval.decided_by} at "
            f"{approval.decided_at.isoformat() if approval.decided_at else '(unknown time)'}"
        )
        embed.set_footer(text=footer_text)
        await interaction.message.edit(embed=embed, view=None)
    except Exception:
        logger.debug("Could not edit approval message", exc_info=True)


# Model aliases for convenience
MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4.5",
    "sonnet": "claude-sonnet-4.6",
    "haiku": "claude-haiku-4.5",
    "claude-opus-4.5": "claude-opus-4.5",
    "claude-sonnet-4.6": "claude-sonnet-4.6",
    "claude-sonnet-4.5": "claude-sonnet-4.6",
    "claude-haiku-4.5": "claude-haiku-4.5",
}

DEFAULT_MODEL = "claude-sonnet-4.6"


def parse_model_flag(text: str) -> tuple[str, str | None]:
    """Parse --model flag from prompt text.

    Args:
        text: The prompt text potentially containing --model flag

    Returns:
        Tuple of (cleaned_prompt, model_name or None)

    Examples:
        "fix the bug --model opus" -> ("fix the bug", "claude-opus-4.5")
        "fix the bug" -> ("fix the bug", None)
    """
    # Match --model or -m followed by model name
    pattern = r"\s*(?:--model|-m)\s+(\S+)\s*"
    match = re.search(pattern, text, re.IGNORECASE)

    if not match:
        return text.strip(), None

    model_arg = match.group(1).lower()
    model = MODEL_ALIASES.get(model_arg)

    if not model:
        # Unknown model, return None and keep the flag in text
        return text.strip(), None

    # Remove the flag from the prompt
    cleaned = re.sub(pattern, " ", text, flags=re.IGNORECASE).strip()
    return cleaned, model


def parse_project_specifier(text: str) -> tuple[str, str | None]:
    """Parse project specifier from message text.

    Supports formats:
        - project:myproject
        - p:myproject
        - --project myproject
        - -p myproject

    Args:
        text: The message text potentially containing project specifier

    Returns:
        Tuple of (cleaned_text, project_name or None)

    Examples:
        "project:myapp fix the bug" -> ("fix the bug", "myapp")
        "p:myapp fix the bug" -> ("fix the bug", "myapp")
        "fix the bug --project myapp" -> ("fix the bug", "myapp")
        "fix the bug" -> ("fix the bug", None)
    """
    # Pattern 1: project:name or p:name at start
    prefix_pattern = r"^(?:project|p):(\S+)\s+"
    prefix_match = re.match(prefix_pattern, text, re.IGNORECASE)
    if prefix_match:
        project = prefix_match.group(1)
        cleaned = text[prefix_match.end() :].strip()
        return cleaned, project

    # Pattern 2: --project name or -p name anywhere
    flag_pattern = r"\s*(?:--project|-p)\s+(\S+)\s*"
    flag_match = re.search(flag_pattern, text, re.IGNORECASE)
    if flag_match:
        project = flag_match.group(1)
        cleaned = re.sub(flag_pattern, " ", text, flags=re.IGNORECASE).strip()
        return cleaned, project

    return text.strip(), None


def parse_agent_flag(text: str) -> tuple[str, str | None]:
    """Parse --agent or -a flag from prompt text.

    Mirrors `parse_model_flag` but for agent name/ID. The flag value is not
    normalized (agent names are opaque strings resolved by the orchestrator).

    Args:
        text: The prompt text potentially containing --agent flag

    Returns:
        Tuple of (cleaned_prompt, agent_name_or_id or None)

    Examples:
        "fix the bug --agent researcher" -> ("fix the bug", "researcher")
        "fix the bug -a abc12345" -> ("fix the bug", "abc12345")
        "fix the bug" -> ("fix the bug", None)
    """
    pattern = r"\s*(?:--agent|-a)\s+(\S+)\s*"
    match = re.search(pattern, text, re.IGNORECASE)

    if not match:
        return text.strip(), None

    agent = match.group(1)
    cleaned = re.sub(pattern, " ", text, flags=re.IGNORECASE).strip()
    return cleaned, agent


def parse_channel_topic(topic: str | None) -> dict[str, str | None]:
    """Parse channel topic for configuration flags.

    Channel topics can contain configuration like:
        --project myproject --model haiku --agent researcher

    Args:
        topic: The channel topic string

    Returns:
        Dict with 'project', 'model', and 'agent' keys (values may be None)

    Examples:
        "--project foo --model opus" -> {"project": "foo", "model": "claude-opus-4.5", "agent": None}
        "--project foo --agent researcher" -> {"project": "foo", "model": None, "agent": "researcher"}
        "Regular topic" -> {"project": None, "model": None, "agent": None}
    """
    result: dict[str, str | None] = {"project": None, "model": None, "agent": None}

    if not topic:
        return result

    # Parse --project or -p flag
    project_pattern = r"(?:--project|-p)\s+(\S+)"
    project_match = re.search(project_pattern, topic, re.IGNORECASE)
    if project_match:
        result["project"] = project_match.group(1)

    # Parse --model or -m flag
    model_pattern = r"(?:--model|-m)\s+(\S+)"
    model_match = re.search(model_pattern, topic, re.IGNORECASE)
    if model_match:
        model_arg = model_match.group(1).lower()
        result["model"] = MODEL_ALIASES.get(model_arg)

    # Parse --agent or -a flag (agent names pass through verbatim)
    agent_pattern = r"(?:--agent|-a)\s+(\S+)"
    agent_match = re.search(agent_pattern, topic, re.IGNORECASE)
    if agent_match:
        result["agent"] = agent_match.group(1)

    return result


class DiscordTransport(Transport):
    """Discord bot transport implementation.

    Features:
    - Channel-to-project mapping (hybrid: auto-match by name, prompt to link)
    - @mention handling for commands and tasks
    - Reply to completion message to resume session
    - Message editing for status updates
    """

    def __init__(
        self,
        token: str,
        guild_id: int,
        bot_core: GluonBotCore,
        allowed_users: list[int] | None = None,
        approval_channel_id: int | None = None,
    ):
        """Initialize Discord transport.

        Args:
            token: Discord bot token
            guild_id: Discord guild (server) ID to operate in
            bot_core: Bot core instance for business logic
            allowed_users: List of allowed Discord user IDs
            approval_channel_id: Channel ID to post approval requests to.
                Set via GLUON_DISCORD_APPROVAL_CHANNEL env var.
        """
        if not DISCORD_AVAILABLE:
            raise ImportError("discord.py is not installed. Install with: pip install 'gluon-agent[discord]'")

        self.token = token
        self.guild_id = guild_id
        self.bot_core = bot_core
        self._allowed_users: set[str] | None = None
        if allowed_users:
            self._allowed_users = {f"discord:{uid}" for uid in allowed_users}

        # Approval delivery channel (where Approve/Deny buttons are posted)
        self.approval_channel_id = approval_channel_id

        # Discord client setup
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self._setup_events()

        # Channel-to-project explicit mappings (loaded from DB)
        self._channel_project_map: dict[int, str] = {}

        # Approval watcher (started on bot ready)
        self._approval_watcher = None

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
            # Start the approval watcher now that the bot is connected
            await self._start_approval_watcher()

        @self.bot.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

    async def _start_approval_watcher(self) -> None:
        """Start the ApprovalWatcher once (idempotent)."""
        if self._approval_watcher is not None and self._approval_watcher.is_running:
            return

        from gluon.approval_watcher import ApprovalWatcher

        self._approval_watcher = ApprovalWatcher(
            store=self.bot_core.store,
            poster=self,
            name="discord-approvals",
        )
        await self._approval_watcher.start()

    def _load_channel_mappings(self) -> None:
        """Load channel-to-project mappings from database."""
        mappings = self.bot_core.store.list_channel_mappings("discord")
        self._channel_project_map = {int(m.channel_id): m.project_name for m in mappings}
        logger.info(f"Loaded {len(self._channel_project_map)} Discord channel mappings")

    def _make_context(self, message: discord.Message, project_hint: str | None = None) -> TransportContext:
        """Create TransportContext from Discord message.

        Args:
            message: Discord message
            project_hint: Optional project hint override (e.g., from DM specifier)
        """
        # Resolve project from channel if not provided
        if project_hint is None:
            project_hint = self._resolve_project(message.channel)

        # Determine if this is a DM
        is_dm = isinstance(message.channel, discord.DMChannel)

        return TransportContext(
            transport="discord",
            user_id=f"discord:{message.author.id}",
            chat_id=str(message.channel.id),
            project_hint=project_hint,
            message_id=str(message.id),
            raw_data={"message": message, "is_dm": is_dm},
        )

    def _get_channel_topic_config(self, channel: discord.abc.Messageable) -> dict[str, str | None]:
        """Get configuration from channel topic.

        Channel topics can contain flags like:
            --project myproject --model haiku

        Returns:
            Dict with 'project' and 'model' keys
        """
        if not isinstance(channel, discord.TextChannel):
            return {"project": None, "model": None}

        return parse_channel_topic(channel.topic)

    def _resolve_project(self, channel: discord.abc.Messageable) -> str | None:
        """Resolve project name from channel context.

        Priority:
        1. Channel topic --project flag
        2. Explicit link command mapping
        3. Auto-match by channel name
        """
        if not isinstance(channel, discord.TextChannel):
            return None

        # 1. Check channel topic for --project flag (highest priority)
        topic_config = self._get_channel_topic_config(channel)
        if topic_config["project"]:
            try:
                project = self.bot_core.orchestrator.get_project(topic_config["project"])
                return project.name
            except ProjectNotFoundError:
                pass  # Fall through to other methods

        channel_id = channel.id

        # 2. Check explicit mapping (from link command)
        if channel_id in self._channel_project_map:
            return self._channel_project_map[channel_id]

        # 3. Try auto-matching channel name to project name
        # Discord channels use hyphens, but project names may use hyphens or underscores
        # Normalize both for comparison
        def normalize(name: str) -> str:
            return name.lower().replace("-", "_").replace(" ", "_")

        channel_normalized = normalize(channel.name)

        # Try exact match first
        try:
            project = self.bot_core.orchestrator.get_project(channel.name)
            return project.name
        except ProjectNotFoundError:
            pass

        # Try matching against all projects with normalization
        for project in self.bot_core.orchestrator.list_projects():
            if normalize(project.name) == channel_normalized:
                return project.name

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

        channel_id = int(ctx.chat_id)
        channel = self.bot.get_channel(channel_id)

        if not channel:
            channel = await self.bot.fetch_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            raise ValueError(f"Cannot send to channel type: {type(channel)}")

        msg = await channel.send(text)
        return str(msg.id)

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

        if not isinstance(channel, discord.TextChannel):
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

        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.typing()
            except Exception as e:
                logger.debug(f"Failed to send typing: {e}")

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
        # Stop approval watcher if it was started
        if self._approval_watcher is not None:
            try:
                await self._approval_watcher.stop()
            except Exception:
                logger.debug("Approval watcher stop failed", exc_info=True)

        await self.bot_core.git_manager.stop_background_sync()
        await self.bot.close()
        logger.info("Discord transport stopped")

    async def post_approval_request(self, approval: PendingApproval) -> bool:
        """Post an approval request to Discord with Approve/Deny buttons.

        Called by the ApprovalWatcher. Returns True on success, False on
        retry-worthy failure. If no approval_channel_id is configured, returns
        True (no-op — don't spam logs, but watcher won't retry).
        """
        if self.approval_channel_id is None:
            logger.warning(
                "Discord has no approval_channel_id configured; skipping approval %s",
                approval.id[:8],
            )
            return True  # No-op — avoid retry spam

        try:
            channel = self.bot.get_channel(self.approval_channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(self.approval_channel_id)
                except Exception as e:
                    logger.warning("Approval channel %s not found: %s", self.approval_channel_id, e)
                    return False

            if not isinstance(channel, discord.abc.Messageable):
                logger.warning(
                    "Approval channel %s is not messageable (%s)",
                    self.approval_channel_id,
                    type(channel),
                )
                return False

            embed = format_approval_embed(approval)
            view = _build_approval_view(approval.id, self.bot_core.store, self.is_authorized)
            # Register as persistent view so buttons survive bot restart
            self.bot.add_view(view)

            await channel.send(embed=embed, view=view)
            return True
        except Exception as e:
            logger.warning("Failed to post approval %s to Discord: %s", approval.id[:8], e)
            return False

    async def _handle_message(self, message: discord.Message) -> None:
        """Handle incoming Discord messages."""
        # Ignore own messages
        if message.author == self.bot.user:
            return

        # Check if this is a DM
        if isinstance(message.channel, discord.DMChannel):
            await self._handle_dm_message(message)
            return

        # Check if this is a reply to a bot message (for session resume)
        if message.reference and message.reference.message_id:
            ref_id = message.reference.message_id
            # Query store for message-to-run mapping
            mapping = self.bot_core.store.get_message_run_map("discord", str(ref_id), str(message.channel.id))
            if mapping:
                await self._handle_reply_resume(message, ref_id)
                return

        # Regular messages require @mention
        if self.bot.user not in message.mentions:
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

        if text.lower() == "models":
            await self._handle_models_command(message)
            return

        if text.lower() == "help":
            await self._handle_help_command(message)
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
            f"**Options:**\n"
            f"1. Add `--project <name>` to channel topic\n"
            f"2. Use `@{self.bot.user.name} link <project>`\n"
            f"3. Rename channel to match project name\n\n"
            f"Available: {names}{more}"
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

    async def _handle_models_command(self, message: discord.Message) -> None:
        """Handle the models command - list available models."""
        text = (
            "**Available Models:**\n"
            "- `opus` / `claude-opus-4.5` - Highest quality, best for complex reasoning\n"
            "- `sonnet` / `claude-sonnet-4.6` - Fast, high-quality (default)\n"
            "- `haiku` / `claude-haiku-4.5` - Fastest, lowest cost\n\n"
            "**Usage:** `@gluon <task> --model opus`\n"
            "**Short form:** `@gluon <task> -m haiku`"
        )
        await message.reply(text)

    async def _handle_help_command(self, message: discord.Message) -> None:
        """Handle the help command - show available commands."""
        bot_name = self.bot.user.name if self.bot.user else "gluon"
        text = (
            f"**{bot_name} Commands:**\n\n"
            "**Tasks:**\n"
            f"`@{bot_name} <task>` - Run a task\n"
            f"`@{bot_name} <task> --model opus` - Override model for this task\n"
            f"`@{bot_name} <task> --agent researcher` - Run as a specific agent\n"
            f"`@{bot_name} <task> -m haiku -a researcher` - Short forms\n\n"
            "**Commands:**\n"
            "`projects` - List registered projects\n"
            "`runs` - List active/recent runs\n"
            "`status` - Show system status\n"
            "`models` - List available models\n"
            "`cancel [run_id]` - Cancel a run\n"
            "`link <project>` - Link channel to project\n"
            "`help` - Show this help\n\n"
            "**Channel Topic Config:**\n"
            "Set defaults in channel topic:\n"
            "`--project myproject --model haiku --agent researcher`\n\n"
            "**Resume:** Reply to a completion message to continue\n\n"
            "**DMs:** Send me a direct message!\n"
            "- Chat naturally, or\n"
            "- `project:myapp fix the bug` to run a task"
        )
        await message.reply(text)

    async def _handle_dm_message(self, message: discord.Message) -> None:
        """Handle direct messages to the bot.

        DMs support two modes:
        1. Chat mode (no project specifier): Routes to chat agent for conversation
        2. Task mode (with project specifier): Executes task on specified project

        Project specifier formats:
        - project:myproject <task>
        - p:myproject <task>
        - <task> --project myproject
        - <task> -p myproject
        """
        text = message.content.strip()
        if not text:
            return

        user_id = f"discord:{message.author.id}"

        # Check authorization
        if not self.is_authorized(user_id):
            await message.reply("You are not authorized to use this bot.")
            return

        # Handle DM-specific commands (no @mention needed)
        text_lower = text.lower()

        if text_lower == "projects":
            await self._handle_projects_command(message)
            return

        if text_lower == "runs":
            await self._handle_runs_command(message)
            return

        if text_lower == "status":
            await self._handle_status_command(message)
            return

        if text_lower.startswith("cancel"):
            await self._handle_cancel_command(message, text[6:].strip())
            return

        if text_lower == "models":
            await self._handle_models_command(message)
            return

        if text_lower == "help":
            await self._handle_dm_help_command(message)
            return

        if text_lower == "clear":
            self.bot_core.clear_history(user_id)
            await message.reply("Conversation history cleared.")
            return

        # Check for project specifier
        cleaned_prompt, project_name = parse_project_specifier(text)

        if project_name:
            # Task mode: Execute task on specified project
            await self._handle_dm_task(message, project_name, cleaned_prompt)
        else:
            # Chat mode: Route to chat agent
            await self._handle_dm_chat(message, text)

    async def _handle_dm_help_command(self, message: discord.Message) -> None:
        """Show DM-specific help."""
        text = (
            "**Direct Message Commands:**\n\n"
            "**Chat Mode:**\n"
            "Just type naturally! I can answer questions about your projects, "
            "check status, or help plan tasks.\n\n"
            "**Task Mode:**\n"
            "`project:myapp fix the bug` - Run task on project\n"
            "`p:myapp add tests --model opus` - Short form with model override\n"
            "`p:myapp review PR --agent researcher` - Target a specific agent\n\n"
            "**Commands:**\n"
            "`projects` - List registered projects\n"
            "`runs` - List your recent runs\n"
            "`status` - Show system status\n"
            "`models` - List available models\n"
            "`cancel [run_id]` - Cancel a run\n"
            "`clear` - Clear conversation history\n"
            "`help` - Show this help"
        )
        await message.reply(text)

    async def _handle_dm_chat(self, message: discord.Message, text: str) -> None:
        """Handle chat-mode DM via chat agent."""
        ctx = self._make_context(message)

        # Show typing indicator
        async with message.channel.typing():

            async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
                msg = await message.reply(response.text[:2000])
                return str(msg.id)

            # Process through chat agent
            pending_task = await self.bot_core.process_natural_language(
                ctx=ctx,
                text=text,
                send_callback=send_callback,
            )

            # If chat agent returned a pending task, ask for confirmation
            if pending_task:
                project = pending_task.get("project")
                prompt = pending_task.get("prompt", "")
                await message.reply(
                    f"Would you like me to run this task?\n"
                    f"**Project:** `{project}`\n"
                    f"**Task:** _{prompt[:100]}{'...' if len(prompt) > 100 else ''}_\n\n"
                    f"Use `project:{project} {prompt}` to execute."
                )

    async def _handle_dm_task(self, message: discord.Message, project_name: str, prompt: str) -> None:
        """Handle task execution from DM with project specifier."""
        from gluon.core import AgentAmbiguousError, AgentNotFoundError, BudgetExceededError

        user_id = f"discord:{message.author.id}"

        # Validate project exists
        try:
            project = self.bot_core.orchestrator.get_project(project_name)
        except ProjectNotFoundError:
            projects = self.bot_core.orchestrator.list_projects()
            names = ", ".join(f"`{p.name}`" for p in projects[:10])
            more = f"... and {len(projects) - 10} more" if len(projects) > 10 else ""
            await message.reply(
                f"Project `{project_name}` not found.\n\nAvailable: {names}{more}\nUse `projects` to see all."
            )
            return

        if not prompt:
            await message.reply(
                f"Project `{project.name}` found, but no task specified.\n"
                f"Usage: `project:{project.name} <your task here>`"
            )
            return

        # Check capacity
        if self.bot_core.is_at_capacity():
            await message.reply(
                f"Max concurrent runs ({self.bot_core._semaphore._value}) reached.\n"
                "Use `runs` to see active runs or `cancel` to stop one."
            )
            return

        # Parse --agent flag from prompt (DM has no channel topic fallback)
        cleaned_prompt, agent_ref = parse_agent_flag(prompt)

        try:
            resolved_agent_id = self.bot_core.orchestrator.resolve_agent(agent_ref, project.workspace_id)
        except AgentNotFoundError as e:
            await message.reply(f"❌ {e}")
            return
        except AgentAmbiguousError as e:
            await message.reply(f"❌ {e}")
            return

        agent_display_name: str | None = None
        if resolved_agent_id is not None:
            resolved_agent = self.bot_core.store.get_agent(resolved_agent_id)
            if resolved_agent is not None:
                agent_display_name = resolved_agent.name

        # Parse --model flag from the (agent-stripped) prompt
        cleaned_prompt, model = parse_model_flag(cleaned_prompt)
        if not model:
            model = DEFAULT_MODEL

        # Proactively check the agent's monthly budget so we fail fast before
        # creating the run.
        if resolved_agent_id is not None:
            try:
                self.bot_core.orchestrator._enforce_agent_budget(resolved_agent_id)
            except BudgetExceededError as e:
                await message.reply(f"❌ {e}")
                return

        # Create run record (D5 Phase 4: attribute to linked Gluon user if any)
        run = self.bot_core.store.create_run(
            project.id,
            cleaned_prompt,
            initiator=user_id,
            model=model,
            agent_id=resolved_agent_id,
            user_id=self.bot_core.resolve_user_id_by_chat_id("discord", int(message.author.id)),
        )

        # Format model name for display
        model_short = model.replace("claude-", "").replace("-4.5", "")

        # Send initial status message (optionally showing the agent name)
        agent_line = f" with agent `{agent_display_name}`" if agent_display_name else ""
        status_msg = await message.reply(
            f"🚀 **Starting task** on `{project.name}` ({model_short}){agent_line}\n"
            f"Run: `{run.id[:8]}`\nStatus: Running..."
        )

        ctx = self._make_context(message, project_hint=project.name)

        async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
            # Send to DM channel
            msg = await message.channel.send(response.text[:2000])
            return str(msg.id)

        # Execute task
        async def execute_task():
            try:
                await self.bot_core.execute_task(
                    ctx=ctx,
                    run=run,
                    project_name=project.name,
                    send_callback=send_callback,
                    force_new_session=True,
                    model=model,
                )

                # Update status message with completion
                run_updated = self.bot_core.store.get_run(run.id)
                if run_updated:
                    emoji = "✅" if run_updated.status.value == "completed" else "❌"
                    await status_msg.edit(
                        content=(
                            f"{emoji} **{project.name}** ({model_short}) - `{run.id[:8]}`\n"
                            f"_{cleaned_prompt[:60]}{'...' if len(cleaned_prompt) > 60 else ''}_\n"
                            f"💬 Reply to continue"
                        )
                    )
                    # Track this message for future resume (persisted to DB)
                    self.bot_core.store.create_message_run_map(
                        transport="discord",
                        message_id=str(status_msg.id),
                        run_id=run.id,
                        chat_id=str(message.channel.id),
                        user_id=ctx.user_id,
                    )

            except Exception:
                logger.exception("DM task execution failed")
                await status_msg.edit(content=f"❌ **Failed** - `{run.id[:8]}`")

        task = asyncio.create_task(execute_task())
        self.bot_core.register_task(run.id, task)

    async def _handle_reply_resume(self, message: discord.Message, ref_message_id: int) -> None:
        """Handle a reply to a completion message to resume the session."""
        prompt = message.content.strip()

        # Strip @mention if present
        if self.bot.user in message.mentions:
            prompt = re.sub(rf"<@!?{self.bot.user.id}>", "", prompt).strip()

        if not prompt:
            return

        # Check authorization
        user_id = f"discord:{message.author.id}"
        if not self.is_authorized(user_id):
            await message.reply("You are not authorized to use this bot.")
            return

        # Look up the run by message ID from store
        mapping = self.bot_core.store.get_message_run_map("discord", str(ref_message_id), str(message.channel.id))
        if not mapping:
            return
        run_id = mapping.run_id

        run = self.bot_core.store.get_run(run_id)
        if not run or not run.session_id:
            await message.reply("Cannot resume: session not found.")
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

        # Create new run for the resume (attribute to linked Gluon user)
        new_run = self.bot_core.store.create_run(
            project.id,
            prompt,
            initiator=user_id,
            user_id=self.bot_core.resolve_user_id_by_chat_id("discord", int(message.author.id)),
        )

        # Send acknowledgment
        status_msg = await message.reply(
            f"🔄 **Resuming session** on `{project.name}`\nRun: `{new_run.id[:8]}`\nStatus: Running..."
        )

        ctx = self._make_context(message)

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
                    force_new_session=False,
                    session_id=run.session_id,
                )

                # Update status message and track for future resume
                run_updated = self.bot_core.store.get_run(new_run.id)
                if run_updated:
                    emoji = "✅" if run_updated.status.value == "completed" else "❌"
                    await status_msg.edit(
                        content=(
                            f"{emoji} **{project.name}** - `{new_run.id[:8]}`\n"
                            f"_{prompt[:60]}{'...' if len(prompt) > 60 else ''}_\n"
                            f"💬 Reply to continue"
                        )
                    )
                    # Track this message for future resume (persisted to DB)
                    self.bot_core.store.create_message_run_map(
                        transport="discord",
                        message_id=str(status_msg.id),
                        run_id=new_run.id,
                        chat_id=str(message.channel.id),
                        user_id=user_id,
                    )

            except Exception:
                logger.exception("Resume task failed")
                await status_msg.edit(content=f"❌ **Failed** - `{new_run.id[:8]}`")

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
        from gluon.core import AgentAmbiguousError, AgentNotFoundError, BudgetExceededError

        user_id = ctx.user_id

        if self.bot_core.is_at_capacity():
            await message.reply(
                f"Max concurrent runs ({self.bot_core._semaphore._value}) reached.\n"
                "Use `runs` to see active runs or `cancel` to stop one."
            )
            return

        # Resolve the project (we need workspace_id for agent resolution)
        try:
            project = self.bot_core.orchestrator.get_project(project_name)
        except ProjectNotFoundError as e:
            await message.reply(f"Error: {e}")
            return

        # Parse --agent flag from prompt (highest priority), then channel topic
        cleaned_prompt, agent_ref = parse_agent_flag(prompt)
        topic_config = self._get_channel_topic_config(message.channel)
        if agent_ref is None:
            agent_ref = topic_config.get("agent")

        # Resolve the agent; None triggers auto-link if the workspace has one active agent
        try:
            resolved_agent_id = self.bot_core.orchestrator.resolve_agent(agent_ref, project.workspace_id)
        except AgentNotFoundError as e:
            await message.reply(f"❌ {e}")
            return
        except AgentAmbiguousError as e:
            await message.reply(f"❌ {e}")
            return

        agent_display_name: str | None = None
        if resolved_agent_id is not None:
            resolved_agent = self.bot_core.store.get_agent(resolved_agent_id)
            if resolved_agent is not None:
                agent_display_name = resolved_agent.name

        # Parse --model flag from (already agent-stripped) prompt
        cleaned_prompt, model = parse_model_flag(cleaned_prompt)

        # Fall back to channel topic --model, then default
        if not model:
            model = topic_config["model"] or DEFAULT_MODEL

        # Proactively check the agent's monthly budget so we fail fast before
        # creating the run.
        if resolved_agent_id is not None:
            try:
                self.bot_core.orchestrator._enforce_agent_budget(resolved_agent_id)
            except BudgetExceededError as e:
                await message.reply(f"❌ {e}")
                return

        # Create run record with model + resolved agent (attribute to linked Gluon user)
        run = self.bot_core.store.create_run(
            project.id,
            cleaned_prompt,
            initiator=user_id,
            model=model,
            agent_id=resolved_agent_id,
            user_id=self.bot_core.resolve_user_id_by_chat_id("discord", int(message.author.id)),
        )

        # Format model name for display (opus/sonnet/haiku)
        model_short = model.replace("claude-", "").replace("-4.5", "")

        # Send initial status message (optionally showing the agent name)
        agent_line = f" with agent `{agent_display_name}`" if agent_display_name else ""
        status_msg = await message.reply(
            f"🚀 **Starting task** on `{project_name}` ({model_short}){agent_line}\n"
            f"Run: `{run.id[:8]}`\nStatus: Running..."
        )

        async def send_callback(ctx: TransportContext, response: TransportResponse) -> str | None:
            return await self.send(ctx, response)

        # Execute task
        async def execute_task():
            try:
                await self.bot_core.execute_task(
                    ctx=ctx,
                    run=run,
                    project_name=project_name,
                    send_callback=send_callback,
                    force_new_session=True,
                    model=model,
                )

                # Update status message with completion
                run_updated = self.bot_core.store.get_run(run.id)
                if run_updated:
                    emoji = "✅" if run_updated.status.value == "completed" else "❌"
                    await status_msg.edit(
                        content=(
                            f"{emoji} **{project_name}** ({model_short}) - `{run.id[:8]}`\n"
                            f"_{cleaned_prompt[:60]}{'...' if len(cleaned_prompt) > 60 else ''}_\n"
                            f"💬 Reply to continue"
                        )
                    )
                    # Track this message for future resume (persisted to DB)
                    self.bot_core.store.create_message_run_map(
                        transport="discord",
                        message_id=str(status_msg.id),
                        run_id=run.id,
                        chat_id=str(message.channel.id),
                        user_id=ctx.user_id,
                    )

            except Exception:
                logger.exception("Task execution failed")
                await status_msg.edit(content=f"❌ **Failed** - `{run.id[:8]}`")

        task = asyncio.create_task(execute_task())
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
