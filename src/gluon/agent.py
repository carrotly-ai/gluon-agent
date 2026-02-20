"""Claude Agent SDK wrapper for Gluon."""

import asyncio
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
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    PermissionResultAllow,
    PermissionResultDeny,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
)

from gluon.agent_hooks import ScreenshotCollector, SubagentTracker, build_hooks
from gluon.models import (
    AGENT_BROWSER_SYSTEM_PROMPT,
    GLUON_SYSTEM_PROMPT,
    PLANNING_AUTONOMOUS_PROMPT,
    PLANNING_SYSTEM_PROMPT,
    RALPH_SYSTEM_PROMPT,
    VERCEL_SYSTEM_PROMPT,
)
from gluon.models_config import ModelTier, get_fallback_model_id, get_model_id

# Type for question handler callback
# Args: run_id, questions (list of question dicts from AskUserQuestion)
# Returns: dict mapping question text -> answer string
QuestionHandler = Callable[[str, list[dict[str, Any]]], Awaitable[dict[str, str]]]

logger = logging.getLogger(__name__)


class ContextOverflowError(Exception):
    """Raised when API returns 400 due to input/context being too long."""

    pass


class RateLimitError(Exception):
    """Raised when API returns 429 (rate limited / throttled)."""

    pass


class ModelUnavailableError(Exception):
    """Raised when the requested model is not found or not available."""

    pass


class AuthenticationError(Exception):
    """Raised when API returns 401/403 (invalid credentials or insufficient permissions)."""

    pass


def _classify_api_error(error: Exception) -> Exception:
    """
    Classify API errors for appropriate handling.

    Detects specific error categories to enable targeted recovery:
    - ContextOverflowError: 400 "input too long" → fresh session recovery
    - RateLimitError: 429 → backoff/retry (SDK fallback_model helps here)
    - ModelUnavailableError: model not found → try different model
    - AuthenticationError: 401/403 → re-authenticate

    Args:
        error: The original exception

    Returns:
        Classified exception subtype, or the original error if unrecognised
    """
    error_str = str(error).lower()

    # Context overflow (400 "input too long")
    is_context_overflow = (
        ("400" in error_str and "too long" in error_str)
        or ("400" in error_str and "input" in error_str and "long" in error_str)
        or ("input is too long" in error_str)
        or ("context" in error_str and "exceeded" in error_str)
        or ("token" in error_str and "limit" in error_str and "exceeded" in error_str)
    )
    if is_context_overflow:
        return ContextOverflowError(str(error))

    # Rate limiting (429)
    if "429" in error_str or "rate limit" in error_str or "throttl" in error_str:
        return RateLimitError(str(error))

    # Model unavailable
    if ("model" in error_str and ("not found" in error_str or "not available" in error_str)) or (
        "no access" in error_str and "model" in error_str
    ):
        return ModelUnavailableError(str(error))

    # Authentication errors (401/403)
    if (
        "401" in error_str
        or "403" in error_str
        or "unauthorized" in error_str
        or "forbidden" in error_str
        or ("credentials" in error_str and ("invalid" in error_str or "expired" in error_str))
    ):
        return AuthenticationError(str(error))

    return error


# Default tools available to Claude Code agents
DEFAULT_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task", "TodoWrite"]

# Dangerous command patterns blocked via PermissionResultDeny
DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf $HOME",
    "git push --force origin main",
    "git push --force origin master",
    "git push -f origin main",
    "git push -f origin master",
    "DROP TABLE",
    "DROP DATABASE",
    "DELETE FROM",
    "chmod 777",
    ":(){ :|:& };:",  # Fork bomb
    "> /dev/sda",
    "mkfs.",
    "dd if=/dev/zero of=/dev/",
]

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
        sandbox_enabled: bool = True,
        agent_teams_enabled: bool = False,
        extended_context_enabled: bool = False,
        file_checkpointing_enabled: bool = False,
        disallowed_tools: list[str] | None = None,
        model_transition: str | None = None,
        effort: str | None = None,
        vercel_cli_enabled: bool = False,
        vercel_token: str | None = None,
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
        # Reasoning effort level (low/medium/high/max) — native SDK field
        self.effort = effort
        # Security sandbox (OS-level isolation via bubblewrap/sandbox-exec)
        self.sandbox_enabled = sandbox_enabled
        # Experimental: coordinated multi-agent teams
        self.agent_teams_enabled = agent_teams_enabled
        # SDK 0.1.35 features
        self.extended_context_enabled = extended_context_enabled
        self.file_checkpointing_enabled = file_checkpointing_enabled
        self.disallowed_tools = disallowed_tools
        self.model_transition = model_transition
        self.vercel_cli_enabled = vercel_cli_enabled
        self.vercel_token = vercel_token

    async def _can_use_tool(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Handle tool permission requests from Claude SDK.

        Safety guardrails: blocks dangerous bash commands via PermissionResultDeny.
        AskUserQuestion: invokes the question handler to get answers.
        All other tools: allowed immediately (bypassPermissions behavior).
        """
        # Safety guardrail: block dangerous bash commands
        if tool_name == "Bash":
            cmd = input_data.get("command", "")
            for pattern in DANGEROUS_PATTERNS:
                if pattern.lower() in cmd.lower():
                    logger.warning(f"Blocked dangerous command: {cmd[:100]}")
                    return PermissionResultDeny(
                        message=f"Blocked dangerous command matching pattern: {pattern}",
                    )

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
        ralph_mode: bool = False,
        subagent_tracker: SubagentTracker | None = None,
        screenshot_collector: ScreenshotCollector | None = None,
        notification_callback: Callable[[dict[str, Any]], None] | None = None,
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
            ralph_mode: If True, append RALPH_SYSTEM_PROMPT for status reporting.
        """
        # Find MCP config (project-level takes precedence over host config)
        mcp_config = find_mcp_config(working_dir)

        # When MCP servers are configured, don't restrict allowed_tools
        # This lets the agent use MCP tools like mcp__scraper__perplexity
        # Without MCP, use the configured allowed_tools list
        effective_tools = None if mcp_config else self.allowed_tools

        # Use configured thinking tokens, default to 10000 if not set
        # -1 is the sentinel for "adaptive" — don't set max_thinking_tokens, let CLI decide
        thinking_tokens = self.max_thinking_tokens if self.max_thinking_tokens is not None else 10000

        options = ClaudeAgentOptions(
            cwd=working_dir,
            allowed_tools=effective_tools,
            permission_mode=self.permission_mode,
            model=self.model,
            mcp_servers=mcp_config if mcp_config else {},
            max_thinking_tokens=thinking_tokens if thinking_tokens >= 0 else None,
        )

        # Set reasoning effort level via native SDK field
        if self.effort:
            options.effort = self.effort

        # Pass CLI path directly to SDK instead of mutating os.environ["PATH"]
        if self.cli_path:
            options.cli_path = str(self.cli_path)

        # Set fallback model for graceful degradation on rate limits / model unavailable
        fallback = get_fallback_model_id(self.model)
        if fallback:
            options.fallback_model = fallback

        # Load CLAUDE.md from target project (but not user/local settings to avoid leakage)
        options.setting_sources = ["project"]

        # Pass custom environment variables to the SDK subprocess
        # This avoids mutating os.environ globally
        sdk_env: dict[str, str] = {}
        if self.cli_path:
            cli_dir = str(self.cli_path.parent)
            current_path = os.environ.get("PATH", "")
            if cli_dir not in current_path:
                sdk_env["PATH"] = f"{cli_dir}:{current_path}"
        if self.agent_teams_enabled:
            sdk_env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        if sdk_env:
            options.env = sdk_env

        # Route SDK stderr debug output through structured logging
        _sdk_logger = logging.getLogger("claude_sdk")
        options.stderr = lambda line: _sdk_logger.debug("sdk_stderr: %s", line.rstrip())

        # Wire SDK hooks for structured tool-use logging (and team tracking when enabled)
        options.hooks = build_hooks(
            tracker=subagent_tracker,
            screenshot_collector=screenshot_collector,
            notification_callback=notification_callback,
        )

        # Add max_turns if configured
        if self.max_turns is not None:
            options.max_turns = self.max_turns

        # Add max_budget_usd if configured
        if self.max_budget_usd is not None:
            options.max_budget_usd = self.max_budget_usd

        # SDK 0.1.35: Extended context beta (1M token context window)
        if self.extended_context_enabled:
            options.betas = ["context-1m-2025-08-07"]

        # SDK 0.1.35: File checkpointing for session rewind support
        if self.file_checkpointing_enabled:
            options.enable_file_checkpointing = True
            if options.extra_args is None:
                options.extra_args = {}
            options.extra_args["replay-user-messages"] = None

        # SDK 0.1.35: Disallowed tools restriction
        if self.disallowed_tools:
            options.disallowed_tools = self.disallowed_tools

        # Add sandbox configuration if enabled
        # Uses OS-level sandboxing (bubblewrap on Linux, sandbox-exec on macOS)
        if self.sandbox_enabled:
            options.sandbox = {
                "enabled": True,
                "autoAllowBashIfSandboxed": True,  # Auto-approve bash when sandboxed
                "excludedCommands": ["git"],  # Git needs full access for commits/push
            }

        # Build combined system prompt append instructions
        # NOTE: Must use system_prompt with preset dict, NOT append_system_prompt
        # (append_system_prompt is not a valid SDK field and was silently ignored)
        append_parts = [GLUON_SYSTEM_PROMPT]

        # Add project boundary instructions with actual working directory
        project_boundary = f"""
## PROJECT BOUNDARY - CRITICAL

**Working Directory**: `{working_dir}`

**RULES:**
1. ALL work MUST stay within this directory
2. NEVER search for projects elsewhere (no `find /home`, no `ls ~`, no directory discovery)
3. NEVER modify files outside this directory
4. If a file path is unclear, assume it's relative to the working directory above
5. Use relative paths from the project root, not absolute paths
"""
        append_parts.append(project_boundary)

        # Always append agent-browser instructions (available in Docker)
        append_parts.append(AGENT_BROWSER_SYSTEM_PROMPT)

        # Append Vercel CLI instructions when enabled with a valid token
        if self.vercel_cli_enabled and self.vercel_token:
            append_parts.append(VERCEL_SYSTEM_PROMPT)
            sdk_env["VERCEL_TOKEN"] = self.vercel_token

        # Additionally append planning prompt if force_planning is enabled
        # In Ralph Loop mode (autonomous), use PLANNING_AUTONOMOUS_PROMPT which
        # instructs the agent to proceed to execution after planning without
        # waiting for human confirmation (which would block the loop).
        if self.force_planning:
            if ralph_mode:
                # Autonomous mode: proceed to execution after planning
                append_parts.append(PLANNING_AUTONOMOUS_PROMPT)
            else:
                # Interactive mode: wait for human confirmation
                append_parts.append(PLANNING_SYSTEM_PROMPT)

        # Append Ralph status reporting instructions for Ralph Loop runs
        if ralph_mode:
            append_parts.append(RALPH_SYSTEM_PROMPT)

        # Use SDK-supported system_prompt with preset and append
        options.system_prompt = {
            "type": "preset",
            "preset": "claude_code",
            "append": "\n".join(append_parts),
        }

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
                if options.extra_args is None:
                    options.extra_args = {}
                options.extra_args["session-id"] = new_session_id
            if fork_session:
                options.fork_session = True

        return options

    # Maximum seconds to wait for agent team subagents to finish
    _TEAM_WAIT_TIMEOUT: int = 300  # 5 minutes
    # Safety cap on team synthesis rounds to prevent infinite re-prompting
    _MAX_TEAM_SYNTHESIS_ROUNDS: int = 3

    async def execute(
        self,
        working_dir: Path,
        prompt: str | MultimodalPrompt,
        resume_session_id: str | None = None,
        images: list[Path] | None = None,
        fork_session: bool = True,
        ralph_mode: bool = False,
        follow_up_queue: asyncio.Queue[str] | None = None,
        screenshot_collector: ScreenshotCollector | None = None,
        notification_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project directory.

        Keeps the SDK session alive across multiple turns to support:
        1. Agent teams — waits for SubagentStop hooks before exiting
        2. In-session follow-ups — consumes messages from *follow_up_queue*

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
            ralph_mode: If True, append RALPH_SYSTEM_PROMPT for status reporting.
            follow_up_queue: Optional queue of follow-up prompts.  When provided
                            the session stays alive and consumes follow-ups
                            in-process instead of spawning new subprocesses.

        Yields:
            AgentMessage during execution
            AgentResult as final yield
        """
        # Track agent team lifecycle when teams are enabled
        tracker = SubagentTracker() if self.agent_teams_enabled else None

        # Generate a unique session ID for new sessions to avoid control
        # channel conflicts with other Claude processes
        new_session_id = str(uuid.uuid4()) if not resume_session_id else None
        options = self._build_options(
            working_dir,
            resume_session_id,
            fork_session,
            new_session_id,
            ralph_mode,
            subagent_tracker=tracker,
            screenshot_collector=screenshot_collector,
            notification_callback=notification_callback,
        )

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

            async with ClaudeSDKClient(options=options) as client:
                # ---- MCP health check ----
                try:
                    mcp_status = await client.get_mcp_status()
                    servers = mcp_status.get("mcpServers", [])
                    if servers:
                        summary_parts = [f"{s['name']} ({s['status']})" for s in servers]
                        yield AgentMessage(
                            type="mcp_status",
                            content=f"MCP servers: {', '.join(summary_parts)}",
                            metadata={"servers": servers},
                        )
                except Exception:
                    logger.debug("MCP status check skipped", exc_info=True)

                # ---- Initial query ----
                if isinstance(prompt, MultimodalPrompt) and prompt.images:

                    async def multimodal_query():
                        content_blocks = prompt.to_content_blocks()
                        yield {
                            "type": "user",
                            "message": {"role": "user", "content": content_blocks},
                        }

                    await client.query(multimodal_query())
                else:
                    text = prompt.text if isinstance(prompt, MultimodalPrompt) else prompt
                    await client.query(text)

                # ---- Multi-turn loop ----
                synthesis_rounds = 0
                model_used: str | None = None
                model_switched = False  # Track whether model transition has fired
                while True:
                    # Process one turn (query already issued above or at bottom of loop)
                    async for msg in client.receive_response():
                        if isinstance(msg, SystemMessage):
                            if msg.subtype == "init" and isinstance(msg.data, dict):
                                session_from_data = msg.data.get("session_id")
                                if session_from_data:
                                    claude_session_id = session_from_data
                            yield AgentMessage(
                                type="system",
                                content=msg.subtype,
                                metadata={"session_id": claude_session_id},
                            )

                        elif isinstance(msg, AssistantMessage):
                            total_turns += 1
                            # Track the actual model used (useful for fallback visibility)
                            if msg.model:
                                model_used = msg.model
                            # Handle API-level errors reported on the message
                            if msg.error:
                                yield AgentMessage(
                                    type="error",
                                    content=f"API error: {msg.error}",
                                    metadata={"error_type": msg.error, "model": msg.model},
                                )
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    yield AgentMessage(type="text", content=block.text)
                                elif isinstance(block, ThinkingBlock):
                                    yield AgentMessage(
                                        type="thinking",
                                        content=block.thinking,
                                    )
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
                                    # Detect planning completion for model transition
                                    if self.model_transition and not model_switched and block.name == "ExitPlanMode":
                                        transition_map = {
                                            "opus-to-sonnet": ModelTier.SONNET,
                                            "opus-to-haiku": ModelTier.HAIKU,
                                        }
                                        target_tier = transition_map.get(self.model_transition)
                                        if target_tier:
                                            target_id = get_model_id(target_tier)
                                            await client.set_model(target_id)
                                            model_switched = True
                                            yield AgentMessage(
                                                type="system",
                                                content=f"Model switched to {target_tier.value} for implementation",
                                                metadata={
                                                    "model_transition": self.model_transition,
                                                    "new_model": target_id,
                                                },
                                            )
                                elif isinstance(block, ToolResultBlock):
                                    yield AgentMessage(
                                        type="tool_result",
                                        content=str(block.content) if block.content else "",
                                        metadata={
                                            "tool_use_id": block.tool_use_id,
                                            "is_error": block.is_error,
                                        },
                                    )

                        elif isinstance(msg, ResultMessage):
                            total_cost_usd = msg.total_cost_usd or 0.0
                            total_turns = msg.num_turns or total_turns
                            usage = msg.usage or {}
                            input_tokens = usage.get("input_tokens")
                            output_tokens = usage.get("output_tokens")
                            if msg.session_id:
                                claude_session_id = msg.session_id
                            yield AgentMessage(
                                type="result",
                                content=msg.result or "Execution complete",
                                metadata={
                                    "cost": total_cost_usd,
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "session_id": claude_session_id,
                                    "duration_ms": msg.duration_ms,
                                    "duration_api_ms": msg.duration_api_ms,
                                    "is_error": msg.is_error,
                                    "model_used": model_used,
                                },
                            )

                    # ---- Turn complete — decide whether to continue ----

                    # Check 1: Are team subagents still running?
                    if tracker and tracker.active_count > 0:
                        if synthesis_rounds >= self._MAX_TEAM_SYNTHESIS_ROUNDS:
                            logger.warning(
                                "Max synthesis rounds (%d) reached, exiting",
                                self._MAX_TEAM_SYNTHESIS_ROUNDS,
                            )
                            break

                        logger.info(
                            "Waiting for %d active subagent(s) to finish",
                            tracker.active_count,
                        )
                        try:
                            await asyncio.wait_for(
                                tracker.all_done.wait(),
                                timeout=self._TEAM_WAIT_TIMEOUT,
                            )
                        except TimeoutError:
                            logger.warning(
                                "Subagent wait timed out after %ds with %d still active",
                                self._TEAM_WAIT_TIMEOUT,
                                tracker.active_count,
                            )

                        # Reset tracker — clears stale counts from nested/orphaned subagents
                        await tracker.reset()
                        synthesis_rounds += 1

                        # Nudge the lead agent to synthesize team results
                        await client.query(
                            "Your agent team teammates have completed their work. "
                            "Synthesize their results and continue with the next phase."
                        )
                        yield AgentMessage(
                            type="system",
                            content="team_synthesis",
                            metadata={"synthesis_round": synthesis_rounds},
                        )
                        continue

                    # Check 2: Any follow-up messages queued?
                    if follow_up_queue is not None:
                        try:
                            followup = follow_up_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        else:
                            logger.info("Processing in-session follow-up")
                            yield AgentMessage(
                                type="system",
                                content="follow_up",
                                metadata={"prompt": followup},
                            )
                            await client.query(followup)
                            continue

                    # Nothing more to do — exit the multi-turn loop
                    break

        except CLINotFoundError as e:
            success = False
            error_msg = str(e)
            yield AgentMessage(
                type="error",
                content=f"CLI not found: {error_msg}",
                metadata={"exception": "CLINotFoundError", "recoverable": False},
            )
        except CLIConnectionError as e:
            success = False
            error_msg = str(e)
            yield AgentMessage(
                type="error",
                content=f"Connection error: {error_msg}",
                metadata={"exception": "CLIConnectionError", "recoverable": True},
            )
        except ProcessError as e:
            success = False
            error_msg = str(e)
            # ProcessError includes exit_code and stderr — classify further
            classified_error = _classify_api_error(e)
            if isinstance(classified_error, ContextOverflowError):
                yield AgentMessage(
                    type="error",
                    content=f"Context overflow: {error_msg}",
                    metadata={
                        "exception": "ContextOverflowError",
                        "recoverable": True,
                        "session_id": claude_session_id,
                    },
                )
            elif isinstance(classified_error, RateLimitError):
                yield AgentMessage(
                    type="error",
                    content=f"Rate limited: {error_msg}",
                    metadata={"exception": "RateLimitError", "recoverable": True},
                )
            elif isinstance(classified_error, ModelUnavailableError):
                yield AgentMessage(
                    type="error",
                    content=f"Model unavailable: {error_msg}",
                    metadata={"exception": "ModelUnavailableError", "recoverable": False},
                )
            elif isinstance(classified_error, AuthenticationError):
                yield AgentMessage(
                    type="error",
                    content=f"Authentication error: {error_msg}",
                    metadata={"exception": "AuthenticationError", "recoverable": False},
                )
            else:
                yield AgentMessage(
                    type="error",
                    content=f"Process error: {error_msg}",
                    metadata={
                        "exception": "ProcessError",
                        "exit_code": e.exit_code,
                    },
                )
        except ClaudeSDKError as e:
            success = False
            error_msg = str(e)
            yield AgentMessage(
                type="error",
                content=f"SDK error: {error_msg}",
                metadata={"exception": type(e).__name__},
            )
        except Exception as e:
            success = False
            error_msg = str(e)

            # Classify non-SDK errors (e.g., wrapped API errors)
            classified_error = _classify_api_error(e)
            recoverable_types = (ContextOverflowError, RateLimitError, ModelUnavailableError, AuthenticationError)
            if isinstance(classified_error, recoverable_types):
                yield AgentMessage(
                    type="error",
                    content=f"{type(classified_error).__name__}: {error_msg}",
                    metadata={
                        "exception": type(classified_error).__name__,
                        "recoverable": isinstance(classified_error, (ContextOverflowError, RateLimitError)),
                        "session_id": claude_session_id,
                    },
                )
            else:
                yield AgentMessage(
                    type="error",
                    content=f"Error: {error_msg}",
                    metadata={"exception": type(e).__name__},
                )

        # Yield final result (model_used tracks actual model from response, falls back to configured)
        yield AgentResult(
            claude_session_id=claude_session_id,
            total_cost_usd=total_cost_usd,
            total_turns=total_turns,
            success=success,
            error=error_msg,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_used=model_used or self.model,
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
