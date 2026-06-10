"""Security Phase 1 — authorization gate regression tests.

Verifies the fail-closed auth middleware + per-route RBAC:
- anonymous (auth on) is rejected (401) on non-allowlisted routes
- viewers cannot mutate (403); operators cannot reach admin routes (403)
- admins can; self-service routes work for any authenticated user
- single-user mode (GLUON_AUTH_ENABLED unset) leaves everything reachable
- the anonymous allowlist (version, login) still works
- GET /api/settings never returns secret values

Status assertions use "not in (401, 403)" for *allowed* cases: the route may
still 404/422 on a missing resource, but anything other than 401/403 means the
auth gate let it through.
"""

from __future__ import annotations

import pytest

from gluon.auth import LocalAuthProvider
from gluon.models import UserRole
from gluon.store import GluonStore


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")


@pytest.fixture
def seeded_users(temp_store: GluonStore):
    provider = LocalAuthProvider(temp_store)
    return {
        "admin": provider.create_user(username="alice", password="correcthorsebatterystaple", role=UserRole.ADMIN),
        "operator": provider.create_user(username="bob", password="correcthorsebatterystaple", role=UserRole.OPERATOR),
        "viewer": provider.create_user(username="carol", password="correcthorsebatterystaple", role=UserRole.VIEWER),
    }


def _login(client, username: str) -> None:
    resp = client.post("/api/auth/login", json={"username": username, "password": "correcthorsebatterystaple"})
    assert resp.status_code == 200, resp.text


# A representative destructive/admin route per tier.
ANON_MUTATIONS = [
    ("delete", "/api/projects/does-not-exist"),
    ("post", "/api/projects"),
    ("put", "/api/settings/foo"),
    ("post", "/api/webhooks"),
    ("delete", "/api/branches/somebranch"),
]
ADMIN_ROUTES = [
    ("get", "/api/settings"),
    ("put", "/api/settings/foo"),
    ("post", "/api/webhooks"),
    ("post", "/api/vercel/test"),
]
OPERATOR_MUTATIONS = [
    ("delete", "/api/projects/does-not-exist"),
    ("post", "/api/runs/does-not-exist/cancel"),
]


def _call(client, method: str, path: str):
    fn = getattr(client, method)
    if method in ("post", "put", "patch"):
        return fn(path, json={})
    return fn(path)


class TestAnonymousDenied:
    @pytest.mark.parametrize(("method", "path"), ANON_MUTATIONS + [("get", "/api/runs"), ("get", "/api/settings")])
    def test_no_session_is_401(self, api_client, seeded_users, auth_enabled, method, path):
        client, _ = api_client
        resp = _call(client, method, path)
        assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"


class TestViewerCannotMutate:
    @pytest.mark.parametrize(("method", "path"), ANON_MUTATIONS)
    def test_viewer_mutation_is_403(self, api_client, seeded_users, auth_enabled, method, path):
        client, _ = api_client
        _login(client, "carol")
        resp = _call(client, method, path)
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"

    def test_viewer_can_read(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        _login(client, "carol")
        assert client.get("/api/runs").status_code not in (401, 403)


class TestOperator:
    @pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
    def test_operator_blocked_from_admin(self, api_client, seeded_users, auth_enabled, method, path):
        client, _ = api_client
        _login(client, "bob")
        resp = _call(client, method, path)
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"

    @pytest.mark.parametrize(("method", "path"), OPERATOR_MUTATIONS)
    def test_operator_allowed_on_operations(self, api_client, seeded_users, auth_enabled, method, path):
        client, _ = api_client
        _login(client, "bob")
        resp = _call(client, method, path)
        assert resp.status_code not in (401, 403), f"{method} {path} → {resp.status_code}"


class TestAdminAllowed:
    @pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES + OPERATOR_MUTATIONS)
    def test_admin_passes_gate(self, api_client, seeded_users, auth_enabled, method, path):
        client, _ = api_client
        _login(client, "alice")
        resp = _call(client, method, path)
        assert resp.status_code not in (401, 403), f"{method} {path} → {resp.status_code}"


class TestSelfService:
    def test_viewer_can_mark_notifications(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        _login(client, "carol")
        # 404 (no such notification) is fine — the point is it's not 401/403.
        assert client.post("/api/notifications/x/read", json={}).status_code not in (401, 403)


class TestSingleUserMode:
    @pytest.mark.parametrize(("method", "path"), ANON_MUTATIONS + ADMIN_ROUTES)
    def test_everything_reachable_when_auth_off(self, api_client, monkeypatch, method, path):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = _call(client, method, path)
        assert resp.status_code not in (401, 403), f"{method} {path} → {resp.status_code}"


class TestAnonymousAllowlist:
    def test_version_is_anonymous(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        assert client.get("/api/version").status_code == 200

    def test_login_is_anonymous(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        assert client.post("/api/auth/login", json={"username": "alice", "password": "x"}).status_code in (200, 401)


class TestSettingsRedaction:
    def test_secrets_are_masked(self, api_client, seeded_users, auth_enabled, temp_store: GluonStore):
        temp_store.set_setting("github_webhook_secret", "super-secret-value")
        temp_store.set_setting("auto_create_pr", "true")
        client, _ = api_client
        _login(client, "alice")
        data = client.get("/api/settings").json()
        assert data["github_webhook_secret"] == "********"
        assert data["auto_create_pr"] == "true"  # non-secret untouched
