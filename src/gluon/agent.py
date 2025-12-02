"""Claude Agent SDK wrapper for Gluon."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

# Default tools available to Claude Code agents
DEFAULT_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task", "TodoWrite"]

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


class GluonAgent:
    """Wrapper around Claude Agent SDK for managing code agent sessions."""

    def __init__(
        self,
        model: str = "sonnet",
        allowed_tools: list[str] | None = None,
        permission_mode: str = "acceptEdits",
        cli_path: Path | str | None = None,
    ):
        self.model = model
        self.allowed_tools = allowed_tools or DEFAULT_TOOLS
        self.permission_mode = permission_mode
        # Auto-detect CLI path if not provided
        self.cli_path = Path(cli_path) if cli_path else find_claude_cli()

    def _build_options(
        self,
        working_dir: Path,
        resume_session_id: str | None = None,
    ) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for a session."""
        options = ClaudeAgentOptions(
            cwd=working_dir,
            allowed_tools=self.allowed_tools,
            permission_mode=self.permission_mode,
            model=self.model,
        )

        # Add resume option if we have a previous session
        if resume_session_id:
            options.resume = resume_session_id

        return options

    async def execute(
        self,
        working_dir: Path,
        prompt: str,
        resume_session_id: str | None = None,
    ) -> AsyncIterator[AgentMessage | AgentResult]:
        """
        Execute a prompt against a project directory.

        Yields AgentMessage objects during execution, then yields
        a final AgentResult with session metadata.

        Args:
            working_dir: Path to project directory
            prompt: User prompt to execute
            resume_session_id: Optional Claude session ID to resume

        Yields:
            AgentMessage during execution
            AgentResult as final yield
        """
        options = self._build_options(working_dir, resume_session_id)

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
                await client.query(prompt)

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
                        # Extract token usage from ResultMessage
                        input_tokens = getattr(msg, "input_tokens", None)
                        output_tokens = getattr(msg, "output_tokens", None)
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
        prompt: str,
        resume_session_id: str | None = None,
    ) -> AgentResult:
        """
        Execute a prompt and return only the final result.

        This is a simpler interface that doesn't stream messages.
        """
        result: AgentResult | None = None

        async for item in self.execute(working_dir, prompt, resume_session_id):
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
