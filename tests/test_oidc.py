"""Tests for D5 Phase 3 — OIDC authentication.

Three layers covered:
- ``OIDCConfig.from_env`` — env-var parsing and validation guardrails
- ``OIDCAuthProvider.resolve_or_provision`` — the core decision logic
  (sub match → email pre-reg → auto-provision → reject)
- ``GET /api/auth/providers`` — feature-detection endpoint that the
  login page hits to know which buttons to render

The actual Authlib redirect/callback dance is not unit-tested here — it
needs real network calls to the IdP's discovery endpoint. Integration is
verified by the smoke-test script in docs/AUTH-OIDC.md.
"""

from __future__ import annotations

import pytest

from gluon.auth import (
    InvalidCredentialsError,
    OIDCAuthProvider,
    OIDCConfig,
    UserDisabledError,
    get_oidc_provider,
)
from gluon.models import AuthProvider, UserRole
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# OIDCConfig.from_env
# ---------------------------------------------------------------------------


class TestOIDCConfigFromEnv:
    """Env-var parsing and the key safety guardrail (auto-provision needs allowlist)."""

    def test_returns_none_when_unset(self, monkeypatch):
        for v in (
            "GLUON_OIDC_ISSUER",
            "GLUON_OIDC_CLIENT_ID",
            "GLUON_OIDC_CLIENT_SECRET",
            "GLUON_OIDC_REDIRECT_URI",
        ):
            monkeypatch.delenv(v, raising=False)
        assert OIDCConfig.from_env() is None

    def test_returns_none_if_any_required_missing(self, monkeypatch):
        # Only set 3 of the 4 required vars
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://example.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "abc")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "shh")
        monkeypatch.delenv("GLUON_OIDC_REDIRECT_URI", raising=False)
        assert OIDCConfig.from_env() is None

    def test_full_config(self, monkeypatch):
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://accounts.google.com/")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "client-abc")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "secret-xyz")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://gluon.example.com/cb")
        monkeypatch.setenv("GLUON_OIDC_PROVIDER_NAME", "Google")
        monkeypatch.setenv("GLUON_OIDC_AUTO_PROVISION", "true")
        monkeypatch.setenv("GLUON_OIDC_DOMAIN_ALLOWLIST", "example.com,other.org")
        monkeypatch.setenv("GLUON_OIDC_DEFAULT_ROLE", "operator")

        cfg = OIDCConfig.from_env()
        assert cfg is not None
        # Trailing slash on issuer is normalized off
        assert cfg.issuer == "https://accounts.google.com"
        assert cfg.client_id == "client-abc"
        assert cfg.client_secret == "secret-xyz"
        assert cfg.provider_name == "Google"
        assert cfg.auto_provision is True
        assert cfg.domain_allowlist == ("example.com", "other.org")
        assert cfg.default_role == UserRole.OPERATOR

    def test_auto_provision_without_allowlist_rejected(self, monkeypatch):
        """The single most security-relevant guardrail: refusing to auto-
        create users without an email-domain restriction."""
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://example.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "x")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "y")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://x/cb")
        monkeypatch.setenv("GLUON_OIDC_AUTO_PROVISION", "true")
        monkeypatch.delenv("GLUON_OIDC_DOMAIN_ALLOWLIST", raising=False)
        with pytest.raises(ValueError, match="DOMAIN_ALLOWLIST"):
            OIDCConfig.from_env()

    def test_invalid_default_role(self, monkeypatch):
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://example.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "x")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "y")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://x/cb")
        monkeypatch.setenv("GLUON_OIDC_DEFAULT_ROLE", "superhero")
        with pytest.raises(ValueError, match="DEFAULT_ROLE"):
            OIDCConfig.from_env()


# ---------------------------------------------------------------------------
# get_oidc_provider — feature-detection helper
# ---------------------------------------------------------------------------


class TestGetOidcProvider:
    def test_returns_none_when_auth_disabled(self, temp_store: GluonStore, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        # Even with full OIDC env, auth disabled overrides
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://example.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "x")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "y")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://x/cb")
        assert get_oidc_provider(temp_store) is None

    def test_returns_none_when_oidc_not_configured(self, temp_store: GluonStore, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        for v in (
            "GLUON_OIDC_ISSUER",
            "GLUON_OIDC_CLIENT_ID",
            "GLUON_OIDC_CLIENT_SECRET",
            "GLUON_OIDC_REDIRECT_URI",
        ):
            monkeypatch.delenv(v, raising=False)
        assert get_oidc_provider(temp_store) is None

    def test_returns_provider_when_configured(self, temp_store: GluonStore, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://accounts.google.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "x")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "y")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://x/cb")
        provider = get_oidc_provider(temp_store)
        assert provider is not None
        assert provider.provider == AuthProvider.OIDC
        assert "Google" not in provider.name  # no name override → defaults to "OIDC"


# ---------------------------------------------------------------------------
# OIDCAuthProvider.resolve_or_provision
# ---------------------------------------------------------------------------


@pytest.fixture
def oidc_strict(temp_store: GluonStore):
    """OIDC provider with auto-provision OFF (the safer default)."""
    cfg = OIDCConfig(
        issuer="https://example.com",
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://gluon.example.com/cb",
    )
    return OIDCAuthProvider(temp_store, cfg)


@pytest.fixture
def oidc_auto(temp_store: GluonStore):
    """OIDC provider with auto-provision ON + a domain allowlist."""
    cfg = OIDCConfig(
        issuer="https://example.com",
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://gluon.example.com/cb",
        auto_provision=True,
        domain_allowlist=("example.com",),
        default_role=UserRole.VIEWER,
    )
    return OIDCAuthProvider(temp_store, cfg)


class TestEmailInAllowlist:
    def test_allowed_when_no_allowlist(self):
        cfg = OIDCConfig(issuer="x", client_id="x", client_secret="x", redirect_uri="x")
        provider = OIDCAuthProvider.__new__(OIDCAuthProvider)
        provider.config = cfg
        assert provider.email_in_allowlist("anything@anywhere.com") is True

    def test_allowed_when_domain_matches(self, oidc_auto):
        assert oidc_auto.email_in_allowlist("alice@example.com") is True

    def test_blocked_when_domain_does_not_match(self, oidc_auto):
        assert oidc_auto.email_in_allowlist("alice@evil.com") is False

    def test_blocked_when_email_missing(self, oidc_auto):
        assert oidc_auto.email_in_allowlist(None) is False
        assert oidc_auto.email_in_allowlist("") is False
        assert oidc_auto.email_in_allowlist("not-an-email") is False

    def test_case_insensitive_domain(self, oidc_auto):
        assert oidc_auto.email_in_allowlist("alice@EXAMPLE.COM") is True


class TestResolveOrProvisionStrict:
    """auto_provision=False — only pre-registered users can sign in."""

    def test_unknown_user_rejected(self, oidc_strict):
        with pytest.raises(InvalidCredentialsError):
            oidc_strict.resolve_or_provision(sub="auth0|stranger", email="stranger@example.com", display_name="X")

    def test_pre_registered_by_email_swaps_subject(self, oidc_strict, temp_store: GluonStore):
        """Admin runs `gluon user add alice --auth-provider oidc --email alice@…`
        which creates a User with auth_subject=email. First OIDC login swaps
        the placeholder for the real `sub`."""
        temp_store.create_user(
            username="alice",
            display_name="Alice",
            email="alice@example.com",
            auth_subject="alice@example.com",  # placeholder
            auth_provider="oidc",
            role=UserRole.ADMIN,
        )

        bound = oidc_strict.resolve_or_provision(
            sub="auth0|abc123",
            email="alice@example.com",
            display_name="Alice from IdP",
        )
        assert bound.username == "alice"
        assert bound.auth_subject == "auth0|abc123"  # placeholder replaced
        assert bound.role == UserRole.ADMIN

        # Subsequent login by sub matches step 1 directly.
        bound2 = oidc_strict.resolve_or_provision(sub="auth0|abc123", email="alice@example.com", display_name="Alice")
        assert bound2.id == bound.id

    def test_disabled_user_rejected(self, oidc_strict, temp_store: GluonStore):
        u = temp_store.create_user(
            username="alice",
            display_name="Alice",
            email="alice@example.com",
            auth_subject="auth0|abc",
            auth_provider="oidc",
        )
        u.disabled = True
        temp_store.update_user(u)

        with pytest.raises(UserDisabledError):
            oidc_strict.resolve_or_provision(sub="auth0|abc", email="alice@example.com", display_name="Alice")


class TestResolveOrProvisionAuto:
    """auto_provision=True — new sub auto-creates users (with allowlist)."""

    def test_creates_new_user_within_allowlist(self, oidc_auto, temp_store: GluonStore):
        bound = oidc_auto.resolve_or_provision(sub="auth0|new", email="newuser@example.com", display_name="New")
        assert bound.username == "newuser"
        assert bound.auth_provider == AuthProvider.OIDC
        assert bound.auth_subject == "auth0|new"
        assert bound.role == UserRole.VIEWER  # default for auto-provisioned

    def test_username_collision_appends_suffix(self, oidc_auto, temp_store: GluonStore):
        # Pre-create someone with the obvious username
        temp_store.create_user(
            username="newuser",
            display_name="Existing",
            auth_subject="local-pwd-hash",
            auth_provider="local",
        )
        bound = oidc_auto.resolve_or_provision(sub="auth0|new", email="newuser@example.com", display_name="New")
        # Suffix appended — original "newuser" intact
        assert bound.username == "newuser2"

    def test_blocked_outside_allowlist(self, oidc_auto):
        with pytest.raises(InvalidCredentialsError):
            oidc_auto.resolve_or_provision(sub="auth0|evil", email="evil@evil.com", display_name="Evil")

    def test_blocked_when_no_email(self, oidc_auto):
        """Auto-provision needs an email for the allowlist check."""
        with pytest.raises(InvalidCredentialsError):
            oidc_auto.resolve_or_provision(sub="auth0|anon", email=None, display_name=None)


# ---------------------------------------------------------------------------
# /api/auth/providers endpoint
# ---------------------------------------------------------------------------


class TestAuthProvidersEndpoint:
    def test_auth_disabled_reports_nothing(self, api_client, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = client.get("/api/auth/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["auth_enabled"] is False
        assert body["local"] is False
        assert body["oidc"] is None

    def test_auth_enabled_local_only(self, api_client, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        for v in (
            "GLUON_OIDC_ISSUER",
            "GLUON_OIDC_CLIENT_ID",
            "GLUON_OIDC_CLIENT_SECRET",
            "GLUON_OIDC_REDIRECT_URI",
        ):
            monkeypatch.delenv(v, raising=False)
        client, _ = api_client
        resp = client.get("/api/auth/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["auth_enabled"] is True
        assert body["local"] is True
        assert body["oidc"] is None

    def test_auth_enabled_with_oidc(self, api_client, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://accounts.google.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "x")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "y")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://gluon.example.com/cb")
        monkeypatch.setenv("GLUON_OIDC_PROVIDER_NAME", "Google")

        client, _ = api_client
        resp = client.get("/api/auth/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["auth_enabled"] is True
        assert body["local"] is True
        assert body["oidc"] is not None
        assert body["oidc"]["name"] == "Google"
        assert "/api/auth/oidc/login" in body["oidc"]["login_url"]

    def test_local_can_be_disabled_for_oidc_only_mode(self, api_client, monkeypatch):
        """Set GLUON_LOCAL_AUTH_ENABLED=false to refuse password logins entirely.
        Useful when you want SSO to be the only path — no fallback password
        endpoint that an attacker could brute-force."""
        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        monkeypatch.setenv("GLUON_LOCAL_AUTH_ENABLED", "false")
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://accounts.google.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "x")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "y")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://gluon.example.com/cb")

        client, _ = api_client
        resp = client.get("/api/auth/providers")
        body = resp.json()
        assert body["local"] is False
        assert body["oidc"] is not None


# ---------------------------------------------------------------------------
# OIDC redirect endpoint — minimal smoke test (we mock the IdP discovery)
# ---------------------------------------------------------------------------


class TestOidcLoginRedirect503WhenNotConfigured:
    """When OIDC env vars are missing, /api/auth/oidc/login should refuse."""

    def test_503_when_not_configured(self, api_client, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        for v in (
            "GLUON_OIDC_ISSUER",
            "GLUON_OIDC_CLIENT_ID",
            "GLUON_OIDC_CLIENT_SECRET",
            "GLUON_OIDC_REDIRECT_URI",
        ):
            monkeypatch.delenv(v, raising=False)
        client, _ = api_client
        resp = client.get("/api/auth/oidc/login", follow_redirects=False)
        assert resp.status_code == 503
