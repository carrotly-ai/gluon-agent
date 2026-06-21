"""Approval-gate routes (Theme D1, #162).

The /api/approvals* routes depend only on the store and the current-user
dependency (grant/deny attribution) — injected via Depends. The
approval_to_dict serializer is lifted to module level (pure given a
PendingApproval). Paths unchanged → same fail-closed auth posture.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from gluon.auth import SYSTEM_USER
from gluon.models import ApprovalStatus, PendingApproval
from gluon.models import User as UserModel
from gluon.store import GluonStore
from gluon.web.routers._deps import get_current_user, get_store

router = APIRouter(tags=["approvals"])

Store = Annotated[GluonStore, Depends(get_store)]
CurrentUser = Annotated[UserModel, Depends(get_current_user)]


def approval_to_dict(approval: PendingApproval) -> dict[str, Any]:
    """Serialize a PendingApproval for API responses."""
    return {
        "id": approval.id,
        "run_id": approval.run_id,
        "tool_name": approval.tool_name,
        "tool_input": approval.tool_input,
        "tool_use_id": approval.tool_use_id,
        "classification_reason": approval.classification_reason,
        "status": approval.status.value,
        "decision_reason": approval.decision_reason,
        "decided_by": approval.decided_by,
        "created_at": approval.created_at.isoformat(),
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "timeout_at": approval.timeout_at.isoformat() if approval.timeout_at else None,
    }


@router.get("/api/approvals")
async def list_approvals_endpoint(
    store: Store,
    status: str | None = None,
    run_id: str | None = None,
    limit: int = Query(default=50, le=200),
) -> dict[str, Any]:
    """List pending approvals, optionally filtered by status or run_id.

    Common usage: GET /api/approvals?status=pending — shows what needs attention.
    """
    resolved_status: ApprovalStatus | None = None
    if status:
        try:
            resolved_status = ApprovalStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Must be one of {[s.value for s in ApprovalStatus]}",
            )
    approvals = store.list_approvals(run_id=run_id, status=resolved_status, limit=limit)
    return {
        "approvals": [approval_to_dict(a) for a in approvals],
        "total": len(approvals),
    }


@router.get("/api/approvals/{approval_id}")
async def get_approval_endpoint(approval_id: str, store: Store) -> dict[str, Any]:
    """Get detail for a single approval."""
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")
    return approval_to_dict(approval)


@router.post("/api/approvals/{approval_id}/grant")
async def grant_approval_endpoint(
    approval_id: str,
    store: Store,
    user: CurrentUser,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grant an approval — the waiting hook will unblock and allow the tool call.

    D5 Phase 2 attribution: the approval's ``decided_by_user_id`` is set to
    the current user's ID when auth is enabled. ``decided_by`` remains a
    free-form string like "web" for cross-surface compatibility.
    """
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Approval already {approval.status.value}",
        )
    reason = (body or {}).get("reason") if body else None
    decided_by = (body or {}).get("decided_by", "web") if body else "web"
    attribution_user_id = user.id if user.id != SYSTEM_USER.id else None
    updated = store.decide_approval(
        approval_id,
        status=ApprovalStatus.GRANTED,
        decided_by=decided_by,
        decided_by_user_id=attribution_user_id,
        decision_reason=reason,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Approval vanished")
    return approval_to_dict(updated)


@router.post("/api/approvals/{approval_id}/deny")
async def deny_approval_endpoint(
    approval_id: str,
    store: Store,
    user: CurrentUser,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deny an approval — the waiting hook will return `permissionDecision: deny`."""
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Approval already {approval.status.value}",
        )
    reason = (body or {}).get("reason") if body else None
    decided_by = (body or {}).get("decided_by", "web") if body else "web"
    attribution_user_id = user.id if user.id != SYSTEM_USER.id else None
    updated = store.decide_approval(
        approval_id,
        status=ApprovalStatus.DENIED,
        decided_by=decided_by,
        decided_by_user_id=attribution_user_id,
        decision_reason=reason,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Approval vanished")
    return approval_to_dict(updated)
