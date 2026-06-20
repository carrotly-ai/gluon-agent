"""Transport abstraction for multi-platform bot support."""

from gluon.transport.base import (
    Transport,
    TransportContext,
    TransportMessage,
    TransportResponse,
)
from gluon.transport.capabilities import (
    DISCORD_CAPS,
    TELEGRAM_CAPS,
    TransportCapabilities,
)

__all__ = [
    "Transport",
    "TransportContext",
    "TransportMessage",
    "TransportResponse",
    "TransportCapabilities",
    "TELEGRAM_CAPS",
    "DISCORD_CAPS",
]
