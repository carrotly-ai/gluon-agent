"""Redis-based job queue for distributed task execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from gluon.models import Job, JobStatus, utc_now

logger = logging.getLogger(__name__)

# Redis key prefixes
KEY_QUEUE = "gluon:jobs:queue"  # Sorted set by priority
KEY_JOB = "gluon:job:"  # Hash per job: gluon:job:{job_id}
KEY_WORKER_JOBS = "gluon:worker:"  # Set per worker: gluon:worker:{worker_id}:jobs
KEY_CHANNEL = "gluon:jobs:updates"  # Pub/sub channel for job updates


def get_redis_url() -> str:
    """Get Redis URL from environment or default."""
    return os.environ.get("GLUON_REDIS_URL", "redis://localhost:6379/0")


class RedisJobQueue:
    """Redis-backed job queue with priority ordering and worker leases."""

    def __init__(self, redis_url: str | None = None):
        """Initialize the queue.

        Args:
            redis_url: Redis connection URL. Defaults to GLUON_REDIS_URL env var
                      or redis://localhost:6379/0
        """
        self.redis_url = redis_url or get_redis_url()
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._listener_task: asyncio.Task | None = None
        self._update_handlers: list[Callable[[dict[str, Any]], Any]] = []

    async def connect(self) -> None:
        """Connect to Redis."""
        if self._redis is None:
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # Test connection
            await self._redis.ping()
            logger.info(f"Connected to Redis at {self.redis_url}")

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.close()
            self._pubsub = None

        if self._redis:
            await self._redis.close()
            self._redis = None
            logger.info("Disconnected from Redis")

    @property
    def redis(self) -> redis.Redis:
        """Get Redis client, raising if not connected."""
        if self._redis is None:
            raise RuntimeError("Not connected to Redis. Call connect() first.")
        return self._redis

    async def enqueue(self, job: Job) -> None:
        """Add a job to the queue.

        Jobs are stored in a sorted set ordered by priority (lower = higher priority).
        Job data is stored in a hash.

        Args:
            job: Job to enqueue
        """
        job.status = JobStatus.QUEUED
        job_data = self._serialize_job(job)

        # Use pipeline for atomicity
        async with self.redis.pipeline(transaction=True) as pipe:
            # Store job data
            await pipe.hset(f"{KEY_JOB}{job.id}", mapping=job_data)
            # Add to priority queue (lower score = higher priority)
            await pipe.zadd(KEY_QUEUE, {job.id: job.priority})
            await pipe.execute()

        logger.info(f"Enqueued job {job.id} with priority {job.priority}")
        await self._publish_update("job_queued", job)

    async def dequeue(self, worker_id: str, count: int = 1, lease_seconds: int = 300) -> list[Job]:
        """Dequeue jobs for a worker with a lease.

        Uses ZPOPMIN to atomically get highest priority jobs.
        Sets a lease expiration for fault tolerance.

        Args:
            worker_id: ID of the worker claiming jobs
            count: Maximum number of jobs to dequeue
            lease_seconds: Lease duration in seconds

        Returns:
            List of claimed jobs
        """
        jobs: list[Job] = []

        # Get job IDs from priority queue
        results = await self.redis.zpopmin(KEY_QUEUE, count)
        if not results:
            return jobs

        for job_id, _score in results:
            job_data = await self.redis.hgetall(f"{KEY_JOB}{job_id}")
            if not job_data:
                logger.warning(f"Job {job_id} in queue but no data found")
                continue

            job = self._deserialize_job(job_data)
            job.assign_to_worker(worker_id, lease_seconds)

            # Update job in Redis
            await self.redis.hset(f"{KEY_JOB}{job.id}", mapping=self._serialize_job(job))
            # Track worker's jobs
            await self.redis.sadd(f"{KEY_WORKER_JOBS}{worker_id}:jobs", job.id)

            jobs.append(job)
            logger.info(f"Worker {worker_id} claimed job {job.id}")
            await self._publish_update("job_assigned", job)

        return jobs

    async def update_status(self, job_id: str, status: JobStatus, error: str | None = None) -> Job | None:
        """Update job status.

        Args:
            job_id: Job ID
            status: New status
            error: Error message if failed

        Returns:
            Updated job or None if not found
        """
        job_data = await self.redis.hgetall(f"{KEY_JOB}{job_id}")
        if not job_data:
            return None

        job = self._deserialize_job(job_data)

        if status == JobStatus.RUNNING:
            job.mark_running()
        elif status == JobStatus.COMPLETED:
            job.mark_completed()
            # Clean up worker tracking
            if job.worker_id:
                await self.redis.srem(f"{KEY_WORKER_JOBS}{job.worker_id}:jobs", job.id)
        elif status == JobStatus.FAILED:
            job.mark_failed(error or "Unknown error")
            if job.worker_id:
                await self.redis.srem(f"{KEY_WORKER_JOBS}{job.worker_id}:jobs", job.id)

        await self.redis.hset(f"{KEY_JOB}{job.id}", mapping=self._serialize_job(job))
        logger.info(f"Job {job_id} status updated to {status}")
        await self._publish_update(f"job_{status.value}", job)

        return job

    async def release_job(self, job_id: str) -> Job | None:
        """Release a job back to the queue (e.g., worker died).

        Args:
            job_id: Job ID to release

        Returns:
            Released job or None if not found
        """
        job_data = await self.redis.hgetall(f"{KEY_JOB}{job_id}")
        if not job_data:
            return None

        job = self._deserialize_job(job_data)
        worker_id = job.worker_id
        job.release_lease()

        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.hset(f"{KEY_JOB}{job.id}", mapping=self._serialize_job(job))
            await pipe.zadd(KEY_QUEUE, {job.id: job.priority})
            if worker_id:
                await pipe.srem(f"{KEY_WORKER_JOBS}{worker_id}:jobs", job.id)
            await pipe.execute()

        logger.info(f"Job {job_id} released back to queue")
        await self._publish_update("job_released", job)

        return job

    async def get_job(self, job_id: str) -> Job | None:
        """Get a job by ID.

        Args:
            job_id: Job ID

        Returns:
            Job or None if not found
        """
        job_data = await self.redis.hgetall(f"{KEY_JOB}{job_id}")
        if not job_data:
            return None
        return self._deserialize_job(job_data)

    async def get_queue_size(self) -> int:
        """Get number of jobs waiting in queue."""
        return await self.redis.zcard(KEY_QUEUE)

    async def get_worker_jobs(self, worker_id: str) -> list[Job]:
        """Get all jobs assigned to a worker.

        Args:
            worker_id: Worker ID

        Returns:
            List of jobs
        """
        job_ids = await self.redis.smembers(f"{KEY_WORKER_JOBS}{worker_id}:jobs")
        jobs = []
        for job_id in job_ids:
            job = await self.get_job(job_id)
            if job:
                jobs.append(job)
        return jobs

    async def recover_expired_leases(self) -> int:
        """Find and release jobs with expired leases.

        Returns:
            Number of jobs recovered
        """
        # Get all job keys
        cursor = 0
        recovered = 0

        while True:
            cursor, keys = await self.redis.scan(cursor, match=f"{KEY_JOB}*", count=100)

            for key in keys:
                job_data = await self.redis.hgetall(key)
                if not job_data:
                    continue

                job = self._deserialize_job(job_data)
                if job.is_lease_expired and job.status in (JobStatus.ASSIGNED, JobStatus.RUNNING):
                    logger.warning(f"Recovering job {job.id} with expired lease")
                    await self.release_job(job.id)
                    recovered += 1

            if cursor == 0:
                break

        if recovered > 0:
            logger.info(f"Recovered {recovered} jobs with expired leases")

        return recovered

    async def subscribe_updates(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        """Subscribe to job update events.

        Args:
            handler: Async callable to handle update events
        """
        self._update_handlers.append(handler)

        if self._pubsub is None:
            self._pubsub = self.redis.pubsub()
            await self._pubsub.subscribe(KEY_CHANNEL)
            self._listener_task = asyncio.create_task(self._listen_updates())

    async def _listen_updates(self) -> None:
        """Background task to listen for pub/sub updates."""
        if self._pubsub is None:
            return

        try:
            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    for handler in self._update_handlers:
                        try:
                            result = handler(data)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"Error in update handler: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in pub/sub listener: {e}")

    async def _publish_update(self, event_type: str, job: Job) -> None:
        """Publish a job update event.

        Args:
            event_type: Type of event (job_queued, job_assigned, etc.)
            job: Job that was updated
        """
        update = {
            "type": event_type,
            "job_id": job.id,
            "run_id": job.run_id,
            "status": job.status.value,
            "worker_id": job.worker_id,
            "timestamp": utc_now().isoformat(),
        }
        await self.redis.publish(KEY_CHANNEL, json.dumps(update))

    def _serialize_job(self, job: Job) -> dict[str, str]:
        """Serialize job for Redis hash storage."""
        data = job.model_dump(mode="json")
        # Convert nested objects to JSON strings
        result: dict[str, str] = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                result[k] = json.dumps(v)
            elif v is not None:
                result[k] = str(v)
            else:
                result[k] = ""
        return result

    def _deserialize_job(self, data: dict[str, str]) -> Job:
        """Deserialize job from Redis hash."""
        # Parse JSON strings back to objects
        parsed: dict[str, Any] = {}
        for k, v in data.items():
            if v == "":
                parsed[k] = None
            elif v == "True":
                parsed[k] = True
            elif v == "False":
                parsed[k] = False
            elif k in ("priority",):
                parsed[k] = int(v)
            elif k in ("created_at", "assigned_at", "started_at", "completed_at", "lease_expires_at"):
                parsed[k] = datetime.fromisoformat(v) if v else None
            elif k == "status":
                parsed[k] = JobStatus(v)
            else:
                parsed[k] = v

        return Job(**parsed)


# Singleton instance for convenience
_queue_instance: RedisJobQueue | None = None


def get_queue() -> RedisJobQueue:
    """Get or create the global queue instance."""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = RedisJobQueue()
    return _queue_instance
