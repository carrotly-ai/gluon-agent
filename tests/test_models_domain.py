"""Tests for domain models: GitStatus, Worker, Job, WebhookConfig, GitSyncResult."""

from datetime import timedelta

from gluon.models import (
    GitStatus,
    GitSyncResult,
    Job,
    JobStatus,
    WebhookConfig,
    Worker,
    WorkerStatus,
    utc_now,
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


# ========== Worker Methods ==========


class TestWorkerIsAvailable:
    def test_true_when_healthy_and_has_slots(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.HEALTHY, active_jobs=0, max_concurrent=4)
        assert w.is_available is True

    def test_false_when_unhealthy(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.UNHEALTHY, active_jobs=0)
        assert w.is_available is False

    def test_false_when_offline(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.OFFLINE, active_jobs=0)
        assert w.is_available is False

    def test_false_at_max_capacity(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.HEALTHY, active_jobs=4, max_concurrent=4)
        assert w.is_available is False


class TestWorkerAvailableSlots:
    def test_returns_zero_when_not_healthy(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.UNHEALTHY, max_concurrent=4)
        assert w.available_slots == 0

    def test_calculates_remaining(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.HEALTHY, active_jobs=1, max_concurrent=4)
        assert w.available_slots == 3

    def test_zero_at_capacity(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.HEALTHY, active_jobs=4, max_concurrent=4)
        assert w.available_slots == 0


class TestWorkerMarkMethods:
    def test_mark_healthy(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.UNHEALTHY)
        w.mark_healthy()
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_heartbeat is not None

    def test_mark_unhealthy(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.HEALTHY)
        w.mark_unhealthy()
        assert w.status == WorkerStatus.UNHEALTHY

    def test_mark_offline(self):
        w = Worker(name="w1", api_key="k", status=WorkerStatus.HEALTHY)
        w.mark_offline()
        assert w.status == WorkerStatus.OFFLINE


# ========== Job Methods ==========


class TestJobAssignToWorker:
    def test_sets_worker_id_and_status(self):
        j = Job(run_id="r1", project_id="p1", prompt="test")
        j.assign_to_worker("worker-1", lease_seconds=300)
        assert j.worker_id == "worker-1"
        assert j.status == JobStatus.ASSIGNED
        assert j.assigned_at is not None
        assert j.lease_expires_at is not None

    def test_lease_expires_in_future(self):
        j = Job(run_id="r1", project_id="p1", prompt="test")
        j.assign_to_worker("worker-1", lease_seconds=600)
        assert j.lease_expires_at > utc_now()


class TestJobMarkRunning:
    def test_sets_status_and_started_at(self):
        j = Job(run_id="r1", project_id="p1", prompt="test")
        j.mark_running()
        assert j.status == JobStatus.RUNNING
        assert j.started_at is not None


class TestJobMarkCompleted:
    def test_sets_status_and_clears_lease(self):
        j = Job(run_id="r1", project_id="p1", prompt="test")
        j.assign_to_worker("w1")
        j.mark_completed()
        assert j.status == JobStatus.COMPLETED
        assert j.completed_at is not None
        assert j.lease_expires_at is None


class TestJobMarkFailed:
    def test_sets_error_and_clears_lease(self):
        j = Job(run_id="r1", project_id="p1", prompt="test")
        j.assign_to_worker("w1")
        j.mark_failed("timeout")
        assert j.status == JobStatus.FAILED
        assert j.error_message == "timeout"
        assert j.lease_expires_at is None


class TestJobReleaseLease:
    def test_resets_to_queued(self):
        j = Job(run_id="r1", project_id="p1", prompt="test")
        j.assign_to_worker("w1")
        j.release_lease()
        assert j.status == JobStatus.QUEUED
        assert j.worker_id is None
        assert j.assigned_at is None
        assert j.lease_expires_at is None


class TestJobIsLeaseExpired:
    def test_false_when_no_lease(self):
        j = Job(run_id="r1", project_id="p1", prompt="test")
        assert j.is_lease_expired is False

    def test_true_when_past_expiry(self):
        j = Job(
            run_id="r1",
            project_id="p1",
            prompt="test",
            lease_expires_at=utc_now() - timedelta(seconds=10),
        )
        assert j.is_lease_expired is True

    def test_false_when_before_expiry(self):
        j = Job(
            run_id="r1",
            project_id="p1",
            prompt="test",
            lease_expires_at=utc_now() + timedelta(seconds=300),
        )
        assert j.is_lease_expired is False


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
