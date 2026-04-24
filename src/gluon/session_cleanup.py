"""Session cleanup — delete stale Claude session JSONL files (Theme C5).

Every `fork_session` call creates a new JSONL file in
`~/.claude/projects/<hash>/<session_id>.jsonl`. Long-running projects with
many resumes accumulate dozens of multi-MB files. This module handles:

  1. **Tracking**: when `run.claude_session_id` changes to a new value, the
     previous session ID is recorded in `run.metadata["previous_session_ids"]`.

  2. **Per-run cleanup**: when a run reaches COMPLETED, optionally delete
     the previous (now-forked-from) session files — the current
     `claude_session_id` is kept.

  3. **Batch cleanup**: the `gluon sessions cleanup` CLI and the
     `cleanup_orphan_sessions` function sweep the disk for JSONL files no
     longer referenced by any active run, respecting a retention window.

All delete operations go through the SDK's `delete_session` so we don't
accidentally corrupt the session store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gluon.models import ExecutionRun
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


# Keys used inside run.metadata for tracking session IDs
METADATA_PREVIOUS_SESSIONS = "previous_session_ids"

# Settings keys
SETTING_CLEANUP_ENABLED = "session_cleanup_enabled"
SETTING_RETENTION_DAYS = "session_cleanup_retention_days"

# Default retention: 30 days. A session younger than this is never deleted
# automatically even if no run references it — gives operators a window to
# manually inspect or resume.
DEFAULT_RETENTION_DAYS = 30

# Default: cleanup is OFF. Operators opt in via the setting.
DEFAULT_CLEANUP_ENABLED = False


@dataclass
class CleanupPreview:
    """Planned cleanup without side-effects. Use with --dry-run."""

    session_ids: list[str] = field(default_factory=list)
    skipped_referenced: int = 0
    skipped_recent: int = 0
    total_bytes: int = 0

    @property
    def count(self) -> int:
        return len(self.session_ids)


@dataclass
class CleanupResult:
    """Outcome of a cleanup sweep."""

    deleted: int = 0
    failed: int = 0
    skipped_referenced: int = 0
    skipped_recent: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)


def is_cleanup_enabled(store: GluonStore) -> bool:
    """Resolve the cleanup on/off flag from the settings table."""
    raw = store.get_setting(SETTING_CLEANUP_ENABLED)
    if raw is None:
        return DEFAULT_CLEANUP_ENABLED
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_retention_days(store: GluonStore) -> int:
    """Resolve retention days from settings with a sensible fallback."""
    raw = store.get_setting(SETTING_RETENTION_DAYS)
    if raw is None:
        return DEFAULT_RETENTION_DAYS
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", SETTING_RETENTION_DAYS, raw, DEFAULT_RETENTION_DAYS)
        return DEFAULT_RETENTION_DAYS


def track_previous_session_id(run: ExecutionRun, new_session_id: str | None) -> bool:
    """Record the run's prior session ID in `run.metadata` before it's overwritten.

    Returns True if a new entry was appended. The caller is responsible for
    persisting the run via `store.update_run(run)`.

    Semantics:
      - No-op when `new_session_id` is falsy or equals the current one.
      - Appends the *current* `run.claude_session_id` (if any) into
        `metadata["previous_session_ids"]`. Duplicates are ignored.
    """
    if not new_session_id:
        return False
    if run.claude_session_id == new_session_id:
        return False

    old_id = run.claude_session_id
    if not old_id:
        return False

    if run.metadata is None:
        run.metadata = {}

    previous = run.metadata.get(METADATA_PREVIOUS_SESSIONS)
    if not isinstance(previous, list):
        previous = []

    if old_id in previous:
        return False

    previous.append(old_id)
    run.metadata[METADATA_PREVIOUS_SESSIONS] = previous
    return True


def _delete_session_safely(session_id: str, directory: str | None) -> bool:
    """Delete a session via the SDK, returning True on success.

    Swallows FileNotFoundError (already gone) as success; logs other errors.
    """
    try:
        from claude_agent_sdk import delete_session
    except ImportError:
        logger.warning("claude_agent_sdk.delete_session unavailable; cannot clean up %s", session_id[:8])
        return False

    try:
        delete_session(session_id, directory=directory)
        return True
    except FileNotFoundError:
        # Already deleted — count as success
        return True
    except Exception:
        logger.warning("Failed to delete session %s", session_id[:8], exc_info=True)
        return False


def _session_file_size(session_id: str, directory: str | None) -> int:
    """Return the size of the session's JSONL file, or 0 if unknown."""
    try:
        from claude_agent_sdk import get_session_info
    except ImportError:
        return 0
    try:
        info = get_session_info(session_id, directory=directory)
        if info is None:
            return 0
        return int(getattr(info, "file_size", 0) or 0)
    except Exception:
        return 0


def cleanup_run_sessions(
    run: ExecutionRun,
    *,
    directory: str | None,
    dry_run: bool = False,
) -> CleanupResult:
    """Delete previous session files for a completed run, keeping the latest.

    Only operates on `run.metadata["previous_session_ids"]`. The current
    `run.claude_session_id` is never touched.
    """
    result = CleanupResult()

    if run.metadata is None:
        return result

    previous = run.metadata.get(METADATA_PREVIOUS_SESSIONS) or []
    if not isinstance(previous, list):
        return result

    for session_id in previous:
        if not isinstance(session_id, str) or not session_id:
            continue
        size = _session_file_size(session_id, directory)
        if dry_run:
            result.deleted += 1
            result.bytes_freed += size
            continue

        ok = _delete_session_safely(session_id, directory)
        if ok:
            result.deleted += 1
            result.bytes_freed += size
        else:
            result.failed += 1
            result.errors.append(session_id)

    # On successful real delete, clear the list so we don't try again next time
    if not dry_run and result.failed == 0:
        run.metadata.pop(METADATA_PREVIOUS_SESSIONS, None)

    return result


def _iter_active_session_ids(store: GluonStore) -> set[str]:
    """Return session IDs currently referenced by non-terminal runs.

    These are protected from orphan cleanup regardless of age — we might
    still need to resume them.
    """
    from gluon.models import RunStatus

    active_statuses = {
        RunStatus.PENDING.value,
        RunStatus.RUNNING.value,
        RunStatus.REVIEW.value,
    }
    active: set[str] = set()
    for run in store.list_runs(limit=10000):
        if run.claude_session_id and run.status.value in active_statuses:
            active.add(run.claude_session_id)
    return active


def _iter_referenced_session_ids(store: GluonStore) -> set[str]:
    """Return ALL session IDs mentioned by any run — current or previous.

    Used by the orphan sweeper: a JSONL file not in this set is a candidate
    for deletion.
    """
    referenced: set[str] = set()
    for run in store.list_runs(limit=100000):
        if run.claude_session_id:
            referenced.add(run.claude_session_id)
        if run.metadata:
            prev = run.metadata.get(METADATA_PREVIOUS_SESSIONS)
            if isinstance(prev, list):
                for sid in prev:
                    if isinstance(sid, str) and sid:
                        referenced.add(sid)
    return referenced


def cleanup_orphan_sessions(
    store: GluonStore,
    *,
    directory: str | None = None,
    older_than_days: int | None = None,
    dry_run: bool = False,
) -> CleanupResult | CleanupPreview:
    """Sweep the SDK's session store for JSONL files unreferenced by any run.

    Args:
        store: Gluon store (used for the run index + settings).
        directory: If given, scope the sweep to a single project directory.
        older_than_days: Only consider sessions with last_modified older
            than N days. Defaults to the `session_cleanup_retention_days`
            setting.
        dry_run: If True, return a CleanupPreview without deleting anything.

    Returns:
        CleanupResult (real run) or CleanupPreview (dry run).
    """
    try:
        from claude_agent_sdk import list_sessions
    except ImportError:
        logger.warning("claude_agent_sdk.list_sessions unavailable; nothing to do")
        return CleanupPreview() if dry_run else CleanupResult()

    retention = older_than_days if older_than_days is not None else get_retention_days(store)
    cutoff = datetime.now(UTC) - timedelta(days=retention)

    # SDK returns a list of SDKSessionInfo sorted by last_modified desc
    try:
        sessions = list_sessions(directory=directory, limit=100000)
    except Exception:
        logger.exception("Failed to list SDK sessions")
        return CleanupPreview() if dry_run else CleanupResult()

    active = _iter_active_session_ids(store)  # protected regardless of age
    referenced = _iter_referenced_session_ids(store)

    preview = CleanupPreview()
    result = CleanupResult()

    for session in sessions:
        session_id = session.session_id
        size = int(getattr(session, "file_size", 0) or 0)

        # Protect sessions still referenced by active runs
        if session_id in active:
            preview.skipped_referenced += 1
            result.skipped_referenced += 1
            continue

        # Protect sessions referenced by any run (even completed ones) —
        # the operator may want to resume or replay. Only orphans go.
        if session_id in referenced:
            preview.skipped_referenced += 1
            result.skipped_referenced += 1
            continue

        # Respect the retention window — only touch old files
        last_modified_ts = getattr(session, "last_modified", None)
        if last_modified_ts is not None:
            try:
                # last_modified is epoch ms per the SDK docs
                last_modified = datetime.fromtimestamp(last_modified_ts / 1000, tz=UTC)
            except (TypeError, ValueError, OSError):
                last_modified = None
            if last_modified is not None and last_modified > cutoff:
                preview.skipped_recent += 1
                result.skipped_recent += 1
                continue

        # Candidate for deletion
        if dry_run:
            preview.session_ids.append(session_id)
            preview.total_bytes += size
            continue

        ok = _delete_session_safely(session_id, getattr(session, "cwd", None) or directory)
        if ok:
            result.deleted += 1
            result.bytes_freed += size
        else:
            result.failed += 1
            result.errors.append(session_id)

    return preview if dry_run else result


__all__ = [
    "CleanupPreview",
    "CleanupResult",
    "DEFAULT_CLEANUP_ENABLED",
    "DEFAULT_RETENTION_DAYS",
    "METADATA_PREVIOUS_SESSIONS",
    "SETTING_CLEANUP_ENABLED",
    "SETTING_RETENTION_DAYS",
    "cleanup_orphan_sessions",
    "cleanup_run_sessions",
    "get_retention_days",
    "is_cleanup_enabled",
    "track_previous_session_id",
]
