"""Behavior-identity net for the settings + vercel-token API routes (#162).

These are admin-gated. The CRITICAL invariant is secret redaction: GET
/api/settings must never round-trip secret-looking values to the client. This
net locks that (plus update + the vercel-token guard) BEFORE the routes move
into web/routers/settings.py, so the security-sensitive relocation is provably
behavior-identical. In single-user mode require_admin is a no-op.
"""

from __future__ import annotations

from unittest.mock import patch

from gluon.store import GluonStore


def test_get_settings_redacts_secrets(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.set_setting("github_token", "ghp_supersecret")
    temp_store.set_setting("openai_api_key", "sk-secret")
    temp_store.set_setting("admin_password", "hunter2")
    temp_store.set_setting("model", "opus")  # not secret-looking

    resp = client.get("/api/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Secret-looking values are masked, never round-tripped
    assert body["github_token"] == "********"
    assert body["openai_api_key"] == "********"
    assert body["admin_password"] == "********"
    # Non-secret values pass through unchanged
    assert body["model"] == "opus"


def test_get_settings_exposes_meta_fields(api_client):
    client, _ = api_client
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "_vercel_token_from_env" in body
    assert "_llm_provider_name" in body
    assert "_llm_provider_supports_cost_tracking" in body


def test_update_setting(api_client, temp_store: GluonStore):
    client, _ = api_client
    resp = client.put("/api/settings/some_key", json={"value": "some_value"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"key": "some_key", "value": "some_value"}
    assert temp_store.get_setting("some_key") == "some_value"


def test_update_setting_missing_value_400(api_client):
    client, _ = api_client
    resp = client.put("/api/settings/some_key", json={})
    assert resp.status_code == 400
    assert "value" in resp.json()["detail"].lower()


def test_vercel_test_no_token_400(api_client):
    client, _ = api_client
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("VERCEL_TOKEN", None)
        resp = client.post("/api/vercel/test", json={})
    assert resp.status_code == 400
    assert "token" in resp.json()["detail"].lower()


def test_vercel_test_valid_token(api_client):
    client, _ = api_client

    class _Result:
        returncode = 0
        stdout = "acme-team\n"
        stderr = ""

    with patch("subprocess.run", return_value=_Result()):
        resp = client.post("/api/vercel/test", json={"token": "vt_abc"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["account"] == "acme-team"
