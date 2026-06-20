"""Tests for Telegram AskUserQuestion buttons + QuestionWatcher wiring (#163).

Mirrors tests/test_telegram_approvals.py. Covers the message formatter, the
option keyboard, post_question_request, the option-button callback (which
records the answer exactly as the Discord/web path does), and that start()
wires a running QuestionWatcher.

NOTE: the live button → answer → resume round-trip needs manual Telegram
verification; these tests exercise the wiring and the answer-recording, not a
real Telegram server.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gluon.models import PendingQuestion, QuestionStatus
from gluon.question_watcher import QuestionWatcher
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "tg_questions.db")


def _make_project(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    return store.create_project("proj", proj_path)


def _make_question(store: GluonStore, project_id: str, *, multi_select: bool = False) -> PendingQuestion:
    run = store.create_run(project_id=project_id, prompt="test")
    q = PendingQuestion(
        run_id=run.id,
        question_text="Which database should we use?",
        header="Database",
        options=[
            {"label": "PostgreSQL (Recommended)", "description": "Relational"},
            {"label": "SQLite", "description": "Embedded"},
        ],
        multi_select=multi_select,
    )
    return store.create_pending_question(q)


def _make_transport_stub(allowed_users=None, approval_chat_id=None):
    """Build a TelegramTransport without actually starting the bot."""
    from gluon.bot_core import GluonBotCore
    from gluon.transport.telegram import TelegramTransport

    bot_core = MagicMock()
    bot_core.store = MagicMock()
    bot_core.is_authorized = GluonBotCore.is_authorized.__get__(bot_core, GluonBotCore)

    return TelegramTransport(
        token="test-token",
        bot_core=bot_core,
        allowed_users=allowed_users or [12345],
        approval_chat_id=approval_chat_id,
    )


# ========== Message formatter ==========


def test_format_question_message_includes_core_fields(tmp_path):
    from gluon.transport.telegram import format_question_message

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    body = format_question_message(q)
    assert "Database" in body  # header
    assert "Which database" in body  # question text
    assert q.id[:8] in body


def test_format_question_message_notes_multi_select(tmp_path):
    from gluon.transport.telegram import format_question_message

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id, multi_select=True)

    body = format_question_message(q)
    assert "Multi-select" in body


# ========== Option keyboard ==========


def test_build_question_keyboard_one_button_per_option(tmp_path):
    from gluon.transport.telegram import build_question_keyboard

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    kb = build_question_keyboard(q)
    rows = kb.inline_keyboard
    assert len(rows) == 2  # one row per option

    # callback_data encodes "question:<id>:<index>" and stays under 64 bytes
    callbacks = [row[0].callback_data for row in rows]
    assert callbacks[0] == f"question:{q.id}:0"
    assert callbacks[1] == f"question:{q.id}:1"
    for cb in callbacks:
        assert len(cb.encode()) < 64


# ========== post_question_request ==========


@pytest.mark.anyio
async def test_post_question_request_returns_false_without_app(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    t = _make_transport_stub()
    assert t.app is None

    ok = await t.post_question_request(q)
    assert ok is False


@pytest.mark.anyio
async def test_post_question_request_skips_without_chat_id(tmp_path):
    from gluon.transport.telegram import TelegramTransport

    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    transport = TelegramTransport("test", MagicMock(), allowed_users=None, approval_chat_id=None)
    transport.app = MagicMock()  # pretend started

    ok = await transport.post_question_request(q)
    assert ok is True  # no-op, watcher should stop retrying


@pytest.mark.anyio
async def test_post_question_request_sends_message_with_keyboard(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    transport.app = MagicMock(bot=mock_bot)

    ok = await transport.post_question_request(q)
    assert ok is True

    mock_bot.send_message.assert_awaited_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 42
    assert "Database" in call_kwargs["text"]
    assert call_kwargs["parse_mode"] == "Markdown"
    assert call_kwargs["reply_markup"] is not None


@pytest.mark.anyio
async def test_post_question_falls_back_to_plain_text(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=[Exception("bad md"), None])
    transport.app = MagicMock(bot=mock_bot)

    ok = await transport.post_question_request(q)
    assert ok is True
    assert mock_bot.send_message.await_count == 2
    second_kwargs = mock_bot.send_message.call_args_list[1].kwargs
    assert "parse_mode" not in second_kwargs


# ========== QuestionWatcher wiring (transport is a valid poster) ==========


@pytest.mark.anyio
async def test_question_watcher_tick_posts_via_telegram(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    transport.app = MagicMock(bot=mock_bot)

    watcher = QuestionWatcher(store, transport)
    posted = await watcher.tick()

    assert posted == 1
    mock_bot.send_message.assert_awaited_once()
    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.notified_at is not None  # marked delivered


# ========== Option callback ==========


@pytest.mark.anyio
async def test_callback_records_answer_for_authorized_user(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = f"question:{q.id}:1"  # pick "SQLite"
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_question_callback(mock_update, MagicMock())

    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.status == QuestionStatus.ANSWERED
    assert fresh.selected_labels == ["SQLite"]
    assert fresh.answer_source == "user"
    mock_query.answer.assert_awaited_once()
    assert "SQLite" in mock_query.answer.await_args.args[0]


@pytest.mark.anyio
async def test_callback_denies_unauthorized_user(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = f"question:{q.id}:0"
    mock_query.from_user = MagicMock(id=999)  # not allowed
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_question_callback(mock_update, MagicMock())

    assert "authorized" in mock_query.answer.await_args.args[0].lower()
    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.status == QuestionStatus.PENDING  # untouched


@pytest.mark.anyio
async def test_callback_already_answered_shows_status(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)
    q.answer(["PostgreSQL (Recommended)"], source="user")
    store.update_pending_question(q)

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = f"question:{q.id}:1"  # try to change to SQLite
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_question_callback(mock_update, MagicMock())

    # Answer must NOT be overwritten
    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.selected_labels == ["PostgreSQL (Recommended)"]
    assert "already" in mock_query.answer.await_args.args[0].lower()


@pytest.mark.anyio
async def test_callback_ignores_malformed_data(tmp_path):
    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = MagicMock()

    mock_query = MagicMock()
    mock_query.data = "question:only-id"  # missing index
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_question_callback(mock_update, MagicMock())

    mock_query.answer.assert_not_called()
    mock_query.edit_message_text.assert_not_called()


@pytest.mark.anyio
async def test_callback_handles_missing_question(tmp_path):
    store = _make_store(tmp_path)
    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = "question:nonexistent-id:0"
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_question_callback(mock_update, MagicMock())

    assert "not found" in mock_query.answer.await_args.args[0].lower()


@pytest.mark.anyio
async def test_callback_rejects_out_of_range_index(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    q = _make_question(store, project.id)  # 2 options (indexes 0,1)

    transport = _make_transport_stub(allowed_users=[42])
    transport.bot_core.store = store

    mock_query = MagicMock()
    mock_query.data = f"question:{q.id}:9"  # out of range
    mock_query.from_user = MagicMock(id=42)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    await transport._handle_question_callback(mock_update, MagicMock())

    assert "invalid" in mock_query.answer.await_args.args[0].lower()
    fresh = store.get_pending_question(q.id)
    assert fresh is not None
    assert fresh.status == QuestionStatus.PENDING


# ========== start() wires a QuestionWatcher ==========


@pytest.mark.anyio
async def test_start_wires_running_question_watcher(tmp_path):
    """start() must construct + start a QuestionWatcher (and stop() tears it down)."""
    from gluon.transport.telegram import TelegramTransport

    store = _make_store(tmp_path)

    bot_core = MagicMock()
    bot_core.store = store
    bot_core.recover_stale_runs = MagicMock()
    bot_core.git_manager = MagicMock()
    bot_core.git_manager.start_background_sync = AsyncMock()
    bot_core.git_manager.stop_background_sync = AsyncMock()

    transport = TelegramTransport("test-token", bot_core, allowed_users=[42])

    # Stub out the whole python-telegram-bot Application lifecycle.
    mock_app = MagicMock()
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock()
    mock_app.updater.stop = AsyncMock()

    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app

    with patch("gluon.transport.telegram.Application") as mock_application:
        mock_application.builder.return_value = mock_builder
        await transport.start()

        watcher = getattr(transport, "_question_watcher", None)
        assert isinstance(watcher, QuestionWatcher)
        assert watcher.is_running

        await transport.stop()
        assert not watcher.is_running
