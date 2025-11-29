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


# ========== Execution Commands ==========


@app.command("run")
def run(
    project: Annotated[str, typer.Argument(help="Project name or ID")],
    prompt: Annotated[str, typer.Argument(help="Prompt for Claude")],
    new_session: Annotated[bool, typer.Option("--new", "-n", help="Force new session")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only show final result")] = False,
):
    """Execute a task on a project."""
    orchestrator = get_orchestrator()

    try:
        orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    async def _run():
        result: AgentResult | None = None

        console.print(f"[bold]Running on project:[/bold] {project}")
        console.print(f"[bold]Prompt:[/bold] {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        console.print()

        async for item in orchestrator.execute(project, prompt, force_new_session=new_session):
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
):
    """Resume the last session for a project."""
    orchestrator = get_orchestrator()

    try:
        orchestrator.get_project(project)
    except ProjectNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    async def _resume():
        result: AgentResult | None = None

        console.print(f"[bold]Resuming session for:[/bold] {project}")
        if prompt:
            console.print(f"[bold]Prompt:[/bold] {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        console.print()

        try:
            async for item in orchestrator.resume(project, prompt):
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
