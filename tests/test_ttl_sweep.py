"""WS-7 regression: the chat-bot TTL cleanup helpers actually prune expired rows.

These were defined but never called by any scheduler, so the tables grew
unbounded. They are now wired into the periodic auth/TTL sweep; this verifies
the underlying delete works.
"""

from __future__ import annotations

from pathlib import Path

from gluon.store import GluonStore


def _store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "ttl.db")


def test_cleanup_expired_chat_history(tmp_path: Path):
    store = _store(tmp_path)
    # One live entry, one already-expired (negative TTL => past expires_at).
    store.create_chat_history("telegram:1", "telegram", "user", "fresh", ttl_hours=48)
    store.create_chat_history("telegram:1", "telegram", "user", "stale", ttl_hours=-1)

    removed = store.cleanup_expired_chat_history()

    assert removed == 1
    remaining = store.get_chat_history("telegram:1")
    assert [e.text for e in remaining] == ["fresh"]


def test_cleanup_expired_message_run_maps(tmp_path: Path):
    store = _store(tmp_path)
    store.create_message_run_map("telegram", "msg-live", "run-1", "chat-1", "user-1", ttl_days=7)
    store.create_message_run_map("telegram", "msg-stale", "run-2", "chat-1", "user-1", ttl_days=-1)

    removed = store.cleanup_expired_message_run_maps()

    assert removed == 1
