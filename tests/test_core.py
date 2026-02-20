"""Unit tests for Orchestrator (core.py).

Tests project management, workspace management, session management,
and exception classes. Uses real GluonStore with tmp_path-backed DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gluon.core import (
    GitBranchNotFoundError,
    GitForcePushRequiredError,
    GitMergeConflictError,
    GitRebaseInProgressError,
    Orchestrator,
    ProjectExistsError,
    ProjectNotFoundError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def orch(store: GluonStore) -> Orchestrator:
    return Orchestrator(store=store)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "my-project"
    d.mkdir()
    (d / "README.md").write_text("# Project")
    return d


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspace"
    d.mkdir()
    return d


# ===================================================================
# Exception classes
# ===================================================================


class TestExceptionClasses:
    def test_git_merge_conflict_error(self):
        err = GitMergeConflictError(["a.py", "b.py", "c.py", "d.py"], "rebase")
        assert "4 file(s)" in str(err)
        assert "..." in str(err)
        assert err.operation == "rebase"

    def test_git_merge_conflict_short_list(self):
        err = GitMergeConflictError(["a.py"])
        assert "1 file(s)" in str(err)
        assert "..." not in str(err)

    def test_git_rebase_in_progress(self):
        err = GitRebaseInProgressError(3, 7)
        assert "3/7" in str(err)

    def test_git_rebase_no_steps(self):
        err = GitRebaseInProgressError()
        assert "Rebase in progress" in str(err)

    def test_git_force_push_required(self):
        err = GitForcePushRequiredError("feat/x", 3)
        assert "feat/x" in str(err)
        assert "3" in str(err)

    def test_git_branch_not_found(self):
        err = GitBranchNotFoundError("missing-branch")
        assert "missing-branch" in str(err)


# ===================================================================
# Project Management
# ===================================================================


class TestRegisterProject:
    def test_register_project(self, orch, project_dir):
        project = orch.register_project("my-project", project_dir)
        assert project.name == "my-project"

    def test_register_project_duplicate(self, orch, project_dir):
        orch.register_project("dup", project_dir)
        with pytest.raises(ProjectExistsError):
            orch.register_project("dup", project_dir)

    def test_register_project_nonexistent_path(self, orch, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            orch.register_project("bad", tmp_path / "nonexistent")

    def test_register_project_file_not_dir(self, orch, tmp_path):
        f = tmp_path / "afile.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a directory"):
            orch.register_project("file", f)


class TestGetProject:
    def test_get_by_name(self, orch, project_dir):
        orch.register_project("proj", project_dir)
        project = orch.get_project("proj")
        assert project.name == "proj"

    def test_get_by_id(self, orch, project_dir):
        created = orch.register_project("proj", project_dir)
        project = orch.get_project(created.id)
        assert project.id == created.id

    def test_not_found(self, orch):
        with pytest.raises(ProjectNotFoundError):
            orch.get_project("nope")


class TestListProjects:
    def test_empty(self, orch):
        assert orch.list_projects() == []

    def test_with_projects(self, orch, tmp_path):
        d1 = tmp_path / "p1"
        d1.mkdir()
        d2 = tmp_path / "p2"
        d2.mkdir()
        orch.register_project("p1", d1)
        orch.register_project("p2", d2)
        assert len(orch.list_projects()) == 2


class TestRemoveProject:
    def test_remove(self, orch, project_dir):
        orch.register_project("rm", project_dir)
        result = orch.remove_project("rm")
        assert result is True
        with pytest.raises(ProjectNotFoundError):
            orch.get_project("rm")

    def test_remove_not_found(self, orch):
        with pytest.raises(ProjectNotFoundError):
            orch.remove_project("nope")


# ===================================================================
# Workspace Management
# ===================================================================


class TestRegisterWorkspace:
    def test_register_workspace(self, orch, workspace_dir):
        ws, projects = orch.register_workspace("ws", workspace_dir, auto_scan=False)
        assert ws.name == "ws"
        assert projects == []

    def test_register_workspace_duplicate(self, orch, workspace_dir):
        orch.register_workspace("ws", workspace_dir, auto_scan=False)
        with pytest.raises(WorkspaceExistsError):
            orch.register_workspace("ws", workspace_dir, auto_scan=False)

    def test_register_workspace_nonexistent_path(self, orch, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            orch.register_workspace("bad", tmp_path / "nope")

    def test_register_workspace_auto_scan(self, orch, workspace_dir):
        # Create a subdirectory with .git to simulate a project
        proj = workspace_dir / "sub-project"
        proj.mkdir()
        (proj / ".git").mkdir()

        ws, projects = orch.register_workspace("ws", workspace_dir, auto_scan=True)
        assert ws.name == "ws"
        # Should have discovered sub-project
        assert len(projects) >= 1


class TestGetWorkspace:
    def test_get_by_name(self, orch, workspace_dir):
        orch.register_workspace("ws", workspace_dir, auto_scan=False)
        ws = orch.get_workspace("ws")
        assert ws.name == "ws"

    def test_get_by_id(self, orch, workspace_dir):
        created, _ = orch.register_workspace("ws", workspace_dir, auto_scan=False)
        ws = orch.get_workspace(created.id)
        assert ws.id == created.id

    def test_not_found(self, orch):
        with pytest.raises(WorkspaceNotFoundError):
            orch.get_workspace("nope")


class TestListWorkspaces:
    def test_empty(self, orch):
        assert orch.list_workspaces() == []

    def test_with_workspaces(self, orch, tmp_path):
        d1 = tmp_path / "ws1"
        d1.mkdir()
        d2 = tmp_path / "ws2"
        d2.mkdir()
        orch.register_workspace("ws1", d1, auto_scan=False)
        orch.register_workspace("ws2", d2, auto_scan=False)
        assert len(orch.list_workspaces()) == 2


class TestRemoveWorkspace:
    def test_remove_workspace(self, orch, workspace_dir):
        orch.register_workspace("ws", workspace_dir, auto_scan=False)
        result = orch.remove_workspace("ws")
        assert result is True
        with pytest.raises(WorkspaceNotFoundError):
            orch.get_workspace("ws")

    def test_remove_workspace_with_projects(self, orch, workspace_dir):
        proj = workspace_dir / "p"
        proj.mkdir()
        (proj / ".git").mkdir()
        orch.register_workspace("ws", workspace_dir, auto_scan=True)

        result = orch.remove_workspace("ws", remove_projects=True)
        assert result is True
        assert orch.list_projects() == []


# ===================================================================
# Session Management
# ===================================================================


class TestSessionManagement:
    def test_list_sessions_empty(self, orch, project_dir):
        orch.register_project("proj", project_dir)
        assert orch.list_sessions("proj") == []

    def test_list_sessions_all(self, orch):
        assert orch.list_sessions() == []

    def test_get_session_none(self, orch):
        assert orch.get_session("nonexistent") is None

    def test_get_active_sessions_empty(self, orch):
        assert orch.get_active_sessions() == []

    def test_get_resumable_session_none(self, orch, project_dir):
        proj = orch.register_project("proj", project_dir)
        assert orch.get_resumable_session(proj) is None


# ===================================================================
# Scan workspace
# ===================================================================


class TestScanWorkspace:
    def test_scan_workspace_finds_new_projects(self, orch, workspace_dir):
        orch.register_workspace("ws", workspace_dir, auto_scan=False)

        # Add a project directory after workspace creation
        proj = workspace_dir / "new-proj"
        proj.mkdir()
        (proj / ".git").mkdir()

        new_projects = orch.scan_workspace("ws")
        assert len(new_projects) >= 1
        names = [p.name for p in new_projects]
        assert "new-proj" in names

    def test_scan_workspace_not_found(self, orch):
        with pytest.raises(WorkspaceNotFoundError):
            orch.scan_workspace("nope")
