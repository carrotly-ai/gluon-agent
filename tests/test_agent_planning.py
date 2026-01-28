"""Tests for GluonAgent planning prompt selection logic."""

import pytest

from gluon.agent import GluonAgent


class TestPlanningPromptSelection:
    """Test that correct planning prompt is selected based on mode."""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        """Create a temp directory for tests."""
        return tmp_path

    def test_interactive_planning_uses_standard_prompt(self, temp_dir):
        """force_planning=True without ralph_mode uses PLANNING_SYSTEM_PROMPT."""
        agent = GluonAgent(force_planning=True)
        options = agent._build_options(temp_dir, ralph_mode=False)

        # Should include PLANNING_SYSTEM_PROMPT (wait for confirmation)
        append_content = options.system_prompt.get("append", "")
        assert "Wait for Confirmation" in append_content
        assert "PLANNING MODE ACTIVE" in append_content
        # Should NOT include autonomous prompt
        assert "PLANNING MODE (Autonomous)" not in append_content

    def test_ralph_planning_uses_autonomous_prompt(self, temp_dir):
        """force_planning=True with ralph_mode uses PLANNING_AUTONOMOUS_PROMPT."""
        agent = GluonAgent(force_planning=True)
        options = agent._build_options(temp_dir, ralph_mode=True)

        # Should include PLANNING_AUTONOMOUS_PROMPT (no wait for confirmation)
        append_content = options.system_prompt.get("append", "")
        assert "PLANNING MODE (Autonomous)" in append_content
        assert "Do NOT wait for human confirmation" in append_content
        # Should NOT include standard planning prompt
        assert "Wait for Confirmation" not in append_content

    def test_no_planning_prompt_when_disabled(self, temp_dir):
        """force_planning=False includes neither planning prompt."""
        agent = GluonAgent(force_planning=False)
        options = agent._build_options(temp_dir, ralph_mode=False)

        append_content = options.system_prompt.get("append", "")
        assert "PLANNING MODE ACTIVE" not in append_content
        assert "PLANNING MODE (Autonomous)" not in append_content

    def test_ralph_without_planning_has_no_planning_prompt(self, temp_dir):
        """ralph_mode=True without force_planning doesn't add planning prompt."""
        agent = GluonAgent(force_planning=False)
        options = agent._build_options(temp_dir, ralph_mode=True)

        append_content = options.system_prompt.get("append", "")
        # Should have RALPH_STATUS instructions
        assert "RALPH_STATUS" in append_content
        # Should NOT have planning prompts
        assert "PLANNING MODE ACTIVE" not in append_content
        assert "PLANNING MODE (Autonomous)" not in append_content
