"""Queue module for distributed job execution."""

from gluon.queue.redis_queue import RedisJobQueue

__all__ = ["RedisJobQueue"]
