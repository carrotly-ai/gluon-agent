"""Tests for domain models: GitStatus, WebhookConfig, GitSyncResult."""

from gluon.models import (
    GitStatus,
    GitSyncResult,
    WebhookConfig,
)

# ========== GitStatus Properties ==========


class TestGitStatusIsDiverged:
    def test_true_when_both_ahead_and_behind(self):
        gs = GitStatus(commits_ahead=2, commits_behind=3)
        assert gs.is_diverged is True

    def test_false_when_only_ahead(self):
        gs = GitStatus(commits_ahead=2, commits_behind=0)
        assert gs.is_diverged is False

    def test_false_when_only_behind(self):
        gs = GitStatus(commits_ahead=0, commits_behind=3)
        assert gs.is_diverged is False

    def test_false_when_both_zero(self):
        gs = GitStatus(commits_ahead=0, commits_behind=0)
        assert gs.is_diverged is False


class TestGitStatusIsClean:
    def test_true_when_all_clean(self):
        gs = GitStatus(
            has_uncommitted=False,
            commits_ahead=0,
            commits_behind=0,
            conflicted_files=[],
        )
        assert gs.is_clean is True

    def test_false_when_has_uncommitted(self):
        gs = GitStatus(has_uncommitted=True)
        assert gs.is_clean is False

    def test_false_when_ahead(self):
        gs = GitStatus(commits_ahead=1)
        assert gs.is_clean is False

    def test_false_when_behind(self):
        gs = GitStatus(commits_behind=1)
        assert gs.is_clean is False

    def test_false_when_conflicted_files(self):
        gs = GitStatus(conflicted_files=["file.py"])
        assert gs.is_clean is False


class TestGitStatusNeedsPull:
    def test_true_when_behind_only(self):
        gs = GitStatus(commits_behind=3, commits_ahead=0)
        assert gs.needs_pull is True

    def test_false_when_diverged(self):
        gs = GitStatus(commits_behind=3, commits_ahead=1)
        assert gs.needs_pull is False

    def test_false_when_not_behind(self):
        gs = GitStatus(commits_behind=0, commits_ahead=0)
        assert gs.needs_pull is False


class TestGitStatusNeedsPush:
    def test_true_when_ahead_only(self):
        gs = GitStatus(commits_ahead=2, commits_behind=0)
        assert gs.needs_push is True

    def test_false_when_diverged(self):
        gs = GitStatus(commits_ahead=2, commits_behind=1)
        assert gs.needs_push is False

    def test_false_when_not_ahead(self):
        gs = GitStatus(commits_ahead=0, commits_behind=0)
        assert gs.needs_push is False


class TestGitStatusHasOperationInProgress:
    def test_true_when_rebase(self):
        gs = GitStatus(is_rebase_in_progress=True)
        assert gs.has_operation_in_progress is True

    def test_true_when_merge(self):
        gs = GitStatus(is_merge_in_progress=True)
        assert gs.has_operation_in_progress is True

    def test_false_when_neither(self):
        gs = GitStatus()
        assert gs.has_operation_in_progress is False


# ========== WebhookConfig ==========


class TestWebhookConfigMatchesBranch:
    def test_allows_all_when_branches_none(self):
        wh = WebhookConfig(handler="github", secret_key="s")
        assert wh.matches_branch("main") is True
        assert wh.matches_branch("dev") is True

    def test_false_when_in_ignore_branches(self):
        wh = WebhookConfig(handler="github", secret_key="s", ignore_branches=["staging"])
        assert wh.matches_branch("staging") is False

    def test_ignore_branches_takes_precedence(self):
        wh = WebhookConfig(
            handler="github",
            secret_key="s",
            branches=["main", "staging"],
            ignore_branches=["staging"],
        )
        assert wh.matches_branch("staging") is False

    def test_false_when_not_in_branches_list(self):
        wh = WebhookConfig(handler="github", secret_key="s", branches=["main"])
        assert wh.matches_branch("dev") is False

    def test_true_when_in_branches_list(self):
        wh = WebhookConfig(handler="github", secret_key="s", branches=["main", "dev"])
        assert wh.matches_branch("dev") is True


class TestWebhookConfigMatchesEvent:
    def test_allows_all_when_events_empty(self):
        wh = WebhookConfig(handler="github", secret_key="s", events=[])
        assert wh.matches_event("push") is True

    def test_false_when_not_in_events(self):
        wh = WebhookConfig(handler="github", secret_key="s", events=["pull_request"])
        assert wh.matches_event("push") is False

    def test_true_when_in_events(self):
        wh = WebhookConfig(handler="github", secret_key="s", events=["push", "pull_request"])
        assert wh.matches_event("push") is True


# ========== GitSyncResult ==========


class TestGitSyncResult:
    def test_ok_returns_success(self):
        r = GitSyncResult.ok("push", "Pushed 3 commits")
        assert r.success is True
        assert r.action == "push"
        assert r.message == "Pushed 3 commits"

    def test_fail_returns_failure(self):
        r = GitSyncResult.fail("auth failed")
        assert r.success is False
        assert r.error == "auth failed"
        assert "auth failed" in r.message

    def test_skip_returns_success_with_none_action(self):
        r = GitSyncResult.skip("Not a git repo")
        assert r.success is True
        assert r.action == "none"
