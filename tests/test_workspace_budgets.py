"""Tests for Theme D2 — workspace-scoped rolling daily/monthly budgets.

Covers:
  - Workspace model + schema roundtrip for daily_budget_usd / monthly_budget_usd
  - Store aggregation helpers (get_workspace_spend_since, daily/monthly shortcuts)
  - Cross-day / cross-month / cross-workspace isolation
  - _enforce_workspace_budget behavior (no-op, raise, 80% warning)
  - Orchestrator.execute integration (raises cleanly when over)
  - Web API create + 402 on over-budget
  - CLI `workspace budget` set/clear semantics
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from gluon.core import (
    Orchestrator,
    WorkspaceBudgetExceededError,
)
from gluon.runner import RunnerConfig, TaskRunner
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> GluonStore:
    return GluonStore(db_path=tmp_path / "budgets.db")


def _make_workspace_with_project(
    store: GluonStore,
    tmp_path: Path,
    ws_name: str = "ws",
    *,
    daily_budget_usd: float | None = None,
    monthly_budget_usd: float | None = None,
):
    ws_path = tmp_path / ws_name
    ws_path.mkdir(exist_ok=True)
    workspace = store.create_workspace(
        ws_name,
        ws_path,
        daily_budget_usd=daily_budget_usd,
        monthly_budget_usd=monthly_budget_usd,
    )
    # Project names are globally unique — scope with the workspace name
    proj_name = f"{ws_name}-proj"
    proj_path = ws_path / "proj"
    proj_path.mkdir(exist_ok=True)
    project = store.create_project(proj_name, proj_path, workspace_id=workspace.id)
    return workspace, project


def _make_orchestrator(store: GluonStore) -> Orchestrator:
    """Bare Orchestrator for budget tests — no git_manager or notifier required."""
    orch = Orchestrator.__new__(Orchestrator)
    orch.store = store  # type: ignore[attr-defined]
    orch.git_manager = None  # type: ignore[attr-defined]
    orch.notifier = None  # type: ignore[attr-defined]
    return orch


def _seed_run_at(store: GluonStore, project_id: str, cost: float, when: datetime | None = None) -> str:
    """Create a run with a specific cost and optional back-dated created_at."""
    run = store.create_run(project_id=project_id, prompt=f"cost ${cost}")
    run.cost_usd = cost
    store.update_run(run)
    if when is not None:
        with store._get_conn() as conn:
            conn.execute(
                "UPDATE execution_runs SET created_at = ? WHERE id = ?",
                (when.isoformat(), run.id),
            )
    return run.id


# ---------------------------------------------------------------------------
# Schema roundtrip
# ---------------------------------------------------------------------------


class TestWorkspaceBudgetSchema:
    def test_budget_fields_default_to_none(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws = store.create_workspace("plain", tmp_path / "plain")
        assert ws.daily_budget_usd is None
        assert ws.monthly_budget_usd is None

        # Roundtrip via store.get_workspace — must still be None after fetch.
        fetched = store.get_workspace(ws.id)
        assert fetched is not None
        assert fetched.daily_budget_usd is None
        assert fetched.monthly_budget_usd is None

    def test_budget_fields_persist_and_hydrate(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws = store.create_workspace(
            "capped",
            tmp_path / "capped",
            daily_budget_usd=5.0,
            monthly_budget_usd=50.0,
        )
        assert ws.daily_budget_usd == 5.0
        assert ws.monthly_budget_usd == 50.0

        fetched = store.get_workspace(ws.id)
        assert fetched is not None
        assert fetched.daily_budget_usd == 5.0
        assert fetched.monthly_budget_usd == 50.0

    def test_update_workspace_persists_budgets(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws = store.create_workspace("upd", tmp_path / "upd")
        ws.daily_budget_usd = 7.5
        ws.monthly_budget_usd = 100.0
        store.update_workspace(ws)

        refreshed = store.get_workspace(ws.id)
        assert refreshed is not None
        assert refreshed.daily_budget_usd == 7.5
        assert refreshed.monthly_budget_usd == 100.0

        # And clearing works
        refreshed.daily_budget_usd = None
        refreshed.monthly_budget_usd = None
        store.update_workspace(refreshed)

        cleared = store.get_workspace(ws.id)
        assert cleared is not None
        assert cleared.daily_budget_usd is None
        assert cleared.monthly_budget_usd is None


# ---------------------------------------------------------------------------
# Store aggregation
# ---------------------------------------------------------------------------


class TestWorkspaceSpendAggregation:
    def test_spend_sums_multiple_runs_in_workspace(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path)
        for cost in (1.0, 2.5, 3.5):
            _seed_run_at(store, proj.id, cost)

        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        total = store.get_workspace_spend_since(ws.id, since)
        assert total == pytest.approx(7.0, rel=1e-6)

    def test_spend_sums_across_multiple_projects_in_workspace(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj1 = _make_workspace_with_project(store, tmp_path)

        # Add a second project inside the same workspace
        ws_path = tmp_path / "ws"
        p2_path = ws_path / "proj2"
        p2_path.mkdir(exist_ok=True)
        p2 = store.create_project("ws-proj2", p2_path, workspace_id=ws.id)

        # Seed one run in each project
        _seed_run_at(store, proj1.id, 4.0)
        _seed_run_at(store, p2.id, 6.0)

        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        total = store.get_workspace_spend_since(ws.id, since)
        assert total == pytest.approx(10.0, rel=1e-6)

    def test_spend_excludes_runs_before_since_timestamp(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path)

        # One ancient run, one current run
        ancient = datetime.now(UTC) - timedelta(days=60)
        _seed_run_at(store, proj.id, 100.0, when=ancient)
        _seed_run_at(store, proj.id, 3.0)  # today

        # since = today midnight — ancient run is excluded
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = store.get_workspace_spend_since(ws.id, since)
        assert total == pytest.approx(3.0)

    def test_spend_isolates_across_workspaces(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws_a, proj_a = _make_workspace_with_project(store, tmp_path, ws_name="a")
        ws_b, proj_b = _make_workspace_with_project(store, tmp_path, ws_name="b")

        _seed_run_at(store, proj_a.id, 9.0)
        _seed_run_at(store, proj_b.id, 2.0)

        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        assert store.get_workspace_spend_since(ws_a.id, since) == pytest.approx(9.0)
        assert store.get_workspace_spend_since(ws_b.id, since) == pytest.approx(2.0)

    def test_null_cost_treated_as_zero(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path)
        run = store.create_run(project_id=proj.id, prompt="free")
        # Leave cost_usd as None
        assert run.cost_usd is None

        since = datetime.now(UTC) - timedelta(hours=1)
        assert store.get_workspace_spend_since(ws.id, since) == pytest.approx(0.0)

    def test_daily_spend_excludes_yesterday(self, tmp_path: Path):
        """A run from yesterday must not count against today's daily spend."""
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path)

        yesterday = datetime.now(UTC) - timedelta(days=1, hours=2)
        _seed_run_at(store, proj.id, 10.0, when=yesterday)
        _seed_run_at(store, proj.id, 1.5)  # today

        assert store.get_workspace_daily_spend(ws.id) == pytest.approx(1.5)

    def test_monthly_spend_excludes_previous_month(self, tmp_path: Path):
        """A run from last month must not count against this month's spend."""
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path)

        # ~40 days ago (definitely prior month)
        last_month = datetime.now(UTC) - timedelta(days=40)
        _seed_run_at(store, proj.id, 25.0, when=last_month)
        _seed_run_at(store, proj.id, 4.0)  # this month

        assert store.get_workspace_monthly_spend(ws.id) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Enforcement (_enforce_workspace_budget)
# ---------------------------------------------------------------------------


class TestEnforceWorkspaceBudget:
    def test_noop_when_no_budgets_set(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path)
        _seed_run_at(store, proj.id, 999.0)  # huge spend, no cap → no error

        orch = _make_orchestrator(store)
        orch._enforce_workspace_budget(ws.id)  # should not raise

    def test_noop_when_workspace_missing(self, tmp_path: Path):
        store = _make_store(tmp_path)
        orch = _make_orchestrator(store)
        # No raise, no crash
        orch._enforce_workspace_budget("does-not-exist-id")

    def test_raises_on_daily_exceed(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path, daily_budget_usd=5.0)
        _seed_run_at(store, proj.id, 6.0)

        orch = _make_orchestrator(store)
        with pytest.raises(WorkspaceBudgetExceededError) as exc:
            orch._enforce_workspace_budget(ws.id)
        assert exc.value.scope == "daily"
        assert exc.value.spent == pytest.approx(6.0)
        assert exc.value.budget == 5.0
        assert exc.value.workspace_name == ws.name

    def test_raises_on_monthly_exceed(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path, monthly_budget_usd=20.0)
        # Seed several runs that together exceed monthly but land on separate days
        # so neither hits a daily cap (there isn't one set).
        for cost in (8.0, 8.0, 8.0):
            _seed_run_at(store, proj.id, cost)

        orch = _make_orchestrator(store)
        with pytest.raises(WorkspaceBudgetExceededError) as exc:
            orch._enforce_workspace_budget(ws.id)
        assert exc.value.scope == "monthly"
        assert exc.value.spent == pytest.approx(24.0)
        assert exc.value.budget == 20.0

    def test_daily_fires_before_monthly_when_both_exceeded(self, tmp_path: Path):
        """If both caps are exceeded, daily is reported first (checked first)."""
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(
            store,
            tmp_path,
            daily_budget_usd=1.0,
            monthly_budget_usd=2.0,
        )
        _seed_run_at(store, proj.id, 5.0)

        orch = _make_orchestrator(store)
        with pytest.raises(WorkspaceBudgetExceededError) as exc:
            orch._enforce_workspace_budget(ws.id)
        assert exc.value.scope == "daily"

    def test_warning_logged_at_80_percent(self, tmp_path: Path, caplog):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path, daily_budget_usd=10.0)
        _seed_run_at(store, proj.id, 8.0)  # exactly 80% — should warn but not raise

        orch = _make_orchestrator(store)
        with caplog.at_level(logging.WARNING, logger="gluon.core"):
            orch._enforce_workspace_budget(ws.id)  # 80% threshold: warn only

        assert any("80" in rec.message or "daily" in rec.message.lower() for rec in caplog.records)

    def test_no_warning_below_80_percent(self, tmp_path: Path, caplog):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path, daily_budget_usd=10.0)
        _seed_run_at(store, proj.id, 5.0)  # 50% — quiet

        orch = _make_orchestrator(store)
        with caplog.at_level(logging.WARNING, logger="gluon.core"):
            orch._enforce_workspace_budget(ws.id)
        assert not any("80" in rec.message for rec in caplog.records)

    def test_at_exactly_cap_raises(self, tmp_path: Path):
        """Spend == cap is treated as exceeded (matches agent-budget semantics)."""
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path, daily_budget_usd=5.0)
        _seed_run_at(store, proj.id, 5.0)

        orch = _make_orchestrator(store)
        with pytest.raises(WorkspaceBudgetExceededError):
            orch._enforce_workspace_budget(ws.id)


# ---------------------------------------------------------------------------
# Orchestrator.execute integration
# ---------------------------------------------------------------------------


class TestOrchestratorExecuteIntegration:
    async def test_execute_raises_when_workspace_over_budget(self, tmp_path: Path):
        """Orchestrator.execute should hard-stop before writing a run row."""
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path, daily_budget_usd=2.0)
        _seed_run_at(store, proj.id, 3.0)  # already over cap

        orch = _make_orchestrator(store)

        with pytest.raises(WorkspaceBudgetExceededError):
            async for _ in orch.execute(proj.name, "new prompt"):
                pass

        # Confirm no new run row was written beyond the pre-seeded one
        runs = store.list_runs(project_id=proj.id)
        assert len(runs) == 1


# ---------------------------------------------------------------------------
# TaskRunner.submit integration
# ---------------------------------------------------------------------------


class TestTaskRunnerSubmitIntegration:
    async def test_runner_submit_blocks_over_workspace_budget(self, tmp_path: Path):
        store = _make_store(tmp_path)
        ws, proj = _make_workspace_with_project(store, tmp_path, monthly_budget_usd=3.0)
        _seed_run_at(store, proj.id, 4.0)

        runner = TaskRunner(store=store, config=RunnerConfig(log_path=tmp_path / "logs"))
        runner._run_task = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(WorkspaceBudgetExceededError):
            await runner.submit(
                project_id=proj.id,
                prompt="should-be-blocked",
                initiator="test",
            )

        runs = store.list_runs(project_id=proj.id)
        assert len(runs) == 1, "No new run should be created when workspace budget is exceeded"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLIWorkspaceBudget:
    def test_cli_sets_both_budgets(self, tmp_path: Path, monkeypatch):
        from gluon import cli as cli_module

        store = GluonStore(db_path=tmp_path / "cli-set.db")
        ws_dir = tmp_path / "target-ws"
        ws_dir.mkdir()
        store.create_workspace("target", ws_dir)

        monkeypatch.setattr(cli_module, "get_orchestrator", lambda: Orchestrator(store=store))

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["workspace", "budget", "target", "--daily", "4.0", "--monthly", "50.0"],
        )
        assert result.exit_code == 0, result.output

        fetched = store.get_workspace_by_name("target")
        assert fetched is not None
        assert fetched.daily_budget_usd == 4.0
        assert fetched.monthly_budget_usd == 50.0

    def test_cli_zero_clears_budget(self, tmp_path: Path, monkeypatch):
        from gluon import cli as cli_module

        store = GluonStore(db_path=tmp_path / "cli-clear.db")
        ws_dir = tmp_path / "clear-ws"
        ws_dir.mkdir()
        store.create_workspace(
            "clear",
            ws_dir,
            daily_budget_usd=10.0,
            monthly_budget_usd=100.0,
        )

        monkeypatch.setattr(cli_module, "get_orchestrator", lambda: Orchestrator(store=store))

        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            ["workspace", "budget", "clear", "--daily", "0", "--monthly", "0"],
        )
        assert result.exit_code == 0, result.output

        fetched = store.get_workspace_by_name("clear")
        assert fetched is not None
        assert fetched.daily_budget_usd is None
        assert fetched.monthly_budget_usd is None

    def test_cli_show_renders_spend(self, tmp_path: Path, monkeypatch):
        """`workspace show` should surface daily/monthly spend vs budget."""
        from gluon import cli as cli_module

        store = GluonStore(db_path=tmp_path / "cli-show.db")
        ws_dir = tmp_path / "show-ws"
        ws_dir.mkdir()
        ws = store.create_workspace("shown", ws_dir, daily_budget_usd=10.0, monthly_budget_usd=50.0)
        proj_dir = ws_dir / "p"
        proj_dir.mkdir()
        proj = store.create_project("p", proj_dir, workspace_id=ws.id)
        _seed_run_at(store, proj.id, 2.5)

        monkeypatch.setattr(cli_module, "get_orchestrator", lambda: Orchestrator(store=store))

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["workspace", "show", "shown"])
        assert result.exit_code == 0, result.output
        assert "shown" in result.output
        # Daily budget line should reflect the seeded $2.50 / $10 cap
        assert "2.50" in result.output
        assert "10.00" in result.output


# ---------------------------------------------------------------------------
# Web API
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client_for_budgets(tmp_path: Path):
    """TestClient wired against a real store with workspace+project on disk."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("FastAPI/Starlette not installed")

    from unittest.mock import AsyncMock, MagicMock, patch

    from gluon.web.api import create_app

    store = GluonStore(db_path=tmp_path / "api.db")
    ws_dir = tmp_path / "api-ws"
    ws_dir.mkdir()
    proj_dir = ws_dir / "api-proj"
    proj_dir.mkdir()

    workspace = store.create_workspace("api-ws", ws_dir, daily_budget_usd=2.0)
    project = store.create_project("api-proj", proj_dir, workspace_id=workspace.id)

    mock_ws = AsyncMock()
    mock_runner = MagicMock()
    mock_runner.submit = AsyncMock()
    mock_runner.cancel = AsyncMock(return_value=True)
    mock_runner.refresh_all_runs = MagicMock()
    mock_runner.refresh_run_status = MagicMock()
    mock_runner.resume_in_place = AsyncMock()
    mock_runner.evaluate_supervision = AsyncMock()
    mock_runner.git_manager = MagicMock()
    mock_runner.git_manager._get_pr_info = AsyncMock(return_value=None)

    with (
        patch("gluon.web.api.TaskRunner", return_value=mock_runner),
        patch("gluon.web.api.ws_manager", mock_ws),
    ):
        app = create_app(store)
        client = TestClient(app)
        yield client, store, workspace, project, mock_runner


class TestWebAPIWorkspaceBudgets:
    def test_workspace_get_returns_budget_fields(self, api_client_for_budgets):
        client, store, workspace, _, _ = api_client_for_budgets
        resp = client.get(f"/api/workspaces/{workspace.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["daily_budget_usd"] == 2.0
        assert body["monthly_budget_usd"] is None
        assert "daily_spend_usd" in body
        assert "monthly_spend_usd" in body

    def test_create_run_returns_402_on_workspace_over_budget(self, api_client_for_budgets):
        client, store, workspace, project, mock_runner = api_client_for_budgets

        # Make runner.submit raise WorkspaceBudgetExceededError like the real one would
        mock_runner.submit = AsyncMock(
            side_effect=WorkspaceBudgetExceededError(
                workspace_name=workspace.name,
                scope="daily",
                spent=5.0,
                budget=2.0,
            )
        )

        resp = client.post(
            "/api/runs",
            json={
                "project_name": project.name,
                "prompt": "hi",
                "use_worktree": False,
            },
        )
        assert resp.status_code == 402, resp.json()
        assert "daily" in resp.json()["detail"].lower()
