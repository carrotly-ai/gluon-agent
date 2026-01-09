"""Tests for webhook handlers."""

import hashlib
import hmac

import pytest

from gluon.webhooks.base import WebhookEvent
from gluon.webhooks.github import GitHubWebhookHandler


class TestGitHubWebhookHandler:
    """Tests for GitHub webhook handler."""

    @pytest.fixture
    def handler(self):
        """Create a handler with a test secret."""
        return GitHubWebhookHandler(secret="test-secret-123")

    @pytest.fixture
    def push_payload(self):
        """Sample push event payload."""
        return {
            "ref": "refs/heads/main",
            "repository": {
                "name": "my-project",
                "full_name": "user/my-project",
            },
            "commits": [
                {"message": "Add new feature\n\nDetailed description"},
                {"message": "Fix bug in parser"},
            ],
            "pusher": {"name": "testuser"},
            "compare": "https://github.com/user/my-project/compare/abc...def",
        }

    @pytest.fixture
    def pr_payload(self):
        """Sample pull_request event payload."""
        return {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "Add awesome feature",
                "body": "This PR adds an awesome feature.",
                "user": {"login": "contributor"},
                "head": {"ref": "feature-branch"},
                "base": {"ref": "main"},
                "html_url": "https://github.com/user/my-project/pull/42",
            },
            "repository": {
                "name": "my-project",
                "full_name": "user/my-project",
            },
        }

    @pytest.fixture
    def issue_payload(self):
        """Sample issues event payload."""
        return {
            "action": "opened",
            "issue": {
                "number": 123,
                "title": "Bug: Something is broken",
                "body": "Description of the bug",
                "user": {"login": "reporter"},
                "html_url": "https://github.com/user/my-project/issues/123",
                "labels": [{"name": "bug"}, {"name": "priority:high"}],
            },
            "repository": {
                "name": "my-project",
                "full_name": "user/my-project",
            },
        }

    @pytest.fixture
    def comment_payload(self):
        """Sample issue_comment event payload with /gluon command."""
        return {
            "action": "created",
            "comment": {
                "body": "/gluon please fix this bug",
                "user": {"login": "manager"},
                "html_url": "https://github.com/user/my-project/issues/123#issuecomment-456",
            },
            "issue": {
                "number": 123,
                "title": "Bug: Something is broken",
            },
            "repository": {
                "name": "my-project",
                "full_name": "user/my-project",
            },
        }

    # ========== Signature Validation ==========

    @pytest.mark.anyio
    async def test_validate_signature_valid(self, handler):
        """Test valid signature passes validation."""
        payload = b'{"test": "data"}'
        signature = (
            "sha256="
            + hmac.new(
                b"test-secret-123",
                payload,
                hashlib.sha256,
            ).hexdigest()
        )

        result = await handler.validate_signature(payload, signature)
        assert result is True

    @pytest.mark.anyio
    async def test_validate_signature_invalid(self, handler):
        """Test invalid signature fails validation."""
        payload = b'{"test": "data"}'
        signature = "sha256=invalid_signature"

        result = await handler.validate_signature(payload, signature)
        assert result is False

    @pytest.mark.anyio
    async def test_validate_signature_wrong_format(self, handler):
        """Test signature without sha256= prefix fails."""
        payload = b'{"test": "data"}'
        signature = "wrong_format_signature"

        result = await handler.validate_signature(payload, signature)
        assert result is False

    # ========== Push Event Parsing ==========

    @pytest.mark.anyio
    async def test_parse_push_event(self, handler, push_payload):
        """Test parsing push event."""
        event = await handler.parse_event("push", push_payload)

        assert event is not None
        assert isinstance(event, WebhookEvent)
        assert event.handler == "github"
        assert event.event_type == "push"
        assert event.project_hint == "my-project"
        assert event.source_ref == "main"
        assert event.author == "testuser"
        assert "Add new feature" in event.prompt
        assert "Fix bug" in event.prompt

    @pytest.mark.anyio
    async def test_parse_push_ignores_tags(self, handler):
        """Test that tag pushes are ignored."""
        payload = {
            "ref": "refs/tags/v1.0.0",
            "repository": {"name": "my-project", "full_name": "user/my-project"},
            "commits": [],
        }

        event = await handler.parse_event("push", payload)
        assert event is None

    @pytest.mark.anyio
    async def test_parse_push_ignores_empty_commits(self, handler):
        """Test that pushes with no commits are ignored."""
        payload = {
            "ref": "refs/heads/main",
            "repository": {"name": "my-project", "full_name": "user/my-project"},
            "commits": [],
        }

        event = await handler.parse_event("push", payload)
        assert event is None

    # ========== Pull Request Event Parsing ==========

    @pytest.mark.anyio
    async def test_parse_pr_opened(self, handler, pr_payload):
        """Test parsing PR opened event."""
        event = await handler.parse_event("pull_request", pr_payload)

        assert event is not None
        assert event.event_type == "pull_request"
        assert event.project_hint == "my-project"
        assert event.source_ref == "feature-branch"
        assert event.title == "Add awesome feature"
        assert event.author == "contributor"
        assert "awesome feature" in event.prompt

    @pytest.mark.anyio
    async def test_parse_pr_synchronize(self, handler, pr_payload):
        """Test parsing PR synchronize (new commits) event."""
        pr_payload["action"] = "synchronize"
        event = await handler.parse_event("pull_request", pr_payload)
        assert event is not None

    @pytest.mark.anyio
    async def test_parse_pr_ignores_closed(self, handler, pr_payload):
        """Test that PR closed events are ignored."""
        pr_payload["action"] = "closed"
        event = await handler.parse_event("pull_request", pr_payload)
        assert event is None

    # ========== Issues Event Parsing ==========

    @pytest.mark.anyio
    async def test_parse_issue_opened(self, handler, issue_payload):
        """Test parsing issue opened event."""
        event = await handler.parse_event("issues", issue_payload)

        assert event is not None
        assert event.event_type == "issue"
        assert event.project_hint == "my-project"
        assert event.title == "Bug: Something is broken"
        assert "reporter" in event.prompt or event.author == "reporter"

    @pytest.mark.anyio
    async def test_parse_issue_labeled(self, handler, issue_payload):
        """Test parsing issue labeled event."""
        issue_payload["action"] = "labeled"
        event = await handler.parse_event("issues", issue_payload)
        assert event is not None

    @pytest.mark.anyio
    async def test_parse_issue_ignores_closed(self, handler, issue_payload):
        """Test that issue closed events are ignored."""
        issue_payload["action"] = "closed"
        event = await handler.parse_event("issues", issue_payload)
        assert event is None

    # ========== Issue Comment Event Parsing ==========

    @pytest.mark.anyio
    async def test_parse_comment_with_gluon_command(self, handler, comment_payload):
        """Test parsing comment with /gluon command."""
        event = await handler.parse_event("issue_comment", comment_payload)

        assert event is not None
        assert event.event_type == "issue_comment"
        assert "please fix this bug" in event.prompt
        assert event.author == "manager"

    @pytest.mark.anyio
    async def test_parse_comment_ignores_non_gluon(self, handler, comment_payload):
        """Test that comments without /gluon are ignored."""
        comment_payload["comment"]["body"] = "Regular comment"
        event = await handler.parse_event("issue_comment", comment_payload)
        assert event is None

    @pytest.mark.anyio
    async def test_parse_comment_on_pr(self, handler, comment_payload):
        """Test parsing /gluon comment on a PR."""
        comment_payload["issue"]["pull_request"] = {}
        event = await handler.parse_event("issue_comment", comment_payload)

        assert event is not None
        assert "PR" in event.prompt

    # ========== Unsupported Events ==========

    @pytest.mark.anyio
    async def test_parse_unsupported_event(self, handler):
        """Test that unsupported events return None."""
        event = await handler.parse_event("deployment", {"repository": {"name": "test"}})
        assert event is None

    @pytest.mark.anyio
    async def test_parse_missing_repo(self, handler):
        """Test that events without repository return None."""
        event = await handler.parse_event("push", {"ref": "refs/heads/main"})
        assert event is None

    # ========== Prompt Generation ==========

    def test_generate_prompt_with_template(self, handler, push_payload):
        """Test custom prompt template rendering."""
        template = "Review {{repo}} branch {{branch}} by {{author}}"
        prompt = handler.generate_prompt("push", push_payload, template)

        assert "my-project" in prompt
        assert "main" in prompt
        assert "testuser" in prompt

    def test_generate_prompt_default(self, handler, push_payload):
        """Test default prompt generation."""
        prompt = handler.generate_prompt("push", push_payload)

        assert "main" in prompt
        assert "Add new feature" in prompt


class TestWebhookEvent:
    """Tests for WebhookEvent dataclass."""

    def test_webhook_event_creation(self):
        """Test creating a WebhookEvent."""
        event = WebhookEvent(
            handler="github",
            event_type="push",
            project_hint="my-project",
            prompt="Review these changes",
            source_ref="main",
            metadata={"commits": 5},
        )

        assert event.handler == "github"
        assert event.event_type == "push"
        assert event.project_hint == "my-project"
        assert event.prompt == "Review these changes"
        assert event.source_ref == "main"
        assert event.metadata["commits"] == 5

    def test_webhook_event_optional_fields(self):
        """Test WebhookEvent with optional fields."""
        event = WebhookEvent(
            handler="github",
            event_type="pull_request",
            project_hint="my-project",
            prompt="Review this PR",
            title="My PR",
            author="contributor",
            url="https://github.com/user/repo/pull/1",
        )

        assert event.title == "My PR"
        assert event.author == "contributor"
        assert event.url == "https://github.com/user/repo/pull/1"
