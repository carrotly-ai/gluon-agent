"""Integration tests for workspace clone API endpoint.

Covers:
- POST /api/workspaces/{workspace_id}/clone
  - URL validation (GitHub-only, injection prevention)
  - Workspace lookup and path validation
  - Directory conflict detection
  - Successful clone with project registration
  - Git failure handling and cleanup
  - Timeout handling
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.models import Workspace
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_on_disk(temp_store: GluonStore, tmp_path: Path) -> tuple[Workspace, Path]:
    """Create a workspace backed by a real directory on disk."""
    ws_dir = tmp_path / "my-workspace"
    ws_dir.mkdir()
    workspace = temp_store.create_workspace("test-ws", str(ws_dir))
    return workspace, ws_dir


@pytest.fixture
def clone_client(temp_store: GluonStore, workspace_on_disk):
    """TestClient with a real workspace on disk and mocked ws_manager."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("FastAPI/Starlette not installed")

    mock_ws = AsyncMock()
    mock_runner = MagicMock()
    mock_runner.refresh_all_runs = MagicMock()
    mock_runner.refresh_run_status = MagicMock()
    mock_runner.submit = AsyncMock()
    mock_runner.cancel = AsyncMock(return_value=True)
    mock_runner.resume_in_place = AsyncMock()
    mock_runner.evaluate_supervision = AsyncMock()
    mock_runner.git_manager = MagicMock()
    mock_runner.git_manager._get_pr_info = AsyncMock(return_value=None)

    with (
        patch("gluon.web.api.TaskRunner", return_value=mock_runner),
        patch("gluon.web.api.ws_manager", mock_ws),
    ):
        from gluon.web.api import create_app

        app = create_app(temp_store)
        client = TestClient(app)
        workspace, ws_dir = workspace_on_disk
        yield client, workspace, ws_dir


# ===================================================================
# URL Validation
# ===================================================================


class TestCloneURLValidation:
    """POST /api/workspaces/{id}/clone - URL validation."""

    def test_clone_invalid_url_rejected(self, clone_client):
        """Non-GitHub URLs are rejected with 400."""
        client, workspace, _ = clone_client
        invalid_urls = [
            "ssh://git@github.com/owner/repo.git",
            "http://evil.com/owner/repo",
            "https://gitlab.com/owner/repo",
            "file:///etc/passwd",
            "",
            "not-a-url",
            "https://github.com/",
            "https://github.com/owner",
        ]
        for url in invalid_urls:
            resp = client.post(
                f"/api/workspaces/{workspace.id}/clone",
                json={"github_url": url},
            )
            assert resp.status_code == 400, f"Expected 400 for URL: {url!r}, got {resp.status_code}"
            assert "Invalid GitHub URL" in resp.json()["detail"]

    def test_clone_command_injection_prevented(self, clone_client):
        """URLs with shell metacharacters are rejected by regex."""
        client, workspace, _ = clone_client
        injection_urls = [
            "https://github.com/owner/repo; rm -rf /",
            "https://github.com/owner/repo$(whoami)",
            "https://github.com/owner/repo`id`",
            "https://github.com/owner/repo | cat /etc/passwd",
            "https://github.com/owner/repo && echo pwned",
        ]
        for url in injection_urls:
            resp = client.post(
                f"/api/workspaces/{workspace.id}/clone",
                json={"github_url": url},
            )
            assert resp.status_code == 400, f"Expected 400 for URL: {url!r}, got {resp.status_code}"

    def test_clone_valid_github_url_formats(self, clone_client):
        """GitHub URL validation accepts correct formats (mocked clone)."""
        client, workspace, ws_dir = clone_client

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Cloning...\n", b""))

        valid_urls = [
            ("https://github.com/octocat/Hello-World", "Hello-World"),
            ("https://github.com/octocat/Hello-World.git", "Hello-World"),
        ]

        for url, repo_name in valid_urls:
            target = ws_dir / repo_name

            async def mock_create_subprocess(*args, **kwargs):
                # Simulate git clone creating the directory
                target.mkdir(exist_ok=True)
                (target / ".git").mkdir(exist_ok=True)
                return mock_proc

            with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess):
                resp = client.post(
                    f"/api/workspaces/{workspace.id}/clone",
                    json={"github_url": url},
                )
                assert resp.status_code == 200, f"Expected 200 for URL: {url!r}, got {resp.status_code}: {resp.json()}"
                data = resp.json()
                assert data["repo_name"] == repo_name

            # Cleanup for next iteration
            import shutil

            if target.exists():
                shutil.rmtree(target)


# ===================================================================
# Workspace Lookup
# ===================================================================


class TestCloneWorkspaceLookup:
    """POST /api/workspaces/{id}/clone - workspace validation."""

    def test_clone_workspace_not_found(self, clone_client):
        """Returns 404 for nonexistent workspace."""
        client, _, _ = clone_client
        resp = client.post(
            "/api/workspaces/nonexistent-id/clone",
            json={"github_url": "https://github.com/octocat/Hello-World"},
        )
        assert resp.status_code == 404
        assert "Workspace not found" in resp.json()["detail"]

    def test_clone_workspace_path_missing(self, clone_client):
        """Returns 400 when workspace directory no longer exists on disk."""
        client, workspace, ws_dir = clone_client

        # Remove the workspace directory
        import shutil

        shutil.rmtree(ws_dir)

        resp = client.post(
            f"/api/workspaces/{workspace.id}/clone",
            json={"github_url": "https://github.com/octocat/Hello-World"},
        )
        assert resp.status_code == 400
        assert "does not exist" in resp.json()["detail"]


# ===================================================================
# Directory Conflicts
# ===================================================================


class TestCloneDirectoryConflict:
    """POST /api/workspaces/{id}/clone - directory conflict detection."""

    def test_clone_directory_already_exists(self, clone_client):
        """Returns 409 when target directory already exists."""
        client, workspace, ws_dir = clone_client

        # Pre-create the directory that would be the clone target
        (ws_dir / "Hello-World").mkdir()

        resp = client.post(
            f"/api/workspaces/{workspace.id}/clone",
            json={"github_url": "https://github.com/octocat/Hello-World"},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]


# ===================================================================
# Clone Success
# ===================================================================


class TestCloneSuccess:
    """POST /api/workspaces/{id}/clone - successful operations."""

    def test_clone_success_registers_project(self, clone_client):
        """Successful clone registers the project and returns scan results."""
        client, workspace, ws_dir = clone_client

        target = ws_dir / "Hello-World"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Cloning into 'Hello-World'...\n", b""))

        async def mock_create_subprocess(*args, **kwargs):
            # Simulate git clone creating the directory with .git marker
            target.mkdir(exist_ok=True)
            (target / ".git").mkdir(exist_ok=True)
            (target / "package.json").write_text('{"name": "hello-world"}')
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess):
            resp = client.post(
                f"/api/workspaces/{workspace.id}/clone",
                json={"github_url": "https://github.com/octocat/Hello-World"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["repo_name"] == "Hello-World"
        assert data["clone_path"] == str(target)
        assert data["project_registered"] is True
        assert data["project_name"] == "Hello-World"
        assert data["scan_result"]["workspace_id"] == workspace.id
        assert "Hello-World" in data["scan_result"]["projects_added"]


# ===================================================================
# Clone Failure
# ===================================================================


class TestCloneFailure:
    """POST /api/workspaces/{id}/clone - failure handling."""

    def test_clone_git_failure(self, clone_client):
        """Returns 400 when git clone fails and cleans up partial clone."""
        client, workspace, ws_dir = clone_client

        target = ws_dir / "Hello-World"

        mock_proc = MagicMock()
        mock_proc.returncode = 128
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"fatal: repository 'https://github.com/octocat/Hello-World' not found\n")
        )

        async def mock_create_subprocess(*args, **kwargs):
            # Simulate partial clone leaving a directory
            target.mkdir(exist_ok=True)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess):
            resp = client.post(
                f"/api/workspaces/{workspace.id}/clone",
                json={"github_url": "https://github.com/octocat/Hello-World"},
            )

        assert resp.status_code == 400
        assert "Clone failed" in resp.json()["detail"]
        # Verify partial clone was cleaned up
        assert not target.exists()

    def test_clone_timeout(self, clone_client):
        """Returns 504 when clone times out and cleans up."""
        client, workspace, ws_dir = clone_client

        target = ws_dir / "Hello-World"

        async def mock_create_subprocess(*args, **kwargs):
            # Simulate partial clone
            target.mkdir(exist_ok=True)
            mock_proc = MagicMock()

            async def mock_communicate():
                raise TimeoutError()

            mock_proc.communicate = mock_communicate
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess),
            patch("asyncio.wait_for", side_effect=TimeoutError()),
        ):
            resp = client.post(
                f"/api/workspaces/{workspace.id}/clone",
                json={"github_url": "https://github.com/octocat/Hello-World"},
            )

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]
        # Verify partial clone was cleaned up
        assert not target.exists()

    def test_clone_git_not_found(self, clone_client):
        """Returns 500 when git command is not found."""
        client, workspace, _ = clone_client

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("git")):
            resp = client.post(
                f"/api/workspaces/{workspace.id}/clone",
                json={"github_url": "https://github.com/octocat/Hello-World"},
            )

        assert resp.status_code == 500
        assert "git command not found" in resp.json()["detail"]
