"""Behavior-identity net for the merge-queue + witness API routes (#162).

Locks GET /api/merge-queue, the retry/cancel actions, and GET
/api/runs/{run_id}/witness BEFORE they move from create_app into dedicated
routers. All are store-only (retry/cancel only mutate DB status — the real git
merge is done by a worker elsewhere). Seeds via the shared temp_store.
"""

from __future__ import annotations

from gluon.models import (
    HealthClassification,
    MergeQueueEntry,
    MergeQueueStatus,
    RecoveryAction,
    WitnessDecision,
)
from gluon.store import GluonStore


def _entry(temp_store: GluonStore, status: MergeQueueStatus = MergeQueueStatus.PENDING) -> MergeQueueEntry:
    return temp_store.enqueue_merge(MergeQueueEntry(run_id="r1", project_id="p1", branch_name="feat/x", status=status))


# ---------------------------------------------------------------- merge queue


def test_list_merge_queue_empty(api_client):
    client, _ = api_client
    resp = client.get("/api/merge-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert body["total"] == 0


def test_list_merge_queue_with_data(api_client, temp_store: GluonStore):
    client, _ = api_client
    _entry(temp_store)
    resp = client.get("/api/merge-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["entries"][0]["branch_name"] == "feat/x"


def test_retry_merge_resets_to_pending(api_client, temp_store: GluonStore):
    client, _ = api_client
    entry = _entry(temp_store, status=MergeQueueStatus.FAILED)
    resp = client.post(f"/api/merge-queue/{entry.id}/retry")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == MergeQueueStatus.PENDING.value
    assert body["conflict_count"] == 0
    assert body["last_error"] is None


def test_retry_merge_wrong_status_400(api_client, temp_store: GluonStore):
    client, _ = api_client
    entry = _entry(temp_store, status=MergeQueueStatus.PENDING)
    resp = client.post(f"/api/merge-queue/{entry.id}/retry")
    assert resp.status_code == 400


def test_retry_merge_404(api_client):
    client, _ = api_client
    resp = client.post("/api/merge-queue/missing/retry")
    assert resp.status_code == 404


def test_cancel_merge(api_client, temp_store: GluonStore):
    client, _ = api_client
    entry = _entry(temp_store, status=MergeQueueStatus.PENDING)
    resp = client.post(f"/api/merge-queue/{entry.id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == MergeQueueStatus.CANCELLED.value


def test_cancel_merge_terminal_status_400(api_client, temp_store: GluonStore):
    client, _ = api_client
    entry = _entry(temp_store, status=MergeQueueStatus.MERGED)
    resp = client.post(f"/api/merge-queue/{entry.id}/cancel")
    assert resp.status_code == 400


def test_cancel_merge_404(api_client):
    client, _ = api_client
    resp = client.post("/api/merge-queue/missing/cancel")
    assert resp.status_code == 404


# ---------------------------------------------------------------- witness


def test_witness_decisions_empty(api_client):
    client, _ = api_client
    resp = client.get("/api/runs/unknown-run/witness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "unknown-run"
    assert body["decisions"] == []


def test_witness_decisions_with_data(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.record_witness_decision(
        WitnessDecision(
            run_id="r1",
            classification=HealthClassification.STUCK,
            confidence=0.9,
            reasoning="no progress",
            action=RecoveryAction.NUDGE,
        )
    )
    resp = client.get("/api/runs/r1/witness")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["decisions"]) == 1
    d = body["decisions"][0]
    assert d["classification"] == HealthClassification.STUCK.value
    assert d["action"] == RecoveryAction.NUDGE.value
    assert d["confidence"] == 0.9
