"""Tests for Discord approve/deny buttons (Theme D1 follow-up #2)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.models import ApprovalPolicy, ApprovalStatus
from gluon.store import GluonStore
from gluon.transport.discord import DISCORD_AVAILABLE

# Skip the whole module if discord.py isn't installed
pytestmark = pytest.mark.skipif(
    not DISCORD_AVAILABLE,
    reason="discord.py not installed (optional extra: pip install gluon-agent[discord])",
)


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "discord_approvals.db")


def _make_project(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    return store.create_project("proj", proj_path)


def _make_approval(store: GluonStore, project_id: str, **kwargs):
    run = store.create_run(
        project_id=project_id,
        prompt="test",
        approval_policy=ApprovalPolicy.CAREFUL,
    )
    return store.create_approval(
        run_id=run.id,
        tool_name=kwargs.get("tool_name", "Bash"),
        classification_reason=kwargs.get("reason", "CAREFUL: matched rm -rf"),
        tool_input=kwargs.get("tool_input", {"command": "rm -rf /tmp/test"}),
    )


def _make_transport_stub(allowed_users=None, approval_channel_id=None):
    """Build a DiscordTransport without actually connecting to Discord."""
    from gluon.transport.discord import DiscordTransport

    bot_core = MagicMock()
    bot_core.store = MagicMock()

    transport = DiscordTransport(
        token="test-token",
        guild_id=1234567,
        bot_core=bot_core,
        allowed_users=allowed_users or [12345],
        approval_channel_id=approval_channel_id,
    )
    return transport


# ========== Embed formatter ==========


def test_format_approval_embed_contains_core_fields(tmp_path):
    from gluon.transport.discord import format_approval_embed

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(
        store,
        project.id,
        tool_name="Bash",
        reason="CAREFUL: rm -rf",
        tool_input={"command": "rm -rf /tmp/important"},
    )

    embed = format_approval_embed(approval)

    assert "Approval needed" in embed.title
    assert "rm -rf" in embed.description  # classification reason
    # Fields
    field_names = [f.name for f in embed.fields]
    assert "Run" in field_names
    assert "Tool" in field_names
    assert "Approval" in field_names
    assert "Command" in field_names

    # Command field should contain the rm command in a code block
    command_field = next(f for f in embed.fields if f.name == "Command")
    assert "/tmp/important" in command_field.value


def test_format_approval_embed_without_command(tmp_path):
    """Write/Edit approvals should render file_path summary instead of Command."""
    from gluon.transport.discord import format_approval_embed

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(
        store,
        project.id,
        tool_name="Write",
        reason="PARANOID: all writes",
        tool_input={"file_path": "/src/config.py", "content": "new content"},
    )

    embed = format_approval_embed(approval)

    field_names = [f.name for f in embed.fields]
    # Should have Input field (not Command) for writes
    assert "Command" not in field_names
    assert "Input" in field_names
    input_field = next(f for f in embed.fields if f.name == "Input")
    assert "config.py" in input_field.value


def test_format_approval_embed_truncates_long_command(tmp_path):
    from gluon.transport.discord import format_approval_embed

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    huge_cmd = "echo " + ("x" * 5000)
    approval = _make_approval(store, project.id, tool_input={"command": huge_cmd})

    embed = format_approval_embed(approval)
    command_field = next(f for f in embed.fields if f.name == "Command")
    # Must stay under Discord's 1024-char field limit
    assert len(command_field.value) <= 1024


def test_format_approval_embed_has_orange_pending_color(tmp_path):
    from gluon.transport.discord import format_approval_embed

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    embed = format_approval_embed(approval)
    # Orange — pending (color encoded as int)
    assert embed.color.value == 0xFFA500


# ========== ApprovalView ==========


def test_build_approval_view_has_two_persistent_buttons(tmp_path):
    from gluon.transport.discord import _build_approval_view

    store = _make_store(tmp_path)
    view = _build_approval_view("abc-123", store, lambda uid: True)

    # Persistent view requires timeout=None
    assert view.timeout is None

    # Two children, Approve + Deny
    buttons = list(view.children)
    assert len(buttons) == 2

    labels = [b.label for b in buttons]
    assert any("Approve" in s for s in labels)
    assert any("Deny" in s for s in labels)

    # custom_id encodes decision + id for persistence across restart
    custom_ids = [b.custom_id for b in buttons]
    assert "approval:grant:abc-123" in custom_ids
    assert "approval:deny:abc-123" in custom_ids


# ========== post_approval_request ==========


@pytest.mark.anyio
async def test_post_approval_returns_true_without_channel(tmp_path):
    """No-op when approval_channel_id is unset — avoids watcher retry spam."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(approval_channel_id=None)

    ok = await transport.post_approval_request(approval)
    assert ok is True  # no-op is a "success" so watcher stops trying


@pytest.mark.anyio
async def test_post_approval_sends_embed_and_view(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(approval_channel_id=999)
    transport.bot_core.store = store

    # Mock out bot.get_channel + channel.send
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    transport.bot = MagicMock()
    transport.bot.get_channel = MagicMock(return_value=mock_channel)
    transport.bot.fetch_channel = AsyncMock(return_value=mock_channel)
    transport.bot.add_view = MagicMock()

    # Make isinstance(channel, discord.abc.Messageable) pass by using a
    # real Messageable subclass. In tests this is hard to mock perfectly,
    # so we monkey-patch isinstance via side_effect — simpler to just let
    # the mock pass through discord.abc.Messageable check using a spec.
    import discord

    mock_channel.__class__ = discord.DMChannel

    ok = await transport.post_approval_request(approval)

    assert ok is True
    mock_channel.send.assert_awaited_once()
    # Check kwargs — should have embed + view
    call_kwargs = mock_channel.send.call_args.kwargs
    assert "embed" in call_kwargs
    assert "view" in call_kwargs
    # View should be registered as persistent
    transport.bot.add_view.assert_called_once()


@pytest.mark.anyio
async def test_post_approval_returns_false_on_missing_channel(tmp_path):
    """When the channel fetch fails, return False so the watcher retries."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(approval_channel_id=999)
    transport.bot_core.store = store

    transport.bot = MagicMock()
    transport.bot.get_channel = MagicMock(return_value=None)
    transport.bot.fetch_channel = AsyncMock(side_effect=Exception("channel not found"))

    ok = await transport.post_approval_request(approval)
    assert ok is False


@pytest.mark.anyio
async def test_post_approval_returns_false_when_channel_not_messageable(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(approval_channel_id=999)
    transport.bot_core.store = store

    # Return an object that isn't a Messageable
    not_messageable = object()  # bare object → not a Messageable
    transport.bot = MagicMock()
    transport.bot.get_channel = MagicMock(return_value=not_messageable)

    ok = await transport.post_approval_request(approval)
    assert ok is False


# ========== Button interaction handler ==========


def _make_interaction(user_id: int, custom_id: str):
    """Build a mock discord.Interaction for a button press."""
    interaction = MagicMock()
    interaction.user = MagicMock(id=user_id, display_name=f"user-{user_id}")
    interaction.data = {"custom_id": custom_id}
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    return interaction


@pytest.mark.anyio
async def test_decision_callback_rejects_unauthorized(tmp_path):
    from gluon.transport.discord import _handle_approval_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    interaction = _make_interaction(user_id=999, custom_id="approval:grant:abc")

    # is_authorized returns False for user 999
    def is_auth(uid: str) -> bool:
        return uid == "discord:42"

    await _handle_approval_decision(
        interaction=interaction,
        store=store,
        approval_id=approval.id,
        status=ApprovalStatus.GRANTED,
        is_authorized=is_auth,
    )

    interaction.response.send_message.assert_awaited_once()
    call_args = interaction.response.send_message.await_args
    assert "Not authorized" in call_args.args[0]
    assert call_args.kwargs.get("ephemeral") is True

    # State should not have changed
    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_decision_callback_grants_for_authorized(tmp_path):
    from gluon.transport.discord import _handle_approval_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    interaction = _make_interaction(user_id=42, custom_id=f"approval:grant:{approval.id}")

    await _handle_approval_decision(
        interaction=interaction,
        store=store,
        approval_id=approval.id,
        status=ApprovalStatus.GRANTED,
        is_authorized=lambda uid: uid == "discord:42",
    )

    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.GRANTED
    assert fresh.decided_by == "discord:42"

    interaction.response.send_message.assert_awaited_once_with("Approved.", ephemeral=True)
    # Message should have been edited to remove buttons
    interaction.message.edit.assert_awaited_once()


@pytest.mark.anyio
async def test_decision_callback_denies_for_authorized(tmp_path):
    from gluon.transport.discord import _handle_approval_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    interaction = _make_interaction(user_id=42, custom_id=f"approval:deny:{approval.id}")

    await _handle_approval_decision(
        interaction=interaction,
        store=store,
        approval_id=approval.id,
        status=ApprovalStatus.DENIED,
        is_authorized=lambda uid: True,
    )

    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.DENIED

    interaction.response.send_message.assert_awaited_once_with("Denied.", ephemeral=True)


@pytest.mark.anyio
async def test_decision_callback_already_decided_is_idempotent(tmp_path):
    """Tapping Approve/Deny on an already-decided approval does not overwrite."""
    from gluon.transport.discord import _handle_approval_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    # Pre-decide via CLI
    store.decide_approval(approval.id, status=ApprovalStatus.GRANTED, decided_by="cli")

    interaction = _make_interaction(user_id=42, custom_id=f"approval:deny:{approval.id}")

    # User taps Deny after it's already been granted
    await _handle_approval_decision(
        interaction=interaction,
        store=store,
        approval_id=approval.id,
        status=ApprovalStatus.DENIED,
        is_authorized=lambda uid: True,
    )

    # Status must stay GRANTED — no overwrite
    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.GRANTED

    # Should tell the user it's already decided
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "granted" in msg.lower() or "already" in msg.lower()


@pytest.mark.anyio
async def test_decision_callback_handles_missing_approval(tmp_path):
    from gluon.transport.discord import _handle_approval_decision

    store = _make_store(tmp_path)
    interaction = _make_interaction(user_id=42, custom_id="approval:grant:missing")

    await _handle_approval_decision(
        interaction=interaction,
        store=store,
        approval_id="missing-id",
        status=ApprovalStatus.GRANTED,
        is_authorized=lambda uid: True,
    )

    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "not found" in msg.lower()


# ========== Transport configuration ==========


def test_transport_accepts_approval_channel_id():
    t = _make_transport_stub(approval_channel_id=123456)
    assert t.approval_channel_id == 123456


def test_transport_defaults_approval_channel_to_none():
    t = _make_transport_stub(approval_channel_id=None)
    assert t.approval_channel_id is None


def test_transport_holds_watcher_slot():
    """Watcher is initialized to None; started in on_ready."""
    t = _make_transport_stub()
    assert t._approval_watcher is None
