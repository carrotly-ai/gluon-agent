"""System health diagnostics for Gluon Agent.

Provides health checks and auto-fix capabilities for common issues:
database integrity, orphan processes, stale runs, disk usage, and expired questions.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from gluon.models import ExecutionRun, QuestionStatus, RunStatus, utc_now
from gluon.store import GluonStore

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticResult:
    """Result of a single health check."""

    name: str
    status: Literal["ok", "warn", "error"]
    message: str
    fixable: bool = False
    details: list[str] = field(default_factory=list)


def check_db_integrity(store: GluonStore) -> DiagnosticResult:
    """Run PRAGMA integrity_check on the database."""
    with store._get_conn() as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result and result[0] == "ok":
            return DiagnosticResult(
                name="Database Integrity",
                status="ok",
                message="Database integrity check passed",
            )
        return DiagnosticResult(
            name="Database Integrity",
            status="error",
            message=f"Database integrity check failed: {result[0] if result else 'unknown'}",
        )


def check_orphan_processes(store: GluonStore) -> DiagnosticResult:
    """Find runs with status=RUNNING but PID dead."""
    active_runs = store.list_active_runs()
    orphans: list[ExecutionRun] = []

    for run in active_runs:
        if run.status != RunStatus.RUNNING or not run.pid:
            continue
        try:
            os.kill(run.pid, 0)
        except ProcessLookupError:
            orphans.append(run)
        except PermissionError:
            pass  # Process exists

    if not orphans:
        return DiagnosticResult(
            name="Orphan Processes",
            status="ok",
            message="No orphan processes found",
        )
    return DiagnosticResult(
        name="Orphan Processes",
        status="error",
        message=f"{len(orphans)} run(s) with dead PIDs",
        fixable=True,
        details=[f"Run {r.id[:8]} (PID {r.pid})" for r in orphans],
    )


def check_stale_runs(store: GluonStore) -> DiagnosticResult:
    """Find runs stuck in RUNNING for >4 hours."""
    active_runs = store.list_active_runs()
    stale: list[ExecutionRun] = []
    now = utc_now()

    for run in active_runs:
        if run.status != RunStatus.RUNNING or not run.started_at:
            continue
        age_hours = (now - run.started_at).total_seconds() / 3600
        if age_hours > 4:
            stale.append(run)

    if not stale:
        return DiagnosticResult(
            name="Stale Runs",
            status="ok",
            message="No stale runs found",
        )
    return DiagnosticResult(
        name="Stale Runs",
        status="warn",
        message=f"{len(stale)} run(s) running for >4 hours",
        fixable=True,
        details=[f"Run {r.id[:8]} started {r.started_at.strftime('%Y-%m-%d %H:%M')}" for r in stale],
    )


def check_log_disk_usage(log_path: Path) -> DiagnosticResult:
    """Check total disk usage of log directory."""
    if not log_path.exists():
        return DiagnosticResult(
            name="Log Disk Usage",
            status="ok",
            message="Log directory does not exist yet",
        )

    total_bytes = sum(f.stat().st_size for f in log_path.rglob("*") if f.is_file())
    total_mb = total_bytes / (1024 * 1024)
    total_gb = total_mb / 1024

    if total_gb > 5:
        return DiagnosticResult(
            name="Log Disk Usage",
            status="error",
            message=f"Log directory using {total_gb:.1f} GB (>5 GB)",
        )
    elif total_gb > 1:
        return DiagnosticResult(
            name="Log Disk Usage",
            status="warn",
            message=f"Log directory using {total_gb:.1f} GB (>1 GB)",
        )
    return DiagnosticResult(
        name="Log Disk Usage",
        status="ok",
        message=f"Log directory using {total_mb:.0f} MB",
    )


def check_stale_pending_questions(store: GluonStore) -> DiagnosticResult:
    """Find pending questions past their expiry."""
    now = utc_now()
    stale_count = 0

    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT id, expires_at FROM pending_questions WHERE status = ?",
            (QuestionStatus.PENDING.value,),
        ).fetchall()

    for row in rows:
        if row["expires_at"]:
            from datetime import UTC, datetime

            expires = datetime.fromisoformat(row["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < now:
                stale_count += 1

    if stale_count == 0:
        return DiagnosticResult(
            name="Stale Questions",
            status="ok",
            message="No expired pending questions",
        )
    return DiagnosticResult(
        name="Stale Questions",
        status="warn",
        message=f"{stale_count} expired pending question(s)",
        fixable=True,
    )


def check_activity_log_size(store: GluonStore) -> DiagnosticResult:
    """Warn if activity_events table has >100k events."""
    try:
        with store._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM activity_events").fetchone()
            count = row["cnt"] if row else 0
    except Exception:
        return DiagnosticResult(
            name="Activity Log Size",
            status="ok",
            message="Activity log table not yet created",
        )

    if count > 100_000:
        return DiagnosticResult(
            name="Activity Log Size",
            status="warn",
            message=f"{count:,} activity events (>100k). Consider running cleanup.",
            fixable=True,
        )
    return DiagnosticResult(
        name="Activity Log Size",
        status="ok",
        message=f"{count:,} activity events",
    )


def check_stale_queue_claims(store: GluonStore) -> DiagnosticResult:
    """Warn if work queue items claimed >30min with no heartbeat."""
    try:
        from gluon.models import WorkQueueStatus

        with store._get_conn() as conn:
            from datetime import timedelta

            cutoff = (utc_now() - timedelta(minutes=30)).isoformat()
            rows = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM work_queue
                WHERE status = ? AND claimed_at < ?
                AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
                """,
                (WorkQueueStatus.CLAIMED.value, cutoff, cutoff),
            ).fetchone()
            count = rows["cnt"] if rows else 0
    except Exception:
        return DiagnosticResult(
            name="Stale Queue Claims",
            status="ok",
            message="Work queue table not yet created",
        )

    if count > 0:
        return DiagnosticResult(
            name="Stale Queue Claims",
            status="warn",
            message=f"{count} work queue item(s) claimed >30min with no heartbeat",
            fixable=True,
        )
    return DiagnosticResult(
        name="Stale Queue Claims",
        status="ok",
        message="No stale work queue claims",
    )


def run_diagnostics(store: GluonStore, log_path: Path) -> list[DiagnosticResult]:
    """Run all health checks and return results."""
    return [
        check_db_integrity(store),
        check_orphan_processes(store),
        check_stale_runs(store),
        check_log_disk_usage(log_path),
        check_stale_pending_questions(store),
        check_activity_log_size(store),
        check_stale_queue_claims(store),
    ]


# ========== Fix Functions ==========


def fix_orphan_processes(store: GluonStore) -> int:
    """Mark dead-PID runs as FAILED. Returns count fixed."""
    active_runs = store.list_active_runs()
    fixed = 0

    for run in active_runs:
        if run.status != RunStatus.RUNNING or not run.pid:
            continue
        try:
            os.kill(run.pid, 0)
        except ProcessLookupError:
            run.mark_failed("Process died unexpectedly (PID not found, detected by gluon doctor)")
            store.update_run(run)
            fixed += 1
            logger.info(f"Fixed orphan run {run.id[:8]} (PID {run.pid})")
        except PermissionError:
            pass

    return fixed


def fix_stale_runs(store: GluonStore) -> int:
    """Mark stale runs (>4h) as FAILED. Returns count fixed."""
    active_runs = store.list_active_runs()
    now = utc_now()
    fixed = 0

    for run in active_runs:
        if run.status != RunStatus.RUNNING or not run.started_at:
            continue
        age_hours = (now - run.started_at).total_seconds() / 3600
        if age_hours > 4:
            run.mark_failed(f"Stale run detected by gluon doctor (running for {age_hours:.1f} hours)")
            store.update_run(run)
            fixed += 1
            logger.info(f"Fixed stale run {run.id[:8]} (age: {age_hours:.1f}h)")

    return fixed


def fix_stale_pending_questions(store: GluonStore) -> int:
    """Expire stale pending questions. Returns count fixed."""
    now = utc_now()
    fixed = 0

    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT id, expires_at FROM pending_questions WHERE status = ?",
            (QuestionStatus.PENDING.value,),
        ).fetchall()

    for row in rows:
        if row["expires_at"]:
            from datetime import UTC, datetime

            expires = datetime.fromisoformat(row["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < now:
                with store._get_conn() as conn:
                    conn.execute(
                        "UPDATE pending_questions SET status = ? WHERE id = ?",
                        (QuestionStatus.EXPIRED.value, row["id"]),
                    )
                fixed += 1

    return fixed


def run_all_fixes(store: GluonStore) -> dict[str, int]:
    """Run all fixable checks. Returns dict of fix name -> count fixed."""
    return {
        "orphan_processes": fix_orphan_processes(store),
        "stale_runs": fix_stale_runs(store),
        "stale_questions": fix_stale_pending_questions(store),
    }
