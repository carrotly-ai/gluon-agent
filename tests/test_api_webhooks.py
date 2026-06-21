"""Behavior-identity net for the webhook API routes (#162).

The CRITICAL invariant is HMAC signature validation on POST /api/webhooks/github
(unauthenticated, gated only by X-Hub-Signature-256): missing/invalid signature
MUST be rejected (401), missing secret MUST 500, missing event MUST 400. This
net locks that — plus the admin-gated webhook-config CRUD — BEFORE the routes
move into web/routers/webhooks.py, so the security-sensitive relocation is
provably behavior-identical. Seeds via the shared temp_store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

from gluon.models import WebhookConfig
from gluon.store import GluonStore

_SECRET = "test-webhook-secret"


def _sign(payload: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _no_env_secret():
    # Ensure the env var doesn't mask the store-based secret resolution.
    import os

    return patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "GITHUB_WEBHOOK_SECRET"}, clear=True)


# ----------------------------------------------- HMAC signature gating (critical)


def test_webhook_no_secret_configured_500(api_client):
    client, _ = api_client
    with _no_env_secret():
        resp = client.post("/api/webhooks/github", content=b"{}", headers={"X-Hub-Signature-256": "sha256=x"})
    assert resp.status_code == 500
    assert "secret" in resp.json()["detail"].lower()


def test_webhook_missing_signature_401(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.set_setting("github_webhook_secret", _SECRET)
    with _no_env_secret():
        resp = client.post("/api/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "push"})
    assert resp.status_code == 401
    assert "signature" in resp.json()["detail"].lower()


def test_webhook_missing_event_400(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.set_setting("github_webhook_secret", _SECRET)
    with _no_env_secret():
        resp = client.post(
            "/api/webhooks/github",
            content=b"{}",
            headers={"X-Hub-Signature-256": "sha256=anything"},
        )
    assert resp.status_code == 400
    assert "event" in resp.json()["detail"].lower()


def test_webhook_invalid_signature_401(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.set_setting("github_webhook_secret", _SECRET)
    body = b'{"hello": "world"}'
    with _no_env_secret():
        resp = client.post(
            "/api/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=deadbeef", "X-GitHub-Event": "push"},
        )
    assert resp.status_code == 401
    assert "signature" in resp.json()["detail"].lower()


def test_webhook_valid_signature_unsupported_event_ignored(api_client, temp_store: GluonStore):
    """A VALID signature passes validation; an unsupported event type is ignored."""
    client, _ = api_client
    temp_store.set_setting("github_webhook_secret", _SECRET)
    body = json.dumps({"zen": "ping"}).encode("utf-8")
    with _no_env_secret():
        resp = client.post(
            "/api/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "ping"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ignored"


# ----------------------------------------------- admin webhook-config CRUD


def test_list_webhooks(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.create_webhook_config(WebhookConfig(handler="github", events=["push"], secret_key="k"))
    resp = client.get("/api/webhooks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["handler"] == "github"


def test_create_webhook_returns_secret(api_client):
    client, _ = api_client
    resp = client.post("/api/webhooks", json={"handler": "github", "events": ["push"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handler"] == "github"
    assert body["secret_key"]  # generated secret returned for GitHub config


def test_delete_webhook(api_client, temp_store: GluonStore):
    client, _ = api_client
    cfg = temp_store.create_webhook_config(WebhookConfig(handler="github", events=["push"], secret_key="k"))
    resp = client.delete(f"/api/webhooks/{cfg.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True


def test_delete_webhook_404(api_client):
    client, _ = api_client
    resp = client.delete("/api/webhooks/missing")
    assert resp.status_code == 404
