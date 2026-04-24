"""Tests for --agent flag wiring in Telegram and Discord transports.

Covers the flag parsers, channel-topic parser extension, and orchestrator
agent resolution flow (including auto-link, explicit name/ID, error paths,
and budget enforcement).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gluon.core import (
    AgentAmbiguousError,
    AgentNotFoundError,
    BudgetExceededError,
    Orchestrator,
)
from gluon.store import GluonStore
from gluon.transport.discord import DISCORD_AVAILABLE, parse_agent_flag, parse_channel_topic
from gluon.transport.telegram import extract_agent_flag

# ========== Telegram extract_agent_flag (list-based) ==========


class TestExtractAgentFlag:
    """Pure-function tests for extract_agent_flag(args)."""

    def test_no_flag(self):
        args, agent = extract_agent_flag(["myapp", "fix", "bug"])
        assert args == ["myapp", "fix", "bug"]
        assert agent is None

    def test_long_flag_at_end(self):
        args, agent = extract_agent_flag(["myapp", "fix", "bug", "--agent", "researcher"])
        assert args == ["myapp", "fix", "bug"]
        assert agent == "researcher"

    def test_short_flag_at_end(self):
        args, agent = extract_agent_flag(["myapp", "fix", "bug", "-a", "researcher"])
        assert args == ["myapp", "fix", "bug"]
        assert agent == "researcher"

    def test_flag_in_middle(self):
        args, agent = extract_agent_flag(["myapp", "--agent", "researcher", "fix", "bug"])
        assert args == ["myapp", "fix", "bug"]
        assert agent == "researcher"

    def test_flag_at_start(self):
        args, agent = extract_agent_flag(["--agent", "researcher", "myapp", "fix", "bug"])
        assert args == ["myapp", "fix", "bug"]
        assert agent == "researcher"

    def test_only_first_flag_is_consumed(self):
        """If --agent appears twice, only the first occurrence is consumed."""
        args, agent = extract_agent_flag(["myapp", "--agent", "first", "fix", "--agent", "second"])
        assert agent == "first"
        # Second --agent + value remain in the list (user error; let project parse fail)
        assert "--agent" in args
        assert "second" in args

    def test_flag_without_value_is_preserved(self):
        """Dangling --agent at the end with no value stays in args."""
        args, agent = extract_agent_flag(["myapp", "fix", "--agent"])
        assert agent is None
        assert args == ["myapp", "fix", "--agent"]

    def test_case_insensitive(self):
        args, agent = extract_agent_flag(["myapp", "fix", "--AGENT", "Researcher"])
        assert agent == "Researcher"
        assert args == ["myapp", "fix"]

    def test_id_prefix_value(self):
        """Agent value can be an ID prefix, not just a name."""
        args, agent = extract_agent_flag(["myapp", "review", "-a", "abc1234f"])
        assert agent == "abc1234f"
        assert args == ["myapp", "review"]

    def test_empty_args(self):
        args, agent = extract_agent_flag([])
        assert args == []
        assert agent is None


# ========== Discord parse_agent_flag (text-based) ==========


class TestParseAgentFlag:
    def test_long_flag(self):
        cleaned, agent = parse_agent_flag("fix the bug --agent researcher")
        assert cleaned == "fix the bug"
        assert agent == "researcher"

    def test_short_flag(self):
        cleaned, agent = parse_agent_flag("fix the bug -a researcher")
        assert cleaned == "fix the bug"
        assert agent == "researcher"

    def test_no_flag(self):
        cleaned, agent = parse_agent_flag("fix the bug")
        assert cleaned == "fix the bug"
        assert agent is None

    def test_flag_at_start(self):
        cleaned, agent = parse_agent_flag("--agent researcher fix the bug")
        assert cleaned == "fix the bug"
        assert agent == "researcher"

    def test_flag_in_middle(self):
        cleaned, agent = parse_agent_flag("fix --agent researcher the bug")
        assert cleaned == "fix the bug"
        assert agent == "researcher"

    def test_flag_with_id_prefix(self):
        cleaned, agent = parse_agent_flag("review PR --agent abc12345")
        assert cleaned == "review PR"
        assert agent == "abc12345"

    def test_case_insensitive_flag(self):
        cleaned, agent = parse_agent_flag("fix bug --AGENT Researcher")
        # Agent names pass through verbatim (they're opaque identifiers)
        assert agent == "Researcher"
        assert cleaned == "fix bug"

    def test_combined_with_model_flag(self):
        """Agent and model flags can coexist — order shouldn't matter."""
        cleaned, agent = parse_agent_flag("fix bug --agent researcher --model opus")
        assert agent == "researcher"
        assert cleaned == "fix bug --model opus"


# ========== Discord parse_channel_topic with --agent ==========


class TestParseChannelTopicAgent:
    def test_agent_only(self):
        result = parse_channel_topic("--agent researcher")
        assert result["project"] is None
        assert result["model"] is None
        assert result["agent"] == "researcher"

    def test_agent_short_flag(self):
        result = parse_channel_topic("-a researcher")
        assert result["agent"] == "researcher"

    def test_project_and_agent(self):
        result = parse_channel_topic("--project myapp --agent researcher")
        assert result["project"] == "myapp"
        assert result["agent"] == "researcher"

    def test_all_three_flags(self):
        result = parse_channel_topic("--project myapp --model haiku --agent researcher")
        assert result["project"] == "myapp"
        assert result["model"] == "claude-haiku-4.5"
        assert result["agent"] == "researcher"

    def test_no_agent_flag(self):
        result = parse_channel_topic("--project myapp --model opus")
        assert result["agent"] is None
        assert result["project"] == "myapp"

    def test_none_topic_has_agent_key(self):
        """Backward compatibility — None topic returns dict with 'agent': None."""
        result = parse_channel_topic(None)
        assert "agent" in result
        assert result["agent"] is None

    def test_empty_topic_has_agent_key(self):
        result = parse_channel_topic("")
        assert "agent" in result
        assert result["agent"] is None


# ========== Integration: orchestrator agent resolution flow ==========


@pytest.fixture
def store_with_workspace(tmp_path: Path):
    """Shared fixture: store + workspace + project + orchestrator."""
    store = GluonStore(db_path=tmp_path / "bot_agent.db")
    workspace = store.create_workspace("team-alpha", tmp_path)
    proj_path = tmp_path / "proj"
    proj_path.mkdir(exist_ok=True)
    project = store.create_project("myapp", proj_path, workspace_id=workspace.id)
    orchestrator = Orchestrator(store=store)
    return store, workspace, project, orchestrator


class TestAgentResolutionInBotFlow:
    """Simulate the full orchestrator.resolve_agent path the bots now take."""

    def test_explicit_agent_by_name_resolves(self, store_with_workspace):
        store, workspace, project, orchestrator = store_with_workspace
        agent = store.create_agent(workspace.id, "researcher", role="reviewer")

        resolved_id = orchestrator.resolve_agent("researcher", project.workspace_id)
        assert resolved_id == agent.id

    def test_explicit_agent_by_id_prefix_resolves(self, store_with_workspace):
        store, workspace, project, orchestrator = store_with_workspace
        agent = store.create_agent(workspace.id, "researcher")

        prefix = agent.id[:8]
        resolved_id = orchestrator.resolve_agent(prefix, project.workspace_id)
        assert resolved_id == agent.id

    def test_auto_link_when_workspace_has_one_active_agent(self, store_with_workspace):
        """No --agent given, but workspace has exactly one active agent → auto-link."""
        store, workspace, project, orchestrator = store_with_workspace
        agent = store.create_agent(workspace.id, "solo")

        resolved_id = orchestrator.resolve_agent(None, project.workspace_id)
        assert resolved_id == agent.id

    def test_no_auto_link_when_workspace_has_multiple_active(self, store_with_workspace):
        """Multiple active agents — should not auto-link, returns None."""
        store, workspace, project, orchestrator = store_with_workspace
        store.create_agent(workspace.id, "researcher")
        store.create_agent(workspace.id, "engineer")

        resolved_id = orchestrator.resolve_agent(None, project.workspace_id)
        assert resolved_id is None  # ambiguous, no auto-link

    def test_no_auto_link_when_workspace_has_no_agents(self, store_with_workspace):
        """Backward-compat: workspaces without agents still work (returns None)."""
        _store, _ws, project, orchestrator = store_with_workspace
        resolved_id = orchestrator.resolve_agent(None, project.workspace_id)
        assert resolved_id is None

    def test_unknown_agent_name_raises(self, store_with_workspace):
        _store, _ws, project, orchestrator = store_with_workspace
        with pytest.raises(AgentNotFoundError):
            orchestrator.resolve_agent("ghost", project.workspace_id)

    def test_ambiguous_id_prefix_raises(self, store_with_workspace):
        """Two agents share an ID prefix → AgentAmbiguousError."""
        store, workspace, _project, orchestrator = store_with_workspace
        # We can't control UUID generation directly, so build a second agent and
        # reuse a short common prefix ("x") by matching on all agents in the ws.
        store.create_agent(workspace.id, "a1")
        store.create_agent(workspace.id, "a2")
        # The empty-string prefix matches every agent → ambiguous
        with pytest.raises(AgentAmbiguousError):
            orchestrator.resolve_agent("", workspace.id)


class TestBudgetEnforcementFromBotFlow:
    """The proactive budget check bots now do before creating runs."""

    def test_under_budget_passes(self, store_with_workspace):
        store, workspace, _project, orchestrator = store_with_workspace
        agent = store.create_agent(workspace.id, "researcher", monthly_budget_usd=10.0)
        # Should not raise — no spend yet
        orchestrator._enforce_agent_budget(agent.id)

    def test_over_budget_raises(self, store_with_workspace, monkeypatch):
        store, workspace, project, orchestrator = store_with_workspace
        agent = store.create_agent(workspace.id, "researcher", monthly_budget_usd=10.0)

        # Simulate spend by overriding get_agent_monthly_spend
        monkeypatch.setattr(store, "get_agent_monthly_spend", lambda aid, _since: 15.0)

        with pytest.raises(BudgetExceededError) as exc_info:
            orchestrator._enforce_agent_budget(agent.id)
        # Error message should mention the agent name + both numbers, so the
        # bot reply "❌ Agent 'researcher' monthly budget exceeded..." is clean.
        assert "researcher" in str(exc_info.value)
        assert exc_info.value.agent_name == "researcher"
        assert exc_info.value.spent == 15.0
        assert exc_info.value.budget == 10.0

    def test_no_budget_configured_is_noop(self, store_with_workspace):
        """Agents without a monthly budget are never rejected."""
        store, workspace, _project, orchestrator = store_with_workspace
        agent = store.create_agent(workspace.id, "researcher", monthly_budget_usd=None)
        # Should not raise
        orchestrator._enforce_agent_budget(agent.id)


class TestCreateRunThreadsAgentId:
    """Verify the bot's store.create_run(..., agent_id=...) wiring persists."""

    def test_create_run_with_agent_id_persists(self, store_with_workspace):
        store, workspace, project, _orchestrator = store_with_workspace
        agent = store.create_agent(workspace.id, "researcher")

        run = store.create_run(
            project_id=project.id,
            prompt="test prompt",
            initiator="telegram:999",
            agent_id=agent.id,
        )

        reloaded = store.get_run(run.id)
        assert reloaded is not None
        assert reloaded.agent_id == agent.id

    def test_create_run_without_agent_id_is_none(self, store_with_workspace):
        """Backward compat — runs with no agent_id stay None."""
        store, _ws, project, _orchestrator = store_with_workspace
        run = store.create_run(project_id=project.id, prompt="test", initiator="cli")
        reloaded = store.get_run(run.id)
        assert reloaded is not None
        assert reloaded.agent_id is None


# ========== Telegram transport: agent plumbing ==========


@pytest.mark.anyio
async def test_telegram_run_rejects_unknown_agent(store_with_workspace):
    """Telegram /run should show a clean error message for unknown --agent values.

    Exercises the code path via the orchestrator that the handler uses.
    """
    store, workspace, _project, orchestrator = store_with_workspace
    # Workspace has no agents at all
    with pytest.raises(AgentNotFoundError) as exc_info:
        orchestrator.resolve_agent("ghost", workspace.id)
    msg = str(exc_info.value)
    assert "ghost" in msg


@pytest.mark.anyio
async def test_telegram_run_auto_links_single_active_agent(store_with_workspace):
    """When a workspace has one active agent, the bot should auto-link."""
    store, workspace, project, orchestrator = store_with_workspace
    agent = store.create_agent(workspace.id, "solo-researcher")

    # Simulate: user calls /run myapp fix bug (no --agent), bot calls resolve_agent(None, ws_id)
    resolved = orchestrator.resolve_agent(None, project.workspace_id)
    assert resolved == agent.id


# ========== Discord transport: agent plumbing ==========


@pytest.mark.skipif(
    not DISCORD_AVAILABLE,
    reason="discord.py not installed",
)
def test_discord_channel_topic_agent_flows_through(tmp_path: Path):
    """When channel topic has --agent <name>, the topic parser surfaces it.

    This is the data a Discord task handler would consult when the user
    didn't pass --agent in the message body.
    """
    result = parse_channel_topic("--project myapp --agent researcher")
    assert result["project"] == "myapp"
    assert result["agent"] == "researcher"


# ========== Backward-compat: tests that should still pass ==========


def test_telegram_extract_agent_is_pure_when_no_flag():
    """Callers using the old flow (no --agent) should see identical behavior."""
    args = ["myapp", "fix", "the", "bug"]
    cleaned_args, agent = extract_agent_flag(args)
    assert agent is None
    assert cleaned_args == args  # unchanged


def test_discord_parse_agent_is_pure_when_no_flag():
    text = "fix the bug please"
    cleaned, agent = parse_agent_flag(text)
    assert agent is None
    assert cleaned == text  # unchanged (modulo strip)
