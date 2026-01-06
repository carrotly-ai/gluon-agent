"""Tests for distributed worker models and store methods."""

from datetime import UTC, datetime, timedelta

import pytest

from gluon.models import (
    Job,
    JobStatus,
    WebhookConfig,
    Worker,
    WorkerStatus,
    WorkerType,
    utc_now,
)
from gluon.store import GluonStore


class TestWorkerModel:
    """Tests for Worker model."""

    def test_worker_creation(self):
        """Test creating a worker with defaults."""
        worker = Worker(
            name="local-worker",
            api_key="test-api-key",
        )

        assert worker.id is not None
        assert worker.name == "local-worker"
        assert worker.type == WorkerType.LOCAL
        assert worker.max_concurrent == 4
        assert worker.status == WorkerStatus.HEALTHY
        assert worker.base_url is None
        assert worker.active_jobs == 0

    def test_worker_remote(self):
        """Test creating a remote worker."""
        worker = Worker(
            name="remote-1",
            type=WorkerType.REMOTE,
            base_url="http://worker1:8080",
            api_key="api-key",
            max_concurrent=8,
        )

        assert worker.type == WorkerType.REMOTE
        assert worker.base_url == "http://worker1:8080"
        assert worker.max_concurrent == 8

    def test_worker_is_available(self):
        """Test worker availability check."""
        worker = Worker(name="test", api_key="key")
        assert worker.is_available is True

        worker.active_jobs = 4  # At max capacity
        assert worker.is_available is False

        worker.active_jobs = 3
        assert worker.is_available is True

        worker.status = WorkerStatus.UNHEALTHY
        assert worker.is_available is False

    def test_worker_available_slots(self):
        """Test available slots calculation."""
        worker = Worker(name="test", api_key="key", max_concurrent=4)

        assert worker.available_slots == 4

        worker.active_jobs = 2
        assert worker.available_slots == 2

        worker.active_jobs = 4
        assert worker.available_slots == 0

        worker.status = WorkerStatus.OFFLINE
        assert worker.available_slots == 0

    def test_worker_mark_healthy(self):
        """Test marking worker healthy."""
        worker = Worker(name="test", api_key="key", status=WorkerStatus.UNHEALTHY)

        worker.mark_healthy()

        assert worker.status == WorkerStatus.HEALTHY
        assert worker.last_heartbeat is not None

    def test_worker_mark_unhealthy(self):
        """Test marking worker unhealthy."""
        worker = Worker(name="test", api_key="key")

        worker.mark_unhealthy()

        assert worker.status == WorkerStatus.UNHEALTHY

    def test_worker_mark_offline(self):
        """Test marking worker offline."""
        worker = Worker(name="test", api_key="key")

        worker.mark_offline()

        assert worker.status == WorkerStatus.OFFLINE


class TestJobModel:
    """Tests for Job model."""

    def test_job_creation(self):
        """Test creating a job with defaults."""
        job = Job(
            run_id="run-123",
            project_id="project-456",
            prompt="Fix the bug",
        )

        assert job.id is not None
        assert job.run_id == "run-123"
        assert job.project_id == "project-456"
        assert job.prompt == "Fix the bug"
        assert job.priority == 5
        assert job.status == JobStatus.QUEUED
        assert job.worker_id is None

    def test_job_assign_to_worker(self):
        """Test assigning job to worker."""
        job = Job(run_id="r1", project_id="p1", prompt="test")

        job.assign_to_worker("worker-1", lease_seconds=300)

        assert job.worker_id == "worker-1"
        assert job.status == JobStatus.ASSIGNED
        assert job.assigned_at is not None
        assert job.lease_expires_at is not None
        # Lease should be ~5 minutes from now
        assert job.lease_expires_at > utc_now()

    def test_job_mark_running(self):
        """Test marking job as running."""
        job = Job(run_id="r1", project_id="p1", prompt="test")

        job.mark_running()

        assert job.status == JobStatus.RUNNING
        assert job.started_at is not None

    def test_job_mark_completed(self):
        """Test marking job as completed."""
        job = Job(run_id="r1", project_id="p1", prompt="test")
        job.assign_to_worker("w1")
        job.mark_running()

        job.mark_completed()

        assert job.status == JobStatus.COMPLETED
        assert job.completed_at is not None
        assert job.lease_expires_at is None

    def test_job_mark_failed(self):
        """Test marking job as failed."""
        job = Job(run_id="r1", project_id="p1", prompt="test")
        job.mark_running()

        job.mark_failed("Connection timeout")

        assert job.status == JobStatus.FAILED
        assert job.error_message == "Connection timeout"
        assert job.completed_at is not None

    def test_job_release_lease(self):
        """Test releasing job lease."""
        job = Job(run_id="r1", project_id="p1", prompt="test")
        job.assign_to_worker("w1")

        job.release_lease()

        assert job.worker_id is None
        assert job.status == JobStatus.QUEUED
        assert job.assigned_at is None
        assert job.lease_expires_at is None

    def test_job_is_lease_expired(self):
        """Test lease expiration check."""
        job = Job(run_id="r1", project_id="p1", prompt="test")

        # No lease set
        assert job.is_lease_expired is False

        # Set expired lease
        job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=5)
        assert job.is_lease_expired is True

        # Set future lease
        job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        assert job.is_lease_expired is False


class TestWebhookConfigModel:
    """Tests for WebhookConfig model."""

    def test_webhook_config_creation(self):
        """Test creating a webhook config."""
        config = WebhookConfig(
            handler="github",
            secret_key="secret-123",
        )

        assert config.id is not None
        assert config.handler == "github"
        assert config.secret_key == "secret-123"
        assert config.enabled is True
        assert config.events == []
        assert config.project_id is None

    def test_webhook_config_with_filters(self):
        """Test webhook config with filters."""
        config = WebhookConfig(
            handler="github",
            secret_key="secret",
            project_id="project-1",
            events=["push", "pull_request"],
            branches=["main", "develop"],
            ignore_branches=["dependabot/*"],
        )

        assert config.project_id == "project-1"
        assert config.events == ["push", "pull_request"]
        assert config.branches == ["main", "develop"]
        assert config.ignore_branches == ["dependabot/*"]

    def test_webhook_matches_branch(self):
        """Test branch matching."""
        config = WebhookConfig(
            handler="github",
            secret_key="secret",
            branches=["main", "develop"],
            ignore_branches=["dependabot/*"],
        )

        assert config.matches_branch("main") is True
        assert config.matches_branch("develop") is True
        assert config.matches_branch("feature") is False

    def test_webhook_matches_branch_with_ignore(self):
        """Test branch matching with ignore list."""
        config = WebhookConfig(
            handler="github",
            secret_key="secret",
            ignore_branches=["main"],
        )

        assert config.matches_branch("main") is False
        assert config.matches_branch("develop") is True

    def test_webhook_matches_branch_no_filter(self):
        """Test branch matching with no filter (all branches)."""
        config = WebhookConfig(
            handler="github",
            secret_key="secret",
        )

        assert config.matches_branch("main") is True
        assert config.matches_branch("any-branch") is True

    def test_webhook_matches_event(self):
        """Test event matching."""
        config = WebhookConfig(
            handler="github",
            secret_key="secret",
            events=["push", "pull_request"],
        )

        assert config.matches_event("push") is True
        assert config.matches_event("pull_request") is True
        assert config.matches_event("issues") is False

    def test_webhook_matches_event_no_filter(self):
        """Test event matching with no filter (all events)."""
        config = WebhookConfig(
            handler="github",
            secret_key="secret",
        )

        assert config.matches_event("push") is True
        assert config.matches_event("anything") is True


class TestWorkerStore:
    """Tests for worker store methods."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a test store."""
        store = GluonStore(db_path=tmp_path / "test.db")
        store._init_db()
        return store

    def test_create_worker(self, store):
        """Test creating a worker in store."""
        worker = Worker(name="test-worker", api_key="key123")
        created = store.create_worker(worker)

        assert created.id == worker.id
        assert created.name == "test-worker"

    def test_get_worker(self, store):
        """Test retrieving a worker."""
        worker = Worker(name="test", api_key="key")
        store.create_worker(worker)

        retrieved = store.get_worker(worker.id)

        assert retrieved is not None
        assert retrieved.id == worker.id
        assert retrieved.name == "test"

    def test_get_worker_by_name(self, store):
        """Test retrieving worker by name."""
        worker = Worker(name="unique-name", api_key="key")
        store.create_worker(worker)

        retrieved = store.get_worker_by_name("unique-name")

        assert retrieved is not None
        assert retrieved.id == worker.id

    def test_list_workers(self, store):
        """Test listing all workers."""
        store.create_worker(Worker(name="w1", api_key="k1"))
        store.create_worker(Worker(name="w2", api_key="k2"))

        workers = store.list_workers()

        assert len(workers) == 2

    def test_get_healthy_workers(self, store):
        """Test listing healthy workers."""
        store.create_worker(Worker(name="healthy", api_key="k1", status=WorkerStatus.HEALTHY))
        unhealthy = Worker(name="unhealthy", api_key="k2", status=WorkerStatus.UNHEALTHY)
        store.create_worker(unhealthy)

        healthy = store.get_healthy_workers()

        assert len(healthy) == 1
        assert healthy[0].name == "healthy"

    def test_update_worker(self, store):
        """Test updating a worker."""
        worker = Worker(name="test", api_key="key")
        store.create_worker(worker)

        worker.max_concurrent = 8
        worker.status = WorkerStatus.UNHEALTHY
        updated = store.update_worker(worker)

        assert updated is not None
        assert updated.max_concurrent == 8
        assert updated.status == WorkerStatus.UNHEALTHY

    def test_delete_worker(self, store):
        """Test deleting a worker."""
        worker = Worker(name="test", api_key="key")
        store.create_worker(worker)

        result = store.delete_worker(worker.id)

        assert result is True
        assert store.get_worker(worker.id) is None


class TestJobStore:
    """Tests for job store methods."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a test store."""
        store = GluonStore(db_path=tmp_path / "test.db")
        store._init_db()
        return store

    @pytest.fixture
    def project_and_run(self, store):
        """Create a project and execution run for FK constraints."""
        from pathlib import Path

        store.create_project(name="test-project", path=Path("/tmp/test"))
        # Get the created project to get its actual ID
        project = store.get_project_by_name("test-project")

        run = store.create_run(project_id=project.id, prompt="test run")

        return project, run

    def test_create_job(self, store, project_and_run):
        """Test creating a job in store."""
        project, run = project_and_run
        job = Job(run_id=run.id, project_id=project.id, prompt="test")
        created = store.create_job(job)

        assert created.id == job.id

    def test_get_job(self, store, project_and_run):
        """Test retrieving a job."""
        project, run = project_and_run
        job = Job(run_id=run.id, project_id=project.id, prompt="test")
        store.create_job(job)

        retrieved = store.get_job(job.id)

        assert retrieved is not None
        assert retrieved.prompt == "test"

    def test_get_job_by_run_id(self, store, project_and_run):
        """Test retrieving job by run ID."""
        project, run = project_and_run
        job = Job(run_id=run.id, project_id=project.id, prompt="test")
        store.create_job(job)

        retrieved = store.get_job_by_run_id(run.id)

        assert retrieved is not None
        assert retrieved.id == job.id

    def test_list_jobs(self, store, project_and_run):
        """Test listing jobs."""
        project, run = project_and_run

        # Create additional runs for more jobs
        run2 = store.create_run(project_id=project.id, prompt="test run 2")
        run3 = store.create_run(project_id=project.id, prompt="test run 3")

        store.create_job(Job(run_id=run.id, project_id=project.id, prompt="t1"))
        store.create_job(Job(run_id=run2.id, project_id=project.id, prompt="t2"))
        store.create_job(Job(run_id=run3.id, project_id=project.id, prompt="t3"))

        # All jobs
        all_jobs = store.list_jobs()
        assert len(all_jobs) == 3

        # Filter by limit
        limited_jobs = store.list_jobs(limit=2)
        assert len(limited_jobs) == 2

    def test_list_queued_jobs(self, store, project_and_run):
        """Test listing queued jobs."""
        project, run = project_and_run

        # Create additional runs
        run2 = store.create_run(project_id=project.id, prompt="run 2")
        run3 = store.create_run(project_id=project.id, prompt="run 3")

        j1 = Job(run_id=run.id, project_id=project.id, prompt="t1", priority=3)
        j2 = Job(run_id=run2.id, project_id=project.id, prompt="t2", priority=1)  # Higher priority
        j3 = Job(run_id=run3.id, project_id=project.id, prompt="t3", priority=5)

        store.create_job(j1)
        store.create_job(j2)
        store.create_job(j3)

        queued = store.list_queued_jobs(limit=2)

        assert len(queued) == 2
        # Should be ordered by priority (lower = higher priority)
        assert queued[0].priority == 1

    def test_update_job(self, store, project_and_run):
        """Test updating a job."""
        project, run = project_and_run
        job = Job(run_id=run.id, project_id=project.id, prompt="test")
        store.create_job(job)

        # Create a worker to assign the job to
        worker = Worker(name="test-worker", api_key="key")
        store.create_worker(worker)

        job.status = JobStatus.RUNNING
        job.worker_id = worker.id
        store.update_job(job)

        # Fetch updated job
        updated = store.get_job(job.id)
        assert updated is not None
        assert updated.status == JobStatus.RUNNING
        assert updated.worker_id == worker.id

    def test_delete_job(self, store, project_and_run):
        """Test deleting a job."""
        project, run = project_and_run
        job = Job(run_id=run.id, project_id=project.id, prompt="test")
        store.create_job(job)

        result = store.delete_job(job.id)

        assert result is True
        assert store.get_job(job.id) is None


class TestWebhookConfigStore:
    """Tests for webhook config store methods."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a test store."""
        store = GluonStore(db_path=tmp_path / "test.db")
        store._init_db()
        return store

    def test_create_webhook_config(self, store):
        """Test creating a webhook config."""
        config = WebhookConfig(handler="github", secret_key="secret")
        created = store.create_webhook_config(config)

        assert created.id == config.id

    def test_get_webhook_config(self, store):
        """Test retrieving a webhook config."""
        config = WebhookConfig(handler="github", secret_key="secret")
        store.create_webhook_config(config)

        retrieved = store.get_webhook_config(config.id)

        assert retrieved is not None
        assert retrieved.handler == "github"

    def test_list_webhook_configs(self, store):
        """Test listing webhook configs."""
        store.create_webhook_config(WebhookConfig(handler="github", secret_key="s1"))
        disabled = WebhookConfig(handler="gitlab", secret_key="s2", enabled=False)
        store.create_webhook_config(disabled)

        # All configs
        all_configs = store.list_webhook_configs(enabled_only=False)
        assert len(all_configs) == 2

        # Only enabled
        enabled = store.list_webhook_configs(enabled_only=True)
        assert len(enabled) == 1

    def test_get_webhook_configs_for_handler(self, store):
        """Test getting configs for a specific handler."""
        store.create_webhook_config(WebhookConfig(handler="github", secret_key="s1"))
        store.create_webhook_config(WebhookConfig(handler="github", secret_key="s2"))
        store.create_webhook_config(WebhookConfig(handler="gitlab", secret_key="s3"))

        github_configs = store.get_webhook_configs_for_handler("github")

        assert len(github_configs) == 2

    def test_update_webhook_config(self, store):
        """Test updating a webhook config."""
        config = WebhookConfig(handler="github", secret_key="secret")
        store.create_webhook_config(config)

        config.enabled = False
        config.events = ["push"]
        updated = store.update_webhook_config(config)

        assert updated is not None
        assert updated.enabled is False
        assert updated.events == ["push"]

    def test_delete_webhook_config(self, store):
        """Test deleting a webhook config."""
        config = WebhookConfig(handler="github", secret_key="secret")
        store.create_webhook_config(config)

        result = store.delete_webhook_config(config.id)

        assert result is True
        assert store.get_webhook_config(config.id) is None
