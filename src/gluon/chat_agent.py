"""Chat agent for natural language interaction with Gluon."""

import os
from dataclasses import dataclass
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
    ProjectNotFoundError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from gluon.models_config import ModelTier, get_model_id

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
- list_runs, cancel_run - Monitor and cancel background runs
- get_git_status - Check git status for a project
- git_sync - Auto-commit, fetch, and fast-forward a project
- git_push - Commit and push changes to remote
- git_fetch - Fetch from remote to see what's new

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
