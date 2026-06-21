"""Usage-dashboard routes (#162).

All /api/usage* routes are read-only and depend only on the store — each calls
one aggregation method and maps the rows to a response model. Injected via
Depends(get_store). Paths unchanged → same fail-closed auth posture.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from gluon.store import GluonStore
from gluon.web.models import (
    DailyUsageResponse,
    LoopEffectivenessResponse,
    ProjectUsageResponse,
    RunUsageItemResponse,
    UsageSummaryResponse,
)
from gluon.web.routers._deps import get_store

router = APIRouter(tags=["usage"])

Store = Annotated[GluonStore, Depends(get_store)]


@router.get("/api/usage/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(store: Store) -> UsageSummaryResponse:
    """Get aggregated usage statistics for header display."""
    summary = store.get_usage_summary()
    return UsageSummaryResponse(**summary)


@router.get("/api/usage/by-project", response_model=list[ProjectUsageResponse])
async def get_usage_by_project(
    store: Store,
    since: str | None = None,
    until: str | None = None,
) -> list[ProjectUsageResponse]:
    """Get usage breakdown by project."""
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    data = store.get_usage_by_project(since=since_dt, until=until_dt)
    return [ProjectUsageResponse(**item) for item in data]


@router.get("/api/usage/effectiveness", response_model=LoopEffectivenessResponse)
async def get_loop_effectiveness(store: Store) -> LoopEffectivenessResponse:
    """Loop-effectiveness (I5): acceptance rate + cost-per-accepted-change,
    split by whether the work kind is objectively gateable. Read-only."""
    return LoopEffectivenessResponse(**store.get_loop_effectiveness())


@router.get("/api/usage/by-day", response_model=list[DailyUsageResponse])
async def get_usage_by_day(
    store: Store,
    since: str | None = None,
    until: str | None = None,
) -> list[DailyUsageResponse]:
    """Get daily usage for charts."""
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    data = store.get_usage_by_day(since=since_dt, until=until_dt)
    return [DailyUsageResponse(**item) for item in data]


@router.get("/api/usage/runs", response_model=list[RunUsageItemResponse])
async def get_usage_runs(
    store: Store,
    since: str | None = None,
    until: str | None = None,
    sort_by: str = "cost",
    sort_order: str = "desc",
    limit: int = 50,
) -> list[RunUsageItemResponse]:
    """Get runs with cost data for usage dashboard."""
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    data = store.get_usage_runs(
        since=since_dt,
        until=until_dt,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
    )
    return [RunUsageItemResponse(**item) for item in data]
