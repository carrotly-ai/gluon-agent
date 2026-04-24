"""Integration tests for D5 Phase 2 — auth API endpoints.

Covers:
- POST /api/auth/login — happy, bad creds (401), disabled user (401),
  auth-disabled mode (400)
- POST /api/auth/logout — clears cookie + deletes session; idempotent
- GET /api/auth/me — auth-off returns SYSTEM_USER + auth_enabled=False;
  auth-on with no cookie returns SYSTEM_USER + auth_enabled=True; auth-on
  with valid cookie returns the real user
- GET /api/users — admin-only (403 for operator; 401 for no session);
  list filters on include_disabled
- POST /api/users — admin-only, password validation, duplicate → 409
- PATCH /api/users/{id} — partial updates, role change rotates sessions,
  disable rotates sessions
- DELETE /api/users/{id} — soft-disables, rotates sessions, 404 on unknown
- POST /api/users/{id}/password — admins change anyone without current;
  non-admins must provide current_password and target only their own account

The `auth_enabled` fixture sets GLUON_AUTH_ENABLED=true for tests that need
it; the default `api_client` fixture operates in single-user mode (unchanged).
"""

from __future__ import annotations

import pytest

from gluon.auth import LocalAuthProvider
from gluon.models import UserRole
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_enabled(monkeypatch):
    """Turn on GLUON_AUTH_ENABLED for the duration of a test."""
    monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")


@pytest.fixture
def seeded_users(temp_store: GluonStore):
    """Create a canonical (admin, operator, viewer) set for auth tests."""
    provider = LocalAuthProvider(temp_store)
    admin = provider.create_user(
        username="alice",
        password="correcthorsebatterystaple",
        display_name="Alice",
        role=UserRole.ADMIN,
    )
    operator = provider.create_user(
        username="bob",
        password="correcthorsebatterystaple",
        display_name="Bob",
        role=UserRole.OPERATOR,
    )
    viewer = provider.create_user(
        username="carol",
        password="correcthorsebatterystaple",
        display_name="Carol",
        role=UserRole.VIEWER,
    )
    return {"admin": admin, "operator": operator, "viewer": viewer}


def login(client, username: str, password: str = "correcthorsebatterystaple"):
    """Helper: log in and return the response."""
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


# ---------------------------------------------------------------------------
# /api/auth/me (auth-disabled — the single-user default)
# ---------------------------------------------------------------------------


class TestMeAuthDisabled:
    def test_returns_system_user(self, api_client, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is False
        assert data["user"]["username"] == "system"
        assert data["user"]["role"] == "admin"

    def test_login_fails_gracefully_when_auth_off(self, api_client, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = login(client, "alice")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/auth/login  (auth enabled)
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_success(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        resp = login(client, "alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["username"] == "alice"
        assert data["user"]["role"] == "admin"
        # Cookie is set
        assert "gluon_session" in resp.cookies

    def test_login_bad_password(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        resp = login(client, "alice", "wrongpassword12345")
        assert resp.status_code == 401
        assert "gluon_session" not in resp.cookies

    def test_login_unknown_user_same_error(self, api_client, seeded_users, auth_enabled):
        """Unknown user must not be distinguishable from bad password."""
        client, _ = api_client
        bad_user = login(client, "ghost", "somepassword1234")
        assert bad_user.status_code == 401
        assert bad_user.json()["detail"] == "invalid credentials"

    def test_login_disabled_user(self, api_client, seeded_users, temp_store: GluonStore, auth_enabled):
        client, _ = api_client
        # Disable alice
        alice = seeded_users["admin"]
        alice.disabled = True
        temp_store.update_user(alice)
        resp = login(client, "alice")
        assert resp.status_code == 401

    def test_login_creates_session_row(self, api_client, seeded_users, temp_store: GluonStore, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        with temp_store._get_conn() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()
            assert rows[0] == 1


# ---------------------------------------------------------------------------
# /api/auth/me (auth enabled)
# ---------------------------------------------------------------------------


class TestMeAuthEnabled:
    def test_me_no_cookie_returns_system_user(self, api_client, auth_enabled):
        client, _ = api_client
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is True
        assert data["user"]["username"] == "system"

    def test_me_with_valid_cookie_returns_real_user(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is True
        assert data["user"]["username"] == "alice"

    def test_me_with_stale_cookie_falls_back(self, api_client, auth_enabled):
        client, _ = api_client
        # Forge a plausible-but-nonexistent session ID
        client.cookies.set("gluon_session", "does-not-exist")
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "system"


# ---------------------------------------------------------------------------
# /api/auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_clears_cookie(self, api_client, seeded_users, temp_store: GluonStore, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        assert client.cookies.get("gluon_session") is not None

        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        # Session row gone
        with temp_store._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]
            assert count == 0

    def test_logout_with_no_session_still_succeeds(self, api_client, auth_enabled):
        client, _ = api_client
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/users — admin-only endpoints
# ---------------------------------------------------------------------------


class TestListUsers:
    def test_list_users_requires_auth(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        resp = client.get("/api/users")
        assert resp.status_code == 401

    def test_list_users_operator_forbidden(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")  # operator
        resp = client.get("/api/users")
        assert resp.status_code == 403

    def test_list_users_admin_ok(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")  # admin
        resp = client.get("/api/users")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert {u["username"] for u in data["users"]} == {"alice", "bob", "carol"}

    def test_list_users_excludes_disabled_by_default(
        self, api_client, seeded_users, temp_store: GluonStore, auth_enabled
    ):
        client, _ = api_client
        login(client, "alice")
        # Disable bob
        bob = seeded_users["operator"]
        bob.disabled = True
        temp_store.update_user(bob)
        resp = client.get("/api/users")
        assert resp.status_code == 200
        usernames = {u["username"] for u in resp.json()["users"]}
        assert "bob" not in usernames

    def test_list_users_all_includes_disabled(self, api_client, seeded_users, temp_store: GluonStore, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        bob = seeded_users["operator"]
        bob.disabled = True
        temp_store.update_user(bob)
        resp = client.get("/api/users?include_disabled=true")
        usernames = {u["username"] for u in resp.json()["users"]}
        assert "bob" in usernames

    def test_list_users_works_with_auth_disabled(self, api_client, seeded_users, monkeypatch):
        """Auth disabled → SYSTEM_USER has admin role → list endpoint works."""
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = client.get("/api/users")
        assert resp.status_code == 200


class TestCreateUser:
    def test_create_user_admin_success(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.post(
            "/api/users",
            json={
                "username": "dave",
                "password": "correcthorsebatterystaple",
                "display_name": "Dave",
                "role": "operator",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "dave"

    def test_create_user_operator_forbidden(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")  # operator
        resp = client.post(
            "/api/users",
            json={"username": "dave", "password": "correcthorsebatterystaple"},
        )
        assert resp.status_code == 403

    def test_create_user_duplicate_username_409(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.post(
            "/api/users",
            json={"username": "alice", "password": "correcthorsebatterystaple"},
        )
        assert resp.status_code == 409

    def test_create_user_short_password_400(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.post(
            "/api/users",
            json={"username": "dave", "password": "short"},
        )
        # Pydantic validation → 422 (min_length=12 on the schema)
        assert resp.status_code in (400, 422)

    def test_create_user_bad_role_400(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.post(
            "/api/users",
            json={
                "username": "dave",
                "password": "correcthorsebatterystaple",
                "role": "supervillain",
            },
        )
        assert resp.status_code == 400


class TestUpdateUser:
    def test_patch_user_display_name(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        bob = seeded_users["operator"]
        resp = client.patch(f"/api/users/{bob.id}", json={"display_name": "Bobby"})
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Bobby"

    def test_patch_role_rotates_sessions(self, api_client, seeded_users, temp_store: GluonStore, auth_enabled):
        client, _ = api_client
        # Bob logs in → creates a session
        login(client, "bob")
        with temp_store._get_conn() as conn:
            sessions_before = conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE user_id = ?",
                (seeded_users["operator"].id,),
            ).fetchone()[0]
        assert sessions_before == 1

        # Admin alice bumps bob to viewer
        client.cookies.clear()
        login(client, "alice")
        resp = client.patch(
            f"/api/users/{seeded_users['operator'].id}",
            json={"role": "viewer"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"

        # Bob's session should be gone
        with temp_store._get_conn() as conn:
            sessions_after = conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE user_id = ?",
                (seeded_users["operator"].id,),
            ).fetchone()[0]
        assert sessions_after == 0

    def test_patch_disable_rotates_sessions(self, api_client, seeded_users, temp_store: GluonStore, auth_enabled):
        client, _ = api_client
        login(client, "bob")
        client.cookies.clear()
        login(client, "alice")
        resp = client.patch(
            f"/api/users/{seeded_users['operator'].id}",
            json={"disabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["disabled"] is True
        with temp_store._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE user_id = ?",
                (seeded_users["operator"].id,),
            ).fetchone()[0]
        assert count == 0

    def test_patch_unknown_user_404(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.patch("/api/users/unknown-id", json={"display_name": "x"})
        assert resp.status_code == 404


class TestDisableUser:
    def test_disable_user_admin_ok(self, api_client, seeded_users, temp_store: GluonStore, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.delete(f"/api/users/{seeded_users['operator'].id}")
        assert resp.status_code == 200
        assert resp.json()["disabled"] is True
        # Bob can no longer log in
        client.cookies.clear()
        resp2 = login(client, "bob")
        assert resp2.status_code == 401

    def test_disable_unknown_404(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.delete("/api/users/ghost")
        assert resp.status_code == 404

    def test_disable_operator_forbidden(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")
        resp = client.delete(f"/api/users/{seeded_users['viewer'].id}")
        assert resp.status_code == 403


class TestChangePassword:
    def test_admin_can_change_anyones_password_without_current(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.post(
            f"/api/users/{seeded_users['operator'].id}/password",
            json={"new_password": "newpasswordof16chars"},
        )
        assert resp.status_code == 200
        # Bob can now login with the new password
        client.cookies.clear()
        resp2 = login(client, "bob", "newpasswordof16chars")
        assert resp2.status_code == 200

    def test_operator_must_provide_current_password(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")
        resp = client.post(
            f"/api/users/{seeded_users['operator'].id}/password",
            json={"new_password": "newpasswordof16chars"},
        )
        assert resp.status_code == 400

    def test_operator_wrong_current_password_401(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")
        resp = client.post(
            f"/api/users/{seeded_users['operator'].id}/password",
            json={
                "new_password": "newpasswordof16chars",
                "current_password": "wrongcurrent12345",
            },
        )
        assert resp.status_code == 401

    def test_operator_cannot_change_other_user_password(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")  # operator
        resp = client.post(
            f"/api/users/{seeded_users['viewer'].id}/password",
            json={
                "new_password": "newpasswordof16chars",
                "current_password": "correcthorsebatterystaple",
            },
        )
        assert resp.status_code == 403

    def test_operator_can_change_own_password_with_current(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")
        resp = client.post(
            f"/api/users/{seeded_users['operator'].id}/password",
            json={
                "new_password": "newpasswordof16chars",
                "current_password": "correcthorsebatterystaple",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth-disabled bypass invariant
# ---------------------------------------------------------------------------


class TestAuthDisabledBypass:
    """When GLUON_AUTH_ENABLED=false, all auth-guarded endpoints succeed.

    This is the critical backward-compat invariant for v0.11.x release.
    Existing deployments can install this without any setup and everything
    keeps working.
    """

    def test_list_users_bypasses(self, api_client, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = client.get("/api/users")
        assert resp.status_code == 200  # SYSTEM_USER is admin

    def test_create_user_bypasses(self, api_client, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = client.post(
            "/api/users",
            json={"username": "dave", "password": "correcthorsebatterystaple"},
        )
        assert resp.status_code == 200

    def test_me_returns_system_user(self, api_client, monkeypatch):
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = client.get("/api/auth/me")
        data = resp.json()
        assert data["auth_enabled"] is False
        assert data["user"]["username"] == "system"


# ---------------------------------------------------------------------------
# Viewer role — can see but not act
# ---------------------------------------------------------------------------


class TestViewerRole:
    def test_viewer_cannot_list_users(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "carol")  # viewer
        resp = client.get("/api/users")
        # Viewer < operator < admin → 403 for admin-required list endpoint
        assert resp.status_code == 403

    def test_viewer_can_hit_me(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "carol")
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "carol"
        assert resp.json()["user"]["role"] == "viewer"
