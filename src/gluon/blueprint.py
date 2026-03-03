"""Blueprint validation: run lint + test after agent execution completes.

Provides post-execution validation gates and feedback prompt generation
for bounded retry loops. Inspired by Stripe's Blueprint Engine pattern.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 3000

# Patterns in test output that indicate "no tests exist" rather than actual failures.
# These should be treated as a pass — nothing to validate.
_NO_TESTS_PATTERNS = (
    "no tests found",
    "no test files found",
    "no tests were found",
    "0 tests found",
    "test suite failed to run",  # jest with no test files
    "no specs found",  # vitest/jasmine
    "no tests ran",  # pytest
    "collected 0 items",  # pytest
)


@dataclass
class StepResult:
    name: str
    passed: bool
    exit_code: int | None = None
    output: str = ""
    duration_secs: float = 0.0


async def _run_step(name: str, cmd: str, cwd: Path, timeout_secs: int) -> StepResult:
    """Run a single validation step as a subprocess."""
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_secs)
        output = (stdout.decode(errors="replace") if stdout else "")[:_MAX_OUTPUT_CHARS]
        duration = time.monotonic() - start
        return StepResult(
            name=name,
            passed=proc.returncode == 0,
            exit_code=proc.returncode,
            output=output,
            duration_secs=round(duration, 2),
        )
    except TimeoutError:
        duration = time.monotonic() - start
        return StepResult(
            name=name,
            passed=False,
            exit_code=None,
            output=f"Timed out after {timeout_secs}s",
            duration_secs=round(duration, 2),
        )
    except OSError as e:
        duration = time.monotonic() - start
        return StepResult(
            name=name,
            passed=False,
            exit_code=None,
            output=f"Failed to run: {e}",
            duration_secs=round(duration, 2),
        )


async def run_validation(
    working_dir: Path,
    lint_cmd: str | None,
    test_cmd: str | None,
    timeout_secs: int = 300,
) -> list[StepResult]:
    """Run lint then test steps. Skip if command is None."""
    results: list[StepResult] = []

    if lint_cmd:
        result = await _run_step("lint", lint_cmd, working_dir, timeout_secs)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("Blueprint lint: %s (exit=%s, %.1fs)", status, result.exit_code, result.duration_secs)

    if test_cmd:
        result = await _run_step("test", test_cmd, working_dir, timeout_secs)
        if not result.passed:
            output_lower = result.output.lower()
            if any(p in output_lower for p in _NO_TESTS_PATTERNS):
                logger.info("Blueprint test: SKIP (no tests found, treating as pass)")
                result = StepResult(
                    name="test",
                    passed=True,
                    exit_code=result.exit_code,
                    output="No tests found — skipped validation",
                    duration_secs=result.duration_secs,
                )
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("Blueprint test: %s (exit=%s, %.1fs)", status, result.exit_code, result.duration_secs)

    return results


def build_feedback_prompt(results: list[StepResult]) -> str:
    """Build a retry prompt from failed validation steps."""
    parts = [
        "Your changes have lint/test failures. Please fix them:\n",
    ]

    for r in results:
        if not r.passed:
            header = f"## {r.name.title()} Errors (exit code {r.exit_code})"
            parts.append(header)
            parts.append(f"```\n{r.output}\n```\n")

    parts.append(
        "Fix only the issues shown above. Do not make unrelated changes. "
        "Run the failing command(s) to verify your fix before completing."
    )

    return "\n".join(parts)


def should_retry(run_metadata: dict) -> bool:
    """Return True if blueprint failed and retry count < 1."""
    status = run_metadata.get("blueprint_status")
    retry_count = run_metadata.get("blueprint_retry_count", 0)
    return status == "failed" and retry_count < 1
