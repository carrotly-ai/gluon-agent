"""Base classes for webhook handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WebhookEvent:
    """Parsed webhook event ready for execution."""

    handler: str  # "github", "gitlab", "bitbucket"
    event_type: str  # "push", "pull_request", "issue", etc.
    project_hint: str  # Repository name for project resolution
    prompt: str  # Generated task prompt
    source_ref: str | None = None  # Branch name or PR number
    metadata: dict[str, Any] = field(default_factory=dict)  # Full payload

    # Optional fields for better task context
    title: str | None = None  # PR/Issue title
    author: str | None = None  # User who triggered the event
    url: str | None = None  # Link to PR/Issue/Commit


class WebhookHandler(ABC):
    """Abstract base class for webhook handlers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return handler name (e.g., 'github', 'gitlab')."""
        ...

    @abstractmethod
    async def validate_signature(self, payload: bytes, signature: str) -> bool:
        """Validate webhook signature.

        Args:
            payload: Raw request body
            signature: Signature header value

        Returns:
            True if signature is valid
        """
        ...

    @abstractmethod
    async def parse_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookEvent | None:
        """Parse webhook payload into a WebhookEvent.

        Args:
            event_type: Event type header (e.g., 'pull_request', 'push')
            payload: Parsed JSON payload

        Returns:
            WebhookEvent if this event should trigger a task, None otherwise
        """
        ...

    def generate_prompt(
        self,
        event_type: str,
        payload: dict[str, Any],
        template: str | None = None,
    ) -> str:
        """Generate a task prompt from the webhook payload.

        Args:
            event_type: Event type
            payload: Webhook payload
            template: Custom prompt template (optional)

        Returns:
            Generated prompt string
        """
        if template:
            return self._render_template(template, event_type, payload)
        return self._default_prompt(event_type, payload)

    @abstractmethod
    def _default_prompt(self, event_type: str, payload: dict[str, Any]) -> str:
        """Generate default prompt for event type."""
        ...

    def _render_template(
        self,
        template: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        """Render a custom prompt template.

        Supports simple {{variable}} substitution.
        """
        result = template

        # Common substitutions
        substitutions = {
            "event_type": event_type,
            "repo": self._get_repo_name(payload),
            "branch": self._get_branch(payload),
            "author": self._get_author(payload),
            "title": self._get_title(payload),
            "body": self._get_body(payload),
            "url": self._get_url(payload),
        }

        for key, value in substitutions.items():
            result = result.replace(f"{{{{{key}}}}}", str(value or ""))

        return result.strip()

    @abstractmethod
    def _get_repo_name(self, payload: dict[str, Any]) -> str | None:
        """Extract repository name from payload."""
        ...

    @abstractmethod
    def _get_branch(self, payload: dict[str, Any]) -> str | None:
        """Extract branch name from payload."""
        ...

    @abstractmethod
    def _get_author(self, payload: dict[str, Any]) -> str | None:
        """Extract author username from payload."""
        ...

    @abstractmethod
    def _get_title(self, payload: dict[str, Any]) -> str | None:
        """Extract title (PR/Issue) from payload."""
        ...

    @abstractmethod
    def _get_body(self, payload: dict[str, Any]) -> str | None:
        """Extract body (PR/Issue description) from payload."""
        ...

    @abstractmethod
    def _get_url(self, payload: dict[str, Any]) -> str | None:
        """Extract URL from payload."""
        ...
