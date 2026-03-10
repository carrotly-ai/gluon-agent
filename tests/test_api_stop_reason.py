"""Tests for stop_reason in GET /api/runs/{id} response.

Verifies the RunDetailResponse includes stop_reason extracted from run.metadata.
"""

from __future__ import annotations

from gluon.models import ExecutionRun, RunStatus
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_run(
    store: GluonStore,
    project_id: str,
    *,
    metadata: dict | None = None,
) -> ExecutionRun:
    run = store.create_run(project_id=project_id, prompt="test", initiator="test")
    run.status = RunStatus.COMPLETED
    if metadata is not None:
        run.metadata = metadata
    store.update_run(run)
    return run


# ===========================================================================
# Tests
# ===========================================================================


class TestGetRunStopReason:
    """GET /api/runs/{id} should include stop_reason from metadata."""

    def test_get_run_includes_stop_reason(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, metadata={"stop_reason": "end_turn"})
        client, _, _ = api_client_with_mocks

        resp = client.get(f"/api/runs/{run.id}?refresh_pr=false")
        assert resp.status_code == 200
        assert resp.json()["stop_reason"] == "end_turn"

    def test_get_run_stop_reason_null_when_no_metadata(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, metadata=None)
        client, _, _ = api_client_with_mocks

        resp = client.get(f"/api/runs/{run.id}?refresh_pr=false")
        assert resp.status_code == 200
        assert resp.json()["stop_reason"] is None

    def test_get_run_stop_reason_null_when_not_in_metadata(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, metadata={"profile": "deep"})
        client, _, _ = api_client_with_mocks

        resp = client.get(f"/api/runs/{run.id}?refresh_pr=false")
        assert resp.status_code == 200
        assert resp.json()["stop_reason"] is None
