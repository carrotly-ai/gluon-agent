"""Transport capabilities declarations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TransportCapabilities:
    """Declares what features a transport supports.

    Used to adapt behavior based on platform limitations
    (message length, threading style, etc.).
    """

    max_message_length: int
    """Maximum characters per message."""

    supports_threads: bool
    """Whether native threading is supported."""

    supports_editing: bool
    """Whether sent messages can be edited."""

    supports_reactions: bool
    """Whether emoji reactions are supported."""

    supports_typing: bool
    """Whether typing indicators are supported."""

    supports_embeds: bool = False
    """Whether rich embeds/cards are supported (Discord, Slack)."""

    supports_buttons: bool = False
    """Whether interactive buttons are supported."""


# Telegram capabilities
TELEGRAM_CAPS = TransportCapabilities(
    max_message_length=4096,
    supports_threads=True,  # via reply_to_message_id (pseudo-threading)
    supports_editing=True,
    supports_reactions=False,  # Limited in Telegram
    supports_typing=True,
    supports_embeds=False,
    supports_buttons=True,  # Inline keyboards
)

# Discord capabilities
DISCORD_CAPS = TransportCapabilities(
    max_message_length=2000,
    supports_threads=True,  # Native forum/message threads
    supports_editing=True,
    supports_reactions=True,
    supports_typing=True,
    supports_embeds=True,
    supports_buttons=True,
)

# Slack capabilities (for future use)
SLACK_CAPS = TransportCapabilities(
    max_message_length=40000,
    supports_threads=True,
    supports_editing=True,
    supports_reactions=True,
    supports_typing=True,
    supports_embeds=True,  # Blocks
    supports_buttons=True,
)

# CLI/Terminal capabilities (for testing)
CLI_CAPS = TransportCapabilities(
    max_message_length=100000,  # Effectively unlimited
    supports_threads=False,
    supports_editing=False,
    supports_reactions=False,
    supports_typing=False,
    supports_embeds=False,
    supports_buttons=False,
)
