"""CLI interface for Gluon Agent."""

from pathlib import Path
from typing import Annotated

import anyio
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gluon import __version__
from gluon.agent import AgentMessage, AgentResult
from gluon.core import (
    Orchestrator,
    ProjectExistsError,
    ProjectNotFoundError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from gluon.git_manager import GitManager
from gluon.models import RunStatus
from gluon.models_config import ModelTier, describe_models
from gluon.runner import TaskRunner, format_duration, format_run_status
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

console = Console()


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
        console.print("[red]Error:[/red] Failed to remove project")
        raise typer.Exit(1)


# ========== Workspace Commands ==========


@workspace_app.command("add")
def workspace_add(
    name: Annotated[str, typer.Argument(help="Unique name for the workspace")],
    path: Annotated[Path, typer.Argument(help="Path to workspace directory")],
    no_scan: Annotated[bool, typer.Option("--no-scan", help="Don't auto-scan for projects")] = False,
):
    """Register a new workspace and scan for projects."""
    orchestrator = get_orchestrator()

    try:
        workspace, projects = orchestrator.register_workspace(name, path, auto_scan=not no_scan)
        console.print(f"[green]✓[/green] Workspace '{workspace.name}' registered")
        console.print(f"  Path: {workspace.path}")
        console.print(f"  ID: {workspace.id}")

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
    table.add_column("Auto-discover")
    table.add_column("ID", style="dim")

    for ws in workspaces:
        projects = orchestrator.list_workspace_projects(ws.name)
        table.add_row(
            ws.name,
            str(ws.path),
            str(len(projects)),
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


# ========== Execution Commands ==========


@app.command("run")
def run(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    prompt: Annotated[str, typer.Argument(help="Prompt for Claude")],
    new_session: Annotated[bool, typer.Option("--new", "-n", help="Force new session")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only show final result")] = False,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model tier: opus/sonnet/haiku")] = None,
    background: Annotated[bool, typer.Option("--background", "-b", help="Run in background")] = False,
    worktree: Annotated[bool, typer.Option("--worktree", "-w", help="Execute in isolated Git worktree")] = False,
):
    """Execute a task on a project."""
    orchestrator = get_orchestrator()

    try:
        proj = orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Validate model if provided
    model_tier: ModelTier | None = None
    if model:
        try:
            model_tier = ModelTier(model.lower())
        except ValueError:
            console.print(f"[red]Error:[/red] Invalid model: {model}")
            console.print(describe_models())
            raise typer.Exit(1)

    # Background execution mode
    if background:
        runner = TaskRunner()

        async def _submit():
            run_obj = await runner.submit(proj.id, prompt, wait=False)
            console.print(f"[green]✓[/green] Task submitted: [cyan]{run_obj.id[:8]}[/cyan]")
            console.print(f"  Project: {project}")
            console.print(f"  Prompt: {prompt[:60]}{'...' if len(prompt) > 60 else ''}")
            console.print()
            console.print("[dim]Use 'gluon runs' to check status[/dim]")
            console.print(f"[dim]Use 'gluon logs {run_obj.id[:8]}' to view logs[/dim]")

        anyio.run(_submit)
        return

    # Foreground execution (existing behavior)
    async def _run():
        result: AgentResult | None = None

        console.print(f"[bold]Running on project:[/bold] {project}")
        console.print(f"[bold]Prompt:[/bold] {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        if model_tier:
            console.print(f"[bold]Model:[/bold] {model_tier.value}")
        if worktree:
            console.print("[bold]Worktree:[/bold] enabled (isolated execution)")
        console.print()

        async for item in orchestrator.execute(
            project,
            prompt,
            force_new_session=new_session,
            model=model_tier,
            use_worktree=worktree,
        ):
            if isinstance(item, AgentMessage):
                if not quiet:
                    _print_message(item)
            elif isinstance(item, AgentResult):
                result = item

        if result:
            console.print()
            _print_result(result)

    anyio.run(_run)


@app.command("resume")
def resume(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    prompt: Annotated[str | None, typer.Argument(help="Optional follow-up prompt")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only show final result")] = False,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model tier: opus/sonnet/haiku")] = None,
):
    """Resume the last session for a project."""
    orchestrator = get_orchestrator()

    try:
        orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Validate model if provided
    model_tier: ModelTier | None = None
    if model:
        try:
            model_tier = ModelTier(model.lower())
        except ValueError:
            console.print(f"[red]Error:[/red] Invalid model: {model}")
            console.print(describe_models())
            raise typer.Exit(1)

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


@app.command("status")
def status():
    """Show overall status."""
    orchestrator = get_orchestrator()
    status_info = orchestrator.status()

    console.print(
        Panel.fit(
            f"[bold]Projects:[/bold] {status_info['total_projects']}\n"
            f"[bold]Active Sessions:[/bold] {status_info['active_sessions']}",
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
    from gluon.bot import run_bot

    allowed_users: list[int] | None = None
    if users:
        allowed_users = [int(u.strip()) for u in users.split(",") if u.strip()]

    try:
        console.print("[bold]Starting Gluon Telegram Bot...[/bold]")
        console.print("[dim]Press Ctrl+C to stop[/dim]")
        run_bot(token=token, allowed_users=allowed_users)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
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
        from gluon.transport.discord import DiscordTransport, run_discord_transport

        _ = DiscordTransport  # Verify import succeeded
    except ImportError:
        console.print("[red]Error:[/red] Discord support not installed.")
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
        anyio.run(run_discord_transport, bot_token, guild_id, bot_core, allowed_users)
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
    - Web: No configuration needed (runs on --web-port, default 45866)
    """
    import os

    if not telegram and not discord and not web:
        console.print("[red]Error:[/red] At least one transport must be enabled.")
        console.print("Use --telegram, --discord, and/or --web flags.")
        raise typer.Exit(1)

    from gluon.bot_core import GluonBotCore

    # Create shared bot core
    bot_core = GluonBotCore()
    transports_to_run: list[tuple[str, any]] = []

    # Configure Telegram
    if telegram:
        telegram_token = os.environ.get("GLUON_TELEGRAM_TOKEN")
        if not telegram_token:
            console.print("[red]Error:[/red] GLUON_TELEGRAM_TOKEN required for Telegram.")
            raise typer.Exit(1)

        telegram_users_str = os.environ.get("GLUON_TELEGRAM_USERS", "")
        telegram_users = (
            [int(u.strip()) for u in telegram_users_str.split(",") if u.strip()] if telegram_users_str else None
        )

        from gluon.transport.telegram import TelegramTransport

        tg_transport = TelegramTransport(telegram_token, bot_core, telegram_users)
        transports_to_run.append(("Telegram", tg_transport))
        console.print("[green]✓[/green] Telegram transport configured")

    # Configure Discord
    if discord:
        try:
            from gluon.transport.discord import DiscordTransport

            _ = DiscordTransport  # Verify import succeeded
        except ImportError:
            console.print("[red]Error:[/red] Discord support not installed.")
            console.print("Install with: [cyan]pip install 'gluon-agent[discord]'[/cyan]")
            raise typer.Exit(1)

        discord_token = os.environ.get("GLUON_DISCORD_TOKEN")
        if not discord_token:
            console.print("[red]Error:[/red] GLUON_DISCORD_TOKEN required for Discord.")
            raise typer.Exit(1)

        discord_guild = int(os.environ.get("GLUON_DISCORD_GUILD", "0"))
        if not discord_guild:
            console.print("[red]Error:[/red] GLUON_DISCORD_GUILD required for Discord.")
            raise typer.Exit(1)

        discord_users_str = os.environ.get("GLUON_DISCORD_USERS", "")
        discord_users = (
            [int(u.strip()) for u in discord_users_str.split(",") if u.strip()] if discord_users_str else None
        )

        from gluon.transport.discord import DiscordTransport

        dc_transport = DiscordTransport(discord_token, discord_guild, bot_core, discord_users)
        transports_to_run.append(("Discord", dc_transport))
        console.print("[green]✓[/green] Discord transport configured")

    # Configure Web dashboard
    web_server = None
    if web:
        try:
            import uvicorn

            from gluon.web import create_app

            web_app = create_app()
            web_server = uvicorn.Server(
                uvicorn.Config(web_app, host="0.0.0.0", port=web_port, log_level="warning")
            )
            console.print(f"[green]✓[/green] Web dashboard configured (port {web_port})")
        except ImportError:
            console.print("[red]Error:[/red] Web dashboard dependencies not installed.")
            console.print("Install with: [cyan]pip install 'gluon-agent[web]'[/cyan]")
            raise typer.Exit(1)

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
    table.add_column("Project")
    table.add_column("Prompt")
    table.add_column("Duration")
    table.add_column("Created", style="dim")

    for run in runs_list:
        emoji, color = format_run_status(run.status)
        duration = format_duration(run.duration_seconds)
        proj_name = project_lookup.get(run.project_id, run.project_id[:8])

        table.add_row(
            run.id[:8],
            f"[{color}]{emoji} {run.status.value}[/{color}]",
            proj_name,
            (run.prompt[:30] + "...") if len(run.prompt) > 30 else run.prompt,
            duration,
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
    except ImportError:
        console.print("[red]Error:[/red] Web dashboard dependencies not installed.")
        console.print("Install with: [cyan]pip install 'gluon-agent[web]'[/cyan]")
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


# ========== Utility Commands ==========


@app.command("version")
def version():
    """Show version."""
    console.print(f"Gluon Agent v{__version__}")


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


if __name__ == "__main__":
    app()
