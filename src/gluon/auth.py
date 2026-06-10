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


_TRUTHY = {"1", "true", "yes", "on"}


def is_auth_enabled() -> bool:
    """Return True when ``GLUON_AUTH_ENABLED`` is truthy.

    Acceptable truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    Every other value — and unset — resolves to False.
    """
    raw = os.environ.get("GLUON_AUTH_ENABLED", "").strip().lower()
    return raw in _TRUTHY


# Hosts whose bind only exposes the local machine. An empty string is treated as
# loopback because uvicorn/socket-activation pass "" to mean localhost.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


def is_loopback_host(host: str) -> bool:
    """True when binding ``host`` only exposes the loopback interface."""
    h = host.strip().lower()
    if h in _LOOPBACK_HOSTS:
        return True
    # Any 127.0.0.0/8 address is loopback.
    return h.startswith("127.")


def insecure_bind_error(host: str) -> str | None:
    """Guard against silently serving an unauthenticated dashboard to the network.

    Returns an explanatory error message when binding ``host`` would expose a
    Gluon server with authentication **disabled** to a non-loopback interface,
    unless the operator has explicitly opted in with ``GLUON_INSECURE_OK=1``.
    Returns ``None`` when the bind is safe (loopback, or auth enabled, or the
    override is set) and the caller should proceed.
    """
    if is_loopback_host(host):
        return None
    if is_auth_enabled():
        return None
    if os.environ.get("GLUON_INSECURE_OK", "").strip().lower() in _TRUTHY:
        return None
    return (
        f"Refusing to bind {host!r} with authentication disabled — this would expose an "
        "unauthenticated Gluon dashboard (full agent control) to the network.\n"
        "Fix one of:\n"
        "  • set GLUON_AUTH_ENABLED=true and create a user (gluon user add …), or\n"
        "  • bind loopback only (--host 127.0.0.1), or\n"
        "  • if this is intentional (e.g. behind your own auth proxy), set GLUON_INSECURE_OK=1."
    )


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
    """Resolve the active auth backend (legacy single-provider helper).

    Resolution order:
      1. Explicit ``backend`` argument
      2. ``GLUON_AUTH_BACKEND`` env var
      3. Default: ``local``

    For Phase 3, prefer :func:`get_local_provider` and :func:`get_oidc_provider`
    directly — they let local + OIDC coexist (each user is bound to one
    provider via their ``auth_provider`` column). This function still works
    and is used by the CLI and password-only endpoints.
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
        provider = get_oidc_provider(store)
        if provider is None:
            raise ValueError(
                "GLUON_AUTH_BACKEND=oidc but OIDC env vars are unset. "
                "Configure GLUON_OIDC_ISSUER, GLUON_OIDC_CLIENT_ID, "
                "GLUON_OIDC_CLIENT_SECRET, GLUON_OIDC_REDIRECT_URI."
            )
        return provider

    # Unreachable but explicit for mypy and future backends.
    raise ValueError(f"Unhandled auth backend: {backend}")


def get_local_provider(store: GluonStore) -> LocalAuthProvider | None:
    """Return the local (password) provider iff local auth is enabled.

    Local is enabled by default (single-user installs use it; the CLI
    ``gluon user add`` always works regardless). Set
    ``GLUON_LOCAL_AUTH_ENABLED=false`` to turn the password endpoint off
    entirely — useful when you want OIDC-only mode and don't want a
    fallback password login.
    """
    if os.environ.get("GLUON_LOCAL_AUTH_ENABLED", "true").lower() in ("0", "false", "no"):
        return None
    return LocalAuthProvider(store)


# ---------------------------------------------------------------------------
# OIDC provider (D5 Phase 3)
# ---------------------------------------------------------------------------


class OIDCConfig:
    """Snapshot of OIDC env vars resolved at startup.

    Keeping this on a separate object makes the intent explicit and lets
    tests construct one without mutating ``os.environ``. None of the values
    here are secret in the Pydantic-model sense — the secret stays as an
    attribute, never serialized.
    """

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        provider_name: str = "OIDC",
        scopes: str = "openid profile email",
        auto_provision: bool = False,
        domain_allowlist: tuple[str, ...] = (),
        default_role: UserRole = UserRole.VIEWER,
    ) -> None:
        if auto_provision and not domain_allowlist:
            raise ValueError(
                "GLUON_OIDC_AUTO_PROVISION=true requires GLUON_OIDC_DOMAIN_ALLOWLIST "
                "to be set — auto-provisioning without an email-domain guard would let "
                "any Google/Auth0/etc. user create a Gluon account."
            )
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.provider_name = provider_name
        self.scopes = scopes
        self.auto_provision = auto_provision
        self.domain_allowlist = domain_allowlist
        self.default_role = default_role

    @classmethod
    def from_env(cls) -> OIDCConfig | None:
        """Build a config from env vars, returning ``None`` if not configured.

        Required env vars: ``GLUON_OIDC_ISSUER``, ``GLUON_OIDC_CLIENT_ID``,
        ``GLUON_OIDC_CLIENT_SECRET``, ``GLUON_OIDC_REDIRECT_URI``. If any
        is missing, OIDC is treated as not-configured (graceful degradation).
        """
        issuer = os.environ.get("GLUON_OIDC_ISSUER")
        client_id = os.environ.get("GLUON_OIDC_CLIENT_ID")
        client_secret = os.environ.get("GLUON_OIDC_CLIENT_SECRET")
        redirect_uri = os.environ.get("GLUON_OIDC_REDIRECT_URI")
        if not (issuer and client_id and client_secret and redirect_uri):
            return None

        provider_name = os.environ.get("GLUON_OIDC_PROVIDER_NAME", "OIDC")
        scopes = os.environ.get("GLUON_OIDC_SCOPES", "openid profile email")
        auto_provision = os.environ.get("GLUON_OIDC_AUTO_PROVISION", "false").lower() in ("1", "true", "yes")
        raw_allowlist = os.environ.get("GLUON_OIDC_DOMAIN_ALLOWLIST", "")
        domain_allowlist = tuple(d.strip().lower() for d in raw_allowlist.split(",") if d.strip())
        default_role_str = os.environ.get("GLUON_OIDC_DEFAULT_ROLE", "viewer")
        try:
            default_role = UserRole(default_role_str.lower())
        except ValueError:
            raise ValueError(
                f"GLUON_OIDC_DEFAULT_ROLE={default_role_str!r} not a valid role; "
                f"use one of {[r.value for r in UserRole]}"
            ) from None

        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            provider_name=provider_name,
            scopes=scopes,
            auto_provision=auto_provision,
            domain_allowlist=domain_allowlist,
            default_role=default_role,
        )


class OIDCAuthProvider(AuthProviderConfig):
    """OpenID Connect authentication via Authlib.

    Wraps Authlib's discovery-driven OAuth client. The provider's metadata
    is fetched lazily from ``{issuer}/.well-known/openid-configuration`` on
    first use; ID tokens are validated against the provider's JWKS.

    The actual redirect/callback flow lives in the web layer (api.py) — this
    class is just a thin holder that exposes ``resolve_or_provision(claims)``,
    keeping the user-lookup-or-create policy in one place so the endpoint
    code stays small.
    """

    def __init__(self, store: GluonStore, config: OIDCConfig) -> None:
        self.store = store
        self.config = config

    @property
    def name(self) -> str:
        return f"OIDC ({self.config.provider_name})"

    @property
    def provider(self) -> AuthProviderEnum:
        return AuthProviderEnum.OIDC

    def email_in_allowlist(self, email: str | None) -> bool:
        """Apply the domain allowlist if one is configured.

        Returns True when allowlist is empty (i.e. no restriction).
        Returns False when ``email`` is None — we never auto-provision
        without an email claim.
        """
        if not self.config.domain_allowlist:
            return True
        if not email or "@" not in email:
            return False
        domain = email.rsplit("@", 1)[-1].lower()
        return domain in self.config.domain_allowlist

    def resolve_or_provision(
        self,
        *,
        sub: str,
        email: str | None,
        display_name: str | None,
    ) -> User:
        """Map an OIDC ID-token to a Gluon ``User``.

        Lookup order:
          1. ``(provider=oidc, auth_subject=sub)`` — exact match by the
             issuer-stable subject claim. Most stable since ``sub`` never
             changes for a given user even if their email does.
          2. ``(provider=oidc, auth_subject=email)`` — convention used for
             pre-registered users created by
             ``gluon user add --auth-provider oidc --email alice@…``. The
             admin doesn't know the sub at registration time, so we use
             email as a placeholder. On first login we swap it for the
             real ``sub`` so step 1 wins next time.
          3. Auto-provision (only when ``config.auto_provision`` AND the
             email passes ``email_in_allowlist``). Creates a new User with
             ``config.default_role``.

        Raises:
            InvalidCredentialsError: no match and auto-provision disabled
                or domain blocked. Message is generic — never discloses
                whether the email exists in the allowlist.
            UserDisabledError: matched user is disabled.
        """
        # Step 1: exact sub match
        user = self.store.get_user_by_auth_subject(AuthProviderEnum.OIDC.value, sub)

        # Step 2: pre-registration by email-as-placeholder-subject
        if user is None and email:
            candidate = self.store.get_user_by_auth_subject(AuthProviderEnum.OIDC.value, email)
            if candidate is not None:
                # Swap the placeholder email for the real sub. From now on
                # step 1 hits directly. UNIQUE(auth_provider, auth_subject)
                # is preserved because we're updating the only existing row.
                candidate.auth_subject = sub
                self.store.update_user(candidate)
                user = candidate

        # Step 3: auto-provision (opt-in + allowlist required)
        if user is None:
            if not self.config.auto_provision:
                raise InvalidCredentialsError(
                    "no Gluon user matches this OIDC identity. Ask an admin to register you with `gluon user add`."
                )
            if not self.email_in_allowlist(email):
                raise InvalidCredentialsError("your email domain is not on the auto-provision allowlist.")
            assert email is not None  # email_in_allowlist enforces this
            username = email.split("@", 1)[0]
            # Username must be unique — append a digit suffix on collision.
            base_username = username
            n = 1
            while self.store.get_user_by_username(username) is not None:
                n += 1
                username = f"{base_username}{n}"
            user = self.store.create_user(
                username=username,
                display_name=display_name or email,
                email=email,
                auth_provider=AuthProviderEnum.OIDC.value,
                auth_subject=sub,
                role=self.config.default_role,
            )

        if user.disabled:
            raise UserDisabledError("user account is disabled")

        # Touch last_login_at — same as LocalAuthProvider does on success.
        user.last_login_at = utc_now()
        self.store.update_user(user)
        return user


def get_oidc_provider(store: GluonStore) -> OIDCAuthProvider | None:
    """Return an OIDC provider iff env vars are configured AND auth is enabled.

    Returns ``None`` for any of:
      - ``GLUON_AUTH_ENABLED=false`` (no auth at all)
      - One of the four required OIDC env vars is missing
      - Config validation failed (e.g. auto-provision without allowlist)

    Callers can use the ``None`` return to feature-detect: web layer renders
    or omits the "Sign in with X" button accordingly.
    """
    if not is_auth_enabled():
        return None
    config = OIDCConfig.from_env()
    if config is None:
        return None
    return OIDCAuthProvider(store, config)


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
