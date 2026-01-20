"""Integration tests for the project files API endpoint.

Tests the /api/projects/{project_id}/files endpoint which powers
the @mention file autocomplete in the new task dialog.
"""

import tempfile
from pathlib import Path

import pytest

from gluon.files import clear_cache
from gluon.models import Workspace
from gluon.store import GluonStore


@pytest.fixture
def temp_store():
    """Create a temporary store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = GluonStore(db_path)
        yield store


@pytest.fixture
def test_workspace(temp_store: GluonStore):
    """Create a test workspace."""
    return temp_store.create_workspace("test-workspace", "/tmp/test-workspace")


@pytest.fixture
def test_project_with_files(temp_store: GluonStore, test_workspace: Workspace):
    """Create a test project with files and directories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create directories
        (tmppath / "src").mkdir()
        (tmppath / "tests").mkdir()
        (tmppath / "src" / "utils").mkdir()

        # Create files
        (tmppath / "README.md").write_text("# Test Project")
        (tmppath / "src" / "index.ts").write_text("export {}")
        (tmppath / "src" / "utils" / "helpers.ts").write_text("export {}")
        (tmppath / "tests" / "test_main.py").write_text("")

        # Create project pointing to this directory
        project = temp_store.create_project(
            name="test-project",
            path=str(tmppath),
            workspace_id=test_workspace.id,
        )

        yield project, tmppath


class TestProjectFilesAPI:
    """Tests for /api/projects/{project_id}/files endpoint."""

    def test_get_project_files_returns_both_types(self, temp_store: GluonStore, test_project_with_files):
        """Verify API returns both files AND directories (CRITICAL)."""
        project, tmppath = test_project_with_files
        clear_cache()

        # Import here to avoid issues if fastapi not installed
        try:
            from starlette.testclient import TestClient

            from gluon.web.api import create_app
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        app = create_app(temp_store)
        client = TestClient(app)

        response = client.get(f"/api/projects/{project.id}/files")
        assert response.status_code == 200

        data = response.json()
        files = data["files"]

        # Verify we have results
        assert len(files) > 0, "No files returned"

        # Verify both types exist
        types = {f["type"] for f in files}
        assert "directory" in types, f"No directories in response. Types: {types}"
        assert "file" in types, f"No files in response. Types: {types}"

        # Verify type field is properly serialized
        for f in files:
            assert "type" in f, f"Missing 'type' field in: {f}"
            assert f["type"] in ("file", "directory"), f"Invalid type: {f['type']}"

    def test_get_project_files_type_field_serialization(self, temp_store: GluonStore, test_project_with_files):
        """Verify 'type' field correctly serialized to JSON."""
        project, tmppath = test_project_with_files
        clear_cache()

        try:
            from starlette.testclient import TestClient

            from gluon.web.api import create_app
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        app = create_app(temp_store)
        client = TestClient(app)

        response = client.get(f"/api/projects/{project.id}/files")
        assert response.status_code == 200

        data = response.json()

        # Each item must have type key with valid value
        for f in data["files"]:
            assert "type" in f, f"Missing type field in {f}"
            assert f["type"] is not None, f"Type is null in {f}"
            assert f["type"] in ("file", "directory"), f"Invalid type value: {f['type']}"

    def test_get_project_files_prefix_filter(self, temp_store: GluonStore, test_project_with_files):
        """Verify prefix filter correctly filters results."""
        project, tmppath = test_project_with_files
        clear_cache()

        try:
            from starlette.testclient import TestClient

            from gluon.web.api import create_app
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        app = create_app(temp_store)
        client = TestClient(app)

        # Filter with prefix=src
        response = client.get(f"/api/projects/{project.id}/files?prefix=src")
        assert response.status_code == 200

        data = response.json()
        files = data["files"]

        # All results should start with "src"
        for f in files:
            assert f["path"].startswith("src"), f"Path doesn't start with 'src': {f['path']}"

        # Should include both directory and files under src
        types = {f["type"] for f in files}
        assert "directory" in types, "No directories in filtered results"
        assert "file" in types, "No files in filtered results"

    def test_get_project_files_not_found(self, temp_store: GluonStore):
        """Verify API returns 404 for unknown project."""
        try:
            from starlette.testclient import TestClient

            from gluon.web.api import create_app
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        app = create_app(temp_store)
        client = TestClient(app)

        response = client.get("/api/projects/nonexistent-id/files")
        assert response.status_code == 404

    def test_get_project_files_limit_parameter(self, temp_store: GluonStore, test_workspace: Workspace):
        """Verify API respects limit parameter."""
        clear_cache()

        try:
            from starlette.testclient import TestClient

            from gluon.web.api import create_app
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        # Create project with many files
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            src_dir = tmppath / "src"
            src_dir.mkdir()

            # Create 20 files
            for i in range(20):
                (src_dir / f"file{i:02d}.ts").write_text("")

            project = temp_store.create_project(
                name="many-files-project",
                path=str(tmppath),
                workspace_id=test_workspace.id,
            )

            app = create_app(temp_store)
            client = TestClient(app)

            # Request with limit=5
            response = client.get(f"/api/projects/{project.id}/files?limit=5")
            assert response.status_code == 200

            data = response.json()
            assert len(data["files"]) == 5
            assert data["truncated"] is True

    def test_get_project_files_includes_nested_files(self, temp_store: GluonStore, test_project_with_files):
        """Verify nested files are included in results."""
        project, tmppath = test_project_with_files
        clear_cache()

        try:
            from starlette.testclient import TestClient

            from gluon.web.api import create_app
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        app = create_app(temp_store)
        client = TestClient(app)

        response = client.get(f"/api/projects/{project.id}/files")
        assert response.status_code == 200

        data = response.json()
        paths = [f["path"] for f in data["files"]]

        # Check nested file is present
        assert "src/utils/helpers.ts" in paths, f"Nested file not found. Paths: {paths}"

        # Verify it has type=file
        helpers_file = next(f for f in data["files"] if f["path"] == "src/utils/helpers.ts")
        assert helpers_file["type"] == "file"

    def test_get_project_files_directories_first_in_sort(self, temp_store: GluonStore, test_project_with_files):
        """Verify directories come before files in sorted results."""
        project, tmppath = test_project_with_files
        clear_cache()

        try:
            from starlette.testclient import TestClient

            from gluon.web.api import create_app
        except ImportError:
            pytest.skip("FastAPI/Starlette not installed")

        app = create_app(temp_store)
        client = TestClient(app)

        response = client.get(f"/api/projects/{project.id}/files")
        assert response.status_code == 200

        data = response.json()
        files = data["files"]

        # Find first file index
        first_file_idx = next((i for i, f in enumerate(files) if f["type"] == "file"), len(files))

        # All items before first file should be directories
        for i in range(first_file_idx):
            assert files[i]["type"] == "directory", (
                f"Expected directory at position {i}, got {files[i]['type']}: {files[i]['path']}"
            )
