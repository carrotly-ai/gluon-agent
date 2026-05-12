"""Tests for Discord channel-per-repo routing (Phase 1 + Phase 2).

Phase 1: approvals + completion notifications route to the channel that
originated the run, not a globally-dedicated approval channel.

Phase 2: AskUserQuestion calls escalate to the originating Discord channel
via the QuestionWatcher; user selections persist back to PendingQuestion.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.models import (
    ApprovalPolicy,
    PendingQuestion,
    QuestionStatus,
    utc_now,
)
from gluon.store import GluonStore
from gluon.transport.discord import DISCORD_AVAILABLE

pytestmark = pytest.mark.skipif(
    not DISCORD_AVAILABLE,
    reason="discord.py not installed (optional extra: pip install gluon-agent[discord])",
)


# ===== Helpers =====


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "channel_routing.db")


def _make_project(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    return store.create_project("proj", proj_path)


def _make_run(store: GluonStore, project_id: str):
    return store.create_run(
        project_id=project_id,
        prompt="test",
        approval_policy=ApprovalPolicy.CAREFUL,
    )


def _make_approval(store: GluonStore, run_id: str):
    return store.create_approval(
        run_id=run_id,
        tool_name="Bash",
        classification_reason="CAREFUL: matched rm -rf",
        tool_input={"command": "rm -rf /tmp/test"},
    )


def _make_question(store: GluonStore, run_id: str, *, header="Pick", multi_select=False) -> PendingQuestion:
    q = PendingQuestion(
        run_id=run_id,
        question_index=0,
        question_text="Which option?",
        header=header,
        options=[
            {"label": "Option A (Recommended)", "description": "default"},
            {"label": "Option B", "description": "alt"},
        ],
        multi_select=multi_select,
        expires_at=utc_now() + timedelta(minutes=5),
    )
    return store.create_pending_question(q)


def _make_transport_stub(*, approval_channel_id=None):
    from gluon.transport.discord import DiscordTransport

    bot_core = MagicMock()
    bot_core.store = MagicMock()

    return DiscordTransport(
        token="test-token",
        guild_id=1234567,
        bot_core=bot_core,
        allowed_users=[42],
        approval_channel_id=approval_channel_id,
    )


# ===== Store layer =====


def test_find_message_run_map_by_run_returns_most_recent(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)

    # Two mappings for the same run; the second should win.
    store.create_message_run_map("discord", "msg-1", run.id, "chan-A", "discord:42")
    store.create_message_run_map("discord", "msg-2", run.id, "chan-B", "discord:42")

    found = store.find_message_run_map_by_run(run.id)
    assert found is not None
    assert found.message_id == "msg-2"
    assert found.chat_id == "chan-B"


def test_find_message_run_map_by_run_filters_transport(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)

    store.create_message_run_map("telegram", "tg-1", run.id, "tg-chat", "telegram:99")

    # Asking for discord on a telegram-only run yields nothing
    assert store.find_message_run_map_by_run(run.id, transport="discord") is None
    # Asking for telegram returns the mapping
    assert store.find_message_run_map_by_run(run.id, transport="telegram") is not None


def test_find_message_run_map_by_run_none_for_unknown_run(tmp_path):
    store = _make_store(tmp_path)
    assert store.find_message_run_map_by_run("does-not-exist") is None


def test_pending_question_notified_at_lifecycle(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    # Fresh question — un-notified
    undelivered = store.list_pending_undelivered_questions()
    assert any(p.id == q.id for p in undelivered)

    # Mark notified
    assert store.mark_question_notified(q.id) is True

    # Second mark loses the race (returns False)
    assert store.mark_question_notified(q.id) is False

    # No longer in the un-notified list
    undelivered = store.list_pending_undelivered_questions()
    assert not any(p.id == q.id for p in undelivered)


def test_list_pending_undelivered_questions_excludes_answered(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    q.answer(["Option A (Recommended)"])
    store.update_pending_question(q)

    undelivered = store.list_pending_undelivered_questions()
    assert not any(p.id == q.id for p in undelivered)


# ===== Phase 1: approval routes to origin channel =====


@pytest.mark.anyio
async def test_post_approval_prefers_origin_channel_over_dedicated(tmp_path):
    """Run originated from channel 555 should get approval posted into 555,
    not the dedicated approval_channel_id."""
    import discord

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    approval = _make_approval(store, run.id)

    # Mark the run as originated from Discord channel 555 / message 7777
    store.create_message_run_map(
        transport="discord",
        message_id="7777",
        run_id=run.id,
        chat_id="555",
        user_id="discord:42",
    )

    transport = _make_transport_stub(approval_channel_id=999)
    transport.bot_core.store = store

    origin_channel = MagicMock(spec=discord.DMChannel)
    origin_channel.id = 555
    origin_channel.send = AsyncMock()
    fallback_channel = MagicMock(spec=discord.DMChannel)
    fallback_channel.id = 999
    fallback_channel.send = AsyncMock()

    transport.bot = MagicMock()

    def _get_channel(cid):
        return {555: origin_channel, 999: fallback_channel}.get(cid)

    transport.bot.get_channel = MagicMock(side_effect=_get_channel)
    transport.bot.fetch_channel = AsyncMock(side_effect=lambda cid: _get_channel(cid))
    transport.bot.add_view = MagicMock()

    ok = await transport.post_approval_request(approval)
    assert ok is True

    # Origin channel got the approval; fallback did not
    origin_channel.send.assert_awaited_once()
    fallback_channel.send.assert_not_called()

    # Sent with a MessageReference to the status message for threading
    kwargs = origin_channel.send.call_args.kwargs
    assert kwargs.get("reference") is not None
    assert kwargs["reference"].message_id == 7777


@pytest.mark.anyio
async def test_post_approval_falls_back_to_approval_channel_when_no_origin(tmp_path):
    """Run with no Discord origin (e.g., CLI-submitted) uses approval_channel_id."""
    import discord

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    approval = _make_approval(store, run.id)
    # No message_run_map for this run

    transport = _make_transport_stub(approval_channel_id=999)
    transport.bot_core.store = store

    fallback_channel = MagicMock(spec=discord.DMChannel)
    fallback_channel.id = 999
    fallback_channel.send = AsyncMock()

    transport.bot = MagicMock()
    transport.bot.get_channel = MagicMock(return_value=fallback_channel)
    transport.bot.fetch_channel = AsyncMock(return_value=fallback_channel)
    transport.bot.add_view = MagicMock()

    ok = await transport.post_approval_request(approval)
    assert ok is True
    fallback_channel.send.assert_awaited_once()

    # No reply threading since there's no origin message
    kwargs = fallback_channel.send.call_args.kwargs
    assert kwargs.get("reference") is None


@pytest.mark.anyio
async def test_post_approval_no_origin_no_fallback_is_noop(tmp_path):
    """Neither origin nor approval channel → no-op (return True so watcher gives up)."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    approval = _make_approval(store, run.id)

    transport = _make_transport_stub(approval_channel_id=None)
    transport.bot_core.store = store

    transport.bot = MagicMock()
    transport.bot.get_channel = MagicMock(return_value=None)

    ok = await transport.post_approval_request(approval)
    assert ok is True


# ===== Phase 2: question embed/view formatting =====


def test_format_question_embed_contains_options(tmp_path):
    from gluon.transport.discord import format_question_embed

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id, header="Pick DB")

    embed = format_question_embed(q)

    assert "Pick DB" in embed.title
    assert "Which option?" in embed.description

    field_names = [f.name for f in embed.fields]
    assert "Run" in field_names
    assert "Options" in field_names

    options_field = next(f for f in embed.fields if f.name == "Options")
    assert "Option A" in options_field.value
    assert "Option B" in options_field.value


def test_format_question_embed_shows_multiselect_mode(tmp_path):
    from gluon.transport.discord import format_question_embed

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id, multi_select=True)

    embed = format_question_embed(q)
    field_names = [f.name for f in embed.fields]
    assert "Mode" in field_names
    mode_field = next(f for f in embed.fields if f.name == "Mode")
    assert "multi-select" in mode_field.value


@pytest.mark.anyio
async def test_build_question_view_has_select_with_options(tmp_path):
    from gluon.transport.discord import _build_question_view

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    view = _build_question_view(q, store, lambda uid: True)
    assert view.timeout is None  # Persistent

    selects = [c for c in view.children if hasattr(c, "options")]
    assert len(selects) == 1
    select = selects[0]
    assert select.custom_id == f"question:{q.id}"
    assert len(select.options) == 2
    assert select.options[0].label.startswith("Option A")
    assert select.options[1].label == "Option B"


@pytest.mark.anyio
async def test_build_question_view_multiselect_max_values(tmp_path):
    from gluon.transport.discord import _build_question_view

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id, multi_select=True)

    view = _build_question_view(q, store, lambda uid: True)
    select = next(c for c in view.children if hasattr(c, "options"))
    # 2 options + multi_select → max_values should be 2 (all selectable)
    assert select.max_values == 2


# ===== Phase 2: question decision callback =====


def _make_interaction(user_id: int, selected_values: list[str]):
    interaction = MagicMock()
    interaction.user = MagicMock(id=user_id, display_name=f"user-{user_id}")
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    return interaction


@pytest.mark.anyio
async def test_question_decision_records_answer(tmp_path):
    from gluon.transport.discord import _handle_question_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    interaction = _make_interaction(user_id=42, selected_values=["0"])

    await _handle_question_decision(
        interaction=interaction,
        store=store,
        question_id=q.id,
        selected_values=["0"],
        option_labels=["Option A (Recommended)", "Option B"],
        is_authorized=lambda uid: uid == "discord:42",
    )

    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.status == QuestionStatus.ANSWERED
    assert fresh.selected_labels == ["Option A (Recommended)"]
    assert fresh.answer_source == "user"

    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "Option A" in msg


@pytest.mark.anyio
async def test_question_decision_rejects_unauthorized(tmp_path):
    from gluon.transport.discord import _handle_question_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    interaction = _make_interaction(user_id=999, selected_values=["0"])

    await _handle_question_decision(
        interaction=interaction,
        store=store,
        question_id=q.id,
        selected_values=["0"],
        option_labels=["Option A (Recommended)", "Option B"],
        is_authorized=lambda uid: uid == "discord:42",
    )

    fresh = store.get_pending_question(q.id)
    assert fresh.status == QuestionStatus.PENDING


@pytest.mark.anyio
async def test_question_decision_multi_select(tmp_path):
    from gluon.transport.discord import _handle_question_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id, multi_select=True)

    interaction = _make_interaction(user_id=42, selected_values=["0", "1"])

    await _handle_question_decision(
        interaction=interaction,
        store=store,
        question_id=q.id,
        selected_values=["0", "1"],
        option_labels=["Option A (Recommended)", "Option B"],
        is_authorized=lambda uid: True,
    )

    fresh = store.get_pending_question(q.id)
    assert fresh.status == QuestionStatus.ANSWERED
    assert sorted(fresh.selected_labels) == sorted(["Option A (Recommended)", "Option B"])


@pytest.mark.anyio
async def test_question_decision_ignores_already_answered(tmp_path):
    from gluon.transport.discord import _handle_question_decision

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)
    q.answer(["Option B"])
    store.update_pending_question(q)

    interaction = _make_interaction(user_id=42, selected_values=["0"])

    await _handle_question_decision(
        interaction=interaction,
        store=store,
        question_id=q.id,
        selected_values=["0"],
        option_labels=["Option A (Recommended)", "Option B"],
        is_authorized=lambda uid: True,
    )

    fresh = store.get_pending_question(q.id)
    # Answer should not be overwritten
    assert fresh.selected_labels == ["Option B"]


# ===== Phase 2: post_question_request routing =====


@pytest.mark.anyio
async def test_post_question_routes_to_origin_channel(tmp_path):
    import discord

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    # Run originated from channel 555 / message 7777
    store.create_message_run_map(
        transport="discord",
        message_id="7777",
        run_id=run.id,
        chat_id="555",
        user_id="discord:42",
    )

    transport = _make_transport_stub()
    transport.bot_core.store = store

    origin_channel = MagicMock(spec=discord.DMChannel)
    origin_channel.id = 555
    origin_channel.send = AsyncMock()

    transport.bot = MagicMock()
    transport.bot.get_channel = MagicMock(return_value=origin_channel)
    transport.bot.fetch_channel = AsyncMock(return_value=origin_channel)
    transport.bot.add_view = MagicMock()

    ok = await transport.post_question_request(q)
    assert ok is True
    origin_channel.send.assert_awaited_once()

    kwargs = origin_channel.send.call_args.kwargs
    assert "embed" in kwargs and "view" in kwargs
    # Threaded under the originating status message
    assert kwargs.get("reference") is not None
    assert kwargs["reference"].message_id == 7777


@pytest.mark.anyio
async def test_post_question_noop_when_no_origin(tmp_path):
    """Questions for non-Discord runs are skipped — other transports handle them."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)
    # No message_run_map → not a Discord-originated run

    transport = _make_transport_stub()
    transport.bot_core.store = store
    transport.bot = MagicMock()
    transport.bot.get_channel = MagicMock(return_value=None)

    ok = await transport.post_question_request(q)
    # Return True so the watcher stops trying (some other transport owns it)
    assert ok is True


# ===== QuestionWatcher =====


@pytest.mark.anyio
async def test_question_watcher_marks_notified_after_post(tmp_path):
    from gluon.question_watcher import QuestionWatcher

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    poster = MagicMock()
    poster.post_question_request = AsyncMock(return_value=True)

    watcher = QuestionWatcher(store=store, poster=poster, name="test")
    posted = await watcher.tick()
    assert posted == 1

    poster.post_question_request.assert_awaited_once()
    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.notified_at is not None


@pytest.mark.anyio
async def test_question_watcher_skips_when_poster_returns_false(tmp_path):
    """Failed delivery leaves the question in the queue for retry."""
    from gluon.question_watcher import QuestionWatcher

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    q = _make_question(store, run.id)

    poster = MagicMock()
    poster.post_question_request = AsyncMock(return_value=False)

    watcher = QuestionWatcher(store=store, poster=poster, name="test")
    posted = await watcher.tick()
    assert posted == 0

    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.notified_at is None  # Still un-notified


@pytest.mark.anyio
async def test_question_watcher_handles_poster_exception(tmp_path):
    """Exceptions from the poster don't bring the watcher down."""
    from gluon.question_watcher import QuestionWatcher

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = _make_run(store, project.id)
    _make_question(store, run.id)

    poster = MagicMock()
    poster.post_question_request = AsyncMock(side_effect=RuntimeError("boom"))

    watcher = QuestionWatcher(store=store, poster=poster, name="test")
    posted = await watcher.tick()
    assert posted == 0  # Boom = retry next tick
