"""Tests for session cleanup (Theme C5)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gluon.models import ApprovalStatus, RunStatus
from gluon.session_cleanup import (
    DEFAULT_CLEANUP_ENABLED,
    DEFAULT_RETENTION_DAYS,
    METADATA_PREVIOUS_SESSIONS,
    SETTING_CLEANUP_ENABLED,
    SETTING_RETENTION_DAYS,
    CleanupPreview,
    CleanupResult,
    cleanup_orphan_sessions,
    cleanup_run_sessions,
    get_retention_days,
    is_cleanup_enabled,
    track_previous_session_id,
)
from gluon.store import GluonStore


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "session_cleanup.db")


def _make_project(store: GluonStore, tmp_path: Path):
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    return store.create_project("proj", proj_path)


# ========== Settings ==========


def test_is_cleanup_enabled_default_is_off(tmp_path):
    store = _make_store(tmp_path)
    assert is_cleanup_enabled(store) is DEFAULT_CLEANUP_ENABLED
    assert is_cleanup_enabled(store) is False


def test_is_cleanup_enabled_reads_truthy_values(tmp_path):
    store = _make_store(tmp_path)
    for value in ("true", "True", "1", "yes", "ON"):
        store.set_setting(SETTING_CLEANUP_ENABLED, value)
        assert is_cleanup_enabled(store) is True, f"should be truthy: {value}"


def test_is_cleanup_enabled_reads_falsy_values(tmp_path):
    store = _make_store(tmp_path)
    for value in ("false", "0", "no", "off", ""):
        store.set_setting(SETTING_CLEANUP_ENABLED, value)
        assert is_cleanup_enabled(store) is False, f"should be falsy: {value!r}"


def test_get_retention_days_default(tmp_path):
    store = _make_store(tmp_path)
    assert get_retention_days(store) == DEFAULT_RETENTION_DAYS


def test_get_retention_days_reads_setting(tmp_path):
    store = _make_store(tmp_path)
    store.set_setting(SETTING_RETENTION_DAYS, "7")
    assert get_retention_days(store) == 7


def test_get_retention_days_ignores_invalid(tmp_path):
    store = _make_store(tmp_path)
    store.set_setting(SETTING_RETENTION_DAYS, "not-a-number")
    assert get_retention_days(store) == DEFAULT_RETENTION_DAYS


def test_get_retention_days_clamps_negative(tmp_path):
    store = _make_store(tmp_path)
    store.set_setting(SETTING_RETENTION_DAYS, "-5")
    assert get_retention_days(store) == 0


# ========== track_previous_session_id ==========


def test_track_noop_on_empty_new_id(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.claude_session_id = "session-a"

    changed = track_previous_session_id(run, None)
    assert changed is False
    assert run.metadata is None or METADATA_PREVIOUS_SESSIONS not in (run.metadata or {})


def test_track_noop_when_ids_match(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.claude_session_id = "session-a"

    assert track_previous_session_id(run, "session-a") is False


def test_track_noop_when_no_current_session(tmp_path):
    """If the run has no claude_session_id yet, there's nothing to archive."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    assert run.claude_session_id is None

    assert track_previous_session_id(run, "session-a") is False


def test_track_appends_old_id(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.claude_session_id = "session-a"

    changed = track_previous_session_id(run, "session-b")
    assert changed is True
    assert run.metadata is not None
    assert run.metadata[METADATA_PREVIOUS_SESSIONS] == ["session-a"]


def test_track_accumulates_multiple_forks(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    run.claude_session_id = "session-a"
    track_previous_session_id(run, "session-b")
    run.claude_session_id = "session-b"

    track_previous_session_id(run, "session-c")
    run.claude_session_id = "session-c"

    track_previous_session_id(run, "session-d")

    assert run.metadata[METADATA_PREVIOUS_SESSIONS] == ["session-a", "session-b", "session-c"]


def test_track_dedupes_entries(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.claude_session_id = "session-a"

    track_previous_session_id(run, "session-b")
    # Simulate a weird sequence where old-id gets revisited
    run.claude_session_id = "session-a"
    changed = track_previous_session_id(run, "session-b")

    # "session-a" is already in the list, so no new append
    assert changed is False
    assert run.metadata[METADATA_PREVIOUS_SESSIONS] == ["session-a"]


# ========== cleanup_run_sessions ==========


def test_cleanup_run_sessions_no_metadata_noop(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")

    result = cleanup_run_sessions(run, directory=str(tmp_path))
    assert isinstance(result, CleanupResult)
    assert result.deleted == 0
    assert result.failed == 0


def test_cleanup_run_sessions_dry_run(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.metadata = {METADATA_PREVIOUS_SESSIONS: ["old-1", "old-2"]}

    with (
        patch("gluon.session_cleanup._delete_session_safely") as mock_delete,
        patch("gluon.session_cleanup._session_file_size", return_value=1024),
    ):
        result = cleanup_run_sessions(run, directory=str(tmp_path), dry_run=True)

    mock_delete.assert_not_called()
    assert result.deleted == 2  # counted as "would delete"
    assert result.bytes_freed == 2048
    # Metadata untouched in dry run
    assert run.metadata[METADATA_PREVIOUS_SESSIONS] == ["old-1", "old-2"]


def test_cleanup_run_sessions_happy_path_clears_metadata(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.metadata = {METADATA_PREVIOUS_SESSIONS: ["old-1", "old-2"]}

    with (
        patch("gluon.session_cleanup._delete_session_safely", return_value=True),
        patch("gluon.session_cleanup._session_file_size", return_value=500),
    ):
        result = cleanup_run_sessions(run, directory=str(tmp_path))

    assert result.deleted == 2
    assert result.failed == 0
    assert result.bytes_freed == 1000
    # Metadata cleared on successful cleanup
    assert METADATA_PREVIOUS_SESSIONS not in run.metadata


def test_cleanup_run_sessions_keeps_metadata_on_failure(tmp_path):
    """If any delete fails, the list isn't cleared so the next run retries."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.metadata = {METADATA_PREVIOUS_SESSIONS: ["old-1", "old-2"]}

    with (
        patch("gluon.session_cleanup._delete_session_safely", return_value=False),
        patch("gluon.session_cleanup._session_file_size", return_value=500),
    ):
        result = cleanup_run_sessions(run, directory=str(tmp_path))

    assert result.deleted == 0
    assert result.failed == 2
    # List retained — will retry next time
    assert run.metadata[METADATA_PREVIOUS_SESSIONS] == ["old-1", "old-2"]


def test_cleanup_run_sessions_ignores_invalid_entries(tmp_path):
    """Garbage in the list shouldn't crash the cleaner."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.metadata = {METADATA_PREVIOUS_SESSIONS: ["valid-id", None, "", 42, "another-valid"]}

    with (
        patch("gluon.session_cleanup._delete_session_safely", return_value=True),
        patch("gluon.session_cleanup._session_file_size", return_value=100),
    ):
        result = cleanup_run_sessions(run, directory=str(tmp_path))

    # Only 2 valid IDs — the garbage entries are skipped
    assert result.deleted == 2


# ========== cleanup_orphan_sessions ==========


def _mock_session(session_id: str, *, size: int = 1024, days_old: int = 0, cwd: str = "/p"):
    """Build a fake SDKSessionInfo for the orphan sweeper."""
    last_modified_ms = int((datetime.now(UTC) - timedelta(days=days_old)).timestamp() * 1000)
    return SimpleNamespace(
        session_id=session_id,
        file_size=size,
        last_modified=last_modified_ms,
        cwd=cwd,
    )


def test_cleanup_orphan_sessions_skips_referenced_by_active_run(tmp_path):
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    # Active run references "active-session"
    run = store.create_run(project_id=project.id, prompt="t")
    run.claude_session_id = "active-session"
    run.status = RunStatus.RUNNING
    store.update_run(run)

    fake_sessions = [_mock_session("active-session", days_old=60)]

    with (
        patch("claude_agent_sdk.list_sessions", return_value=fake_sessions),
        patch("gluon.session_cleanup._delete_session_safely") as mock_delete,
    ):
        preview = cleanup_orphan_sessions(store, older_than_days=30, dry_run=True)

    mock_delete.assert_not_called()
    assert isinstance(preview, CleanupPreview)
    assert preview.count == 0
    assert preview.skipped_referenced == 1


def test_cleanup_orphan_sessions_skips_previous_session_refs(tmp_path):
    """Sessions in a COMPLETED run's previous_session_ids are still protected."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.claude_session_id = "current-session"
    run.metadata = {METADATA_PREVIOUS_SESSIONS: ["fork-ancestor"]}
    run.status = RunStatus.COMPLETED
    store.update_run(run)

    fake_sessions = [_mock_session("fork-ancestor", days_old=60)]

    with patch("claude_agent_sdk.list_sessions", return_value=fake_sessions):
        preview = cleanup_orphan_sessions(store, older_than_days=30, dry_run=True)

    assert isinstance(preview, CleanupPreview)
    assert preview.count == 0
    assert preview.skipped_referenced == 1


def test_cleanup_orphan_sessions_skips_recent(tmp_path):
    store = _make_store(tmp_path)
    _make_project(store, tmp_path)

    # Session is orphan but only 2 days old
    fake_sessions = [_mock_session("young-orphan", days_old=2)]

    with patch("claude_agent_sdk.list_sessions", return_value=fake_sessions):
        preview = cleanup_orphan_sessions(store, older_than_days=30, dry_run=True)

    assert isinstance(preview, CleanupPreview)
    assert preview.count == 0
    assert preview.skipped_recent == 1


def test_cleanup_orphan_sessions_deletes_old_orphans(tmp_path):
    store = _make_store(tmp_path)
    _make_project(store, tmp_path)

    fake_sessions = [
        _mock_session("old-1", days_old=60, size=1000),
        _mock_session("old-2", days_old=45, size=2000),
    ]

    with (
        patch("claude_agent_sdk.list_sessions", return_value=fake_sessions),
        patch("gluon.session_cleanup._delete_session_safely", return_value=True) as mock_delete,
    ):
        result = cleanup_orphan_sessions(store, older_than_days=30, dry_run=False)

    assert isinstance(result, CleanupResult)
    assert result.deleted == 2
    assert result.failed == 0
    assert result.bytes_freed == 3000
    assert mock_delete.call_count == 2


def test_cleanup_orphan_sessions_dry_run_returns_preview(tmp_path):
    store = _make_store(tmp_path)
    _make_project(store, tmp_path)

    fake_sessions = [
        _mock_session("old-1", days_old=60, size=1000),
        _mock_session("recent", days_old=5, size=500),
    ]

    with (
        patch("claude_agent_sdk.list_sessions", return_value=fake_sessions),
        patch("gluon.session_cleanup._delete_session_safely") as mock_delete,
    ):
        preview = cleanup_orphan_sessions(store, older_than_days=30, dry_run=True)

    mock_delete.assert_not_called()
    assert isinstance(preview, CleanupPreview)
    assert preview.count == 1
    assert preview.session_ids == ["old-1"]
    assert preview.total_bytes == 1000
    assert preview.skipped_recent == 1


def test_cleanup_orphan_sessions_tolerates_list_failure(tmp_path):
    """If list_sessions raises, return an empty result instead of crashing."""
    store = _make_store(tmp_path)

    with patch("claude_agent_sdk.list_sessions", side_effect=RuntimeError("boom")):
        result = cleanup_orphan_sessions(store, older_than_days=30, dry_run=False)

    assert isinstance(result, CleanupResult)
    assert result.deleted == 0
    assert result.failed == 0


def test_cleanup_orphan_sessions_honors_retention_from_settings(tmp_path):
    """When older_than_days is None, the retention comes from the setting."""
    store = _make_store(tmp_path)
    _make_project(store, tmp_path)

    # Set retention to 7 days; orphan is 10 days old → should be deleted
    store.set_setting(SETTING_RETENTION_DAYS, "7")

    fake_sessions = [_mock_session("old", days_old=10, size=500)]

    with (
        patch("claude_agent_sdk.list_sessions", return_value=fake_sessions),
        patch("gluon.session_cleanup._delete_session_safely", return_value=True),
    ):
        result = cleanup_orphan_sessions(store, dry_run=False)

    assert isinstance(result, CleanupResult)
    assert result.deleted == 1


# ========== Doctor check ==========


def test_doctor_check_ok_when_under_threshold(tmp_path):
    from gluon.doctor import check_claude_session_disk_usage

    store = _make_store(tmp_path)
    # Build sessions totaling 500 MB (under 1 GB threshold)
    fake_sessions = [
        SimpleNamespace(session_id=f"s-{i}", file_size=100 * 1024 * 1024, last_modified=0, cwd="/") for i in range(5)
    ]

    with patch("claude_agent_sdk.list_sessions", return_value=fake_sessions):
        result = check_claude_session_disk_usage(store)

    assert result.status == "ok"
    assert "5" in result.message  # count


def test_doctor_check_warns_over_threshold(tmp_path):
    from gluon.doctor import check_claude_session_disk_usage

    store = _make_store(tmp_path)
    # 2 GB of sessions — should warn
    fake_sessions = [
        SimpleNamespace(session_id=f"s-{i}", file_size=400 * 1024 * 1024, last_modified=0, cwd="/") for i in range(5)
    ]

    with patch("claude_agent_sdk.list_sessions", return_value=fake_sessions):
        result = check_claude_session_disk_usage(store)

    assert result.status == "warn"
    assert result.fixable is True
    assert "sessions-cleanup" in result.message.lower()


def test_doctor_check_tolerates_list_failure(tmp_path):
    from gluon.doctor import check_claude_session_disk_usage

    store = _make_store(tmp_path)

    with patch("claude_agent_sdk.list_sessions", side_effect=RuntimeError("boom")):
        result = check_claude_session_disk_usage(store)

    # Should not raise — return an ok result with context
    assert result.status == "ok"


# ========== Integration: approval status untouched ==========


def test_run_approval_status_not_affected_by_session_cleanup(tmp_path):
    """Sanity check that cleanup doesn't touch other ExecutionRun fields."""
    store = _make_store(tmp_path)
    project = _make_project(store, tmp_path)
    run = store.create_run(project_id=project.id, prompt="t")
    run.metadata = {METADATA_PREVIOUS_SESSIONS: ["old-1"]}
    run.claude_session_id = "current"

    with (
        patch("gluon.session_cleanup._delete_session_safely", return_value=True),
        patch("gluon.session_cleanup._session_file_size", return_value=0),
    ):
        cleanup_run_sessions(run, directory=str(tmp_path))

    # Current session and other fields untouched
    assert run.claude_session_id == "current"
    # The approval policy is unrelated to session cleanup but let's be sure
    assert run.approval_policy is not None  # default PERMISSIVE

    # Cross-reference: ApprovalStatus should still be importable (nothing leaked)
    assert ApprovalStatus.PENDING.value == "pending"
