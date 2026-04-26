"""Tests for D5 Phase 1 — identity foundation (auth.py + store CRUD)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gluon.auth import (
    SYSTEM_USER,
    SYSTEM_USER_ID,
    AuthBackend,
    AuthError,
    InvalidCredentialsError,
    LocalAuthProvider,
    UserDisabledError,
    create_session_for_user,
    get_auth_provider,
    is_auth_enabled,
    new_session_secret,
    resolve_session,
)
from gluon.models import AuthProvider as AuthProviderEnum
from gluon.models import UserRole, utc_now
from gluon.store import GluonStore


@pytest.fixture
def store(tmp_path: Path) -> GluonStore:
    return GluonStore(tmp_path / "test.db")


@pytest.fixture
def provider(store: GluonStore) -> LocalAuthProvider:
    return LocalAuthProvider(store)


# ---------------------------------------------------------------------------
# SYSTEM_USER
# ---------------------------------------------------------------------------


class TestSystemUser:
    def test_system_user_id_is_all_zeros(self):
        assert SYSTEM_USER.id == SYSTEM_USER_ID == "00000000-0000-0000-0000-000000000000"

    def test_system_user_is_admin(self):
        assert SYSTEM_USER.role == UserRole.ADMIN

    def test_system_user_uses_system_provider(self):
        assert SYSTEM_USER.auth_provider == AuthProviderEnum.SYSTEM

    def test_system_user_not_disabled(self):
        assert SYSTEM_USER.disabled is False

    def test_system_user_is_not_persisted(self, store):
        # The whole point — SYSTEM_USER is never written to the DB.
        assert store.get_user(SYSTEM_USER_ID) is None


# ---------------------------------------------------------------------------
# is_auth_enabled()
# ---------------------------------------------------------------------------


class TestIsAuthEnabled:
    def test_default_is_off(self, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        assert is_auth_enabled() is False

    @pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "True"])
    def test_truthy_values(self, monkeypatch, truthy):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", truthy)
        assert is_auth_enabled() is True

    @pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "", "2", "enabled"])
    def test_falsy_values(self, monkeypatch, falsy):
        monkeypatch.setenv("GLUON_AUTH_ENABLED", falsy)
        assert is_auth_enabled() is False


# ---------------------------------------------------------------------------
# LocalAuthProvider — password hashing
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_returns_argon2_string(self, provider):
        hashed = provider.hash_password("correcthorsebatterystaple")
        assert hashed.startswith("$argon2")  # argon2-cffi uses $argon2id$ by default

    def test_hash_is_nondeterministic(self, provider):
        """Same password twice → different hashes (because of random salt)."""
        h1 = provider.hash_password("correcthorsebatterystaple")
        h2 = provider.hash_password("correcthorsebatterystaple")
        assert h1 != h2

    def test_verify_correct_password(self, provider):
        hashed = provider.hash_password("correcthorsebatterystaple")
        assert provider.verify_password(hashed, "correcthorsebatterystaple") is True

    def test_verify_wrong_password(self, provider):
        hashed = provider.hash_password("correcthorsebatterystaple")
        assert provider.verify_password(hashed, "wrongpassword1234") is False

    def test_verify_malformed_hash_returns_false(self, provider):
        assert provider.verify_password("not-a-real-hash", "anything") is False

    def test_hash_rejects_empty_password(self, provider):
        with pytest.raises(ValueError, match="non-empty"):
            provider.hash_password("")

    def test_hash_rejects_short_password(self, provider):
        with pytest.raises(ValueError, match="12 characters"):
            provider.hash_password("short")


# ---------------------------------------------------------------------------
# LocalAuthProvider — create_user + authenticate
# ---------------------------------------------------------------------------


class TestLocalAuthCreateUser:
    def test_create_user_basic(self, provider):
        user = provider.create_user(
            username="alice",
            password="correcthorsebatterystaple",
            display_name="Alice Example",
            email="alice@example.com",
            role=UserRole.ADMIN,
        )
        assert user.username == "alice"
        assert user.display_name == "Alice Example"
        assert user.email == "alice@example.com"
        assert user.role == UserRole.ADMIN
        assert user.auth_provider == AuthProviderEnum.LOCAL
        assert user.auth_subject.startswith("$argon2")

    def test_create_user_default_display_name_is_username(self, provider):
        user = provider.create_user(username="bob", password="somepassword12345")
        assert user.display_name == "bob"

    def test_create_user_default_role_is_operator(self, provider):
        user = provider.create_user(username="bob", password="somepassword12345")
        assert user.role == UserRole.OPERATOR

    def test_create_user_rejects_short_password(self, provider):
        with pytest.raises(ValueError):
            provider.create_user(username="bob", password="short")

    def test_create_user_duplicate_username_raises(self, provider):
        provider.create_user(username="alice", password="somepassword12345")
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            provider.create_user(username="alice", password="anotherpass12345")


class TestLocalAuthAuthenticate:
    def test_authenticate_happy_path(self, provider):
        provider.create_user(username="alice", password="correcthorsebatterystaple")
        user = provider.authenticate("alice", "correcthorsebatterystaple")
        assert user.username == "alice"
        assert user.last_login_at is not None

    def test_authenticate_updates_last_login_at(self, store, provider):
        provider.create_user(username="alice", password="correcthorsebatterystaple")
        before = provider.authenticate("alice", "correcthorsebatterystaple")
        first_login = before.last_login_at
        assert first_login is not None

        # Second login → last_login_at moves forward
        import time

        time.sleep(0.01)
        after = provider.authenticate("alice", "correcthorsebatterystaple")
        assert after.last_login_at > first_login

    def test_authenticate_unknown_user_raises_invalid_credentials(self, provider):
        with pytest.raises(InvalidCredentialsError):
            provider.authenticate("ghost", "somepassword12345")

    def test_authenticate_wrong_password_raises_invalid_credentials(self, provider):
        provider.create_user(username="alice", password="correcthorsebatterystaple")
        with pytest.raises(InvalidCredentialsError):
            provider.authenticate("alice", "wrongpassword12345")

    def test_authenticate_disabled_user_raises(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        user.disabled = True
        store.update_user(user)
        with pytest.raises(UserDisabledError):
            provider.authenticate("alice", "correcthorsebatterystaple")

    def test_authenticate_oidc_user_not_accepted_by_local(self, store, provider):
        """A user created under OIDC provider cannot log in via local — even if the
        `auth_subject` field happens to match a password string."""
        store.create_user(
            username="ext",
            display_name="External",
            auth_subject="oidc-sub-claim-here",
            auth_provider="oidc",
        )
        with pytest.raises(InvalidCredentialsError):
            provider.authenticate("ext", "oidc-sub-claim-here")

    def test_set_password_rotates_sessions(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        # Give them some sessions
        s1 = create_session_for_user(store, user)
        s2 = create_session_for_user(store, user)
        assert store.get_user_session(s1.id) is not None
        assert store.get_user_session(s2.id) is not None

        provider.set_password(user, "newpassword12345678")
        assert store.get_user_session(s1.id) is None
        assert store.get_user_session(s2.id) is None


# ---------------------------------------------------------------------------
# get_auth_provider() factory
# ---------------------------------------------------------------------------


class TestGetAuthProvider:
    def test_default_is_local(self, store, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_BACKEND", raising=False)
        provider = get_auth_provider(store)
        assert isinstance(provider, LocalAuthProvider)

    def test_env_var_local(self, store, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_BACKEND", "local")
        provider = get_auth_provider(store)
        assert isinstance(provider, LocalAuthProvider)

    def test_env_var_case_insensitive(self, store, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_BACKEND", "LOCAL")
        provider = get_auth_provider(store)
        assert isinstance(provider, LocalAuthProvider)

    def test_explicit_argument_overrides_env(self, store, monkeypatch):
        monkeypatch.setenv("GLUON_AUTH_BACKEND", "oidc")
        provider = get_auth_provider(store, AuthBackend.LOCAL)
        assert isinstance(provider, LocalAuthProvider)

    def test_oidc_raises_when_env_unset(self, store, monkeypatch):
        """Phase 3: requesting OIDC explicitly raises a clear error if the
        env vars aren't set. (Phase 1 used to NotImplementedError here;
        OIDC is now implemented and this path validates env presence.)"""
        for v in (
            "GLUON_OIDC_ISSUER",
            "GLUON_OIDC_CLIENT_ID",
            "GLUON_OIDC_CLIENT_SECRET",
            "GLUON_OIDC_REDIRECT_URI",
        ):
            monkeypatch.delenv(v, raising=False)
        with pytest.raises(ValueError, match="OIDC env vars are unset"):
            get_auth_provider(store, "oidc")

    def test_oidc_returns_provider_when_env_set(self, store, monkeypatch):
        from gluon.auth import OIDCAuthProvider

        monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")
        monkeypatch.setenv("GLUON_OIDC_ISSUER", "https://example.com")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_ID", "x")
        monkeypatch.setenv("GLUON_OIDC_CLIENT_SECRET", "y")
        monkeypatch.setenv("GLUON_OIDC_REDIRECT_URI", "https://x/cb")
        provider = get_auth_provider(store, "oidc")
        assert isinstance(provider, OIDCAuthProvider)

    def test_unknown_backend_raises(self, store):
        with pytest.raises(ValueError, match="Unknown auth backend"):
            get_auth_provider(store, "ldap")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    def test_create_session_for_user(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        session = create_session_for_user(store, user)
        assert session.user_id == user.id
        assert session.expires_at > utc_now()

    def test_create_session_custom_ttl(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        session = create_session_for_user(store, user, ttl=timedelta(hours=1))
        # expires in ~1 hour
        delta = session.expires_at - utc_now()
        assert timedelta(minutes=55) < delta < timedelta(hours=1, minutes=5)

    def test_resolve_session_happy_path(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        session = create_session_for_user(store, user)
        result = resolve_session(store, session.id)
        assert result is not None
        resolved_user, resolved_session = result
        assert resolved_user.id == user.id
        assert resolved_session.id == session.id

    def test_resolve_session_unknown_id(self, store):
        assert resolve_session(store, "does-not-exist") is None

    def test_resolve_session_expired(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        # Create a session that's already expired
        from datetime import datetime

        past = datetime(2020, 1, 1, tzinfo=utc_now().tzinfo)
        store.create_user_session(
            user_id=user.id,
            expires_at=past,
        )
        # Any session created directly with past expiry won't resolve
        sessions_in_db = [r for r in store._get_conn().execute("SELECT id FROM user_sessions").fetchall()]
        assert len(sessions_in_db) == 1
        sid = sessions_in_db[0][0]
        assert resolve_session(store, sid) is None

    def test_resolve_session_disabled_user_invalidates_session(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        session = create_session_for_user(store, user)
        # Now disable the user
        user.disabled = True
        store.update_user(user)
        # resolve_session should return None AND clean up the session
        assert resolve_session(store, session.id) is None
        assert store.get_user_session(session.id) is None

    def test_resolve_session_rolls_expiry_past_half_life(self, store, provider):
        """Session past its half-life gets its expiry rolled forward."""
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        # Create with a 2-hour TTL
        session = create_session_for_user(store, user, ttl=timedelta(hours=2))
        original_expires = session.expires_at

        # Simulate resolving past half-life (> 1 hour later)
        future_now = utc_now() + timedelta(hours=1, minutes=5)
        result = resolve_session(store, session.id, now=future_now, ttl=timedelta(hours=2))
        assert result is not None
        _, refreshed = result
        # expires_at should have moved forward
        assert refreshed.expires_at > original_expires

    def test_delete_sessions_for_user(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        create_session_for_user(store, user)
        create_session_for_user(store, user)
        create_session_for_user(store, user)
        count = store.delete_user_sessions_for_user(user.id)
        assert count == 3

    def test_delete_expired_sessions(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        # 3 expired + 2 live
        past = utc_now() - timedelta(days=1)
        future = utc_now() + timedelta(days=1)
        for _ in range(3):
            store.create_user_session(user_id=user.id, expires_at=past)
        for _ in range(2):
            store.create_user_session(user_id=user.id, expires_at=future)
        deleted = store.delete_expired_user_sessions()
        assert deleted == 3

    def test_new_session_secret_is_random(self):
        s1 = new_session_secret()
        s2 = new_session_secret()
        assert s1 != s2
        assert len(s1) >= 32


# ---------------------------------------------------------------------------
# Store CRUD edges
# ---------------------------------------------------------------------------


class TestStoreUserCRUD:
    def test_list_users_empty(self, store):
        assert store.list_users() == []

    def test_list_users_excludes_disabled_by_default(self, store, provider):
        provider.create_user(username="alice", password="correcthorsebatterystaple")
        u2 = provider.create_user(username="bob", password="correcthorsebatterystaple")
        u2.disabled = True
        store.update_user(u2)

        listed = store.list_users()
        names = [u.username for u in listed]
        assert "alice" in names
        assert "bob" not in names

    def test_list_users_include_disabled(self, store, provider):
        provider.create_user(username="alice", password="correcthorsebatterystaple")
        u2 = provider.create_user(username="bob", password="correcthorsebatterystaple")
        u2.disabled = True
        store.update_user(u2)

        listed = store.list_users(include_disabled=True)
        assert {u.username for u in listed} == {"alice", "bob"}

    def test_get_user_by_username_case_insensitive(self, store, provider):
        provider.create_user(username="Alice", password="correcthorsebatterystaple")
        assert store.get_user_by_username("ALICE") is not None
        assert store.get_user_by_username("alice") is not None
        assert store.get_user_by_username("AlIcE") is not None

    def test_get_user_by_auth_subject(self, store, provider):
        u = provider.create_user(username="alice", password="correcthorsebatterystaple")
        found = store.get_user_by_auth_subject("local", u.auth_subject)
        assert found is not None
        assert found.id == u.id

    def test_telegram_link_roundtrip(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        user.telegram_user_id = 12345
        store.update_user(user)
        found = store.get_user_by_telegram_id(12345)
        assert found is not None
        assert found.id == user.id

    def test_discord_link_roundtrip(self, store, provider):
        user = provider.create_user(username="alice", password="correcthorsebatterystaple")
        user.discord_user_id = 54321
        store.update_user(user)
        found = store.get_user_by_discord_id(54321)
        assert found is not None
        assert found.id == user.id


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_invalid_credentials_is_auth_error(self):
        assert issubclass(InvalidCredentialsError, AuthError)

    def test_user_disabled_is_auth_error(self):
        assert issubclass(UserDisabledError, AuthError)
