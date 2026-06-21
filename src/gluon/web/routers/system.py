"""System / meta routes (#162) — trivial stateless or store-only endpoints.

Holds the small system endpoints: overall status, LLM-provider info, the
liveness health probe, and the global slash-command listing. These are either
stateless or depend only on the store/orchestrator (injected via Depends).
``get_version`` stays inline (it memoizes into a create_app-scoped cache).
Paths unchanged → same fail-closed auth posture (``/api/health`` stays on the
anonymous allowlist, which is path-based).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from gluon.commands import get_slash_commands
from gluon.core import Orchestrator
from gluon.models import RunStatus
from gluon.store import GluonStore
from gluon.web.models import (
    AttentionCountsResponse,
    ProviderResponse,
    SlashCommandResponse,
    SlashCommandsResponse,
    StatusResponse,
)
from gluon.web.routers._deps import get_orchestrator, get_store

router = APIRouter(tags=["system"])

Store = Annotated[GluonStore, Depends(get_store)]
Orch = Annotated[Orchestrator, Depends(get_orchestrator)]


@router.get("/api/status", response_model=StatusResponse)
async def get_status(store: Store, orchestrator: Orch) -> StatusResponse:
    """Get overall system status."""
    projects = orchestrator.list_projects()
    active_runs = store.list_active_runs()
    all_runs = store.list_runs(limit=1000)  # Get count

    return StatusResponse(
        total_projects=len(projects),
        active_runs=len(active_runs),
        total_runs=len(all_runs),
    )


@router.get("/api/provider", response_model=ProviderResponse)
async def get_provider_info() -> ProviderResponse:
    """Get current LLM provider configuration and model mappings."""
    from gluon.llm_provider import get_provider, get_provider_source

    provider = get_provider()
    return ProviderResponse(
        provider=provider.__class__.__name__.replace("Provider", "").lower(),
        name=provider.name,
        supports_cost_tracking=provider.supports_cost_tracking,
        source=get_provider_source(),
        models={tier.value: model_id for tier, model_id in provider.MODELS.items()},
    )


@router.get("/api/health")
async def get_health() -> dict[str, str]:
    """Liveness probe for the container healthcheck.

    Intentionally a pure liveness check: a 200 means the event loop is
    responsive. It deliberately does NOT touch the DB or other subsystems —
    a readiness-style check here could flap and trigger restart loops on
    transient load. Reachable without auth (see the anonymous allowlist).
    """
    return {"status": "ok"}


@router.get("/api/commands", response_model=SlashCommandsResponse)
async def get_commands() -> SlashCommandsResponse:
    """Get available slash commands and skills from ~/.claude directories."""
    commands = get_slash_commands()
    return SlashCommandsResponse(
        commands=[
            SlashCommandResponse(
                name=cmd.name,
                type=cmd.type,
                description=cmd.description,
                argument_hint=cmd.argument_hint,
            )
            for cmd in commands
        ]
    )


@router.get("/api/attention-counts", response_model=AttentionCountsResponse)
async def get_attention_counts(store: Store) -> AttentionCountsResponse:
    """Aggregate counts of runs that need user attention.

    A run "needs attention" if it is FAILED, has a CONFLICTING PR, or has a
    pending question (``pending_questions.status = 'pending'``). Snoozed and
    archived runs are excluded.
    """
    runs = store.list_runs(limit=1000)
    try:
        pending_q_run_ids = store.list_run_ids_with_pending_questions()
    except Exception:
        pending_q_run_ids = set()

    needs_input = 0
    failed = 0
    conflicts = 0
    by_project: dict[str, int] = {}
    for run in runs:
        if run.archived or run.is_snoozed:
            continue
        attention = False
        if run.id in pending_q_run_ids:
            needs_input += 1
            attention = True
        if run.status == RunStatus.FAILED:
            failed += 1
            attention = True
        if run.pr_mergeable == "CONFLICTING":
            conflicts += 1
            attention = True
        if attention:
            by_project[run.project_id] = by_project.get(run.project_id, 0) + 1

    return AttentionCountsResponse(
        total=needs_input + failed + conflicts,
        needs_input=needs_input,
        failed=failed,
        conflicts=conflicts,
        by_project=by_project,
    )


@router.get("/api/sandbox/status")
async def get_sandbox_status(store: Store) -> dict:
    """Get sandbox availability and configuration.

    Returns information about OS-level sandboxing:
    - Linux: bubblewrap (bwrap)
    - macOS: sandbox-exec with Seatbelt profiles
    """
    import platform
    import shutil

    system = platform.system()

    # Check if sandbox runtime is available
    if system == "Linux":
        available = shutil.which("bwrap") is not None
        runtime = "bubblewrap"
    elif system == "Darwin":
        available = shutil.which("sandbox-exec") is not None
        runtime = "sandbox-exec"
    else:
        available = False
        runtime = None

    return {
        "available": available,
        "runtime": runtime,
        "enabled": store.get_setting("sandbox_enabled", "true") == "true",
        "platform": system,
    }
