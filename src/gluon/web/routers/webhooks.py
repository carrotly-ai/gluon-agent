"""Webhook routes (#162).

POST /api/webhooks/github is unauthenticated by session — it is gated solely by
HMAC X-Hub-Signature-256 validation (and stays on the path-based anonymous
allowlist). The validation logic is preserved byte-for-byte. The webhook-config
CRUD (list/create/delete) is admin-gated via require_admin. Collaborators are
injected via Depends. Behaviour locked by tests/test_api_webhooks.py. Paths
unchanged → same fail-closed auth posture.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from gluon.runner import TaskRunner
from gluon.store import GluonStore
from gluon.web.routers._deps import get_runner, get_store, get_ws_manager, require_admin
from gluon.web.websocket import WebSocketManager

router = APIRouter(tags=["webhooks"])

Store = Annotated[GluonStore, Depends(get_store)]
Runner = Annotated[TaskRunner, Depends(get_runner)]
WsManager = Annotated[WebSocketManager, Depends(get_ws_manager)]


@router.post("/api/webhooks/github")
async def handle_github_webhook(request: Request, store: Store, runner: Runner, ws_manager: WsManager) -> dict:
    """
    Handle GitHub webhook events.

    Validates webhook signature and creates runs for supported events:
    - push: Review pushed commits
    - pull_request: Review PR (opened, synchronize, reopened)
    - issues: Analyze new issues
    - issue_comment: Handle /gluon commands in comments
    - pull_request_review: Address requested changes

    Requires X-Hub-Signature-256 header for signature validation.
    """
    import os

    from gluon.webhooks.github import GitHubWebhookHandler

    # Get webhook secret from environment or database
    webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not webhook_secret:
        # Try to get from settings
        webhook_secret = store.get_setting("github_webhook_secret")

    if not webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="GitHub webhook secret not configured. Set GITHUB_WEBHOOK_SECRET env var.",
        )

    # Get signature and event type from headers
    signature = request.headers.get("X-Hub-Signature-256", "")
    event_type = request.headers.get("X-GitHub-Event", "")

    if not signature:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    if not event_type:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    # Read raw body for signature validation
    payload_bytes = await request.body()

    # Validate signature
    handler = GitHubWebhookHandler(secret=webhook_secret)
    is_valid = await handler.validate_signature(payload_bytes, signature)

    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse payload
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Parse event into WebhookEvent
    event = await handler.parse_event(event_type, payload)

    if event is None:
        # Event type not supported or filtered out
        return {
            "status": "ignored",
            "reason": f"Event type '{event_type}' not processed or filtered",
        }

    # Resolve project by repository name
    project = store.get_project_by_name(event.project_hint)
    if not project:
        # Try to find by partial match (e.g., 'my-app' matches 'my-app-backend')
        projects = store.list_projects()
        for p in projects:
            if event.project_hint.lower() in p.name.lower():
                project = p
                break

    if not project:
        return {
            "status": "skipped",
            "reason": f"No project found matching '{event.project_hint}'",
        }

    # Check webhook config for this project
    configs = store.get_webhook_configs_for_handler("github")
    matching_config = None
    for config in configs:
        if config.project_id == project.id or config.project_id is None:
            # Check event type filter
            if config.matches_event(event.event_type):
                # Check branch filter
                if event.source_ref and not config.matches_branch(event.source_ref):
                    continue
                matching_config = config
                break

    if not matching_config:
        return {
            "status": "skipped",
            "reason": f"No webhook config matches event for project '{project.name}'",
        }

    # Use custom prompt template if configured
    prompt = event.prompt
    if matching_config.prompt_template:
        prompt = handler.generate_prompt(event_type, payload, matching_config.prompt_template)

    # Create and queue the run
    run = await runner.submit(
        project_id=project.id,
        prompt=prompt,
        wait=False,
        use_worktree=True,  # Webhooks default to worktree isolation
        initiator=f"webhook:github:{event.event_type}",
        model=None,  # Use default model
    )

    # Broadcast to WebSocket clients
    await ws_manager.broadcast_run_created(run, project.name)

    return {
        "status": "queued",
        "run_id": run.id,
        "project": project.name,
        "event_type": event.event_type,
        "prompt": prompt[:200] + "..." if len(prompt) > 200 else prompt,
    }


@router.get("/api/webhooks", dependencies=[Depends(require_admin)])
async def list_webhooks(store: Store) -> list[dict]:
    """List all configured webhooks."""
    configs = store.list_webhook_configs(enabled_only=False)
    return [
        {
            "id": c.id,
            "handler": c.handler,
            "project_id": c.project_id,
            "events": c.events,
            "enabled": c.enabled,
            "created_at": c.created_at.isoformat(),
        }
        for c in configs
    ]


@router.post("/api/webhooks", dependencies=[Depends(require_admin)])
async def create_webhook(body: dict, store: Store) -> dict:
    """Create a new webhook configuration."""
    import secrets

    from gluon.models import WebhookConfig

    handler = body.get("handler", "github")
    project_id = body.get("project_id")
    events = body.get("events", [])
    prompt_template = body.get("prompt_template")
    branches = body.get("branches")
    ignore_branches = body.get("ignore_branches")

    # Generate a secret if not provided
    secret_key = body.get("secret_key") or secrets.token_hex(32)

    config = WebhookConfig(
        handler=handler,
        project_id=project_id,
        secret_key=secret_key,
        events=events,
        prompt_template=prompt_template,
        branches=branches,
        ignore_branches=ignore_branches,
    )

    store.create_webhook_config(config)

    return {
        "id": config.id,
        "handler": config.handler,
        "secret_key": secret_key,  # Return so user can configure in GitHub
        "message": "Webhook created. Configure this secret in GitHub webhook settings.",
    }


@router.delete("/api/webhooks/{webhook_id}", dependencies=[Depends(require_admin)])
async def delete_webhook(webhook_id: str, store: Store) -> dict:
    """Delete a webhook configuration."""
    config = store.get_webhook_config(webhook_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Webhook not found: {webhook_id}")

    success = store.delete_webhook_config(webhook_id)
    return {"deleted": success, "webhook_id": webhook_id}
