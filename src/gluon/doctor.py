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
        details=[f"Run {r.id[:8]} started {r.started_at.strftime('%Y-%m-%d %H:%M')}" for r in stale if r.started_at],
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


def check_claude_session_disk_usage(store: GluonStore) -> DiagnosticResult:
    """Warn when Claude SDK session JSONL files consume >1 GB.

    Part of Theme C5. Sessions accumulate with every fork; long-lived
    projects can hit double-digit GB if nothing cleans up. The `gluon
    sessions-cleanup` command drains orphans safely.
    """
    try:
        from claude_agent_sdk import list_sessions
    except ImportError:
        return DiagnosticResult(
            name="Claude Session Disk Usage",
            status="ok",
            message="SDK session inspection unavailable",
        )

    try:
        sessions = list_sessions(limit=100000)
    except Exception:
        return DiagnosticResult(
            name="Claude Session Disk Usage",
            status="ok",
            message="Session store unreadable (may be empty)",
        )

    total_bytes = sum(int(getattr(s, "file_size", 0) or 0) for s in sessions)
    count = len(sessions)

    def _fmt(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
            n //= 1024
        return f"{n} B"

    if total_bytes > 1024**3:  # >1 GB
        return DiagnosticResult(
            name="Claude Session Disk Usage",
            status="warn",
            message=(f"{count:,} sessions consuming {_fmt(total_bytes)}. Consider running 'gluon sessions-cleanup'."),
            fixable=True,
        )
    return DiagnosticResult(
        name="Claude Session Disk Usage",
        status="ok",
        message=f"{count:,} sessions, {_fmt(total_bytes)}",
    )


def check_llm_provider_config(_store: GluonStore) -> DiagnosticResult:
    """Verify the active LLM provider's required env vars are set.

    Each of the four providers reads a different set of credentials — this
    check surfaces missing config early (before a run fails at inference
    time) and suggests the fix.
    """
    try:
        from gluon.llm_provider import get_provider, get_provider_source
    except Exception as e:  # pragma: no cover — import-time failure
        return DiagnosticResult(
            name="LLM Provider Config",
            status="error",
            message=f"Could not import LLM provider abstraction: {e}",
        )

    try:
        provider = get_provider()
    except Exception as e:
        return DiagnosticResult(
            name="LLM Provider Config",
            status="error",
            message=f"Unknown provider configured: {e}. Run `gluon provider <name>` to fix.",
        )

    provider_key = provider.__class__.__name__.replace("Provider", "").lower()
    source = get_provider_source()

    missing: list[str] = []
    hints: list[str] = []

    if provider_key == "bedrock":
        if not os.environ.get("AWS_REGION"):
            missing.append("AWS_REGION")
        has_creds = any(
            os.environ.get(k)
            for k in (
                "AWS_BEARER_TOKEN_BEDROCK",
                "AWS_ACCESS_KEY_ID",
                "AWS_PROFILE",
                "AWS_SESSION_TOKEN",
            )
        )
        if not has_creds and not Path.home().joinpath(".aws", "credentials").exists():
            missing.append("AWS credentials (bearer token, access keys, or ~/.aws/credentials)")
    elif provider_key == "anthropic":
        # Valid if either the API key is set OR the user has a Claude CLI login
        # (~/.claude). We can't verify the login from here, so we treat missing
        # API key as a *warn* rather than *error*.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            hints.append("ANTHROPIC_API_KEY not set; relying on `claude login` session at ~/.claude")
    elif provider_key == "vertex":
        if not os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"):
            missing.append("ANTHROPIC_VERTEX_PROJECT_ID")
        if not os.environ.get("CLOUD_ML_REGION"):
            hints.append("CLOUD_ML_REGION not set (defaulting to 'global')")
        adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        has_adc = adc_path.exists() or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not has_adc:
            missing.append(
                "GCP credentials (run `gcloud auth application-default login` or set GOOGLE_APPLICATION_CREDENTIALS)"
            )
    elif provider_key == "foundry":
        has_endpoint = os.environ.get("ANTHROPIC_FOUNDRY_RESOURCE") or os.environ.get("ANTHROPIC_FOUNDRY_BASE_URL")
        if not has_endpoint:
            missing.append("ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_FOUNDRY_BASE_URL")
        has_api_key = bool(os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"))
        azure_dir = Path.home() / ".azure"
        has_entra = azure_dir.exists() or os.environ.get("AZURE_CLIENT_ID")
        if not has_api_key and not has_entra:
            missing.append("Azure credentials (set ANTHROPIC_FOUNDRY_API_KEY or run `az login`)")

    if missing:
        return DiagnosticResult(
            name="LLM Provider Config",
            status="error",
            message=f"{provider.name} (source: {source}) is missing required config",
            details=[f"Missing: {m}" for m in missing] + hints,
        )
    if hints:
        return DiagnosticResult(
            name="LLM Provider Config",
            status="warn",
            message=f"{provider.name} (source: {source}) configured with warnings",
            details=hints,
        )
    return DiagnosticResult(
        name="LLM Provider Config",
        status="ok",
        message=f"{provider.name} (source: {source})",
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
        check_claude_session_disk_usage(store),
        check_llm_provider_config(store),
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
