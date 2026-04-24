"""Shared fixtures for API and WebSocket tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.models import Workspace
from gluon.store import GluonStore


@pytest.fixture(params=["bedrock", "anthropic", "vertex", "foundry"])
def llm_provider(request, monkeypatch):
    """Parametrize tests across all LLM providers."""
    monkeypatch.setenv("GLUON_LLM_PROVIDER", request.param)
    return request.param


@pytest.fixture
def temp_store(tmp_path: Path) -> GluonStore:
    """Create a temporary store backed by a SQLite DB in tmp_path."""
    db_path = tmp_path / "test.db"
    return GluonStore(db_path)


@pytest.fixture
def store(tmp_path: Path) -> GluonStore:
    """Shared store fixture — replaces per-file duplicates."""
    return GluonStore(tmp_path / "test.db")


@pytest.fixture
def test_workspace(temp_store: GluonStore) -> Workspace:
    """Create a test workspace."""
    return temp_store.create_workspace("test-workspace", "/tmp/test-workspace")


@pytest.fixture
def project_with_path(temp_store: GluonStore, test_workspace: Workspace, tmp_path: Path):
    """Create a project with a real directory on disk."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Test")
    project = temp_store.create_project(
        name="test-project",
        path=str(project_dir),
        workspace_id=test_workspace.id,
    )
    return project, project_dir


@pytest.fixture
def api_client(temp_store: GluonStore):
    """TestClient with real store, real runner/orchestrator, patched ws_manager.

    Suitable for Tier 1 tests that only use store operations (archive, pr-status,
    questions, queued messages). The ws_manager is patched to avoid real WebSocket
    broadcasts and to verify broadcast calls.
    """
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("FastAPI/Starlette not installed")

    mock_ws = AsyncMock()

    with patch("gluon.web.api.ws_manager", mock_ws):
        from gluon.web.api import create_app

        app = create_app(temp_store)
        client = TestClient(app)
        yield client, mock_ws


@pytest.fixture
def api_client_with_mocks(temp_store: GluonStore):
    """TestClient with mocked TaskRunner and Orchestrator.

    Suitable for Tier 2 tests that call runner.submit(), runner.cancel(), etc.
    Patches constructors before create_app() so closure variables get mocks.
    """
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("FastAPI/Starlette not installed")

    mock_runner = MagicMock()
    mock_runner.refresh_all_runs = MagicMock()
    mock_runner.refresh_run_status = MagicMock()
    mock_runner.submit = AsyncMock()
    mock_runner.cancel = AsyncMock(return_value=True)
    mock_runner.resume_in_place = AsyncMock()
    mock_runner.evaluate_supervision = AsyncMock()
    mock_runner.git_manager = MagicMock()
    mock_runner.git_manager._get_pr_info = AsyncMock(return_value=None)

    mock_ws = AsyncMock()

    with (
        patch("gluon.web.api.TaskRunner", return_value=mock_runner),
        patch("gluon.web.api.ws_manager", mock_ws),
    ):
        from gluon.web.api import create_app

        app = create_app(temp_store)
        client = TestClient(app)
        yield client, mock_runner, mock_ws
