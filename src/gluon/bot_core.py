"""Transport-agnostic bot core for Gluon Agent."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from gluon.agent import AgentMessage, AgentResult
from gluon.chat_agent import ChatMessage, GluonChatAgent
from gluon.core import Orchestrator, ProjectNotFoundError
from gluon.git_manager import GitManager
from gluon.models import ExecutionRun, TaskProfile, ThinkingBudget
from gluon.models_config import ModelTier
from gluon.notifier import NotificationDispatcher
from gluon.runner import format_duration, format_run_status
from gluon.store import GluonStore
from gluon.transport import TransportContext, TransportResponse

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Type alias for send callback
SendCallback = Callable[
    [TransportContext, TransportResponse],
    Coroutine[Any, Any, str | None],
]


class GluonBotCore:
    """Transport-agnostic bot coordination logic.

    Handles message processing, task execution, state management,
    and chat agent integration. Transport implementations delegate
    to this core for business logic.
    """

    def __init__(
        self,
        store: GluonStore | None = None,
        orchestrator: Orchestrator | None = None,
        git_manager: GitManager | None = None,
        max_concurrent: int = 16,
        notifier: NotificationDispatcher | None = None,
    ):
        """Initialize the bot core.

        Args:
            store: Gluon store instance
            orchestrator: Orchestrator instance
            git_manager: Git manager instance
            max_concurrent: Maximum concurrent tasks
            notifier: Notification dispatcher for run status changes
        """
        self.store = store or GluonStore()
        self.git_manager = git_manager or GitManager(store=self.store)
        self.notifier = notifier or NotificationDispatcher(store=self.store)
        self.orchestrator = orchestrator or Orchestrator(
            store=self.store,
            git_manager=self.git_manager,
            notifier=self.notifier,
        )
        self.chat_agent = GluonChatAgent(self.orchestrator)

        # State management (transport-agnostic)
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_history_per_user = 10

        # Natural language mode
        self.nl_mode_enabled = True

    # ========== Authorization ==========

    def is_authorized(self, user_id: str, allowed_users: set[str] | None) -> bool:
        """Check if a user is authorized.

        Args:
            user_id: Universal user ID (e.g., 'telegram:123', 'discord:456')
            allowed_users: Set of allowed user IDs, or None to allow all

        Returns:
            True if authorized
        """
        if allowed_users is None:
            return True
        return user_id in allowed_users

    # ========== Message History (Store-backed) ==========

    def add_to_history(self, user_id: str, role: str, text: str) -> None:
        """Add a message to user's conversation history.

        Persisted to database for survival across restarts.

        Args:
            user_id: Universal user ID (e.g., 'telegram:123', 'discord:456')
            role: 'user' or 'assistant'
            text: Message text
        """
        # Extract transport from user_id (format: 'transport:id')
        transport = user_id.split(":")[0] if ":" in user_id else "unknown"
        self.store.create_chat_history(user_id, transport, role, text)

    def get_history(self, user_id: str) -> list[ChatMessage]:
        """Get conversation history for a user.

        Returns non-expired entries from database in chronological order.
        """
        entries = self.store.get_chat_history(user_id, limit=self._max_history_per_user)
        return [ChatMessage(role=e.role, text=e.text) for e in entries]

    def clear_history(self, user_id: str) -> None:
        """Clear conversation history for a user."""
        self.store.clear_chat_history(user_id)

    # ========== Run Info Extraction ==========

    def extract_run_info_from_message(self, text: str) -> tuple[str | None, str | None]:
        """Extract run ID and project name from a bot message.

        Parses completion/status messages to find run context.

        Returns:
            Tuple of (run_id, project_name) - either may be None
        """
        run_id = None
        project_name = None

        # Pattern: "Complete (abc12345)" or "Failed (abc12345)"
        complete_match = re.search(r"[✅❌]\s*\*?\*?(?:Complete|Failed)\*?\*?\s*\(`?([a-f0-9]{8})`?\)", text)
        if complete_match:
            run_id = complete_match.group(1)

        # Pattern: "Run: abc12345"
        run_match = re.search(r"Run:\s*`?([a-f0-9]{8})`?", text)
        if run_match:
            run_id = run_match.group(1)

        # Pattern: "Task started: abc12345"
        task_match = re.search(r"Task\s*(?:started:)?\s*`?([a-f0-9]{8})`?", text)
        if task_match and not run_id:
            run_id = task_match.group(1)

        # Pattern: "Project: myapp"
        project_match = re.search(r"Project:\s*`?([a-zA-Z0-9_/-]+)`?", text)
        if project_match:
            project_name = project_match.group(1)

        return run_id, project_name

    # ========== Concurrency Management ==========

    def get_active_run_count(self) -> int:
        """Get count of active runs in the store."""
        return len(self.store.list_active_runs())

    def is_at_capacity(self) -> bool:
        """Check if we're at the concurrent task limit."""
        return self.get_active_run_count() >= self._semaphore._value

    def register_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        """Register an active asyncio task."""
        self._active_tasks[run_id] = task

    def unregister_task(self, run_id: str) -> None:
        """Unregister a task."""
        if run_id in self._active_tasks:
            del self._active_tasks[run_id]

    def get_task(self, run_id: str) -> asyncio.Task[Any] | None:
        """Get a registered task by run ID."""
        return self._active_tasks.get(run_id)

    async def cancel_task(self, run_id: str) -> bool:
        """Cancel a task by run ID.

        Returns:
            True if task was found and cancelled
        """
        task = self._active_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.unregister_task(run_id)
            return True
        return False

    # ========== Project Resolution ==========

    def resolve_project(
        self,
        project_hint: str | None,
        channel_name: str | None = None,
    ) -> str | None:
        """Resolve a project name from hints.

        Args:
            project_hint: Direct project name hint
            channel_name: Channel name to try matching

        Returns:
            Project name if found, None otherwise
        """
        # Direct hint takes priority
        if project_hint:
            try:
                self.orchestrator.get_project(project_hint)
                return project_hint
            except ProjectNotFoundError:
                pass

        # Try channel name match (normalize - to _)
        if channel_name:
            normalized = channel_name.lower().replace("-", "_")
            try:
                project = self.orchestrator.get_project(normalized)
                return project.name
            except ProjectNotFoundError:
                pass

        return None

    # ========== Task Execution ==========

    async def execute_task(
        self,
        ctx: TransportContext,
        run: ExecutionRun,
        project_name: str,
        send_callback: SendCallback,
        model: ModelTier | str | None = None,
        force_new_session: bool = True,
        session_id: str | None = None,
        initial_msg_id: str | None = None,
        create_thread_callback: Callable[[TransportContext, str, str | None], Coroutine[Any, Any, str | None]]
        | None = None,
        use_worktree: bool = False,
        # Task profile options
        profile: TaskProfile | str | None = None,
        thinking_budget: ThinkingBudget | str | None = None,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        force_planning: bool | None = None,
    ) -> None:
        """Execute a Gluon task with streaming updates.

        This is the core execution logic, transport-agnostic.
        The transport provides callbacks for sending messages.

        Args:
            ctx: Transport context
            run: Execution run record
            project_name: Project name
            send_callback: Async callback to send messages
            model: Model tier to use
            force_new_session: Force new session vs resume
            session_id: Specific session ID to resume (from previous run)
            initial_msg_id: ID of initial message (for threading)
            create_thread_callback: Optional callback to create threads
            use_worktree: Execute in isolated Git worktree (default: False)
            profile: Task profile (quick, standard, deep, planning)
            thinking_budget: Thinking budget override
            max_turns: Maximum conversation turns
            max_budget_usd: Maximum cost in USD
            force_planning: Force planning mode
        """
        result: AgentResult | None = None
        message_buffer: list[str] = []
        last_update_time = 0.0
        thread_id: str | None = None

        # Note: Run status management (mark_running, mark_completed, mark_failed)
        # is now handled by orchestrator.execute() for unified tracking

        async def send_update(text: str, thread: bool = False) -> None:
            """Send an update message."""
            try:
                response = TransportResponse(
                    text=text,
                    thread_id=thread_id if thread else None,
                    reply_to_id=initial_msg_id if thread and not thread_id else None,
                )
                await send_callback(ctx, response)
            except Exception as e:
                logger.warning(f"Failed to send update: {e}")

        try:
            async with self._semaphore:
                # Create thread if callback provided and platform supports it
                if create_thread_callback and initial_msg_id:
                    thread_name = f"Run {run.id[:8]}: {run.prompt[:40]}..."
                    thread_id = await create_thread_callback(ctx, thread_name, initial_msg_id)

                execution = self.orchestrator.execute(
                    project_name,
                    run.prompt,
                    run_id=run.id,  # Link to pre-created ExecutionRun
                    force_new_session=force_new_session,
                    model=model,
                    session_id=session_id,
                    use_worktree=use_worktree,
                    # Task profile options
                    profile=profile,
                    thinking_budget=thinking_budget,
                    max_turns=max_turns,
                    max_budget_usd=max_budget_usd,
                    force_planning=force_planning,
                )

                async for item in execution:
                    if isinstance(item, AgentMessage):
                        content_to_add: str | None = None

                        if item.type == "text" and item.content:
                            content_to_add = item.content
                        elif item.type == "tool_use" and item.metadata:
                            # Format tool call for display
                            tool_name = item.metadata.get("tool", "unknown")
                            tool_input = item.metadata.get("input", {})
                            # Create compact representation of tool call
                            if isinstance(tool_input, dict):
                                # Show key params for common tools
                                if tool_name == "Read" and "file_path" in tool_input:
                                    param = tool_input["file_path"]
                                    if len(param) > 50:
                                        param = "..." + param[-47:]
                                    content_to_add = f"🔧 `{tool_name}({param})`"
                                elif tool_name == "Bash" and "command" in tool_input:
                                    param = tool_input["command"][:40]
                                    content_to_add = f"🔧 `{tool_name}({param}...)`"
                                elif tool_name in ("Grep", "Glob") and "pattern" in tool_input:
                                    param = tool_input["pattern"][:30]
                                    content_to_add = f"🔧 `{tool_name}({param})`"
                                elif tool_name == "Edit" and "file_path" in tool_input:
                                    param = tool_input["file_path"]
                                    if len(param) > 40:
                                        param = "..." + param[-37:]
                                    content_to_add = f"🔧 `{tool_name}({param})`"
                                elif tool_name == "Write" and "file_path" in tool_input:
                                    param = tool_input["file_path"]
                                    if len(param) > 40:
                                        param = "..." + param[-37:]
                                    content_to_add = f"🔧 `{tool_name}({param})`"
                                elif tool_name == "Task":
                                    desc = tool_input.get("description", "")[:30]
                                    content_to_add = f"🔧 `{tool_name}({desc})`"
                                else:
                                    content_to_add = f"🔧 `{tool_name}`"
                            else:
                                content_to_add = f"🔧 `{tool_name}`"

                        if content_to_add:
                            message_buffer.append(content_to_add)
                            current_time = asyncio.get_event_loop().time()

                            # Send updates every 2 seconds
                            if current_time - last_update_time > 2.0 and message_buffer:
                                text = "\n".join(message_buffer[-5:])
                                if len(text) > 4000:
                                    text = text[-4000:]
                                await send_update(text, thread=bool(thread_id))
                                message_buffer.clear()
                                last_update_time = current_time

                    elif isinstance(item, AgentResult):
                        result = item

                # Send remaining buffered messages
                if message_buffer:
                    text = "\n".join(message_buffer[-5:])
                    if len(text) > 4000:
                        text = text[-4000:]
                    await send_update(text, thread=bool(thread_id))

                # Send summary message (status updates handled by orchestrator)
                if result:
                    if result.success:
                        summary = (
                            f"✅ **Complete** (`{run.id[:8]}`)\n"
                            f"Cost: ${result.total_cost_usd:.4f}\n"
                            f"Turns: {result.total_turns}"
                        )
                    else:
                        summary = f"❌ **Failed** (`{run.id[:8]}`): {result.error}"

                    # Send completion message
                    await send_update(summary, thread=bool(thread_id))

        except asyncio.CancelledError:
            # Note: orchestrator exception handler marks run as failed
            await send_update(f"Task `{run.id[:8]}` was cancelled.", thread=bool(thread_id))

        except Exception as e:
            # Note: orchestrator exception handler marks run as failed
            logger.exception("Task execution failed")
            await send_update(f"❌ Error (`{run.id[:8]}`): {e}", thread=bool(thread_id))

        finally:
            # Unregister task from active tasks
            self.unregister_task(run.id)

    # ========== Chat Agent Integration ==========

    async def process_natural_language(
        self,
        ctx: TransportContext,
        text: str,
        send_callback: SendCallback,
        reply_context: str | None = None,
    ) -> dict[str, Any] | None:
        """Process a natural language message through the chat agent.

        Args:
            ctx: Transport context
            text: User message text
            send_callback: Callback to send responses
            reply_context: Text of message being replied to, if any

        Returns:
            Pending task dict if chat agent wants to execute a task, None otherwise
        """
        user_id = ctx.user_id
        history = self.get_history(user_id)

        try:
            response = await self.chat_agent.chat(
                text,
                history=history,
                reply_context=reply_context,
            )

            # Store user message in history
            self.add_to_history(user_id, "user", text)

            # Send response
            response_text = ""
            if response.text:
                response_text = response.text[:4000]
                await send_callback(
                    ctx,
                    TransportResponse(text=response_text, parse_mode="markdown"),
                )

            # Store assistant response in history
            if response_text:
                self.add_to_history(user_id, "assistant", response_text)

            # Return pending task if any
            return self.chat_agent.get_pending_task()

        except Exception as e:
            logger.exception("Error processing natural language")
            await send_callback(ctx, TransportResponse(text=f"Error: {e}"))
            return None

    # ========== Command Helpers ==========

    def format_projects_list(
        self,
        filter_term: str | None = None,
        limit: int = 20,
    ) -> str:
        """Format a list of projects for display.

        Args:
            filter_term: Optional filter for project names
            limit: Maximum projects to show

        Returns:
            Formatted markdown string
        """
        projects = self.orchestrator.list_projects()

        if filter_term:
            projects = [p for p in projects if filter_term.lower() in p.name.lower()]

        if not projects:
            if filter_term:
                return f"No projects matching `{filter_term}`."
            return "No projects registered.\nUse CLI: `gluon project add <name> <path>`"

        header = (
            f"**Projects matching `{filter_term}` ({len(projects)}):**\n"
            if filter_term
            else f"**Projects ({len(projects)}):**\n"
        )
        lines = [header]

        for p in projects[:limit]:
            sessions = self.orchestrator.list_sessions(p.name)
            path_str = str(p.path)
            if len(path_str) > 50:
                path_str = "..." + path_str[-47:]
            safe_path = path_str.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
            lines.append(f"- `{p.name}` ({len(sessions)} sessions)")
            lines.append(f"  {safe_path}")

        if len(projects) > limit:
            lines.append(f"\n_...and {len(projects) - limit} more projects_")

        return "\n".join(lines)

    def format_runs_list(
        self,
        initiator: str | None = None,
        limit: int = 10,
    ) -> str:
        """Format a list of runs for display.

        Args:
            initiator: Filter by initiator (e.g., 'telegram:123')
            limit: Maximum runs to show

        Returns:
            Formatted markdown string
        """
        runs_list = self.store.list_runs(initiator=initiator, limit=limit)

        if not runs_list:
            if initiator:
                return "No runs found.\nUse `/runs all` to see all runs."
            return "No runs found."

        # Build project lookup
        projects = self.store.list_projects()
        project_lookup = {p.id: p.name for p in projects}

        header = "**Recent Runs:**\n" if not initiator else "**Your Runs:**\n"
        lines = [header]

        for run in runs_list:
            emoji, _ = format_run_status(run.status)
            proj_name = project_lookup.get(run.project_id, run.project_id[:8])
            duration = format_duration(run.duration_seconds) if run.duration_seconds else "-"
            prompt_preview = run.prompt[:25] + "..." if len(run.prompt) > 25 else run.prompt

            line = f"{emoji} `{run.id[:8]}` | {proj_name}"
            line += f"\n   _{prompt_preview}_ ({duration})"
            lines.append(line)

        active_runs = self.store.list_active_runs()
        if active_runs:
            lines.append(f"\n**{len(active_runs)}** run(s) currently active")

        return "\n".join(lines)

    def format_status(self) -> str:
        """Format overall status for display."""
        status_info = self.orchestrator.status()

        text = (
            f"**Gluon Status**\n\n"
            f"Projects: {status_info['total_projects']}\n"
            f"Active Sessions: {status_info['active_sessions']}\n"
        )

        if status_info["projects"]:
            text += "\n**Projects:**\n"
            for p in status_info["projects"]:
                text += f"- `{p['name']}`: {p['sessions']} sessions\n"

        return text

    # ========== Recovery ==========

    def recover_stale_runs(self, transport_prefix: str) -> int:
        """Mark stale runs from previous bot instances as failed.

        Args:
            transport_prefix: Transport prefix to filter (e.g., 'telegram')

        Returns:
            Number of runs recovered
        """
        active_runs = self.store.list_active_runs()
        transport_runs = [r for r in active_runs if r.initiator and r.initiator.startswith(f"{transport_prefix}:")]

        if transport_runs:
            logger.info(f"Recovering {len(transport_runs)} stale {transport_prefix} run(s) from previous instance")
            for run in transport_runs:
                run.mark_failed("Bot restarted - run interrupted", exit_code=1)
                self.store.update_run(run)
                logger.info(f"Marked run {run.id[:8]} as failed (bot restart)")

        return len(transport_runs)
