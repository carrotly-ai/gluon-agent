"""Main orchestrator for ralph loop execution.

RalphManager coordinates the autonomous loop lifecycle:
1. Execute Claude iterations until completion
2. Use CircuitBreaker to detect and prevent runaway loops
3. Use CompletionDetector to identify task completion
4. Use RateLimiter to enforce API and cost limits
5. Persist iteration history for observability
"""

import asyncio
import json
import logging
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from gluon.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from gluon.completion_detector import CompletionDetector, CompletionDetectorConfig
from gluon.gate import run_gate
from gluon.models import CircuitState, ExecutionRun, RalphLoopIteration, RunStatus, SupervisionConfig
from gluon.rate_limiter import RateLimiter, RateLimiterConfig

if TYPE_CHECKING:
    from gluon.agent import GluonAgent
    from gluon.store import GluonStore

logger = logging.getLogger(__name__)

# Default delay between loop iterations
DEFAULT_LOOP_DELAY_SECONDS = 5

# TODO file names to check for completion
TODO_FILE_NAMES = ["@fix_plan.md", "TODO.md", "todo.md", "TASKS.md"]

# Progress file for fresh context sessions
PROGRESS_FILE_NAME = ".gluon-progress.md"


class RalphManager:
    """Orchestrates ralph loop lifecycle.

    Coordinates execution of multiple Claude iterations with safety
    controls and completion detection.
    """

    def __init__(
        self,
        run: ExecutionRun,
        agent: "GluonAgent",
        store: "GluonStore",
        working_dir: Path,
        *,
        log_dir: Path | None = None,
        loop_delay_seconds: int = DEFAULT_LOOP_DELAY_SECONDS,
    ):
        self.run = run
        self.agent = agent
        self.store = store
        self.working_dir = working_dir
        self.log_dir = log_dir
        self.loop_delay_seconds = loop_delay_seconds

        # Initialize components with run's config
        cb_config = CircuitBreakerConfig()
        self.circuit_breaker = CircuitBreaker(cb_config)

        cd_config = CompletionDetectorConfig()
        self.completion_detector = CompletionDetector(cd_config)

        rl_config = RateLimiterConfig(
            max_calls_per_hour=run.max_calls_per_hour,
            max_cost_usd=run.max_cost_usd,
        )
        self.rate_limiter = RateLimiter(rl_config)
        # Restore cumulative cost from previous iterations (for resume continuity)
        if run.cost_usd is not None:
            self.rate_limiter.total_cost_usd = run.cost_usd

        # Restore circuit breaker state from run if resuming
        if run.circuit_state != CircuitState.CLOSED:
            self.circuit_breaker.state = run.circuit_state
            self.circuit_breaker.consecutive_no_progress = run.consecutive_no_progress
            self.circuit_breaker.consecutive_same_error = run.consecutive_same_error
            self.circuit_breaker.last_progress_loop = run.last_progress_loop
            # Restore half_open_iterations for correct HALF_OPEN patience window tracking
            self.circuit_breaker.half_open_iterations = run.half_open_iterations

        # Tracking for completion detection
        self.consecutive_done_signals = run.completion_signals
        self.consecutive_test_only = run.test_only_loops

        # Cache for iteration output (used by completion detection)
        self._last_iteration_output: str = ""

        # Loop-engineering I1: when a gated run's verify_cmd fails after the agent
        # self-declared done, stash the gate output here so the next iteration's
        # prompt tells the agent to fix it (instead of re-declaring done).
        self._last_gate_failure: str | None = None

        # Planning phase tracking for force_planning + ralph_mode
        # When True, planning is complete and agent should execute, not re-plan
        self.planning_complete: bool = False

    def _disable_supervision(self, reason: str) -> None:
        """Disable supervision when Ralph Loop completes.

        This prevents the supervisor from auto-resuming a completed Ralph Loop.
        """
        if self.run.supervision_config is None:
            self.run.supervision_config = SupervisionConfig()
        self.run.supervision_config.enabled = False
        self.run.supervision_disabled_reason = f"Ralph Loop completed: {reason}"
        logger.info(f"Disabled supervision for run {self.run.id[:8]}: {reason}")

    def _apply_objective_gate(self, should_exit: bool, exit_reason: str) -> tuple[bool, str]:
        """Loop-engineering I1: for *gated* runs (``verify_cmd`` set), the objective
        gate is the authority. Self-report (``should_exit``) only TRIGGERS the gate;
        actually exiting requires the gate to pass (exit 0). When it fails, demote
        the self-report to a hint, stash the output for the next prompt, and keep
        looping (bounded by max_loops). Gateless runs are returned unchanged.
        """
        if not (should_exit and self.run.verify_cmd):
            return should_exit, exit_reason
        result = run_gate(self.run.verify_cmd, self.working_dir)
        if result.passed:
            self._last_gate_failure = None
            return True, f"{exit_reason}; verify_cmd passed"
        logger.info(
            "verify_cmd did not pass for run %s — self-report claimed done but the "
            "objective gate is red; demoting EXIT_SIGNAL to a hint and continuing",
            self.run.id[:8],
        )
        self._last_gate_failure = result.output
        return False, exit_reason

    async def execute_loop(self) -> ExecutionRun:
        """Execute the ralph loop until completion or circuit break.

        Returns:
            Updated ExecutionRun with final status
        """
        logger.info(f"Starting ralph loop for run {self.run.id[:8]}, max {self.run.max_loops} iterations")

        while self.run.loop_count < self.run.max_loops:
            # Check for stop request by polling database for status change
            refreshed_run = self.store.get_run(self.run.id)
            if refreshed_run and refreshed_run.status in (RunStatus.REVIEW, RunStatus.CANCELLED):
                logger.info(f"Stop requested for run {self.run.id[:8]}, halting loop")
                self.run.status = refreshed_run.status
                self.run.completion_reason = refreshed_run.completion_reason or "User requested stop"
                self._sync_run_state()
                break

            # Pre-execution checks
            if not self.circuit_breaker.can_execute():
                reason = self.circuit_breaker.get_open_reason()
                logger.warning(f"Circuit breaker OPEN: {reason}")
                self.run.status = RunStatus.FAILED
                self.run.error_message = f"Circuit breaker OPEN: {reason}"
                self._disable_supervision(f"Circuit breaker OPEN: {reason}")
                self._sync_run_state()
                break

            can_call, limit_reason = self.rate_limiter.can_make_call()
            if not can_call:
                if "Cost cap" in limit_reason:
                    # Cost cap is terminal
                    logger.warning(f"Cost cap reached: {limit_reason}")
                    self.run.status = RunStatus.REVIEW
                    self.run.completion_reason = f"Cost cap reached: {limit_reason}"
                    self._disable_supervision(f"Cost cap reached: {limit_reason}")
                    self._sync_run_state()
                    break
                else:
                    # Rate limit - wait for reset
                    wait_seconds = self.rate_limiter.seconds_until_reset()
                    logger.info(f"Rate limit reached, waiting {wait_seconds}s for reset")
                    await asyncio.sleep(wait_seconds + 1)
                    continue

            # Execute single iteration
            iteration = await self._execute_iteration()

            # Update completion tracking
            if iteration.has_completion_signal:
                self.consecutive_done_signals += 1
                logger.debug(f"Completion signal detected (consecutive={self.consecutive_done_signals})")
            else:
                self.consecutive_done_signals = 0

            if iteration.is_test_only:
                self.consecutive_test_only += 1
                logger.debug(f"Test-only iteration (consecutive={self.consecutive_test_only})")
            else:
                self.consecutive_test_only = 0

            # Check completion
            todo_content = self._read_todo_file()
            signals = self.completion_detector.analyze(
                self._get_iteration_output(),
                todo_content,
            )

            # Log completion analysis details
            if signals.matched_patterns:
                logger.info(
                    f"Completion analysis: confidence={signals.confidence:.0f}, patterns={signals.matched_patterns}"
                )
            else:
                logger.debug(f"Completion analysis: confidence={signals.confidence:.0f}, no patterns matched")

            should_exit, exit_reason = self.completion_detector.should_exit(
                signals,
                self.consecutive_done_signals,
                self.consecutive_test_only,
            )
            # Loop-engineering I1: for gated runs the objective gate overrides
            # self-report. No-op for gateless runs (today's behavior unchanged).
            should_exit, exit_reason = self._apply_objective_gate(should_exit, exit_reason)

            if should_exit:
                logger.info(
                    f"Completion detected: {exit_reason} "
                    f"(confidence={signals.confidence:.0f}, done_signals={self.consecutive_done_signals}, "
                    f"test_only={self.consecutive_test_only})"
                )
                self.run.status = RunStatus.REVIEW
                self.run.completion_reason = exit_reason
                self._disable_supervision(exit_reason)
                self._sync_run_state()
                break
            else:
                logger.debug(
                    f"Continuing loop: confidence={signals.confidence:.0f}, "
                    f"done_signals={self.consecutive_done_signals}, test_only={self.consecutive_test_only}"
                )

            # Brief pause between iterations
            if self.loop_delay_seconds > 0:
                await asyncio.sleep(self.loop_delay_seconds)

        # Final state sync if we hit max loops
        if self.run.loop_count >= self.run.max_loops:
            logger.warning(f"Max loops ({self.run.max_loops}) reached")
            self.run.status = RunStatus.REVIEW
            self.run.completion_reason = f"Max loops ({self.run.max_loops}) reached"
            self._disable_supervision(f"Max loops ({self.run.max_loops}) reached")
            self._sync_run_state()

        return self.run

    async def _execute_iteration(self) -> RalphLoopIteration:
        """Execute a single loop iteration.

        Returns:
            RalphLoopIteration with execution results
        """
        self.run.loop_count += 1
        loop_num = self.run.loop_count

        logger.info(f"Starting iteration {loop_num}/{self.run.max_loops}")

        iteration = RalphLoopIteration(
            run_id=self.run.id,
            loop_number=loop_num,
            started_at=datetime.now(UTC),
        )

        # Build prompt with loop context
        loop_prompt = self._build_loop_prompt(loop_num)

        # Execute Claude
        try:
            result = await self._run_claude(loop_prompt)

            iteration.ended_at = datetime.now(UTC)
            iteration.claude_session_id = result.get("session_id")
            iteration.cost_usd = result.get("cost_usd", 0.0)
            iteration.tokens_used = result.get("tokens", 0)
            iteration.output_length = len(result.get("output", ""))
            iteration.has_errors = result.get("has_errors", False)
            iteration.error_summary = result.get("error_summary")

            # Cache output for completion detection
            self._last_iteration_output = result.get("output", "")

            # Store session ID in run for resume
            if iteration.claude_session_id:
                self.run.claude_session_id = iteration.claude_session_id

        except Exception as e:
            logger.error(f"Iteration {loop_num} failed: {e}", exc_info=True)
            iteration.ended_at = datetime.now(UTC)
            iteration.has_errors = True
            iteration.error_summary = str(e)[:200]
            self._last_iteration_output = ""

        # Analyze git changes
        iteration.files_changed = self._count_git_changes()
        iteration.progress_detected = iteration.files_changed > 0

        # Analyze output for completion signals
        output_text = self._get_iteration_output()
        signals = self.completion_detector.analyze(output_text, self._read_todo_file())
        iteration.has_completion_signal = signals.has_done_keyword or signals.has_complete_keyword
        iteration.is_test_only = signals.is_test_only
        iteration.confidence_score = signals.confidence

        # Update circuit breaker
        old_state = self.circuit_breaker.state
        new_state = self.circuit_breaker.record_iteration(
            loop_number=loop_num,
            files_changed=iteration.files_changed,
            has_errors=iteration.has_errors,
            error_summary=iteration.error_summary,
            output_length=iteration.output_length,
        )

        # Log circuit breaker state transitions
        if new_state != old_state:
            logger.warning(
                f"Circuit breaker state changed: {old_state.value} → {new_state.value} "
                f"(no_progress={self.circuit_breaker.consecutive_no_progress}, "
                f"same_error={self.circuit_breaker.consecutive_same_error})"
            )

        # Record rate limit
        self.rate_limiter.record_call(iteration.cost_usd)

        # Persist iteration
        self.store.create_ralph_iteration(iteration)

        # Sync run state to database
        self._sync_run_state()

        # Write progress file for next iteration (fresh context sessions)
        self._write_progress_file(iteration, output_text)

        # Log iteration summary
        logger.info(
            f"Iteration {loop_num} complete: "
            f"files={iteration.files_changed}, "
            f"errors={iteration.has_errors}, "
            f"confidence={iteration.confidence_score:.0f}, "
            f"cost=${iteration.cost_usd:.4f}, "
            f"output_len={iteration.output_length}, "
            f"circuit={new_state.value}"
        )

        return iteration

    async def _run_claude(self, prompt: str) -> dict:
        """Execute Claude with the given prompt.

        Returns dict with: session_id, cost_usd, tokens, output, has_errors, error_summary
        """
        result = {
            "session_id": None,
            "cost_usd": 0.0,
            "tokens": 0,
            "output": "",
            "has_errors": False,
            "error_summary": None,
        }

        output_parts = []

        # Path to messages log file for WebSocket streaming
        messages_path = self.log_dir / "messages.jsonl" if self.log_dir else None

        try:
            # Log the loop prompt as a user message for visibility in web-ui
            if messages_path:
                prompt_msg = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "type": "user",
                    "content": prompt,
                    "loop_number": self.run.loop_count,
                }
                with open(messages_path, "a") as f:
                    f.write(json.dumps(prompt_msg) + "\n")

            # IMPORTANT: Use fresh sessions for each Ralph Loop iteration.
            # This prevents "context rot" where LLM performance degrades as
            # the context window fills up. Context is passed via progress file
            # instead of session resumption.
            # NOTE: Manual user resumes (CLI/web-ui) still use legacy resume
            # behavior - this change only affects Ralph Loop auto-iterations.
            async for message in self.agent.execute(
                prompt=prompt,
                working_dir=self.working_dir,
                resume_session_id=None,  # Always fresh for Ralph auto-iterations
                ralph_mode=True,  # Enable RALPH_STATUS instructions in system prompt
            ):
                # Capture session ID from init message
                if hasattr(message, "metadata") and message.metadata:
                    if "session_id" in message.metadata:
                        result["session_id"] = message.metadata["session_id"]

                # Write message to log file for WebSocket streaming (like regular tasks)
                msg_type = getattr(message, "type", None)
                if messages_path and msg_type:
                    msg_dict = {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "type": msg_type,
                        "content": getattr(message, "content", ""),
                        "metadata": getattr(message, "metadata", None),
                        "loop_number": self.run.loop_count,
                    }
                    with open(messages_path, "a") as f:
                        f.write(json.dumps(msg_dict) + "\n")

                # Capture output based on message type for more complete completion detection
                # Use isinstance to narrow type for proper attribute access
                from gluon.agent import AgentMessage as AgentMsg

                if isinstance(message, AgentMsg):
                    if msg_type == "tool_use":
                        # Include tool usage in output for better completion detection
                        tool_name = message.metadata.get("tool", "unknown") if message.metadata else "unknown"
                        output_parts.append(f"[Tool: {tool_name}]")
                    elif msg_type == "text":
                        output_parts.append(str(message.content))
                    elif message.content:
                        # Fallback for other message types with content
                        output_parts.append(str(message.content))

                    # Check for errors
                    if hasattr(message, "is_error") and message.is_error:
                        result["has_errors"] = True
                        result["error_summary"] = str(message.content)[:200]

                    # Capture AgentResult when yielded (contains cost/token info)
                    if msg_type == "result" and message.metadata:
                        result["cost_usd"] = message.metadata.get("cost", 0.0)
                        input_tokens = message.metadata.get("input_tokens", 0) or 0
                        output_tokens = message.metadata.get("output_tokens", 0) or 0
                        result["tokens"] = input_tokens + output_tokens
                        result["session_id"] = message.metadata.get("session_id")

                # Also check for AgentResult object directly (from agent.py)
                from gluon.agent import AgentResult

                if isinstance(message, AgentResult):
                    result["cost_usd"] = message.total_cost_usd or 0.0
                    input_tokens = message.input_tokens or 0
                    output_tokens = message.output_tokens or 0
                    result["tokens"] = input_tokens + output_tokens
                    result["session_id"] = message.claude_session_id

            result["output"] = "\n".join(output_parts)

        except Exception as e:
            result["has_errors"] = True
            result["error_summary"] = str(e)[:200]
            logger.error(f"Claude execution error: {e}")

        return result

    def _build_loop_prompt(self, loop_number: int) -> str:
        """Build prompt with loop context.

        Prepends loop context to original prompt to help Claude
        understand this is part of an autonomous loop.

        For iterations 2+, uses the RECOMMENDATION from the previous iteration
        as the primary focus, with the original prompt available as context.

        Note: Project boundary instructions are injected via agent.py's
        append_system_prompt, so they apply to ALL runs (not just Ralph Loops).
        """
        context_parts = [f"[Loop {loop_number}/{self.run.max_loops}]"]

        # Loop-engineering I1: if the objective gate (verify_cmd) failed last time the
        # agent declared done, surface it so this iteration FIXES it before declaring done.
        if self._last_gate_failure:
            context_parts.append(
                f"[OBJECTIVE GATE FAILED] Your verify command `{self.run.verify_cmd}` did NOT "
                "pass. Make it pass (exit 0) before declaring done — EXIT_SIGNAL must stay false "
                f"until it does. Last gate output:\n{self._last_gate_failure}"
            )

        # Add remaining TODO count if available - with explicit EXIT_SIGNAL guidance
        todo_content = self._read_todo_file()
        if todo_content:
            total = todo_content.count("- [ ]") + todo_content.count("- [x]")
            remaining = todo_content.count("- [ ]")
            if remaining > 0:
                context_parts.append(f"[{remaining}/{total} tasks remaining - EXIT_SIGNAL must be false]")
            elif total > 0:
                context_parts.append(f"[0/{total} tasks remaining - all done]")

        # Add circuit state if not normal
        if self.circuit_breaker.state != CircuitState.CLOSED:
            context_parts.append(f"[Circuit: {self.circuit_breaker.state.value}]")

        context = " ".join(context_parts)

        # Phase-aware execution directive for loops 2+
        # When planning is complete, inject strong directive to execute (not re-plan)
        execution_directive = ""
        if loop_number > 1 and self._check_planning_complete():
            execution_directive = """
---
## EXECUTION MODE ACTIVE

Planning is COMPLETE. Your TODO file exists.
Do NOT create new plans or re-analyze.
EXECUTE the next unchecked task in your TODO file NOW.
---
"""

        # Extract RECOMMENDATION from previous iteration for loops 2+
        recommendation_section = ""
        if loop_number > 1:
            recommendation = self._read_recommendation_from_progress()
            if recommendation:
                # Detect if recommendation is about execution
                exec_keywords = ["proceed", "execute", "implement", "continue", "work on", "start", "begin", "next"]
                is_exec_recommendation = any(kw in recommendation.lower() for kw in exec_keywords)

                if is_exec_recommendation:
                    recommendation_section = f"""
---
## MANDATORY DIRECTIVE

**{recommendation}**

This is NOT a suggestion. EXECUTE this now. Do not re-plan.
---
"""
                else:
                    recommendation_section = f"""
---
## Primary Focus (from previous iteration)

{recommendation}

This is your immediate priority for this iteration.
---
"""

        # Inject previous iteration summary for fresh context sessions
        previous_summary = ""
        if loop_number > 1:
            prev_content = self._read_progress_file()
            if prev_content:
                previous_summary = f"""
---
## Previous Iteration Summary

{prev_content}

**Note:** This is a fresh session. The above describes what was done previously.
---
"""

        # Add messages log filepath for context (loops 2+)
        messages_context = ""
        if self.log_dir and loop_number > 1:
            messages_path = self.log_dir / "messages.jsonl"
            if messages_path.exists():
                messages_context = f"""
---
## Previous Messages Log

The full conversation history from previous loops is available at: `{messages_path}`
You can read this file to review detailed tool calls, outputs, and decisions from earlier iterations.
---
"""

        # NOTE: RALPH_STATUS instructions have been moved to system prompt
        # via RALPH_SYSTEM_PROMPT in models.py (injected when ralph_mode=True)

        # Build prompt with execution directive FIRST for highest visibility
        prompt_parts = [
            context,
            execution_directive,  # Phase-aware directive (empty if not applicable)
            recommendation_section,
            messages_context,
            previous_summary,
            "\n\n",
            self.run.prompt,
        ]
        return "".join(prompt_parts)

    def _read_todo_file(self) -> str | None:
        """Read @fix_plan.md or TODO.md if present."""
        for filename in TODO_FILE_NAMES:
            path = self.working_dir / filename
            if path.exists():
                try:
                    return path.read_text()
                except Exception:
                    pass
        return None

    def _check_planning_complete(self) -> bool:
        """Check if planning phase is complete (TODO file with unchecked tasks exists).

        Once detected, caches the result since planning doesn't "undo" itself.

        Returns:
            True if planning is complete (TODO file exists with tasks)
        """
        if self.planning_complete:
            return True

        todo_content = self._read_todo_file()
        if todo_content and "- [ ]" in todo_content:
            self.planning_complete = True
            logger.info("Planning phase complete - TODO file with tasks detected")

        return self.planning_complete

    def _get_iteration_output(self) -> str:
        """Get output text for the current iteration.

        Returns the cached output from the most recent _run_claude() call.
        This is used for completion detection to analyze Claude's response.
        """
        return self._last_iteration_output

    def _count_git_changes(self) -> int:
        """Count uncommitted file changes via git."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                lines = [line for line in result.stdout.strip().split("\n") if line]
                return len(lines)
        except Exception as e:
            logger.debug(f"Git status failed: {e}")
        return 0

    def _sync_run_state(self) -> None:
        """Sync run state to database.

        Updates circuit breaker and completion tracking fields.
        """
        self.run.circuit_state = self.circuit_breaker.state
        self.run.consecutive_no_progress = self.circuit_breaker.consecutive_no_progress
        self.run.consecutive_same_error = self.circuit_breaker.consecutive_same_error
        self.run.last_progress_loop = self.circuit_breaker.last_progress_loop
        self.run.last_error_hash = self.circuit_breaker.last_error_hash
        self.run.half_open_iterations = self.circuit_breaker.half_open_iterations

        self.run.completion_signals = self.consecutive_done_signals
        self.run.test_only_loops = self.consecutive_test_only

        self.run.calls_this_hour = self.rate_limiter.calls_this_hour
        self.run.hour_start = self.rate_limiter.hour_start

        # Update cost tracking
        if self.run.cost_usd is None:
            self.run.cost_usd = 0.0
        self.run.cost_usd = self.rate_limiter.total_cost_usd

        self.store.update_run(self.run)

    # -------------------------------------------------------------------------
    # Progress file methods for fresh context sessions
    # -------------------------------------------------------------------------

    def _write_progress_file(self, iteration: RalphLoopIteration, output: str) -> None:
        """Write iteration summary to progress file in project directory.

        This enables fresh context sessions by persisting key learnings
        from each iteration to a markdown file that the next iteration
        can read. Overwrites previous content (last iteration only).
        """
        progress_path = self.working_dir / PROGRESS_FILE_NAME

        # Extract RALPH_STATUS block from output
        ralph_status = self._extract_ralph_status(output)

        # Extract RECOMMENDATION for easy extraction by next iteration
        recommendation = self._extract_recommendation(output)

        # Extract key output summary
        key_output = self._extract_key_output(output)

        # Build markdown summary - RECOMMENDATION at top for easy extraction
        summary = f"""## Iteration {iteration.loop_number}

**Recommendation**: {recommendation or "None provided"}
**Started**: {iteration.started_at.isoformat() if iteration.started_at else "N/A"}
**Files Changed**: {iteration.files_changed}
**Errors**: {iteration.has_errors}
**Confidence**: {iteration.confidence_score:.0f}%

### Status
{ralph_status or "No RALPH_STATUS block found"}

### Key Output
{key_output or "No significant output captured"}

---
"""
        try:
            progress_path.write_text(summary)
            logger.debug(f"Wrote progress file: {progress_path}")
        except Exception as e:
            logger.warning(f"Failed to write progress file: {e}")

    def _read_progress_file(self) -> str | None:
        """Read previous iteration summary from progress file.

        Returns:
            Content of progress file, or None if not found.
        """
        progress_path = self.working_dir / PROGRESS_FILE_NAME
        if progress_path.exists():
            try:
                return progress_path.read_text()
            except Exception as e:
                logger.debug(f"Failed to read progress file: {e}")
        return None

    def _read_recommendation_from_progress(self) -> str | None:
        """Extract RECOMMENDATION from progress file.

        Looks for the **Recommendation**: line in the progress file.

        Returns:
            The recommendation value, or None if not found or empty.
        """
        content = self._read_progress_file()
        if not content:
            return None
        # Match **Recommendation**: <text> format
        pattern = r"\*\*Recommendation\*\*:\s*(.+?)(?:\n|$)"
        match = re.search(pattern, content)
        if match:
            rec = match.group(1).strip()
            # Filter out placeholder values
            if rec and rec.lower() != "none provided":
                return rec
        return None

    def _extract_ralph_status(self, output: str) -> str | None:
        """Extract RALPH_STATUS block from Claude output.

        Returns:
            The RALPH_STATUS block content, or None if not found.
        """
        # Match the RALPH_STATUS block
        pattern = r"---RALPH_STATUS---(.*?)---END_RALPH_STATUS---"
        match = re.search(pattern, output, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _extract_recommendation(self, output: str) -> str | None:
        """Extract RECOMMENDATION value from RALPH_STATUS block.

        Returns:
            The recommendation string, or None if not found/empty.
        """
        ralph_status = self._extract_ralph_status(output)
        if not ralph_status:
            return None
        # Parse RECOMMENDATION line from the status block
        pattern = r"RECOMMENDATION:\s*(.+?)(?:\n|$)"
        match = re.search(pattern, ralph_status)
        if match:
            rec = match.group(1).strip()
            return rec if rec else None
        return None

    def _extract_key_output(self, output: str, max_chars: int = 2000) -> str:
        """Extract key information from iteration output.

        Focuses on tool calls, file modifications, and decisions.
        Truncates to max_chars to keep context concise.

        Args:
            output: Full output from Claude
            max_chars: Maximum characters to include

        Returns:
            Summarized key output
        """
        if not output:
            return ""

        lines = []

        # Extract tool usage mentions
        tool_pattern = r"\[Tool: (\w+)\]"
        tools_used = re.findall(tool_pattern, output)
        if tools_used:
            # Count unique tools
            tool_counts: dict[str, int] = {}
            for tool in tools_used:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            tool_summary = ", ".join(f"{t}({c})" for t, c in tool_counts.items())
            lines.append(f"- Tools used: {tool_summary}")

        # Extract file modification patterns (common in Claude output)
        file_patterns = [
            r"(?:Modified|Created|Edited|Updated|Deleted)\s+[`'\"]?([^\s`'\"]+\.\w+)[`'\"]?",
            r"(?:wrote to|edited|created)\s+[`'\"]?([^\s`'\"]+\.\w+)[`'\"]?",
        ]
        files_mentioned: set[str] = set()
        for pattern in file_patterns:
            matches = re.findall(pattern, output, re.IGNORECASE)
            files_mentioned.update(matches)
        if files_mentioned:
            lines.append(f"- Files mentioned: {', '.join(sorted(files_mentioned)[:10])}")

        # Extract test results if any
        test_patterns = [
            r"(\d+)\s+(?:tests?\s+)?passed",
            r"(\d+)\s+(?:tests?\s+)?failed",
            r"pytest.*?(\d+\s+passed|\d+\s+failed)",
        ]
        for pattern in test_patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                lines.append(f"- Test results: {match.group(0)}")
                break

        # Extract error summaries if any
        error_pattern = r"(?:Error|Exception|Failed):\s*(.+?)(?:\n|$)"
        errors = re.findall(error_pattern, output, re.IGNORECASE)
        if errors:
            lines.append(f"- Errors: {errors[0][:100]}")

        # If we found structured info, return it
        if lines:
            return "\n".join(lines)

        # Fallback: return truncated raw output (skip tool markers)
        clean_output = re.sub(r"\[Tool: \w+\]\n?", "", output)
        if len(clean_output) > max_chars:
            return clean_output[:max_chars] + "..."
        return clean_output
