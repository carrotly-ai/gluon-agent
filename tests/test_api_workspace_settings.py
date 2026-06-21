"""Behavior-identity net for the workspace settings + env-vars API routes (#162).

Locks the 4 admin-gated store-only routes (PUT/DELETE settings, PUT/DELETE
env-vars) BEFORE they move from create_app into web/routers/workspaces.py.
In single-user mode require_admin is a no-op (SYSTEM_USER is admin), so the
plain api_client exercises them. Seeds via the shared temp_store.
"""

from __future__ import annotations

from pathlib import Path

from gluon.store import GluonStore


def _ws(temp_store: GluonStore, tmp_path: Path):
    return temp_store.create_workspace("ws", str(tmp_path / "ws"))


def test_update_workspace_settings(api_client, temp_store: GluonStore, tmp_path: Path):
    client, _ = api_client
    ws = _ws(temp_store, tmp_path)
    resp = client.put(f"/api/workspaces/{ws.id}/settings", json={"model": "opus", "effort": "high"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 2
    assert body["workspace_id"] == ws.id
    assert temp_store.get_workspace_setting(ws.id, "model") == "opus"


def test_update_settings_rejects_env_prefix_400(api_client, temp_store: GluonStore, tmp_path: Path):
    client, _ = api_client
    ws = _ws(temp_store, tmp_path)
    resp = client.put(f"/api/workspaces/{ws.id}/settings", json={"env.FOO": "bar"})
    assert resp.status_code == 400
    assert "env-vars" in resp.json()["detail"]


def test_update_settings_missing_workspace_404(api_client):
    client, _ = api_client
    resp = client.put("/api/workspaces/missing/settings", json={"k": "v"})
    assert resp.status_code == 404


def test_delete_workspace_setting(api_client, temp_store: GluonStore, tmp_path: Path):
    client, _ = api_client
    ws = _ws(temp_store, tmp_path)
    temp_store.set_workspace_setting(ws.id, "model", "opus")
    resp = client.delete(f"/api/workspaces/{ws.id}/settings/model")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["key"] == "model"
    assert temp_store.get_workspace_setting(ws.id, "model") is None


def test_update_workspace_env_vars(api_client, temp_store: GluonStore, tmp_path: Path):
    client, _ = api_client
    ws = _ws(temp_store, tmp_path)
    resp = client.put(f"/api/workspaces/{ws.id}/env-vars", json={"API_KEY": "secret"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1
    # Env vars are stored with the env. prefix
    assert temp_store.get_workspace_setting(ws.id, "env.API_KEY") == "secret"


def test_update_env_vars_missing_workspace_404(api_client):
    client, _ = api_client
    resp = client.put("/api/workspaces/missing/env-vars", json={"K": "v"})
    assert resp.status_code == 404


def test_delete_workspace_env_var(api_client, temp_store: GluonStore, tmp_path: Path):
    client, _ = api_client
    ws = _ws(temp_store, tmp_path)
    temp_store.set_workspace_setting(ws.id, "env.API_KEY", "secret")
    resp = client.delete(f"/api/workspaces/{ws.id}/env-vars/API_KEY")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True
    assert temp_store.get_workspace_setting(ws.id, "env.API_KEY") is None
