"""Chat agent for natural language interaction with Gluon."""

import json
import os
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    tool,
    create_sdk_mcp_server,
)

from gluon.agent import find_claude_cli
from gluon.core import Orchestrator, ProjectNotFoundError, WorkspaceNotFoundError, WorkspaceExistsError


SYSTEM_PROMPT = """You are Gluon, an AI orchestrator that manages multiple Claude Code agents across different software projects.

You help users:
- List and manage their registered projects and workspaces
- Add workspaces to auto-discover projects in a directory
- Run coding tasks on projects using Claude Code agents
- Resume previous sessions to continue work
- Check status of sessions and costs

When users ask you to do something, use the available tools to help them. Be concise in your responses.

If a user wants to add a workspace directory, use add_workspace.
If they want to see their workspaces, use list_workspaces.
If they want to scan a workspace for new projects, use scan_workspace.
If a user wants to run a task on a project, use the run_task tool.
If they want to see their projects, use list_projects.
If they want to resume work, use resume_session.
If they want to see sessions, use list_sessions.
If they want status info, use get_status.

Always confirm what action you're taking before executing it."""


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

        @tool("list_sessions", "List sessions for a project or all sessions", {
            "project_name": str,  # Optional project name
        })
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
            for s in sessions[:10]:
                status_emoji = {"active": "🟢", "paused": "🟡", "completed": "🔵", "failed": "🔴"}.get(s.status.value, "⚪")
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

        @tool("run_task", "Run a coding task on a project using Claude Code", {
            "project_name": str,  # Name of the project
            "prompt": str,  # The task to perform
        })
        async def run_task(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")
            prompt = args.get("prompt", "")

            if not project_name or not prompt:
                return {"content": [{"type": "text", "text": "Error: project_name and prompt are required"}]}

            try:
                orchestrator.get_project(project_name)
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            # Store the pending task - actual execution happens in the bot
            self._pending_task = {
                "action": "run_task",
                "project_name": project_name,
                "prompt": prompt,
            }

            return {"content": [{"type": "text", "text": f"Starting task on `{project_name}`: {prompt[:100]}..."}]}

        @tool("resume_session", "Resume the last session for a project", {
            "project_name": str,  # Name of the project
            "prompt": str,  # Optional follow-up prompt
        })
        async def resume_session(args: dict[str, Any]) -> dict[str, Any]:
            project_name = args.get("project_name", "")
            prompt = args.get("prompt", "Continue from where you left off.")

            if not project_name:
                return {"content": [{"type": "text", "text": "Error: project_name is required"}]}

            try:
                project = orchestrator.get_project(project_name)
                session = orchestrator.get_resumable_session(project)
                if not session or not session.claude_session_id:
                    return {"content": [{"type": "text", "text": f"No resumable session for `{project_name}`. Use run_task to start a new session."}]}
            except ProjectNotFoundError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            # Store the pending task
            self._pending_task = {
                "action": "resume_session",
                "project_name": project_name,
                "prompt": prompt,
            }

            return {"content": [{"type": "text", "text": f"Resuming session on `{project_name}`..."}]}

        # Workspace tools
        @tool("add_workspace", "Add a workspace directory to auto-discover projects", {
            "name": str,  # Name for the workspace
            "path": str,  # Path to the workspace directory (can use ~ for home)
        })
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
                return {"content": [{"type": "text", "text": "No workspaces registered. Use add_workspace to add one."}]}

            result = "**Workspaces:**\n"
            for w in workspaces:
                projects = orchestrator.list_workspace_projects(w.name)
                result += f"- `{w.name}`: {w.path} ({len(projects)} projects)\n"

            return {"content": [{"type": "text", "text": result}]}

        @tool("scan_workspace", "Scan a workspace for new projects", {
            "name": str,  # Name of the workspace to scan
        })
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

        return [list_projects, list_sessions, get_status, run_task, resume_session, add_workspace, list_workspaces, scan_workspace]

    async def chat(self, message: str) -> ChatResponse:
        """
        Process a natural language message and return a response.

        May set self._pending_task if an action needs to be executed by the caller.
        """
        self._pending_task = None

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

        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={"gluon": server},
            allowed_tools=[
                "mcp__gluon__list_projects",
                "mcp__gluon__list_sessions",
                "mcp__gluon__get_status",
                "mcp__gluon__run_task",
                "mcp__gluon__resume_session",
                "mcp__gluon__add_workspace",
                "mcp__gluon__list_workspaces",
                "mcp__gluon__scan_workspace",
            ],
            max_turns=3,
            model="sonnet",
        )

        response_text = ""
        action_taken = None

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(message)

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
