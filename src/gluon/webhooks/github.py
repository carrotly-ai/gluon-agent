"""GitHub webhook handler."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from gluon.webhooks.base import WebhookEvent, WebhookHandler

logger = logging.getLogger(__name__)

# Supported GitHub webhook events
SUPPORTED_EVENTS = {
    "push",
    "pull_request",
    "issues",
    "issue_comment",
    "pull_request_review",
    "pull_request_review_comment",
}


class GitHubWebhookHandler(WebhookHandler):
    """Handler for GitHub webhooks."""

    def __init__(self, secret: str):
        """Initialize with webhook secret.

        Args:
            secret: GitHub webhook secret for signature validation
        """
        self.secret = secret

    @property
    def name(self) -> str:
        return "github"

    async def validate_signature(self, payload: bytes, signature: str) -> bool:
        """Validate GitHub webhook signature (X-Hub-Signature-256).

        GitHub sends signatures in format: sha256=<hash>
        """
        if not signature.startswith("sha256="):
            logger.warning("Invalid signature format: missing sha256= prefix")
            return False

        expected_sig = signature[7:]  # Remove 'sha256=' prefix
        computed_sig = hmac.new(
            self.secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        is_valid = hmac.compare_digest(expected_sig, computed_sig)
        if not is_valid:
            logger.warning("GitHub webhook signature validation failed")

        return is_valid

    async def parse_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookEvent | None:
        """Parse GitHub webhook payload.

        Args:
            event_type: GitHub event type (X-GitHub-Event header)
            payload: Parsed JSON payload

        Returns:
            WebhookEvent or None if event should be ignored
        """
        if event_type not in SUPPORTED_EVENTS:
            logger.debug(f"Ignoring unsupported event type: {event_type}")
            return None

        # Get repo info
        repo = payload.get("repository", {})
        repo_name = repo.get("name", "")
        repo_full_name = repo.get("full_name", "")

        if not repo_name:
            logger.warning("Webhook payload missing repository name")
            return None

        # Route to specific parser
        if event_type == "push":
            return self._parse_push(payload, repo_name, repo_full_name)
        elif event_type == "pull_request":
            return self._parse_pull_request(payload, repo_name, repo_full_name)
        elif event_type == "issues":
            return self._parse_issue(payload, repo_name, repo_full_name)
        elif event_type == "issue_comment":
            return self._parse_issue_comment(payload, repo_name, repo_full_name)
        elif event_type in ("pull_request_review", "pull_request_review_comment"):
            return self._parse_pr_review(payload, repo_name, repo_full_name, event_type)

        return None

    def _parse_push(
        self,
        payload: dict[str, Any],
        repo_name: str,
        repo_full_name: str,
    ) -> WebhookEvent | None:
        """Parse push event."""
        ref = payload.get("ref", "")
        branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

        # Skip tag pushes
        if ref.startswith("refs/tags/"):
            logger.debug(f"Ignoring tag push: {ref}")
            return None

        commits = payload.get("commits", [])
        if not commits:
            logger.debug("Push with no commits, ignoring")
            return None

        # Get commit messages for context
        commit_messages = [c.get("message", "").split("\n")[0] for c in commits[:5]]
        pusher = payload.get("pusher", {}).get("name", "unknown")
        compare_url = payload.get("compare", "")

        prompt = self._default_prompt("push", payload)

        return WebhookEvent(
            handler="github",
            event_type="push",
            project_hint=repo_name,
            prompt=prompt,
            source_ref=branch,
            title=f"Push to {branch}",
            author=pusher,
            url=compare_url,
            metadata={
                "ref": ref,
                "branch": branch,
                "commits": commits,
                "commit_messages": commit_messages,
                "pusher": pusher,
                "repo_full_name": repo_full_name,
            },
        )

    def _parse_pull_request(
        self,
        payload: dict[str, Any],
        repo_name: str,
        repo_full_name: str,
    ) -> WebhookEvent | None:
        """Parse pull_request event."""
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})

        # Only process opened, synchronize (new commits), or reopened
        if action not in ("opened", "synchronize", "reopened"):
            logger.debug(f"Ignoring PR action: {action}")
            return None

        pr_number = pr.get("number")
        title = pr.get("title", "")
        body = pr.get("body", "") or ""
        head_ref = pr.get("head", {}).get("ref", "")
        base_ref = pr.get("base", {}).get("ref", "")
        author = pr.get("user", {}).get("login", "unknown")
        html_url = pr.get("html_url", "")

        prompt = self._default_prompt("pull_request", payload)

        return WebhookEvent(
            handler="github",
            event_type="pull_request",
            project_hint=repo_name,
            prompt=prompt,
            source_ref=head_ref,
            title=title,
            author=author,
            url=html_url,
            metadata={
                "action": action,
                "pr_number": pr_number,
                "title": title,
                "body": body,
                "head_ref": head_ref,
                "base_ref": base_ref,
                "author": author,
                "repo_full_name": repo_full_name,
            },
        )

    def _parse_issue(
        self,
        payload: dict[str, Any],
        repo_name: str,
        repo_full_name: str,
    ) -> WebhookEvent | None:
        """Parse issues event."""
        action = payload.get("action", "")
        issue = payload.get("issue", {})

        # Only process opened or labeled
        if action not in ("opened", "labeled"):
            logger.debug(f"Ignoring issue action: {action}")
            return None

        issue_number = issue.get("number")
        title = issue.get("title", "")
        body = issue.get("body", "") or ""
        author = issue.get("user", {}).get("login", "unknown")
        html_url = issue.get("html_url", "")
        labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]

        prompt = self._default_prompt("issues", payload)

        return WebhookEvent(
            handler="github",
            event_type="issue",
            project_hint=repo_name,
            prompt=prompt,
            source_ref=f"issue-{issue_number}",
            title=title,
            author=author,
            url=html_url,
            metadata={
                "action": action,
                "issue_number": issue_number,
                "title": title,
                "body": body,
                "author": author,
                "labels": labels,
                "repo_full_name": repo_full_name,
            },
        )

    def _parse_issue_comment(
        self,
        payload: dict[str, Any],
        repo_name: str,
        repo_full_name: str,
    ) -> WebhookEvent | None:
        """Parse issue_comment event (comments on issues/PRs)."""
        action = payload.get("action", "")

        if action != "created":
            return None

        comment = payload.get("comment", {})
        issue = payload.get("issue", {})
        comment_body = comment.get("body", "") or ""
        comment_author = comment.get("user", {}).get("login", "unknown")

        # Check if this is a command comment (e.g., "/gluon fix this")
        if not comment_body.strip().startswith("/gluon"):
            logger.debug("Comment doesn't start with /gluon, ignoring")
            return None

        # Extract command from comment
        command = comment_body.strip()[7:].strip()  # Remove "/gluon " prefix
        if not command:
            return None

        issue_number = issue.get("number")
        is_pr = "pull_request" in issue
        title = issue.get("title", "")
        html_url = comment.get("html_url", "")

        context_type = "PR" if is_pr else "Issue"
        prompt = (
            f"Handle this request from {comment_author}: {command}\n\nContext: {context_type} #{issue_number} - {title}"
        )

        return WebhookEvent(
            handler="github",
            event_type="issue_comment",
            project_hint=repo_name,
            prompt=prompt,
            source_ref=f"{'pr' if is_pr else 'issue'}-{issue_number}",
            title=f"Comment on #{issue_number}",
            author=comment_author,
            url=html_url,
            metadata={
                "action": action,
                "issue_number": issue_number,
                "is_pr": is_pr,
                "comment_body": comment_body,
                "command": command,
                "comment_author": comment_author,
                "repo_full_name": repo_full_name,
            },
        )

    def _parse_pr_review(
        self,
        payload: dict[str, Any],
        repo_name: str,
        repo_full_name: str,
        event_type: str,
    ) -> WebhookEvent | None:
        """Parse pull_request_review or pull_request_review_comment event."""
        action = payload.get("action", "")

        if action != "submitted" and action != "created":
            return None

        pr = payload.get("pull_request", {})
        review = payload.get("review", {}) or payload.get("comment", {})
        review_body = review.get("body", "") or ""
        reviewer = review.get("user", {}).get("login", "unknown")

        # Only process if review requests changes or has a command
        review_state = review.get("state", "").lower()
        has_command = review_body.strip().startswith("/gluon")

        if review_state != "changes_requested" and not has_command:
            return None

        pr_number = pr.get("number")
        pr_title = pr.get("title", "")
        html_url = review.get("html_url", "") or pr.get("html_url", "")

        if has_command:
            command = review_body.strip()[7:].strip()
            prompt = f"Handle this review request from {reviewer}: {command}\n\nContext: PR #{pr_number} - {pr_title}"
        else:
            prompt = (
                f"Address the changes requested by {reviewer} in their review.\n\n"
                f"Review comment: {review_body[:500]}\n\n"
                f"Context: PR #{pr_number} - {pr_title}"
            )

        return WebhookEvent(
            handler="github",
            event_type=event_type,
            project_hint=repo_name,
            prompt=prompt,
            source_ref=f"pr-{pr_number}",
            title=f"Review on PR #{pr_number}",
            author=reviewer,
            url=html_url,
            metadata={
                "action": action,
                "pr_number": pr_number,
                "review_body": review_body,
                "review_state": review_state,
                "reviewer": reviewer,
                "repo_full_name": repo_full_name,
            },
        )

    def _default_prompt(self, event_type: str, payload: dict[str, Any]) -> str:
        """Generate default prompt for event type."""
        repo = payload.get("repository", {}).get("name", "unknown")

        if event_type == "push":
            branch = payload.get("ref", "").replace("refs/heads/", "")
            commits = payload.get("commits", [])
            commit_msgs = [c.get("message", "").split("\n")[0] for c in commits[:3]]
            msgs_str = "\n".join(f"- {msg}" for msg in commit_msgs)

            return f"""Review the following commits pushed to {branch}:

{msgs_str}

Analyze the changes, check for potential issues, and suggest improvements if needed."""

        elif event_type == "pull_request":
            pr = payload.get("pull_request", {})
            title = pr.get("title", "")
            body = (pr.get("body", "") or "")[:500]
            author = pr.get("user", {}).get("login", "unknown")

            return f"""Review Pull Request from {author}: {title}

Description:
{body}

Please:
1. Review the code changes for correctness and best practices
2. Check for potential bugs or issues
3. Suggest improvements if applicable"""

        elif event_type == "issues":
            issue = payload.get("issue", {})
            title = issue.get("title", "")
            body = (issue.get("body", "") or "")[:500]
            author = issue.get("user", {}).get("login", "unknown")

            return f"""Issue opened by {author}: {title}

{body}

Please analyze this issue and:
1. Identify the root cause if it's a bug
2. Propose a solution or implementation approach
3. Estimate complexity and potential impact"""

        return f"Process {event_type} event for {repo}"

    def _get_repo_name(self, payload: dict[str, Any]) -> str | None:
        return payload.get("repository", {}).get("name")

    def _get_branch(self, payload: dict[str, Any]) -> str | None:
        ref = payload.get("ref", "")
        if ref.startswith("refs/heads/"):
            return ref[11:]
        pr = payload.get("pull_request", {})
        if pr:
            return pr.get("head", {}).get("ref")
        return None

    def _get_author(self, payload: dict[str, Any]) -> str | None:
        # Try various sources
        if "pusher" in payload:
            return payload["pusher"].get("name")
        if "sender" in payload:
            return payload["sender"].get("login")
        if "pull_request" in payload:
            return payload["pull_request"].get("user", {}).get("login")
        if "issue" in payload:
            return payload["issue"].get("user", {}).get("login")
        return None

    def _get_title(self, payload: dict[str, Any]) -> str | None:
        if "pull_request" in payload:
            return payload["pull_request"].get("title")
        if "issue" in payload:
            return payload["issue"].get("title")
        return None

    def _get_body(self, payload: dict[str, Any]) -> str | None:
        if "pull_request" in payload:
            return payload["pull_request"].get("body")
        if "issue" in payload:
            return payload["issue"].get("body")
        if "comment" in payload:
            return payload["comment"].get("body")
        return None

    def _get_url(self, payload: dict[str, Any]) -> str | None:
        if "pull_request" in payload:
            return payload["pull_request"].get("html_url")
        if "issue" in payload:
            return payload["issue"].get("html_url")
        if "compare" in payload:
            return payload["compare"]
        return None
