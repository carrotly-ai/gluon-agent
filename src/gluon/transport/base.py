"""Base transport abstraction for multi-platform bot support."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gluon.transport.capabilities import TransportCapabilities


def truncate_preview(text: str, limit: int = 300) -> str:
    """Trim text to ``limit`` chars with an ellipsis, for compact transport
    previews (approval cards, embed fields). Distinct from
    ``Transport.truncate_text``, which enforces the full per-message length cap.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


@dataclass
class TransportContext:
    """Unified context for any transport.

    Represents the context of an incoming message, including
    user identity, channel/chat info, and transport-specific metadata.
    """

    transport: str
    """Transport identifier: 'telegram', 'discord', 'slack', etc."""

    user_id: str
    """Universal user identifier in format '{transport}:{id}', e.g., 'telegram:123'."""

    chat_id: str
    """Channel/conversation identifier."""

    thread_id: str | None = None
    """Thread ID if message is in a thread (Discord threads, Telegram reply chains)."""

    project_hint: str | None = None
    """Project name hint from context (e.g., Discord channel name)."""

    message_id: str | None = None
    """ID of the triggering message (for threading replies)."""

    raw_data: dict[str, Any] = field(default_factory=dict)
    """Transport-specific metadata for advanced use cases."""

    @property
    def platform_user_id(self) -> str:
        """Extract the platform-specific user ID from universal user_id."""
        if ":" in self.user_id:
            return self.user_id.split(":", 1)[1]
        return self.user_id


@dataclass
class TransportMessage:
    """Unified incoming message representation.

    Normalizes messages from different platforms into a common format.
    """

    text: str
    """Message text content."""

    context: TransportContext
    """Context of the message (user, channel, transport info)."""

    reply_to_id: str | None = None
    """ID of the message being replied to, if any."""

    reply_to_text: str | None = None
    """Text of the message being replied to (for context)."""


@dataclass
class TransportResponse:
    """Unified outgoing response representation.

    Defines how to send a response, with transport-agnostic options
    that each transport interprets appropriately.
    """

    text: str
    """Message text to send."""

    thread_id: str | None = None
    """Thread to send into (or create). None = send to main channel/chat."""

    reply_to_id: str | None = None
    """Message ID to reply to (for Telegram-style threading)."""

    parse_mode: str = "markdown"
    """Text formatting: 'plain', 'markdown', 'html'."""

    editable: bool = False
    """Hint that this message may be edited later (for progress updates)."""


class Transport(ABC):
    """Abstract base class for transport implementations.

    Each transport (Telegram, Discord, Slack, etc.) implements this interface
    to provide a consistent API for the bot core to interact with.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return transport name (e.g., 'telegram', 'discord')."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> TransportCapabilities:
        """Return transport capabilities."""
        ...

    @abstractmethod
    async def send(
        self,
        ctx: TransportContext,
        response: TransportResponse,
    ) -> str:
        """Send a message and return its ID.

        Args:
            ctx: Context for where to send (chat_id, optional thread_id)
            response: What to send

        Returns:
            Message ID that can be used for threading or editing
        """
        ...

    @abstractmethod
    async def edit(
        self,
        ctx: TransportContext,
        message_id: str,
        response: TransportResponse,
    ) -> bool:
        """Edit an existing message.

        Args:
            ctx: Context (chat_id)
            message_id: ID of message to edit
            response: New content

        Returns:
            True if edit succeeded
        """
        ...

    @abstractmethod
    async def send_typing(self, ctx: TransportContext) -> None:
        """Show typing indicator in the chat."""
        ...

    async def create_thread(
        self,
        ctx: TransportContext,
        name: str,
        message_id: str | None = None,
    ) -> str | None:
        """Create a thread and return its ID.

        Optional - not all transports support threading.

        Args:
            ctx: Context (chat_id)
            name: Thread name/title
            message_id: Optional message to attach thread to (Discord)

        Returns:
            Thread ID, or None if threading not supported
        """
        return None

    @abstractmethod
    async def start(self) -> None:
        """Start the transport (begin receiving messages)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport gracefully."""
        ...

    def is_authorized(self, user_id: str | int) -> bool:
        """Check if user is authorized. Override in subclass.

        Default implementation allows all users.
        """
        return True

    def truncate_text(self, text: str) -> str:
        """Truncate text to transport's max message length."""
        max_len = self.capabilities.max_message_length
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."
