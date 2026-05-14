"""Tests for list-view cockpit endpoints (tmp/list-view-plan.md):

- PATCH /api/runs/{id}              -> title / kind round-trip
- POST  /api/runs/{id}/snooze       -> set/clear snooze
- GET   /api/attention-counts       -> aggregate badges
- list_runs() respects include_snoozed
- auto_detect_kind() classifies common prompt prefixes
- ExecutionRun.bump_activity / is_snoozed properties
- runner.fork_run validates parent state

Fork-run *execution* (subprocess spawn) is not tested here — it requires the
full Claude SDK runtime; we cover its validation path and confirm the row is
created with the right linkage.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from gluon.models import ExecutionRun, PendingQuestion, RunStatus, utc_now
from gluon.runner import auto_detect_kind
from gluon.store import GluonStore


def _seed_run(
    store: GluonStore,
    project_id: str,
    *,
    prompt: str = "test task",
    status: RunStatus = RunStatus.COMPLETED,
) -> ExecutionRun:
    run = store.create_run(project_id=project_id, prompt=prompt, initiator="test")
    if status != RunStatus.PENDING:
        run.status = status
        store.update_run(run)
    return run


# ---------------------------------------------------------------------------
# Pure model behaviour
# ---------------------------------------------------------------------------


class TestRunModelExtensions:
    def test_new_fields_default_to_none(self):
        run = ExecutionRun(project_id="p", prompt="hi")
        assert run.custom_title is None
        assert run.kind is None
        assert run.snoozed_until is None
        assert run.last_activity_at is None
        assert run.forked_from_run_id is None

    def test_bump_activity_sets_timestamp(self):
        run = ExecutionRun(project_id="p", prompt="hi")
        assert run.last_activity_at is None
        run.bump_activity()
        assert run.last_activity_at is not None

    def test_is_snoozed_future(self):
        run = ExecutionRun(project_id="p", prompt="hi")
        run.snoozed_until = utc_now() + timedelta(hours=1)
        assert run.is_snoozed is True

    def test_is_snoozed_past_is_false(self):
        run = ExecutionRun(project_id="p", prompt="hi")
        run.snoozed_until = utc_now() - timedelta(hours=1)
        assert run.is_snoozed is False

    def test_is_snoozed_none_is_false(self):
        run = ExecutionRun(project_id="p", prompt="hi")
        assert run.is_snoozed is False


# ---------------------------------------------------------------------------
# auto_detect_kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("research postgres alternatives for sharded reads", "research"),
        ("investigate the slow query", "research"),
        ("explore using duckdb here", "research"),
        ("fix the migration race condition", "bug"),
        ("broken auth flow on safari", "bug"),
        ("document the new endpoint", "docs"),
        ("write up the API contract", "docs"),
        ("review PR 123 for the auth changes", "review"),
        ("audit our IAM policies", "review"),
        ("refactor the runner module", "chore"),
        ("rename Foo to Bar everywhere", "chore"),
        ("add a tab for billing", "build"),  # default
        ("implement the new dashboard", "build"),
    ],
)
def test_auto_detect_kind(prompt: str, expected: str) -> None:
    assert auto_detect_kind(prompt) == expected


# ---------------------------------------------------------------------------
# Store: list_runs respects include_snoozed
# ---------------------------------------------------------------------------


class TestListRunsSnoozeFilter:
    def test_default_excludes_future_snoozed(self, temp_store, project_with_path):
        project, _ = project_with_path
        snoozed = _seed_run(temp_store, project.id, prompt="snoozed one")
        active = _seed_run(temp_store, project.id, prompt="active one")

        snoozed.snoozed_until = utc_now() + timedelta(hours=2)
        temp_store.update_run(snoozed)

        ids = {r.id for r in temp_store.list_runs()}
        assert active.id in ids
        assert snoozed.id not in ids

    def test_include_snoozed_returns_all(self, temp_store, project_with_path):
        project, _ = project_with_path
        snoozed = _seed_run(temp_store, project.id, prompt="snoozed two")
        snoozed.snoozed_until = utc_now() + timedelta(hours=2)
        temp_store.update_run(snoozed)

        ids = {r.id for r in temp_store.list_runs(include_snoozed=True)}
        assert snoozed.id in ids

    def test_past_snooze_is_returned_by_default(self, temp_store, project_with_path):
        """Once snoozed_until is in the past, the run is implicitly awake."""
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, prompt="just woke up")
        run.snoozed_until = utc_now() - timedelta(minutes=5)
        temp_store.update_run(run)

        ids = {r.id for r in temp_store.list_runs()}
        assert run.id in ids


# ---------------------------------------------------------------------------
# PATCH /api/runs/{id}
# ---------------------------------------------------------------------------


class TestPatchRunEndpoint:
    def test_set_custom_title(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.patch(f"/api/runs/{run.id}", json={"custom_title": "Auth rewrite"})
        assert resp.status_code == 200
        assert resp.json()["custom_title"] == "Auth rewrite"

        # Round-trip: GET returns the same title.
        get_resp = client.get(f"/api/runs/{run.id}")
        assert get_resp.json()["custom_title"] == "Auth rewrite"

    def test_clear_custom_title(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        run.custom_title = "old"
        temp_store.update_run(run)
        client, _ = api_client

        resp = client.patch(f"/api/runs/{run.id}", json={"custom_title": None})
        assert resp.status_code == 200
        assert resp.json()["custom_title"] is None

    def test_set_valid_kind(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.patch(f"/api/runs/{run.id}", json={"kind": "research"})
        assert resp.status_code == 200
        assert resp.json()["kind"] == "research"

    def test_set_invalid_kind_rejected(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.patch(f"/api/runs/{run.id}", json={"kind": "frobnicate"})
        assert resp.status_code == 400

    def test_omitted_fields_unchanged(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        run.custom_title = "keep me"
        run.kind = "build"
        temp_store.update_run(run)
        client, _ = api_client

        # Only patch kind; title must survive.
        resp = client.patch(f"/api/runs/{run.id}", json={"kind": "docs"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "docs"
        assert body["custom_title"] == "keep me"

    def test_title_too_long_rejected(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        resp = client.patch(f"/api/runs/{run.id}", json={"custom_title": "x" * 201})
        assert resp.status_code == 400

    def test_run_not_found(self, api_client):
        client, _ = api_client
        resp = client.patch("/api/runs/00000000-0000-0000-0000-000000000000", json={"kind": "build"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/snooze
# ---------------------------------------------------------------------------


class TestSnoozeRunEndpoint:
    def test_snooze_run(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        future = (utc_now() + timedelta(hours=3)).isoformat()
        resp = client.post(f"/api/runs/{run.id}/snooze", json={"until": future})
        assert resp.status_code == 200
        assert resp.json()["snoozed_until"] is not None

    def test_unsnooze_run(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        run.snoozed_until = utc_now() + timedelta(hours=1)
        temp_store.update_run(run)
        client, _ = api_client

        resp = client.post(f"/api/runs/{run.id}/snooze", json={"until": None})
        assert resp.status_code == 200
        assert resp.json()["snoozed_until"] is None

    def test_snoozed_run_excluded_from_default_listing(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id)
        client, _ = api_client

        future = (utc_now() + timedelta(hours=1)).isoformat()
        client.post(f"/api/runs/{run.id}/snooze", json={"until": future})

        list_resp = client.get("/api/runs")
        ids = {r["id"] for r in list_resp.json()}
        assert run.id not in ids


# ---------------------------------------------------------------------------
# GET /api/attention-counts
# ---------------------------------------------------------------------------


class TestAttentionCounts:
    def test_empty_returns_zero(self, api_client):
        client, _ = api_client
        resp = client.get("/api/attention-counts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["needs_input"] == 0
        assert body["failed"] == 0
        assert body["conflicts"] == 0
        assert body["by_project"] == {}

    def test_failed_run_counted(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        _seed_run(temp_store, project.id, status=RunStatus.FAILED)
        client, _ = api_client

        body = client.get("/api/attention-counts").json()
        assert body["failed"] == 1
        assert body["total"] == 1
        assert body["by_project"][project.id] == 1

    def test_pending_question_counted(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.RUNNING)
        q = PendingQuestion(
            run_id=run.id,
            question_index=0,
            question_text="Pick one",
            header="Choice",
            options=[{"label": "A", "description": "A"}, {"label": "B", "description": "B"}],
        )
        temp_store.create_pending_question(q)
        client, _ = api_client

        body = client.get("/api/attention-counts").json()
        assert body["needs_input"] == 1
        assert body["total"] == 1

    def test_snoozed_run_not_counted(self, temp_store, project_with_path, api_client):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.FAILED)
        run.snoozed_until = utc_now() + timedelta(hours=1)
        temp_store.update_run(run)
        client, _ = api_client

        body = client.get("/api/attention-counts").json()
        assert body["failed"] == 0
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/fork — validation paths only
# ---------------------------------------------------------------------------


class TestForkRunValidation:
    def test_fork_missing_parent_returns_404(self, api_client):
        client, _ = api_client
        resp = client.post(
            "/api/runs/00000000-0000-0000-0000-000000000000/fork",
            json={"prompt": "now write the docs"},
        )
        assert resp.status_code == 404

    def test_fork_without_claude_session_returns_400(self, temp_store, project_with_path, api_client, monkeypatch):
        project, _ = project_with_path
        run = _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
        # Note: _seed_run never sets claude_session_id, so fork should refuse.
        client, _ = api_client

        resp = client.post(f"/api/runs/{run.id}/fork", json={"prompt": "spinoff"})
        assert resp.status_code == 400
        assert "no Claude session" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# runner.fork_run — direct unit test (bypasses HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_run_creates_child_row(temp_store, project_with_path, monkeypatch):
    """fork_run inherits claude_session_id, sets forked_from_run_id, spawns process."""
    from gluon.runner import TaskRunner

    project, _ = project_with_path
    parent = _seed_run(temp_store, project.id, status=RunStatus.COMPLETED)
    parent.claude_session_id = "sess-parent-abc"
    temp_store.update_run(parent)

    # Stub out the subprocess spawn — we just want to verify the row.
    runner = TaskRunner(store=temp_store)
    monkeypatch.setattr(runner, "_spawn_background_process", lambda r: None)

    child = await runner.fork_run(
        parent_run_id=parent.id,
        new_prompt="now write the docs",
        custom_title="Docs spinoff",
    )

    assert child.id != parent.id
    assert child.forked_from_run_id == parent.id
    assert child.claude_session_id == "sess-parent-abc"
    assert child.custom_title == "Docs spinoff"
    assert child.kind == "docs"  # auto-detected from prompt
    # Persisted to DB.
    refetched = temp_store.get_run(child.id)
    assert refetched is not None
    assert refetched.forked_from_run_id == parent.id
