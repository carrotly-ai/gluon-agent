"""Tests for D5 Phase 4 self-serve `/link` flow.

Three layers covered:
- ``GluonStore`` create/consume/unlink CRUD with all error paths.
- ``POST /api/auth/link-codes`` + ``GET /api/auth/links`` +
  ``DELETE /api/auth/links/{transport}`` happy and unhappy paths.
- End-to-end: a user generates a code via the API, then a chat handler
  consumes it via the store and the user is bound.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from gluon.auth import LinkCodeError, LocalAuthProvider
from gluon.bot_core import GluonBotCore
from gluon.models import UserRole, utc_now
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")


@pytest.fixture
def seeded_users(temp_store: GluonStore):
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
    return {"admin": admin, "operator": operator}


def login(client, username: str, password: str = "correcthorsebatterystaple"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------------------
# Store: create_link_code / consume_link_code
# ---------------------------------------------------------------------------


class TestCreateLinkCode:
    def test_creates_code_with_default_ttl(self, temp_store: GluonStore, seeded_users):
        alice = seeded_users["admin"]
        link = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        assert link.user_id == alice.id
        assert link.transport == "telegram"
        assert link.consumed_at is None
        assert link.expires_at > link.created_at
        # Default 10-min TTL
        assert (link.expires_at - link.created_at) >= timedelta(minutes=9, seconds=30)
        assert (link.expires_at - link.created_at) <= timedelta(minutes=10, seconds=30)
        # Code is uppercase, 10 chars, no ambiguous characters
        assert len(link.code) == 10
        assert link.code == link.code.upper()
        assert "0" not in link.code
        assert "1" not in link.code
        assert "I" not in link.code
        assert "O" not in link.code

    def test_regenerating_code_invalidates_prior(self, temp_store: GluonStore, seeded_users):
        """A second create_link_code for the same user+transport should
        delete the unconsumed first one — the dashboard should never
        show two valid codes at once."""
        alice = seeded_users["admin"]
        first = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        second = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        assert first.code != second.code
        # First is gone; second is the only one.
        assert temp_store.get_link_code(first.code) is None
        assert temp_store.get_link_code(second.code) is not None

    def test_codes_are_isolated_per_transport(self, temp_store: GluonStore, seeded_users):
        """Generating a Telegram code shouldn't affect a Discord code for
        the same user."""
        alice = seeded_users["admin"]
        tg = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        dc = temp_store.create_link_code(user_id=alice.id, transport="discord")
        assert temp_store.get_link_code(tg.code) is not None
        assert temp_store.get_link_code(dc.code) is not None


class TestConsumeLinkCode:
    def test_happy_path_telegram_binds_user(self, temp_store: GluonStore, seeded_users):
        alice = seeded_users["admin"]
        link = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        bound = temp_store.consume_link_code(code=link.code, transport="telegram", chat_id=12345)
        assert bound.id == alice.id
        assert bound.telegram_user_id == 12345
        # Code is now consumed.
        consumed = temp_store.get_link_code(link.code)
        assert consumed is not None
        assert consumed.consumed_at is not None

    def test_happy_path_discord_binds_user(self, temp_store: GluonStore, seeded_users):
        bob = seeded_users["operator"]
        link = temp_store.create_link_code(user_id=bob.id, transport="discord")
        bound = temp_store.consume_link_code(code=link.code, transport="discord", chat_id=98765)
        assert bound.id == bob.id
        assert bound.discord_user_id == 98765

    def test_unknown_code(self, temp_store: GluonStore):
        with pytest.raises(LinkCodeError) as exc:
            temp_store.consume_link_code(code="FAKE000000", transport="telegram", chat_id=1)
        assert exc.value.reason == "unknown"

    def test_consumed_code_cannot_be_replayed(self, temp_store: GluonStore, seeded_users):
        alice = seeded_users["admin"]
        link = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        temp_store.consume_link_code(code=link.code, transport="telegram", chat_id=12345)
        with pytest.raises(LinkCodeError) as exc:
            temp_store.consume_link_code(code=link.code, transport="telegram", chat_id=12345)
        assert exc.value.reason == "consumed"

    def test_expired_code_rejected(self, temp_store: GluonStore, seeded_users):
        """Manually backdate the expiry to simulate a stale code."""
        alice = seeded_users["admin"]
        link = temp_store.create_link_code(user_id=alice.id, transport="telegram", ttl_minutes=10)
        # Force the row to look expired.
        with temp_store._get_conn() as conn:
            conn.execute(
                "UPDATE link_codes SET expires_at = ? WHERE code = ?",
                ((utc_now() - timedelta(minutes=1)).isoformat(), link.code),
            )
        with pytest.raises(LinkCodeError) as exc:
            temp_store.consume_link_code(code=link.code, transport="telegram", chat_id=12345)
        assert exc.value.reason == "expired"

    def test_transport_mismatch_rejected(self, temp_store: GluonStore, seeded_users):
        alice = seeded_users["admin"]
        link = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        # User generated a Telegram code but tries to redeem it from Discord.
        with pytest.raises(LinkCodeError) as exc:
            temp_store.consume_link_code(code=link.code, transport="discord", chat_id=12345)
        assert exc.value.reason == "transport_mismatch"

    def test_chat_taken_by_other_user(self, temp_store: GluonStore, seeded_users):
        """If chat ID 12345 is already bound to bob, alice can't take it."""
        bob = seeded_users["operator"]
        bob.telegram_user_id = 12345
        temp_store.update_user(bob)

        alice = seeded_users["admin"]
        link = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        with pytest.raises(LinkCodeError) as exc:
            temp_store.consume_link_code(code=link.code, transport="telegram", chat_id=12345)
        assert exc.value.reason == "chat_taken"

    def test_lowercase_code_normalized(self, temp_store: GluonStore, seeded_users):
        """Phones often auto-lowercase or whitespace-pad — be forgiving."""
        alice = seeded_users["admin"]
        link = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        bound = temp_store.consume_link_code(
            code=link.code.lower() + "  ",  # lowercased + whitespace padding
            transport="telegram",
            chat_id=12345,
        )
        assert bound.id == alice.id


class TestUnlinkChat:
    def test_unlink_telegram_clears_field(self, temp_store: GluonStore, seeded_users):
        alice = seeded_users["admin"]
        alice.telegram_user_id = 999
        temp_store.update_user(alice)

        result = temp_store.unlink_chat(user_id=alice.id, transport="telegram")
        assert result is not None
        assert result.telegram_user_id is None

    def test_unlink_unknown_user_returns_none(self, temp_store: GluonStore):
        assert temp_store.unlink_chat(user_id="does-not-exist", transport="telegram") is None


class TestExpiredLinkCodeSweep:
    def test_delete_expired_link_codes_keeps_consumed(self, temp_store: GluonStore, seeded_users):
        """Three codes — one expired-unconsumed, one consumed, one fresh.
        Sweep should remove only the expired-unconsumed one. We use different
        users for the expired and fresh telegram codes so ``create_link_code``
        doesn't tear down the first when adding the second (it auto-clears
        prior unconsumed codes for the same user+transport)."""
        alice = seeded_users["admin"]
        bob = seeded_users["operator"]

        expired = temp_store.create_link_code(user_id=alice.id, transport="telegram")
        consumed = temp_store.create_link_code(user_id=alice.id, transport="discord")
        fresh = temp_store.create_link_code(user_id=bob.id, transport="telegram")

        # Backdate the first; consume the second.
        with temp_store._get_conn() as conn:
            conn.execute(
                "UPDATE link_codes SET expires_at = ? WHERE code = ?",
                ((utc_now() - timedelta(minutes=5)).isoformat(), expired.code),
            )
        temp_store.consume_link_code(code=consumed.code, transport="discord", chat_id=42)

        deleted = temp_store.delete_expired_link_codes()
        assert deleted == 1
        # The expired one is gone, the consumed one stays as audit, fresh stays.
        assert temp_store.get_link_code(expired.code) is None
        assert temp_store.get_link_code(consumed.code) is not None
        assert temp_store.get_link_code(fresh.code) is not None


# ---------------------------------------------------------------------------
# API endpoints: /api/auth/link-codes + /api/auth/links
# ---------------------------------------------------------------------------


class TestLinkCodeEndpoints:
    def test_create_link_code_requires_session(self, api_client, auth_enabled):
        client, _ = api_client
        resp = client.post("/api/auth/link-codes", json={"transport": "telegram"})
        assert resp.status_code == 401

    def test_create_link_code_returns_code(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")

        resp = client.post("/api/auth/link-codes", json={"transport": "telegram"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["transport"] == "telegram"
        assert len(body["code"]) == 10
        assert "expires_at" in body

    def test_create_link_code_rejects_unknown_transport(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.post("/api/auth/link-codes", json={"transport": "slack"})
        assert resp.status_code == 400

    def test_create_link_code_refused_in_single_user_mode(self, api_client, monkeypatch):
        """When auth is disabled there's no real user to link — refuse."""
        monkeypatch.delenv("GLUON_AUTH_ENABLED", raising=False)
        client, _ = api_client
        resp = client.post("/api/auth/link-codes", json={"transport": "telegram"})
        # current_user_dep yields SYSTEM_USER; endpoint refuses with 400.
        assert resp.status_code == 400
        assert "real user" in resp.json()["detail"]

    def test_get_my_links_shows_current_bindings(self, api_client, seeded_users, auth_enabled, temp_store: GluonStore):
        client, _ = api_client
        login(client, "alice")

        # Pre-bind alice's discord
        alice = seeded_users["admin"]
        alice.discord_user_id = 7777
        temp_store.update_user(alice)

        resp = client.get("/api/auth/links")
        assert resp.status_code == 200
        assert resp.json() == {"telegram_user_id": None, "discord_user_id": 7777}

    def test_unlink_my_chat_clears_binding(self, api_client, seeded_users, auth_enabled, temp_store: GluonStore):
        client, _ = api_client
        login(client, "alice")

        alice = seeded_users["admin"]
        alice.telegram_user_id = 9999
        temp_store.update_user(alice)

        resp = client.delete("/api/auth/links/telegram")
        assert resp.status_code == 200
        assert resp.json() == {"telegram_user_id": None, "discord_user_id": None}

    def test_unlink_unknown_transport_400(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")
        resp = client.delete("/api/auth/links/slack")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# End-to-end: web user generates code, bot consumes it
# ---------------------------------------------------------------------------


class TestEndToEndLinkFlow:
    """Simulate the real user journey:
    1. Logged-in user calls POST /api/auth/link-codes from the dashboard
    2. They send `/link <code>` to the bot
    3. The bot's transport handler calls store.consume_link_code(...)
    4. They're now bound — subsequent bot interactions resolve to their User
    """

    def test_full_flow_telegram(self, api_client, seeded_users, auth_enabled, temp_store: GluonStore):
        client, _ = api_client
        login(client, "bob")  # operator

        # Step 1 — dashboard generates a code
        resp = client.post("/api/auth/link-codes", json={"transport": "telegram"})
        assert resp.status_code == 200
        code = resp.json()["code"]

        # Step 2/3 — bot consumes it (simulating what telegram.py does)
        bound = temp_store.consume_link_code(code=code, transport="telegram", chat_id=8888888)
        assert bound.id == seeded_users["operator"].id

        # Step 4 — subsequent lookups resolve correctly
        bot = MagicMock(spec=GluonBotCore)
        bot.store = temp_store
        # The real bot_core.resolve_user_id_by_chat_id uses store.get_user_by_*
        # so we just exercise the store directly here.
        resolved = temp_store.get_user_by_telegram_id(8888888)
        assert resolved is not None
        assert resolved.id == seeded_users["operator"].id

        # And the dashboard now reflects it.
        resp = client.get("/api/auth/links")
        assert resp.json()["telegram_user_id"] == 8888888
