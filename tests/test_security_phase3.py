"""Security Phase 3 — release-readiness hardening regression tests.

Covers:
- the insecure-bind guard (refuse non-loopback + auth-off unless overridden)
- per-IP rate limiting on the auth endpoints
- defensive security response headers
- the privileged-action audit trail
- env-var read paths never returning values
"""

from __future__ import annotations

import pytest

from gluon.auth import LocalAuthProvider, insecure_bind_error, is_loopback_host
from gluon.models import UserRole
from gluon.store import GluonStore
from gluon.web.api import _SlidingWindowLimiter


# ---------------------------------------------------------------------------
# Insecure-bind guard
# ---------------------------------------------------------------------------
class TestInsecureBindGuard:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "", "127.0.0.5"])
    def test_loopback_hosts(self, host):
        assert is_loopback_host(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
    def test_non_loopback_hosts(self, host):
        assert not is_loopback_host(host)

    def test_loopback_bind_always_allowed(self, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        monkeypatch.delenv("GLUON_INSECURE_OK", raising=False)
        assert insecure_bind_error("127.0.0.1") is None

    def test_public_bind_auth_off_refused(self, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        monkeypatch.delenv("GLUON_INSECURE_OK", raising=False)
        err = insecure_bind_error("0.0.0.0")
        assert err is not None
        assert "GLUON_INSECURE_OK" in err

    def test_public_bind_auth_on_allowed(self, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        monkeypatch.delenv("GLUON_INSECURE_OK", raising=False)
        assert insecure_bind_error("0.0.0.0") is None

    def test_public_bind_override_allowed(self, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("GLUON_INSECURE_OK", "1")
        assert insecure_bind_error("0.0.0.0") is None


# ---------------------------------------------------------------------------
# Sliding-window limiter (unit)
# ---------------------------------------------------------------------------
class TestSlidingWindowLimiter:
    def test_allows_up_to_budget_then_blocks(self):
        lim = _SlidingWindowLimiter(max_events=3, window_secs=60.0)
        assert lim.allow("k", now=0.0)
        assert lim.allow("k", now=1.0)
        assert lim.allow("k", now=2.0)
        assert not lim.allow("k", now=3.0)  # 4th in window → blocked

    def test_window_slides(self):
        lim = _SlidingWindowLimiter(max_events=2, window_secs=10.0)
        assert lim.allow("k", now=0.0)
        assert lim.allow("k", now=1.0)
        assert not lim.allow("k", now=2.0)
        # After the window passes, old hits expire and new ones are allowed.
        assert lim.allow("k", now=12.0)

    def test_keys_are_independent(self):
        lim = _SlidingWindowLimiter(max_events=1, window_secs=60.0)
        assert lim.allow("a", now=0.0)
        assert lim.allow("b", now=0.0)
        assert not lim.allow("a", now=1.0)


# ---------------------------------------------------------------------------
# Rate limiting (integration)
# ---------------------------------------------------------------------------
class TestAuthRateLimiting:
    def test_login_throttled_after_budget(self, api_client, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)  # auth off → login 400s
        client, _ = api_client
        # Budget is 10/min per IP; the 11th call within the window is throttled.
        statuses = [
            client.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code for _ in range(11)
        ]
        assert 429 in statuses
        assert statuses[-1] == 429


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    def test_headers_present_on_anonymous_route(self, api_client):
        client, _ = api_client
        resp = client.get("/api/version")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert "referrer-policy" in {k.lower() for k in resp.headers}

    def test_no_hsts_on_plain_http(self, api_client):
        client, _ = api_client
        resp = client.get("/api/version")
        # TestClient speaks http:// — HSTS must not be emitted.
        assert "strict-transport-security" not in {k.lower() for k in resp.headers}


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")


class TestAuditTrail:
    def test_privileged_mutation_is_logged(self, api_client, temp_store: GluonStore, auth_enabled):
        provider = LocalAuthProvider(temp_store)
        provider.create_user(username="alice", password="correcthorsebatterystaple", role=UserRole.ADMIN)
        client, _ = api_client
        assert (
            client.post(
                "/api/auth/login", json={"username": "alice", "password": "correcthorsebatterystaple"}
            ).status_code
            == 200
        )

        # A privileged (operator+) mutation — 404 is fine; the point is it's audited.
        client.delete("/api/projects/does-not-exist")

        actions = [a.action for a in temp_store.list_activities(actor="alice")]
        assert any("DELETE /api/projects/does-not-exist" in a for a in actions)

    def test_read_is_not_logged(self, api_client, temp_store: GluonStore, auth_enabled):
        provider = LocalAuthProvider(temp_store)
        provider.create_user(username="alice", password="correcthorsebatterystaple", role=UserRole.ADMIN)
        client, _ = api_client
        client.post("/api/auth/login", json={"username": "alice", "password": "correcthorsebatterystaple"})

        client.get("/api/runs")

        actions = [a.action for a in temp_store.list_activities(actor="alice")]
        assert not any("GET /api/runs" in a for a in actions)


# ---------------------------------------------------------------------------
# Env-var read paths never leak values
# ---------------------------------------------------------------------------
class TestEnvVarMasking:
    def test_workspace_detail_omits_env_values(self, api_client, temp_store: GluonStore, tmp_path):
        ws_path = tmp_path / "ws"
        ws_path.mkdir()
        ws = temp_store.create_workspace("ws", ws_path)
        temp_store.set_workspace_setting(ws.id, "env.MY_SECRET", "topsecret-value")
        client, _ = api_client

        resp = client.get(f"/api/workspaces/{ws.id}")
        assert resp.status_code == 200
        assert "topsecret-value" not in resp.text
