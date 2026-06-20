"""Tests for Step 1 (I4 warn-only): the `verify_cmd` field + readiness
classification. Additive + opt-in — runs created without `verify_cmd` must behave
exactly as before (gateless), and the field is NOT yet enforced.
"""

from __future__ import annotations

from gluon.models import run_readiness
from gluon.store import GluonStore


def _project(store: GluonStore):
    ws = store.create_workspace("w", "/tmp/w")
    return store.create_project(name="p", path="/tmp/w/p", workspace_id=ws.id)


def test_run_readiness_helper() -> None:
    assert run_readiness("uv run pytest") == "gated"
    assert run_readiness("") == "gateless"  # empty string is falsy → gateless
    assert run_readiness(None) == "gateless"


def test_verify_cmd_persists(temp_store: GluonStore) -> None:
    proj = _project(temp_store)
    run = temp_store.create_run(project_id=proj.id, prompt="t", verify_cmd="uv run pytest")
    assert run.verify_cmd == "uv run pytest"
    # round-trips through the DB (column added via additive migration)
    fetched = temp_store.get_run(run.id)
    assert fetched is not None
    assert fetched.verify_cmd == "uv run pytest"


def test_no_verify_cmd_is_gateless_unchanged(temp_store: GluonStore) -> None:
    """Non-regression: a run created without verify_cmd is unchanged (gateless)."""
    proj = _project(temp_store)
    run = temp_store.create_run(project_id=proj.id, prompt="t")
    assert run.verify_cmd is None
    fetched = temp_store.get_run(run.id)
    assert fetched is not None
    assert fetched.verify_cmd is None
    assert run_readiness(fetched.verify_cmd) == "gateless"


def test_run_response_exposes_readiness(api_client, temp_store: GluonStore) -> None:
    # api_client builds create_app(temp_store), so they share one store.
    proj = _project(temp_store)
    gated = temp_store.create_run(project_id=proj.id, prompt="t", verify_cmd="make test")
    gateless = temp_store.create_run(project_id=proj.id, prompt="t")

    client, _ = api_client
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    by_id = {r["id"]: r for r in resp.json()}

    assert by_id[gated.id]["verify_cmd"] == "make test"
    assert by_id[gated.id]["readiness"] == "gated"
    assert by_id[gateless.id]["verify_cmd"] is None
    assert by_id[gateless.id]["readiness"] == "gateless"
