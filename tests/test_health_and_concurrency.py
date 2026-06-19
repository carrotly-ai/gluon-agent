"""Tests for the container liveness endpoint and the concurrency cap default.

Both back the post-incident containment work (a runaway fan-out of concurrent
agents wedged the host VM): the health endpoint feeds the compose healthcheck,
and GLUON_MAX_CONCURRENT_RUNS bounds how many runs execute at once.
"""

from __future__ import annotations

import os
from unittest.mock import patch

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
