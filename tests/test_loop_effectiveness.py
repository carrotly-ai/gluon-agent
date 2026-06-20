"""Tests for the loop-effectiveness metric (I5): acceptance rate and
cost-per-accepted-change, split by gateability of the run `kind`.

Part of the loop-engineering work — purely additive, read-only over existing
data, so it must not change existing usage behavior.
"""

from __future__ import annotations

from gluon.models import GATEABLE_KINDS, RunStatus, is_gateable_kind
from gluon.store import GluonStore


def _seed(
    store: GluonStore,
    project_id: str,
    *,
    kind: str | None,
    cost: float,
    pr_number: int | None = None,
    pr_status: str | None = None,
) -> None:
    run = store.create_run(project_id=project_id, prompt="t", initiator="test")
    run.kind = kind
    run.cost_usd = cost
    run.status = RunStatus.COMPLETED
    run.pr_number = pr_number
    run.pr_status = pr_status
    store.update_run(run)


def test_is_gateable_kind() -> None:
    for k in ("build", "bug", "chore"):
        assert is_gateable_kind(k) is True
    for k in ("research", "docs", "review"):
        assert is_gateable_kind(k) is False
    assert is_gateable_kind(None) is True  # auto_detect_kind defaults to "build"
    assert GATEABLE_KINDS == frozenset({"build", "bug", "chore"})


def test_loop_effectiveness_metrics(temp_store: GluonStore) -> None:
    store = temp_store
    ws = store.create_workspace("w", "/tmp/w")
    proj = store.create_project(name="p", path="/tmp/w/p", workspace_id=ws.id)

    # gateable: build merged ($4), bug open ($6), chore no-PR ($2)
    _seed(store, proj.id, kind="build", cost=4.0, pr_number=1, pr_status="merged")
    _seed(store, proj.id, kind="bug", cost=6.0, pr_number=2, pr_status="open")
    _seed(store, proj.id, kind="chore", cost=2.0)
    # gateless: research merged ($10)
    _seed(store, proj.id, kind="research", cost=10.0, pr_number=3, pr_status="merged")

    eff = store.get_loop_effectiveness()

    o = eff["overall"]
    assert (o["runs"], o["pr_producing"], o["accepted"]) == (4, 3, 2)
    assert o["acceptance_rate"] == 2 / 3
    assert o["cost_usd"] == 22.0
    assert o["cost_per_accepted_usd"] == 11.0

    g = eff["gateable"]
    assert (g["runs"], g["pr_producing"], g["accepted"]) == (3, 2, 1)
    assert g["acceptance_rate"] == 0.5
    assert g["cost_usd"] == 12.0  # 4 + 6 + 2
    assert g["cost_per_accepted_usd"] == 12.0

    gl = eff["gateless"]
    assert (gl["runs"], gl["accepted"]) == (1, 1)
    assert gl["cost_per_accepted_usd"] == 10.0

    kinds = {row["kind"]: row for row in eff["by_kind"]}
    assert set(kinds) == {"build", "bug", "chore", "research"}
    assert kinds["build"]["accepted"] == 1


def test_loop_effectiveness_empty(temp_store: GluonStore) -> None:
    eff = temp_store.get_loop_effectiveness()
    assert eff["overall"]["runs"] == 0
    assert eff["overall"]["acceptance_rate"] == 0.0
    assert eff["overall"]["cost_per_accepted_usd"] is None
    assert eff["by_kind"] == []


def test_null_kind_counts_as_gateable(temp_store: GluonStore) -> None:
    ws = temp_store.create_workspace("w", "/tmp/w")
    proj = temp_store.create_project(name="p", path="/tmp/w/p", workspace_id=ws.id)
    _seed(temp_store, proj.id, kind=None, cost=1.0, pr_number=1, pr_status="merged")
    eff = temp_store.get_loop_effectiveness()
    assert eff["gateable"]["accepted"] == 1
    assert eff["gateless"]["runs"] == 0


def test_usage_summary_unchanged(temp_store: GluonStore) -> None:
    """Non-regression: the existing header summary keeps its exact shape."""
    summary = temp_store.get_usage_summary()
    assert set(summary) == {
        "today_cost_usd",
        "today_runs",
        "week_cost_usd",
        "week_runs",
        "month_cost_usd",
        "month_runs",
        "total_cost_usd",
        "total_runs",
    }


def test_effectiveness_endpoint(api_client) -> None:
    client, _ = api_client
    resp = client.get("/api/usage/effectiveness")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"overall", "gateable", "gateless", "by_kind"}
    assert "cost_per_accepted_usd" in body["overall"]
    assert "acceptance_rate" in body["gateable"]
