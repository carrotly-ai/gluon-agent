"""Intelligent completion detection for ralph loops.

Analyzes Claude output and TODO files to detect when a task is complete.
Uses multiple signals with confidence scoring to avoid false positives.

Exit signals:
1. RALPH_STATUS block with EXIT_SIGNAL=true (highest priority)
2. All TODO items marked complete in @fix_plan.md or TODO.md
3. Multiple consecutive "done" signals from Claude
4. Test saturation (only running tests, no implementation)
5. High completion confidence score
"""

import re
from dataclasses import dataclass, field
from enum import Enum


class RalphStatus(str, Enum):
    """Status values for RALPH_STATUS block."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


class TestsStatus(str, Enum):
    """Test status values for RALPH_STATUS block."""

    PASSING = "PASSING"
    FAILING = "FAILING"
    NOT_RUN = "NOT_RUN"


class WorkType(str, Enum):
    """Work type values for RALPH_STATUS block."""

    IMPLEMENTATION = "IMPLEMENTATION"
    TESTING = "TESTING"
    DOCUMENTATION = "DOCUMENTATION"
    REFACTORING = "REFACTORING"


@dataclass
class RalphStatusBlock:
    """Parsed RALPH_STATUS block from Claude output.

    Format:
    ---RALPH_STATUS---
    STATUS: IN_PROGRESS | COMPLETE | BLOCKED
    TASKS_COMPLETED_THIS_LOOP: <number>
    FILES_MODIFIED: <number>
    TESTS_STATUS: PASSING | FAILING | NOT_RUN
    WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING
    EXIT_SIGNAL: false | true
    RECOMMENDATION: <one line summary>
    ---END_RALPH_STATUS---
    """

    found: bool = False
    status: RalphStatus | None = None
    tasks_completed: int = 0
    files_modified: int = 0
    tests_status: TestsStatus | None = None
    work_type: WorkType | None = None
    exit_signal: bool = False
    recommendation: str | None = None


@dataclass
class CompletionSignals:
    """Signals indicating task completion."""

    has_done_keyword: bool = False
    has_complete_keyword: bool = False
    has_no_work_pattern: bool = False
    all_todos_done: bool = False
    is_test_only: bool = False
    confidence: float = 0.0
    matched_patterns: list[str] = field(default_factory=list)
    ralph_status: RalphStatusBlock | None = None  # Parsed RALPH_STATUS block


@dataclass
class CompletionDetectorConfig:
    """Configuration for completion detection thresholds."""

    min_confidence: float = 60.0  # Minimum confidence to trigger exit
    max_consecutive_done: int = 2  # Exit after N consecutive done signals
    max_consecutive_test_only: int = 3  # Exit after N test-only loops


class CompletionDetector:
    """Analyzes Claude output for completion signals.

    Combines keyword matching, pattern detection, and TODO file parsing
    to determine when a task is complete.
    """

    COMPLETION_KEYWORDS = [
        "done",
        "complete",
        "finished",
        "all tasks complete",
        "project complete",
        "ready for review",
        "implementation complete",
        "work is complete",
        "task completed",
    ]

    TEST_ONLY_PATTERNS = [
        r"running tests?",
        r"npm test",
        r"pytest",
        r"bun test",
        r"jest",
        r"cargo test",
        r"go test",
        r"uv run pytest",
        r"make test",
    ]

    NO_WORK_PATTERNS = [
        "nothing to do",
        "no changes needed",
        "already implemented",
        "up to date",
        "no work remaining",
        "everything is working",
        "no further changes",
    ]

    IMPLEMENTATION_PATTERNS = [
        r"implement",
        r"creat",
        r"writ",
        r"add\s+\w+",
        r"function",
        r"class",
        r"method",
        r"modify",
        r"update\s+\w+",
        r"fix\s+\w+",
    ]

    def __init__(self, config: CompletionDetectorConfig | None = None):
        self.config = config or CompletionDetectorConfig()

    def analyze(self, output: str, todo_file_content: str | None = None) -> CompletionSignals:
        """Analyze output for completion signals.

        IMPORTANT: When a RALPH_STATUS block is present, EXIT_SIGNAL is the SOLE
        authority for completion. Fallback heuristics (keywords, TODO files, etc.)
        are ONLY used when no RALPH_STATUS block is found.

        Args:
            output: Claude's output text from the iteration
            todo_file_content: Contents of @fix_plan.md or TODO.md if present

        Returns:
            CompletionSignals with detected indicators and confidence score
        """
        signals = CompletionSignals()
        output_lower = output.lower()

        # First, try to parse RALPH_STATUS block (highest priority signal)
        ralph_status = self.parse_ralph_status_block(output)
        if ralph_status.found:
            signals.ralph_status = ralph_status
            signals.matched_patterns.append("ralph_status_block")

            # When RALPH_STATUS is present, EXIT_SIGNAL is the SOLE authority
            # Do NOT run fallback heuristics - they cause false positives
            if ralph_status.exit_signal:
                signals.confidence = 100  # Definitive completion
                signals.matched_patterns.append("ralph_exit_signal=true")
            else:
                signals.confidence = 0  # Explicitly NOT complete
                signals.matched_patterns.append("ralph_exit_signal=false")

            # Record other fields for logging/debugging (but don't affect completion)
            if ralph_status.status:
                signals.matched_patterns.append(f"ralph_status={ralph_status.status.value}")
            if ralph_status.work_type == WorkType.TESTING:
                signals.is_test_only = True
                signals.matched_patterns.append("ralph_work_type=TESTING")

            # Return early - do NOT run fallback heuristics
            return signals

        # =====================================================================
        # FALLBACK HEURISTICS - only used when no RALPH_STATUS block found
        # =====================================================================
        signals.matched_patterns.append("fallback_heuristics")

        # Check completion keywords with word boundaries to avoid false positives
        # (e.g., "incomplete" should not match "complete")
        for keyword in self.COMPLETION_KEYWORDS:
            # Use word boundary regex for multi-word phrases or single words
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, output_lower):
                signals.has_complete_keyword = True
                signals.confidence += 10
                signals.matched_patterns.append(f"keyword:{keyword}")
                break

        # Check for explicit "done" signals
        if re.search(r"\bdone\b", output_lower):
            signals.has_done_keyword = True
            signals.confidence += 15
            signals.matched_patterns.append("done_keyword")

        # Check no-work patterns with word boundaries
        for pattern in self.NO_WORK_PATTERNS:
            # Use word boundary regex to avoid false positives
            pattern_regex = r"\b" + re.escape(pattern) + r"\b"
            if re.search(pattern_regex, output_lower):
                signals.has_no_work_pattern = True
                signals.confidence += 20
                signals.matched_patterns.append(f"no_work:{pattern}")
                break

        # Check TODO file completion
        if todo_file_content:
            todo_result = self._analyze_todo_file(todo_file_content)
            if todo_result["all_done"]:
                signals.all_todos_done = True
                signals.confidence += 40
                signals.matched_patterns.append(f"todos_complete:{todo_result['completed']}/{todo_result['total']}")
            elif todo_result["total"] > 0:
                # Partial completion gives some confidence
                completion_ratio = todo_result["completed"] / todo_result["total"]
                signals.confidence += completion_ratio * 20
                signals.matched_patterns.append(f"todos_partial:{todo_result['completed']}/{todo_result['total']}")

        # Check test-only loop (tests without implementation)
        signals.is_test_only = self._is_test_only_loop(output_lower)
        if signals.is_test_only:
            signals.matched_patterns.append("test_only")

        return signals

    def should_exit(
        self,
        signals: CompletionSignals,
        consecutive_done_signals: int,
        consecutive_test_only: int,
    ) -> tuple[bool, str]:
        """Determine if loop should exit based on signals.

        IMPORTANT: When a RALPH_STATUS block is present, EXIT_SIGNAL is the SOLE
        authority. If EXIT_SIGNAL=false, we MUST continue regardless of other signals.
        Fallback heuristics only apply when no RALPH_STATUS block is found.

        Args:
            signals: CompletionSignals from current iteration
            consecutive_done_signals: Count of consecutive done signal iterations
            consecutive_test_only: Count of consecutive test-only iterations

        Returns:
            Tuple of (should_exit, reason)
        """
        # When RALPH_STATUS block is present, EXIT_SIGNAL is the SOLE authority
        if signals.ralph_status and signals.ralph_status.found:
            if signals.ralph_status.exit_signal:
                return True, "RALPH_STATUS EXIT_SIGNAL=true"
            else:
                # EXIT_SIGNAL=false means explicitly NOT done - continue loop
                return False, ""

        # =====================================================================
        # FALLBACK HEURISTICS - only used when no RALPH_STATUS block found
        # =====================================================================

        # All TODOs done is strongest traditional signal
        if signals.all_todos_done:
            return True, "All TODO items completed (fallback)"

        # Multiple done signals indicate stable completion
        if consecutive_done_signals >= self.config.max_consecutive_done:
            return True, f"Multiple completion signals ({consecutive_done_signals}) (fallback)"

        # Test saturation: only running tests, no new implementation
        if consecutive_test_only >= self.config.max_consecutive_test_only:
            return True, f"Test saturation ({consecutive_test_only} test-only loops) (fallback)"

        # High confidence from combined signals
        if signals.confidence >= self.config.min_confidence:
            return True, f"High completion confidence ({signals.confidence:.0f}%) (fallback)"

        return False, ""

    def _analyze_todo_file(self, content: str) -> dict:
        """Parse TODO file to count completed vs total items.

        Supports markdown checkbox format: - [ ] incomplete, - [x] complete
        """
        # Match markdown checkboxes
        total_items = len(re.findall(r"^- \[[ xX]\]", content, re.MULTILINE))
        completed_items = len(re.findall(r"^- \[[xX]\]", content, re.MULTILINE))

        return {
            "total": total_items,
            "completed": completed_items,
            "all_done": total_items > 0 and completed_items == total_items,
        }

    def _is_test_only_loop(self, output_lower: str) -> bool:
        """Detect if loop only ran tests without implementation.

        A test-only loop has test commands but no implementation activity.
        Multiple consecutive test-only loops indicate the work is done
        and Claude is just verifying.
        """
        test_matches = sum(1 for p in self.TEST_ONLY_PATTERNS if re.search(p, output_lower))

        impl_matches = sum(1 for p in self.IMPLEMENTATION_PATTERNS if re.search(p, output_lower))

        # Test-only if we found test patterns but no implementation patterns
        return test_matches > 0 and impl_matches == 0

    def parse_ralph_status_block(self, output: str) -> RalphStatusBlock:
        """Parse RALPH_STATUS block from Claude output.

        Args:
            output: Claude's full output text

        Returns:
            RalphStatusBlock with parsed values, found=False if no block
        """
        block = RalphStatusBlock()

        # Find the status block
        pattern = r"---RALPH_STATUS---\s*(.*?)\s*---END_RALPH_STATUS---"
        match = re.search(pattern, output, re.DOTALL | re.IGNORECASE)

        if not match:
            return block

        block.found = True
        block_content = match.group(1)

        # Parse each field
        for line in block_content.strip().split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue

            key, value = line.split(":", 1)
            key = key.strip().upper()
            value = value.strip()

            if key == "STATUS":
                try:
                    block.status = RalphStatus(value.upper())
                except ValueError:
                    pass

            elif key == "TASKS_COMPLETED_THIS_LOOP":
                try:
                    block.tasks_completed = int(value)
                except ValueError:
                    pass

            elif key == "FILES_MODIFIED":
                try:
                    block.files_modified = int(value)
                except ValueError:
                    pass

            elif key == "TESTS_STATUS":
                try:
                    block.tests_status = TestsStatus(value.upper())
                except ValueError:
                    pass

            elif key == "WORK_TYPE":
                try:
                    block.work_type = WorkType(value.upper())
                except ValueError:
                    pass

            elif key == "EXIT_SIGNAL":
                block.exit_signal = value.lower() in ("true", "yes", "1")

            elif key == "RECOMMENDATION":
                block.recommendation = value

        return block
