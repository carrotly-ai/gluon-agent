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


# Telegram capabilities
TELEGRAM_CAPS = TransportCapabilities(max_message_length=4096)

# Discord capabilities
DISCORD_CAPS = TransportCapabilities(max_message_length=2000)
