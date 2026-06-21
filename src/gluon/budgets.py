"""Budget-enforcement helpers shared by the Orchestrator (core.py) and the
background runner (runner.py).

These used to be duplicated near-verbatim in both modules; keeping them here
gives the agent/workspace cap rules + the 80%-headroom warnings a single source
of truth. The custom exceptions live in ``core``; they're imported lazily inside
the functions to avoid a circular import (core/runner import this module at
top level).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)


def _month_start_utc() -> datetime:
    """Return the first-of-month timestamp (UTC midnight) for today."""
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def enforce_agent_budget(store: GluonStore, agent_id: str) -> None:
    """Raise ``BudgetExceededError`` if the agent has already hit its monthly cap.

    No-op when the agent has no budget configured or doesn't exist.
    """
    from gluon.core import BudgetExceededError

    agent = store.get_agent(agent_id)
    if agent is None or agent.monthly_budget_usd is None:
        return

    spent = store.get_agent_monthly_spend(agent_id, _month_start_utc())
    if spent >= agent.monthly_budget_usd:
        raise BudgetExceededError(
            agent_name=agent.name,
            spent=spent,
            budget=agent.monthly_budget_usd,
        )


def enforce_workspace_budget(store: GluonStore, workspace_id: str) -> None:
    """Raise ``WorkspaceBudgetExceededError`` if a daily or monthly cap is hit.

    No-op when neither budget is set (or the workspace doesn't exist). Checks
    daily first, then monthly. Logs a WARNING at 80% for each scope so operators
    see headroom before the hard stop.
    """
    from gluon.core import WorkspaceBudgetExceededError

    workspace = store.get_workspace(workspace_id)
    if workspace is None:
        return
    if workspace.daily_budget_usd is None and workspace.monthly_budget_usd is None:
        return

    now = datetime.now(UTC)

    # Daily scope
    if workspace.daily_budget_usd is not None:
        spent_today = store.get_workspace_daily_spend(workspace_id, now)
        budget = workspace.daily_budget_usd
        if spent_today >= budget:
            raise WorkspaceBudgetExceededError(
                workspace_name=workspace.name,
                scope="daily",
                spent=spent_today,
                budget=budget,
            )
        if budget > 0 and (spent_today / budget) >= 0.8:
            logger.warning(
                "Workspace '%s' daily spend at %.1f%% of cap ($%.2f / $%.2f)",
                workspace.name,
                (spent_today / budget) * 100,
                spent_today,
                budget,
            )

    # Monthly scope
    if workspace.monthly_budget_usd is not None:
        spent_month = store.get_workspace_monthly_spend(workspace_id, now)
        budget = workspace.monthly_budget_usd
        if spent_month >= budget:
            raise WorkspaceBudgetExceededError(
                workspace_name=workspace.name,
                scope="monthly",
                spent=spent_month,
                budget=budget,
            )
        if budget > 0 and (spent_month / budget) >= 0.8:
            logger.warning(
                "Workspace '%s' monthly spend at %.1f%% of cap ($%.2f / $%.2f)",
                workspace.name,
                (spent_month / budget) * 100,
                spent_month,
                budget,
            )


def touch_agent_last_active(store: GluonStore, agent_id: str) -> None:
    """Best-effort update of the agent's ``last_active_at`` timestamp on run start."""
    try:
        agent = store.get_agent(agent_id)
        if agent is None:
            return
        agent.last_active_at = datetime.now(UTC)
        store.update_agent(agent)
    except Exception:
        logger.debug("Failed to update agent last_active_at", exc_info=True)
