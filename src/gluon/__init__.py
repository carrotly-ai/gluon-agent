"""Gluon Agent - AI orchestrator for managing multiple Claude Code agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("gluon-agent")
except PackageNotFoundError:
    __version__ = "0+unknown"

from gluon.models import Project, Session, SessionStatus

__all__ = ["Project", "Session", "SessionStatus", "__version__"]
