"""Background polling and cleanup coroutines for the web app.

Extracted verbatim from ``web/api.py``'s ``create_app`` closure (#162). Each
coroutine takes its dependencies as explicit parameters instead of closing over
``create_app`` locals, so the API module no longer carries these long-running
loop bodies. Behavior is identical to the in-closure versions; the startup hook
in ``api.py`` launches each as an ``asyncio`` task with the same arguments.

The WebSocket manager is a module-level singleton
(``gluon.web.websocket.ws_manager``), so it is imported directly rather than
threaded through every signature — matching how ``api.py`` obtained it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from gluon.cleanup import LogCleanupService, WorktreeCleanupService
from gluon.web.websocket import ws_manager

if TYPE_CHECKING:
    from gluon.runner import TaskRunner
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


async def poll_run_status_changes(
    store: GluonStore,
    runner: TaskRunner,
    get_project_lookup: Callable[[], dict[str, str]],
    last_run_states: dict[str, str],
) -> None:
    """Background task to poll for run status changes and broadcast updates."""
    project_lookup = get_project_lookup()

    while True:
        try:
            # Refresh all running runs
            await asyncio.to_thread(runner.refresh_all_runs)

            # Check all non-archived runs for status changes
            runs = store.list_runs(limit=100, include_archived=False)
            runs = [r for r in runs if not r.archived]

            for run in runs:
                run_key = run.id
                # Track status AND pr_status for Kanban column changes
                current_state = f"{run.status.value}:{run.pr_status or 'none'}"

                # Check if state changed
                if run_key in last_run_states:
                    if last_run_states[run_key] != current_state:
                        # State changed - broadcast update
                        project_name = project_lookup.get(run.project_id, run.project_id[:8])
                        await ws_manager.broadcast_run_update(run, project_name)
                        logger.debug(
                            f"Broadcast run update: {run.id[:8]} {last_run_states[run_key]} -> {current_state}"
                        )

                last_run_states[run_key] = current_state

            # Clean up old run states (keep last 200)
            if len(last_run_states) > 200:
                # Keep only runs we just saw
                last_run_states.clear()
                for run in runs:
                    last_run_states[run.id] = f"{run.status.value}:{run.pr_status or 'none'}"

            # Refresh project lookup occasionally (new projects)
            project_lookup = get_project_lookup()

        except Exception as e:
            logger.error(f"Error in run status polling: {e}")

        # Poll every 2 seconds
        await asyncio.sleep(2)


async def poll_log_updates(
    runner: TaskRunner,
    log_file_positions: dict[str, int],
    progress_file_mtimes: dict[str, float],
    tokens_file_mtimes: dict[str, float],
) -> None:
    """Background task to poll log files for new content and stream to WebSocket subscribers.

    Only polls runs that have active WebSocket subscribers, minimizing I/O.
    Reads messages.jsonl incrementally and broadcasts new lines.
    Also checks progress.json and tokens.json for updates.
    """
    while True:
        try:
            # Only poll runs with active subscribers
            subscribed_runs = list(ws_manager.log_subscriptions.keys())

            for run_id in subscribed_runs:
                log_dir = runner.get_log_path(run_id)
                if not log_dir or not log_dir.exists():
                    continue

                # 1. Poll messages.jsonl for new agent messages
                messages_path = log_dir / "messages.jsonl"
                if messages_path.exists():
                    current_size = messages_path.stat().st_size

                    # Initialize position to current size for NEW subscriptions
                    # This prevents re-streaming messages that were already fetched via HTTP
                    # (fixes duplicate messages bug when resuming runs)
                    if run_id not in log_file_positions:
                        log_file_positions[run_id] = current_size
                        logger.debug(f"Initialized log position for {run_id[:8]} at {current_size} bytes")

                    last_pos = log_file_positions[run_id]

                    if current_size > last_pos:
                        try:
                            with open(messages_path) as f:
                                f.seek(last_pos)
                                for line in f:
                                    if line.strip():
                                        try:
                                            msg = json.loads(line)
                                            await ws_manager.stream_agent_message(run_id, msg)
                                        except json.JSONDecodeError:
                                            pass  # Skip malformed lines
                                log_file_positions[run_id] = f.tell()
                        except Exception as e:
                            logger.debug(f"Error reading messages.jsonl for {run_id[:8]}: {e}")

                # 2. Poll progress.json for progress updates
                progress_path = log_dir / "progress.json"
                if progress_path.exists():
                    try:
                        current_mtime = progress_path.stat().st_mtime
                        last_mtime = progress_file_mtimes.get(run_id, 0)

                        if current_mtime > last_mtime:
                            progress = json.loads(progress_path.read_text())
                            await ws_manager.stream_progress(
                                run_id,
                                turns=progress.get("turns", 0),
                                tool_calls=progress.get("tool_calls", 0),
                                elapsed_seconds=progress.get("elapsed_seconds", 0),
                            )
                            progress_file_mtimes[run_id] = current_mtime
                    except Exception as e:
                        logger.debug(f"Error reading progress.json for {run_id[:8]}: {e}")

                # 3. Poll tokens.json for token/cost updates
                tokens_path = log_dir / "tokens.json"
                if tokens_path.exists():
                    try:
                        current_mtime = tokens_path.stat().st_mtime
                        last_mtime = tokens_file_mtimes.get(run_id, 0)

                        if current_mtime > last_mtime:
                            tokens = json.loads(tokens_path.read_text())
                            await ws_manager.stream_token_update(
                                run_id,
                                input_tokens=tokens.get("input_tokens", 0),
                                output_tokens=tokens.get("output_tokens", 0),
                                estimated_cost_usd=tokens.get("estimated_cost_usd", 0),
                                context_used=tokens.get("context_used"),
                                context_window=tokens.get("context_window"),
                                cache_read=tokens.get("cache_read", 0),
                                cache_create=tokens.get("cache_create", 0),
                                model=tokens.get("model"),
                            )
                            tokens_file_mtimes[run_id] = current_mtime
                    except Exception as e:
                        logger.debug(f"Error reading tokens.json for {run_id[:8]}: {e}")

            # Cleanup tracking for unsubscribed runs
            active_subs = set(ws_manager.log_subscriptions.keys())
            for run_id in list(log_file_positions.keys()):
                if run_id not in active_subs:
                    log_file_positions.pop(run_id, None)
                    progress_file_mtimes.pop(run_id, None)
                    tokens_file_mtimes.pop(run_id, None)

        except Exception as e:
            logger.error(f"Error in log polling: {e}")

        # Poll every 100ms for responsive streaming
        await asyncio.sleep(0.1)


async def poll_pr_status_changes(
    store: GluonStore,
    runner: TaskRunner,
    get_project_lookup: Callable[[], dict[str, str]],
) -> None:
    """Background task to poll GitHub PR status, comments, and CI failures.

    Checks runs with open PRs every 60 seconds for:
    1. @gluon or /gluon comments -> auto-resume to address feedback
    2. CI failures (Vercel, build, deploy) -> auto-resume to fix issues
    3. Merged PRs -> transition to COMPLETED

    Supports both REVIEW and COMPLETED status runs with open PRs.
    """
    from gluon.git_manager import GitManager
    from gluon.models import RunStatus
    from gluon.pr_monitor import PRMonitorService

    pr_git_manager = GitManager(store)
    pr_monitor = PRMonitorService(store, runner, pr_git_manager)
    project_lookup = get_project_lookup()

    while True:
        try:
            # Find runs with open PRs (REVIEW or COMPLETED status)
            runs = store.list_runs(limit=100, include_archived=False)
            runs_with_open_prs = [r for r in runs if pr_monitor.should_monitor_run(r) and r.branch_name]

            for run in runs_with_open_prs:
                try:
                    project = store.get_project(run.project_id)
                    if not project:
                        continue

                    project_name = project_lookup.get(run.project_id, run.project_id[:8])

                    # 1. Check for new @gluon comments
                    triggered_comment = await pr_monitor.check_pr_comments(run)
                    if triggered_comment:
                        # Post "Addressing feedback..." comment
                        author = triggered_comment.get("author", "reviewer")
                        await pr_monitor.post_pr_comment(run, f"Addressing feedback from @{author}...")
                        # Auto-resume to address the comment
                        updated_run = await pr_monitor.auto_resume_for_comment(run, triggered_comment)
                        if updated_run:
                            await ws_manager.broadcast_run_update(updated_run, project_name)
                        continue

                    # 2. Poll CI check status and persist it
                    if run.git_commit_sha:
                        all_checks = await pr_git_manager.get_check_runs(project.expanded_path, run.git_commit_sha)
                        if all_checks:
                            has_pending = any(c.get("status") != "completed" for c in all_checks)
                            has_failure = any(
                                c.get("status") == "completed" and c.get("conclusion") in ("failure", "timed_out")
                                for c in all_checks
                            )
                            new_ci = "failure" if has_failure else ("pending" if has_pending else "success")
                        else:
                            new_ci = None
                        if new_ci != run.ci_status:
                            run.ci_status = new_ci
                            store.update_run(run)
                            await ws_manager.broadcast_run_update(run, project_name)

                    # 2b. Auto-resume on CI failures (existing behavior)
                    ci_failures = await pr_monitor.check_ci_failures(run)
                    if ci_failures:
                        failure_names = ", ".join(f.get("name", "unknown") for f in ci_failures[:3])
                        await pr_monitor.post_pr_comment(
                            run, f"Detected CI failures ({failure_names}). Investigating..."
                        )
                        updated_run = await pr_monitor.auto_resume_for_ci_failure(run, ci_failures)
                        if updated_run:
                            await ws_manager.broadcast_run_update(updated_run, project_name)
                        continue

                    # 3. Check if PR was merged (existing logic)
                    pr_info = await pr_git_manager._get_pr_info(project.expanded_path, run.branch_name)

                    if pr_info and pr_info.get("status") == "merged":
                        # PR was merged - transition to COMPLETED
                        logger.info(f"PR #{run.pr_number} merged - transitioning run {run.id[:8]} to COMPLETED")
                        run.pr_status = "merged"
                        run.status = RunStatus.COMPLETED
                        store.update_run(run)
                        await ws_manager.broadcast_run_update(run, project_name)

                    elif pr_info and pr_info.get("status") == "closed":
                        # PR was closed without merge - just update pr_status
                        if run.pr_status != "closed":
                            run.pr_status = "closed"
                            store.update_run(run)
                            await ws_manager.broadcast_run_update(run, project_name)

                except Exception as e:
                    logger.debug(f"Error checking PR for run {run.id[:8]}: {e}")

            # Refresh project lookup occasionally
            project_lookup = get_project_lookup()

        except Exception as e:
            logger.error(f"Error in PR status polling: {e}")

        # Poll every 60 seconds (GitHub API rate limiting consideration)
        await asyncio.sleep(60)


async def cleanup_old_logs(
    store: GluonStore,
    cleanup_initial_delay_seconds: int,
    cleanup_interval_seconds: int,
) -> None:
    """Background task to cleanup old log files based on retention policies.

    Runs after initial delay, then every 8 hours.
    - Archived runs: logs deleted 30 days after execution
    - Failed runs: logs deleted 7 days after execution
    - Orphan logs (no DB record): deleted immediately
    """
    cleanup_service = LogCleanupService(store=store)

    # Initial delay before first cleanup (300 seconds = 5 minutes)
    logger.info(
        f"Log cleanup scheduled: first run in {cleanup_initial_delay_seconds}s, "
        f"then every {cleanup_interval_seconds // 3600}h"
    )
    await asyncio.sleep(cleanup_initial_delay_seconds)

    while True:
        try:
            logger.info("Starting log cleanup...")
            stats = cleanup_service.cleanup()
            total = (
                stats["orphan_deleted"]
                + stats["archived_deleted"]
                + stats["failed_deleted"]
                + stats["completed_deleted"]
            )
            if total > 0 or stats["errors"] > 0:
                logger.info(
                    f"Log cleanup complete: {stats['orphan_deleted']} orphan, "
                    f"{stats['archived_deleted']} archived, "
                    f"{stats['failed_deleted']} failed, "
                    f"{stats['completed_deleted']} completed deleted, "
                    f"{stats['errors']} errors"
                )
            else:
                logger.info("Log cleanup complete: no logs to delete")
        except Exception as e:
            logger.error(f"Error in log cleanup task: {e}")

        # Wait for next cleanup cycle
        await asyncio.sleep(cleanup_interval_seconds)


async def cleanup_old_worktrees(
    store: GluonStore,
    cleanup_initial_delay_seconds: int,
    cleanup_interval_seconds: int,
) -> None:
    """Background task to garbage-collect stale Git worktrees.

    Runs alongside log cleanup on the same schedule.
    - Orphan worktrees (no DB record): deleted immediately
    - Merged PRs: deleted immediately
    - Completed/failed/cancelled runs: deleted after retention period
    """
    retention_setting = store.resolve_setting("worktree_retention_days")
    retention_days = int(retention_setting) if retention_setting else 7
    wt_service = WorktreeCleanupService(store=store, retention_days=retention_days)

    # Use same initial delay as log cleanup
    await asyncio.sleep(cleanup_initial_delay_seconds)

    while True:
        try:
            stats = wt_service.cleanup()
            total = stats["orphan_deleted"] + stats["merged_deleted"] + stats["expired_deleted"]
            if total > 0 or stats["errors"] > 0:
                freed_mb = stats["bytes_freed"] / (1024 * 1024)
                logger.info(
                    f"Worktree cleanup: {stats['orphan_deleted']} orphan, "
                    f"{stats['merged_deleted']} merged, "
                    f"{stats['expired_deleted']} expired deleted "
                    f"({freed_mb:.1f} MB freed, {stats['errors']} errors)"
                )
        except Exception as e:
            logger.error(f"Error in worktree cleanup task: {e}")

        await asyncio.sleep(cleanup_interval_seconds)


async def sweep_auth_state(store: GluonStore) -> None:
    """Sweep expired auth artifacts (D5).

    Two store-level helpers were added in Phases 1 and 4 but had no
    scheduler — without this task, expired ``user_sessions`` rows and
    unconsumed-but-expired ``link_codes`` rows accumulate forever.
    Both sweeps are cheap (single DELETE each), so we run them on a
    single shared cadence.

    Tunable via ``GLUON_AUTH_SWEEP_INTERVAL_SECS`` (default 1 hour).
    First pass runs after a short delay so we don't spike at startup
    alongside other heavy tasks.
    """
    sweep_interval = int(os.environ.get("GLUON_AUTH_SWEEP_INTERVAL_SECS", str(60 * 60)))
    await asyncio.sleep(60)  # short startup delay
    logger.info(f"Auth state sweep scheduled every {sweep_interval}s")
    while True:
        try:
            expired_sessions = store.delete_expired_user_sessions()
            expired_codes = store.delete_expired_link_codes()
            # TTL'd chat-bot tables had cleanup helpers but no scheduler —
            # without this they grow unbounded. Same cheap cadence.
            expired_chat = store.cleanup_expired_chat_history()
            expired_maps = store.cleanup_expired_message_run_maps()
            if expired_sessions or expired_codes or expired_chat or expired_maps:
                logger.info(
                    "Auth/TTL sweep: %d sessions, %d link codes, %d chat-history, %d message-run maps deleted",
                    expired_sessions,
                    expired_codes,
                    expired_chat,
                    expired_maps,
                )
        except Exception as e:
            logger.error(f"Error in auth/TTL sweep: {e}")
        await asyncio.sleep(sweep_interval)
