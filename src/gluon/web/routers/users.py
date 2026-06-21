"""User-management routes (#162) — admin-gated (D5 RBAC).

list/create/update/disable are admin-only (require_admin); change-password has
its own per-request rule (admins change anyone; others change only their own
password and must verify current_password). All auth logic is preserved
byte-for-byte from the inline versions. Behaviour locked by test_api_auth.py
(403/401 gating, password verification, session rotation). Paths unchanged →
same fail-closed auth posture.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.auth import get_auth_provider
from gluon.models import User as UserModel
from gluon.models import UserRole
from gluon.store import GluonStore
from gluon.web.models import (
    ChangePasswordRequest,
    CreateUserRequest,
    UpdateUserRequest,
    UserListResponse,
    UserResponse,
)
from gluon.web.routers._deps import get_current_user, get_store, require_admin, user_to_response

router = APIRouter(tags=["users"])

Store = Annotated[GluonStore, Depends(get_store)]


@router.get("/api/users", response_model=UserListResponse)
async def list_users_endpoint(
    store: Store,
    include_disabled: bool = False,
    _admin: UserModel = Depends(require_admin),
) -> UserListResponse:
    """List all users. Admin-only."""
    users = store.list_users(include_disabled=include_disabled)
    return UserListResponse(
        users=[user_to_response(u) for u in users],
        total=len(users),
    )


@router.post("/api/users", response_model=UserResponse)
async def create_user_endpoint(
    body: CreateUserRequest,
    store: Store,
    _admin: UserModel = Depends(require_admin),
) -> UserResponse:
    """Create a new user. Admin-only.

    Password must be at least 12 characters. Returns 409 if the username
    already exists.
    """
    provider = get_auth_provider(store)
    if not hasattr(provider, "create_user"):
        raise HTTPException(
            status_code=500,
            detail="current auth provider does not support user creation",
        )
    try:
        role_enum = UserRole(body.role.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"unknown role '{body.role}'; valid: {[r.value for r in UserRole]}",
        ) from None
    try:
        user = provider.create_user(  # type: ignore[attr-defined]
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            email=body.email,
            role=role_enum,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="username already exists") from None
        raise
    return user_to_response(user)


@router.patch("/api/users/{user_id}", response_model=UserResponse)
async def update_user_endpoint(
    user_id: str,
    body: UpdateUserRequest,
    store: Store,
    admin: UserModel = Depends(require_admin),  # noqa: ARG001
) -> UserResponse:
    """Update a user's profile fields. Admin-only.

    Any field left `None` in the request is unchanged. Role changes and
    `disabled=True` rotate the target user's active sessions.

    Chat-account binding (D5 Phase 4): `telegram_user_id` /
    `discord_user_id` accept either a positive integer to set the link,
    or `0` to clear it. We refuse to set a chat ID that is already
    bound to a different user (returns 409) — chat IDs must be unique
    per platform so the bot can resolve them unambiguously.
    """
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    needs_session_rotation = False
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.email is not None:
        user.email = body.email
    if body.role is not None and body.role.lower() != user.role.value:
        try:
            user.role = UserRole(body.role.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"unknown role '{body.role}'",
            ) from None
        needs_session_rotation = True
    if body.disabled is not None and body.disabled != user.disabled:
        user.disabled = body.disabled
        if body.disabled:
            needs_session_rotation = True

    # D5 Phase 4 — chat-account binding (admin pre-registration).
    # 0 is the "clear" sentinel; positive integers set the link.
    if body.telegram_user_id is not None:
        new_tg: int | None = body.telegram_user_id or None
        if new_tg is not None and new_tg != user.telegram_user_id:
            conflict = store.get_user_by_telegram_id(new_tg)
            if conflict is not None and conflict.id != user.id:
                raise HTTPException(
                    status_code=409,
                    detail=(f"telegram user {new_tg} is already bound to @{conflict.username}"),
                )
        user.telegram_user_id = new_tg
    if body.discord_user_id is not None:
        new_dc: int | None = body.discord_user_id or None
        if new_dc is not None and new_dc != user.discord_user_id:
            conflict = store.get_user_by_discord_id(new_dc)
            if conflict is not None and conflict.id != user.id:
                raise HTTPException(
                    status_code=409,
                    detail=(f"discord user {new_dc} is already bound to @{conflict.username}"),
                )
        user.discord_user_id = new_dc

    store.update_user(user)
    if needs_session_rotation:
        store.delete_user_sessions_for_user(user.id)
    return user_to_response(user)


@router.delete("/api/users/{user_id}", response_model=UserResponse)
async def disable_user_endpoint(
    user_id: str,
    store: Store,
    _admin: UserModel = Depends(require_admin),
) -> UserResponse:
    """Disable a user (soft delete). Admin-only. Rotates their sessions.

    We don't hard-delete users because all the D5 Phase 2 attribution
    links (``execution_runs.user_id``, ``orchestrator_tasks.created_by_user_id``,
    ``pending_approvals.decided_by_user_id``) would lose their target.
    Disable-and-preserve keeps the audit trail intact.
    """
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not user.disabled:
        user.disabled = True
        store.update_user(user)
        store.delete_user_sessions_for_user(user.id)
    return user_to_response(user)


@router.post("/api/users/{user_id}/password", response_model=UserResponse)
async def change_password_endpoint(
    user_id: str,
    body: ChangePasswordRequest,
    store: Store,
    current: Annotated[UserModel, Depends(get_current_user)],
) -> UserResponse:
    """Change a user's password.

    - Admins may change anyone's password without providing `current_password`.
    - Any other user may change only their own password AND must provide
      `current_password` which is verified against the stored hash.

    All sessions for the target user are rotated on success.
    """
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    provider = get_auth_provider(store)
    if not hasattr(provider, "set_password") or not hasattr(provider, "verify_password"):
        raise HTTPException(status_code=500, detail="auth provider misconfigured")

    is_admin = current.role == UserRole.ADMIN
    is_self = current.id == target.id

    if not is_admin:
        if not is_self:
            raise HTTPException(status_code=403, detail="can only change your own password")
        if not body.current_password:
            raise HTTPException(status_code=400, detail="current_password required")
        if not provider.verify_password(target.auth_subject, body.current_password):  # type: ignore[attr-defined]
            raise HTTPException(status_code=401, detail="current password is incorrect")

    try:
        provider.set_password(target, body.new_password)  # type: ignore[attr-defined]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return user_to_response(target)
