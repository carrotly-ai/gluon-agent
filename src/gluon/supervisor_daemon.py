"""Supervisor daemon for CLI-based supervision.

Provides a long-running process that polls for REVIEW tasks and
auto-resumes based on supervision policies.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from gluon.resume_coordinator import ResumeCoordinator
from gluon.runner import TaskRunner
from gluon.store import GluonStore

# Default paths
DEFAULT_PID_FILE = Path.home() / ".gluon" / "supervisor.pid"
DEFAULT_LOG_FILE = Path.home() / ".gluon" / "supervisor.log"

logger = logging.getLogger(__name__)


def get_pid_file() -> Path:
    """Get PID file path."""
    return DEFAULT_PID_FILE


def get_log_file() -> Path:
    """Get log file path."""
    return DEFAULT_LOG_FILE


def is_running() -> tuple[bool, int | None]:
    """Check if supervisor daemon is running.

    Returns:
        Tuple of (is_running, pid)
    """
    pid_file = get_pid_file()
    if not pid_file.exists():
        return False, None

    try:
        pid = int(pid_file.read_text().strip())
        # Check if process is actually running
        os.kill(pid, 0)
        return True, pid
    except (ValueError, ProcessLookupError, PermissionError):
        # PID file exists but process is dead
        pid_file.unlink(missing_ok=True)
        return False, None


def write_pid_file() -> None:
    """Write current PID to file."""
    pid_file = get_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    """Remove PID file."""
    get_pid_file().unlink(missing_ok=True)


def setup_logging(log_file: Path | None = None) -> None:
    """Setup logging for daemon."""
    log_path = log_file or get_log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure file handler
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    # Also log to stderr for debugging
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root_logger.addHandler(stderr_handler)


async def run_supervisor(poll_interval: int = 30) -> None:
    """Run the supervision polling loop.

    Args:
        poll_interval: Seconds between polls
    """
    store = GluonStore()
    runner = TaskRunner(store=store)
    coordinator = ResumeCoordinator(store=store, runner=runner, poll_interval=poll_interval)

    # Setup signal handlers
    stop_event = asyncio.Event()

    def signal_handler(signum: int, frame) -> None:
        logger.info(f"Received signal {signum}, shutting down...")
        stop_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Write PID file
    write_pid_file()
    logger.info(f"Supervisor daemon started (PID: {os.getpid()}, interval: {poll_interval}s)")

    try:
        await coordinator.start()

        # Wait for stop signal
        await stop_event.wait()

    finally:
        await coordinator.stop()
        remove_pid_file()
        logger.info("Supervisor daemon stopped")


def main(poll_interval: int = 30) -> None:
    """Entry point for supervisor daemon."""
    setup_logging()

    # Check if already running
    running, pid = is_running()
    if running:
        logger.error(f"Supervisor already running (PID: {pid})")
        sys.exit(1)

    try:
        asyncio.run(run_supervisor(poll_interval))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Supervisor crashed: {e}", exc_info=True)
        remove_pid_file()
        sys.exit(1)


def stop_daemon() -> bool:
    """Stop the running supervisor daemon.

    Returns:
        True if stopped, False if not running
    """
    running, pid = is_running()
    if not running or pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait for process to exit
        for _ in range(50):  # 5 seconds max
            try:
                os.kill(pid, 0)
                asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.1))
            except ProcessLookupError:
                break
        remove_pid_file()
        return True
    except (ProcessLookupError, PermissionError):
        remove_pid_file()
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gluon Supervisor Daemon")
    parser.add_argument("--poll-interval", type=int, default=30, help="Poll interval in seconds")
    args = parser.parse_args()

    main(args.poll_interval)
