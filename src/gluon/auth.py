"""Authentication layer for Gluon Agent (D5 Phase 1).

Provides a pluggable auth provider abstraction mirroring the pattern from
`llm_provider.py`. Phase 1 ships the **local** provider (username + argon2
password). OIDC arrives in Phase 3.

Crucial invariant: when ``GLUON_AUTH_ENABLED`` is unset or false (the
default), none of this code runs in the request path. Single-user mode is
preserved by ``SYSTEM_USER`` — a singleton representing the "anyone with
access to the DB file" implicit user — which every existing CLI/web/runner
call site will be taught to resolve to in Phase 2.

See ``docs/plans/d5-multi-user-auth.md`` for the full design.
"""

from __future__ import annotations

import os
import secrets
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from gluon.models import AuthProvider as AuthProviderEnum
from gluon.models import User, UserRole, UserSession, utc_now

if TYPE_CHECKING:
    from gluon.store import GluonStore


# ---------------------------------------------------------------------------
# SYSTEM_USER singleton (single-user compatibility)
# ---------------------------------------------------------------------------

# Deterministic UUID so log lines and audit trails across runs reference the
# same identifier. Chosen as a readable zero-ish value to stand out in DB
# dumps if someone accidentally persists it.
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000"

SYSTEM_USER: User = User(
    id=SYSTEM_USER_ID,
    username="system",
    display_name="System",
    email=None,
    auth_provider=AuthProviderEnum.SYSTEM,
    auth_subject="system",
    role=UserRole.ADMIN,
    disabled=False,
)
"""The implicit user under ``GLUON_AUTH_ENABLED=false``.

Never persisted. Every call site that needs a "who did this" value in
single-user mode falls back to this object. Role is ``admin`` because in
single-user mode the operator holding the DB file has full authority —
nothing would be gained by pretending otherwise.
"""


def is_auth_enabled() -> bool:
    """Return True when ``GLUON_AUTH_ENABLED`` is truthy.

    Acceptable truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    Every other value — and unset — resolves to False.
    """
    raw = os.environ.get("GLUON_AUTH_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for auth-layer errors."""


class InvalidCredentialsError(AuthError):
    """Raised when a username/password pair doesn't match or the user is disabled.

    The message is deliberately generic — callers must not leak whether the
    username existed (prevents user enumeration).
    """


class UserDisabledError(AuthError):
    """Raised when a disabled user attempts to authenticate.

    Callers may want to surface this differently than InvalidCredentialsError
    (e.g. email the user); otherwise it's safe to flatten both to the same
    HTTP 401.
    """


class LinkCodeError(AuthError):
    """Raised when ``GluonStore.consume_link_code`` rejects a redemption.

    The caller (a chat command handler, typically) inspects ``reason`` to
    pick the right user-facing message. The reason strings are part of the
    public contract — see :meth:`GluonStore.consume_link_code` for the
    full enumeration.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AuthBackend(StrEnum):
    """Which `AuthProviderConfig` implementation to use."""

    LOCAL = "local"
    OIDC = "oidc"


# ---------------------------------------------------------------------------
# AuthProviderConfig ABC
# ---------------------------------------------------------------------------


class AuthProviderConfig(ABC):
    """Strategy interface for authentication backends.

    Each implementation answers exactly one question: *given some
    credentials (whatever shape), return the matching `User` or raise*.
    Session management (cookie signing, TTL rolling, rotation) is
    orthogonal and lives in its own layer.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short human-readable name for logs / docs."""

    @property
    @abstractmethod
    def provider(self) -> AuthProviderEnum:
        """Which `User.auth_provider` value this backend mints users with."""


class LocalAuthProvider(AuthProviderConfig):
    """Username + argon2 password authentication.

    argon2id with library defaults is the right starting point — memory and
    time costs are tuned to be expensive enough to discourage GPU/ASIC
    attacks without making the login latency noticeable (~100ms).
    """

    def __init__(self, store: GluonStore) -> None:
        self.store = store
        # Import inside __init__ so projects not using local auth don't pay
        # the argon2-cffi import cost on every startup.
        from argon2 import PasswordHasher  # type: ignore[import-untyped]

        self._hasher = PasswordHasher()

    @property
    def name(self) -> str:
        return "Local (username + argon2 password)"

    @property
    def provider(self) -> AuthProviderEnum:
        return AuthProviderEnum.LOCAL

    # ---- Password hashing ----

    def hash_password(self, password: str) -> str:
        """Hash a plaintext password. Returns an argon2-encoded string.

        The returned string includes the algorithm, parameters, salt, and
        hash — everything needed to verify later. Suitable to store as-is
        in ``User.auth_subject``.
        """
        if not password:
            raise ValueError("password must be non-empty")
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        return self._hasher.hash(password)

    def verify_password(self, hashed: str, password: str) -> bool:
        """Verify a password against a stored hash.

        Returns ``False`` on any mismatch or malformed hash. Never raises
        (wraps the argon2 exception types so callers can treat this as a
        boolean check).
        """
        from argon2.exceptions import InvalidHashError, VerifyMismatchError  # type: ignore[import-untyped]

        try:
            self._hasher.verify(hashed, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
        return True

    def check_rehash_needed(self, hashed: str) -> bool:
        """True if the stored hash uses older argon2 parameters.

        Call after a successful verify — if True, hash the password again
        and call ``store.update_user`` to upgrade silently. Follows the
        standard "passively upgrade" password storage pattern.
        """
        return self._hasher.check_needs_rehash(hashed)

    # ---- Authentication flow ----

    def authenticate(self, username: str, password: str) -> User:
        """Resolve a (username, password) pair to a `User` or raise.

        Raises:
          - InvalidCredentialsError — if the username doesn't exist OR the
            password doesn't match OR the user isn't a local-auth user.
            The caller cannot distinguish these — by design.
          - UserDisabledError — if the user exists and matches but is
            disabled.
        """
        user = self.store.get_user_by_username(username)
        if (
            user is None
            or user.auth_provider != AuthProviderEnum.LOCAL
            or not self.verify_password(user.auth_subject, password)
        ):
            # Deliberate: no distinction between "bad user" and "bad pass"
            # leaks out to the caller.
            raise InvalidCredentialsError("invalid credentials")
        if user.disabled:
            raise UserDisabledError("user is disabled")

        # Passive upgrade of legacy hashes.
        if self.check_rehash_needed(user.auth_subject):
            user.auth_subject = self.hash_password(password)

        user.last_login_at = utc_now()
        self.store.update_user(user)
        return user

    # ---- User creation ----

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        email: str | None = None,
        role: UserRole = UserRole.OPERATOR,
    ) -> User:
        """Create a user with a hashed password.

        ``display_name`` defaults to ``username``. Raises ``ValueError`` for
        short passwords and ``sqlite3.IntegrityError`` for duplicate
        usernames.
        """
        hashed = self.hash_password(password)
        return self.store.create_user(
            username=username,
            display_name=display_name or username,
            auth_subject=hashed,
            auth_provider="local",
            email=email,
            role=role,
        )

    def set_password(self, user: User, new_password: str) -> User:
        """Change a user's password. Also invalidates their active sessions.

        Returns the updated user.
        """
        user.auth_subject = self.hash_password(new_password)
        self.store.update_user(user)
        # Per the design doc §4.4, rotate all sessions on credential change.
        self.store.delete_user_sessions_for_user(user.id)
        return user


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

DEFAULT_SESSION_TTL_DAYS = 7


def create_session_for_user(
    store: GluonStore,
    user: User,
    *,
    ttl: timedelta = timedelta(days=DEFAULT_SESSION_TTL_DAYS),
    ip: str | None = None,
    user_agent: str | None = None,
) -> UserSession:
    """Mint a new `UserSession` for the given user.

    Session ID is a 128-bit random value (UUID4 from the model default).
    The caller is responsible for putting the ID into a signed cookie or
    equivalent transport.
    """
    expires_at = utc_now() + ttl
    return store.create_user_session(
        user_id=user.id,
        expires_at=expires_at,
        ip=ip,
        user_agent=user_agent,
    )


def resolve_session(
    store: GluonStore,
    session_id: str,
    *,
    now: datetime | None = None,
    ttl: timedelta = timedelta(days=DEFAULT_SESSION_TTL_DAYS),
) -> tuple[User, UserSession] | None:
    """Look up a session + its owning user, rolling the expiry forward on success.

    Returns ``None`` for unknown, expired, or disabled-owner sessions.
    On success returns the user and the session (with refreshed ``last_seen_at``
    and ``expires_at`` if rolling was needed).

    Rolling strategy: if the session is more than half-expired, push the
    new expiry out by ``ttl``. Cheap touches otherwise.
    """
    now = now or utc_now()
    session = store.get_user_session(session_id)
    if session is None or session.expires_at < now:
        return None

    user = store.get_user(session.user_id)
    if user is None or user.disabled:
        # User deleted or disabled — treat the session as invalid. Clean up
        # while we're here.
        store.delete_user_session(session_id)
        return None

    # Roll the expiry forward if we're past half-life
    half_life = session.created_at + (session.expires_at - session.created_at) / 2
    new_expires_at: datetime | None = None
    if now > half_life:
        new_expires_at = now + ttl
        session.expires_at = new_expires_at
    session.last_seen_at = now
    store.touch_user_session(session.id, new_expires_at=new_expires_at)

    return user, session


def new_session_secret() -> str:
    """Generate a fresh server-side session-signing secret.

    Stored in the ``settings`` table (``session_secret`` key) on first auth
    startup. Caller should only invoke when the setting is missing.
    """
    # 256-bit random — generous for HMAC-SHA256 signing.
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def get_auth_provider(
    store: GluonStore,
    backend: AuthBackend | str | None = None,
) -> AuthProviderConfig:
    """Resolve the active auth backend.

    Resolution order:
      1. Explicit ``backend`` argument
      2. ``GLUON_AUTH_BACKEND`` env var
      3. Default: ``local``
    """
    if backend is None:
        backend = os.environ.get("GLUON_AUTH_BACKEND", "local")

    if isinstance(backend, str):
        try:
            backend = AuthBackend(backend.lower())
        except ValueError:
            raise ValueError(
                f"Unknown auth backend: {backend}. Available: {', '.join(b.value for b in AuthBackend)}"
            ) from None

    if backend == AuthBackend.LOCAL:
        return LocalAuthProvider(store)
    if backend == AuthBackend.OIDC:
        raise NotImplementedError("OIDC auth arrives in D5 Phase 3. Use LOCAL in the meantime.")

    # Unreachable but explicit for mypy and future backends.
    raise ValueError(f"Unhandled auth backend: {backend}")


# ---------------------------------------------------------------------------
# Web / FastAPI integration helpers (D5 Phase 2)
#
# Everything below is only meaningful when GLUON_AUTH_ENABLED=true. When the
# flag is false, `get_current_user` returns SYSTEM_USER and `require_role` is
# a no-op — so existing endpoints keep working identically.
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "gluon_session"
"""Cookie name for the session ID. httpOnly + sameSite=lax + secure (in prod)."""


def _role_rank(role: UserRole) -> int:
    """Numeric rank so `require_role` can do >= comparisons.

    admin > operator > viewer. A user with a higher-ranked role passes any
    check that requires a lower-ranked role.
    """
    return {UserRole.ADMIN: 3, UserRole.OPERATOR: 2, UserRole.VIEWER: 1}[role]


def _current_user_impl(store: GluonStore, session_cookie: str | None) -> User:
    """Resolve the current user from a session cookie, raising on failure.

    - Auth disabled → returns SYSTEM_USER unconditionally
    - Auth enabled + no cookie → raises ``HTTPException(401)``
    - Auth enabled + invalid/expired cookie → raises ``HTTPException(401)``

    Kept as a plain function (not a FastAPI dependency) so tests + non-FastAPI
    callers (future CLI remote mode) can reuse it without pulling FastAPI in.
    """
    if not is_auth_enabled():
        return SYSTEM_USER

    from fastapi import HTTPException  # local import — only needed here

    if not session_cookie:
        raise HTTPException(status_code=401, detail="authentication required")

    result = resolve_session(store, session_cookie)
    if result is None:
        raise HTTPException(status_code=401, detail="session invalid or expired")
    user, _session = result
    return user


def make_current_user_dependency(store: GluonStore):
    """Build a FastAPI dependency that returns the current `User` for a request.

    Usage (inside `create_app`):
        current_user = make_current_user_dependency(store)

        @app.get("/api/something")
        async def something(user: User = Depends(current_user)):
            ...

    Factory shape matches the existing store/orchestrator wiring style in
    `create_app` — everything that depends on runtime state takes `store` as
    a closure.
    """
    from fastapi import Cookie, Depends  # noqa: F401 — used as FastAPI hints

    async def current_user(
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    ) -> User:
        return _current_user_impl(store, session)

    return current_user


def make_require_role(store: GluonStore, required: UserRole):
    """Build a FastAPI dependency that 403s if the current user's role is too low.

    Usage:
        require_admin = make_require_role(store, UserRole.ADMIN)

        @app.post("/api/users", dependencies=[Depends(require_admin)])
        async def create_user(...): ...

    When ``GLUON_AUTH_ENABLED=false`` the check is a no-op — SYSTEM_USER has
    admin role and passes every level.
    """
    from fastapi import Cookie, HTTPException

    async def role_check(
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    ) -> User:
        user = _current_user_impl(store, session)
        if _role_rank(user.role) < _role_rank(required):
            raise HTTPException(
                status_code=403,
                detail=f"role '{required.value}' required (you are '{user.role.value}')",
            )
        return user

    return role_check
