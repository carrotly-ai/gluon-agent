"""Settings + vercel-token routes (#162) — admin-gated.

GET/PUT /api/settings and POST /api/vercel/test. All gated by require_admin
(shared dependency reading store from app.state — byte-identical posture to the
inline ``dependencies=[Depends(require_admin)]``). The secret-redaction helper
(_redact_setting + _SECRET_KEY_MARKERS) moves here unchanged — GET /api/settings
must never round-trip secret-looking values. The vercel-token test passes the
token via env, not argv, exactly as before. Behaviour locked by
tests/test_api_settings.py. Paths unchanged → same fail-closed auth posture.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gluon.store import GluonStore
from gluon.web.routers._deps import get_store, require_admin

router = APIRouter(tags=["settings"])

Store = Annotated[GluonStore, Depends(get_store)]

_SECRET_KEY_MARKERS = ("secret", "token", "password", "passwd", "api_key")


def _redact_setting(key: str, value: str) -> str:
    """Mask secret-looking setting values so they are never returned to clients."""
    low = key.lower()
    if value and (any(m in low for m in _SECRET_KEY_MARKERS) or low.endswith("_key")):
        return "********"
    return value


@router.get("/api/settings", dependencies=[Depends(require_admin)])
async def get_all_settings(store: Store) -> dict[str, str]:
    """Get all settings as key-value pairs."""
    from gluon.llm_provider import get_provider

    settings = store.get_all_settings()
    # Never round-trip secret values to the client (even for admins) — show
    # only that a value is set. Covers e.g. github_webhook_secret, *_token.
    settings = {k: _redact_setting(k, v) for k, v in settings.items()}
    # Expose whether VERCEL_TOKEN is available from environment (without leaking the value)
    settings["_vercel_token_from_env"] = "true" if os.environ.get("VERCEL_TOKEN") else "false"

    # Expose resolved provider info (the actual provider may come from env var, not DB)
    provider = get_provider()
    settings["_llm_provider_name"] = provider.name
    settings["_llm_provider_supports_cost_tracking"] = str(provider.supports_cost_tracking).lower()
    return settings


@router.put("/api/settings/{key}", dependencies=[Depends(require_admin)])
async def update_setting(key: str, body: dict, store: Store) -> dict[str, str]:
    """Update a single setting value."""
    value = body.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="Missing 'value' in request body")
    store.set_setting(key, str(value))
    return {"key": key, "value": str(value)}


@router.post("/api/vercel/test", dependencies=[Depends(require_admin)])
async def test_vercel_token(body: dict) -> dict:
    """Test a Vercel API token by calling `vercel whoami`."""
    token = (body.get("token") or "").strip() or os.environ.get("VERCEL_TOKEN", "")
    if not token:
        raise HTTPException(status_code=400, detail="No token provided and VERCEL_TOKEN not set")

    try:
        # Pass the token via env, not argv, so it doesn't leak through
        # /proc/<pid>/cmdline or `ps`.
        result = await asyncio.to_thread(
            subprocess.run,
            ["vercel", "whoami"],
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "VERCEL_TOKEN": token},
        )
        if result.returncode == 0:
            return {"valid": True, "account": result.stdout.strip()}
        else:
            return {"valid": False, "error": result.stderr.strip() or "Invalid token"}
    except FileNotFoundError:
        return {"valid": False, "error": "Vercel CLI not installed"}
    except subprocess.TimeoutExpired:
        return {"valid": False, "error": "Request timed out"}
