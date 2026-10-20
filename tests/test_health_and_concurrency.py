"""Tests for the container liveness endpoint and the concurrency cap default.

Both back the post-incident containment work (a runaway fan-out of concurrent
agents wedged the host VM): the health endpoint feeds the compose healthcheck,
and GLUON_MAX_CONCURRENT_RUNS bounds how many runs execute at once.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from contextlib import closing
from unittest.mock import patch

import httpx
import pytest


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")


def test_health_endpoint_ok(api_client) -> None:
    """/api/health returns 200 {"status": "ok"} for the container healthcheck."""
    client, _ = api_client
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_endpoint_reachable_without_auth(api_client, auth_enabled) -> None:
    """With the fail-closed auth gate ON, the unauthenticated probe must still
    reach /api/health (200), or the container healthcheck would flap unhealthy."""
    client, _ = api_client
    resp = client.get("/api/health")  # no session cookie
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("enabled", [False, True])
def test_readiness_endpoint_ok(api_client, monkeypatch, enabled) -> None:
    monkeypatch.setenv("GLUON_AUTH_ENABLED", str(enabled).lower())
    client, _ = api_client
    resp = client.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("failure", ["missing", "corrupt", "directory"])
def test_readiness_failure_preserves_liveness(api_client, temp_store, auth_enabled, failure) -> None:
    client, _ = api_client
    if failure == "missing":
        temp_store.db_path.rename(temp_store.db_path.with_suffix(".offline"))
    elif failure == "corrupt":
        temp_store.db_path.write_bytes(b"not a SQLite database")
    else:
        temp_store.db_path = temp_store.db_path.parent

    resp = client.get("/api/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "error": "database_unavailable"}
    assert client.get("/api/health").json() == {"status": "ok"}
    if failure == "missing":
        assert not temp_store.db_path.exists()
    # Only the exact probe path is anonymous.
    assert client.get("/api/ready/details").status_code == 401


def test_readiness_returns_unavailable_while_database_locked(api_client, temp_store, auth_enabled) -> None:
    client, _ = api_client
    with closing(sqlite3.connect(temp_store.db_path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        assert client.get("/api/ready").status_code == 503
        assert client.get("/api/health").status_code == 200
        writer.rollback()
    assert client.get("/api/ready").status_code == 200


async def test_readiness_deadline_bounds_workers_and_keeps_event_loop_responsive(
    api_client, temp_store, monkeypatch
) -> None:
    from gluon.web.routers import system

    client, _ = api_client
    started = threading.Event()
    release = threading.Event()
    connect = sqlite3.connect
    connection_attempts = 0

    def stalled_connection(*args, **kwargs):
        nonlocal connection_attempts
        connection_attempts += 1
        started.set()
        assert release.wait(2), "test must release the stalled database connection"
        return connect(*args, **kwargs)

    monkeypatch.setattr("gluon.store.sqlite3.connect", stalled_connection)
    monkeypatch.setattr(system, "READINESS_TIMEOUT_SECONDS", 0.1)
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        first = asyncio.create_task(async_client.get("/api/ready"))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            # The stalled filesystem operation must neither block liveness nor
            # let repeated timed-out probes create more stalled DB workers.
            health = await asyncio.wait_for(async_client.get("/api/health"), timeout=0.5)
            assert health.status_code == 200
            assert (await asyncio.wait_for(first, timeout=0.5)).status_code == 503
            for _ in range(3):
                resp = await asyncio.wait_for(async_client.get("/api/ready"), timeout=0.5)
                assert resp.status_code == 503
            assert connection_attempts == 1
        finally:
            release.set()
            await first
            acquired = await asyncio.to_thread(temp_store._readiness_lock.acquire, timeout=1)
            if acquired:
                temp_store._readiness_lock.release()
            assert acquired, "database probe must finish before test cleanup"


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, 3),  # default
        ("1", 1),
        ("8", 8),
        ("0", 1),  # clamped to a minimum of 1
        ("-5", 1),  # clamped to a minimum of 1
        ("not-an-int", 3),  # falls back to the default
    ],
)
def test_default_max_concurrent(env_value: str | None, expected: int) -> None:
    """_default_max_concurrent honors GLUON_MAX_CONCURRENT_RUNS and is fail-safe."""
    from gluon.runner import _default_max_concurrent

    env = {k: v for k, v in os.environ.items() if k != "GLUON_MAX_CONCURRENT_RUNS"}
    if env_value is not None:
        env["GLUON_MAX_CONCURRENT_RUNS"] = env_value
    with patch.dict(os.environ, env, clear=True):
        assert _default_max_concurrent() == expected


def test_runner_config_uses_env_default() -> None:
    """RunnerConfig picks up the env-driven default at construction time."""
    from gluon.runner import RunnerConfig

    env = {k: v for k, v in os.environ.items() if k != "GLUON_MAX_CONCURRENT_RUNS"}
    env["GLUON_MAX_CONCURRENT_RUNS"] = "5"
    with patch.dict(os.environ, env, clear=True):
        assert RunnerConfig().max_concurrent == 5
