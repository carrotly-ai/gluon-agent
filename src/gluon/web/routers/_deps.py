"""Shared FastAPI ``Depends`` providers for the extracted routers (#162).

``create_app`` stores its shared collaborators on ``app.state`` (see the
``app.state.<name> = ...`` block near the end of ``create_app``); these
providers read them back so per-domain routers can inject what they need via
``Depends`` instead of closing over ``create_app`` locals.

Keep the attribute names here in sync with those assignments.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from fastapi import Cookie, HTTPException, Request

from gluon.auth import SESSION_COOKIE_NAME, _current_user_impl, _role_rank
from gluon.core import Orchestrator
from gluon.models import ExecutionRun, Project, User, UserRole
from gluon.notifier import NotificationDispatcher
from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.models import RunResponse, WorkspaceResponse
from gluon.web.websocket import WebSocketManager


def get_store(request: Request) -> GluonStore:
    return cast(GluonStore, request.app.state.store)


def get_runner(request: Request) -> TaskRunner:
    return cast(TaskRunner, request.app.state.runner)


def get_orchestrator(request: Request) -> Orchestrator:
    return cast(Orchestrator, request.app.state.orchestrator)


def get_ws_manager(request: Request) -> WebSocketManager:
    return cast(WebSocketManager, request.app.state.ws_manager)


def get_notifier(request: Request) -> NotificationDispatcher:
    return cast(NotificationDispatcher, request.app.state.notifier)


def get_project_lookup(request: Request) -> Callable[[], dict[str, str]]:
    return cast("Callable[[], dict[str, str]]", request.app.state.get_project_lookup)


def get_run_to_response(request: Request) -> Callable[..., RunResponse]:
    return cast("Callable[..., RunResponse]", request.app.state.run_to_response)


def get_resolve_run_or_404(request: Request) -> Callable[[str], ExecutionRun]:
    return cast("Callable[[str], ExecutionRun]", request.app.state.resolve_run_or_404)


def get_resolve_project_or_404(request: Request) -> Callable[[str], Project]:
    return cast("Callable[[str], Project]", request.app.state.resolve_project_or_404)


def get_workspace_to_response(request: Request) -> Callable[..., WorkspaceResponse]:
    return cast("Callable[..., WorkspaceResponse]", request.app.state.workspace_to_response)


async def get_current_user(
    request: Request,
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> User:
    """Current-user dependency for extracted routers — mirrors create_app's
    ``current_user_dep`` (``make_current_user_dependency(store)``).

    Reads the store from app.state instead of closing over it; byte-identical
    behavior to ``_current_user_impl(store, session)`` (returns SYSTEM_USER in
    single-user mode / for an absent or invalid session).
    """
    store = cast(GluonStore, request.app.state.store)
    return _current_user_impl(store, session)


async def require_admin(
    request: Request,
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> User:
    """Admin-role gate for extracted routers — mirrors create_app's `require_admin`.

    Reads the store from app.state instead of closing over it; otherwise
    byte-identical to ``make_require_role(store, ADMIN)`` (no-op in single-user
    mode where SYSTEM_USER is admin, 403 with the same message otherwise).
    """
    store = cast(GluonStore, request.app.state.store)
    user = _current_user_impl(store, session)
    if _role_rank(user.role) < _role_rank(UserRole.ADMIN):
        raise HTTPException(
            status_code=403,
            detail=f"role '{UserRole.ADMIN.value}' required (you are '{user.role.value}')",
        )
    return user
