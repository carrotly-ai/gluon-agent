"""Tests for CompletionDetector - task completion detection."""

from gluon.completion_detector import (
    CompletionDetector,
    CompletionDetectorConfig,
    RalphStatus,
    TestsStatus,
    WorkType,
)


class TestRalphStatusParsing:
    """Test RALPH_STATUS block parsing."""

    def test_parse_valid_ralph_status_block(self):
        """Parse a complete RALPH_STATUS block."""
        output = """
        Some work output here.

        ---RALPH_STATUS---
        STATUS: COMPLETE
        TASKS_COMPLETED_THIS_LOOP: 3
        FILES_MODIFIED: 5
        TESTS_STATUS: PASSING
        WORK_TYPE: IMPLEMENTATION
        EXIT_SIGNAL: true
        RECOMMENDATION: Ready for review
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        block = detector.parse_ralph_status_block(output)

        assert block.found
        assert block.status == RalphStatus.COMPLETE
        assert block.tasks_completed == 3
        assert block.files_modified == 5
        assert block.tests_status == TestsStatus.PASSING
        assert block.work_type == WorkType.IMPLEMENTATION
        assert block.exit_signal is True
        assert block.recommendation == "Ready for review"

    def test_parse_ralph_status_in_progress(self):
        """Parse IN_PROGRESS status."""
        output = """
        ---RALPH_STATUS---
        STATUS: IN_PROGRESS
        TASKS_COMPLETED_THIS_LOOP: 1
        FILES_MODIFIED: 2
        TESTS_STATUS: NOT_RUN
        WORK_TYPE: REFACTORING
        EXIT_SIGNAL: false
        RECOMMENDATION: Continue with next task
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        block = detector.parse_ralph_status_block(output)

        assert block.found
        assert block.status == RalphStatus.IN_PROGRESS
        assert block.exit_signal is False

    def test_parse_ralph_status_blocked(self):
        """Parse BLOCKED status."""
        output = """
        ---RALPH_STATUS---
        STATUS: BLOCKED
        EXIT_SIGNAL: false
        RECOMMENDATION: Need API key to proceed
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        block = detector.parse_ralph_status_block(output)

        assert block.found
        assert block.status == RalphStatus.BLOCKED

    def test_no_ralph_status_block(self):
        """Handle output without RALPH_STATUS block."""
        output = "Just some regular output without any status block."
        detector = CompletionDetector()
        block = detector.parse_ralph_status_block(output)

        assert not block.found
        assert block.status is None

    def test_partial_ralph_status_block(self):
        """Handle incomplete RALPH_STATUS block."""
        output = """
        ---RALPH_STATUS---
        STATUS: COMPLETE
        EXIT_SIGNAL: true
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        block = detector.parse_ralph_status_block(output)

        assert block.found
        assert block.status == RalphStatus.COMPLETE
        assert block.exit_signal is True
        # Missing fields default to zero/None
        assert block.tasks_completed == 0


class TestCompletionSignals:
    """Test completion signal detection."""

    def test_ralph_exit_signal_high_confidence(self):
        """RALPH_STATUS EXIT_SIGNAL=true gives high confidence."""
        output = """
        ---RALPH_STATUS---
        STATUS: COMPLETE
        EXIT_SIGNAL: true
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        signals = detector.analyze(output)

        assert signals.ralph_status is not None
        assert signals.ralph_status.exit_signal is True
        assert signals.confidence >= 50  # High confidence for exit signal

    def test_keyword_detection(self):
        """Detect completion keywords in output."""
        output = "All tasks complete. The implementation is done."
        detector = CompletionDetector()
        signals = detector.analyze(output)

        assert signals.has_complete_keyword
        assert signals.confidence > 0
        assert any("keyword" in p for p in signals.matched_patterns)

    def test_test_only_detection(self):
        """Detect test-only loops."""
        output = "Running pytest to verify the changes. All tests passed."
        detector = CompletionDetector()
        signals = detector.analyze(output)

        assert signals.is_test_only

    def test_no_work_pattern(self):
        """Detect 'no work needed' patterns."""
        output = "Nothing to do here, everything is already implemented."
        detector = CompletionDetector()
        signals = detector.analyze(output)

        assert signals.has_no_work_pattern

    def test_todo_completion(self):
        """Detect when all TODO items are done."""
        output = "Completed the final task."
        # Note: markdown checkboxes need to be at start of line
        todo_content = """# Tasks
- [x] First task
- [x] Second task
- [x] Third task
"""
        detector = CompletionDetector()
        signals = detector.analyze(output, todo_content)

        assert signals.all_todos_done

    def test_incomplete_todos(self):
        """Don't flag complete when TODOs remain."""
        output = "Made some progress."
        # Note: markdown checkboxes need to be at start of line
        todo_content = """# Tasks
- [x] First task
- [ ] Second task still pending
- [x] Third task
"""
        detector = CompletionDetector()
        signals = detector.analyze(output, todo_content)

        assert not signals.all_todos_done

    def test_no_false_positive_on_incomplete(self):
        """Word 'incomplete' should not match 'complete'."""
        output = "The task is still incomplete."
        detector = CompletionDetector()
        signals = detector.analyze(output)

        # Should not trigger keyword match for "complete"
        assert not any("keyword:complete" in p for p in signals.matched_patterns)


class TestShouldExitLogic:
    """Test exit decision logic."""

    def test_exit_on_ralph_exit_signal(self):
        """Exit immediately on RALPH_STATUS EXIT_SIGNAL=true."""
        output = """
        ---RALPH_STATUS---
        STATUS: COMPLETE
        EXIT_SIGNAL: true
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        signals = detector.analyze(output)
        should_exit, reason = detector.should_exit(signals, 0, 0)

        assert should_exit
        assert "EXIT_SIGNAL" in reason

    def test_no_exit_when_exit_signal_false(self):
        """Do NOT exit when EXIT_SIGNAL=false, even if STATUS=COMPLETE.

        EXIT_SIGNAL is the SOLE authority when RALPH_STATUS block is present.
        STATUS=COMPLETE alone does NOT trigger exit - Claude must explicitly
        set EXIT_SIGNAL=true to indicate completion.
        """
        output = """
        ---RALPH_STATUS---
        STATUS: COMPLETE
        EXIT_SIGNAL: false
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        signals = detector.analyze(output)
        should_exit, reason = detector.should_exit(signals, 0, 0)

        # EXIT_SIGNAL=false means continue, regardless of STATUS
        assert not should_exit
        assert reason == ""

    def test_fallback_heuristics_skipped_when_ralph_status_present(self):
        """Fallback heuristics are NOT used when RALPH_STATUS block is present.

        When Claude provides a RALPH_STATUS block, EXIT_SIGNAL is the SOLE
        authority. Keywords like 'complete' or 'done' should NOT trigger
        completion detection.
        """
        # Output with RALPH_STATUS (EXIT_SIGNAL=false) AND completion keywords
        output = """
        All tasks complete! The work is done.
        ---RALPH_STATUS---
        STATUS: IN_PROGRESS
        EXIT_SIGNAL: false
        ---END_RALPH_STATUS---
        """
        detector = CompletionDetector()
        signals = detector.analyze(output)

        # Verify RALPH_STATUS was detected
        assert signals.ralph_status is not None
        assert signals.ralph_status.found

        # Verify confidence is 0 (not affected by keywords)
        assert signals.confidence == 0

        # Verify fallback_heuristics pattern was NOT added
        assert "fallback_heuristics" not in signals.matched_patterns

        # Verify we do NOT exit despite completion keywords
        should_exit, reason = detector.should_exit(signals, 0, 0)
        assert not should_exit

    def test_exit_on_all_todos_done(self):
        """Exit when all TODO items are complete."""
        output = "Finished."
        todo_content = "- [x] Only task"
        detector = CompletionDetector()
        signals = detector.analyze(output, todo_content)
        should_exit, reason = detector.should_exit(signals, 0, 0)

        assert should_exit
        assert "TODO" in reason

    def test_exit_on_consecutive_done_signals(self):
        """Exit after multiple consecutive done signals."""
        config = CompletionDetectorConfig(max_consecutive_done=2)
        detector = CompletionDetector(config)
        signals = detector.analyze("Just some output")  # Low confidence

        # First signal - no exit
        signals.has_done_keyword = True
        should_exit, _ = detector.should_exit(signals, consecutive_done_signals=1, consecutive_test_only=0)
        assert not should_exit

        # Second consecutive signal - exit
        should_exit, reason = detector.should_exit(signals, consecutive_done_signals=2, consecutive_test_only=0)
        assert should_exit
        assert "multiple" in reason.lower() or "signal" in reason.lower()

    def test_exit_on_test_saturation(self):
        """Exit after multiple test-only loops."""
        config = CompletionDetectorConfig(max_consecutive_test_only=3)
        detector = CompletionDetector(config)
        signals = detector.analyze("Running tests...")
        signals.is_test_only = True

        # Not enough yet
        should_exit, _ = detector.should_exit(signals, 0, consecutive_test_only=2)
        assert not should_exit

        # Saturation reached
        should_exit, reason = detector.should_exit(signals, 0, consecutive_test_only=3)
        assert should_exit
        assert "test" in reason.lower()

    def test_exit_on_high_confidence(self):
        """Exit when confidence exceeds threshold."""
        config = CompletionDetectorConfig(min_confidence=60.0)
        detector = CompletionDetector(config)

        # Build signals with high confidence
        output = """
        ---RALPH_STATUS---
        STATUS: COMPLETE
        EXIT_SIGNAL: true
        ---END_RALPH_STATUS---
        The task is complete. All done.
        """
        signals = detector.analyze(output)
        # EXIT_SIGNAL gives +50, STATUS=COMPLETE gives +30, keyword gives +10 = 90+
        assert signals.confidence >= 60

        should_exit, reason = detector.should_exit(signals, 0, 0)
        assert should_exit

    def test_no_exit_on_low_confidence(self):
        """Don't exit when confidence is below threshold."""
        config = CompletionDetectorConfig(min_confidence=60.0)
        detector = CompletionDetector(config)
        signals = detector.analyze("Working on the task...")

        assert signals.confidence < 60
        should_exit, _ = detector.should_exit(signals, 0, 0)
        assert not should_exit


class TestCompletionDetectorConfig:
    """Test configurable thresholds."""

    def test_custom_config(self):
        """Custom config is respected."""
        config = CompletionDetectorConfig(
            min_confidence=80.0,
            max_consecutive_done=5,
            max_consecutive_test_only=10,
        )
        detector = CompletionDetector(config)
        assert detector.config.min_confidence == 80.0
        assert detector.config.max_consecutive_done == 5
        assert detector.config.max_consecutive_test_only == 10

    def test_default_config(self):
        """Default config has sensible values."""
        config = CompletionDetectorConfig()
        assert config.min_confidence == 60.0
        assert config.max_consecutive_done == 2
        assert config.max_consecutive_test_only == 3


class TestTodoFileParsing:
    """Test TODO file completion detection via analyze()."""

    def test_markdown_checkboxes(self):
        """Parse markdown checkbox format via analyze()."""
        # Checkboxes must be at start of line
        todo = """# TODO
- [x] Done task
- [ ] Pending task
"""
        detector = CompletionDetector()
        # Use _analyze_todo_file directly for detailed testing
        result = detector._analyze_todo_file(todo)
        assert result["completed"] == 1
        assert result["total"] == 2
        # remaining = total - completed
        assert result["total"] - result["completed"] == 1

    def test_all_complete(self):
        """Detect 100% completion."""
        todo = """- [x] Task 1
- [x] Task 2
"""
        detector = CompletionDetector()
        result = detector._analyze_todo_file(todo)
        # All completed = total
        assert result["completed"] == result["total"] == 2
        assert result["all_done"]

    def test_empty_todo(self):
        """Handle empty TODO content."""
        detector = CompletionDetector()
        result = detector._analyze_todo_file("")
        assert result["completed"] == 0
        assert result["total"] == 0
        assert result["all_done"] is False  # No todos means not "all done"

    def test_via_signals(self):
        """Test TODO detection via analyze() signals."""
        todo = """- [x] Task 1
- [x] Task 2
"""
        detector = CompletionDetector()
        signals = detector.analyze("Some output", todo)
        assert signals.all_todos_done
        assert "todos_complete" in str(signals.matched_patterns)
