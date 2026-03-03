"""Unit tests for blueprint.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from gluon.blueprint import (
    StepResult,
    build_feedback_prompt,
    run_autofix,
    run_lint,
    run_test,
    run_validation,
    should_retry,
)


class TestStepResult:
    def test_passed_result(self):
        r = StepResult(name="lint", passed=True, exit_code=0, output="ok", duration_secs=1.5)
        assert r.passed
        assert r.exit_code == 0

    def test_failed_result(self):
        r = StepResult(name="test", passed=False, exit_code=1, output="FAIL", duration_secs=5.0)
        assert not r.passed


@pytest.mark.asyncio
class TestRunValidation:
    async def test_skip_when_no_commands(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd=None, test_cmd=None)
        assert results == []

    async def test_lint_pass(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd="true", test_cmd=None)
        assert len(results) == 1
        assert results[0].name == "lint"
        assert results[0].passed

    async def test_lint_fail(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd="false", test_cmd=None)
        assert len(results) == 1
        assert not results[0].passed
        assert results[0].exit_code != 0

    async def test_both_steps_run(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd="true", test_cmd="true")
        assert len(results) == 2
        assert results[0].name == "lint"
        assert results[1].name == "test"
        assert all(r.passed for r in results)

    async def test_lint_fails_test_still_runs(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd="false", test_cmd="true")
        assert len(results) == 2
        assert not results[0].passed  # lint failed
        assert results[1].passed  # test still ran

    async def test_timeout(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd="sleep 10", test_cmd=None, timeout_secs=1)
        assert len(results) == 1
        assert not results[0].passed
        assert "Timed out" in results[0].output

    async def test_captures_output(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd="echo 'hello world'", test_cmd=None)
        assert len(results) == 1
        assert "hello world" in results[0].output

    async def test_records_duration(self, tmp_path: Path):
        results = await run_validation(tmp_path, lint_cmd="true", test_cmd=None)
        assert results[0].duration_secs >= 0

    async def test_no_tests_found_treated_as_pass(self, tmp_path: Path):
        results = await run_validation(
            tmp_path,
            lint_cmd=None,
            test_cmd="echo 'No tests found' && exit 1",
        )
        assert len(results) == 1
        assert results[0].passed
        assert "No tests found" in results[0].output

    async def test_no_tests_found_case_insensitive(self, tmp_path: Path):
        results = await run_validation(
            tmp_path,
            lint_cmd=None,
            test_cmd="echo 'NO TESTS FOUND' && exit 1",
        )
        assert len(results) == 1
        assert results[0].passed

    async def test_real_test_failure_not_treated_as_no_tests(self, tmp_path: Path):
        results = await run_validation(
            tmp_path,
            lint_cmd=None,
            test_cmd="echo 'FAILED test_foo.py::test_bar' && exit 1",
        )
        assert len(results) == 1
        assert not results[0].passed


class TestBuildFeedbackPrompt:
    def test_includes_failed_steps_only(self):
        results = [
            StepResult(name="lint", passed=False, exit_code=1, output="E001: bad indent"),
            StepResult(name="test", passed=True, exit_code=0, output="all passed"),
        ]
        prompt = build_feedback_prompt(results)
        assert "Lint Errors" in prompt
        assert "E001: bad indent" in prompt
        assert "all passed" not in prompt  # passed step not included

    def test_includes_multiple_failures(self):
        results = [
            StepResult(name="lint", passed=False, exit_code=1, output="lint error"),
            StepResult(name="test", passed=False, exit_code=1, output="test error"),
        ]
        prompt = build_feedback_prompt(results)
        assert "Lint Errors" in prompt
        assert "Test Errors" in prompt

    def test_includes_fix_instruction(self):
        results = [
            StepResult(name="lint", passed=False, exit_code=1, output="error"),
        ]
        prompt = build_feedback_prompt(results)
        assert "Fix only the issues" in prompt


class TestShouldRetry:
    def test_retry_on_first_failure(self):
        meta = {"blueprint_status": "failed", "blueprint_retry_count": 0}
        assert should_retry(meta) is True

    def test_no_retry_after_max(self):
        meta = {"blueprint_status": "failed", "blueprint_retry_count": 1}
        assert should_retry(meta) is False

    def test_no_retry_when_passed(self):
        meta = {"blueprint_status": "passed", "blueprint_retry_count": 0}
        assert should_retry(meta) is False

    def test_no_retry_when_no_status(self):
        assert should_retry({}) is False

    def test_no_retry_when_missing_count(self):
        meta = {"blueprint_status": "failed"}
        assert should_retry(meta) is True  # default count is 0


@pytest.mark.asyncio
class TestRunAutofix:
    async def test_successful_autofix(self, tmp_path: Path):
        result = await run_autofix(tmp_path, "true")
        assert result.name == "autofix"
        assert result.passed
        assert result.exit_code == 0

    async def test_partial_autofix(self, tmp_path: Path):
        # Auto-fix tools may exit non-zero when some issues can't be fixed
        result = await run_autofix(tmp_path, "echo 'fixed some' && exit 1")
        assert result.name == "autofix"
        assert not result.passed
        assert "fixed some" in result.output

    async def test_autofix_timeout(self, tmp_path: Path):
        result = await run_autofix(tmp_path, "sleep 10", timeout_secs=1)
        assert not result.passed
        assert "Timed out" in result.output


@pytest.mark.asyncio
class TestRunLint:
    async def test_lint_pass(self, tmp_path: Path):
        result = await run_lint(tmp_path, "true")
        assert result.name == "lint"
        assert result.passed

    async def test_lint_fail(self, tmp_path: Path):
        result = await run_lint(tmp_path, "echo 'E001: bad indent' && exit 1")
        assert result.name == "lint"
        assert not result.passed
        assert "E001" in result.output

    async def test_lint_captures_output(self, tmp_path: Path):
        result = await run_lint(tmp_path, "echo 'all clean'")
        assert "all clean" in result.output


@pytest.mark.asyncio
class TestRunTest:
    async def test_test_pass(self, tmp_path: Path):
        result = await run_test(tmp_path, "true")
        assert result.name == "test"
        assert result.passed

    async def test_test_fail(self, tmp_path: Path):
        result = await run_test(tmp_path, "echo 'FAILED test_foo' && exit 1")
        assert result.name == "test"
        assert not result.passed

    async def test_no_tests_found_treated_as_pass(self, tmp_path: Path):
        result = await run_test(tmp_path, "echo 'No tests found' && exit 1")
        assert result.passed
        assert "No tests found" in result.output

    async def test_no_tests_found_case_insensitive(self, tmp_path: Path):
        result = await run_test(tmp_path, "echo 'NO TESTS FOUND' && exit 1")
        assert result.passed

    async def test_collected_0_items(self, tmp_path: Path):
        result = await run_test(tmp_path, "echo 'collected 0 items' && exit 1")
        assert result.passed

    async def test_real_failure_not_treated_as_no_tests(self, tmp_path: Path):
        result = await run_test(tmp_path, "echo 'FAILED test_bar' && exit 1")
        assert not result.passed

    async def test_timeout(self, tmp_path: Path):
        result = await run_test(tmp_path, "sleep 10", timeout_secs=1)
        assert not result.passed
        assert "Timed out" in result.output
