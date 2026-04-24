"""Tests for Telegram approval buttons + ApprovalWatcher (Theme D1 follow-up)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gluon.approval_watcher import ApprovalWatcher
from gluon.models import ApprovalPolicy, ApprovalStatus
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "tg_approvals.db")


def _make_project(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    return store.create_project("proj", proj_path)


def _make_approval(store: GluonStore, project_id: str, **kwargs):
    run = store.create_run(project_id=project_id, prompt="test", approval_policy=ApprovalPolicy.CAREFUL)
    return store.create_approval(
        run_id=run.id,
        tool_name=kwargs.get("tool_name", "Bash"),
        classification_reason=kwargs.get("reason", "test reason"),
        tool_input=kwargs.get("tool_input", {"command": "rm -rf /tmp/foo"}),
    )


# ========== Delivery tracking ==========


def test_list_pending_undelivered_returns_only_pending_unnotified(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    a1 = _make_approval(store, project.id)  # pending + unnotified
    a2 = _make_approval(store, project.id)  # will notify
    a3 = _make_approval(store, project.id)  # will decide

    # Mark a2 as notified
    store.mark_approval_notified(a2.id)

    # Decide a3
    store.decide_approval(a3.id, status=ApprovalStatus.GRANTED, decided_by="test")

    undelivered = store.list_pending_undelivered_approvals()
    ids = {a.id for a in undelivered}
    assert a1.id in ids
    assert a2.id not in ids  # already notified
    assert a3.id not in ids  # no longer pending


def test_mark_approval_notified_atomic(tmp_path):
    """The first call wins; subsequent calls return False (already marked)."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    assert store.mark_approval_notified(approval.id) is True
    assert store.mark_approval_notified(approval.id) is False  # idempotent

    # notified_at should now be set
    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.notified_at is not None


def test_notified_at_persists_across_fetches(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    assert approval.notified_at is None  # fresh create
    store.mark_approval_notified(approval.id)

    refreshed = store.get_approval(approval.id)
    assert refreshed is not None
    assert refreshed.notified_at is not None


# ========== ApprovalWatcher ==========


class _FakePoster:
    """Test double for ApprovalPoster — records everything it's asked to post."""

    def __init__(self, *, return_ok: bool = True):
        self.posted: list = []
        self.return_ok = return_ok
        self.raise_on: set[str] = set()

    async def post_approval_request(self, approval) -> bool:
        if approval.id in self.raise_on:
            raise RuntimeError("fake transport failure")
        self.posted.append(approval)
        return self.return_ok


@pytest.mark.anyio
async def test_watcher_tick_posts_pending_approvals(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    a1 = _make_approval(store, project.id)
    a2 = _make_approval(store, project.id)

    poster = _FakePoster(return_ok=True)
    watcher = ApprovalWatcher(store, poster)

    posted = await watcher.tick()
    assert posted == 2
    assert {a.id for a in poster.posted} == {a1.id, a2.id}

    # Both should be marked notified now
    for a in (a1, a2):
        fresh = store.get_approval(a.id)
        assert fresh is not None
        assert fresh.notified_at is not None


@pytest.mark.anyio
async def test_watcher_skips_already_notified(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)

    a1 = _make_approval(store, project.id)
    store.mark_approval_notified(a1.id)  # pretend already posted

    a2 = _make_approval(store, project.id)  # fresh, will be posted

    poster = _FakePoster()
    watcher = ApprovalWatcher(store, poster)

    posted = await watcher.tick()
    assert posted == 1
    assert len(poster.posted) == 1
    assert poster.posted[0].id == a2.id


@pytest.mark.anyio
async def test_watcher_does_not_mark_when_poster_returns_false(tmp_path):
    """Transport failure → approval stays un-notified so next tick retries."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    poster = _FakePoster(return_ok=False)  # "transport failed"
    watcher = ApprovalWatcher(store, poster)

    posted = await watcher.tick()
    assert posted == 0

    # Still un-notified — next tick will retry
    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.notified_at is None


@pytest.mark.anyio
async def test_watcher_survives_poster_exception(tmp_path):
    """A poster raising an exception should not crash the watcher."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    a_ok = _make_approval(store, project.id)
    a_bad = _make_approval(store, project.id)

    poster = _FakePoster()
    poster.raise_on.add(a_bad.id)
    watcher = ApprovalWatcher(store, poster)

    # Should not raise — the bad approval is skipped, the good one posts
    posted = await watcher.tick()
    assert posted >= 1
    posted_ids = {a.id for a in poster.posted}
    # a_ok should be posted; a_bad raised and stays unposted
    assert a_ok.id in posted_ids
    fresh_ok = store.get_approval(a_ok.id)
    fresh_bad = store.get_approval(a_bad.id)
    assert fresh_ok is not None and fresh_ok.notified_at is not None
    assert fresh_bad is not None and fresh_bad.notified_at is None


@pytest.mark.anyio
async def test_watcher_skips_non_pending(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    # Decide it before the watcher sees it
    store.decide_approval(approval.id, status=ApprovalStatus.DENIED, decided_by="cli")

    poster = _FakePoster()
    watcher = ApprovalWatcher(store, poster)

    posted = await watcher.tick()
    assert posted == 0
    assert poster.posted == []


@pytest.mark.anyio
async def test_watcher_start_stop_clean(tmp_path):
    import asyncio

    store = _make_store(tmp_path)
    poster = _FakePoster()
    watcher = ApprovalWatcher(store, poster, poll_interval_secs=1)

    await watcher.start()
    assert watcher.is_running

    # Double-start is safe
    await watcher.start()

    await asyncio.sleep(0.05)
    await watcher.stop()
    assert not watcher.is_running


# ========== Telegram message formatter ==========


def test_format_approval_message_includes_core_fields(tmp_path):
    from gluon.transport.telegram import format_approval_message

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(
        store,
        project.id,
        tool_name="Bash",
        reason="CAREFUL: rm -rf",
        tool_input={"command": "rm -rf /tmp/important"},
    )

    body = format_approval_message(approval)
    assert "Approval needed" in body
    assert approval.run_id[:8] in body
    assert "Bash" in body
    assert "rm -rf" in body
    assert "/tmp/important" in body
    assert approval.id[:8] in body


def test_format_approval_message_without_command(tmp_path):
    """For Write/Edit approvals, the formatter should still produce a useful message."""
    from gluon.transport.telegram import format_approval_message

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(
        store,
        project.id,
        tool_name="Write",
        reason="PARANOID: all writes",
        tool_input={"file_path": "/src/config.py", "content": "long content..."},
    )

    body = format_approval_message(approval)
    assert "Write" in body
    assert "config.py" in body
    # file_path should be referenced since we don't have a command
    assert "file_path" in body


def test_format_approval_message_truncates_long_commands(tmp_path):
    from gluon.transport.telegram import format_approval_message

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    huge_cmd = "echo " + ("x" * 1000)
    approval = _make_approval(
        store,
        project.id,
        tool_input={"command": huge_cmd},
    )

    body = format_approval_message(approval)
    # Should fit comfortably under Telegram's 4096-char cap
    assert len(body) < 800


def test_build_approval_keyboard_has_both_buttons():
    from gluon.transport.telegram import build_approval_keyboard

    kb = build_approval_keyboard("abc-123")
    # Telegram InlineKeyboardMarkup stores rows as a tuple of tuples
    rows = kb.inline_keyboard
    assert len(rows) == 1
    buttons = list(rows[0])
    assert len(buttons) == 2

    labels = [b.text for b in buttons]
    assert any("Approve" in s for s in labels)
    assert any("Deny" in s for s in labels)

    # callback_data encodes the decision + approval id
    callbacks = [b.callback_data for b in buttons]
    assert "approval:grant:abc-123" in callbacks
    assert "approval:deny:abc-123" in callbacks


# ========== TelegramTransport integration (mocked Telegram) ==========


def _make_transport_stub(allowed_users=None, approval_chat_id=None):
    """Build a TelegramTransport without actually starting the bot."""
    from gluon.transport.telegram import TelegramTransport

    bot_core = MagicMock()
    # This is fine — we never call bot_core.store from the tests below
    bot_core.store = MagicMock()

    transport = TelegramTransport(
        token="test-token",
        bot_core=bot_core,
        allowed_users=allowed_users or [12345],
        approval_chat_id=approval_chat_id,
    )
    return transport


def test_transport_derives_approval_chat_from_first_allowed_user():
    t = _make_transport_stub(allowed_users=[42, 99])
    assert t.approval_chat_id == 42


def test_transport_respects_explicit_approval_chat_id():
    t = _make_transport_stub(allowed_users=[42], approval_chat_id=999)
    assert t.approval_chat_id == 999


def test_transport_has_no_chat_when_no_allowed_users():
    t = _make_transport_stub(allowed_users=None, approval_chat_id=None)
    # allowed_users=[12345] was the default in the stub; let's rebuild
    from gluon.transport.telegram import TelegramTransport

    t = TelegramTransport("test", MagicMock(), allowed_users=None, approval_chat_id=None)
    assert t.approval_chat_id is None


@pytest.mark.anyio
async def test_post_approval_request_returns_false_without_app(tmp_path):
    """Transport that hasn't started yet must refuse to post (watcher retries)."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    t = _make_transport_stub()
    assert t.app is None  # not started

    ok = await t.post_approval_request(approval)
    assert ok is False


@pytest.mark.anyio
async def test_post_approval_request_skips_without_chat_id(tmp_path):
    """Transport with no approval_chat_id returns True (no-op — avoid retry spam)."""
    from gluon.transport.telegram import TelegramTransport

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    bot_core = MagicMock()
    transport = TelegramTransport("test", bot_core, allowed_users=None, approval_chat_id=None)
    transport.app = MagicMock()  # pretend started

    ok = await transport.post_approval_request(approval)
    assert ok is True  # no-op, but watcher should stop trying


@pytest.mark.anyio
async def test_post_approval_request_sends_message_with_keyboard(tmp_path):
    """Happy path — verify the transport calls send_message with the right args."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    # Mock out the Application + bot
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    transport.app = MagicMock(bot=mock_bot)

    ok = await transport.post_approval_request(approval)
    assert ok is True

    mock_bot.send_message.assert_awaited_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 42
    assert "Approval needed" in call_kwargs["text"]
    assert call_kwargs["parse_mode"] == "Markdown"
    # Keyboard present
    assert call_kwargs["reply_markup"] is not None


@pytest.mark.anyio
async def test_post_approval_falls_back_to_plain_text_on_markdown_failure(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])

    # First call (Markdown) raises, second call (plain) succeeds
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=[Exception("bad md"), None])
    transport.app = MagicMock(bot=mock_bot)

    ok = await transport.post_approval_request(approval)
    assert ok is True
    assert mock_bot.send_message.await_count == 2
    # Second call should NOT have parse_mode
    second_kwargs = mock_bot.send_message.call_args_list[1].kwargs
    assert "parse_mode" not in second_kwargs


# ========== Callback handler ==========


@pytest.mark.anyio
async def test_callback_denies_unauthorized_user(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])  # 42 is allowed
    transport.bot_core.store = store

    # Simulate an update from user 999 (not allowed)
    mock_query = MagicMock()
    mock_query.data = f"approval:grant:{approval.id}"
    mock_query.from_user = MagicMock(id=999)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_approval_callback(mock_update, MagicMock())

    mock_query.answer.assert_awaited_once()
    call_args = mock_query.answer.await_args
    assert "authorized" in call_args.args[0].lower()
    mock_query.edit_message_text.assert_not_called()

    # Approval should remain PENDING
    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_callback_grants_approval_for_authorized_user(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = f"approval:grant:{approval.id}"
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_approval_callback(mock_update, MagicMock())

    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.GRANTED
    assert "telegram:42" in (fresh.decided_by or "")
    mock_query.answer.assert_awaited_once_with("Approved.")
    mock_query.edit_message_text.assert_awaited_once()


@pytest.mark.anyio
async def test_callback_denies_approval_for_authorized_user(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = f"approval:deny:{approval.id}"
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_approval_callback(mock_update, MagicMock())

    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.DENIED
    mock_query.answer.assert_awaited_once_with("Denied.")


@pytest.mark.anyio
async def test_callback_already_decided_shows_status(tmp_path):
    """Tapping a button on an already-decided approval just updates the UI."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    approval = _make_approval(store, project.id)

    # Pre-decide
    store.decide_approval(approval.id, status=ApprovalStatus.GRANTED, decided_by="cli")

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = f"approval:deny:{approval.id}"
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_approval_callback(mock_update, MagicMock())

    # Status should remain GRANTED (not overwritten by the DENY click)
    fresh = store.get_approval(approval.id)
    assert fresh is not None
    assert fresh.status == ApprovalStatus.GRANTED

    # The bot should have told the user
    mock_query.answer.assert_awaited_once()
    answer_text = mock_query.answer.await_args.args[0]
    assert "granted" in answer_text.lower() or "already" in answer_text.lower()


@pytest.mark.anyio
async def test_callback_ignores_malformed_data(tmp_path):
    store = _make_store(tmp_path)
    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    # Missing approval id
    mock_query = MagicMock()
    mock_query.data = "approval:grant"
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    # Should not raise
    await transport._handle_approval_callback(mock_update, MagicMock())

    # Should not have answered the query or edited anything
    mock_query.answer.assert_not_called()
    mock_query.edit_message_text.assert_not_called()


@pytest.mark.anyio
async def test_callback_handles_missing_approval(tmp_path):
    store = _make_store(tmp_path)
    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = "approval:grant:nonexistent-id"
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_approval_callback(mock_update, MagicMock())

    mock_query.answer.assert_awaited_once()
    answer_text = mock_query.answer.await_args.args[0]
    assert "not found" in answer_text.lower()
