"""Regression test for the cross-workspace credential bleed (WS-1).

`_workspace_env` injects a workspace's env vars into the process-global
``os.environ``. Because it is held across ``await`` points, two concurrent
requests for *different* workspaces must not be able to observe each other's
injected values. The lock-guarded async context manager guarantees this.
"""

from __future__ import annotations

import asyncio
import os

from gluon.web.api import _workspace_env


class _StubStore:
    def __init__(self, mapping: dict[str, dict[str, str]]):
        self._mapping = mapping

    def get_workspace_env_vars(self, workspace_id: str) -> dict[str, str]:
        return self._mapping.get(workspace_id, {})


async def test_workspace_env_no_cross_workspace_bleed():
    store = _StubStore(
        {
            "ws-a": {"WS_SECRET_TOKEN": "alpha"},
            "ws-b": {"WS_SECRET_TOKEN": "beta"},
        }
    )
    assert "WS_SECRET_TOKEN" not in os.environ  # precondition

    seen: dict[str, str | None] = {}

    async def run(workspace_id: str, label: str) -> None:
        async with _workspace_env(store, workspace_id):
            # Force a scheduling point: an unguarded implementation would let the
            # other workspace overwrite os.environ here, producing a bleed.
            await asyncio.sleep(0)
            seen[label] = os.environ.get("WS_SECRET_TOKEN")

    await asyncio.gather(run("ws-a", "a"), run("ws-b", "b"))

    assert seen["a"] == "alpha", "workspace A observed another workspace's value"
    assert seen["b"] == "beta", "workspace B observed another workspace's value"
    # Environment is fully restored afterwards.
    assert "WS_SECRET_TOKEN" not in os.environ


async def test_workspace_env_restores_existing_value():
    store = _StubStore({"ws-a": {"WS_PREEXISTING": "override"}})
    os.environ["WS_PREEXISTING"] = "original"
    try:
        async with _workspace_env(store, "ws-a"):
            assert os.environ["WS_PREEXISTING"] == "override"
        assert os.environ["WS_PREEXISTING"] == "original"
    finally:
        os.environ.pop("WS_PREEXISTING", None)
