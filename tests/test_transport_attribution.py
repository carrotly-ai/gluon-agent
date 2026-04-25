"""Tests for D5 Phase 4 transport linking — bot resolves chat ID → User.

Two layers covered:
- ``GluonBotCore.resolve_user_id_by_chat_id`` and the ``resolve_user_attribution(ctx)``
  helper translate a chat identity to a Gluon ``User.id`` (or ``None``).
- ``PATCH /api/users/{id}`` accepts ``telegram_user_id`` / ``discord_user_id``
  for admin pre-registration, with conflict detection.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gluon.auth import LocalAuthProvider
from gluon.bot_core import GluonBotCore
from gluon.models import UserRole
from gluon.store import GluonStore
from gluon.transport.base import TransportContext

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_enabled(monkeypatch):
    """Turn on GLUON_AUTH_ENABLED for the duration of a test."""
    monkeypatch.setenv("GLUON_AUTH_ENABLED", "true")


@pytest.fixture
def seeded_users(temp_store: GluonStore):
    """Canonical admin + operator pair for tests."""
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


@pytest.fixture
def bot_core_with_real_store(temp_store: GluonStore):
    """A GluonBotCore wired to the real test store but with mocked
    orchestrator/git so we don't hit Claude or git during these tests."""
    with (
        patch("gluon.bot_core.Orchestrator") as mock_orch_cls,
        patch("gluon.bot_core.GitManager"),
        patch("gluon.bot_core.NotificationDispatcher"),
        patch("gluon.bot_core.GluonChatAgent"),
    ):
        orchestrator = MagicMock()
        mock_orch_cls.return_value = orchestrator
        return GluonBotCore(store=temp_store, orchestrator=orchestrator)


def login(client, username: str, password: str = "correcthorsebatterystaple"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------------------
# bot_core.resolve_user_id_by_chat_id
# ---------------------------------------------------------------------------


class TestResolveUserIdByChatId:
    def test_unlinked_telegram_returns_none(self, bot_core_with_real_store):
        assert bot_core_with_real_store.resolve_user_id_by_chat_id("telegram", 12345) is None

    def test_unlinked_discord_returns_none(self, bot_core_with_real_store):
        assert bot_core_with_real_store.resolve_user_id_by_chat_id("discord", 234567890123456789) is None

    def test_unknown_transport_returns_none(self, bot_core_with_real_store):
        assert bot_core_with_real_store.resolve_user_id_by_chat_id("slack", 12345) is None

    def test_zero_chat_id_returns_none(self, bot_core_with_real_store):
        # Defensive — chat libs sometimes report 0 for missing user info.
        assert bot_core_with_real_store.resolve_user_id_by_chat_id("telegram", 0) is None

    def test_linked_telegram_returns_user_id(self, bot_core_with_real_store, seeded_users):
        alice = seeded_users["admin"]
        alice.telegram_user_id = 999111
        bot_core_with_real_store.store.update_user(alice)

        resolved = bot_core_with_real_store.resolve_user_id_by_chat_id("telegram", 999111)
        assert resolved == alice.id

    def test_linked_discord_returns_user_id(self, bot_core_with_real_store, seeded_users):
        bob = seeded_users["operator"]
        bob.discord_user_id = 987654321
        bot_core_with_real_store.store.update_user(bob)

        resolved = bot_core_with_real_store.resolve_user_id_by_chat_id("discord", 987654321)
        assert resolved == bob.id

    def test_telegram_id_does_not_match_discord_lookup(self, bot_core_with_real_store, seeded_users):
        """A user bound on Telegram only must not be returned for a Discord lookup
        with the same numeric ID. The two IDs live in separate columns."""
        alice = seeded_users["admin"]
        alice.telegram_user_id = 555
        bot_core_with_real_store.store.update_user(alice)

        # Same number, different transport → no match.
        assert bot_core_with_real_store.resolve_user_id_by_chat_id("discord", 555) is None


class TestResolveUserAttributionFromContext:
    """Convenience wrapper that parses ``ctx.user_id`` like ``"telegram:123"``."""

    def test_parses_telegram_context(self, bot_core_with_real_store, seeded_users):
        alice = seeded_users["admin"]
        alice.telegram_user_id = 1234
        bot_core_with_real_store.store.update_user(alice)

        ctx = TransportContext(
            transport="telegram",
            user_id="telegram:1234",
            chat_id="-100",
            message_id=None,
        )
        assert bot_core_with_real_store.resolve_user_attribution(ctx) == alice.id

    def test_parses_discord_context(self, bot_core_with_real_store, seeded_users):
        bob = seeded_users["operator"]
        bob.discord_user_id = 5678
        bot_core_with_real_store.store.update_user(bob)

        ctx = TransportContext(
            transport="discord",
            user_id="discord:5678",
            chat_id="general",
            message_id=None,
        )
        assert bot_core_with_real_store.resolve_user_attribution(ctx) == bob.id

    def test_malformed_user_id_returns_none(self, bot_core_with_real_store):
        ctx = TransportContext(
            transport="telegram",
            user_id="not-a-real-id",
            chat_id="-100",
            message_id=None,
        )
        assert bot_core_with_real_store.resolve_user_attribution(ctx) is None

    def test_non_int_chat_id_returns_none(self, bot_core_with_real_store):
        ctx = TransportContext(
            transport="telegram",
            user_id="telegram:not-a-number",
            chat_id="-100",
            message_id=None,
        )
        assert bot_core_with_real_store.resolve_user_attribution(ctx) is None

    def test_empty_user_id_returns_none(self, bot_core_with_real_store):
        ctx = TransportContext(transport="telegram", user_id="", chat_id="-100", message_id=None)
        assert bot_core_with_real_store.resolve_user_attribution(ctx) is None


# ---------------------------------------------------------------------------
# PATCH /api/users/{id} chat-id binding
# ---------------------------------------------------------------------------


class TestPatchUserChatBinding:
    def test_admin_can_set_telegram_id(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")

        bob = seeded_users["operator"]
        resp = client.patch(f"/api/users/{bob.id}", json={"telegram_user_id": 12345678})
        assert resp.status_code == 200
        assert resp.json()["telegram_user_id"] == 12345678

    def test_admin_can_set_discord_id(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "alice")

        bob = seeded_users["operator"]
        resp = client.patch(f"/api/users/{bob.id}", json={"discord_user_id": 234567890123456789})
        assert resp.status_code == 200
        assert resp.json()["discord_user_id"] == 234567890123456789

    def test_admin_can_clear_telegram_id_with_zero(
        self, api_client, seeded_users, auth_enabled, temp_store: GluonStore
    ):
        """Sending 0 clears the link (because Pydantic can't tell `null` from
        absent in PATCH semantics, we use 0-as-unset)."""
        client, _ = api_client
        login(client, "alice")

        bob = seeded_users["operator"]
        bob.telegram_user_id = 999
        temp_store.update_user(bob)

        resp = client.patch(f"/api/users/{bob.id}", json={"telegram_user_id": 0})
        assert resp.status_code == 200
        assert resp.json()["telegram_user_id"] is None

    def test_conflict_when_id_bound_to_another_user(
        self, api_client, seeded_users, auth_enabled, temp_store: GluonStore
    ):
        """Same telegram_user_id on two users would break unambiguous lookup."""
        client, _ = api_client
        login(client, "alice")

        # Bind 555 to bob first
        bob = seeded_users["operator"]
        bob.telegram_user_id = 555
        temp_store.update_user(bob)

        # Now try to bind the same ID to alice — should 409.
        alice = seeded_users["admin"]
        resp = client.patch(f"/api/users/{alice.id}", json={"telegram_user_id": 555})
        assert resp.status_code == 409
        assert "already bound" in resp.json()["detail"]

    def test_can_re_set_same_id_on_same_user(self, api_client, seeded_users, auth_enabled, temp_store: GluonStore):
        """Updating a user with their existing ID is a no-op, not a conflict."""
        client, _ = api_client
        login(client, "alice")

        bob = seeded_users["operator"]
        bob.telegram_user_id = 777
        temp_store.update_user(bob)

        resp = client.patch(f"/api/users/{bob.id}", json={"telegram_user_id": 777})
        assert resp.status_code == 200
        assert resp.json()["telegram_user_id"] == 777

    def test_non_admin_cannot_set_telegram_id(self, api_client, seeded_users, auth_enabled):
        client, _ = api_client
        login(client, "bob")  # operator

        alice = seeded_users["admin"]
        resp = client.patch(f"/api/users/{alice.id}", json={"telegram_user_id": 1})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Approval attribution end-to-end via store + simulated transport
# ---------------------------------------------------------------------------


class TestApprovalAttributionWiring:
    """When the bot's transport handler resolves a chat user to a Gluon user
    and passes ``decided_by_user_id`` to ``decide_approval``, the row should
    persist with both the transport tag and the Gluon user_id."""

    def test_decide_approval_with_resolved_user_persists_both(self, temp_store: GluonStore, seeded_users):
        from gluon.models import ApprovalStatus

        alice = seeded_users["admin"]
        alice.telegram_user_id = 4242
        temp_store.update_user(alice)

        # Simulate what telegram.py does inline.
        resolved_user_id: str | None = None
        linked = temp_store.get_user_by_telegram_id(4242)
        if linked is not None:
            resolved_user_id = linked.id
        assert resolved_user_id == alice.id

        # Now exercise the full approval flow.
        proj = temp_store.create_project(name="proj", path="/tmp/x", workspace_id=None)
        run = temp_store.create_run(project_id=proj.id, prompt="hi")
        approval = temp_store.create_approval(
            run_id=run.id,
            tool_name="Bash",
            tool_input={"command": "ls"},
            classification_reason="test",
        )
        updated = temp_store.decide_approval(
            approval.id,
            status=ApprovalStatus.GRANTED,
            decided_by="telegram:4242",
            decided_by_user_id=resolved_user_id,
            decision_reason="ok",
        )
        assert updated is not None
        assert updated.decided_by == "telegram:4242"  # transport tag preserved
        assert updated.decided_by_user_id == alice.id  # gluon user attribution
