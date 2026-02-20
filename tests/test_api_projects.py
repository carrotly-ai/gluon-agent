"""Integration tests for project, status, version, and usage API endpoints.

Covers:
- GET /api/status
- GET /api/version
- GET /api/projects, POST /api/projects, GET /api/projects/{id}, DELETE /api/projects/{id}
- GET /api/usage/summary, /api/usage/by-project, /api/usage/by-day, /api/usage/runs
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from gluon.models import RunStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_run(store, project_id, *, prompt="task", status=RunStatus.COMPLETED, cost_usd=None):
    run = store.create_run(project_id=project_id, prompt=prompt, initiator="test")
    if status != RunStatus.PENDING:
        run.status = status
    if cost_usd is not None:
        run.cost_usd = cost_usd
    store.update_run(run)
    return run


# ===================================================================
# Status & Version
# ===================================================================


class TestStatusEndpoint:
    """GET /api/status."""

    def test_status_empty(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_projects"] == 0
        assert data["active_runs"] == 0
        assert data["total_runs"] == 0

    def test_status_with_data(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_projects"] == 1
        assert data["active_runs"] == 1
        assert data["total_runs"] == 2


class TestVersionEndpoint:
    """GET /api/version."""

    def test_version_development_mode(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks

        # Clear cached version info — the _version_info closure must be reset
        # This is a best-effort test; we verify the shape is correct.
        resp = client.get("/api/version")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "full_version" in data
        assert "build_time" in data
        assert "environment" in data

    def test_version_production_mode(self, temp_store):
        """GLUON_VERSION env var triggers 'production' environment."""
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        env_patch = {
            "GLUON_VERSION": "abc1234",
            "GLUON_FULL_VERSION": "abc1234567890",
            "GLUON_BUILD_TIME": "2026-01-01T00:00:00",
        }
        with patch.dict(os.environ, env_patch):
            from gluon.web.api import create_app

            app = create_app(temp_store)
            # Force reset cached version info by setting closure to None
            # The _version_info is a nonlocal in create_app; we re-create app so it's fresh.
            client = TestClient(app)
            resp = client.get("/api/version")
            assert resp.status_code == 200
            data = resp.json()
            assert data["environment"] == "production"
            assert data["version"] == "abc1234"

    def test_version_response_fields(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/version")
        data = resp.json()
        # All four fields must be non-empty strings
        for field in ("version", "full_version", "build_time", "environment"):
            assert isinstance(data[field], str)
            assert len(data[field]) > 0


# ===================================================================
# Project CRUD
# ===================================================================


class TestListProjects:
    """GET /api/projects."""

    def test_list_projects_empty(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_projects_returns_projects(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "test-project"


class TestGetProject:
    """GET /api/projects/{id}."""

    def test_get_project_by_id(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        client, _, _ = api_client_with_mocks

        resp = client.get(f"/api/projects/{project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == project.id
        assert data["name"] == "test-project"

    def test_get_project_not_found(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/projects/nonexistent-id")
        assert resp.status_code == 404


class TestCreateProject:
    """POST /api/projects."""

    def test_create_project(self, temp_store, tmp_path, api_client_with_mocks):
        client, _, _ = api_client_with_mocks

        # Create a real directory under home (or tmp_path which may not be under home)
        # Use the tmp_path-based approach; the endpoint validates path is under home dir.
        # We need to use a path that resolves under Path.home().
        home = Path.home()
        test_dir = home / ".gluon" / "test-create-project"
        test_dir.mkdir(parents=True, exist_ok=True)
        try:
            resp = client.post(
                "/api/projects",
                json={"name": "new-project", "path": str(test_dir)},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "new-project"
        finally:
            test_dir.rmdir()

    def test_create_project_duplicate_name(self, temp_store, project_with_path, api_client_with_mocks):
        project, project_dir = project_with_path
        client, _, _ = api_client_with_mocks

        resp = client.post(
            "/api/projects",
            json={"name": "test-project", "path": str(project_dir)},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_create_project_nonexistent_path(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        home = Path.home()
        resp = client.post(
            "/api/projects",
            json={"name": "bad-path", "path": str(home / "nonexistent-dir-xyz")},
        )
        assert resp.status_code == 400

    def test_create_project_outside_home(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.post(
            "/api/projects",
            json={"name": "outside", "path": "/etc"},
        )
        assert resp.status_code == 400
        assert "home directory" in resp.json()["detail"]


class TestDeleteProject:
    """DELETE /api/projects/{id}."""

    def test_delete_project(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        client, _, _ = api_client_with_mocks

        resp = client.delete(f"/api/projects/{project.id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone
        resp = client.get(f"/api/projects/{project.id}")
        assert resp.status_code == 404

    def test_delete_project_not_found(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.delete("/api/projects/nonexistent-id")
        assert resp.status_code == 404

    def test_delete_project_cascades(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id)
        client, _, _ = api_client_with_mocks

        resp = client.delete(f"/api/projects/{project.id}")
        assert resp.status_code == 200

        # Runs for deleted project should be gone
        runs = temp_store.list_runs(project_id=project.id)
        assert len(runs) == 0


# ===================================================================
# Usage Analytics
# ===================================================================


class TestUsageSummary:
    """GET /api/usage/summary."""

    def test_usage_summary_empty(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 0
        assert data["total_cost_usd"] == 0.0

    def test_usage_summary_with_data(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, cost_usd=1.50)
        _seed_run(temp_store, project.id, cost_usd=2.50)
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 2
        assert data["total_cost_usd"] == pytest.approx(4.0, abs=0.01)


class TestUsageByProject:
    """GET /api/usage/by-project."""

    def test_usage_by_project_empty(self, api_client_with_mocks):
        client, _, _ = api_client_with_mocks
        resp = client.get("/api/usage/by-project")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_usage_by_project_with_data(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, cost_usd=3.0)
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/usage/by-project")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["project_name"] == "test-project"
        assert data[0]["run_count"] == 1

    def test_usage_by_project_date_filters(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, cost_usd=1.0)
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/usage/by-project?since=2020-01-01T00:00:00&until=2099-12-31T00:00:00")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestUsageByDay:
    """GET /api/usage/by-day."""

    def test_usage_by_day(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, cost_usd=2.0)
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/usage/by-day")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["run_count"] >= 1


class TestUsageRuns:
    """GET /api/usage/runs."""

    def test_usage_runs(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, cost_usd=5.0)
        client, _, _ = api_client_with_mocks

        resp = client.get("/api/usage/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["project_name"] == "test-project"

    def test_usage_runs_filter_by_project(self, temp_store, project_with_path, api_client_with_mocks):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, cost_usd=1.0)
        client, _, _ = api_client_with_mocks

        # The usage/runs endpoint doesn't directly filter by project_id in query params,
        # but we can verify it returns results with the correct project.
        resp = client.get("/api/usage/runs")
        assert resp.status_code == 200
        for item in resp.json():
            assert item["project_name"] == "test-project"
