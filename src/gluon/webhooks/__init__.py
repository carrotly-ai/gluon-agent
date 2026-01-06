"""Webhook handlers for external service integrations."""

from gluon.webhooks.base import WebhookEvent, WebhookHandler
from gluon.webhooks.github import GitHubWebhookHandler

__all__ = ["WebhookEvent", "WebhookHandler", "GitHubWebhookHandler"]
