"""Tests for workspace-specific settings and environment variables."""

from pathlib import Path

import pytest

from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path: Path) -> GluonStore:
    return GluonStore(tmp_path / "test.db")


@pytest.fixture
def workspace_id(store: GluonStore) -> str:
    ws = store.create_workspace("test-ws", "/tmp/test-ws")
    return ws.id


class TestWorkspaceSettingsCRUD:
    def test_set_and_get_setting(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "git_user_name", "OrgBot")
        assert store.get_workspace_setting(workspace_id, "git_user_name") == "OrgBot"

    def test_get_missing_returns_default(self, store: GluonStore, workspace_id: str):
        assert store.get_workspace_setting(workspace_id, "nonexistent") is None
        assert store.get_workspace_setting(workspace_id, "nonexistent", "fallback") == "fallback"

    def test_upsert_overwrites(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "key", "v1")
        store.set_workspace_setting(workspace_id, "key", "v2")
        assert store.get_workspace_setting(workspace_id, "key") == "v2"

    def test_get_all_settings(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "a", "1")
        store.set_workspace_setting(workspace_id, "b", "2")
        store.set_workspace_setting(workspace_id, "env.GH_TOKEN", "ghp_xxx")
        settings = store.get_workspace_settings(workspace_id)
        assert settings == {"a": "1", "b": "2", "env.GH_TOKEN": "ghp_xxx"}

    def test_delete_setting(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "key", "val")
        assert store.delete_workspace_setting(workspace_id, "key") is True
        assert store.get_workspace_setting(workspace_id, "key") is None

    def test_delete_nonexistent_returns_false(self, store: GluonStore, workspace_id: str):
        assert store.delete_workspace_setting(workspace_id, "nope") is False

    def test_delete_all_settings(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "a", "1")
        store.set_workspace_setting(workspace_id, "b", "2")
        store.set_workspace_setting(workspace_id, "env.TOKEN", "xxx")
        count = store.delete_all_workspace_settings(workspace_id)
        assert count == 3
        assert store.get_workspace_settings(workspace_id) == {}


class TestResolveSetting:
    def test_global_only(self, store: GluonStore, workspace_id: str):
        store.set_setting("auto_create_pr", "true")
        assert store.resolve_setting("auto_create_pr", workspace_id=workspace_id) == "true"

    def test_workspace_overrides_global(self, store: GluonStore, workspace_id: str):
        store.set_setting("auto_create_pr", "true")
        store.set_workspace_setting(workspace_id, "auto_create_pr", "false")
        assert store.resolve_setting("auto_create_pr", workspace_id=workspace_id) == "false"

    def test_no_workspace_id_uses_global(self, store: GluonStore):
        store.set_setting("git_user_name", "Global Bot")
        assert store.resolve_setting("git_user_name") == "Global Bot"

    def test_none_workspace_id_uses_global(self, store: GluonStore):
        store.set_setting("key", "global_val")
        assert store.resolve_setting("key", workspace_id=None) == "global_val"

    def test_default_when_nothing_set(self, store: GluonStore, workspace_id: str):
        assert store.resolve_setting("missing", "default_val", workspace_id) == "default_val"

    def test_default_when_no_workspace(self, store: GluonStore):
        assert store.resolve_setting("missing", "fallback") == "fallback"


class TestEnvVarHelpers:
    def test_get_env_vars(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "env.GH_TOKEN", "ghp_xxx")
        store.set_workspace_setting(workspace_id, "env.AWS_PROFILE", "org-profile")
        store.set_workspace_setting(workspace_id, "git_user_name", "OrgBot")  # not an env var

        env_vars = store.get_workspace_env_vars(workspace_id)
        assert env_vars == {"GH_TOKEN": "ghp_xxx", "AWS_PROFILE": "org-profile"}

    def test_empty_when_no_env_vars(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "git_user_name", "Bot")
        assert store.get_workspace_env_vars(workspace_id) == {}


class TestCascadeDelete:
    def test_workspace_delete_cleans_settings(self, store: GluonStore, workspace_id: str):
        store.set_workspace_setting(workspace_id, "key", "val")
        store.set_workspace_setting(workspace_id, "env.TOKEN", "xxx")
        store.delete_workspace(workspace_id)
        # Settings should be gone
        assert store.get_workspace_settings(workspace_id) == {}
