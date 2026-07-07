"""CLI interface for Gluon Agent."""

import os
from pathlib import Path
from typing import Annotated, Any

import anyio
import typer
from claude_agent_sdk import EffortLevel
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gluon import __version__
from gluon.agent import AgentMessage, AgentResult, GluonAgent
from gluon.cleanup import LogCleanupService, WorktreeCleanupService
from gluon.core import (
    BudgetExceededError,
    Orchestrator,
    ProjectExistsError,
    ProjectNotFoundError,
    WorkspaceBudgetExceededError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from gluon.git_manager import GitManager
from gluon.models import CircuitState, RunStatus
from gluon.models_config import MODEL_ALIASES, ModelTier, describe_models
from gluon.runner import RunHealth, TaskRunner, assess_run_health, format_duration, format_run_status
from gluon.store import GluonStore

# Load environment variables from .env files (in order of precedence)
# Later files override earlier ones
load_dotenv(Path.home() / ".gluon" / ".env")  # Global config
load_dotenv(".env")  # Project .env
load_dotenv(".env.local")  # Local overrides (highest priority)

app = typer.Typer(
    name="gluon",
    help="AI orchestrator for managing multiple Claude Code agents across projects.",
    no_args_is_help=True,
)

project_app = typer.Typer(help="Manage projects")
app.add_typer(project_app, name="project")

workspace_app = typer.Typer(help="Manage workspaces")
app.add_typer(workspace_app, name="workspace")

git_app = typer.Typer(help="Git operations for projects")
app.add_typer(git_app, name="git")

mcp_app = typer.Typer(help="MCP server diagnostics")
app.add_typer(mcp_app, name="mcp")

webhook_app = typer.Typer(help="Webhook configuration")
app.add_typer(webhook_app, name="webhook")

ralph_app = typer.Typer(help="Ralph loop commands")
app.add_typer(ralph_app, name="ralph")

loop_app = typer.Typer(help="Agent loops (loop engineering) — objective-driven, agent-authored iterations")
app.add_typer(loop_app, name="loop")

supervision_app = typer.Typer(help="Supervision and auto-resume commands")
app.add_typer(supervision_app, name="supervision")

doctor_app = typer.Typer(help="System health diagnostics")
app.add_typer(doctor_app, name="doctor")

chain_app = typer.Typer(help="Task chain management")
app.add_typer(chain_app, name="chain")

supervisor_app = typer.Typer(help="Supervisor daemon management")
app.add_typer(supervisor_app, name="supervisor")

worktree_app = typer.Typer(help="Worktree management and cleanup")
app.add_typer(worktree_app, name="worktree")

settings_app = typer.Typer(help="Gluon global settings (stored in gluon.db)")
app.add_typer(settings_app, name="settings")

agent_app = typer.Typer(help="Manage persistent agent identities (Theme B Phase 1)")
app.add_typer(agent_app, name="agent")

task_app = typer.Typer(help="Orchestrator-layer task tracking (Theme B Phase 3)")
app.add_typer(task_app, name="task")

schedule_app = typer.Typer(help="Agent schedules — cron-based wakeups (Theme B Phase 2)")
app.add_typer(schedule_app, name="schedule")

heartbeat_app = typer.Typer(help="Heartbeat runs — history of scheduled agent firings")
app.add_typer(heartbeat_app, name="heartbeat")

approvals_app = typer.Typer(help="Approval gates for risky tool calls (Theme D1)")
app.add_typer(approvals_app, name="approvals")

user_app = typer.Typer(help="Manage Gluon user accounts (D5 Phase 1)")
app.add_typer(user_app, name="user")

console = Console()


def _validate_model(model: str | None) -> ModelTier | None:
    """Resolve a ``--model`` value (alias or tier name) to a ModelTier.

    Prints an error + the model list and exits on an invalid value. Shared by
    the ``run`` and ``resume`` commands.
    """
    if not model:
        return None
    model_lower = model.lower()
    if model_lower in MODEL_ALIASES:
        return MODEL_ALIASES[model_lower]
    try:
        return ModelTier(model_lower)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid model: {model}")
        console.print(describe_models())
        raise typer.Exit(1) from None


def get_orchestrator() -> Orchestrator:
    """Get orchestrator instance."""
    return Orchestrator()


# ========== Project Commands ==========


@project_app.command("add")
def project_add(
    name: Annotated[str, typer.Argument(help="Unique name for the project")],
    path: Annotated[Path, typer.Argument(help="Path to project directory")],
):
    """Register a new project."""
    orchestrator = get_orchestrator()

    try:
        project = orchestrator.register_project(name, path)
        console.print(f"[green]✓[/green] Project '{project.name}' registered")
        console.print(f"  Path: {project.path}")
        console.print(f"  ID: {project.id}")
    except ProjectExistsError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@project_app.command("list")
def project_list():
    """List all registered projects."""
    orchestrator = get_orchestrator()
    projects = orchestrator.list_projects()

    if not projects:
        console.print("[dim]No projects registered.[/dim]")
        console.print("Use 'gluon project add <name> <path>' to register a project.")
        return

    table = Table(title="Projects")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    table.add_column("Sessions", justify="right")
    table.add_column("ID", style="dim")

    for project in projects:
        sessions = orchestrator.list_sessions(project.name)
        table.add_row(
            project.name,
            str(project.path),
            str(len(sessions)),
            project.id[:8],
        )

    console.print(table)


@project_app.command("remove")
def project_remove(
    name: Annotated[str, typer.Argument(help="Project name or ID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
):
    """Remove a project and all its sessions."""
    orchestrator = get_orchestrator()

    try:
        project = orchestrator.get_project(name)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Remove project '{project.name}' and all its sessions?")
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(0)

    if orchestrator.remove_project(name):
        console.print(f"[green]✓[/green] Project '{project.name}' removed")
    else:
        console.print(
            f"[red]Error:[/red] Could not remove project '{project.name}'. "
            "It may have active runs or be in use — cancel any running tasks and try again."
        )
        raise typer.Exit(1)


# ========== Workspace Commands ==========


@workspace_app.command("add")
def workspace_add(
    name: Annotated[str, typer.Argument(help="Unique name for the workspace")],
    path: Annotated[Path, typer.Argument(help="Path to workspace directory")],
    no_scan: Annotated[bool, typer.Option("--no-scan", help="Don't auto-scan for projects")] = False,
    daily: Annotated[
        float | None,
        typer.Option("--daily", help="Daily rolling budget in USD (0 = unset)"),
    ] = None,
    monthly: Annotated[
        float | None,
        typer.Option("--monthly", help="Monthly rolling budget in USD (0 = unset)"),
    ] = None,
):
    """Register a new workspace and scan for projects."""
    orchestrator = get_orchestrator()

    try:
        workspace, projects = orchestrator.register_workspace(name, path, auto_scan=not no_scan)

        # Apply initial budgets if supplied. Treat 0 as "unset" to match the
        # `workspace budget` command semantics.
        if daily is not None or monthly is not None:
            if daily is not None:
                workspace.daily_budget_usd = daily if daily > 0 else None
            if monthly is not None:
                workspace.monthly_budget_usd = monthly if monthly > 0 else None
            orchestrator.store.update_workspace(workspace)

        console.print(f"[green]✓[/green] Workspace '{workspace.name}' registered")
        console.print(f"  Path: {workspace.path}")
        console.print(f"  ID: {workspace.id}")
        if workspace.daily_budget_usd is not None:
            console.print(f"  Daily budget: ${workspace.daily_budget_usd:.2f}")
        if workspace.monthly_budget_usd is not None:
            console.print(f"  Monthly budget: ${workspace.monthly_budget_usd:.2f}")

        if projects:
            console.print(f"\n[bold]Discovered {len(projects)} project(s):[/bold]")
            for p in projects:
                console.print(f"  • {p.name}")
        elif not no_scan:
            console.print(
                "\n[dim]No projects discovered. Projects need markers like "
                "package.json, pyproject.toml, .git, etc.[/dim]"
            )

    except WorkspaceExistsError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@workspace_app.command("list")
def workspace_list():
    """List all registered workspaces."""
    orchestrator = get_orchestrator()
    workspaces = orchestrator.list_workspaces()

    if not workspaces:
        console.print("[dim]No workspaces registered.[/dim]")
        console.print("Use 'gluon workspace add <name> <path>' to register a workspace.")
        return

    table = Table(title="Workspaces")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    table.add_column("Projects", justify="right")
    table.add_column("Daily budget", justify="right")
    table.add_column("Monthly budget", justify="right")
    table.add_column("Auto-discover")
    table.add_column("ID", style="dim")

    for ws in workspaces:
        projects = orchestrator.list_workspace_projects(ws.name)
        daily_str = f"${ws.daily_budget_usd:.2f}" if ws.daily_budget_usd is not None else "—"
        monthly_str = f"${ws.monthly_budget_usd:.2f}" if ws.monthly_budget_usd is not None else "—"
        table.add_row(
            ws.name,
            str(ws.path),
            str(len(projects)),
            daily_str,
            monthly_str,
            "Yes" if ws.auto_discover else "No",
            ws.id[:8],
        )

    console.print(table)


@workspace_app.command("remove")
def workspace_remove(
    name: Annotated[str, typer.Argument(help="Workspace name or ID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
    with_projects: Annotated[bool, typer.Option("--with-projects", help="Also remove all projects")] = False,
):
    """Remove a workspace."""
    orchestrator = get_orchestrator()

    try:
        workspace = orchestrator.get_workspace(name)
    except WorkspaceNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    projects = orchestrator.list_workspace_projects(name)
    msg = f"Remove workspace '{workspace.name}'?"
    if with_projects and projects:
        msg += f" This will also remove {len(projects)} project(s)."

    if not force:
        confirm = typer.confirm(msg)
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(0)

    if orchestrator.remove_workspace(name, remove_projects=with_projects):
        console.print(f"[green]✓[/green] Workspace '{workspace.name}' removed")
        if with_projects and projects:
            console.print(f"  Also removed {len(projects)} project(s)")
    else:
        console.print("[red]Error:[/red] Failed to remove workspace")
        raise typer.Exit(1)


@workspace_app.command("scan")
def workspace_scan(
    name: Annotated[str | None, typer.Argument(help="Workspace name (optional, scans all if not specified)")] = None,
):
    """Scan workspace(s) for new projects."""
    orchestrator = get_orchestrator()

    if name:
        try:
            new_projects = orchestrator.scan_workspace(name)
            if new_projects:
                console.print(f"[green]✓[/green] Found {len(new_projects)} new project(s) in '{name}':")
                for p in new_projects:
                    console.print(f"  • {p.name}")
            else:
                console.print(f"[dim]No new projects found in '{name}'[/dim]")
        except WorkspaceNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    else:
        results = orchestrator.refresh_all_workspaces()
        if results:
            total = sum(len(p) for p in results.values())
            console.print(f"[green]✓[/green] Found {total} new project(s):")
            for ws_name, projects in results.items():
                console.print(f"\n[bold]{ws_name}:[/bold]")
                for p in projects:
                    console.print(f"  • {p.name}")
        else:
            console.print("[dim]No new projects found in any workspace[/dim]")


@workspace_app.command("projects")
def workspace_projects(
    name: Annotated[str, typer.Argument(help="Workspace name or ID")],
):
    """List all projects in a workspace."""
    orchestrator = get_orchestrator()

    try:
        workspace = orchestrator.get_workspace(name)
        projects = orchestrator.list_workspace_projects(name)
    except WorkspaceNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not projects:
        console.print(f"[dim]No projects in workspace '{workspace.name}'.[/dim]")
        console.print("Use 'gluon workspace scan' to discover projects.")
        return

    table = Table(title=f"Projects in '{workspace.name}'")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    table.add_column("Sessions", justify="right")

    for p in projects:
        sessions = orchestrator.list_sessions(p.name)
        table.add_row(p.name, str(p.path), str(len(sessions)))

    console.print(table)


@workspace_app.command("budget")
def workspace_budget(
    name: Annotated[str, typer.Argument(help="Workspace name or ID")],
    daily: Annotated[
        float | None,
        typer.Option("--daily", help="Daily rolling budget in USD (0 to clear)"),
    ] = None,
    monthly: Annotated[
        float | None,
        typer.Option("--monthly", help="Monthly rolling budget in USD (0 to clear)"),
    ] = None,
) -> None:
    """Set or clear daily/monthly rolling cost budgets for a workspace.

    Pass 0 to clear a budget. When neither flag is supplied, the current
    budgets are printed (use `workspace show` for richer output).
    """
    orchestrator = get_orchestrator()

    try:
        workspace = orchestrator.get_workspace(name)
    except WorkspaceNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if daily is None and monthly is None:
        console.print(f"[bold cyan]{workspace.name}[/bold cyan] budgets")
        if workspace.daily_budget_usd is not None:
            daily_str = f"${workspace.daily_budget_usd:.2f}"
        else:
            daily_str = "[dim]unset[/dim]"
        if workspace.monthly_budget_usd is not None:
            monthly_str = f"${workspace.monthly_budget_usd:.2f}"
        else:
            monthly_str = "[dim]unset[/dim]"
        console.print(f"  Daily:   {daily_str}")
        console.print(f"  Monthly: {monthly_str}")
        console.print("\n[dim]Use --daily/--monthly to change; pass 0 to clear.[/dim]")
        return

    changes: list[str] = []
    if daily is not None:
        if daily == 0:
            workspace.daily_budget_usd = None
            changes.append("daily=cleared")
        else:
            workspace.daily_budget_usd = daily
            changes.append(f"daily=${daily:.2f}")
    if monthly is not None:
        if monthly == 0:
            workspace.monthly_budget_usd = None
            changes.append("monthly=cleared")
        else:
            workspace.monthly_budget_usd = monthly
            changes.append(f"monthly=${monthly:.2f}")

    orchestrator.store.update_workspace(workspace)
    console.print(f"[green]✓[/green] Updated workspace [cyan]{workspace.name}[/cyan]: {', '.join(changes)}")


@workspace_app.command("show")
def workspace_show(
    name: Annotated[str, typer.Argument(help="Workspace name or ID")],
) -> None:
    """Show workspace detail including current-period spend vs budgets."""
    orchestrator = get_orchestrator()

    try:
        workspace = orchestrator.get_workspace(name)
    except WorkspaceNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    projects = orchestrator.list_workspace_projects(workspace.name)
    store = orchestrator.store

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    spent_today = store.get_workspace_daily_spend(workspace.id, now)
    spent_month = store.get_workspace_monthly_spend(workspace.id, now)

    console.print(f"[bold cyan]{workspace.name}[/bold cyan] (id [dim]{workspace.id}[/dim])")
    console.print(f"  Path: {workspace.path}")
    console.print(f"  Projects: {len(projects)}")
    console.print(f"  Auto-discover: {'yes' if workspace.auto_discover else 'no'}")

    # Daily
    if workspace.daily_budget_usd is not None:
        pct = (spent_today / workspace.daily_budget_usd) * 100 if workspace.daily_budget_usd > 0 else 0
        color = "green" if pct < 80 else ("yellow" if pct < 100 else "red")
        console.print(
            f"  Spend today: [{color}]${spent_today:.2f}[/{color}] / ${workspace.daily_budget_usd:.2f} ({pct:.1f}%)"
        )
    else:
        console.print(f"  Spend today: ${spent_today:.2f} (no cap)")

    # Monthly
    if workspace.monthly_budget_usd is not None:
        pct = (spent_month / workspace.monthly_budget_usd) * 100 if workspace.monthly_budget_usd > 0 else 0
        color = "green" if pct < 80 else ("yellow" if pct < 100 else "red")
        console.print(
            f"  Spend this month: [{color}]${spent_month:.2f}[/{color}] / "
            f"${workspace.monthly_budget_usd:.2f} ({pct:.1f}%)"
        )
    else:
        console.print(f"  Spend this month: ${spent_month:.2f} (no cap)")


# ========== Git Commands ==========


@git_app.command("status")
def git_status(
    project: Annotated[str | None, typer.Argument(help="Project name (optional, shows all if not specified)")] = None,
):
    """Show git status for project(s)."""
    store = GluonStore()
    git_manager = GitManager(store=store)
    orchestrator = get_orchestrator()

    if project:
        try:
            proj = orchestrator.get_project(project)
            projects = [proj]
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    else:
        projects = orchestrator.list_projects()

    if not projects:
        console.print("[dim]No projects registered.[/dim]")
        return

    table = Table(title="Git Status")
    table.add_column("Project", style="cyan")
    table.add_column("Branch")
    table.add_column("Remote")
    table.add_column("Status")
    table.add_column("Ahead/Behind")
    table.add_column("Last Fetch", style="dim")

    for proj in projects:
        status = git_manager.get_cached_status(proj)

        if not status or not status.is_git_repo:
            table.add_row(proj.name, "-", "-", "[dim]Not a git repo[/dim]", "-", "-")
            continue

        # Status indicator
        if status.is_diverged:
            status_text = "[red]Diverged[/red]"
        elif status.has_uncommitted:
            status_text = f"[yellow]{status.uncommitted_count} uncommitted[/yellow]"
        elif status.is_clean:
            status_text = "[green]Clean[/green]"
        else:
            status_text = "[yellow]Changes pending[/yellow]"

        # Ahead/behind
        ahead_behind = ""
        if status.commits_ahead > 0:
            ahead_behind += f"[green]+{status.commits_ahead}[/green]"
        if status.commits_behind > 0:
            if ahead_behind:
                ahead_behind += "/"
            ahead_behind += f"[yellow]-{status.commits_behind}[/yellow]"
        if not ahead_behind:
            ahead_behind = "[dim]-[/dim]"

        # Last fetch time
        last_fetch = "-"
        if status.last_fetch_at:
            last_fetch = status.last_fetch_at.strftime("%Y-%m-%d %H:%M")

        table.add_row(
            proj.name,
            status.branch or "-",
            status.remote or "-",
            status_text,
            ahead_behind,
            last_fetch,
        )

    console.print(table)


@git_app.command("fetch")
def git_fetch(
    project: Annotated[str | None, typer.Argument(help="Project name (optional, fetches all if not specified)")] = None,
):
    """Fetch latest changes from remote for project(s)."""
    store = GluonStore()
    git_manager = GitManager(store=store)
    orchestrator = get_orchestrator()

    if project:
        try:
            proj = orchestrator.get_project(project)
            projects = [proj]
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    else:
        projects = orchestrator.list_projects()

    if not projects:
        console.print("[dim]No projects registered.[/dim]")
        return

    async def _fetch():
        for proj in projects:
            console.print(f"Fetching {proj.name}...", end=" ")
            status = await git_manager.refresh_status(proj)

            if not status.is_git_repo:
                console.print("[dim]skipped (not a git repo)[/dim]")
                continue

            if status.is_diverged:
                console.print(f"[red]diverged[/red] ({status.commits_ahead} ahead, {status.commits_behind} behind)")
            elif status.commits_behind > 0:
                console.print(f"[yellow]{status.commits_behind} commits behind[/yellow]")
            elif status.commits_ahead > 0:
                console.print(f"[green]{status.commits_ahead} commits ahead[/green]")
            else:
                console.print("[green]up to date[/green]")

    anyio.run(_fetch)


@git_app.command("sync")
def git_sync(
    project: Annotated[str, typer.Argument(help="Project name")],
):
    """Sync a project: commit uncommitted changes, fetch, and fast-forward."""
    store = GluonStore()
    git_manager = GitManager(store=store)
    orchestrator = get_orchestrator()

    try:
        proj = orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    async def _sync():
        console.print(f"Syncing {proj.name}...")
        result = await git_manager.pre_task_sync(proj)

        if not result.success:
            console.print(f"[red]Error:[/red] {result.error}")
            raise typer.Exit(1)

        if result.action == "none":
            console.print(f"[green]✓[/green] {result.message}")
        else:
            console.print(f"[green]✓[/green] {result.message}")
            if result.files_committed > 0:
                console.print(f"  Committed {result.files_committed} files")
            if result.commits_pulled > 0:
                console.print(f"  Pulled {result.commits_pulled} commits")

    anyio.run(_sync)


@git_app.command("push")
def git_push(
    project: Annotated[str, typer.Argument(help="Project name")],
    message: Annotated[str | None, typer.Option("--message", "-m", help="Commit message")] = None,
):
    """Commit any uncommitted changes and push to remote."""
    store = GluonStore()
    git_manager = GitManager(store=store)
    orchestrator = get_orchestrator()

    try:
        proj = orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    async def _push():
        console.print(f"Pushing {proj.name}...")
        commit_msg = message or "gluon: manual push"
        result = await git_manager.post_task_sync(proj, commit_msg)

        if not result.success:
            console.print(f"[red]Error:[/red] {result.error}")
            raise typer.Exit(1)

        if result.action == "none":
            console.print(f"[green]✓[/green] {result.message}")
        else:
            console.print(f"[green]✓[/green] {result.message}")
            if result.files_committed > 0:
                console.print(f"  Committed {result.files_committed} files")
            if result.commits_pushed > 0:
                console.print("  Pushed to remote")

    anyio.run(_push)


# ========== MCP Commands ==========


@mcp_app.command("status")
def mcp_status(
    project: Annotated[str | None, typer.Argument(help="Optional project name to check project-level config")] = None,
):
    """
    Show MCP server configuration and test connectivity.

    Checks which MCP config file would be used and tests each server's reachability.
    """
    import json
    import urllib.error
    import urllib.request

    from gluon.agent import find_mcp_config

    # Determine working directory
    working_dir = None
    if project:
        orchestrator = get_orchestrator()
        try:
            proj = orchestrator.get_project(project)
            working_dir = proj.expanded_path
            console.print(f"[dim]Project:[/dim] {project} ({working_dir})")
        except ProjectNotFoundError:
            console.print(f"[yellow]Warning:[/yellow] Project '{project}' not found, using global config")

    # Find MCP config
    mcp_path = find_mcp_config(working_dir)

    if not mcp_path:
        console.print("[yellow]No MCP configuration found.[/yellow]")
        console.print("\nExpected locations:")
        console.print("  • Project: .mcp.json (in project directory)")
        console.print("  • Global: ~/.claude/.mcp.json")
        raise typer.Exit(1)

    console.print(f"\n[bold]MCP Config:[/bold] {mcp_path}")

    # Load and display config
    try:
        with open(mcp_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing MCP config:[/red] {e}")
        raise typer.Exit(1)

    servers = config.get("mcpServers", {})
    if not servers:
        console.print("[yellow]No MCP servers configured.[/yellow]")
        raise typer.Exit(0)

    # Create table for results
    table = Table(title="MCP Servers")
    table.add_column("Server", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("URL/Command")
    table.add_column("Status")

    for name, server in servers.items():
        server_type = server.get("type", "stdio")
        url = server.get("url", "")
        command = server.get("command", "")

        # Test connectivity for HTTP/SSE servers
        status = "[dim]—[/dim]"
        if server_type in ("http", "sse") and url:
            try:
                headers = server.get("headers", {})
                req = urllib.request.Request(url, headers=headers, method="POST")
                # Send minimal MCP request
                req.add_header("Content-Type", "application/json")
                data = json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": 1}).encode()
                with urllib.request.urlopen(req, data=data, timeout=5) as resp:
                    status = f"[green]✓ OK ({resp.status})[/green]"
            except urllib.error.HTTPError as e:
                if e.code in (400, 405, 406):
                    # Server responded, just doesn't like our request format
                    status = "[green]✓ Reachable[/green]"
                else:
                    status = f"[yellow]HTTP {e.code}[/yellow]"
            except urllib.error.URLError as e:
                reason = str(e.reason)
                if "Connection refused" in reason:
                    status = "[red]✗ Connection refused[/red]"
                elif "Name or service not known" in reason or "nodename nor servname" in reason:
                    status = "[red]✗ Host not found[/red]"
                else:
                    status = f"[red]✗ {reason[:30]}[/red]"
            except Exception as e:
                status = f"[red]✗ {str(e)[:30]}[/red]"
        elif server_type == "stdio":
            status = "[dim]stdio (not tested)[/dim]"

        # Add row
        location = url if url else command
        table.add_row(name, server_type, location, status)

    console.print(table)

    # Show tips for common issues
    console.print("\n[bold]Tips:[/bold]")
    console.print(
        "  • For services on your Mac, use [cyan]host.docker.internal[/cyan] instead of [cyan]localhost[/cyan]"
    )
    console.print("  • Project-level [cyan].mcp.json[/cyan] overrides global config")
    console.print("  • Run [cyan]gluon mcp status <project>[/cyan] to check project-specific config")


# ========== Webhook Commands ==========


@webhook_app.command("list")
def webhook_list():
    """List all configured webhooks."""
    store = GluonStore()
    configs = store.list_webhook_configs(enabled_only=False)

    if not configs:
        console.print("[dim]No webhooks configured.[/dim]")
        console.print("Use 'gluon webhook add' to configure a webhook.")
        return

    # Build project lookup
    project_lookup: dict[str, str] = {}
    for p in store.list_projects():
        project_lookup[p.id] = p.name

    table = Table(title="Webhooks")
    table.add_column("ID", style="dim")
    table.add_column("Handler", style="cyan")
    table.add_column("Project")
    table.add_column("Events")
    table.add_column("Enabled")

    for config in configs:
        project_name = project_lookup.get(config.project_id, "All") if config.project_id else "All"
        events_str = ", ".join(config.events) if config.events else "All"
        enabled = "[green]Yes[/green]" if config.enabled else "[red]No[/red]"

        table.add_row(
            config.id[:8],
            config.handler,
            project_name,
            events_str,
            enabled,
        )

    console.print(table)


@webhook_app.command("add")
def webhook_add(
    handler: Annotated[str, typer.Option("--handler", "-h", help="Webhook handler")] = "github",
    project: Annotated[str | None, typer.Option("--project", "-p", help="Project name")] = None,
    events: Annotated[str | None, typer.Option("--events", "-e", help="Event types")] = None,
    branches: Annotated[str | None, typer.Option("--branches", "-b", help="Branch filter")] = None,
    ignore_branches: Annotated[str | None, typer.Option("--ignore-branches", help="Branches to ignore")] = None,
    secret: Annotated[str | None, typer.Option("--secret", "-s", help="Webhook secret")] = None,
):
    """
    Add a new webhook configuration.

    Example:
        gluon webhook add --handler github --project myapp --events push,pull_request
    """
    import secrets as secrets_module

    from gluon.models import WebhookConfig

    store = GluonStore()

    # Resolve project if provided
    project_id = None
    if project:
        proj = store.get_project_by_name(project)
        if not proj:
            console.print(f"[red]Error:[/red] Project not found: {project}")
            raise typer.Exit(1)
        project_id = proj.id

    # Parse event list
    events_list = [e.strip() for e in events.split(",")] if events else []

    # Parse branch filters
    branches_list = [b.strip() for b in branches.split(",")] if branches else None
    ignore_branches_list = [b.strip() for b in ignore_branches.split(",")] if ignore_branches else None

    # Generate secret if not provided
    secret_key = secret or secrets_module.token_hex(32)

    config = WebhookConfig(
        handler=handler,
        project_id=project_id,
        secret_key=secret_key,
        events=events_list,
        branches=branches_list,
        ignore_branches=ignore_branches_list,
    )

    store.create_webhook_config(config)

    console.print("[green]✓[/green] Webhook created")
    console.print(f"  ID: {config.id[:8]}")
    console.print(f"  Handler: {handler}")
    console.print(f"  Project: {project or 'All'}")
    console.print(f"  Events: {', '.join(events_list) if events_list else 'All'}")
    console.print()
    console.print("[bold]Configure in GitHub:[/bold]")
    console.print("  Webhook URL: https://your-gluon-server/api/webhooks/github")
    console.print(f"  Secret: {secret_key}")
    console.print()
    console.print("[dim]Tip: Set GITHUB_WEBHOOK_SECRET env var if using a single secret for all webhooks.[/dim]")


@webhook_app.command("remove")
def webhook_remove(
    webhook_id: Annotated[str, typer.Argument(help="Webhook ID (use 'gluon webhook list' to see IDs)")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
):
    """Remove a webhook configuration."""
    store = GluonStore()

    # Find webhook by ID prefix
    config = store.get_webhook_config(webhook_id)
    if not config:
        # Try partial ID match
        configs = store.list_webhook_configs(enabled_only=False)
        for c in configs:
            if c.id.startswith(webhook_id):
                config = c
                break

    if not config:
        console.print(f"[red]Error:[/red] Webhook not found: {webhook_id}")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Remove webhook {config.id[:8]} ({config.handler})?")
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(0)

    if store.delete_webhook_config(config.id):
        console.print(f"[green]✓[/green] Webhook {config.id[:8]} removed")
    else:
        console.print("[red]Error:[/red] Failed to remove webhook")
        raise typer.Exit(1)


@webhook_app.command("enable")
def webhook_enable(
    webhook_id: Annotated[str, typer.Argument(help="Webhook ID")],
):
    """Enable a webhook."""
    store = GluonStore()

    config = store.get_webhook_config(webhook_id)
    if not config:
        # Try partial ID match
        configs = store.list_webhook_configs(enabled_only=False)
        for c in configs:
            if c.id.startswith(webhook_id):
                config = c
                break

    if not config:
        console.print(f"[red]Error:[/red] Webhook not found: {webhook_id}")
        raise typer.Exit(1)

    config.enabled = True
    store.update_webhook_config(config)
    console.print(f"[green]✓[/green] Webhook {config.id[:8]} enabled")


@webhook_app.command("disable")
def webhook_disable(
    webhook_id: Annotated[str, typer.Argument(help="Webhook ID")],
):
    """Disable a webhook."""
    store = GluonStore()

    config = store.get_webhook_config(webhook_id)
    if not config:
        # Try partial ID match
        configs = store.list_webhook_configs(enabled_only=False)
        for c in configs:
            if c.id.startswith(webhook_id):
                config = c
                break

    if not config:
        console.print(f"[red]Error:[/red] Webhook not found: {webhook_id}")
        raise typer.Exit(1)

    config.enabled = False
    store.update_webhook_config(config)
    console.print(f"[yellow]✓[/yellow] Webhook {config.id[:8]} disabled")


# ========== Execution Commands ==========


@app.command("run")
def run(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    prompt: Annotated[str, typer.Argument(help="Prompt for Claude")],
    new_session: Annotated[bool, typer.Option("--new", "-n", help="Force new session")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only show final result")] = False,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model: opus-4.8/4.7/4.6/sonnet/haiku")] = None,
    background: Annotated[bool, typer.Option("--background", "-b", help="Run in background")] = False,
    worktree: Annotated[bool, typer.Option("--worktree", "-w", help="Execute in isolated Git worktree")] = False,
    ralph: Annotated[bool, typer.Option("--ralph", "-r", help="Enable ralph loop mode")] = False,
    max_loops: Annotated[int, typer.Option("--max-loops", help="Max loop iterations (ralph mode)")] = 50,
    max_calls: Annotated[int, typer.Option("--max-calls", help="Max API calls per hour (ralph mode)")] = 100,
    max_cost: Annotated[float | None, typer.Option("--max-cost", help="Max cost in USD (ralph mode)")] = None,
    verify_cmd: Annotated[
        str | None,
        typer.Option(
            "--verify-cmd",
            help="Objective gate command for ralph loops (e.g. 'uv run pytest'); marks the run 'gated'.",
        ),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-P", help="Task profile: quick/standard/deep/planning"),
    ] = None,
    thinking: Annotated[
        str | None,
        typer.Option("--thinking", help="Thinking budget: none/low/medium/high/ultrathink/adaptive"),
    ] = None,
    effort: Annotated[
        EffortLevel | None,
        typer.Option("--effort", help="Reasoning effort: low/medium/high/xhigh/max"),
    ] = None,
    planning: Annotated[bool, typer.Option("--planning", help="Force planning mode")] = False,
    task_budget: Annotated[
        int | None,
        typer.Option("--task-budget", help="Token budget for task (model paces itself to finish within budget)"),
    ] = None,
    no_hydrate: Annotated[bool, typer.Option("--no-hydrate", help="Disable pre-hydration of project context")] = False,
    no_validate: Annotated[
        bool, typer.Option("--no-validate", help="Disable lint+test validation after completion")
    ] = False,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Agent name (or ID prefix) to link this run to. "
            "Auto-selects if the project's workspace has exactly one active agent.",
        ),
    ] = None,
    approval_policy: Annotated[
        str,
        typer.Option(
            "--approval-policy",
            help="Approval policy: permissive (default) / careful / paranoid. "
            "CAREFUL gates known-destructive Bash (rm -rf, git push --force, "
            "npm publish, etc.). PARANOID gates ALL Bash + writes.",
        ),
    ] = "permissive",
    max_tool_calls: Annotated[
        int | None,
        typer.Option(
            "--max-tool-calls",
            help="Hard cap on total tool calls. Run aborts with deny once reached.",
        ),
    ] = None,
    max_duration: Annotated[
        int | None,
        typer.Option(
            "--max-duration",
            help="Hard cap on wall-clock runtime in minutes. Run cancelled when exceeded.",
        ),
    ] = None,
):
    """Execute a task on a project.

    Use --profile to select a task profile (quick/standard/deep/planning).
    Use --ralph for autonomous loop mode that iterates until completion.

    Profiles bundle model + thinking budget + cost limits:
      quick    - Haiku, no thinking, $0.50 budget
      standard - Sonnet, 10k thinking, $3 budget (default)
      deep     - Opus, 32k thinking, $15 budget
      planning - Opus, plan before executing
    """
    from gluon.core import AgentAmbiguousError, AgentNotFoundError

    orchestrator = get_orchestrator()

    try:
        proj = orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Resolve the agent (explicit or auto-selected)
    resolved_agent_id: str | None = None
    try:
        resolved_agent_id = orchestrator.resolve_agent(agent, proj.workspace_id)
    except (AgentNotFoundError, AgentAmbiguousError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None

    if resolved_agent_id is not None:
        resolved = orchestrator.store.get_agent(resolved_agent_id)
        if resolved is not None:
            console.print(f"[dim]Agent:[/dim] [cyan]{resolved.name}[/cyan]")

    # Resolve approval policy
    from gluon.models import ApprovalPolicy

    try:
        resolved_approval_policy = ApprovalPolicy(approval_policy.lower())
    except ValueError:
        console.print(
            f"[red]Invalid --approval-policy:[/red] {approval_policy}. "
            f"Must be one of: {[p.value for p in ApprovalPolicy]}"
        )
        raise typer.Exit(code=1) from None
    if resolved_approval_policy != ApprovalPolicy.PERMISSIVE:
        console.print(f"[dim]Approval policy:[/dim] [yellow]{resolved_approval_policy.value}[/yellow]")
    if max_tool_calls is not None:
        console.print(f"[dim]Hard cap:[/dim] max {max_tool_calls} tool calls")
    if max_duration is not None:
        console.print(f"[dim]Hard cap:[/dim] max {max_duration} minute(s) wall-clock")

    # Validate model if provided
    model_tier: ModelTier | None = _validate_model(model)

    # Background execution mode
    if background:
        runner = TaskRunner()

        async def _submit():
            run_obj = await runner.submit(
                proj.id,
                prompt,
                wait=False,
                use_worktree=worktree,
                model=model_tier.value if model_tier else None,
                ralph_enabled=ralph,
                max_loops=max_loops,
                max_calls_per_hour=max_calls,
                max_cost_usd=max_cost,
                verify_cmd=verify_cmd,
                profile=profile,
                thinking_budget=thinking,
                force_planning=planning if planning else None,
                effort=effort,
                task_budget=task_budget,
                enable_prehydration=not no_hydrate,
                blueprint_enabled=not no_validate,
                agent_id=resolved_agent_id,
                approval_policy=resolved_approval_policy,
                max_tool_calls=max_tool_calls,
                max_duration_minutes=max_duration,
            )
            console.print(f"[green]✓[/green] Task submitted: [cyan]{run_obj.id[:8]}[/cyan]")
            console.print(f"  Project: {project}")
            console.print(f"  Prompt: {prompt[:60]}{'...' if len(prompt) > 60 else ''}")
            if profile:
                console.print(f"  [blue]Profile:[/blue] {profile}")
            if ralph:
                console.print(f"  [blue]Ralph mode:[/blue] max {max_loops} loops, {max_calls} calls/hr")
                if max_cost:
                    console.print(f"  Cost cap: ${max_cost:.2f}")
            if no_hydrate:
                console.print("  [blue]Pre-hydration:[/blue] disabled")
            if no_validate:
                console.print("  [blue]Blueprint validation:[/blue] disabled")
            console.print()
            console.print("[dim]Use 'gluon runs' to check status[/dim]")
            console.print(f"[dim]Use 'gluon logs {run_obj.id[:8]}' to view logs[/dim]")
            if ralph:
                console.print(f"[dim]Use 'gluon ralph status {run_obj.id[:8]}' for loop details[/dim]")

        try:
            anyio.run(_submit)
        except BudgetExceededError as e:
            console.print(f"[red]Budget exceeded:[/red] {e}")
            raise typer.Exit(1) from None
        except WorkspaceBudgetExceededError as e:
            console.print(f"[red]Workspace budget exceeded:[/red] {e}")
            raise typer.Exit(1) from None
        return

    # Foreground execution (existing behavior)
    async def _run():
        result: AgentResult | None = None

        console.print(f"[bold]Running on project:[/bold] {project}")
        console.print(f"[bold]Prompt:[/bold] {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        if profile:
            console.print(f"[bold]Profile:[/bold] {profile}")
        if model_tier:
            console.print(f"[bold]Model:[/bold] {model_tier.value}")
        if thinking:
            console.print(f"[bold]Thinking:[/bold] {thinking}")
        if effort:
            console.print(f"[bold]Effort:[/bold] {effort}")
        if planning:
            console.print("[bold]Planning:[/bold] enabled (plan before executing)")
        if worktree:
            console.print("[bold]Worktree:[/bold] enabled (isolated execution)")
        console.print()

        async for item in orchestrator.execute(
            project,
            prompt,
            force_new_session=new_session,
            model=model_tier,
            use_worktree=worktree,
            initiator="cli:foreground",
            profile=profile,
            thinking_budget=thinking,
            force_planning=planning if planning else None,
            effort=effort,
            task_budget=task_budget,
            agent_id=resolved_agent_id,
            approval_policy=resolved_approval_policy,
            max_tool_calls=max_tool_calls,
            max_duration_minutes=max_duration,
        ):
            if isinstance(item, AgentMessage):
                if not quiet:
                    _print_message(item)
            elif isinstance(item, AgentResult):
                result = item

        if result:
            console.print()
            _print_result(result)

    try:
        anyio.run(_run)
    except BudgetExceededError as e:
        console.print(f"[red]Budget exceeded:[/red] {e}")
        raise typer.Exit(1) from None
    except WorkspaceBudgetExceededError as e:
        console.print(f"[red]Workspace budget exceeded:[/red] {e}")
        raise typer.Exit(1) from None


@app.command("resume")
def resume(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    prompt: Annotated[str | None, typer.Argument(help="Optional follow-up prompt")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only show final result")] = False,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model: opus-4.8/4.7/4.6/sonnet/haiku")] = None,
):
    """Resume the last session for a project."""
    orchestrator = get_orchestrator()

    try:
        orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Validate model if provided
    model_tier: ModelTier | None = _validate_model(model)

    async def _resume():
        result: AgentResult | None = None

        console.print(f"[bold]Resuming session for:[/bold] {project}")
        if prompt:
            console.print(f"[bold]Prompt:[/bold] {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        if model_tier:
            console.print(f"[bold]Model:[/bold] {model_tier.value}")
        console.print()

        try:
            async for item in orchestrator.resume(project, prompt, model=model_tier):
                if isinstance(item, AgentMessage):
                    if not quiet:
                        _print_message(item)
                elif isinstance(item, AgentResult):
                    result = item

            if result:
                console.print()
                _print_result(result)

        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            console.print("[dim]Tip: Use 'gluon run' to start a new session.[/dim]")
            raise typer.Exit(1)

    anyio.run(_resume)


@app.command("recover")
def recover(
    run_id: Annotated[str, typer.Argument(help="Run ID to recover (full or short ID)")],
    fresh: Annotated[bool, typer.Option("--fresh", help="Start completely fresh session")] = False,
    wait: Annotated[bool, typer.Option("--wait", "-w", help="Wait for completion")] = True,
):
    """
    Recover a run that failed due to context overflow.

    Extracts progress from the failed run's logs and starts a fresh session
    with a summary of completed work.

    Example:
        gluon recover 424b9a8e
        gluon recover abc12345 --fresh
    """
    orchestrator = get_orchestrator()
    runner = TaskRunner(store=orchestrator.store)

    # Find the run
    run = orchestrator.store.get_run_by_short_id(run_id)
    if not run:
        run = orchestrator.store.get_run(run_id)

    if not run:
        console.print(f"[red]Error:[/red] Run not found: {run_id}")
        raise typer.Exit(1)

    # Check if it looks like a context overflow failure
    error_msg = (run.error_message or "").lower()
    is_context_overflow = "context" in error_msg or "too long" in error_msg or "overflow" in error_msg

    if not is_context_overflow and run.status != RunStatus.FAILED:
        console.print(f"[yellow]Warning:[/yellow] Run {run.id[:8]} doesn't appear to have failed from context overflow")
        console.print(f"[dim]Status: {run.status.value}, Error: {run.error_message or 'None'}[/dim]")

    # Get project
    project = orchestrator.store.get_project(run.project_id)
    if not project:
        console.print(f"[red]Error:[/red] Project not found for run: {run.project_id}")
        raise typer.Exit(1)

    console.print(f"[bold]Recovering run:[/bold] {run.id[:8]}")
    console.print(f"[bold]Project:[/bold] {project.name}")
    console.print(f"[bold]Original prompt:[/bold] {run.prompt[:80]}{'...' if len(run.prompt) > 80 else ''}")

    if run.cost_usd:
        console.print(f"[dim]Previous cost: ${run.cost_usd:.4f}[/dim]")

    # Extract recovery state
    recovery_state = runner._extract_recovery_state(run)
    completed = recovery_state.get("completed_work", [])
    console.print(f"[dim]Completed tasks found: {len(completed)}[/dim]")
    if completed:
        for task in completed[:5]:  # Show first 5
            console.print(f"  [green]✓[/green] {task[:60]}{'...' if len(task) > 60 else ''}")
        if len(completed) > 5:
            console.print(f"  [dim]... and {len(completed) - 5} more[/dim]")

    console.print()

    async def _recover():
        from gluon.models import utc_now

        # Determine working directory
        if run.worktree_path and Path(run.worktree_path).exists():
            working_dir = Path(run.worktree_path)
            console.print(f"[dim]Using worktree: {working_dir}[/dim]")
        else:
            working_dir = project.expanded_path

        # Create recovery run or update existing
        if fresh:
            # Create new run linked to the failed one
            new_run = orchestrator.store.create_run(
                project_id=run.project_id,
                prompt=f"[Recovery from {run.id[:8]}] {run.prompt}",
                initiator="cli:recover",
                use_worktree=run.use_worktree,
                model=run.model,
            )
            new_run.recovery_from_run_id = run.id
            new_run.recovery_count = 1
            new_run.last_recovery_at = utc_now()
            orchestrator.store.update_run(new_run)
            console.print(f"[bold]New recovery run:[/bold] {new_run.id[:8]}")
        else:
            # Update existing run for in-place recovery
            run.recovery_count += 1
            run.last_recovery_at = utc_now()
            run.status = RunStatus.RUNNING
            orchestrator.store.update_run(run)

        # Execute recovery
        agent = GluonAgent(model=run.model) if run.model else GluonAgent()

        console.print("[bold]Starting recovery...[/bold]\n")

        result: AgentResult | None = None
        async for item in agent.resume_with_fresh_context(
            recovery_state=recovery_state,
            working_dir=working_dir,
        ):
            if isinstance(item, AgentMessage):
                _print_message(item)
            elif isinstance(item, AgentResult):
                result = item

        if result:
            console.print()
            _print_result(result)

            # Update run with result
            target_run = orchestrator.store.get_run(new_run.id if fresh else run.id)
            if target_run:
                if result.claude_session_id:
                    target_run.claude_session_id = result.claude_session_id
                target_run.cost_usd = (target_run.cost_usd or 0) + (result.total_cost_usd or 0)
                target_run.input_tokens = (target_run.input_tokens or 0) + (result.input_tokens or 0)
                target_run.output_tokens = (target_run.output_tokens or 0) + (result.output_tokens or 0)
                target_run.model_used = result.model_used

                if result.success:
                    target_run.status = RunStatus.REVIEW
                else:
                    target_run.mark_failed(result.error or "Recovery failed")

                orchestrator.store.update_run(target_run)

    anyio.run(_recover)


# ========== Session Commands ==========


@app.command("sessions")
def sessions(
    project: Annotated[str | None, typer.Argument(help="Project name (optional)")] = None,
):
    """List sessions for a project or all sessions."""
    orchestrator = get_orchestrator()

    try:
        session_list = orchestrator.list_sessions(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not session_list:
        console.print("[dim]No sessions found.[/dim]")
        return

    # Build project lookup for showing project names when listing all sessions
    project_lookup: dict[str, str] = {}
    if not project:
        for p in orchestrator.list_projects():
            project_lookup[p.id] = p.name

    table = Table(title=f"Sessions{f' for {project}' if project else ''}")
    if not project:
        table.add_column("Project", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Status")
    table.add_column("Turns", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Last Prompt")
    table.add_column("Updated")

    for session in session_list:
        status_color = {
            "active": "green",
            "paused": "yellow",
            "completed": "blue",
            "failed": "red",
        }.get(session.status.value, "white")

        row = []
        if not project:
            row.append(project_lookup.get(session.project_id, session.project_id[:8]))
        row.extend(
            [
                session.id[:8],
                f"[{status_color}]{session.status.value}[/{status_color}]",
                str(session.total_turns),
                f"${session.total_cost_usd:.4f}",
                (session.last_prompt or "")[:40]
                + ("..." if session.last_prompt and len(session.last_prompt) > 40 else ""),
                session.updated_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )

        table.add_row(*row)

    console.print(table)


@app.command("session")
def session_show(
    session_id: Annotated[str, typer.Argument(help="Session ID (can use short prefix)")],
):
    """Show detail for a session including linked runs."""
    store = GluonStore()

    session = store.get_session_by_short_id(session_id) or store.get_session(session_id)
    if not session:
        console.print(f"[red]Error:[/red] Session not found: {session_id}")
        raise typer.Exit(1)

    project = store.get_project(session.project_id)
    proj_name = project.name if project else session.project_id[:8]

    status_color = {
        "active": "green",
        "paused": "yellow",
        "completed": "blue",
        "failed": "red",
    }.get(session.status.value, "white")

    console.print(
        Panel.fit(
            f"[bold]ID:[/bold] {session.id}\n"
            f"[bold]Project:[/bold] {proj_name}\n"
            f"[bold]Status:[/bold] [{status_color}]{session.status.value}[/{status_color}]\n"
            f"[bold]Turns:[/bold] {session.total_turns}\n"
            f"[bold]Cost:[/bold] ${session.total_cost_usd:.4f}\n"
            f"[bold]Last Prompt:[/bold] {(session.last_prompt or '')[:80]}\n"
            f"[bold]Created:[/bold] {session.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"[bold]Updated:[/bold] {session.updated_at.strftime('%Y-%m-%d %H:%M')}"
            + (f"\n[bold]Claude Session:[/bold] {session.claude_session_id}" if session.claude_session_id else ""),
            title="Session Detail",
        )
    )

    # Show linked runs
    if session.claude_session_id:
        linked_runs = store.list_runs_by_claude_session(session.claude_session_id)
        if linked_runs:
            table = Table(title="Linked Runs")
            table.add_column("ID", style="cyan")
            table.add_column("Status")
            table.add_column("Prompt")
            table.add_column("Created", style="dim")

            for run in linked_runs:
                from gluon.runner import format_run_status

                emoji, color = format_run_status(run.status, None)
                table.add_row(
                    run.id[:8],
                    f"[{color}]{emoji} {run.status.value}[/{color}]",
                    (run.prompt[:40] + "...") if len(run.prompt) > 40 else run.prompt,
                    run.created_at.strftime("%Y-%m-%d %H:%M"),
                )

            console.print(table)


@app.command("status")
def status():
    """Show overall status."""
    from gluon.llm_provider import get_provider, get_provider_source

    orchestrator = get_orchestrator()
    status_info = orchestrator.status()
    provider = get_provider()

    console.print(
        Panel.fit(
            f"[bold]Projects:[/bold] {status_info['total_projects']}\n"
            f"[bold]Active Sessions:[/bold] {status_info['active_sessions']}\n"
            f"[bold]LLM Provider:[/bold] {provider.name} [dim]({get_provider_source()})[/dim]",
            title="Gluon Status",
        )
    )

    if status_info["projects"]:
        table = Table()
        table.add_column("Project")
        table.add_column("Sessions", justify="right")

        for p in status_info["projects"]:
            table.add_row(p["name"], str(p["sessions"]))

        console.print(table)


# ========== Bot Commands ==========


@app.command("bot")
def bot(
    token: Annotated[str | None, typer.Option("--token", "-t", help="Telegram bot token")] = None,
    users: Annotated[str | None, typer.Option("--users", "-u", help="Comma-separated allowed user IDs")] = None,
):
    """
    Run Telegram bot interface.

    Set GLUON_TELEGRAM_TOKEN env var or use --token.
    Set GLUON_TELEGRAM_USERS env var or use --users to restrict access.

    To get a bot token:
    1. Message @BotFather on Telegram
    2. Send /newbot and follow instructions
    3. Copy the token

    To get your user ID:
    1. Message @userinfobot on Telegram
    2. It will reply with your user ID
    """
    import asyncio
    from pathlib import Path

    from dotenv import load_dotenv

    from gluon.bot_core import GluonBotCore

    # Load .env.local for AWS Bedrock configuration
    env_path = Path(__file__).parent.parent / ".env.local"
    if env_path.exists():
        load_dotenv(env_path)

    # Get token
    bot_token = token or os.environ.get("GLUON_TELEGRAM_TOKEN")
    if not bot_token:
        console.print("[red]Error:[/red] Telegram bot token required.")
        console.print("Set GLUON_TELEGRAM_TOKEN environment variable or use --token.")
        raise typer.Exit(1)

    # Get allowed users from env if not provided
    allowed_users: list[int] | None = None
    if users:
        allowed_users = [int(u.strip()) for u in users.split(",") if u.strip()]
    else:
        users_env = os.environ.get("GLUON_TELEGRAM_USERS", "")
        if users_env:
            allowed_users = [int(u.strip()) for u in users_env.split(",") if u.strip()]

    try:
        console.print("[bold]Starting Gluon Telegram Bot (TelegramTransport)...[/bold]")
        console.print("[dim]Press Ctrl+C to stop[/dim]")

        bot_core = GluonBotCore()

        async def _run_telegram():
            from gluon.transport.telegram import TelegramTransport

            approval_chat_env = os.environ.get("GLUON_TELEGRAM_APPROVAL_CHAT")
            approval_chat_id = int(approval_chat_env) if approval_chat_env else None

            transport = TelegramTransport(
                bot_token,
                bot_core,
                allowed_users,
                approval_chat_id=approval_chat_id,
            )
            bot_core.notifier.transports[transport.name] = transport
            await transport.start()
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass

        asyncio.run(_run_telegram())
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped.[/yellow]")


@app.command("discord")
def discord_bot(
    token: Annotated[str | None, typer.Option("--token", "-t", help="Discord bot token")] = None,
    guild: Annotated[int | None, typer.Option("--guild", "-g", help="Discord guild (server) ID")] = None,
    users: Annotated[str | None, typer.Option("--users", "-u", help="Comma-separated allowed user IDs")] = None,
):
    """
    Run Discord bot interface.

    Set GLUON_DISCORD_TOKEN env var or use --token.
    Set GLUON_DISCORD_GUILD env var or use --guild.
    Set GLUON_DISCORD_USERS env var or use --users to restrict access.

    To create a Discord bot:
    1. Go to https://discord.com/developers/applications
    2. Create New Application and add a Bot
    3. Copy the bot token
    4. Enable MESSAGE CONTENT INTENT in Bot settings
    5. Invite to your server with bot + applications.commands scopes

    To get your user ID:
    1. Enable Developer Mode in Discord settings
    2. Right-click your name and Copy ID
    """
    import os

    try:
        from gluon.transport.discord import DiscordTransport

        _ = DiscordTransport  # Verify import succeeded
    except ImportError as exc:
        console.print(f"[red]Error:[/red] Discord support not installed (missing: [yellow]{exc.name or exc}[/yellow]).")
        console.print("Install with: [cyan]pip install 'gluon-agent[discord]'[/cyan]")
        raise typer.Exit(1)

    from gluon.bot_core import GluonBotCore

    # Get token
    bot_token = token or os.environ.get("GLUON_DISCORD_TOKEN")
    if not bot_token:
        console.print("[red]Error:[/red] Discord bot token required.")
        console.print("Set GLUON_DISCORD_TOKEN env var or use --token.")
        raise typer.Exit(1)

    # Get guild ID
    guild_id = guild or int(os.environ.get("GLUON_DISCORD_GUILD", "0"))
    if not guild_id:
        console.print("[red]Error:[/red] Discord guild ID required.")
        console.print("Set GLUON_DISCORD_GUILD env var or use --guild.")
        raise typer.Exit(1)

    # Get allowed users
    allowed_users: list[int] | None = None
    users_str = users or os.environ.get("GLUON_DISCORD_USERS", "")
    if users_str:
        allowed_users = [int(u.strip()) for u in users_str.split(",") if u.strip()]

    try:
        console.print("[bold]Starting Gluon Discord Bot...[/bold]")
        console.print(f"[dim]Guild ID: {guild_id}[/dim]")
        console.print("[dim]Press Ctrl+C to stop[/dim]")

        bot_core = GluonBotCore()

        async def _run_discord():
            from gluon.transport.discord import DiscordTransport

            approval_channel_env = os.environ.get("GLUON_DISCORD_APPROVAL_CHANNEL")
            approval_channel_id = int(approval_channel_env) if approval_channel_env else None

            transport = DiscordTransport(
                bot_token,
                guild_id,
                bot_core,
                allowed_users,
                approval_channel_id=approval_channel_id,
            )
            bot_core.notifier.transports[transport.name] = transport
            await transport.start()

        anyio.run(_run_discord)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Discord bot stopped.[/yellow]")


@app.command("serve")
def serve(
    telegram: Annotated[bool, typer.Option("--telegram", help="Enable Telegram transport")] = False,
    discord: Annotated[bool, typer.Option("--discord", help="Enable Discord transport")] = False,
    web: Annotated[bool, typer.Option("--web", help="Enable web dashboard")] = False,
    web_port: Annotated[int, typer.Option("--web-port", help="Web dashboard port")] = 45866,
):
    """
    Run multiple bot transports concurrently.

    Example: gluon serve --telegram --discord --web

    Configure each transport with environment variables:
    - Telegram: GLUON_TELEGRAM_TOKEN, GLUON_TELEGRAM_USERS
    - Discord: GLUON_DISCORD_TOKEN, GLUON_DISCORD_GUILD, GLUON_DISCORD_USERS
    - Web: GLUON_SSL_CERTFILE, GLUON_SSL_KEYFILE (optional, for HTTPS)
    """
    import os

    if not telegram and not discord and not web:
        console.print("[red]Error:[/red] At least one transport must be enabled.")
        console.print("Use --telegram, --discord, and/or --web flags.")
        raise typer.Exit(1)

    from gluon.bot_core import GluonBotCore

    # Create shared bot core
    bot_core = GluonBotCore()
    transports_to_run: list[tuple[str, Any]] = []

    # Configure Telegram
    if telegram:
        telegram_token = os.environ.get("GLUON_TELEGRAM_TOKEN")
        if not telegram_token:
            console.print("[yellow]⚠[/yellow] Telegram skipped: GLUON_TELEGRAM_TOKEN not set")
        else:
            telegram_users_str = os.environ.get("GLUON_TELEGRAM_USERS", "")
            telegram_users = (
                [int(u.strip()) for u in telegram_users_str.split(",") if u.strip()] if telegram_users_str else None
            )

            from gluon.transport.telegram import TelegramTransport

            telegram_approval_chat_env = os.environ.get("GLUON_TELEGRAM_APPROVAL_CHAT")
            telegram_approval_chat_id = int(telegram_approval_chat_env) if telegram_approval_chat_env else None

            tg_transport = TelegramTransport(
                telegram_token,
                bot_core,
                telegram_users,
                approval_chat_id=telegram_approval_chat_id,
            )
            transports_to_run.append(("Telegram", tg_transport))
            console.print("[green]✓[/green] Telegram transport configured")

    # Configure Discord
    if discord:
        try:
            from gluon.transport.discord import DiscordTransport

            _ = DiscordTransport  # Verify import succeeded
        except ImportError as exc:
            console.print(f"[yellow]⚠[/yellow] Discord skipped: missing [yellow]{exc.name or exc}[/yellow]")
        else:
            discord_token = os.environ.get("GLUON_DISCORD_TOKEN")
            discord_guild_str = os.environ.get("GLUON_DISCORD_GUILD", "0")
            discord_guild = int(discord_guild_str) if discord_guild_str.isdigit() else 0

            if not discord_token:
                console.print("[yellow]⚠[/yellow] Discord skipped: GLUON_DISCORD_TOKEN not set")
            elif not discord_guild:
                console.print("[yellow]⚠[/yellow] Discord skipped: GLUON_DISCORD_GUILD not set")
            else:
                discord_users_str = os.environ.get("GLUON_DISCORD_USERS", "")
                discord_users = (
                    [int(u.strip()) for u in discord_users_str.split(",") if u.strip()] if discord_users_str else None
                )

                from gluon.transport.discord import DiscordTransport

                discord_approval_channel_env = os.environ.get("GLUON_DISCORD_APPROVAL_CHANNEL")
                discord_approval_channel_id = (
                    int(discord_approval_channel_env) if discord_approval_channel_env else None
                )

                dc_transport = DiscordTransport(
                    discord_token,
                    discord_guild,
                    bot_core,
                    discord_users,
                    approval_channel_id=discord_approval_channel_id,
                )
                transports_to_run.append(("Discord", dc_transport))
                console.print("[green]✓[/green] Discord transport configured")

    # Configure Web dashboard
    web_server = None
    if web:
        try:
            import uvicorn

            # The web dashboard binds 0.0.0.0 below; refuse to expose it
            # unauthenticated to the network.
            from gluon.auth import insecure_bind_error
            from gluon.web import create_app

            bind_error = insecure_bind_error("0.0.0.0")
            if bind_error:
                console.print(f"[red]Error:[/red] {bind_error}")
                raise typer.Exit(1)

            # Share the bot's notifier so web-submitted runs and event-bus
            # question escalation reach the same Telegram/Discord transports.
            web_app = create_app(notifier=bot_core.notifier)

            # Optional HTTPS via SSL certificates
            ssl_certfile = os.environ.get("GLUON_SSL_CERTFILE")
            ssl_keyfile = os.environ.get("GLUON_SSL_KEYFILE")

            ssl_enabled = False
            if ssl_certfile and ssl_keyfile:
                cert_path = Path(ssl_certfile)
                key_path = Path(ssl_keyfile)
                if not cert_path.exists():
                    console.print(f"[red]Error:[/red] SSL certificate not found: {ssl_certfile}")
                    raise typer.Exit(1)
                if not key_path.exists():
                    console.print(f"[red]Error:[/red] SSL key not found: {ssl_keyfile}")
                    raise typer.Exit(1)
                ssl_enabled = True
            elif ssl_certfile or ssl_keyfile:
                console.print(
                    "[yellow]⚠[/yellow] HTTPS skipped: both GLUON_SSL_CERTFILE and GLUON_SSL_KEYFILE must be set"
                )

            config_kwargs: dict[str, Any] = {
                "host": "0.0.0.0",
                "port": web_port,
                "log_level": "warning",
            }
            if ssl_enabled:
                config_kwargs["ssl_certfile"] = ssl_certfile
                config_kwargs["ssl_keyfile"] = ssl_keyfile

            web_server = uvicorn.Server(uvicorn.Config(web_app, **config_kwargs))
            protocol = "HTTPS" if ssl_enabled else "HTTP"
            console.print(f"[green]✓[/green] Web dashboard configured ({protocol}, port {web_port})")
        except ImportError as exc:
            missing = exc.name or exc
            console.print(
                f"[red]Error:[/red] Web dashboard dependency not installed (missing: [yellow]{missing}[/yellow])."
            )
            console.print("Install with: [cyan]pip install 'gluon-agent[web]'[/cyan]")
            raise typer.Exit(1)

    # Verify at least one service is configured
    if not transports_to_run and not web_server:
        console.print("[red]Error:[/red] No services configured.")
        console.print("Check that required environment variables are set for your transports.")
        raise typer.Exit(1)

    # Register transports with notifier so run notifications can reach channels
    for _, transport in transports_to_run:
        bot_core.notifier.transports[transport.name] = transport

    async def _run_all():
        """Run all configured transports concurrently."""
        import asyncio

        service_count = len(transports_to_run) + (1 if web_server else 0)
        console.print(f"\n[bold]Starting {service_count} service(s)...[/bold]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        # Start git background sync once (shared across transports)
        await bot_core.git_manager.start_background_sync()

        try:
            # Run all transports and web server concurrently
            tasks = [transport.start() for _, transport in transports_to_run]
            if web_server:
                tasks.append(web_server.serve())
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            # Stop git sync
            await bot_core.git_manager.stop_background_sync()
            # Stop web server
            if web_server:
                web_server.should_exit = True
                console.print("[dim]Web dashboard stopped[/dim]")
            # Stop all transports
            for name, transport in transports_to_run:
                try:
                    await transport.stop()
                    console.print(f"[dim]{name} stopped[/dim]")
                except Exception as e:
                    console.print(f"[yellow]Warning: {name} stop failed: {e}[/yellow]")

    try:
        anyio.run(_run_all)
    except KeyboardInterrupt:
        console.print("\n[yellow]All transports stopped.[/yellow]")


# ========== Background Run Commands ==========


@app.command("runs")
def runs(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Filter by project")] = None,
    active: Annotated[bool, typer.Option("--active", "-a", help="Show only active runs")] = False,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max number of runs")] = 20,
):
    """List background execution runs."""
    store = GluonStore()
    runner = TaskRunner(store=store)

    # Refresh status of active runs
    runner.refresh_all_runs()

    # Get project ID if name provided
    project_id = None
    if project:
        orchestrator = get_orchestrator()
        try:
            proj = orchestrator.get_project(project)
            project_id = proj.id
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    # Get runs
    statuses = [RunStatus.PENDING, RunStatus.RUNNING] if active else None
    runs_list = store.list_runs(project_id=project_id, statuses=statuses, limit=limit)

    if not runs_list:
        console.print("[dim]No runs found.[/dim]")
        console.print("Use 'gluon run <project> <prompt> --background' to start a background task.")
        return

    # Build project lookup
    projects = store.list_projects()
    project_lookup = {p.id: p.name for p in projects}

    table = Table(title="Execution Runs")
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Health")
    table.add_column("Project")
    table.add_column("Prompt")
    table.add_column("Duration")
    table.add_column("Stop Reason", style="dim")
    table.add_column("Created", style="dim")

    log_path = runner.config.log_path
    for run in runs_list:
        # Assess health for running tasks
        health: RunHealth | None = None
        if run.status == RunStatus.RUNNING:
            health = assess_run_health(run, log_path)

        emoji, color = format_run_status(run.status, health)
        duration = format_duration(run.duration_seconds)
        proj_name = project_lookup.get(run.project_id, run.project_id[:8])

        health_str = ""
        if health and health != RunHealth.UNKNOWN:
            health_color = {"healthy": "green", "slow": "yellow", "stalled": "red"}.get(health.value, "dim")
            health_str = f"[{health_color}]{health.value}[/{health_color}]"

        stop_reason = run.metadata.get("stop_reason", "") if run.metadata else ""
        stop_reason_str = f"[yellow]{stop_reason}[/yellow]" if stop_reason == "max_turns" else stop_reason

        table.add_row(
            run.id[:8],
            f"[{color}]{emoji} {run.status.value}[/{color}]",
            health_str,
            proj_name,
            (run.prompt[:30] + "...") if len(run.prompt) > 30 else run.prompt,
            duration,
            stop_reason_str,
            run.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)

    # Show active count
    active_runs = store.list_active_runs()
    if active_runs:
        console.print(f"\n[bold]{len(active_runs)}[/bold] run(s) currently active")


@app.command("logs")
def logs(
    run_id: Annotated[str, typer.Argument(help="Run ID (can use short prefix)")],
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs in real-time")] = False,
    tail: Annotated[int | None, typer.Option("--tail", "-n", help="Show last N lines")] = None,
    stream: Annotated[str, typer.Option("--stream", "-s", help="Log stream: stdout/stderr/messages")] = "stdout",
):
    """View logs for a background run."""
    store = GluonStore()
    runner = TaskRunner(store=store)

    # Find run by short ID
    run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
    if not run:
        console.print(f"[red]Error:[/red] Run not found: {run_id}")
        raise typer.Exit(1)

    # Get project name
    project = store.get_project(run.project_id)
    proj_name = project.name if project else run.project_id[:8]

    emoji, color = format_run_status(run.status)
    console.print(f"[bold]Run:[/bold] {run.id[:8]} [{color}]{emoji} {run.status.value}[/{color}]")
    console.print(f"[bold]Project:[/bold] {proj_name}")
    console.print(f"[bold]Prompt:[/bold] {run.prompt[:80]}{'...' if len(run.prompt) > 80 else ''}")
    console.print()

    if follow and run.is_active:
        # Live tail
        console.print(f"[dim]Following {stream} logs (Ctrl+C to stop)...[/dim]\n")

        async def _tail():
            try:
                async for line in runner.tail_logs(run.id, stream=stream):
                    console.print(line)
            except KeyboardInterrupt:
                pass

        anyio.run(_tail)
    else:
        # Static view
        logs_data = runner.get_logs(run.id, tail=tail)
        content = logs_data.get(stream, "")

        if not content:
            console.print(f"[dim]No {stream} logs available.[/dim]")
        else:
            console.print(content)

        if run.error_message:
            console.print(f"\n[red]Error:[/red] {run.error_message}")


@app.command("cancel")
def cancel(
    run_id: Annotated[str, typer.Argument(help="Run ID to cancel (can use short prefix)")],
):
    """Cancel a running background task."""
    store = GluonStore()
    runner = TaskRunner(store=store)

    # Find run by short ID
    run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
    if not run:
        console.print(f"[red]Error:[/red] Run not found: {run_id}")
        raise typer.Exit(1)

    if not run.is_active:
        console.print(f"[yellow]Run {run.id[:8]} is not active (status: {run.status.value})[/yellow]")
        return

    async def _cancel():
        success = await runner.cancel(run.id)
        if success:
            console.print(f"[green]✓[/green] Cancelled run {run.id[:8]}")
        else:
            console.print(f"[red]Failed to cancel run {run.id[:8]}[/red]")
            console.print("[dim]Process may have already completed or is not accessible.[/dim]")

    anyio.run(_cancel)


# ========== Web Dashboard Commands ==========


@app.command("web")
def web(
    host: Annotated[str, typer.Option("--host", "-h", help="Host to bind to")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to listen on")] = 45866,
    reload: Annotated[bool, typer.Option("--reload", "-r", help="Enable auto-reload for development")] = False,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Don't open browser automatically")] = False,
):
    """
    Start the Gluon web dashboard.

    Opens a browser to the dashboard at http://localhost:45866

    Install dependencies: pip install 'gluon-agent[web]'
    """
    try:
        import uvicorn

        from gluon.web import create_app
    except ImportError as exc:
        missing = exc.name or exc
        console.print(
            f"[red]Error:[/red] Web dashboard dependency not installed (missing: [yellow]{missing}[/yellow])."
        )
        console.print("Install with: [cyan]pip install 'gluon-agent[web]'[/cyan]")
        raise typer.Exit(1)

    # Refuse to expose an unauthenticated dashboard to the network.
    from gluon.auth import insecure_bind_error

    bind_error = insecure_bind_error(host)
    if bind_error:
        console.print(f"[red]Error:[/red] {bind_error}")
        raise typer.Exit(1)

    console.print("[bold]Starting Gluon Web Dashboard...[/bold]")
    console.print(f"[dim]URL: http://{host}:{port}[/dim]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    # Open browser unless disabled
    if not no_browser:
        import webbrowser

        webbrowser.open(f"http://{host}:{port}")

    try:
        # Create the app
        app_instance = create_app()
        uvicorn.run(app_instance, host=host, port=port, reload=reload, log_level="info")
    except KeyboardInterrupt:
        console.print("\n[yellow]Web dashboard stopped.[/yellow]")


# ========== Ralph Commands ==========


@ralph_app.command("status")
def ralph_status(
    run_id: Annotated[str, typer.Argument(help="Run ID (can use short prefix)")],
):
    """Show ralph loop status for a run."""
    store = GluonStore()

    # Find run by short ID
    run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
    if not run:
        console.print(f"[red]Error:[/red] Run not found: {run_id}")
        raise typer.Exit(1)

    if not run.ralph_enabled:
        console.print(f"[yellow]Run {run.id[:8]} is not a ralph mode run[/yellow]")
        raise typer.Exit(0)

    # Get project name
    project = store.get_project(run.project_id)
    proj_name = project.name if project else run.project_id[:8]

    emoji, color = format_run_status(run.status)

    # Circuit state styling
    circuit_colors = {
        CircuitState.CLOSED: "green",
        CircuitState.HALF_OPEN: "yellow",
        CircuitState.OPEN: "red",
    }
    circuit_color = circuit_colors.get(run.circuit_state, "white")

    console.print(f"[bold]Ralph Run:[/bold] {run.id[:8]} [{color}]{emoji} {run.status.value}[/{color}]")
    console.print(f"[bold]Project:[/bold] {proj_name}")
    console.print(f"[bold]Prompt:[/bold] {run.prompt[:80]}{'...' if len(run.prompt) > 80 else ''}")
    console.print()

    # Loop progress
    console.print(f"[bold]Loop Progress:[/bold] {run.loop_count}/{run.max_loops}")
    console.print(f"[bold]Circuit State:[/bold] [{circuit_color}]{run.circuit_state.value}[/{circuit_color}]")

    # Completion tracking
    if run.completion_reason:
        console.print(f"[bold]Completion:[/bold] {run.completion_reason}")
    else:
        console.print(f"[bold]Completion Signals:[/bold] {run.completion_signals}")
        console.print(f"[bold]Test-Only Loops:[/bold] {run.test_only_loops}")

    # Circuit breaker details
    if run.consecutive_no_progress > 0:
        console.print(f"[bold]No Progress:[/bold] {run.consecutive_no_progress} consecutive loops")
    if run.consecutive_same_error > 0:
        console.print(f"[bold]Same Error:[/bold] {run.consecutive_same_error} consecutive loops")

    # Rate limiting
    console.print()
    console.print(f"[bold]API Calls:[/bold] {run.calls_this_hour}/{run.max_calls_per_hour} this hour")
    if run.cost_usd:
        cost_display = f"${run.cost_usd:.4f}"
        if run.max_cost_usd:
            cost_display += f" / ${run.max_cost_usd:.2f} cap"
        console.print(f"[bold]Cost:[/bold] {cost_display}")


@ralph_app.command("iterations")
def ralph_iterations(
    run_id: Annotated[str, typer.Argument(help="Run ID (can use short prefix)")],
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max iterations to show")] = 20,
):
    """Show iteration history for a ralph run."""
    store = GluonStore()

    # Find run by short ID
    run = store.get_run_by_short_id(run_id) or store.get_run(run_id)
    if not run:
        console.print(f"[red]Error:[/red] Run not found: {run_id}")
        raise typer.Exit(1)

    if not run.ralph_enabled:
        console.print(f"[yellow]Run {run.id[:8]} is not a ralph mode run[/yellow]")
        raise typer.Exit(0)

    iterations = store.list_ralph_iterations(run.id, limit=limit)

    if not iterations:
        console.print("[dim]No iterations recorded yet.[/dim]")
        return

    table = Table(title=f"Ralph Iterations for {run.id[:8]}")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Status")
    table.add_column("Files", justify="right")
    table.add_column("Confidence", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Duration")

    for it in iterations:
        # Status indicator
        if it.has_errors:
            status = "[red]Error[/red]"
        elif it.has_completion_signal:
            status = "[green]Done signal[/green]"
        elif it.is_test_only:
            status = "[yellow]Test only[/yellow]"
        elif it.progress_detected:
            status = "[green]Progress[/green]"
        else:
            status = "[dim]No change[/dim]"

        # Duration
        duration = "-"
        if it.started_at and it.ended_at:
            secs = (it.ended_at - it.started_at).total_seconds()
            duration = f"{secs:.1f}s"

        table.add_row(
            str(it.loop_number),
            status,
            str(it.files_changed),
            f"{it.confidence_score:.0f}%",
            f"${it.cost_usd:.4f}",
            duration,
        )

    console.print(table)

    # Summary
    total_cost = sum(it.cost_usd for it in iterations)
    console.print(f"\n[bold]Total iterations:[/bold] {len(iterations)}")
    console.print(f"[bold]Total cost:[/bold] ${total_cost:.4f}")


@ralph_app.command("runs")
def ralph_runs(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Filter by project")] = None,
    active: Annotated[bool, typer.Option("--active", "-a", help="Show only active runs")] = False,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max runs to show")] = 20,
):
    """List ralph-enabled runs."""
    store = GluonStore()
    runner = TaskRunner(store=store)

    # Refresh status of active runs
    runner.refresh_all_runs()

    # Get project ID if name provided
    project_id = None
    if project:
        orchestrator = get_orchestrator()
        try:
            proj = orchestrator.get_project(project)
            project_id = proj.id
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    # Get runs - filter for ralph_enabled
    statuses = [RunStatus.PENDING, RunStatus.RUNNING] if active else None
    all_runs = store.list_runs(project_id=project_id, statuses=statuses, limit=limit * 2)

    # Filter to ralph runs only
    ralph_runs = [r for r in all_runs if r.ralph_enabled][:limit]

    if not ralph_runs:
        console.print("[dim]No ralph runs found.[/dim]")
        console.print("Use 'gluon run <project> <prompt> --ralph --background' to start a ralph task.")
        return

    # Build project lookup
    projects = store.list_projects()
    project_lookup = {p.id: p.name for p in projects}

    table = Table(title="Ralph Runs")
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Project")
    table.add_column("Loops")
    table.add_column("Circuit")
    table.add_column("Cost", justify="right")

    circuit_colors = {
        CircuitState.CLOSED: "green",
        CircuitState.HALF_OPEN: "yellow",
        CircuitState.OPEN: "red",
    }

    for r in ralph_runs:
        emoji, color = format_run_status(r.status)
        proj_name = project_lookup.get(r.project_id, r.project_id[:8])
        circuit_color = circuit_colors.get(r.circuit_state, "white")

        table.add_row(
            r.id[:8],
            f"[{color}]{emoji} {r.status.value}[/{color}]",
            proj_name,
            f"{r.loop_count}/{r.max_loops}",
            f"[{circuit_color}]{r.circuit_state.value}[/{circuit_color}]",
            f"${r.cost_usd:.4f}" if r.cost_usd else "-",
        )

    console.print(table)


# ========== Supervisor Daemon Commands ==========


@supervisor_app.command("start")
def supervisor_start(
    poll_interval: Annotated[int, typer.Option("--poll-interval", "-i", help="Poll interval in seconds")] = 30,
    foreground: Annotated[bool, typer.Option("--foreground", "-f", help="Run in foreground (don't daemonize)")] = False,
):
    """Start the supervisor daemon for auto-resume polling.

    The supervisor polls REVIEW tasks and auto-resumes based on supervision policies.
    """
    from gluon.supervisor_daemon import is_running, run_supervisor, setup_logging

    running, pid = is_running()
    if running:
        console.print(f"[yellow]Supervisor already running[/yellow] (PID: {pid})")
        raise typer.Exit(1)

    if foreground:
        console.print(f"[cyan]Starting supervisor in foreground[/cyan] (poll interval: {poll_interval}s)")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
        setup_logging()
        try:
            import asyncio

            asyncio.run(run_supervisor(poll_interval))
        except KeyboardInterrupt:
            console.print("\n[yellow]Supervisor stopped[/yellow]")
    else:
        # Start as background process
        import subprocess
        import sys

        cmd = [
            sys.executable,
            "-m",
            "gluon.supervisor_daemon",
            "--poll-interval",
            str(poll_interval),
        ]

        # Start detached process
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Give it a moment to start
        import time

        time.sleep(0.5)

        # Check if it started successfully
        running, pid = is_running()
        from gluon.supervisor_daemon import get_log_file

        if running:
            console.print(f"[green]✓[/green] Supervisor started (PID: {pid})")
            console.print(f"[dim]Log file: {get_log_file()}[/dim]")
        else:
            console.print("[red]Error:[/red] Failed to start supervisor")
            console.print(f"[dim]Check log file: {get_log_file()}[/dim]")
            raise typer.Exit(1)


@supervisor_app.command("stop")
def supervisor_stop():
    """Stop the supervisor daemon."""
    from gluon.supervisor_daemon import is_running, stop_daemon

    running, pid = is_running()
    if not running:
        console.print("[yellow]Supervisor not running[/yellow]")
        return

    if stop_daemon():
        console.print(f"[green]✓[/green] Supervisor stopped (was PID: {pid})")
    else:
        console.print("[red]Error:[/red] Failed to stop supervisor")
        raise typer.Exit(1)


@supervisor_app.command("status")
def supervisor_status():
    """Check supervisor daemon status."""
    from gluon.supervisor_daemon import get_log_file, get_pid_file, is_running

    running, pid = is_running()

    console.print("\n[bold]Supervisor Daemon Status[/bold]\n")

    table = Table()
    table.add_column("Setting", style="cyan")
    table.add_column("Value")

    if running:
        table.add_row("Status", "[green]Running[/green]")
        table.add_row("PID", str(pid))
    else:
        table.add_row("Status", "[yellow]Stopped[/yellow]")
        table.add_row("PID", "-")

    table.add_row("PID File", str(get_pid_file()))
    table.add_row("Log File", str(get_log_file()))

    console.print(table)

    # Show recent log entries if running
    if running:
        log_file = get_log_file()
        if log_file.exists():
            console.print("\n[bold]Recent Log Entries:[/bold]")
            try:
                lines = log_file.read_text().strip().split("\n")[-10:]
                for line in lines:
                    console.print(f"[dim]{line}[/dim]")
            except Exception:
                console.print("[dim]Unable to read log file[/dim]")


@supervisor_app.command("logs")
def supervisor_logs(
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow log output")] = False,
    lines: Annotated[int, typer.Option("--lines", "-n", help="Number of lines to show")] = 50,
):
    """View supervisor daemon logs."""
    from gluon.supervisor_daemon import get_log_file

    log_file = get_log_file()

    if not log_file.exists():
        console.print("[yellow]No log file found[/yellow]")
        console.print(f"[dim]Expected at: {log_file}[/dim]")
        return

    if follow:
        import subprocess

        subprocess.run(["tail", "-f", str(log_file)])
    else:
        content = log_file.read_text().strip().split("\n")
        for line in content[-lines:]:
            console.print(line)


# ========== Supervision Commands ==========


@supervision_app.command("status")
def supervision_status(
    run_id: Annotated[str, typer.Argument(help="Run ID to check")],
):
    """Show supervision status for a run."""
    store = GluonStore()

    # Try to find run by full ID or prefix
    run = store.get_run(run_id)
    if not run:
        run = store.get_run_by_short_id(run_id)

    if not run:
        console.print(f"[red]Error:[/red] Run '{run_id}' not found")
        raise typer.Exit(1)

    from gluon.policies import get_supervision_config

    config = get_supervision_config(run)

    console.print(f"\n[bold]Supervision Status for {run.id[:8]}[/bold]\n")

    table = Table()
    table.add_column("Setting", style="cyan")
    table.add_column("Value")

    table.add_row("Enabled", "[green]Yes[/green]" if config.enabled else "[red]No[/red]")
    table.add_row("Policy", config.policy.value)
    table.add_row("Max Auto-Resumes", str(config.max_auto_resumes))
    table.add_row("Auto-Resume Count", str(run.supervision_auto_resume_count))
    table.add_row("Min Time Between", f"{config.min_time_between_resumes}s")
    table.add_row(
        "Last Check",
        run.last_supervision_check_at.strftime("%Y-%m-%d %H:%M:%S") if run.last_supervision_check_at else "-",
    )
    table.add_row(
        "Last Resume",
        run.last_supervision_resume_at.strftime("%Y-%m-%d %H:%M:%S") if run.last_supervision_resume_at else "-",
    )

    if run.supervision_disabled_reason:
        table.add_row("Disabled Reason", f"[yellow]{run.supervision_disabled_reason}[/yellow]")

    console.print(table)


@supervision_app.command("logs")
def supervision_logs(
    run_id: Annotated[str, typer.Argument(help="Run ID to show logs for")],
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max decisions to show")] = 20,
):
    """Show supervision decision log for a run."""
    store = GluonStore()

    run = store.get_run(run_id)
    if not run:
        run = store.get_run_by_short_id(run_id)

    if not run:
        console.print(f"[red]Error:[/red] Run '{run_id}' not found")
        raise typer.Exit(1)

    decisions = store.list_supervision_decisions(run.id, limit=limit)

    if not decisions:
        console.print(f"[dim]No supervision decisions for run {run.id[:8]}[/dim]")
        return

    console.print(f"\n[bold]Supervision Decisions for {run.id[:8]}[/bold]\n")

    table = Table()
    table.add_column("Time", style="dim")
    table.add_column("Decision")
    table.add_column("Reason")
    table.add_column("Trigger", style="dim")

    decision_colors = {
        "resume": "green",
        "skip": "yellow",
        "hold": "blue",
        "disable": "red",
        "resume_failed": "red",
    }

    for d in decisions:
        color = decision_colors.get(d.decision, "white")
        table.add_row(
            d.timestamp.strftime("%H:%M:%S"),
            f"[{color}]{d.decision.upper()}[/{color}]",
            d.reason[:50] + "..." if len(d.reason) > 50 else d.reason,
            d.trigger or "-",
        )

    console.print(table)


@supervision_app.command("disable")
def supervision_disable(
    run_id: Annotated[str, typer.Argument(help="Run ID to disable supervision for")],
    reason: Annotated[str, typer.Option("--reason", "-r", help="Reason for disabling")] = "Manual disable",
):
    """Disable supervision for a run."""
    store = GluonStore()
    runner = TaskRunner(store=store)

    run = store.get_run(run_id)
    if not run:
        run = store.get_run_by_short_id(run_id)

    if not run:
        console.print(f"[red]Error:[/red] Run '{run_id}' not found")
        raise typer.Exit(1)

    from gluon.resume_coordinator import ResumeCoordinator

    coordinator = ResumeCoordinator(store=store, runner=runner)

    import asyncio

    success = asyncio.get_event_loop().run_until_complete(coordinator.disable_supervision(run.id, reason))

    if success:
        console.print(f"[green]✓[/green] Supervision disabled for run {run.id[:8]}")
    else:
        console.print("[red]Error:[/red] Failed to disable supervision")
        raise typer.Exit(1)


@supervision_app.command("evaluate")
def supervision_evaluate(
    run_id: Annotated[str, typer.Argument(help="Run ID to evaluate")],
):
    """Manually evaluate a run for auto-resume."""
    store = GluonStore()
    runner = TaskRunner(store=store)

    run = store.get_run(run_id)
    if not run:
        run = store.get_run_by_short_id(run_id)

    if not run:
        console.print(f"[red]Error:[/red] Run '{run_id}' not found")
        raise typer.Exit(1)

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(runner.evaluate_supervision(run.id))

    if result:
        decision = result["decision"]
        reason = result["reason"]
        color = "green" if decision == "resume" else "yellow"
        console.print(f"\n[bold]Evaluation Result for {run.id[:8]}[/bold]\n")
        console.print(f"Decision: [{color}]{decision.upper()}[/{color}]")
        console.print(f"Reason: {reason}")
        if result.get("wait_seconds", 0) > 0:
            console.print(f"Wait: {result['wait_seconds']}s until retry")
    else:
        console.print("[red]Error:[/red] Failed to evaluate run")
        raise typer.Exit(1)


# ========== Utility Commands ==========


@app.command("version")
def version():
    """Show version."""
    console.print(f"Gluon Agent v{__version__}")


@app.command("cleanup")
def cleanup(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview what would be deleted without deleting"),
    ] = False,
):
    """Clean up old log files based on retention policies.

    Retention policies:
    - Orphan logs (no DB record): deleted immediately
    - Archived runs: deleted 30 days after completion
    - Failed runs: deleted 7 days after completion
    - Completed runs (non-archived): deleted 30 days after completion
    """
    store = GluonStore()
    service = LogCleanupService(store=store)

    if dry_run:
        preview = service.preview()
        total = sum(len(ids) for ids in preview.values())

        if total == 0:
            console.print("[green]No logs to clean up[/green]")
            return

        console.print("[bold]Logs that would be deleted:[/bold]\n")

        table = Table(show_header=True, header_style="bold")
        table.add_column("Category")
        table.add_column("Count", justify="right")
        table.add_column("Run IDs")

        for category, run_ids in preview.items():
            if run_ids:
                ids_display = ", ".join(run_ids[:3])
                if len(run_ids) > 3:
                    ids_display += f" (+{len(run_ids) - 3} more)"
                table.add_row(category.title(), str(len(run_ids)), ids_display)

        console.print(table)
        console.print(f"\n[bold]Total:[/bold] {total} log directories would be deleted")
    else:
        stats = service.cleanup()
        total = (
            stats["orphan_deleted"] + stats["archived_deleted"] + stats["failed_deleted"] + stats["completed_deleted"]
        )

        if total == 0:
            console.print("[green]No logs to clean up[/green]")
        else:
            console.print("[bold]Cleanup complete:[/bold]")
            console.print(f"  Orphan:    {stats['orphan_deleted']}")
            console.print(f"  Archived:  {stats['archived_deleted']}")
            console.print(f"  Failed:    {stats['failed_deleted']}")
            console.print(f"  Completed: {stats['completed_deleted']}")
            console.print(f"  [bold]Total:[/bold]    {total}")

        if stats["errors"] > 0:
            console.print(f"\n[yellow]Errors: {stats['errors']}[/yellow]")


def _format_bytes(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    size: float = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@app.command("stats")
def stats():
    """Show disk usage statistics for ~/.gluon directory."""
    gluon_dir = Path.home() / ".gluon"

    if not gluon_dir.exists():
        console.print("[yellow]~/.gluon directory does not exist[/yellow]")
        return

    console.print("[bold]Disk Usage: ~/.gluon[/bold]\n")

    # Calculate sizes for main sections
    sections = []
    for entry in gluon_dir.iterdir():
        if entry.is_file():
            sections.append((entry.name, entry.stat().st_size))
        elif entry.is_dir():
            total = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            sections.append((entry.name + "/", total))

    sections.sort(key=lambda x: x[1], reverse=True)
    total_size = sum(size for _, size in sections)

    # Display section breakdown
    table = Table(show_header=True, header_style="bold")
    table.add_column("Section")
    table.add_column("Size", justify="right")
    table.add_column("%", justify="right")

    for name, size in sections:
        pct = (size / total_size * 100) if total_size > 0 else 0
        table.add_row(name, _format_bytes(size), f"{pct:.0f}%")

    table.add_row("[bold]Total[/bold]", f"[bold]{_format_bytes(total_size)}[/bold]", "[bold]100%[/bold]")
    console.print(table)

    # Show top runs by size
    store = GluonStore()
    service = LogCleanupService(store=store)
    usage = service.get_disk_usage()

    if usage["run_count"] > 0:
        console.print(f"\n[bold]Log Directory:[/bold] {usage['run_count']} runs, {_format_bytes(usage['total_bytes'])}")

        top_runs = usage.get("top_runs", [])
        if top_runs:
            console.print("\n[bold]Top 5 Largest Runs:[/bold]")
            runs_table = Table(show_header=True, header_style="bold")
            runs_table.add_column("Run ID")
            runs_table.add_column("Size", justify="right")
            runs_table.add_column("Status")
            runs_table.add_column("Project")

            db_runs = {run.id: run for run in store.list_runs(limit=10000, include_archived=True)}

            for run_id, size in top_runs[:5]:
                run = db_runs.get(run_id)
                status = run.status.value if run else "[orphan]"
                project = run.project_id if run else "-"
                runs_table.add_row(run_id[:8] + "...", _format_bytes(size), status, project)

            console.print(runs_table)


# ========== Helper Functions ==========


def _print_message(msg: AgentMessage) -> None:
    """Print an agent message to console."""
    if msg.type == "text":
        console.print(msg.content)
    elif msg.type == "tool_use":
        console.print(f"[dim]{msg.content}[/dim]")
    elif msg.type == "system":
        pass  # Silent
    elif msg.type == "error":
        console.print(f"[red]{msg.content}[/red]")
    elif msg.type == "result":
        pass  # Handled separately


def _print_result(result: AgentResult) -> None:
    """Print agent result summary."""
    if result.success:
        console.print("[green]✓ Complete[/green]")
    else:
        console.print(f"[red]✗ Failed: {result.error}[/red]")

    console.print(f"[dim]Cost: ${result.total_cost_usd:.4f} | Turns: {result.total_turns}[/dim]")

    if result.claude_session_id:
        console.print(f"[dim]Session: {result.claude_session_id[:8]}...[/dim]")


# ========== Chain Commands ==========


@chain_app.command("create")
def chain_create(
    project: Annotated[str, typer.Argument(help="Project name")],
    name: Annotated[str, typer.Argument(help="Chain name")],
    steps_file: Annotated[Path, typer.Argument(help="YAML/JSON file with step definitions")],
    worktree: Annotated[bool, typer.Option("--worktree", "-w", help="Execute in worktree")] = False,
):
    """Create a task chain from a step definition file."""
    import yaml

    from gluon.chain_executor import ChainExecutor
    from gluon.models import TaskChain, TaskProfile, TaskStep

    store = GluonStore()
    orchestrator = get_orchestrator()

    try:
        proj = orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not steps_file.exists():
        console.print(f"[red]Error:[/red] File not found: {steps_file}")
        raise typer.Exit(1)

    # Parse step definitions
    with open(steps_file) as f:
        if steps_file.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(f)
        else:
            import json as json_mod

            data = json_mod.load(f)

    chain = TaskChain(
        project_id=proj.id,
        name=data.get("name", name),
        description=data.get("description"),
        use_worktree=data.get("use_worktree", worktree),
        initiator="cli",
    )

    # Create steps with name-based ID mapping
    step_name_to_id: dict[str, str] = {}
    steps_data = data.get("steps", [])

    # First pass: create steps and map names to IDs
    for step_data in steps_data:
        step = TaskStep(
            chain_id=chain.id,
            name=step_data["name"],
            prompt=step_data["prompt"],
            profile=TaskProfile(step_data.get("profile", "standard")),
        )
        step_name_to_id[step.name] = step.id
        chain.steps.append(step)

    # Second pass: resolve depends_on names to IDs
    for i, step_data in enumerate(steps_data):
        dep_names = step_data.get("depends_on", [])
        dep_ids = []
        for dep_name in dep_names:
            dep_id = step_name_to_id.get(dep_name)
            if not dep_id:
                console.print(f"[red]Error:[/red] Step '{chain.steps[i].name}' depends on unknown step '{dep_name}'")
                raise typer.Exit(1)
            dep_ids.append(dep_id)
        chain.steps[i].depends_on = dep_ids

    # Validate
    runner = TaskRunner(store=store)
    executor = ChainExecutor(store, runner)
    errors = executor.validate_chain(chain)
    if errors:
        for err in errors:
            console.print(f"[red]Error:[/red] {err}")
        raise typer.Exit(1)

    # Persist
    store.create_chain(chain)
    for step in chain.steps:
        store.create_step(step)

    console.print(f"[green]Created chain[/green] {chain.id} ({chain.name})")
    console.print(f"  {len(chain.steps)} steps defined")

    # Show step graph
    for step in chain.steps:
        deps = [s.name for s in chain.steps if s.id in step.depends_on]
        dep_str = f" (after: {', '.join(deps)})" if deps else ""
        console.print(f"  - {step.name} [{step.profile.value}]{dep_str}")

    console.print(f"\nStart with: [bold]gluon chain start {chain.id}[/bold]")


@chain_app.command("start")
def chain_start(
    chain_id: Annotated[str, typer.Argument(help="Chain ID to start")],
):
    """Start executing a task chain."""
    from gluon.chain_executor import ChainExecutor

    store = GluonStore()
    runner = TaskRunner(store=store)
    executor = ChainExecutor(store, runner)

    chain = store.get_chain(chain_id)
    if not chain:
        console.print(f"[red]Error:[/red] Chain not found: {chain_id}")
        raise typer.Exit(1)

    async def _start():
        await executor.start_chain(chain_id)

    anyio.run(_start)
    console.print(f"[green]Started chain[/green] {chain_id}")
    console.print(f"Use [bold]gluon chain show {chain_id}[/bold] to track progress.")


@chain_app.command("list")
def chain_list(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Filter by project")] = None,
):
    """List task chains."""
    from gluon.models import ChainStatus

    store = GluonStore()
    project_id = None
    if project:
        orchestrator = get_orchestrator()
        try:
            proj = orchestrator.get_project(project)
            project_id = proj.id
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    chains = store.list_chains(project_id=project_id)
    if not chains:
        console.print("[dim]No chains found.[/dim]")
        return

    # Project lookup
    projects = store.list_projects()
    project_lookup = {p.id: p.name for p in projects}

    table = Table(title="Task Chains")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Project")
    table.add_column("Steps")
    table.add_column("Created", style="dim")

    status_styles = {
        ChainStatus.PENDING: ("⏳", "yellow"),
        ChainStatus.RUNNING: ("🔄", "blue"),
        ChainStatus.COMPLETED: ("✅", "green"),
        ChainStatus.FAILED: ("❌", "red"),
        ChainStatus.CANCELLED: ("🚫", "dim"),
    }

    for chain in chains:
        emoji, color = status_styles.get(chain.status, ("❓", "white"))
        proj_name = project_lookup.get(chain.project_id, chain.project_id[:8])
        completed = sum(1 for s in chain.steps if s.status.value == "completed")
        step_str = f"{completed}/{len(chain.steps)}"

        table.add_row(
            chain.id,
            chain.name,
            f"[{color}]{emoji} {chain.status.value}[/{color}]",
            proj_name,
            step_str,
            chain.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@chain_app.command("show")
def chain_show(
    chain_id: Annotated[str, typer.Argument(help="Chain ID")],
):
    """Show chain details with step status."""
    from gluon.models import StepStatus

    store = GluonStore()
    chain = store.get_chain(chain_id)
    if not chain:
        console.print(f"[red]Error:[/red] Chain not found: {chain_id}")
        raise typer.Exit(1)

    projects = store.list_projects()
    project_lookup = {p.id: p.name for p in projects}
    proj_name = project_lookup.get(chain.project_id, chain.project_id[:8])

    console.print(
        Panel(
            f"[bold]{chain.name}[/bold]\n"
            f"Project: {proj_name}\n"
            f"Status: {chain.status.value}\n"
            f"Created: {chain.created_at.strftime('%Y-%m-%d %H:%M')}"
            + (f"\nDescription: {chain.description}" if chain.description else ""),
            title=f"Chain {chain.id}",
        )
    )

    step_styles = {
        StepStatus.PENDING: ("⏳", "yellow"),
        StepStatus.BLOCKED: ("🔒", "dim"),
        StepStatus.READY: ("🟢", "green"),
        StepStatus.RUNNING: ("🔄", "blue"),
        StepStatus.COMPLETED: ("✅", "green"),
        StepStatus.FAILED: ("❌", "red"),
        StepStatus.SKIPPED: ("⏭️", "dim"),
    }

    table = Table(title="Steps")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Profile")
    table.add_column("Run ID")
    table.add_column("Duration")

    step_name_lookup = {s.id: s.name for s in chain.steps}
    for step in chain.steps:
        emoji, color = step_styles.get(step.status, ("❓", "white"))
        deps = [step_name_lookup.get(d, d[:8]) for d in step.depends_on]
        name = step.name
        if deps:
            name += f" (after: {', '.join(deps)})"

        duration = "-"
        if step.duration_seconds:
            from gluon.runner import format_duration

            duration = format_duration(step.duration_seconds)

        table.add_row(
            name,
            f"[{color}]{emoji} {step.status.value}[/{color}]",
            step.profile.value,
            step.run_id[:8] if step.run_id else "-",
            duration,
        )

    console.print(table)


@chain_app.command("cancel")
def chain_cancel(
    chain_id: Annotated[str, typer.Argument(help="Chain ID to cancel")],
):
    """Cancel a running chain and all its steps."""
    from gluon.chain_executor import ChainExecutor

    store = GluonStore()
    runner = TaskRunner(store=store)
    executor = ChainExecutor(store, runner)

    chain = store.get_chain(chain_id)
    if not chain:
        console.print(f"[red]Error:[/red] Chain not found: {chain_id}")
        raise typer.Exit(1)

    async def _cancel():
        await executor.cancel_chain(chain_id)

    anyio.run(_cancel)
    console.print(f"[green]Cancelled chain[/green] {chain_id}")


# ========== Doctor Commands ==========


@doctor_app.callback(invoke_without_command=True)
def doctor_check(
    ctx: typer.Context,
    fix: Annotated[bool, typer.Option("--fix", help="Auto-fix fixable issues")] = False,
):
    """Run system health diagnostics."""
    if ctx.invoked_subcommand is not None:
        return

    from gluon.doctor import run_all_fixes, run_diagnostics
    from gluon.store import DEFAULT_LOG_PATH

    store = GluonStore()
    results = run_diagnostics(store, DEFAULT_LOG_PATH)

    table = Table(title="Gluon Health Check")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Message")
    table.add_column("Fixable")

    status_styles = {"ok": "green", "warn": "yellow", "error": "red"}

    for r in results:
        style = status_styles.get(r.status, "white")
        table.add_row(
            r.name,
            f"[{style}]{r.status.upper()}[/{style}]",
            r.message,
            "yes" if r.fixable else "",
        )
        for detail in r.details:
            table.add_row("", "", f"  {detail}", "")

    console.print(table)

    # Summary
    errors = sum(1 for r in results if r.status == "error")
    warns = sum(1 for r in results if r.status == "warn")
    if errors == 0 and warns == 0:
        console.print("\n[green]All checks passed.[/green]")
    else:
        if errors:
            console.print(f"\n[red]{errors} error(s)[/red]", end="")
        if warns:
            console.print(f"  [yellow]{warns} warning(s)[/yellow]", end="")
        console.print()

    # Auto-fix if requested
    if fix:
        fixable = [r for r in results if r.fixable and r.status in ("warn", "error")]
        if not fixable:
            console.print("[dim]Nothing to fix.[/dim]")
            return

        console.print("\n[bold]Running fixes...[/bold]")
        fix_results = run_all_fixes(store)
        total_fixed = sum(fix_results.values())
        for name, count in fix_results.items():
            if count > 0:
                console.print(f"  [green]Fixed {count}[/green] {name.replace('_', ' ')}")
        if total_fixed == 0:
            console.print("  [dim]No issues needed fixing.[/dim]")
        else:
            console.print(f"\n[green]Fixed {total_fixed} issue(s).[/green]")


@doctor_app.command("fix")
def doctor_fix():
    """Auto-fix all fixable issues."""
    from gluon.doctor import run_all_fixes

    store = GluonStore()
    console.print("[bold]Running all fixes...[/bold]")
    fix_results = run_all_fixes(store)
    total_fixed = sum(fix_results.values())
    for name, count in fix_results.items():
        if count > 0:
            console.print(f"  [green]Fixed {count}[/green] {name.replace('_', ' ')}")
    if total_fixed == 0:
        console.print("[dim]No issues found.[/dim]")
    else:
        console.print(f"\n[green]Fixed {total_fixed} issue(s).[/green]")


# ========== Activity Log Commands (F11) ==========


@app.command("activity")
def activity_list(
    limit: Annotated[int, typer.Option(help="Max events to show")] = 50,
    actor: Annotated[str | None, typer.Option(help="Filter by actor")] = None,
    action: Annotated[str | None, typer.Option(help="Filter by action")] = None,
) -> None:
    """Show recent activity events."""
    from gluon.activity_log import ActivityLogger

    store = GluonStore()
    logger = ActivityLogger(store)
    events = logger.query(actor=actor, action=action, limit=limit)

    if not events:
        console.print("[dim]No activity events found.[/dim]")
        return

    table = Table(title="Activity Log")
    table.add_column("Timestamp", style="dim", width=20)
    table.add_column("Actor", width=12)
    table.add_column("Action", style="cyan", width=20)
    table.add_column("Result", width=10)
    table.add_column("Message", max_width=50)

    for event in events:
        table.add_row(
            event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            event.actor[:12],
            event.action,
            event.result or "",
            event.message or "",
        )

    console.print(table)


# ========== Formula Commands (F10) ==========

formula_app = typer.Typer(help="Workflow formula templates")
app.add_typer(formula_app, name="formula")


@formula_app.command("list")
def formula_list_cmd() -> None:
    """List available workflow formulas."""
    from gluon.formulas import FormulaLoader

    templates = FormulaLoader.discover()

    if not templates:
        console.print("[dim]No formulas found.[/dim]")
        return

    table = Table(title="Available Formulas")
    table.add_column("Name", style="cyan")
    table.add_column("Kind")
    table.add_column("Description")
    table.add_column("Steps", justify="right")
    table.add_column("Variables", justify="right")
    table.add_column("Source", style="dim")

    for t in templates:
        source = str(t.source_path) if t.source_path else "builtin"
        table.add_row(
            t.name,
            t.kind,
            t.description or "",
            "loop" if t.kind == "loop" else str(len(t.steps)),
            str(len(t.variables)),
            source,
        )

    console.print(table)


@formula_app.command("show")
def formula_show(name: Annotated[str, typer.Argument(help="Formula name")]) -> None:
    """Show details of a workflow formula."""
    from gluon.formulas import FormulaLoader

    template = FormulaLoader.load(name)
    if not template:
        console.print(f"[red]Formula not found: {name}[/red]")
        raise typer.Exit(1)

    console.print(
        Panel(f"[bold]{template.name}[/bold] ({template.kind})\n{template.description or ''}", title="Formula")
    )

    if template.variables:
        var_table = Table(title="Variables")
        var_table.add_column("Name", style="cyan")
        var_table.add_column("Type")
        var_table.add_column("Required")
        var_table.add_column("Default")
        var_table.add_column("Help")

        for v in template.variables:
            var_table.add_row(
                v.name,
                v.type,
                "yes" if v.required else "no",
                v.default or "",
                v.help or "",
            )
        console.print(var_table)

    if template.kind == "loop":
        console.print(Panel(template.objective or "", title="Loop objective (template)"))
        console.print(f"[bold]Gate:[/bold] {template.verify_cmd or '(gateless)'}")
        console.print(f"[bold]Independent verifier:[/bold] {'yes' if template.agent_verifier else 'no'}")
        console.print(
            f"[bold]Budget:[/bold] {template.max_iterations} iterations"
            + (f", ${template.max_cost_usd:.2f} cap" if template.max_cost_usd else ", no cost cap")
        )
        return

    step_table = Table(title="Steps")
    step_table.add_column("ID", style="cyan")
    step_table.add_column("Name")
    step_table.add_column("Profile")
    step_table.add_column("Depends On")

    for s in template.steps:
        step_table.add_row(
            s.id,
            s.name,
            s.profile,
            ", ".join(s.depends_on) if s.depends_on else "-",
        )
    console.print(step_table)


@formula_app.command("run")
def formula_run(
    name: Annotated[str, typer.Argument(help="Formula name")],
    project: Annotated[str, typer.Argument(help="Project name")],
    var: Annotated[list[str] | None, typer.Option("--var", help="Variable as key=value")] = None,
) -> None:
    """Execute a workflow formula on a project."""
    from gluon.chain_executor import ChainExecutor
    from gluon.formula_executor import FormulaExecutor
    from gluon.formulas import FormulaLoader
    from gluon.runner import TaskRunner

    template = FormulaLoader.load(name)
    if not template:
        console.print(f"[red]Formula not found: {name}[/red]")
        raise typer.Exit(1)

    # Parse variables from --var key=value
    variables: dict[str, str] = {}
    for v in var or []:
        if "=" not in v:
            console.print(f"[red]Invalid variable format: {v} (expected key=value)[/red]")
            raise typer.Exit(1)
        k, val = v.split("=", 1)
        variables[k] = val

    orchestrator = get_orchestrator()
    proj = orchestrator.get_project(project)
    store = GluonStore()
    runner = TaskRunner(store)
    chain_executor = ChainExecutor(store, runner)
    formula_executor = FormulaExecutor(store, chain_executor)

    from gluon.formula_executor import FormulaRunOutcome

    async def _run() -> FormulaRunOutcome:
        return await formula_executor.execute(
            template=template,
            project_id=proj.id,
            variables=variables,
            initiator="cli",
        )

    outcome = anyio.run(_run)
    if outcome.kind == "loop":
        console.print(f"[green]Formula '{name}' created agent loop {outcome.loop_id}[/green]")
        console.print("Iteration 1 seeded — the server's queue drain will dispatch it. See `gluon loop show`.")
    else:
        console.print(f"[green]Formula '{name}' started as chain {outcome.chain_id}[/green]")


@formula_app.command("validate")
def formula_validate(path: Annotated[Path, typer.Argument(help="Path to YAML formula file")]) -> None:
    """Validate a formula template file."""
    from gluon.formulas import FormulaLoader, validate_formula

    template = FormulaLoader.load_from_file(path)
    errors = validate_formula(template)

    if errors:
        for err in errors:
            console.print(f"[red]  {err}[/red]")
        raise typer.Exit(1)
    detail = "loop template" if template.kind == "loop" else f"{len(template.steps)} steps"
    console.print(f"[green]Formula '{template.name}' is valid ({detail}).[/green]")


# ========== Work Queue Commands (F12) ==========

queue_app = typer.Typer(help="Work queue management")
app.add_typer(queue_app, name="queue")


@queue_app.command("add")
def queue_add(
    project: Annotated[str, typer.Argument(help="Project name")],
    prompt: Annotated[str, typer.Argument(help="Task prompt")],
    profile: Annotated[str, typer.Option(help="Task profile")] = "standard",
    priority: Annotated[int, typer.Option(help="Priority (lower=higher)")] = 10,
) -> None:
    """Add a task to the work queue."""
    from gluon.work_queue import WorkQueueManager

    orchestrator = get_orchestrator()
    proj = orchestrator.get_project(project)
    store = GluonStore()
    wq = WorkQueueManager(store)
    item = wq.enqueue(proj.id, prompt, profile=profile, priority=priority)
    console.print(f"[green]Queued: {item.id} (priority={priority})[/green]")


@queue_app.command("list")
def queue_list_cmd(
    project: Annotated[str | None, typer.Option(help="Filter by project name")] = None,
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
) -> None:
    """List work queue items."""
    from gluon.work_queue import WorkQueueManager

    store = GluonStore()
    wq = WorkQueueManager(store)

    project_id = None
    if project:
        orchestrator = get_orchestrator()
        proj = orchestrator.get_project(project)
        project_id = proj.id

    items = wq.list_items(project_id=project_id, status=status)

    if not items:
        console.print("[dim]No work queue items found.[/dim]")
        return

    table = Table(title="Work Queue")
    table.add_column("ID", style="cyan", width=12)
    table.add_column("Project", width=12)
    table.add_column("Status", width=10)
    table.add_column("Priority", justify="right", width=8)
    table.add_column("Prompt", max_width=40)
    table.add_column("Created", style="dim", width=16)

    for item in items:
        table.add_row(
            item.id,
            item.project_id[:12],
            item.status.value,
            str(item.priority),
            item.prompt[:40],
            item.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@queue_app.command("cancel")
def queue_cancel(item_id: Annotated[str, typer.Argument(help="Work queue item ID")]) -> None:
    """Cancel a queued work item."""
    from gluon.work_queue import WorkQueueManager

    store = GluonStore()
    wq = WorkQueueManager(store)
    wq.cancel(item_id)
    console.print(f"[green]Cancelled: {item_id}[/green]")


# ========== Merge Queue Commands (F8) ==========

merge_app = typer.Typer(help="Merge queue management")
app.add_typer(merge_app, name="merge")


@merge_app.command("list")
def merge_list_cmd(
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
) -> None:
    """List merge queue entries."""
    store = GluonStore()
    entries = store.list_merge_entries(status=status)

    if not entries:
        console.print("[dim]No merge queue entries found.[/dim]")
        return

    table = Table(title="Merge Queue")
    table.add_column("ID", style="cyan", width=12)
    table.add_column("Branch", width=25)
    table.add_column("PR", width=8)
    table.add_column("Status", width=10)
    table.add_column("Conflicts", justify="right", width=10)
    table.add_column("Created", style="dim", width=16)

    for entry in entries:
        table.add_row(
            entry.id,
            entry.branch_name,
            str(entry.pr_number or "-"),
            entry.status.value,
            str(entry.conflict_count),
            entry.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@merge_app.command("retry")
def merge_retry(entry_id: Annotated[str, typer.Argument(help="Merge queue entry ID")]) -> None:
    """Retry a failed/conflicted merge entry."""
    from gluon.models import MergeQueueStatus

    store = GluonStore()
    entry = store.get_merge_entry(entry_id)
    if not entry:
        console.print(f"[red]Entry not found: {entry_id}[/red]")
        raise typer.Exit(1)

    entry.status = MergeQueueStatus.PENDING
    entry.next_retry_at = None
    store.update_merge_entry(entry)
    console.print(f"[green]Reset entry {entry_id} to PENDING for retry.[/green]")


@merge_app.command("cancel")
def merge_cancel(entry_id: Annotated[str, typer.Argument(help="Merge queue entry ID")]) -> None:
    """Cancel a merge queue entry."""
    from gluon.models import MergeQueueStatus, utc_now

    store = GluonStore()
    entry = store.get_merge_entry(entry_id)
    if not entry:
        console.print(f"[red]Entry not found: {entry_id}[/red]")
        raise typer.Exit(1)

    entry.status = MergeQueueStatus.CANCELLED
    entry.completed_at = utc_now()
    store.update_merge_entry(entry)
    console.print(f"[green]Cancelled: {entry_id}[/green]")


# ========== Witness Commands (F9) ==========


@app.command("witness")
def witness_show(run_id: Annotated[str, typer.Argument(help="Run ID")]) -> None:
    """Show witness decision history for a run."""
    store = GluonStore()
    decisions = store.list_witness_decisions(run_id)

    if not decisions:
        console.print("[dim]No witness decisions found for this run.[/dim]")
        return

    table = Table(title=f"Witness Decisions for {run_id[:12]}")
    table.add_column("Timestamp", style="dim", width=20)
    table.add_column("Classification", style="cyan", width=20)
    table.add_column("Confidence", justify="right", width=10)
    table.add_column("Action", width=12)
    table.add_column("Reasoning", max_width=40)

    for d in decisions:
        table.add_row(
            d.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            d.classification.value,
            f"{d.confidence:.2f}",
            d.action.value,
            d.reasoning[:40] if d.reasoning else "",
        )

    console.print(table)


# ========== Worktree Commands ==========


def _get_worktree_service(retention_days: int | None = None) -> WorktreeCleanupService:
    """Get WorktreeCleanupService with optional retention override."""
    store = GluonStore()
    # Resolve retention from settings if not explicitly provided
    if retention_days is None:
        setting = store.resolve_setting("worktree_retention_days")
        retention_days = int(setting) if setting else 7
    return WorktreeCleanupService(store=store, retention_days=retention_days)


@worktree_app.command("list")
def worktree_list():
    """List all worktree directories and their status."""
    service = _get_worktree_service()
    preview = service.preview()
    usage = service.get_disk_usage()

    if usage["run_count"] == 0:
        console.print("[green]No worktrees found[/green]")
        return

    console.print(
        f"[bold]Worktrees:[/bold] {usage['run_count']} directories, {_format_bytes(usage['total_bytes'])} total\n"
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Directory")
    table.add_column("Run ID")
    table.add_column("Size", justify="right")
    table.add_column("Status")
    table.add_column("Reason")

    # Show eligible for deletion first
    for category, style in [
        ("orphan", "red"),
        ("merged", "yellow"),
        ("expired", "yellow"),
        ("active", "green"),
        ("retained", "dim"),
    ]:
        for info in preview.get(category, []):
            run_id_display = info["run_id"][:8] + "..." if info["run_id"] else "[orphan]"
            table.add_row(
                info["path"].name,
                run_id_display,
                _format_bytes(info["size_bytes"]),
                f"[{style}]{category}[/{style}]",
                info["reason"],
            )

    console.print(table)

    # Summary of what would be cleaned
    deletable = sum(len(preview.get(cat, [])) for cat in ("orphan", "merged", "expired"))
    if deletable > 0:
        deletable_bytes = sum(
            info["size_bytes"] for cat in ("orphan", "merged", "expired") for info in preview.get(cat, [])
        )
        console.print(
            f"\n[bold]{deletable}[/bold] worktrees eligible for cleanup "
            f"({_format_bytes(deletable_bytes)}). "
            f"Run [cyan]gluon worktree gc[/cyan] to clean up."
        )


@worktree_app.command("gc")
def worktree_gc(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview what would be deleted without deleting"),
    ] = False,
    retention_days: Annotated[
        int | None,
        typer.Option("--retention-days", "-r", help="Override retention period in days (default: 7)"),
    ] = None,
):
    """Garbage-collect stale worktrees.

    Deletes worktree directories for:
    - Orphan worktrees (no matching run in DB): immediately
    - Merged PRs: immediately
    - Completed/failed/cancelled runs: after retention period (default 7 days)

    Active runs (pending/running/review) are never touched.
    """
    service = _get_worktree_service(retention_days)

    if dry_run:
        preview = service.preview()
        deletable_cats = ("orphan", "merged", "expired")
        total = sum(len(preview.get(cat, [])) for cat in deletable_cats)
        total_bytes = sum(info["size_bytes"] for cat in deletable_cats for info in preview.get(cat, []))

        if total == 0:
            console.print("[green]No worktrees to clean up[/green]")
            return

        console.print("[bold]Worktrees that would be deleted:[/bold]\n")

        table = Table(show_header=True, header_style="bold")
        table.add_column("Category")
        table.add_column("Count", justify="right")
        table.add_column("Size", justify="right")

        for category in deletable_cats:
            items = preview.get(category, [])
            if items:
                cat_bytes = sum(i["size_bytes"] for i in items)
                table.add_row(category.title(), str(len(items)), _format_bytes(cat_bytes))

        console.print(table)
        console.print(f"\n[bold]Total:[/bold] {total} worktrees, {_format_bytes(total_bytes)} would be freed")
    else:
        stats = service.cleanup()
        total = stats["orphan_deleted"] + stats["merged_deleted"] + stats["expired_deleted"]

        if total == 0:
            console.print("[green]No worktrees to clean up[/green]")
        else:
            freed = _format_bytes(stats["bytes_freed"])
            console.print("[bold]Worktree cleanup complete:[/bold]")
            console.print(f"  Orphan:  {stats['orphan_deleted']}")
            console.print(f"  Merged:  {stats['merged_deleted']}")
            console.print(f"  Expired: {stats['expired_deleted']}")
            console.print(f"  [bold]Total:[/bold]  {total} ({freed} freed)")

        if stats["git_pruned"] > 0:
            console.print(f"  Git refs pruned in {stats['git_pruned']} repo(s)")

        if stats["errors"] > 0:
            console.print(f"\n[yellow]Errors: {stats['errors']}[/yellow]")


# ========== Settings Commands ==========


# Well-known setting keys with short descriptions for `gluon settings list`.
_KNOWN_SETTINGS: dict[str, str] = {
    "default_run_max_cost_usd": "Default per-run cost cap (USD). Overrides profile default for non-ralph runs.",
    "prehydration_enabled": "Pre-hydrate project context before the run starts (true/false).",
    "agent_teams_enabled": "Enable agent teams / sub-agents (true/false).",
    "skills_enabled": "Enable SDK skills feature (true/false).",
    "include_hook_events": "Stream hook events (PreToolUse, PostToolUse, Stop) in the message stream (true/false).",
    "sandbox_enabled": "Enable sandboxed execution (true/false).",
    "github_webhook_secret": "HMAC secret for GitHub webhook signature verification.",
    "session_cleanup_enabled": ("Auto-delete previous Claude session JSONL files on run completion (default false)."),
    "session_cleanup_retention_days": "Retention window for orphan Claude sessions (default 30 days).",
}


@settings_app.command("list")
def settings_list() -> None:
    """Show all Gluon settings stored in the database."""
    store = GluonStore()
    all_settings = store.get_all_settings()

    if not all_settings and not _KNOWN_SETTINGS:
        console.print("[dim]No settings configured.[/dim]")
        return

    table = Table(title="Gluon Settings")
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    table.add_column("Description", style="dim")

    # Show known settings first (configured or not), then any extras
    shown: set[str] = set()
    for key, desc in _KNOWN_SETTINGS.items():
        value = all_settings.get(key, "[dim](unset)[/dim]")
        table.add_row(key, value, desc)
        shown.add(key)

    for key, value in sorted(all_settings.items()):
        if key in shown:
            continue
        table.add_row(key, value, "")

    console.print(table)


@settings_app.command("get")
def settings_get(
    key: Annotated[str, typer.Argument(help="Setting key")],
) -> None:
    """Print a single setting value."""
    store = GluonStore()
    value = store.get_setting(key)
    if value is None:
        console.print(f"[dim]{key} is unset[/dim]")
        raise typer.Exit(code=1)
    console.print(value)


@settings_app.command("set")
def settings_set(
    key: Annotated[str, typer.Argument(help="Setting key")],
    value: Annotated[str, typer.Argument(help="Value to store")],
) -> None:
    """Set a Gluon setting. Pass values as strings; numeric settings are parsed at use."""
    store = GluonStore()
    store.set_setting(key, value)
    desc = _KNOWN_SETTINGS.get(key, "")
    console.print(f"[green]Set[/green] [cyan]{key}[/cyan] = {value}")
    if desc:
        console.print(f"  [dim]{desc}[/dim]")


@settings_app.command("delete")
def settings_delete(
    key: Annotated[str, typer.Argument(help="Setting key")],
) -> None:
    """Delete a setting (reverts to default)."""
    store = GluonStore()
    with store._get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    console.print(f"[yellow]Deleted[/yellow] setting [cyan]{key}[/cyan]")


# ========== Provider Command ==========


# Per-provider config hints shown after a switch — points users at the env vars
# they'll need to set for that backend.
_PROVIDER_CONFIG_HINTS: dict[str, str] = {
    "bedrock": (
        "Required env: [cyan]AWS_REGION[/cyan] (e.g. us-east-1) + standard AWS credentials "
        "([cyan]AWS_BEARER_TOKEN_BEDROCK[/cyan] or access keys)."
    ),
    "anthropic": ("Required env: [cyan]ANTHROPIC_API_KEY[/cyan], or authenticate via [cyan]claude login[/cyan]."),
    "vertex": (
        "Required env: [cyan]ANTHROPIC_VERTEX_PROJECT_ID[/cyan] + [cyan]CLOUD_ML_REGION[/cyan] "
        "(global / us / eu / us-east5 / europe-west1 / ...)\n"
        "Auth: run [cyan]gcloud auth application-default login[/cyan] on the host, or set "
        "[cyan]GOOGLE_APPLICATION_CREDENTIALS[/cyan] to a service-account key."
    ),
    "foundry": (
        "Required env: [cyan]ANTHROPIC_FOUNDRY_RESOURCE[/cyan] (your Azure resource name) "
        "or [cyan]ANTHROPIC_FOUNDRY_BASE_URL[/cyan]\n"
        "Auth: set [cyan]ANTHROPIC_FOUNDRY_API_KEY[/cyan], or leave unset and use Entra ID "
        "via [cyan]az login[/cyan] / managed identity."
    ),
}


@app.command("provider")
def provider_cmd(
    provider: Annotated[
        str | None,
        typer.Argument(help="Provider to set: 'bedrock', 'anthropic', 'vertex', or 'foundry'. Omit to show current."),
    ] = None,
) -> None:
    """View or change the LLM provider.

    Gluon supports four backends — Bedrock (default), direct Anthropic API,
    Google Vertex AI, and Microsoft Foundry (Azure). Each reads a different
    set of credentials; see `gluon provider <name>` output for which env
    vars to set.

    Without arguments, shows the current provider and model mappings.
    With an argument, sets the provider in the database.

    Examples:
        gluon provider              # Show current provider
        gluon provider bedrock      # Switch to AWS Bedrock
        gluon provider anthropic    # Switch to direct Anthropic API / Claude CLI subscription
        gluon provider vertex       # Switch to Google Vertex AI
        gluon provider foundry      # Switch to Microsoft Foundry (Azure)
    """
    from gluon.llm_provider import LLMProvider, get_provider, get_provider_source

    if provider is None:
        # Show current provider info
        current = get_provider()
        source = get_provider_source()
        provider_key = current.__class__.__name__.replace("Provider", "").lower()

        console.print(
            Panel.fit(
                f"[bold]Provider:[/bold] {current.name}\n"
                f"[bold]Source:[/bold] {source}\n"
                f"[bold]Cost Tracking:[/bold] {'yes' if current.supports_cost_tracking else 'no'}",
                title="LLM Provider",
            )
        )

        table = Table(title="Model Mappings")
        table.add_column("Tier")
        table.add_column("Model ID")
        for tier, model_id in current.MODELS.items():
            table.add_row(tier.value, model_id)
        console.print(table)

        hint = _PROVIDER_CONFIG_HINTS.get(provider_key)
        if hint:
            console.print(f"\n[dim]{hint}[/dim]")
        console.print(
            "\n[dim]Other providers:[/dim] "
            + ", ".join(f"[cyan]{p.value}[/cyan]" for p in LLMProvider if p.value != provider_key)
            + "  [dim](switch with `gluon provider <name>`)[/dim]"
        )
        return

    # Validate the provider value
    try:
        LLMProvider(provider.lower())
    except ValueError:
        available = ", ".join(p.value for p in LLMProvider)
        console.print(f"[red]Unknown provider:[/red] {provider}. Available: {available}")
        raise typer.Exit(1)

    # Save to database
    store = GluonStore()
    store.set_setting("llm_provider", provider.lower())

    # Show confirmation
    new_provider = get_provider(provider)
    console.print(f"[green]LLM provider set to:[/green] {new_provider.name}")

    table = Table(title="Model Mappings")
    table.add_column("Tier")
    table.add_column("Model ID")
    for tier, model_id in new_provider.MODELS.items():
        table.add_row(tier.value, model_id)
    console.print(table)

    hint = _PROVIDER_CONFIG_HINTS.get(provider.lower())
    if hint:
        console.print(f"\n[yellow]Next steps:[/yellow] {hint}")


# ========== Agent Commands (Theme B Phase 1) ==========


@agent_app.command("list")
def agent_list(
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Filter by workspace name"),
    ] = None,
    active_only: Annotated[
        bool,
        typer.Option("--active-only", help="Only show active agents"),
    ] = False,
) -> None:
    """List persistent agent identities."""
    orchestrator = get_orchestrator()
    workspace_id: str | None = None
    if workspace:
        ws = orchestrator.store.get_workspace_by_name(workspace)
        if ws is None:
            console.print(f"[red]Workspace not found: {workspace}[/red]")
            raise typer.Exit(code=1)
        workspace_id = ws.id

    agents = orchestrator.store.list_agents(
        workspace_id=workspace_id,
        is_active=True if active_only else None,
    )
    if not agents:
        console.print("[dim]No agents registered.[/dim]")
        return

    # Pre-fetch workspace names
    ws_names: dict[str, str] = {}
    for a in agents:
        if a.workspace_id not in ws_names:
            w = orchestrator.store.get_workspace(a.workspace_id)
            ws_names[a.workspace_id] = w.name if w else a.workspace_id[:8]

    table = Table(title="Agents")
    table.add_column("Name", style="cyan")
    table.add_column("Workspace")
    table.add_column("Role")
    table.add_column("Active")
    table.add_column("Budget", justify="right")
    table.add_column("Max concurrent", justify="right")
    table.add_column("ID", style="dim")

    for a in agents:
        budget = f"${a.monthly_budget_usd:.2f}" if a.monthly_budget_usd else "[dim]—[/dim]"
        active = "[green]yes[/green]" if a.is_active else "[dim]no[/dim]"
        table.add_row(
            a.name,
            ws_names[a.workspace_id],
            a.role,
            active,
            budget,
            str(a.max_concurrent_runs),
            a.id[:8],
        )
    console.print(table)


@agent_app.command("create")
def agent_create(
    workspace: Annotated[str, typer.Argument(help="Workspace name to attach to")],
    name: Annotated[str, typer.Argument(help="Agent name (unique within workspace)")],
    role: Annotated[str, typer.Option("--role", help="Role label (e.g. researcher, engineer)")] = "worker",
    description: Annotated[str | None, typer.Option("--description", help="Optional description")] = None,
    budget: Annotated[float | None, typer.Option("--budget", help="Monthly budget in USD")] = None,
    max_concurrent: Annotated[int, typer.Option("--max-concurrent", help="Max concurrent runs for this agent")] = 1,
) -> None:
    """Create a new persistent agent within a workspace."""
    orchestrator = get_orchestrator()
    ws = orchestrator.store.get_workspace_by_name(workspace)
    if ws is None:
        console.print(f"[red]Workspace not found: {workspace}[/red]")
        raise typer.Exit(code=1)

    import sqlite3

    try:
        agent = orchestrator.store.create_agent(
            ws.id,
            name,
            description=description,
            role=role,
            monthly_budget_usd=budget,
            max_concurrent_runs=max_concurrent,
        )
    except sqlite3.IntegrityError:
        console.print(f"[red]Agent '{name}' already exists in workspace '{workspace}'[/red]")
        raise typer.Exit(code=1) from None

    console.print(
        f"[green]Created[/green] agent [cyan]{agent.name}[/cyan] "
        f"in workspace [bold]{ws.name}[/bold] (id {agent.id[:8]})"
    )


@agent_app.command("show")
def agent_show(
    workspace: Annotated[str, typer.Argument(help="Workspace name")],
    name: Annotated[str, typer.Argument(help="Agent name")],
) -> None:
    """Show detail for a single agent."""
    orchestrator = get_orchestrator()
    ws = orchestrator.store.get_workspace_by_name(workspace)
    if ws is None:
        console.print(f"[red]Workspace not found: {workspace}[/red]")
        raise typer.Exit(code=1)
    agent = orchestrator.store.get_agent_by_name(ws.id, name)
    if agent is None:
        console.print(f"[red]Agent not found: {workspace}/{name}[/red]")
        raise typer.Exit(code=1)

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent_this_month = orchestrator.store.get_agent_monthly_spend(agent.id, month_start)
    active_runs = orchestrator.store.count_agent_active_runs(agent.id)

    console.print(f"[bold cyan]{agent.name}[/bold cyan] (id [dim]{agent.id}[/dim])")
    console.print(f"  Workspace: {ws.name}")
    console.print(f"  Role: {agent.role}")
    console.print(f"  Description: {agent.description or '[dim]—[/dim]'}")
    console.print(f"  Active: {'yes' if agent.is_active else 'no'}")

    if agent.monthly_budget_usd:
        pct = (spent_this_month / agent.monthly_budget_usd) * 100
        color = "green" if pct < 80 else ("yellow" if pct < 100 else "red")
        console.print(
            f"  Spend this month: [{color}]${spent_this_month:.2f}[/{color}] / "
            f"${agent.monthly_budget_usd:.2f} ({pct:.1f}%)"
        )
    else:
        console.print(f"  Spend this month: ${spent_this_month:.2f} (no cap)")

    console.print(f"  Max concurrent runs: {agent.max_concurrent_runs}")
    console.print(f"  Active runs: {active_runs}")
    console.print(f"  Created: {agent.created_at.isoformat()}")
    last_active = agent.last_active_at.isoformat() if agent.last_active_at else "never"
    console.print(f"  Last active: {last_active}")


@agent_app.command("update")
def agent_update(
    workspace: Annotated[str, typer.Argument(help="Workspace name")],
    name: Annotated[str, typer.Argument(help="Agent name")],
    role: Annotated[str | None, typer.Option("--role", help="New role")] = None,
    description: Annotated[str | None, typer.Option("--description", help="New description")] = None,
    budget: Annotated[float | None, typer.Option("--budget", help="New monthly budget in USD")] = None,
    max_concurrent: Annotated[int | None, typer.Option("--max-concurrent", help="New max concurrent runs")] = None,
    active: Annotated[bool | None, typer.Option("--active/--inactive", help="Toggle active status")] = None,
) -> None:
    """Update an existing agent."""
    orchestrator = get_orchestrator()
    ws = orchestrator.store.get_workspace_by_name(workspace)
    if ws is None:
        console.print(f"[red]Workspace not found: {workspace}[/red]")
        raise typer.Exit(code=1)
    agent = orchestrator.store.get_agent_by_name(ws.id, name)
    if agent is None:
        console.print(f"[red]Agent not found: {workspace}/{name}[/red]")
        raise typer.Exit(code=1)

    changes: list[str] = []
    if role is not None:
        agent.role = role
        changes.append(f"role={role}")
    if description is not None:
        agent.description = description
        changes.append("description")
    if budget is not None:
        agent.monthly_budget_usd = budget
        changes.append(f"budget=${budget:.2f}")
    if max_concurrent is not None:
        agent.max_concurrent_runs = max_concurrent
        changes.append(f"max_concurrent={max_concurrent}")
    if active is not None:
        agent.is_active = active
        changes.append(f"active={active}")

    if not changes:
        console.print("[yellow]No changes specified[/yellow]")
        return

    orchestrator.store.update_agent(agent)
    console.print(f"[green]Updated[/green] agent [cyan]{agent.name}[/cyan]: {', '.join(changes)}")


@agent_app.command("delete")
def agent_delete(
    workspace: Annotated[str, typer.Argument(help="Workspace name")],
    name: Annotated[str, typer.Argument(help="Agent name")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete an agent. Historical runs are preserved (agent_id becomes NULL)."""
    orchestrator = get_orchestrator()
    ws = orchestrator.store.get_workspace_by_name(workspace)
    if ws is None:
        console.print(f"[red]Workspace not found: {workspace}[/red]")
        raise typer.Exit(code=1)
    agent = orchestrator.store.get_agent_by_name(ws.id, name)
    if agent is None:
        console.print(f"[red]Agent not found: {workspace}/{name}[/red]")
        raise typer.Exit(code=1)

    if not force:
        confirm = typer.confirm(f"Delete agent '{workspace}/{name}'?")
        if not confirm:
            console.print("[yellow]Cancelled[/yellow]")
            return

    orchestrator.store.delete_agent(agent.id)
    console.print(f"[yellow]Deleted[/yellow] agent [cyan]{agent.name}[/cyan]")


# ========== Task Commands (Theme B Phase 3) ==========


def _resolve_task_or_exit(store: GluonStore, task_ref: str):
    """Resolve a task by ID or 8-char prefix; exit 1 with a friendly error if not found."""
    task = store.get_task(task_ref)
    if task is None:
        console.print(f"[red]Task not found:[/red] {task_ref}")
        raise typer.Exit(code=1)
    return task


def _resolve_agent_or_exit(orchestrator: Orchestrator, workspace_id: str | None, name_or_id: str):
    """Resolve an agent reference; exit 1 with a friendly error if not found."""
    from gluon.core import AgentAmbiguousError, AgentNotFoundError

    try:
        agent_id = orchestrator.resolve_agent(name_or_id, workspace_id)
    except (AgentNotFoundError, AgentAmbiguousError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None
    if agent_id is None:
        console.print(f"[red]Could not resolve agent:[/red] {name_or_id}")
        raise typer.Exit(code=1)
    return agent_id


def _render_task_row(task, project_name: str, agent_name: str | None) -> list[str]:
    """Format a task as a table row."""
    status_color = {
        "backlog": "dim",
        "assigned": "cyan",
        "in_progress": "blue",
        "review": "magenta",
        "done": "green",
        "cancelled": "dim",
    }.get(task.status.value, "white")

    title_preview = (task.title[:50] + "…") if len(task.title) > 50 else task.title

    return [
        task.id[:8],
        project_name,
        str(task.priority),
        f"[{status_color}]{task.status.value}[/{status_color}]",
        agent_name or "[dim]—[/dim]",
        title_preview,
        task.created_at.strftime("%Y-%m-%d %H:%M"),
    ]


@task_app.command("create")
def task_create(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    title: Annotated[str, typer.Argument(help="Task title")],
    description: Annotated[str | None, typer.Option("--description", "-d", help="Task description (optional)")] = None,
    priority: Annotated[int, typer.Option("--priority", "-P", help="Priority 1-10, higher runs first")] = 5,
    assign: Annotated[str | None, typer.Option("--assign", "-a", help="Assign to agent name or ID")] = None,
    files: Annotated[
        str | None,
        typer.Option("--files", help="Comma-separated files/globs this task touches (advisory)"),
    ] = None,
) -> None:
    """Create a new task on a project."""
    orchestrator = get_orchestrator()
    try:
        proj = orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None

    assigned_agent_id: str | None = None
    if assign:
        assigned_agent_id = _resolve_agent_or_exit(orchestrator, proj.workspace_id, assign)

    assigned_files = [f.strip() for f in files.split(",")] if files else []

    task = orchestrator.store.create_task(
        project_id=proj.id,
        title=title,
        description=description,
        priority=priority,
        assigned_agent_id=assigned_agent_id,
        created_by="cli",
        assigned_files=assigned_files,
    )

    console.print(f"[green]Created[/green] task [cyan]{task.id[:8]}[/cyan] on [bold]{proj.name}[/bold]")
    console.print(f"  Title: {task.title}")
    console.print(f"  Priority: {task.priority}")
    console.print(f"  Status: {task.status.value}")
    if assigned_agent_id:
        agent = orchestrator.store.get_agent(assigned_agent_id)
        console.print(f"  Assigned to: [cyan]{agent.name if agent else assigned_agent_id[:8]}[/cyan]")
    if assigned_files:
        console.print(f"  Files: {', '.join(assigned_files)}")


@task_app.command("list")
def task_list(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Filter by project name")] = None,
    agent: Annotated[str | None, typer.Option("--agent", "-a", help="Filter by assigned agent name")] = None,
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            "-s",
            help="Filter by status (backlog/assigned/in_progress/review/done/cancelled)",
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max tasks to show")] = 50,
) -> None:
    """List tasks with optional filters."""
    orchestrator = get_orchestrator()

    project_id: str | None = None
    workspace_id: str | None = None
    if project:
        proj = orchestrator.get_project(project)
        project_id = proj.id
        workspace_id = proj.workspace_id

    agent_id: str | None = None
    if agent:
        agent_id = _resolve_agent_or_exit(orchestrator, workspace_id, agent)

    tasks = orchestrator.store.list_tasks(
        project_id=project_id,
        agent_id=agent_id,
        status=status,
        limit=limit,
    )

    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    # Pre-fetch projects and agents for display
    project_cache: dict[str, str] = {}
    agent_cache: dict[str, str] = {}
    for t in tasks:
        if t.project_id not in project_cache:
            p = orchestrator.store.get_project(t.project_id)
            project_cache[t.project_id] = p.name if p else t.project_id[:8]
        if t.assigned_agent_id and t.assigned_agent_id not in agent_cache:
            a = orchestrator.store.get_agent(t.assigned_agent_id)
            agent_cache[t.assigned_agent_id] = a.name if a else t.assigned_agent_id[:8]

    table = Table(title="Tasks")
    table.add_column("ID", style="dim")
    table.add_column("Project")
    table.add_column("Pri", justify="right")
    table.add_column("Status")
    table.add_column("Agent", style="cyan")
    table.add_column("Title")
    table.add_column("Created", style="dim")

    for t in tasks:
        table.add_row(
            *_render_task_row(
                t,
                project_cache[t.project_id],
                agent_cache.get(t.assigned_agent_id) if t.assigned_agent_id else None,
            )
        )

    console.print(table)


@task_app.command("show")
def task_show(
    task_ref: Annotated[str, typer.Argument(help="Task ID or 8-char prefix")],
) -> None:
    """Show detail for a task including its comments."""
    orchestrator = get_orchestrator()
    task = _resolve_task_or_exit(orchestrator.store, task_ref)

    project = orchestrator.store.get_project(task.project_id)
    project_name = project.name if project else task.project_id[:8]

    agent_name = None
    if task.assigned_agent_id:
        agent = orchestrator.store.get_agent(task.assigned_agent_id)
        agent_name = agent.name if agent else task.assigned_agent_id[:8]

    console.print(f"[bold cyan]{task.title}[/bold cyan] (id [dim]{task.id}[/dim])")
    console.print(f"  Project: {project_name}")
    console.print(f"  Priority: {task.priority}")
    console.print(f"  Status: [bold]{task.status.value}[/bold]")
    console.print(f"  Assigned to: {agent_name or '[dim]unassigned[/dim]'}")
    console.print(f"  Created by: {task.created_by}")
    console.print(f"  Created: {task.created_at.isoformat()}")
    if task.completed_at:
        console.print(f"  Completed: {task.completed_at.isoformat()}")
    if task.assigned_files:
        console.print(f"  Files: {', '.join(task.assigned_files)}")
    if task.execution_run_id:
        console.print(f"  Locked by run: [dim]{task.execution_run_id[:8]}[/dim]")
    if task.description:
        console.print()
        console.print("[bold]Description[/bold]")
        console.print(task.description)

    comments = orchestrator.store.list_task_comments(task.id)
    if comments:
        console.print()
        console.print(f"[bold]Comments[/bold] ({len(comments)})")
        for c in comments:
            author = c.author_label or (c.author_agent_id[:8] if c.author_agent_id else "system")
            ts = c.created_at.strftime("%Y-%m-%d %H:%M")
            console.print(f"  [dim]{ts}[/dim] [cyan]{author}[/cyan]: {c.content}")


@task_app.command("assign")
def task_assign(
    task_ref: Annotated[str, typer.Argument(help="Task ID or prefix")],
    agent_ref: Annotated[str, typer.Argument(help="Agent name or ID")],
) -> None:
    """Assign a task to an agent (also sets status to ASSIGNED if currently BACKLOG)."""
    from gluon.models import TaskStatus

    orchestrator = get_orchestrator()
    task = _resolve_task_or_exit(orchestrator.store, task_ref)
    project = orchestrator.store.get_project(task.project_id)
    workspace_id = project.workspace_id if project else None

    agent_id = _resolve_agent_or_exit(orchestrator, workspace_id, agent_ref)

    task.assigned_agent_id = agent_id
    if task.status == TaskStatus.BACKLOG:
        task.status = TaskStatus.ASSIGNED
    orchestrator.store.update_task(task)

    agent = orchestrator.store.get_agent(agent_id)
    console.print(
        f"[green]Assigned[/green] task [cyan]{task.id[:8]}[/cyan] to "
        f"[bold]{agent.name if agent else agent_id[:8]}[/bold]"
    )


@task_app.command("done")
def task_done(
    task_ref: Annotated[str, typer.Argument(help="Task ID or prefix")],
) -> None:
    """Mark a task as DONE and release any execution lock."""
    from gluon.models import TaskStatus

    orchestrator = get_orchestrator()
    task = _resolve_task_or_exit(orchestrator.store, task_ref)
    released = orchestrator.store.release_task(task.id, TaskStatus.DONE)
    console.print(f"[green]✓ Done[/green] task [cyan]{released.id[:8]}[/cyan]: {released.title}")


@task_app.command("cancel")
def task_cancel(
    task_ref: Annotated[str, typer.Argument(help="Task ID or prefix")],
) -> None:
    """Cancel a task and release any execution lock."""
    from gluon.models import TaskStatus

    orchestrator = get_orchestrator()
    task = _resolve_task_or_exit(orchestrator.store, task_ref)
    released = orchestrator.store.release_task(task.id, TaskStatus.CANCELLED)
    console.print(f"[yellow]Cancelled[/yellow] task [cyan]{released.id[:8]}[/cyan]: {released.title}")


@task_app.command("inbox")
def task_inbox(
    workspace: Annotated[str, typer.Argument(help="Workspace name")],
    agent: Annotated[str, typer.Argument(help="Agent name or ID")],
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max tasks to show")] = 20,
) -> None:
    """Show an agent's task inbox (ASSIGNED + IN_PROGRESS, priority-ordered)."""
    orchestrator = get_orchestrator()
    ws = orchestrator.store.get_workspace_by_name(workspace)
    if ws is None:
        console.print(f"[red]Workspace not found:[/red] {workspace}")
        raise typer.Exit(code=1)

    agent_id = _resolve_agent_or_exit(orchestrator, ws.id, agent)
    agent_obj = orchestrator.store.get_agent(agent_id)
    agent_name = agent_obj.name if agent_obj else agent_id[:8]

    tasks = orchestrator.store.get_agent_inbox(agent_id, limit=limit)
    if not tasks:
        console.print(f"[dim]Inbox for {agent_name} is empty.[/dim]")
        return

    project_cache: dict[str, str] = {}
    for t in tasks:
        if t.project_id not in project_cache:
            p = orchestrator.store.get_project(t.project_id)
            project_cache[t.project_id] = p.name if p else t.project_id[:8]

    table = Table(title=f"Inbox — {agent_name}")
    table.add_column("ID", style="dim")
    table.add_column("Project")
    table.add_column("Pri", justify="right")
    table.add_column("Status")
    table.add_column("Title")
    table.add_column("Created", style="dim")

    for t in tasks:
        row = _render_task_row(t, project_cache[t.project_id], agent_name)
        # Drop the agent column (we know it's this agent) to keep the inbox compact
        table.add_row(row[0], row[1], row[2], row[3], row[5], row[6])

    console.print(table)


@task_app.command("comment")
def task_comment(
    task_ref: Annotated[str, typer.Argument(help="Task ID or prefix")],
    message: Annotated[str, typer.Argument(help="Comment text")],
    author: Annotated[
        str | None,
        typer.Option("--author", help="Author label for the comment (default: 'cli')"),
    ] = None,
) -> None:
    """Add a comment to a task."""
    orchestrator = get_orchestrator()
    task = _resolve_task_or_exit(orchestrator.store, task_ref)

    comment = orchestrator.store.add_task_comment(
        task.id,
        content=message,
        author_label=author or "cli",
    )
    console.print(f"[green]Comment added[/green] to [cyan]{task.id[:8]}[/cyan] (comment [dim]{comment.id[:8]}[/dim])")


@task_app.command("delete")
def task_delete(
    task_ref: Annotated[str, typer.Argument(help="Task ID or prefix")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a task and all its comments. Permanent."""
    orchestrator = get_orchestrator()
    task = _resolve_task_or_exit(orchestrator.store, task_ref)

    if not force:
        confirm = typer.confirm(f"Delete task '{task.title}' and its comments?")
        if not confirm:
            console.print("[yellow]Cancelled[/yellow]")
            return

    orchestrator.store.delete_task(task.id)
    console.print(f"[yellow]Deleted[/yellow] task [cyan]{task.id[:8]}[/cyan]")


# ========== Schedule Commands (Theme B Phase 2) ==========


def _resolve_workspace_or_exit(store: GluonStore, name: str):
    ws = store.get_workspace_by_name(name)
    if ws is None:
        console.print(f"[red]Workspace not found:[/red] {name}")
        raise typer.Exit(code=1)
    return ws


@schedule_app.command("create")
def schedule_create(
    workspace: Annotated[str, typer.Argument(help="Workspace name")],
    agent: Annotated[str, typer.Argument(help="Agent name or ID")],
    cron: Annotated[
        str,
        typer.Option(
            "--cron",
            help='Cron expression (5 fields, UTC). Examples: "*/30 * * * *", "0 9 * * *"',
        ),
    ],
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", help="Prompt template (use {inbox_summary}, {inbox_count}, etc.)"),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option("--project", "-p", help="Pin to a specific project (default: workspace-wide)"),
    ] = None,
    profile: Annotated[
        str,
        typer.Option("--profile", help="Task profile for heartbeat runs (default: quick)"),
    ] = "quick",
    coalesce: Annotated[
        int, typer.Option("--coalesce-ttl", help="Coalesce window in seconds (skip if heartbeat still live)")
    ] = 300,
    description: Annotated[str | None, typer.Option("--description", "-d", help="Human-readable description")] = None,
) -> None:
    """Create a cron-scheduled wakeup for an agent.

    Example: gluon schedule create ml-research researcher \\
             --cron "0 */6 * * *" --prompt "Review open PRs and pick the next task"
    """
    from gluon.scheduler import DEFAULT_PROMPT_TEMPLATE, compute_next_fire, validate_cron

    orchestrator = get_orchestrator()
    ws = _resolve_workspace_or_exit(orchestrator.store, workspace)
    agent_id = _resolve_agent_or_exit(orchestrator, ws.id, agent)

    if not validate_cron(cron):
        console.print(f"[red]Invalid cron expression:[/red] {cron}")
        raise typer.Exit(code=1)

    project_id = None
    if project:
        try:
            proj = orchestrator.get_project(project)
            project_id = proj.id
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

    next_fire = compute_next_fire(cron)

    schedule = orchestrator.store.create_schedule(
        agent_id=agent_id,
        project_id=project_id,
        prompt_template=prompt or DEFAULT_PROMPT_TEMPLATE,
        schedule_cron=cron,
        coalesce_ttl_seconds=coalesce,
        task_profile=profile,
        description=description,
        next_fire_at=next_fire,
    )

    agent_obj = orchestrator.store.get_agent(agent_id)
    console.print(
        f"[green]Created[/green] schedule [cyan]{schedule.id[:8]}[/cyan] for agent "
        f"[bold]{agent_obj.name if agent_obj else agent_id[:8]}[/bold]"
    )
    console.print(f"  Cron: {cron}")
    console.print(f"  Profile: {schedule.task_profile}")
    console.print(f"  Coalesce TTL: {schedule.coalesce_ttl_seconds}s")
    console.print(f"  Next fire: {next_fire.isoformat()}")


@schedule_app.command("list")
def schedule_list(
    agent: Annotated[
        str | None, typer.Option("--agent", "-a", help="Filter by agent name (requires --workspace)")
    ] = None,
    workspace: Annotated[str | None, typer.Option("--workspace", "-w", help="Workspace for agent lookup")] = None,
    enabled_only: Annotated[bool, typer.Option("--enabled-only", help="Only show enabled schedules")] = False,
) -> None:
    """List scheduled wakeups."""
    orchestrator = get_orchestrator()

    agent_id: str | None = None
    if agent:
        if workspace is None:
            console.print("[red]--agent requires --workspace[/red]")
            raise typer.Exit(code=1)
        ws = _resolve_workspace_or_exit(orchestrator.store, workspace)
        agent_id = _resolve_agent_or_exit(orchestrator, ws.id, agent)

    schedules = orchestrator.store.list_schedules(agent_id=agent_id, enabled_only=enabled_only)
    if not schedules:
        console.print("[dim]No schedules configured.[/dim]")
        return

    # Build agent name cache
    agent_names: dict[str, str] = {}
    for s in schedules:
        if s.agent_id not in agent_names:
            a = orchestrator.store.get_agent(s.agent_id)
            agent_names[s.agent_id] = a.name if a else s.agent_id[:8]

    table = Table(title="Schedules")
    table.add_column("ID", style="dim")
    table.add_column("Agent", style="cyan")
    table.add_column("Cron")
    table.add_column("Profile")
    table.add_column("Enabled")
    table.add_column("Failures", justify="right")
    table.add_column("Next fire", style="dim")

    for s in schedules:
        next_str = s.next_fire_at.strftime("%Y-%m-%d %H:%M") if s.next_fire_at else "—"
        enabled_str = "[green]yes[/green]" if s.is_enabled else "[dim]no[/dim]"
        fail_str = f"[red]{s.consecutive_failures}[/red]" if s.consecutive_failures else "0"
        table.add_row(
            s.id[:8],
            agent_names[s.agent_id],
            s.schedule_cron,
            s.task_profile,
            enabled_str,
            fail_str,
            next_str,
        )
    console.print(table)


@schedule_app.command("show")
def schedule_show(
    schedule_id: Annotated[str, typer.Argument(help="Schedule ID or prefix")],
) -> None:
    """Show detail for a schedule."""
    orchestrator = get_orchestrator()
    sched = orchestrator.store.get_schedule(schedule_id)
    if sched is None:
        console.print(f"[red]Schedule not found:[/red] {schedule_id}")
        raise typer.Exit(code=1)

    agent = orchestrator.store.get_agent(sched.agent_id)
    agent_name = agent.name if agent else sched.agent_id[:8]

    console.print(f"[bold cyan]Schedule {sched.id[:8]}[/bold cyan] (id [dim]{sched.id}[/dim])")
    console.print(f"  Agent: {agent_name}")
    console.print(f"  Cron: {sched.schedule_cron}")
    console.print(f"  Enabled: {'yes' if sched.is_enabled else 'no'}")
    console.print(f"  Profile: {sched.task_profile}")
    console.print(f"  Coalesce TTL: {sched.coalesce_ttl_seconds}s")
    console.print(f"  Consecutive failures: {sched.consecutive_failures}")
    if sched.description:
        console.print(f"  Description: {sched.description}")
    if sched.project_id:
        p = orchestrator.store.get_project(sched.project_id)
        console.print(f"  Project: {p.name if p else sched.project_id[:8]}")
    last_fire = sched.last_fired_at.isoformat() if sched.last_fired_at else "never"
    console.print(f"  Last fire: {last_fire}")
    next_fire = sched.next_fire_at.isoformat() if sched.next_fire_at else "pending"
    console.print(f"  Next fire: {next_fire}")
    console.print()
    console.print("[bold]Prompt template[/bold]")
    console.print(sched.prompt_template)


@schedule_app.command("enable")
def schedule_enable(
    schedule_id: Annotated[str, typer.Argument(help="Schedule ID or prefix")],
) -> None:
    """Enable a schedule (also resets consecutive_failures)."""
    orchestrator = get_orchestrator()
    sched = orchestrator.store.get_schedule(schedule_id)
    if sched is None:
        console.print(f"[red]Schedule not found:[/red] {schedule_id}")
        raise typer.Exit(code=1)

    sched.is_enabled = True
    sched.consecutive_failures = 0
    orchestrator.store.update_schedule(sched)
    console.print(f"[green]Enabled[/green] schedule [cyan]{sched.id[:8]}[/cyan]")


@schedule_app.command("disable")
def schedule_disable(
    schedule_id: Annotated[str, typer.Argument(help="Schedule ID or prefix")],
) -> None:
    """Disable a schedule (stops firing until re-enabled)."""
    orchestrator = get_orchestrator()
    sched = orchestrator.store.get_schedule(schedule_id)
    if sched is None:
        console.print(f"[red]Schedule not found:[/red] {schedule_id}")
        raise typer.Exit(code=1)

    sched.is_enabled = False
    orchestrator.store.update_schedule(sched)
    console.print(f"[yellow]Disabled[/yellow] schedule [cyan]{sched.id[:8]}[/cyan]")


@schedule_app.command("delete")
def schedule_delete(
    schedule_id: Annotated[str, typer.Argument(help="Schedule ID or prefix")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a schedule and all its heartbeat history."""
    orchestrator = get_orchestrator()
    sched = orchestrator.store.get_schedule(schedule_id)
    if sched is None:
        console.print(f"[red]Schedule not found:[/red] {schedule_id}")
        raise typer.Exit(code=1)

    if not force:
        confirm = typer.confirm(f"Delete schedule {sched.id[:8]} ({sched.schedule_cron})?")
        if not confirm:
            console.print("[yellow]Cancelled[/yellow]")
            return

    orchestrator.store.delete_schedule(sched.id)
    console.print(f"[yellow]Deleted[/yellow] schedule [cyan]{sched.id[:8]}[/cyan]")


@schedule_app.command("fire")
def schedule_fire(
    schedule_id: Annotated[str, typer.Argument(help="Schedule ID or prefix")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip coalesce check — fire even if a heartbeat is still live"),
    ] = False,
) -> None:
    """Manually fire a schedule for testing. Spawns a heartbeat run right now."""
    from gluon.runner import TaskRunner
    from gluon.scheduler import HeartbeatScheduler

    orchestrator = get_orchestrator()
    sched = orchestrator.store.get_schedule(schedule_id)
    if sched is None:
        console.print(f"[red]Schedule not found:[/red] {schedule_id}")
        raise typer.Exit(code=1)

    async def _fire():
        runner = TaskRunner(store=orchestrator.store)
        scheduler = HeartbeatScheduler(orchestrator.store, runner)
        heartbeat = await scheduler.fire_heartbeat(sched, force=force)
        status_color = {
            "pending": "yellow",
            "running": "green",
            "completed": "green",
            "failed": "red",
            "coalesced": "dim",
            "skipped": "dim",
        }.get(heartbeat.status.value, "white")
        console.print(
            f"[{status_color}]{heartbeat.status.value}[/{status_color}] heartbeat [cyan]{heartbeat.id[:8]}[/cyan]"
        )
        if heartbeat.execution_run_id:
            console.print(f"  Run: [dim]{heartbeat.execution_run_id[:8]}[/dim]")
        if heartbeat.result_summary:
            console.print(f"  Summary: {heartbeat.result_summary}")
        if heartbeat.error_message:
            console.print(f"  [red]Error:[/red] {heartbeat.error_message}")

    anyio.run(_fire)


# ========== Heartbeat Commands ==========


@heartbeat_app.command("list")
def heartbeat_list(
    schedule_id: Annotated[str | None, typer.Option("--schedule", "-s", help="Filter by schedule ID")] = None,
    agent: Annotated[
        str | None, typer.Option("--agent", "-a", help="Filter by agent name (requires --workspace)")
    ] = None,
    workspace: Annotated[str | None, typer.Option("--workspace", "-w", help="Workspace for agent lookup")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max heartbeats to show")] = 20,
) -> None:
    """List recent heartbeat firings."""
    orchestrator = get_orchestrator()

    resolved_schedule_id: str | None = None
    if schedule_id:
        sched = orchestrator.store.get_schedule(schedule_id)
        if sched is None:
            console.print(f"[red]Schedule not found:[/red] {schedule_id}")
            raise typer.Exit(code=1)
        resolved_schedule_id = sched.id

    agent_id: str | None = None
    if agent:
        if workspace is None:
            console.print("[red]--agent requires --workspace[/red]")
            raise typer.Exit(code=1)
        ws = _resolve_workspace_or_exit(orchestrator.store, workspace)
        agent_id = _resolve_agent_or_exit(orchestrator, ws.id, agent)

    heartbeats = orchestrator.store.list_heartbeats(schedule_id=resolved_schedule_id, agent_id=agent_id, limit=limit)

    if not heartbeats:
        console.print("[dim]No heartbeats recorded.[/dim]")
        return

    # Cache agent + schedule names
    agent_names: dict[str, str] = {}
    for h in heartbeats:
        if h.agent_id not in agent_names:
            a = orchestrator.store.get_agent(h.agent_id)
            agent_names[h.agent_id] = a.name if a else h.agent_id[:8]

    table = Table(title="Heartbeats")
    table.add_column("ID", style="dim")
    table.add_column("Agent", style="cyan")
    table.add_column("Schedule", style="dim")
    table.add_column("Status")
    table.add_column("Run", style="dim")
    table.add_column("Fired", style="dim")
    table.add_column("Summary")

    status_colors = {
        "pending": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "coalesced": "dim",
        "skipped": "dim",
    }

    for h in heartbeats:
        status_color = status_colors.get(h.status.value, "white")
        run_str = h.execution_run_id[:8] if h.execution_run_id else "—"
        summary = (h.result_summary or "")[:40]
        table.add_row(
            h.id[:8],
            agent_names[h.agent_id],
            h.schedule_id[:8],
            f"[{status_color}]{h.status.value}[/{status_color}]",
            run_str,
            h.fired_at.strftime("%Y-%m-%d %H:%M"),
            summary,
        )
    console.print(table)


# ========== Approval Commands (Theme D1) ==========


@approvals_app.command("list")
def approvals_list(
    status: Annotated[
        str | None,
        typer.Option("--status", "-s", help="Filter by status (pending/granted/denied/expired)"),
    ] = "pending",
    run: Annotated[str | None, typer.Option("--run", "-r", help="Filter by run ID or short prefix")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max rows to show")] = 50,
) -> None:
    """List approval requests — defaults to pending-only.

    Use `--status all` (or pass any invalid value) to see everything.
    """
    from gluon.models import ApprovalStatus

    orchestrator = get_orchestrator()

    resolved_status = None
    if status and status.lower() != "all":
        try:
            resolved_status = ApprovalStatus(status.lower())
        except ValueError:
            console.print(
                f"[red]Invalid status:[/red] {status}. Must be one of {[s.value for s in ApprovalStatus]} (or 'all')."
            )
            raise typer.Exit(code=1) from None

    resolved_run_id = None
    if run:
        target = orchestrator.store.get_run_by_short_id(run) or orchestrator.store.get_run(run)
        if target is None:
            console.print(f"[red]Run not found:[/red] {run}")
            raise typer.Exit(code=1)
        resolved_run_id = target.id

    approvals = orchestrator.store.list_approvals(run_id=resolved_run_id, status=resolved_status, limit=limit)

    if not approvals:
        console.print("[dim]No approvals found.[/dim]")
        return

    status_colors = {
        "pending": "yellow",
        "granted": "green",
        "denied": "red",
        "expired": "dim",
    }

    table = Table(title="Approvals")
    table.add_column("ID", style="dim")
    table.add_column("Run", style="dim")
    table.add_column("Tool", style="cyan")
    table.add_column("Status")
    table.add_column("Reason")
    table.add_column("Created", style="dim")

    for a in approvals:
        color = status_colors.get(a.status.value, "white")
        reason_preview = (
            (a.classification_reason[:60] + "…") if len(a.classification_reason) > 60 else a.classification_reason
        )
        table.add_row(
            a.id[:8],
            a.run_id[:8],
            a.tool_name,
            f"[{color}]{a.status.value}[/{color}]",
            reason_preview,
            a.created_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@approvals_app.command("show")
def approvals_show(
    approval_id: Annotated[str, typer.Argument(help="Approval ID or 8-char prefix")],
) -> None:
    """Show detail for a single approval, including the tool input."""
    import json as _json

    orchestrator = get_orchestrator()
    approval = orchestrator.store.get_approval(approval_id)
    if approval is None:
        console.print(f"[red]Approval not found:[/red] {approval_id}")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Approval {approval.id[:8]}[/bold cyan] ([dim]{approval.id}[/dim])")
    console.print(f"  Run: [dim]{approval.run_id[:8]}[/dim]")
    console.print(f"  Tool: [bold]{approval.tool_name}[/bold]")
    console.print(f"  Status: [bold]{approval.status.value}[/bold]")
    console.print(f"  Classification: {approval.classification_reason}")
    if approval.tool_use_id:
        console.print(f"  tool_use_id: [dim]{approval.tool_use_id}[/dim]")
    console.print(f"  Created: {approval.created_at.isoformat()}")
    if approval.timeout_at:
        console.print(f"  Timeout at: {approval.timeout_at.isoformat()}")
    if approval.decided_at:
        console.print(f"  Decided: {approval.decided_at.isoformat()} by {approval.decided_by}")
    if approval.decision_reason:
        console.print(f"  Decision reason: {approval.decision_reason}")
    console.print()
    console.print("[bold]Tool input[/bold]")
    console.print(_json.dumps(approval.tool_input, indent=2))


@approvals_app.command("grant")
def approvals_grant(
    approval_id: Annotated[str, typer.Argument(help="Approval ID or 8-char prefix")],
    reason: Annotated[str | None, typer.Option("--reason", help="Optional reason for the approval")] = None,
) -> None:
    """Grant an approval — the blocking hook will unblock and allow the tool call."""
    from gluon.models import ApprovalStatus

    orchestrator = get_orchestrator()
    approval = orchestrator.store.get_approval(approval_id)
    if approval is None:
        console.print(f"[red]Approval not found:[/red] {approval_id}")
        raise typer.Exit(code=1)
    if approval.status != ApprovalStatus.PENDING:
        console.print(f"[red]Approval already {approval.status.value}[/red]")
        raise typer.Exit(code=1)

    updated = orchestrator.store.decide_approval(
        approval.id,
        status=ApprovalStatus.GRANTED,
        decided_by="cli",
        decision_reason=reason,
    )
    if updated is None:
        console.print("[red]Approval vanished[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Granted[/green] approval [cyan]{updated.id[:8]}[/cyan] ({updated.tool_name})")


@approvals_app.command("deny")
def approvals_deny(
    approval_id: Annotated[str, typer.Argument(help="Approval ID or 8-char prefix")],
    reason: Annotated[str | None, typer.Option("--reason", help="Reason for denial — surfaced to the agent")] = None,
) -> None:
    """Deny an approval — the hook will return `permissionDecision: deny` to the SDK."""
    from gluon.models import ApprovalStatus

    orchestrator = get_orchestrator()
    approval = orchestrator.store.get_approval(approval_id)
    if approval is None:
        console.print(f"[red]Approval not found:[/red] {approval_id}")
        raise typer.Exit(code=1)
    if approval.status != ApprovalStatus.PENDING:
        console.print(f"[red]Approval already {approval.status.value}[/red]")
        raise typer.Exit(code=1)

    updated = orchestrator.store.decide_approval(
        approval.id,
        status=ApprovalStatus.DENIED,
        decided_by="cli",
        decision_reason=reason or "Denied via CLI",
    )
    if updated is None:
        console.print("[red]Approval vanished[/red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]Denied[/yellow] approval [cyan]{updated.id[:8]}[/cyan] ({updated.tool_name})")


@approvals_app.command("expire")
def approvals_expire() -> None:
    """Manually expire any pending approvals past their timeout. Returns count."""
    orchestrator = get_orchestrator()
    count = orchestrator.store.expire_stale_approvals()
    if count == 0:
        console.print("[green]No stale approvals found[/green]")
    else:
        console.print(f"[yellow]Expired {count} stale approval(s)[/yellow]")


# ========== Session Cleanup (Theme C5) ==========


def _format_bytes_short(num_bytes: int) -> str:
    """Format a byte count compactly."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024  # type: ignore[assignment]
    return f"{num_bytes} B"


@app.command("sessions-cleanup")
def sessions_cleanup(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview what would be deleted without deleting"),
    ] = False,
    older_than_days: Annotated[
        int | None,
        typer.Option("--older-than", help="Only delete sessions older than N days (default: from settings)"),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option("--project", "-p", help="Limit sweep to a single project"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Delete stale Claude session JSONL files (Theme C5).

    Scopes:
      - Per-run tracked ancestors (previous_session_ids in metadata) are deleted
        automatically when a run reaches COMPLETED, if session_cleanup_enabled=true.
      - This command is the batch sweeper for orphan files on disk.

    By default, sessions referenced by any run (current or previous) are kept,
    and sessions younger than `session_cleanup_retention_days` (default 30)
    are kept. Use --older-than to override.
    """
    from gluon.session_cleanup import cleanup_orphan_sessions, get_retention_days

    orchestrator = get_orchestrator()

    project_dir: str | None = None
    if project:
        try:
            proj = orchestrator.get_project(project)
            project_dir = str(proj.expanded_path)
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from None

    effective_days = older_than_days if older_than_days is not None else get_retention_days(orchestrator.store)

    console.print(
        f"[dim]Scanning for orphan sessions older than {effective_days} days"
        + (f" in project [cyan]{project}[/cyan]" if project else " (all projects)")
        + "...[/dim]"
    )

    preview = cleanup_orphan_sessions(
        orchestrator.store,
        directory=project_dir,
        older_than_days=effective_days,
        dry_run=True,
    )

    from gluon.session_cleanup import CleanupPreview

    assert isinstance(preview, CleanupPreview)

    console.print()
    console.print(f"[bold]Candidates for deletion:[/bold] {preview.count}")
    console.print(f"  Skipped (referenced by a run): {preview.skipped_referenced}")
    console.print(f"  Skipped (within retention):    {preview.skipped_recent}")
    console.print(f"  Total bytes to free:           {_format_bytes_short(preview.total_bytes)}")

    if preview.count == 0:
        console.print()
        console.print("[green]Nothing to clean up.[/green]")
        return

    if dry_run:
        console.print()
        console.print("[dim]Dry run — no files deleted.[/dim]")
        return

    if not force:
        console.print()
        confirm = typer.confirm(f"Delete {preview.count} session JSONL files?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            return

    result = cleanup_orphan_sessions(
        orchestrator.store,
        directory=project_dir,
        older_than_days=effective_days,
        dry_run=False,
    )

    from gluon.session_cleanup import CleanupResult

    assert isinstance(result, CleanupResult)

    console.print()
    console.print("[bold]Cleanup complete:[/bold]")
    console.print(f"  Deleted: [green]{result.deleted}[/green]")
    console.print(f"  Failed:  [red]{result.failed}[/red]")
    console.print(f"  Freed:   {_format_bytes_short(result.bytes_freed)}")

    if result.failed:
        console.print()
        console.print("[red]Failed session IDs:[/red]")
        for sid in result.errors[:10]:
            console.print(f"  - {sid}")
        if len(result.errors) > 10:
            console.print(f"  ...and {len(result.errors) - 10} more")


@queue_app.command("release")
def queue_release_stale(
    threshold_minutes: Annotated[
        int,
        typer.Option("--threshold", "-t", help="Release items claimed more than N minutes ago"),
    ] = 30,
) -> None:
    """Release stale CLAIMED items back to PENDING.

    Useful when a runner crashed and left queue items stuck. Default threshold
    is 30 minutes with no heartbeat.
    """
    store = GluonStore()
    released = store.release_stale_work_claims(threshold_secs=threshold_minutes * 60)

    if released == 0:
        console.print("[green]No stale claims found[/green]")
    else:
        console.print(f"[yellow]Released {released} stale claim(s) back to pending[/yellow]")


# ========== Claude Session Explorer Commands (C4) ==========

claude_sessions_app = typer.Typer(help="Browse Claude Code sessions for a project (read-only)")
app.add_typer(claude_sessions_app, name="claude-sessions")


def _resolve_project_or_exit(name: str):
    """Resolve a project by name/id, printing an error and exiting on failure."""
    orchestrator = get_orchestrator()
    try:
        return orchestrator.get_project(name)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)


def _format_ms_to_human(ms: int | None) -> str:
    """Format an epoch-milliseconds value as a local-time string."""
    if ms is None:
        return "-"
    try:
        from datetime import datetime as _dt

        return _dt.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "-"


def _format_bytes_compact(size: int | None) -> str:
    """Compact byte-size formatter used by the claude-sessions table."""
    if size is None:
        return "-"
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}K"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}M"
    return f"{size / (1024 * 1024 * 1024):.1f}G"


def _flatten_claude_cli_message(raw: object) -> str:
    """Best-effort flattening of a SessionMessage.message payload to text."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    tval = block.get("text")
                    if isinstance(tval, str):
                        parts.append(tval)
                elif btype == "tool_use":
                    parts.append(f"[tool_use: {block.get('name') or 'tool'}]")
                elif btype == "tool_result":
                    parts.append("[tool_result]")
            return "\n".join(p for p in parts if p)
    return str(raw)


@claude_sessions_app.command("list")
def claude_sessions_list(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max sessions to list")] = 20,
    tag: Annotated[str | None, typer.Option("--tag", help="Filter by session tag")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """List Claude Code sessions stored for a project."""
    proj = _resolve_project_or_exit(project)

    try:
        from claude_agent_sdk import list_sessions
    except ImportError:
        console.print("[red]Error:[/red] claude_agent_sdk is not installed.")
        raise typer.Exit(code=2)

    try:
        sessions = list_sessions(
            directory=str(proj.expanded_path),
            limit=limit,
            include_worktrees=True,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to list sessions: {e}")
        raise typer.Exit(code=1)

    if tag:
        sessions = [s for s in sessions if getattr(s, "tag", None) == tag]

    if json_out:
        import json as _json

        payload = [
            {
                "session_id": s.session_id,
                "summary": s.summary,
                "last_modified_ms": s.last_modified,
                "file_size": s.file_size,
                "tag": getattr(s, "tag", None),
                "created_at_ms": getattr(s, "created_at", None),
                "git_branch": s.git_branch,
                "cwd": s.cwd,
                "first_prompt": s.first_prompt,
                "custom_title": s.custom_title,
            }
            for s in sessions
        ]
        console.print_json(_json.dumps(payload))
        return

    if not sessions:
        console.print(f"[dim]No Claude sessions found for {proj.name}.[/dim]")
        return

    table = Table(title=f"Claude sessions for {proj.name}")
    table.add_column("ID", style="cyan", width=10)
    table.add_column("Git Branch", width=22)
    table.add_column("Summary", max_width=42)
    table.add_column("Size", justify="right", width=7)
    table.add_column("Last Modified", style="dim", width=16)

    for s in sessions:
        summary = (s.custom_title or s.summary or s.first_prompt or "-").strip().replace("\n", " ")
        if len(summary) > 40:
            summary = summary[:39] + "…"
        table.add_row(
            (s.session_id or "")[:8],
            (s.git_branch or "-")[:22],
            summary,
            _format_bytes_compact(s.file_size),
            _format_ms_to_human(s.last_modified),
        )

    console.print(table)


@claude_sessions_app.command("show")
def claude_sessions_show(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    session_id: Annotated[str, typer.Argument(help="Session UUID")],
) -> None:
    """Show detailed metadata for a single Claude Code session."""
    proj = _resolve_project_or_exit(project)

    try:
        from claude_agent_sdk import get_session_info
    except ImportError:
        console.print("[red]Error:[/red] claude_agent_sdk is not installed.")
        raise typer.Exit(code=2)

    try:
        info = get_session_info(session_id=session_id, directory=str(proj.expanded_path))
    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to read session: {e}")
        raise typer.Exit(code=1)

    if info is None:
        console.print(f"[red]Error:[/red] Session not found: {session_id}")
        raise typer.Exit(code=1)

    lines = [
        f"[bold]Session:[/bold] {info.session_id}",
        f"[bold]Project:[/bold] {proj.name} ({proj.expanded_path})",
        f"[bold]Git Branch:[/bold] {info.git_branch or '-'}",
        f"[bold]CWD:[/bold] {info.cwd or '-'}",
        f"[bold]Size:[/bold] {_format_bytes_compact(info.file_size)}",
        f"[bold]Tag:[/bold] {getattr(info, 'tag', None) or '-'}",
        f"[bold]Created:[/bold] {_format_ms_to_human(getattr(info, 'created_at', None))}",
        f"[bold]Last modified:[/bold] {_format_ms_to_human(info.last_modified)}",
        "",
        f"[bold]Custom title:[/bold] {info.custom_title or '-'}",
        f"[bold]Summary:[/bold] {info.summary or '-'}",
        "",
        "[bold]First prompt:[/bold]",
        (info.first_prompt or "-"),
    ]
    console.print(Panel("\n".join(lines), title=f"Claude Session {info.session_id[:8]}"))


@claude_sessions_app.command("messages")
def claude_sessions_messages(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    session_id: Annotated[str, typer.Argument(help="Session UUID")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max messages")] = 20,
    offset: Annotated[int, typer.Option("--offset", help="Messages to skip")] = 0,
) -> None:
    """Print conversation messages for a Claude Code session."""
    proj = _resolve_project_or_exit(project)

    try:
        from claude_agent_sdk import get_session_messages
    except ImportError:
        console.print("[red]Error:[/red] claude_agent_sdk is not installed.")
        raise typer.Exit(code=2)

    try:
        messages = get_session_messages(
            session_id=session_id,
            directory=str(proj.expanded_path),
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to read messages: {e}")
        raise typer.Exit(code=1)

    if not messages:
        console.print(f"[dim]No messages for session {session_id[:8]}.[/dim]")
        return

    for i, m in enumerate(messages, start=offset + 1):
        role = m.type.upper()
        style = "blue" if m.type == "user" else "green"
        body = _flatten_claude_cli_message(m.message)
        if len(body) > 500:
            body = body[:500] + "…"
        console.print(f"[{style}][bold]{i}. {role}[/bold][/{style}]")
        console.print(body or "[dim](empty)[/dim]")
        console.print()


# ========== User Commands (D5 Phase 1 — multi-user auth) ==========
#
# These populate the `users` table that's present in every DB (the migration
# runs unconditionally) but is only consulted when GLUON_AUTH_ENABLED=true.
# You can create accounts ahead of the flag flip so the team is ready to go.


@user_app.command("add")
def user_add(
    username: Annotated[str, typer.Argument(help="Unique username (URL-safe)")],
    display_name: Annotated[
        str | None,
        typer.Option("--display-name", help="Human-readable name; defaults to username"),
    ] = None,
    email: Annotated[str | None, typer.Option("--email", help="Optional email")] = None,
    role: Annotated[
        str,
        typer.Option("--role", help="admin / operator / viewer"),
    ] = "operator",
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="Password (min 12 chars). Prompts if not given. Ignored for OIDC users.",
            hide_input=True,
            prompt=False,
        ),
    ] = None,
    auth_provider: Annotated[
        str,
        typer.Option(
            "--auth-provider",
            help="Auth backend for this user: local (password) or oidc (D5 Phase 3).",
        ),
    ] = "local",
    auth_subject: Annotated[
        str | None,
        typer.Option(
            "--auth-subject",
            help=(
                "OIDC `sub` claim. If unknown, omit and we'll use --email as a "
                "placeholder; the real sub gets pinned on the user's first OIDC login."
            ),
        ),
    ] = None,
) -> None:
    """Create a new user.

    Examples:

        # Local password user (default)
        gluon user add alice --role admin --email alice@org.example

        # Pre-register an OIDC user — admin doesn't need to know the
        # `sub` yet; the real sub is pinned on first login by email match.
        gluon user add alice --auth-provider oidc --email alice@org.example --role admin

        # Pre-register an OIDC user when you DO know the sub (e.g. from
        # the IdP's directory export):
        gluon user add alice --auth-provider oidc --auth-subject 'auth0|abc123' --email alice@org.example
    """
    import getpass

    from gluon.auth import LocalAuthProvider
    from gluon.models import AuthProvider, UserRole

    try:
        parsed_role = UserRole(role.lower())
    except ValueError:
        valid = ", ".join(r.value for r in UserRole)
        console.print(f"[red]Unknown role:[/red] {role}. Valid: {valid}")
        raise typer.Exit(1)

    try:
        parsed_provider = AuthProvider(auth_provider.lower())
    except ValueError:
        valid = ", ".join(p.value for p in AuthProvider if p.value != "system")
        console.print(f"[red]Unknown auth provider:[/red] {auth_provider}. Valid: {valid}")
        raise typer.Exit(1)

    store = GluonStore()

    if parsed_provider == AuthProvider.LOCAL:
        # Local: password required, hashed via argon2id.
        if password is None:
            password = getpass.getpass("Password (min 12 chars): ")
            confirm = getpass.getpass("Password again: ")
            if password != confirm:
                console.print("[red]Passwords do not match.[/red]")
                raise typer.Exit(1)
        provider = LocalAuthProvider(store)
        try:
            user = provider.create_user(
                username=username,
                password=password,
                display_name=display_name,
                email=email,
                role=parsed_role,
            )
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
        except Exception as e:
            if "UNIQUE" in str(e):
                console.print(f"[red]User '{username}' already exists.[/red]")
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    elif parsed_provider == AuthProvider.OIDC:
        # OIDC: no password, but we need an auth_subject. Use email as the
        # placeholder if --auth-subject isn't given; OIDCAuthProvider swaps
        # it for the real `sub` on first login.
        if auth_subject is None:
            if not email:
                console.print("[red]OIDC users need either --auth-subject or --email to bind by on first login.[/red]")
                raise typer.Exit(1)
            auth_subject = email
        try:
            user = store.create_user(
                username=username,
                display_name=display_name or username,
                email=email,
                auth_subject=auth_subject,
                auth_provider=parsed_provider.value,
                role=parsed_role,
            )
        except Exception as e:
            if "UNIQUE" in str(e):
                console.print(f"[red]A user with this username or (oidc, {auth_subject!r}) already exists.[/red]")
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    else:
        console.print(f"[red]auth-provider {auth_provider} not supported by `user add`.[/red]")
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[bold]Username:[/bold] {user.username}\n"
            f"[bold]Display name:[/bold] {user.display_name}\n"
            f"[bold]Email:[/bold] {user.email or '—'}\n"
            f"[bold]Role:[/bold] {user.role.value}\n"
            f"[bold]Provider:[/bold] {user.auth_provider.value}\n"
            f"[bold]ID:[/bold] [dim]{user.id}[/dim]",
            title="Created user",
        )
    )
    console.print(
        "[dim]Auth enforcement requires GLUON_AUTH_ENABLED=true (currently "
        f"{'on' if os.environ.get('GLUON_AUTH_ENABLED', '').lower() in ('1', 'true', 'yes', 'on') else 'off'})."
        "[/dim]"
    )


@user_app.command("list")
def user_list(
    include_disabled: Annotated[
        bool,
        typer.Option("--all", help="Include disabled users"),
    ] = False,
) -> None:
    """List Gluon users."""
    store = GluonStore()
    users = store.list_users(include_disabled=include_disabled)

    if not users:
        console.print("[dim]No users yet. Create one with `gluon user add <username>`.[/dim]")
        return

    table = Table(title=f"Users ({len(users)})")
    table.add_column("Username")
    table.add_column("Display")
    table.add_column("Role")
    table.add_column("Provider")
    table.add_column("Disabled")
    table.add_column("Last login")
    for u in users:
        table.add_row(
            u.username,
            u.display_name,
            u.role.value,
            u.auth_provider.value,
            "yes" if u.disabled else "",
            u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "—",
        )
    console.print(table)


@user_app.command("show")
def user_show(
    username: Annotated[str, typer.Argument(help="Username to show")],
) -> None:
    """Show detailed info about a user."""
    store = GluonStore()
    user = store.get_user_by_username(username)
    if user is None:
        console.print(f"[red]User '{username}' not found.[/red]")
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[bold]ID:[/bold] [dim]{user.id}[/dim]\n"
            f"[bold]Username:[/bold] {user.username}\n"
            f"[bold]Display name:[/bold] {user.display_name}\n"
            f"[bold]Email:[/bold] {user.email or '—'}\n"
            f"[bold]Role:[/bold] {user.role.value}\n"
            f"[bold]Provider:[/bold] {user.auth_provider.value}\n"
            f"[bold]Disabled:[/bold] {'yes' if user.disabled else 'no'}\n"
            f"[bold]Telegram link:[/bold] {user.telegram_user_id or '—'}\n"
            f"[bold]Discord link:[/bold] {user.discord_user_id or '—'}\n"
            f"[bold]Created:[/bold] {user.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"[bold]Last login:[/bold] "
            + (user.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if user.last_login_at else "—"),
            title=f"User: {user.username}",
        )
    )


@user_app.command("disable")
def user_disable(
    username: Annotated[str, typer.Argument(help="Username to disable")],
) -> None:
    """Disable a user (prevents login) and invalidate their active sessions."""
    store = GluonStore()
    user = store.get_user_by_username(username)
    if user is None:
        console.print(f"[red]User '{username}' not found.[/red]")
        raise typer.Exit(1)
    if user.disabled:
        console.print(f"[yellow]User '{username}' is already disabled.[/yellow]")
        return
    user.disabled = True
    store.update_user(user)
    rotated = store.delete_user_sessions_for_user(user.id)
    console.print(f"[yellow]Disabled[/yellow] {user.username}. {rotated} active session(s) rotated.")


@user_app.command("enable")
def user_enable(
    username: Annotated[str, typer.Argument(help="Username to re-enable")],
) -> None:
    """Re-enable a previously disabled user."""
    store = GluonStore()
    user = store.get_user_by_username(username)
    if user is None:
        console.print(f"[red]User '{username}' not found.[/red]")
        raise typer.Exit(1)
    if not user.disabled:
        console.print(f"[yellow]User '{username}' is not disabled.[/yellow]")
        return
    user.disabled = False
    store.update_user(user)
    console.print(f"[green]Enabled[/green] {user.username}.")


@user_app.command("set-role")
def user_set_role(
    username: Annotated[str, typer.Argument(help="Username")],
    role: Annotated[str, typer.Argument(help="admin / operator / viewer")],
) -> None:
    """Change a user's role. Invalidates their active sessions."""
    from gluon.models import UserRole

    try:
        parsed_role = UserRole(role.lower())
    except ValueError:
        valid = ", ".join(r.value for r in UserRole)
        console.print(f"[red]Unknown role:[/red] {role}. Valid: {valid}")
        raise typer.Exit(1)

    store = GluonStore()
    user = store.get_user_by_username(username)
    if user is None:
        console.print(f"[red]User '{username}' not found.[/red]")
        raise typer.Exit(1)
    old_role = user.role
    if old_role == parsed_role:
        console.print(f"[dim]User '{username}' already has role {parsed_role.value}.[/dim]")
        return
    user.role = parsed_role
    store.update_user(user)
    rotated = store.delete_user_sessions_for_user(user.id)
    console.print(
        f"[green]Role updated:[/green] {user.username}: "
        f"{old_role.value} → {parsed_role.value}. "
        f"{rotated} active session(s) rotated."
    )


@user_app.command("set-password")
def user_set_password(
    username: Annotated[str, typer.Argument(help="Username")],
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="New password (prompts if not given)",
            hide_input=True,
        ),
    ] = None,
) -> None:
    """Change a user's password. Invalidates their active sessions."""
    import getpass

    from gluon.auth import get_auth_provider

    store = GluonStore()
    user = store.get_user_by_username(username)
    if user is None:
        console.print(f"[red]User '{username}' not found.[/red]")
        raise typer.Exit(1)

    provider = get_auth_provider(store)
    if not hasattr(provider, "set_password"):
        console.print(f"[red]Provider {provider.name} does not support password change.[/red]")
        raise typer.Exit(1)

    if password is None:
        password = getpass.getpass("New password (min 12 chars): ")
        confirm = getpass.getpass("Password again: ")
        if password != confirm:
            console.print("[red]Passwords do not match.[/red]")
            raise typer.Exit(1)

    try:
        provider.set_password(user, password)  # type: ignore[attr-defined]
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Password updated[/green] for {user.username}. All active sessions rotated.")


# ========== Agent Loop Commands (loop-engineering Phase 2) ==========
# docs/design/agent-loops.md — CLI writes to the store; the running server's
# queue-drain loop dispatches iterations (within GLUON_QUEUE_DRAIN_INTERVAL_SECS).


def _get_loop_or_exit(store: GluonStore, loop_id: str) -> Any:
    loops = store.list_agent_loops(limit=500)
    loop = next((lp for lp in loops if lp.id == loop_id or lp.id.startswith(loop_id)), None)
    if loop is None:
        console.print(f"[red]Error:[/red] Agent loop not found: {loop_id}")
        raise typer.Exit(1)
    return loop


@loop_app.command("create")
def loop_create(
    project: Annotated[str, typer.Argument(help="Project name")],
    objective: Annotated[str, typer.Argument(help="The durable objective the loop works toward")],
    verify_cmd: Annotated[
        str | None,
        typer.Option("--verify-cmd", help="Objective gate: completion granted only when this command exits 0"),
    ] = None,
    agent_verifier: Annotated[
        bool,
        typer.Option("--agent-verifier", help="Judge completion claims with an independent verifier iteration (I2)"),
    ] = False,
    profile: Annotated[str, typer.Option("--profile", "-P", help="Task profile for iterations")] = "standard",
    model: Annotated[str | None, typer.Option("--model", "-m", help="Judgment model (surveyor/verifier/fixes)")] = None,
    executor_model: Annotated[
        str | None,
        typer.Option("--executor-model", help="Cheaper model for mechanical fan-out tasks (default: --model)"),
    ] = None,
    watch_cmd: Annotated[
        str | None,
        typer.Option(
            "--watch-cmd",
            help="Event-reactive loop: when idle, re-seed from this command's output if it exits 0",
        ),
    ] = None,
    worktree: Annotated[bool, typer.Option("--worktree", "-w", help="Run iterations in isolated worktrees")] = False,
    autonomy: Annotated[
        str,
        typer.Option(
            "--autonomy",
            "-a",
            help="Autonomy ladder: L1 report-only / L2 assisted (pause at plan for approval) / L3 unattended",
        ),
    ] = "L3",
    max_iterations: Annotated[int, typer.Option("--max-iterations", help="Hard iteration ceiling")] = 20,
    max_cost: Annotated[float | None, typer.Option("--max-cost", help="Loop-level spend cap in USD")] = None,
    max_stalls: Annotated[int, typer.Option("--max-stalls", help="Consecutive stalls before pause")] = 2,
    max_fanout: Annotated[int, typer.Option("--max-fanout", help="Max pending tasks the loop may hold")] = 10,
):
    """Create an agent loop: seed iteration 1; the agent authors what comes next."""
    from gluon.loop_manager import LoopManager

    orchestrator = get_orchestrator()
    try:
        proj = orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None

    loop = LoopManager(orchestrator.store).create_loop(
        project_id=proj.id,
        objective=objective,
        verify_cmd=verify_cmd,
        agent_verifier=agent_verifier,
        profile=profile,
        model=model,
        executor_model=executor_model,
        watch_cmd=watch_cmd,
        use_worktree=worktree,
        autonomy=autonomy,
        max_iterations=max_iterations,
        max_cost_usd=max_cost,
        max_stalls=max_stalls,
        max_fanout=max_fanout,
        initiator="cli",
    )
    readiness = "[green]gated[/green]" if verify_cmd else "[yellow]gateless[/yellow]"
    console.print(f"[green]Agent loop created:[/green] {loop.id} ({readiness})")
    console.print(f"[bold]Objective:[/bold] {objective}")
    if verify_cmd:
        console.print(f"[bold]Gate:[/bold] {verify_cmd}")
    console.print(
        f"[bold]Budget:[/bold] {max_iterations} iterations"
        + (f", ${max_cost:.2f} cap" if max_cost else ", no cost cap")
    )
    if loop.executor_model:
        console.print(
            f"[bold]Model routing:[/bold] judgment={model or 'profile default'}, executor={loop.executor_model}"
        )
    if loop.watch_cmd:
        console.print(
            f"[bold]Watch:[/bold] event-reactive — when idle, re-seeds from `{loop.watch_cmd}` (exit 0 = work). "
            "Pair with `gluon schedule` to re-arm it periodically."
        )
    if loop.autonomy in ("L1", "L2"):
        console.print(
            f"[bold]Autonomy:[/bold] {loop.autonomy} — the loop will PAUSE after the surveyor "
            "authors the plan; inspect the graph, then `gluon loop resume` to execute."
        )
    console.print("Iteration 1 seeded — the server's queue drain will dispatch it.")


@loop_app.command("list")
def loop_list(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Filter by project name")] = None,
    status: Annotated[str | None, typer.Option("--status", "-s", help="Filter by status")] = None,
):
    """List agent loops."""
    store = GluonStore()
    project_id: str | None = None
    if project:
        try:
            project_id = get_orchestrator().get_project(project).id
        except ProjectNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from None

    loops = store.list_agent_loops(project_id=project_id, status=status)
    if not loops:
        console.print("[dim]No agent loops found[/dim]")
        return

    table = Table(title="Agent Loops")
    table.add_column("ID", style="cyan")
    table.add_column("Project")
    table.add_column("Status")
    table.add_column("Iter", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Gate")
    table.add_column("Objective")
    status_colors = {"running": "green", "paused": "yellow", "completed": "blue", "failed": "red", "cancelled": "dim"}
    for lp in loops:
        proj = store.get_project(lp.project_id)
        color = status_colors.get(lp.status.value, "white")
        table.add_row(
            lp.id[:8],
            proj.name if proj else lp.project_id[:8],
            f"[{color}]{lp.status.value}[/{color}]",
            f"{lp.iteration_count}/{lp.max_iterations}",
            f"${lp.total_cost_usd:.2f}",
            "gated" if lp.verify_cmd else "gateless",
            lp.objective[:60] + ("…" if len(lp.objective) > 60 else ""),
        )
    console.print(table)


@loop_app.command("show")
def loop_show(loop_id: Annotated[str, typer.Argument(help="Loop ID (short prefix ok)")]):
    """Show one agent loop: state, budgets, pending tasks, recent iterations."""
    store = GluonStore()
    loop = _get_loop_or_exit(store, loop_id)

    proj = store.get_project(loop.project_id)
    console.print(f"[bold]Agent Loop:[/bold] {loop.id} [{loop.status.value}]")
    console.print(f"[bold]Project:[/bold] {proj.name if proj else loop.project_id}")
    console.print(f"[bold]Objective:[/bold] {loop.objective}")
    console.print(f"[bold]Gate:[/bold] {loop.verify_cmd or '(gateless)'}")
    console.print(
        f"[bold]Budget:[/bold] iteration {loop.iteration_count}/{loop.max_iterations}, "
        f"${loop.total_cost_usd:.2f}" + (f" of ${loop.max_cost_usd:.2f}" if loop.max_cost_usd else " (no cap)")
    )
    console.print(f"[bold]Stalls:[/bold] {loop.stall_count}/{loop.max_stalls}")
    if loop.status_reason:
        console.print(f"[bold]Status reason:[/bold] {loop.status_reason}")
    if loop.completion_summary:
        console.print(f"[bold]Completion summary:[/bold] {loop.completion_summary}")

    pending = [
        i
        for i in store.list_work_items(project_id=loop.project_id, status="pending", limit=100)
        if i.loop_id == loop.id
    ]
    console.print(f"\n[bold]Pending tasks ({len(pending)}):[/bold]")
    for item in pending:
        console.print(f"  [{item.id[:8]}] ({item.source}) {item.prompt[:100]}")

    runs = store.list_runs_for_loop(loop.id, limit=10)
    console.print(f"\n[bold]Iteration runs ({len(runs)} most recent):[/bold]")
    for r in runs:
        emoji, color = format_run_status(r.status)
        cost = f"${r.cost_usd:.2f}" if r.cost_usd else "$0.00"
        console.print(f"  [{r.id[:8]}] [{color}]{emoji} {r.status.value}[/{color}] {cost} — {r.prompt[:80]}")


@loop_app.command("pause")
def loop_pause(loop_id: Annotated[str, typer.Argument(help="Loop ID (short prefix ok)")]):
    """Pause a running loop (pending tasks preserved, inert until resume)."""
    from gluon.loop_manager import LoopManager

    store = GluonStore()
    loop = _get_loop_or_exit(store, loop_id)
    updated = LoopManager(store).pause_loop(loop.id)
    if updated and updated.status.value == "paused":
        console.print(f"[yellow]Paused[/yellow] agent loop {updated.id[:8]}")
    else:
        console.print(f"[red]Error:[/red] loop is {loop.status.value}, cannot pause")
        raise typer.Exit(1)


@loop_app.command("resume")
def loop_resume(loop_id: Annotated[str, typer.Argument(help="Loop ID (short prefix ok)")]):
    """Resume a paused loop (re-seeds a continuation if nothing is pending)."""
    from gluon.loop_manager import LoopManager

    store = GluonStore()
    loop = _get_loop_or_exit(store, loop_id)
    updated = LoopManager(store).resume_loop(loop.id)
    if updated and updated.status.value == "running":
        console.print(f"[green]Resumed[/green] agent loop {updated.id[:8]}")
    else:
        console.print(f"[red]Error:[/red] loop is {loop.status.value}, cannot resume")
        raise typer.Exit(1)


@loop_app.command("cancel")
def loop_cancel(loop_id: Annotated[str, typer.Argument(help="Loop ID (short prefix ok)")]):
    """Cancel a loop and drop its pending tasks."""
    from gluon.loop_manager import LoopManager

    store = GluonStore()
    loop = _get_loop_or_exit(store, loop_id)
    updated = LoopManager(store).cancel_loop(loop.id)
    if updated and updated.status.value == "cancelled":
        console.print(f"[red]Cancelled[/red] agent loop {updated.id[:8]}")
    else:
        console.print(f"[red]Error:[/red] loop is {loop.status.value}, cannot cancel")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
