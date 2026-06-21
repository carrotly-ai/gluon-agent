"""Behavior-identity net for the activity + work-queue API routes (#162).

These routes were previously un-netted; this locks their behavior BEFORE they
move from create_app into dedicated routers, so the extraction is provably
behavior-identical. Seeds via the shared temp_store (same instance api_client
is built from).
"""

from __future__ import annotations

from gluon.models import WorkQueueStatus
from gluon.store import GluonStore

# ---------------------------------------------------------------- activity


def test_list_activity_empty(api_client):
    client, _ = api_client
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["events"] == []
    assert body["total"] == 0


def test_list_activity_with_data(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.log_activity(actor="alice", action="run.create", result="ok", message="hi")
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    e = body["events"][0]
    assert e["actor"] == "alice"
    assert e["action"] == "run.create"
    assert e["result"] == "ok"
    assert e["message"] == "hi"


def test_list_activity_filter_by_actor(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.log_activity(actor="alice", action="a")
    temp_store.log_activity(actor="bob", action="b")
    resp = client.get("/api/activity", params={"actor": "bob"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["events"][0]["actor"] == "bob"


def test_list_activity_invalid_since_400(api_client):
    client, _ = api_client
    resp = client.get("/api/activity", params={"since": "not-a-date"})
    assert resp.status_code == 400
    assert "since" in resp.json()["detail"].lower()


def test_cleanup_activity_returns_deleted_count(api_client):
    client, _ = api_client
    resp = client.post("/api/activity/cleanup", params={"days": 30})
    assert resp.status_code == 200
    assert "deleted" in resp.json()
    assert isinstance(resp.json()["deleted"], int)


# ---------------------------------------------------------------- work queue


def test_list_queue_empty(api_client):
    client, _ = api_client
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_add_to_queue(api_client):
    client, _ = api_client
    resp = client.post("/api/queue", json={"project_id": "proj-1", "prompt": "do a thing"})
    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["project_id"] == "proj-1"
    assert item["prompt"] == "do a thing"
    assert item["status"] == WorkQueueStatus.PENDING.value
    assert item["claimed_at"] is None
    assert item["completed_at"] is None


def test_list_queue_with_data(api_client, temp_store: GluonStore):
    client, _ = api_client
    temp_store.enqueue_work(project_id="proj-1", prompt="p1")
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["prompt"] == "p1"


def test_cancel_queue_item(api_client, temp_store: GluonStore):
    client, _ = api_client
    item = temp_store.enqueue_work(project_id="proj-1", prompt="p1")
    resp = client.post(f"/api/queue/{item.id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == WorkQueueStatus.CANCELLED.value


def test_cancel_queue_item_404(api_client):
    client, _ = api_client
    resp = client.post("/api/queue/does-not-exist/cancel")
    assert resp.status_code == 404


def test_release_requires_claimed_400(api_client, temp_store: GluonStore):
    client, _ = api_client
    item = temp_store.enqueue_work(project_id="proj-1", prompt="p1")  # PENDING
    resp = client.post(f"/api/queue/{item.id}/release")
    assert resp.status_code == 400
    assert "claimed" in resp.json()["detail"].lower()


def test_release_claimed_item(api_client, temp_store: GluonStore):
    client, _ = api_client
    item = temp_store.enqueue_work(project_id="proj-1", prompt="p1")
    item.status = WorkQueueStatus.CLAIMED
    item.claimed_by = "worker-1"
    temp_store.update_work_item(item)
    resp = client.post(f"/api/queue/{item.id}/release")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == WorkQueueStatus.PENDING.value
    assert body["claimed_by"] is None


def test_release_queue_item_404(api_client):
    client, _ = api_client
    resp = client.post("/api/queue/does-not-exist/release")
    assert resp.status_code == 404
