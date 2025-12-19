"""Chat agent for natural language interaction with Gluon."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

from gluon.agent import find_claude_cli
from gluon.core import (
    Orchestrator,
    ProjectExistsError,
    ProjectNotFoundError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from gluon.models_config import ModelTier, get_model_id
from gluon.runner import TaskRunner, format_duration

SYSTEM_PROMPT = """You are Gluon, an AI orchestrator that manages multiple Claude Code agents \
across different software projects.

You help users:
- List and manage their registered projects and workspaces
- Add workspaces to auto-discover projects in a directory
- Run coding tasks on projects using Claude Code agents
- Resume previous sessions to continue work
- Check status of sessions, runs, and costs
- Monitor ALL task executions (CLI, Web Dashboard, Telegram, Discord - all in one view)
- Check git status of projects
- Read files, search code, and run commands in project directories
- Search the web and fetch information from URLs

**Unified Task Visibility:**
All tasks are tracked in a single database regardless of where they were started.
When you use list_runs, you see ALL runs from CLI, Web Dashboard, and other bot interfaces.

**Model Selection Guidelines:**
When running tasks, choose the appropriate model based on task complexity:
- **opus**: Complex tasks requiring deep reasoning, architecture decisions, or large refactors
- **sonnet**: Default for most tasks - balanced performance and cost
- **haiku**: Simple tasks like bug fixes, documentation, or straightforward implementations

**Worktree Isolation:**
Use `use_worktree=true` in run_task when you need isolated execution (e.g., experimental changes,
parallel tasks on the same project). This creates a temporary Git worktree for the task.

When users ask you to do something, use the available tools to help them. Be concise in your responses.

**Gluon Tools:**
- list_projects, list_sessions, get_status - View projects and sessions
- run_task, resume_session - Execute coding tasks
- add_workspace, list_workspaces, scan_workspace - Manage workspaces
- add_project, remove_project - Register/unregister projects
- remove_workspace, list_workspace_projects - Workspace management
- list_runs, get_run, get_logs, cancel_run - Monitor runs and view logs
- archive_run - Archive completed runs (hides from default list)
- get_usage, get_usage_by_project - Cost and token usage analytics
- create_pr - Create a pull request for a worktree run
- get_git_status - Check git status for a project
- git_sync - Auto-commit, fetch, and fast-forward a project
- git_push - Commit and push changes to remote
- git_fetch - Fetch from remote to see what's new
- list_branches, delete_branch - Branch management
- get_run_commits - Get commits made on a run's branch
- get_run_files - Get files changed on a run's branch
- get_file_diff - Get the diff for a specific file
- merge_branch - Merge a run's branch into main
- check_conflicts - Check for merge conflicts in a project
- get_conflict_diff, resolve_conflict - Conflict resolution (3-way diff, ours/theirs)
- rebase_branch, rebase_continue, rebase_abort - Rebase operations
- upload_image, list_run_images - Attach images to runs
- get_setting, set_setting - Configuration management

**Built-in Tools:**
- Read, Glob, Grep - Read files and search code
- Bash, BashOutput - Run shell commands
- WebSearch, WebFetch - Search web and fetch URLs

Always confirm what action you're taking before executing it."""


@dataclass
class ChatMessage:
    """A message in the conversation history."""

    role: str  # "user" or "assistant"
    text: str


@dataclass
class ChatResponse:
    """Response from the chat agent."""

    text: str
    action_taken: str | None = None
    action_result: dict[str, Any] | None = None


class GluonChatAgent:
    """
    Chat agent that interprets natural language and performs Gluon actions.

    Uses Claude with custom tools to understand user intent and execute
    appropriate Gluon operations.
    """

    def __init__(self, orchestrator: Orchestrator | None = None):
        self.orchestrator = orchestrator or Orchestrator()
        self._pending_task: dict[str, Any] | None = None

    def _create_tools(self):
        """Create MCP tools for Gluon operations."""
        orchestrator = self.orchestrator

        @tool("list_projects", "List all registered projects", {})
        async def list_projects(args: dict[str, Any]) -> dict[str, Any]:
            projects = orchestrator.list_projects()
            if not projects:
                return {"content": [{"type": "text", "text": "No projects registered."}]}

            result = "**Projects:**\n"
            for p in projects:
                sessions = orchestrator.list_sessions(p.name)
                result += f"- `{p.name}`: {p.path} ({len(sessions)} sessions)\n"

            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "list_sessions",
            "List sessions for a project or all sessions",
            {
                "project_name": str,  # Optional project name
            },
        )
        async def list_sessions(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name")

            try:
                sessions = orchestrator.list_sessions(project_name if project_name else None)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            if not sessions:
                return {"content": [{"type": "text", "text": "No sessions found."}]}

            # Build project lookup
            project_lookup: dict[str, str] = {}
            if not project_name:
                for p in orchestrator.list_projects():
                    project_lookup[p.id] = p.name

            result = f"**Sessions{f' for {project_name}' if project_name else ''}:**\n"
            status_emojis = {"active": "🟢", "paused": "🟡", "completed": "🔵", "failed": "🔴"}
            for s in sessions[:10]:
                status_emoji = status_emojis.get(s.status.value, "⚪")
                proj = project_lookup.get(s.project_id, "") if not project_name else ""
                result += f"{status_emoji} "
                if proj:
                    result += f"`{proj}` "
                result += f"${s.total_cost_usd:.4f} | {s.total_turns} turns\n"

            return {"content": [{"type": "text", "text": result}]}

        @tool("get_status", "Get overall Gluon status", {})
        async def get_status(args: dict[str, Any]) -> dict[str, Any]:
            status = orchestrator.status()
            result = (
                f"**Gluon Status**\n"
                f"Projects: {status['total_projects']}\n"
                f"Active Sessions: {status['active_sessions']}\n"
            )
            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "run_task",
            "Run a coding task on a project using Claude Code",
            {
                "project_name": str,  # Name of the project
                "prompt": str,  # The task to perform
                "model": str,  # Optional: Model tier (opus/sonnet/haiku). Default: sonnet
                "use_worktree": bool,  # Optional: Execute in isolated Git worktree. Default: false
            },
        )
        async def run_task(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")
            prompt = args.get("prompt", "")
            model = args.get("model", "sonnet")
            use_worktree = args.get("use_worktree", False)

            if not project_name or not prompt:
                return {"content": [{"type": "text", "text": "Error: project_name and prompt are required"}]}

            try:
                orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            # Validate model
            try:
                ModelTier(model.lower())
            except ValueError:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error: Invalid model '{model}'. Use opus/sonnet/haiku. Defaulting to sonnet.",
                        }
                    ]
                }
                model = "sonnet"

            # Store the pending task - actual execution happens in the bot
            self._pending_task = {
                "action": "run_task",
                "project_name": project_name,
                "prompt": prompt,
                "model": model,
                "use_worktree": use_worktree,
            }

            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Starting task on `{project_name}` with {model}: {prompt[:100]}...",
                    }
                ]
            }

        @tool(
            "resume_session",
            "Resume the last session for a project",
            {
                "project_name": str,  # Name of the project
                "prompt": str,  # Optional follow-up prompt
                "model": str,  # Optional: Model tier (opus/sonnet/haiku). Default: sonnet
            },
        )
        async def resume_session(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")
            prompt = args.get("prompt", "Continue from where you left off.")
            model = args.get("model", "sonnet")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                project = orchestrator.get_project(project_name)
                session = orchestrator.get_resumable_session(project)
                if not session or not session.claude_session_id:
                    msg = f"No resumable session for `{project_name}`. Use run_task to start a new session."
                    return {"content": [{"type": "text", "text": msg}]}
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            # Validate model
            try:
                ModelTier(model.lower())
            except ValueError:
                model = "sonnet"

            # Store the pending task
            self._pending_task = {
                "action": "resume_session",
                "project_name": project_name,
                "prompt": prompt,
                "model": model,
            }

            return {"content": [{"type": "text", "text": f"Resuming session on `{project_name}` with {model}..."}]}

        # Workspace tools
        @tool(
            "add_workspace",
            "Add a workspace directory to auto-discover projects",
            {
                "name": str,  # Name for the workspace
                "path": str,  # Path to the workspace directory (can use ~ for home)
            },
        )
        async def add_workspace(args: dict[str, Any]) -> dict[str, Any]:
            name = args.get("name", "")
            path = args.get("path", "")

            if not name or not path:
                return {"content": [{"type": "text", "text": "Error: name and path are required"}]}

            # Expand ~ to home directory
            path = os.path.expanduser(path)

            try:
                workspace, projects = orchestrator.register_workspace(name, path, auto_scan=True)
                result = f"✅ Workspace `{name}` added: {workspace.path}\n"
                if projects:
                    result += f"**Discovered {len(projects)} project(s):**\n"
                    for p in projects:
                        result += f"- `{p.name}`: {p.path}\n"
                else:
                    result += "No projects discovered yet. You can scan later with scan_workspace."
                return {"content": [{"type": "text", "text": result}]}
            except WorkspaceExistsError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}
            except ValueError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        @tool("list_workspaces", "List all registered workspaces", {})
        async def list_workspaces(args: dict[str, Any]) -> dict[str, Any]:
            workspaces = orchestrator.list_workspaces()
            if not workspaces:
                msg = "No workspaces registered. Use add_workspace to add one."
                return {"content": [{"type": "text", "text": msg}]}

            result = "**Workspaces:**\n"
            for w in workspaces:
                projects = orchestrator.list_workspace_projects(w.name)
                result += f"- `{w.name}`: {w.path} ({len(projects)} projects)\n"

            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "scan_workspace",
            "Scan a workspace for new projects",
            {
                "name": str,  # Name of the workspace to scan
            },
        )
        async def scan_workspace(args: dict[str, Any]) -> dict[str, Any]:
            name = args.get("name", "")

            if not name:
                return {"content": [{"type": "text", "text": "Error: workspace name is required"}]}

            try:
                new_projects = orchestrator.scan_workspace(name)
                if new_projects:
                    result = f"**Found {len(new_projects)} new project(s) in `{name}`:**\n"
                    for p in new_projects:
                        result += f"- `{p.name}`: {p.path}\n"
                else:
                    result = f"No new projects found in workspace `{name}`."
                return {"content": [{"type": "text", "text": result}]}
            except WorkspaceNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        # Run management tools
        @tool(
            "list_runs",
            "List all execution runs (from CLI, Web, Telegram, Discord)",
            {
                "project_name": str,  # Optional: filter by project name
                "active_only": bool,  # Optional: only show active runs (default: false)
            },
        )
        async def list_runs(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name")
            active_only = args.get("active_only", False)

            try:
                runs = orchestrator.list_runs(
                    project_name=project_name,
                    active_only=active_only,
                    limit=10,
                )
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            if not runs:
                return {"content": [{"type": "text", "text": "No runs found."}]}

            # Build project lookup for display
            project_lookup = {p.id: p.name for p in orchestrator.list_projects()}

            status_emojis = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }

            # Map initiator to friendly source names
            def get_source(initiator: str | None) -> str:
                if not initiator:
                    return ""
                if initiator.startswith("cli:"):
                    return "CLI"
                if initiator.startswith("telegram:"):
                    return "TG"
                if initiator.startswith("discord:"):
                    return "DC"
                if initiator.startswith("web:"):
                    return "Web"
                if initiator == "orchestrator":
                    return "Resume"
                return initiator[:6]

            result = "**Runs:**\n"
            for r in runs:
                emoji = status_emojis.get(r.status.value, "❓")
                proj_name = project_lookup.get(r.project_id, r.project_id[:8])
                source = get_source(r.initiator)
                prompt_preview = r.prompt[:30] + "..." if len(r.prompt) > 30 else r.prompt
                source_tag = f"[{source}] " if source else ""
                result += f"{emoji} `{r.id[:8]}` | {source_tag}{proj_name}\n"
                result += f"   _{prompt_preview}_\n"

            active_count = len(orchestrator.list_runs(active_only=True))
            if active_count:
                result += f"\n**{active_count}** run(s) currently active"

            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "cancel_run",
            "Cancel a running task",
            {
                "run_id": str,  # Run ID (can be short ID like 'abc123')
            },
        )
        async def cancel_run(args: dict[str, Any]) -> dict[str, Any]:
            run_id = args.get("run_id", "")

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            success, message = orchestrator.cancel_run(run_id)
            if success:
                return {"content": [{"type": "text", "text": f"✅ {message}"}]}
            else:
                return {"content": [{"type": "text", "text": f"Error: {message}"}]}

        # Git tools
        @tool(
            "get_git_status",
            "Get git status for a project (branch, uncommitted changes, ahead/behind)",
            {
                "project_name": str,  # Name of the project
            },
        )
        async def get_git_status(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                status = await orchestrator.get_git_status(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            if not status:
                return {"content": [{"type": "text", "text": f"No git status available for `{project_name}`."}]}

            if not status.is_git_repo:
                return {"content": [{"type": "text", "text": f"`{project_name}` is not a git repository."}]}

            result = f"**Git Status for `{project_name}`:**\n"
            result += f"Branch: `{status.branch or 'unknown'}`\n"

            if status.remote:
                result += f"Remote: {status.remote}\n"

            if status.has_uncommitted:
                result += f"⚠️ {status.uncommitted_count} uncommitted change(s)\n"
            else:
                result += "✅ Working tree clean\n"

            if status.commits_ahead or status.commits_behind:
                if status.is_diverged:
                    result += f"⚠️ Diverged: {status.commits_ahead} ahead, {status.commits_behind} behind\n"
                elif status.commits_ahead:
                    result += f"↑ {status.commits_ahead} commit(s) ahead\n"
                elif status.commits_behind:
                    result += f"↓ {status.commits_behind} commit(s) behind\n"
            else:
                result += "✅ Up to date with remote\n"

            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "git_sync",
            "Sync a project: auto-commit uncommitted changes, fetch from remote, and fast-forward",
            {
                "project_name": str,  # Name of the project
            },
        )
        async def git_sync(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                success, message = await orchestrator.git_sync(project_name)
                if success:
                    return {"content": [{"type": "text", "text": f"✅ {message}"}]}
                else:
                    return {"content": [{"type": "text", "text": f"❌ {message}"}]}
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        @tool(
            "git_push",
            "Commit all changes and push to remote",
            {
                "project_name": str,  # Name of the project
                "commit_message": str,  # Message for the commit
            },
        )
        async def git_push(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")
            commit_message = args.get("commit_message", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}
            if not commit_message:
                return {"content": [{"type": "text", "text": "Error: commit_message is required"}]}

            try:
                success, message = await orchestrator.git_push(project_name, commit_message)
                if success:
                    return {"content": [{"type": "text", "text": f"✅ {message}"}]}
                else:
                    return {"content": [{"type": "text", "text": f"❌ {message}"}]}
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        @tool(
            "git_fetch",
            "Fetch from remote to see what's new (without merging)",
            {
                "project_name": str,  # Name of the project
            },
        )
        async def git_fetch(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                success, message = await orchestrator.git_fetch(project_name)
                if success:
                    return {"content": [{"type": "text", "text": f"✅ {message}"}]}
                else:
                    return {"content": [{"type": "text", "text": f"❌ {message}"}]}
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        # Additional tools for enhanced functionality
        @tool(
            "get_run",
            "Get details for a specific run (status, cost, duration, error)",
            {
                "run_id": str,  # Run ID (can be short ID like 'abc12345')
            },
        )
        async def get_run(args: dict[str, Any]) -> dict[str, Any]:
            run_id = args.get("run_id", "")

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            # Build project lookup
            project_lookup = {p.id: p.name for p in orchestrator.list_projects()}
            proj_name = project_lookup.get(run.project_id, run.project_id[:8])

            status_emojis = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }
            emoji = status_emojis.get(run.status.value, "❓")

            result = f"**Run `{run.id[:8]}`** {emoji}\n"
            result += f"**Project:** {proj_name}\n"
            result += f"**Status:** {run.status.value}\n"
            result += f"**Prompt:** {run.prompt[:100]}{'...' if len(run.prompt) > 100 else ''}\n"

            if run.duration_seconds:
                result += f"**Duration:** {format_duration(run.duration_seconds)}\n"
            if run.cost_usd:
                result += f"**Cost:** ${run.cost_usd:.4f}\n"
            if run.input_tokens or run.output_tokens:
                result += f"**Tokens:** {run.input_tokens or 0:,} in / {run.output_tokens or 0:,} out\n"
            if run.model_used:
                result += f"**Model:** {run.model_used}\n"
            if run.branch_name:
                result += f"**Branch:** {run.branch_name}\n"
            if run.pr_url:
                result += f"**PR:** {run.pr_url}\n"
            if run.error_message:
                result += f"**Error:** {run.error_message}\n"
            if run.initiator:
                result += f"**Source:** {run.initiator}\n"

            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "get_logs",
            "Get logs from a run (what Claude did)",
            {
                "run_id": str,  # Run ID (can be short ID)
                "tail": int,  # Optional: only last N lines (default: 50)
            },
        )
        async def get_logs(args: dict[str, Any]) -> dict[str, Any]:
            run_id = args.get("run_id", "")
            tail = args.get("tail", 50)

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            # Use runner to get logs
            runner = TaskRunner(store=orchestrator.store)
            logs = runner.get_logs(run.id, tail=tail)

            stdout = logs.get("stdout", "")
            if not stdout:
                return {"content": [{"type": "text", "text": f"No logs available for run `{run_id[:8]}`"}]}

            # Truncate if too long for chat
            if len(stdout) > 3000:
                stdout = "...(truncated)...\n" + stdout[-3000:]

            result = f"**Logs for `{run.id[:8]}`:**\n```\n{stdout}\n```"
            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "add_project",
            "Register a single project (use add_workspace for multiple projects)",
            {
                "name": str,  # Unique name for the project
                "path": str,  # Path to the project directory (can use ~ for home)
            },
        )
        async def add_project(args: dict[str, Any]) -> dict[str, Any]:
            name = args.get("name", "")
            path = args.get("path", "")

            if not name or not path:
                return {"content": [{"type": "text", "text": "Error: name and path are required"}]}

            # Expand ~ to home directory
            path = os.path.expanduser(path)

            try:
                project = orchestrator.register_project(name, path)
                return {"content": [{"type": "text", "text": f"✅ Project `{name}` registered: {project.path}"}]}
            except ProjectExistsError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}
            except ValueError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        @tool(
            "get_usage",
            "Get usage summary (today's cost, weekly cost, run counts)",
            {},
        )
        async def get_usage(args: dict[str, Any]) -> dict[str, Any]:
            summary = orchestrator.store.get_usage_summary()

            result = "**Usage Summary**\n"
            result += f"**Today:** ${summary.get('today_cost', 0):.4f} ({summary.get('today_runs', 0)} runs)\n"
            result += f"**This Week:** ${summary.get('week_cost', 0):.4f} ({summary.get('week_runs', 0)} runs)\n"
            result += f"**Tokens Today:** {summary.get('today_input_tokens', 0):,} in / {summary.get('today_output_tokens', 0):,} out\n"

            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "create_pr",
            "Create a pull request for a worktree run",
            {
                "run_id": str,  # Run ID (must be a completed worktree run with a branch)
            },
        )
        async def create_pr(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            run_id = args.get("run_id", "")

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            if not run.branch_name:
                return {"content": [{"type": "text", "text": f"Run `{run_id[:8]}` has no branch (not a worktree run)"}]}

            if run.pr_url:
                return {"content": [{"type": "text", "text": f"PR already exists: {run.pr_url}"}]}

            # Get project path
            project = orchestrator.store.get_project(run.project_id)
            if not project:
                return {"content": [{"type": "text", "text": f"Project not found for run `{run_id[:8]}`"}]}

            git_manager = GitManager(orchestrator.store)
            try:
                result = await git_manager.push_branch_and_create_pr(
                    project_path=project.expanded_path,
                    branch_name=run.branch_name,
                    prompt=run.prompt,
                    run_id=run.id,
                )

                if result.get("pr_url"):
                    # Update run with PR info
                    run.pr_number = result.get("pr_number")
                    run.pr_url = result.get("pr_url")
                    run.pr_status = result.get("pr_status")
                    orchestrator.store.update_run(run)
                    return {"content": [{"type": "text", "text": f"✅ PR created: {result['pr_url']}"}]}
                elif result.get("error"):
                    return {"content": [{"type": "text", "text": f"❌ {result['error']}"}]}
                else:
                    return {"content": [{"type": "text", "text": "PR creation returned no result"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error creating PR: {e}"}]}

        # ========== Agent Workflow Tools (inspect & merge) ==========

        @tool(
            "get_run_commits",
            "Get commits made on a run's branch (for worktree runs)",
            {
                "run_id": str,  # Run ID (can be short ID like 'abc12345')
            },
        )
        async def get_run_commits(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            run_id = args.get("run_id", "")
            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            if not run.branch_name:
                return {"content": [{"type": "text", "text": f"Run `{run_id[:8]}` has no branch (not a worktree run)"}]}

            project = orchestrator.store.get_project(run.project_id)
            if not project:
                return {"content": [{"type": "text", "text": f"Project not found for run `{run_id[:8]}`"}]}

            # Determine working path
            working_path = (
                Path(run.worktree_path)
                if run.worktree_path and Path(run.worktree_path).exists()
                else project.expanded_path
            )
            base_branch = run.source_branch or "main"

            git_manager = GitManager(orchestrator.store)
            try:
                commits_data = await git_manager.get_branch_commits(
                    path=working_path,
                    branch_name=run.branch_name,
                    base_branch=base_branch,
                )

                if not commits_data:
                    return {"content": [{"type": "text", "text": f"No commits on branch `{run.branch_name}`"}]}

                result = f"**Commits on `{run.branch_name}`** ({len(commits_data)} total):\n"
                for c in commits_data[:10]:  # Limit to 10 for readability
                    sha_short = c["sha"][:7]
                    msg_first_line = c["message"].split("\n")[0][:60]
                    result += f"- `{sha_short}` {msg_first_line}\n"

                if len(commits_data) > 10:
                    result += f"... and {len(commits_data) - 10} more commits\n"

                return {"content": [{"type": "text", "text": result}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error getting commits: {e}"}]}

        @tool(
            "get_run_files",
            "Get files changed on a run's branch (for worktree runs)",
            {
                "run_id": str,  # Run ID (can be short ID like 'abc12345')
            },
        )
        async def get_run_files(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            run_id = args.get("run_id", "")
            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            if not run.branch_name:
                return {"content": [{"type": "text", "text": f"Run `{run_id[:8]}` has no branch (not a worktree run)"}]}

            project = orchestrator.store.get_project(run.project_id)
            if not project:
                return {"content": [{"type": "text", "text": f"Project not found for run `{run_id[:8]}`"}]}

            # Determine working path
            working_path = (
                Path(run.worktree_path)
                if run.worktree_path and Path(run.worktree_path).exists()
                else project.expanded_path
            )
            base_branch = run.source_branch or "main"

            git_manager = GitManager(orchestrator.store)
            try:
                files_data = await git_manager.get_changed_files(
                    path=working_path,
                    branch_name=run.branch_name,
                    base_branch=base_branch,
                )

                if not files_data:
                    return {"content": [{"type": "text", "text": f"No files changed on branch `{run.branch_name}`"}]}

                total_additions = sum(f["additions"] for f in files_data)
                total_deletions = sum(f["deletions"] for f in files_data)

                result = f"**Files changed on `{run.branch_name}`** ({len(files_data)} files, +{total_additions}/-{total_deletions}):\n"
                for f in files_data[:20]:  # Limit to 20 for readability
                    status_emoji = {"added": "🟢", "modified": "🟡", "deleted": "🔴"}.get(f["status"], "⚪")
                    result += f"{status_emoji} `{f['file_path']}` (+{f['additions']}/-{f['deletions']})\n"

                if len(files_data) > 20:
                    result += f"... and {len(files_data) - 20} more files\n"

                return {"content": [{"type": "text", "text": result}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error getting files: {e}"}]}

        @tool(
            "get_file_diff",
            "Get the diff for a specific file in a run's branch",
            {
                "run_id": str,  # Run ID (can be short ID like 'abc12345')
                "file_path": str,  # Path to the file (e.g., 'src/main.py')
            },
        )
        async def get_file_diff(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            run_id = args.get("run_id", "")
            file_path = args.get("file_path", "")

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}
            if not file_path:
                return {"content": [{"type": "text", "text": "Error: file_path is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            if not run.branch_name:
                return {"content": [{"type": "text", "text": f"Run `{run_id[:8]}` has no branch (not a worktree run)"}]}

            project = orchestrator.store.get_project(run.project_id)
            if not project:
                return {"content": [{"type": "text", "text": f"Project not found for run `{run_id[:8]}`"}]}

            # Determine working path
            working_path = (
                Path(run.worktree_path)
                if run.worktree_path and Path(run.worktree_path).exists()
                else project.expanded_path
            )
            base_branch = run.source_branch or "main"

            git_manager = GitManager(orchestrator.store)
            try:
                diff_data = await git_manager.get_file_diff(
                    path=working_path,
                    file_path=file_path,
                    branch_name=run.branch_name,
                    base_branch=base_branch,
                )

                diff_text = diff_data.get("diff", "")
                if not diff_text:
                    return {"content": [{"type": "text", "text": f"No diff available for `{file_path}`"}]}

                # Truncate if too long for chat
                if len(diff_text) > 3000:
                    diff_text = diff_text[:3000] + "\n... (truncated)"

                result = f"**Diff for `{file_path}`** (+{diff_data['additions']}/-{diff_data['deletions']}):\n```diff\n{diff_text}\n```"
                return {"content": [{"type": "text", "text": result}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error getting diff: {e}"}]}

        @tool(
            "merge_branch",
            "Merge a run's feature branch into main (completes the workflow)",
            {
                "run_id": str,  # Run ID (must be a completed worktree run with a branch)
            },
        )
        async def merge_branch(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            run_id = args.get("run_id", "")
            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            if not run.use_worktree or not run.branch_name:
                return {"content": [{"type": "text", "text": f"Run `{run_id[:8]}` is not a worktree run or has no branch"}]}

            project = orchestrator.store.get_project(run.project_id)
            if not project:
                return {"content": [{"type": "text", "text": f"Project not found for run `{run_id[:8]}`"}]}

            git_manager = GitManager(orchestrator.store)
            project_path = project.expanded_path
            base_branch = run.source_branch or "main"

            try:
                merge_result = await git_manager.merge_branch_locally(
                    project_path=project_path,
                    branch_name=run.branch_name,
                    base_branch=base_branch,
                    push_after_merge=True,
                )

                if merge_result.get("success"):
                    # Mark run as merged
                    run.pr_status = "merged"
                    orchestrator.store.update_run(run)

                    result = f"✅ Successfully merged `{run.branch_name}` into `{base_branch}`"
                    if merge_result.get("merged_commit_sha"):
                        result += f"\nCommit: `{merge_result['merged_commit_sha'][:7]}`"
                    if merge_result.get("message"):
                        result += f"\n{merge_result['message']}"
                    return {"content": [{"type": "text", "text": result}]}
                else:
                    error = merge_result.get("error", "Merge failed")
                    if merge_result.get("has_conflicts"):
                        conflicting = merge_result.get("conflicting_files", [])
                        result = f"❌ Merge conflict detected!\n**Conflicting files:**\n"
                        for f in conflicting[:10]:
                            result += f"- `{f}`\n"
                        result += "\nUse `check_conflicts` to see details, or resume the agent to resolve."
                        return {"content": [{"type": "text", "text": result}]}
                    return {"content": [{"type": "text", "text": f"❌ {error}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error merging: {e}"}]}

        @tool(
            "check_conflicts",
            "Check if a project has merge conflicts (rebase/merge in progress)",
            {
                "project_name": str,  # Name of the project
            },
        )
        async def check_conflicts(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")
            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                # Detect conflict state
                conflict_state = await git_manager._detect_conflict_state(project.expanded_path)
                conflicts = await git_manager.detect_conflicts(project.expanded_path)

                if not conflicts and not conflict_state.get("is_rebase_in_progress") and not conflict_state.get("is_merge_in_progress"):
                    return {"content": [{"type": "text", "text": f"✅ No conflicts in `{project_name}`"}]}

                result = f"**Conflicts in `{project_name}`:**\n"

                if conflict_state.get("is_rebase_in_progress"):
                    step = conflict_state.get("rebase_current_step", "?")
                    total = conflict_state.get("rebase_total_steps", "?")
                    result += f"⚠️ Rebase in progress (step {step}/{total})\n"
                elif conflict_state.get("is_merge_in_progress"):
                    result += "⚠️ Merge in progress\n"

                if conflicts:
                    result += f"\n**{len(conflicts)} conflicted file(s):**\n"
                    for c in conflicts[:10]:
                        markers = c.get("conflict_markers_count", 0)
                        result += f"- `{c['file_path']}` ({markers} conflict markers)\n"
                    if len(conflicts) > 10:
                        result += f"... and {len(conflicts) - 10} more files\n"

                return {"content": [{"type": "text", "text": result}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error checking conflicts: {e}"}]}

        # ========== Phase 1: High Priority Tools ==========

        @tool(
            "archive_run",
            "Archive a completed run (hides from default list, cleans up after retention period)",
            {
                "run_id": str,  # Run ID (can be short ID like 'abc12345')
                "unarchive": bool,  # Optional: set to true to unarchive (default: false)
            },
        )
        async def archive_run(args: dict[str, Any]) -> dict[str, Any]:
            run_id = args.get("run_id", "")
            unarchive = args.get("unarchive", False)

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            updated = orchestrator.store.archive_run(run.id, archived=not unarchive)
            if updated:
                action = "unarchived" if unarchive else "archived"
                return {"content": [{"type": "text", "text": f"✅ Run `{run.id[:8]}` {action}"}]}
            else:
                return {"content": [{"type": "text", "text": f"Failed to update run `{run_id[:8]}`"}]}

        @tool(
            "list_branches",
            "List all git branches in a project with their status",
            {
                "project_name": str,  # Name of the project
            },
        )
        async def list_branches(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")
            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                branches = await git_manager.list_branches(project.expanded_path)

                if not branches:
                    return {"content": [{"type": "text", "text": f"No branches found in `{project_name}`"}]}

                result = f"**Branches in `{project_name}`:**\n"
                for b in branches:
                    current = "→ " if b.get("is_current") else "  "
                    name = b.get("name", "unknown")
                    ahead = b.get("ahead", 0)
                    behind = b.get("behind", 0)

                    status = ""
                    if ahead or behind:
                        if ahead and behind:
                            status = f" (↑{ahead} ↓{behind})"
                        elif ahead:
                            status = f" (↑{ahead})"
                        elif behind:
                            status = f" (↓{behind})"

                    result += f"{current}`{name}`{status}\n"

                return {"content": [{"type": "text", "text": result}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error listing branches: {e}"}]}

        @tool(
            "delete_branch",
            "Delete a git branch (local or remote)",
            {
                "project_name": str,  # Name of the project
                "branch": str,  # Branch name to delete
                "remote": bool,  # Optional: delete from remote (default: false, local only)
                "force": bool,  # Optional: force delete unmerged branch (default: false)
            },
        )
        async def delete_branch(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")
            branch = args.get("branch", "")
            remote = args.get("remote", False)
            force = args.get("force", False)

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}
            if not branch:
                return {"content": [{"type": "text", "text": "Error: branch is required"}]}

            # Prevent deleting main/master
            if branch in ("main", "master"):
                return {"content": [{"type": "text", "text": "Error: Cannot delete main/master branch"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                result_data = await git_manager.delete_branch(
                    project.expanded_path, branch, force=force, remote=remote
                )

                if result_data.get("success"):
                    return {"content": [{"type": "text", "text": f"✅ {result_data.get('message', 'Branch deleted')}"}]}
                else:
                    return {"content": [{"type": "text", "text": f"❌ {result_data.get('message', 'Delete failed')}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error deleting branch: {e}"}]}

        @tool(
            "upload_image",
            "Upload an image to attach to a run (for visual context)",
            {
                "run_id": str,  # Run ID to attach the image to
                "image_path": str,  # Path to the image file (can use ~ for home)
            },
        )
        async def upload_image(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.image_storage import ImageStorageService, ImageStorageError

            run_id = args.get("run_id", "")
            image_path = args.get("image_path", "")

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}
            if not image_path:
                return {"content": [{"type": "text", "text": "Error: image_path is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            # Expand path
            image_path = os.path.expanduser(image_path)
            path = Path(image_path)

            if not path.exists():
                return {"content": [{"type": "text", "text": f"Image file not found: {image_path}"}]}

            try:
                storage = ImageStorageService(orchestrator.store)
                data = path.read_bytes()
                image = storage.save_image(data, path.name)
                storage.attach_to_run(run.id, image.id)

                return {"content": [{"type": "text", "text": f"✅ Image `{path.name}` attached to run `{run.id[:8]}`"}]}
            except ImageStorageError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error uploading image: {e}"}]}

        # ========== Phase 2: Medium Priority Tools ==========

        @tool(
            "remove_project",
            "Remove a project from Gluon (does not delete files, just unregisters)",
            {
                "project_name": str,  # Name of the project to remove
            },
        )
        async def remove_project(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                success = orchestrator.remove_project(project_name)
                if success:
                    return {"content": [{"type": "text", "text": f"✅ Project `{project_name}` removed"}]}
                else:
                    return {"content": [{"type": "text", "text": f"Failed to remove project `{project_name}`"}]}
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        @tool(
            "remove_workspace",
            "Remove a workspace from Gluon (optionally remove projects too)",
            {
                "name": str,  # Name of the workspace to remove
                "remove_projects": bool,  # Optional: also remove all projects in workspace (default: false)
            },
        )
        async def remove_workspace(args: dict[str, Any]) -> dict[str, Any]:
            name = args.get("name", "")
            remove_projects = args.get("remove_projects", False)

            if not name:
                return {"content": [{"type": "text", "text": "Error: workspace name is required"}]}

            try:
                success = orchestrator.remove_workspace(name, remove_projects=remove_projects)
                if success:
                    msg = f"✅ Workspace `{name}` removed"
                    if remove_projects:
                        msg += " (along with its projects)"
                    return {"content": [{"type": "text", "text": msg}]}
                else:
                    return {"content": [{"type": "text", "text": f"Failed to remove workspace `{name}`"}]}
            except WorkspaceNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        @tool(
            "list_workspace_projects",
            "List all projects in a specific workspace",
            {
                "name": str,  # Name of the workspace
            },
        )
        async def list_workspace_projects(args: dict[str, Any]) -> dict[str, Any]:
            name = args.get("name", "")

            if not name:
                return {"content": [{"type": "text", "text": "Error: workspace name is required"}]}

            try:
                projects = orchestrator.list_workspace_projects(name)

                if not projects:
                    return {"content": [{"type": "text", "text": f"No projects in workspace `{name}`"}]}

                result = f"**Projects in `{name}`:**\n"
                for p in projects:
                    sessions = orchestrator.list_sessions(p.name)
                    result += f"- `{p.name}`: {p.path} ({len(sessions)} sessions)\n"

                return {"content": [{"type": "text", "text": result}]}
            except WorkspaceNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        @tool(
            "get_usage_by_project",
            "Get cost breakdown by project",
            {
                "days": int,  # Optional: number of days to look back (default: 7)
            },
        )
        async def get_usage_by_project(args: dict[str, Any]) -> dict[str, Any]:
            from datetime import timedelta
            from gluon.models import utc_now

            days = args.get("days", 7)
            since = utc_now() - timedelta(days=days)

            usage_data = orchestrator.store.get_usage_by_project(since=since)

            if not usage_data:
                return {"content": [{"type": "text", "text": f"No usage data in the last {days} days"}]}

            result = f"**Usage by Project (last {days} days):**\n"
            total_cost = 0.0
            total_runs = 0

            for u in usage_data:
                cost = u.get("cost_usd", 0) or 0
                runs = u.get("run_count", 0)
                total_cost += cost
                total_runs += runs
                result += f"- `{u.get('project_name')}`: ${cost:.4f} ({runs} runs)\n"

            result += f"\n**Total:** ${total_cost:.4f} ({total_runs} runs)"

            return {"content": [{"type": "text", "text": result}]}

        @tool(
            "get_setting",
            "Get a Gluon configuration setting",
            {
                "key": str,  # Setting key (e.g., 'auto_create_pr')
            },
        )
        async def get_setting(args: dict[str, Any]) -> dict[str, Any]:
            key = args.get("key", "")

            if not key:
                # List all settings
                settings = orchestrator.store.get_all_settings()
                if not settings:
                    return {"content": [{"type": "text", "text": "No settings configured"}]}

                result = "**Gluon Settings:**\n"
                for k, v in settings.items():
                    result += f"- `{k}`: {v}\n"
                return {"content": [{"type": "text", "text": result}]}

            value = orchestrator.store.get_setting(key)
            if value is None:
                return {"content": [{"type": "text", "text": f"Setting `{key}` not found"}]}

            return {"content": [{"type": "text", "text": f"**{key}:** {value}"}]}

        @tool(
            "set_setting",
            "Update a Gluon configuration setting",
            {
                "key": str,  # Setting key (e.g., 'auto_create_pr')
                "value": str,  # New value for the setting
            },
        )
        async def set_setting(args: dict[str, Any]) -> dict[str, Any]:
            key = args.get("key", "")
            value = args.get("value", "")

            if not key:
                return {"content": [{"type": "text", "text": "Error: key is required"}]}
            if not value:
                return {"content": [{"type": "text", "text": "Error: value is required"}]}

            orchestrator.store.set_setting(key, value)
            return {"content": [{"type": "text", "text": f"✅ Setting `{key}` updated to `{value}`"}]}

        @tool(
            "rebase_branch",
            "Rebase current branch onto another branch",
            {
                "project_name": str,  # Name of the project
                "onto_branch": str,  # Branch to rebase onto (e.g., 'main')
            },
        )
        async def rebase_branch(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")
            onto_branch = args.get("onto_branch", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}
            if not onto_branch:
                return {"content": [{"type": "text", "text": "Error: onto_branch is required"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                result_data = await git_manager.rebase_branch(project.expanded_path, onto_branch)

                if result_data.get("success"):
                    return {"content": [{"type": "text", "text": f"✅ {result_data.get('message', 'Rebase completed')}"}]}
                else:
                    conflicts = result_data.get("conflicts", [])
                    if conflicts:
                        result = f"❌ Rebase conflict!\n**Conflicting files:**\n"
                        for f in conflicts[:10]:
                            result += f"- `{f}`\n"
                        result += "\nUse `rebase_continue` after resolving, or `rebase_abort` to cancel."
                        return {"content": [{"type": "text", "text": result}]}
                    return {"content": [{"type": "text", "text": f"❌ {result_data.get('message', 'Rebase failed')}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error during rebase: {e}"}]}

        @tool(
            "rebase_continue",
            "Continue a rebase after resolving conflicts",
            {
                "project_name": str,  # Name of the project
            },
        )
        async def rebase_continue(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                result_data = await git_manager.rebase_continue(project.expanded_path)

                if result_data.get("success"):
                    return {"content": [{"type": "text", "text": f"✅ {result_data.get('message', 'Rebase continued')}"}]}
                else:
                    conflicts = result_data.get("conflicts", [])
                    if conflicts:
                        result = f"❌ More conflicts!\n**Conflicting files:**\n"
                        for f in conflicts[:10]:
                            result += f"- `{f}`\n"
                        return {"content": [{"type": "text", "text": result}]}
                    return {"content": [{"type": "text", "text": f"❌ {result_data.get('message', 'Continue failed')}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error during rebase continue: {e}"}]}

        @tool(
            "rebase_abort",
            "Abort an in-progress rebase and restore previous state",
            {
                "project_name": str,  # Name of the project
            },
        )
        async def rebase_abort(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                result_data = await git_manager.rebase_abort(project.expanded_path)

                if result_data.get("success"):
                    return {"content": [{"type": "text", "text": f"✅ {result_data.get('message', 'Rebase aborted')}"}]}
                else:
                    return {"content": [{"type": "text", "text": f"❌ {result_data.get('message', 'Abort failed')}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error aborting rebase: {e}"}]}

        @tool(
            "list_run_images",
            "List images attached to a run",
            {
                "run_id": str,  # Run ID (can be short ID)
            },
        )
        async def list_run_images(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.image_storage import ImageStorageService

            run_id = args.get("run_id", "")

            if not run_id:
                return {"content": [{"type": "text", "text": "Error: run_id is required"}]}

            run = orchestrator.get_run(run_id)
            if not run:
                return {"content": [{"type": "text", "text": f"Run not found: {run_id}"}]}

            storage = ImageStorageService(orchestrator.store)
            images = storage.list_images_for_run(run.id)

            if not images:
                return {"content": [{"type": "text", "text": f"No images attached to run `{run.id[:8]}`"}]}

            result = f"**Images for run `{run.id[:8]}`:**\n"
            for img in images:
                size_kb = img.size_bytes / 1024
                result += f"- `{img.original_name}` ({size_kb:.1f} KB)\n"

            return {"content": [{"type": "text", "text": result}]}

        # ========== Conflict Resolution Tools ==========

        @tool(
            "get_conflict_diff",
            "Get 3-way diff for a conflicted file (base, ours, theirs versions)",
            {
                "project_name": str,  # Name of the project
                "file_path": str,  # Path to the conflicted file
            },
        )
        async def get_conflict_diff(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")
            file_path = args.get("file_path", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}
            if not file_path:
                return {"content": [{"type": "text", "text": "Error: file_path is required"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                diff_data = await git_manager.get_conflict_diff(project.expanded_path, file_path)

                result = f"**Conflict in `{file_path}`:**\n\n"

                if diff_data.get("base"):
                    base_preview = diff_data["base"][:500]
                    if len(diff_data["base"]) > 500:
                        base_preview += "\n... (truncated)"
                    result += f"**Base (common ancestor):**\n```\n{base_preview}\n```\n\n"
                else:
                    result += "**Base:** (not available - file added in both branches)\n\n"

                if diff_data.get("ours"):
                    ours_preview = diff_data["ours"][:500]
                    if len(diff_data["ours"]) > 500:
                        ours_preview += "\n... (truncated)"
                    result += f"**Ours (HEAD):**\n```\n{ours_preview}\n```\n\n"

                if diff_data.get("theirs"):
                    theirs_preview = diff_data["theirs"][:500]
                    if len(diff_data["theirs"]) > 500:
                        theirs_preview += "\n... (truncated)"
                    result += f"**Theirs (incoming):**\n```\n{theirs_preview}\n```\n\n"

                result += "Use `resolve_conflict` to resolve with 'ours', 'theirs', or 'resolved' (after manual edit)."

                return {"content": [{"type": "text", "text": result}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error getting conflict diff: {e}"}]}

        @tool(
            "resolve_conflict",
            "Resolve a git conflict by choosing ours, theirs, or marking as resolved",
            {
                "project_name": str,  # Name of the project
                "file_path": str,  # Path to the conflicted file
                "resolution": str,  # Resolution strategy: 'ours', 'theirs', or 'resolved'
            },
        )
        async def resolve_conflict(args: dict[str, Any]) -> dict[str, Any]:
            from gluon.git_manager import GitManager

            project_name = args.get("project_name", "")
            file_path = args.get("file_path", "")
            resolution = args.get("resolution", "")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}
            if not file_path:
                return {"content": [{"type": "text", "text": "Error: file_path is required"}]}
            if not resolution:
                return {"content": [{"type": "text", "text": "Error: resolution is required ('ours', 'theirs', or 'resolved')"}]}

            if resolution not in ("ours", "theirs", "resolved"):
                return {"content": [{"type": "text", "text": "Error: resolution must be 'ours', 'theirs', or 'resolved'"}]}

            try:
                project = orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            git_manager = GitManager(orchestrator.store)

            try:
                result_data = await git_manager.resolve_conflict(
                    project.expanded_path, file_path, resolution
                )

                if result_data.get("success"):
                    return {"content": [{"type": "text", "text": f"✅ {result_data.get('message', 'Conflict resolved')}"}]}
                else:
                    return {"content": [{"type": "text", "text": f"❌ {result_data.get('message', 'Resolution failed')}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error resolving conflict: {e}"}]}

        return [
            list_projects,
            list_sessions,
            get_status,
            run_task,
            resume_session,
            add_workspace,
            list_workspaces,
            scan_workspace,
            list_runs,
            cancel_run,
            get_git_status,
            git_sync,
            git_push,
            git_fetch,
            # New tools
            get_run,
            get_logs,
            add_project,
            get_usage,
            create_pr,
            # Agent workflow tools (inspect & merge)
            get_run_commits,
            get_run_files,
            get_file_diff,
            merge_branch,
            check_conflicts,
            # Phase 1: High Priority Tools
            archive_run,
            list_branches,
            delete_branch,
            upload_image,
            # Phase 2: Medium Priority Tools
            remove_project,
            remove_workspace,
            list_workspace_projects,
            get_usage_by_project,
            get_setting,
            set_setting,
            rebase_branch,
            rebase_continue,
            rebase_abort,
            list_run_images,
            # Conflict Resolution Tools
            get_conflict_diff,
            resolve_conflict,
        ]

    async def chat(
        self,
        message: str,
        history: list[ChatMessage] | None = None,
        reply_context: str | None = None,
    ) -> ChatResponse:
        """
        Process a natural language message and return a response.

        Args:
            message: The user's message
            history: Recent conversation history (oldest first)
            reply_context: If the user is replying to a specific message, that message's text

        May set self._pending_task if an action needs to be executed by the caller.
        """
        self._pending_task = None

        # Build the full message with context
        full_message = ""

        # Add reply context if present (user is replying to a specific message)
        if reply_context:
            full_message += f'[User is replying to this previous message: "{reply_context}"]\n\n'

        # Add recent conversation history
        if history:
            full_message += "[Recent conversation history:]\n"
            for msg in history[-10:]:  # Last 10 messages max
                prefix = "User" if msg.role == "user" else "Assistant"
                # Truncate long messages in history
                text = msg.text[:500] + "..." if len(msg.text) > 500 else msg.text
                full_message += f"{prefix}: {text}\n"
            full_message += "\n[Current message:]\n"

        full_message += message

        tools = self._create_tools()
        server = create_sdk_mcp_server(
            name="gluon-tools",
            version="1.0.0",
            tools=tools,
        )

        # Find Claude CLI path
        cli_path = find_claude_cli()
        if not cli_path:
            return ChatResponse(
                text="Error: Claude CLI not found. Please install Claude Code.",
                action_taken=None,
                action_result=None,
            )

        # Also add to PATH as fallback for SDK internals
        cli_dir = str(cli_path.parent)
        current_path = os.environ.get("PATH", "")
        if cli_dir not in current_path:
            os.environ["PATH"] = f"{cli_dir}:{current_path}"

        # Use Haiku for chat agent (fast, efficient for simple conversational tasks)
        haiku_model = get_model_id(ModelTier.HAIKU)

        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={"gluon": server},
            allowed_tools=[
                # Built-in tools
                "Read",
                "Glob",
                "Grep",
                "Bash",
                "BashOutput",
                "WebSearch",
                "WebFetch",
                # Gluon MCP tools
                "mcp__gluon__list_projects",
                "mcp__gluon__list_sessions",
                "mcp__gluon__get_status",
                "mcp__gluon__run_task",
                "mcp__gluon__resume_session",
                "mcp__gluon__add_workspace",
                "mcp__gluon__list_workspaces",
                "mcp__gluon__scan_workspace",
                "mcp__gluon__list_runs",
                "mcp__gluon__cancel_run",
                "mcp__gluon__get_git_status",
                "mcp__gluon__git_sync",
                "mcp__gluon__git_push",
                "mcp__gluon__git_fetch",
                # New tools
                "mcp__gluon__get_run",
                "mcp__gluon__get_logs",
                "mcp__gluon__add_project",
                "mcp__gluon__get_usage",
                "mcp__gluon__create_pr",
                # Agent workflow tools (inspect & merge)
                "mcp__gluon__get_run_commits",
                "mcp__gluon__get_run_files",
                "mcp__gluon__get_file_diff",
                "mcp__gluon__merge_branch",
                "mcp__gluon__check_conflicts",
                # Phase 1: High Priority Tools
                "mcp__gluon__archive_run",
                "mcp__gluon__list_branches",
                "mcp__gluon__delete_branch",
                "mcp__gluon__upload_image",
                # Phase 2: Medium Priority Tools
                "mcp__gluon__remove_project",
                "mcp__gluon__remove_workspace",
                "mcp__gluon__list_workspace_projects",
                "mcp__gluon__get_usage_by_project",
                "mcp__gluon__get_setting",
                "mcp__gluon__set_setting",
                "mcp__gluon__rebase_branch",
                "mcp__gluon__rebase_continue",
                "mcp__gluon__rebase_abort",
                "mcp__gluon__list_run_images",
                # Conflict Resolution Tools
                "mcp__gluon__get_conflict_diff",
                "mcp__gluon__resolve_conflict",
            ],
            max_turns=3,
            model=haiku_model,
        )

        response_text = ""
        action_taken = None

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(full_message)

                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                response_text += block.text + "\n"
                            elif isinstance(block, ToolUseBlock):
                                action_taken = block.name

                    elif isinstance(msg, ResultMessage):
                        pass  # Done

        except Exception as e:
            response_text = f"Error processing message: {e}"

        return ChatResponse(
            text=response_text.strip(),
            action_taken=action_taken,
            action_result=self._pending_task,
        )

    def get_pending_task(self) -> dict[str, Any] | None:
        """Get any pending task that needs to be executed."""
        return self._pending_task

    def clear_pending_task(self) -> None:
        """Clear the pending task."""
        self._pending_task = None
