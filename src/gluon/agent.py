"""Claude Agent SDK wrapper for Gluon."""

import base64
import logging
import mimetypes
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from gluon.models import GLUON_SYSTEM_PROMPT, PLANNING_SYSTEM_PROMPT
from gluon.models_config import get_model_id

# Type for question handler callback
# Args: run_id, questions (list of question dicts from AskUserQuestion)
# Returns: dict mapping question text -> answer string
QuestionHandler = Callable[[str, list[dict[str, Any]]], Awaitable[dict[str, str]]]

logger = logging.getLogger(__name__)


class ContextOverflowError(Exception):
    """Raised when API returns 400 due to input/context being too long."""

    pass


def _classify_api_error(error: Exception) -> Exception:
    """
    Classify API errors for appropriate handling.

    Detects context overflow errors (400 "input too long") to enable
    graceful recovery via fresh session.

    Args:
        error: The original exception

    Returns:
        ContextOverflowError if it's a context overflow, otherwise original error
    """
    error_str = str(error).lower()

    # Check for various forms of context overflow error messages
    is_context_overflow = (
        ("400" in error_str and "too long" in error_str)
        or ("400" in error_str and "input" in error_str and "long" in error_str)
        or ("input is too long" in error_str)
        or ("context" in error_str and "exceeded" in error_str)
        or ("token" in error_str and "limit" in error_str and "exceeded" in error_str)
    )

    if is_context_overflow:
        return ContextOverflowError(str(error))

    return error


# Default tools available to Claude Code agents
DEFAULT_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task", "TodoWrite"]

# Default MCP config locations (checked in order of priority)
MCP_CONFIG_PATHS = [
    # Host's Claude config (fallback)
    Path.home() / ".claude" / ".mcp.json",
    # Alternative location
    Path.home() / ".claude" / "mcp.json",
]


def find_mcp_config(working_dir: Path | None = None) -> Path | None:
    """
    Find MCP configuration file with layered precedence.

    Priority:
    1. Project-level .mcp.json (if working_dir provided)
    2. Host's ~/.claude/.mcp.json

    Args:
        working_dir: Optional project directory to check for local .mcp.json

    Returns:
        Path to MCP config file, or None if not found
    """
    # 1. Project-level .mcp.json (highest priority)
    if working_dir:
        project_mcp = working_dir / ".mcp.json"
        if project_mcp.exists():
            return project_mcp

    # 2. Check default locations
    for path in MCP_CONFIG_PATHS:
        if path.exists():
            return path

    return None


# Common locations for Claude CLI (checked in order)
CLAUDE_CLI_PATHS = [
    # Official Claude Code installation
    Path.home() / ".claude" / "local" / "claude",
    # npm global install locations
    Path.home() / ".npm-global" / "bin" / "claude",
    Path("/usr/local/bin/claude"),
    Path("/usr/bin/claude"),
    # npm local install
    Path.home() / "node_modules" / ".bin" / "claude",
    # bun global install
    Path.home() / ".bun" / "bin" / "claude",
    # pnpm global install
    Path.home() / ".local" / "share" / "pnpm" / "claude",
    # yarn global install
    Path.home() / ".yarn" / "bin" / "claude",
    # nvm-based npm global
    Path.home() / ".nvm" / "versions" / "node",  # Will be expanded
    # macOS Homebrew
    Path("/opt/homebrew/bin/claude"),
    # Linux snap
    Path("/snap/bin/claude"),
]


def find_claude_cli() -> Path | None:
    """Find the Claude CLI executable."""
    import shutil

    # First check if claude is already in PATH
    claude_in_path = shutil.which("claude")
    if claude_in_path:
        return Path(claude_in_path)

    # Check common locations
    for path in CLAUDE_CLI_PATHS:
        # Handle nvm special case - need to find the active node version
        if ".nvm" in str(path) and "versions" in str(path):
            nvm_dir = Path.home() / ".nvm" / "versions" / "node"
            if nvm_dir.exists():
                # Check each node version's bin directory
                for version_dir in sorted(nvm_dir.iterdir(), reverse=True):
                    claude_path = version_dir / "bin" / "claude"
                    if claude_path.exists() and os.access(claude_path, os.X_OK):
                        return claude_path
            continue

        if path.exists() and os.access(path, os.X_OK):
            return path

    return None


@dataclass
class AgentResult:
    """Result from an agent execution."""

    claude_session_id: str | None
    total_cost_usd: float
    total_turns: int
    success: bool
    error: str | None = None
    session_id: str | None = None  # Gluon session ID (for linking runs)
    execution_run_id: str | None = None  # ExecutionRun ID (for dashboard visibility)
    # Token usage
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_used: str | None = None


@dataclass
class AgentMessage:
    """Normalized message from agent execution."""

    type: str  # "text", "tool_use", "tool_result", "system", "error", "result"
    content: str
    metadata: dict[str, Any] | None = None


@dataclass
class ImageContent:
    """Image content for multimodal prompts."""

    path: Path
    media_type: str | None = None

    def to_content_block(self) -> dict[str, Any]:
        """Convert to API content block format."""
        if not self.path.exists():
            raise FileNotFoundError(f"Image not found: {self.path}")

        # Read and base64 encode the image
        data = base64.b64encode(self.path.read_bytes()).decode("utf-8")

        # Determine media type
        media_type = self.media_type
        if not media_type:
            media_type, _ = mimetypes.guess_type(str(self.path))
            if not media_type or not media_type.startswith("image/"):
                # Default based on extension
                suffix = self.path.suffix.lower()
                media_type = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(suffix, "image/png")

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        }


@dataclass
class MultimodalPrompt:
    """A prompt with optional images."""

    text: str
    images: list[ImageContent] = field(default_factory=list)

    def to_content_blocks(self) -> list[dict[str, Any]]:
        """Convert to API content block format."""
        blocks: list[dict[str, Any]] = []

        # Add images first (better for model understanding)
        for image in self.images:
            blocks.append(image.to_content_block())

        # Add text
        blocks.append({"type": "text", "text": self.text})

        return blocks


class GluonAgent:
    """Wrapper around Claude Agent SDK for managing code agent sessions."""

    def __init__(
        self,
        model: str = "sonnet",
        allowed_tools: list[str] | None = None,
        permission_mode: str = "bypassPermissions",
        cli_path: Path | str | None = None,
        question_handler: QuestionHandler | None = None,
        run_id: str | None = None,
        max_thinking_tokens: int | None = None,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        force_planning: bool = False,
    ):
        # Convert tier names (opus/sonnet/haiku) to full Bedrock model IDs
        # This ensures consistent model resolution across local and Docker environments
        try:
            self.model = get_model_id(model)
        except ValueError:
            # Already a full model ID, use as-is
            self.model = model
        self.allowed_tools = allowed_tools or DEFAULT_TOOLS
        self.permission_mode = permission_mode
        # Auto-detect CLI path if not provided
        self.cli_path = Path(cli_path) if cli_path else find_claude_cli()
        # Question handler for AskUserQuestion support
        self.question_handler = question_handler
        self.run_id = run_id
        # Task profile options
        self.max_thinking_tokens = max_thinking_tokens
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.force_planning = force_planning

    async def _can_use_tool(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow:
        """Handle tool permission requests from Claude SDK.

        For most tools, we allow immediately (bypassPermissions behavior).
        For AskUserQuestion, we invoke the question handler to get answers.
        """
        # Special handling for AskUserQuestion tool
        if tool_name == "AskUserQuestion" and self.question_handler and self.run_id:
            questions = input_data.get("questions", [])
            if questions:
                logger.info(f"AskUserQuestion intercepted with {len(questions)} question(s)")
                try:
                    # Call the question handler to get answers
                    answers = await self.question_handler(self.run_id, questions)

                    # Return with answers injected into the input
                    return PermissionResultAllow(
                        behavior="allow",
                        updated_input={
                            "questions": questions,
                            "answers": answers,
                        },
                    )
                except TimeoutError:
                    logger.warning("Question handler timed out, allowing with empty answers")
                except Exception as e:
                    logger.error(f"Question handler failed: {e}")

        # For all other tools (or if no handler), allow immediately
        return PermissionResultAllow(behavior="allow", updated_input=input_data)

    def _build_options(
        self,
        working_dir: Path,
        resume_session_id: str | None = None,
        fork_session: bool = False,
        new_session_id: str | None = None,
    ) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for a session.

        Args:
            working_dir: Path to project directory
            resume_session_id: Optional Claude session ID to resume
            fork_session: If True, fork the session for concurrent execution.
                         This allows multiple Gluon runs to execute in parallel
                         without blocking each other.
            new_session_id: If provided, use this as the session ID for new sessions.
                           This helps avoid control channel conflicts with other
                           Claude processes.
        """
        # Find MCP config (project-level takes precedence over host config)
        mcp_config = find_mcp_config(working_dir)

        # When MCP servers are configured, don't restrict allowed_tools
        # This lets the agent use MCP tools like mcp__scraper__perplexity
        # Without MCP, use the configured allowed_tools list
        effective_tools = None if mcp_config else self.allowed_tools

        # Use configured thinking tokens, default to 10000 if not set
        thinking_tokens = self.max_thinking_tokens if self.max_thinking_tokens is not None else 10000

        options = ClaudeAgentOptions(
            cwd=working_dir,
            allowed_tools=effective_tools,
            permission_mode=self.permission_mode,
            model=self.model,
            mcp_servers=mcp_config if mcp_config else {},
            max_thinking_tokens=thinking_tokens,
        )

        # Add max_turns if configured
        if self.max_turns is not None:
            options.max_turns = self.max_turns

        # Add max_budget_usd if configured
        if self.max_budget_usd is not None:
            options.max_budget_usd = self.max_budget_usd

        # Always append base Gluon system prompt (environment context)
        options.append_system_prompt = GLUON_SYSTEM_PROMPT

        # Additionally append planning prompt if force_planning is enabled
        if self.force_planning:
            options.append_system_prompt += "\n" + PLANNING_SYSTEM_PROMPT

        # Add can_use_tool callback if question handler is configured
        # This enables AskUserQuestion support
        if self.question_handler:
            options.can_use_tool = self._can_use_tool

        # Add resume option if we have a previous session
        if resume_session_id:
            options.resume = resume_session_id
            # Fork the session to allow concurrent execution
            # This creates an independent branch that won't conflict
            # with other sessions (including interactive Claude sessions)
            options.fork_session = True
        else:
            # For new sessions, use a unique session ID to avoid control
            # channel conflicts with other Claude processes (including
            # interactive Claude sessions running in the terminal)
            if new_session_id:
                options.extra_args = {"session-id": new_session_id}
            if fork_session:
                options.fork_session = True

        return options

    async def execute(
        self,
        working_dir: Path,
        prompt: str | MultimodalPrompt,
        resume_session_id: str | None = None,
        images: list[Path] | None = None,
        fork_session: bool = True,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project directory.

        Yields AgentMessage objects during execution, then yields
        a final AgentResult with session metadata.

        Args:
            working_dir: Path to project directory
            prompt: User prompt (string or MultimodalPrompt with images)
            resume_session_id: Optional Claude session ID to resume
            images: Optional list of image paths to include
            fork_session: If True (default), fork the session to allow
                         concurrent execution. This prevents "Control request
                         timeout: initialize" errors when running alongside
                         other Claude sessions.

        Yields:
            AgentMessage during execution
            AgentResult as final yield
        """
        # Generate a unique session ID for new sessions to avoid control
        # channel conflicts with other Claude processes
        new_session_id = str(uuid.uuid4()) if not resume_session_id else None
        options = self._build_options(working_dir, resume_session_id, fork_session, new_session_id)

        # Build multimodal prompt if images provided
        if images:
            text = prompt.text if isinstance(prompt, MultimodalPrompt) else prompt
            existing_images = prompt.images if isinstance(prompt, MultimodalPrompt) else []
            prompt = MultimodalPrompt(
                text=text,
                images=existing_images + [ImageContent(path=p) for p in images],
            )

        claude_session_id: str | None = None
        total_cost_usd: float = 0.0
        total_turns: int = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        success = True
        error_msg: str | None = None

        try:
            # Validate CLI path exists
            if not self.cli_path:
                raise RuntimeError(
                    "Claude CLI not found. Install Claude Code or ensure it's in your PATH. "
                    f"Checked: PATH, {', '.join(str(p) for p in CLAUDE_CLI_PATHS)}"
                )

            # Also add to PATH as fallback for SDK internals
            cli_dir = str(self.cli_path.parent)
            current_path = os.environ.get("PATH", "")
            if cli_dir not in current_path:
                os.environ["PATH"] = f"{cli_dir}:{current_path}"

            async with ClaudeSDKClient(options=options) as client:
                # Build query based on prompt type
                if isinstance(prompt, MultimodalPrompt) and prompt.images:
                    # Use async generator for multimodal content
                    async def multimodal_query():
                        content_blocks = prompt.to_content_blocks()
                        yield {
                            "type": "user",
                            "message": {"role": "user", "content": content_blocks},
                        }

                    await client.query(multimodal_query())
                else:
                    # Simple string prompt
                    text = prompt.text if isinstance(prompt, MultimodalPrompt) else prompt
                    await client.query(text)

                async for msg in client.receive_response():
                    # Extract session ID from system init message
                    if isinstance(msg, SystemMessage):
                        # Check for init message with session_id in data
                        if hasattr(msg, "subtype") and msg.subtype == "init":
                            if hasattr(msg, "data") and isinstance(msg.data, dict):
                                session_from_data = msg.data.get("session_id")
                                if session_from_data:
                                    claude_session_id = session_from_data
                        # Also check direct session_id attribute
                        if hasattr(msg, "session_id") and msg.session_id:
                            claude_session_id = msg.session_id
                        yield AgentMessage(
                            type="system",
                            content=getattr(msg, "subtype", ""),
                            metadata={"session_id": claude_session_id},
                        )

                    # Handle assistant messages (text and tool use)
                    elif isinstance(msg, AssistantMessage):
                        total_turns += 1
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                yield AgentMessage(type="text", content=block.text)
                            elif isinstance(block, ToolUseBlock):
                                yield AgentMessage(
                                    type="tool_use",
                                    content=f"Using tool: {block.name}",
                                    metadata={
                                        "tool": block.name,
                                        "id": block.id,
                                        "input": block.input,
                                    },
                                )

                    # Handle result message (final)
                    elif isinstance(msg, ResultMessage):
                        total_cost_usd = getattr(msg, "total_cost_usd", 0.0) or 0.0
                        total_turns = getattr(msg, "num_turns", 0) or total_turns
                        # Extract token usage from ResultMessage.usage dict
                        usage = getattr(msg, "usage", None) or {}
                        input_tokens = usage.get("input_tokens")
                        output_tokens = usage.get("output_tokens")
                        # Extract session_id from ResultMessage
                        if hasattr(msg, "session_id") and msg.session_id:
                            claude_session_id = msg.session_id
                        yield AgentMessage(
                            type="result",
                            content="Execution complete",
                            metadata={
                                "cost": total_cost_usd,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "session_id": claude_session_id,
                                "stop_reason": getattr(msg, "stop_reason", None),
                            },
                        )

        except Exception as e:
            success = False
            error_msg = str(e)

            # Classify the error for appropriate handling
            classified_error = _classify_api_error(e)

            if isinstance(classified_error, ContextOverflowError):
                # Context overflow is recoverable - emit special message
                yield AgentMessage(
                    type="error",
                    content=f"Context overflow: {error_msg}",
                    metadata={
                        "exception": "ContextOverflowError",
                        "recoverable": True,
                        "session_id": claude_session_id,
                    },
                )
            else:
                yield AgentMessage(
                    type="error",
                    content=f"Error: {error_msg}",
                    metadata={"exception": type(e).__name__},
                )

        # Yield final result
        yield AgentResult(
            claude_session_id=claude_session_id,
            total_cost_usd=total_cost_usd,
            total_turns=total_turns,
            success=success,
            error=error_msg,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_used=self.model,
        )

    async def execute_simple(
        self,
        working_dir: Path,
        prompt: str | MultimodalPrompt,
        resume_session_id: str | None = None,
        images: list[Path] | None = None,
        fork_session: bool = True,
    ) -> AgentResult:
        """
        Execute a prompt and return only the final result.

        This is a simpler interface that doesn't stream messages.
        """
        result: AgentResult | None = None

        async for item in self.execute(working_dir, prompt, resume_session_id, images, fork_session):
            if isinstance(item, AgentResult):
                result = item

        if result is None:
            return AgentResult(
                claude_session_id=None,
                total_cost_usd=0.0,
                total_turns=0,
                success=False,
                error="No result received from agent",
            )

        return result

    async def resume_with_fresh_context(
        self,
        recovery_state: dict[str, Any],
        working_dir: Path,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Resume work in a fresh session with summarized context.

        This method creates a new Claude session (no resume) with a summary
        of the previous session's progress, allowing recovery from context
        overflow errors.

        Args:
            recovery_state: Dict with recovery info from _extract_recovery_state:
                - original_prompt: The original task prompt
                - completed_work: List of completed task descriptions
                - branch_name: Git branch (if using worktree)
                - worktree_path: Worktree path (if using worktree)
            working_dir: Path to project directory

        Yields:
            AgentMessage during execution
            AgentResult as final yield
        """
        # Build completed work summary
        completed_work = recovery_state.get("completed_work", [])
        if completed_work:
            completed_list = "\n".join(f"- {task}" for task in completed_work)
        else:
            completed_list = "(No specific completed tasks recorded)"

        # Build branch context
        branch_context = ""
        if recovery_state.get("branch_name"):
            branch_context = f"- Working on branch: {recovery_state['branch_name']}"
        if recovery_state.get("worktree_path"):
            branch_context += f"\n- Worktree: {recovery_state['worktree_path']}"

        # Truncate long prompts
        original_prompt = recovery_state.get("original_prompt", "")
        if len(original_prompt) > 500:
            original_prompt = original_prompt[:500] + "..."

        # Build summary prompt
        summary_prompt = f"""## RECOVERY CONTEXT

You are resuming work that was interrupted due to context overflow.
The previous session ran out of context space and needs to continue in a fresh session.

**Original Task:**
{original_prompt}

**Completed Work:**
{completed_list}

**Current State:**
{branch_context if branch_context else "- Working in main project directory"}

## INSTRUCTIONS
1. Review the current state of the codebase to understand where work left off
2. Identify remaining work from the original task
3. Continue implementation from where it left off
4. Do NOT repeat already completed work listed above

## RESUME TASK
Continue the original task, picking up where the previous session left off.
Focus on what still needs to be done.
"""

        print(f"[AGENT] resume_with_fresh_context called, working_dir={working_dir}", flush=True)
        logger.info("Starting fresh session for context overflow recovery")

        # Yield a system message indicating recovery
        yield AgentMessage(
            type="system",
            content="Context overflow recovery - starting fresh session with progress summary",
            metadata={"recovery": True, "parent_run_id": recovery_state.get("run_id")},
        )

        # Start fresh session (no resume)
        async for item in self.execute(
            working_dir=working_dir,
            prompt=summary_prompt,
            resume_session_id=None,  # Fresh session - no resume
            fork_session=True,
        ):
            yield item
