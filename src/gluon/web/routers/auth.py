"""Local-auth routes (#162) — login / logout / me / providers.

The local username+password flow plus the auth-mode probe. Session-cookie
handling, the user-enumeration-safe 401s, and the per-IP rate limit on login are
preserved byte-for-byte (the limiter is the per-app instance from app.state via
rate_limit_auth). The OIDC routes (oidc/login, oidc/callback) and the chat-link
routes stay INLINE in create_app — they're coupled to Authlib's app.state.oauth
cache and the conditional SessionMiddleware registration. providers' url_for
resolves the still-inline named ``oidc_login_endpoint``. Paths unchanged → same
fail-closed auth posture. Behaviour locked by test_api_auth.py.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from gluon.auth import (
    DEFAULT_SESSION_TTL_DAYS,
    SESSION_COOKIE_NAME,
    SYSTEM_USER,
    InvalidCredentialsError,
    UserDisabledError,
    create_session_for_user,
    get_auth_provider,
    get_local_provider,
    get_oidc_provider,
    is_auth_enabled,
)
from gluon.store import GluonStore
from gluon.web.models import (
    AuthProvidersResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    OIDCProviderInfo,
)
from gluon.web.routers._deps import get_store, rate_limit_auth, user_to_response

router = APIRouter(tags=["auth"])

Store = Annotated[GluonStore, Depends(get_store)]


@router.post("/api/auth/login", response_model=LoginResponse, dependencies=[Depends(rate_limit_auth)])
async def auth_login(
    body: LoginRequest,
    request: Request,
    response: Response,
    store: Store,
) -> LoginResponse:
    """Authenticate a username + password and set the session cookie.

    Returns 400 if auth is disabled (the endpoint exists so clients can
    detect the mode, but using it is meaningless in single-user).
    Returns 401 on bad credentials or a disabled user — the two are
    deliberately indistinguishable to callers (prevents user enumeration).
    """
    if not is_auth_enabled():
        raise HTTPException(
            status_code=400,
            detail=("GLUON_AUTH_ENABLED is false — login is a no-op. Use the system user or enable auth."),
        )
    provider = get_auth_provider(store)
    if not hasattr(provider, "authenticate"):
        raise HTTPException(status_code=500, detail="auth provider misconfigured")
    try:
        user = provider.authenticate(body.username, body.password)  # type: ignore[attr-defined]
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="invalid credentials") from None
    except UserDisabledError:
        raise HTTPException(status_code=401, detail="invalid credentials") from None

    session = create_session_for_user(
        store,
        user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    # httpOnly + sameSite=lax; Secure is set when served over HTTPS (mirrors
    # the OIDC callback) so the session cookie can't travel in cleartext.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.id,
        max_age=DEFAULT_SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return LoginResponse(user=user_to_response(user))


@router.post("/api/auth/logout")
async def auth_logout(
    response: Response,
    request: Request,
    store: Store,
) -> dict[str, bool]:
    """Clear the session cookie and delete the session from the store.

    Always succeeds — even with no valid session, we clear the cookie so
    state-mismatch scenarios don't lock users in a broken state.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        try:
            store.delete_user_session(session_id)
        except Exception:
            # Never fail logout — worst case the session expires naturally.
            pass
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/auth/me", response_model=MeResponse)
async def auth_me(request: Request, store: Store) -> MeResponse:
    """Return the current user.

    With auth disabled: always returns SYSTEM_USER.
    With auth enabled + no/invalid session: returns SYSTEM_USER with
    `auth_enabled=True` so the client knows to show a login prompt.
    With auth enabled + valid session: returns the real user.
    """
    auth_on = is_auth_enabled()
    if not auth_on:
        return MeResponse(user=user_to_response(SYSTEM_USER), auth_enabled=False)

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        # Tell client they're not logged in — use SYSTEM_USER payload as
        # a placeholder so the shape is uniform.
        return MeResponse(user=user_to_response(SYSTEM_USER), auth_enabled=True)
    from gluon.auth import resolve_session

    result = resolve_session(store, session_id)
    if result is None:
        return MeResponse(user=user_to_response(SYSTEM_USER), auth_enabled=True)
    user, _ = result
    return MeResponse(user=user_to_response(user), auth_enabled=True)


@router.get("/api/auth/providers", response_model=AuthProvidersResponse)
async def auth_providers_endpoint(request: Request, store: Store) -> AuthProvidersResponse:
    """Tell the login page which auth methods are available.

    - ``auth_enabled=false`` → caller is in single-user mode; no login UI.
    - ``local=true`` → render the username/password form.
    - ``oidc != null`` → render a "Sign in with {oidc.name}" button
      that POSTs to ``oidc.login_url`` (which 302s to the IdP).

    Local + OIDC can both be enabled simultaneously (typical pattern:
    OIDC for humans, a few service-account local users for automation).
    """
    if not is_auth_enabled():
        return AuthProvidersResponse(auth_enabled=False, local=False, oidc=None)

    local_enabled = get_local_provider(store) is not None
    oidc_provider = get_oidc_provider(store)
    oidc_info: OIDCProviderInfo | None = None
    if oidc_provider is not None:
        # Build a same-origin URL the browser can navigate to.
        login_url = str(request.url_for("oidc_login_endpoint"))
        oidc_info = OIDCProviderInfo(
            name=oidc_provider.config.provider_name,
            login_url=login_url,
        )
    return AuthProvidersResponse(
        auth_enabled=True,
        local=local_enabled,
        oidc=oidc_info,
    )
