"""Tests for Discord transport helper functions."""

from gluon.transport.discord import parse_model_flag, parse_project_specifier


class TestParseProjectSpecifier:
    """Tests for parse_project_specifier function."""

    def test_project_prefix(self):
        """Test project:name prefix format."""
        cleaned, project = parse_project_specifier("project:myapp fix the bug")
        assert project == "myapp"
        assert cleaned == "fix the bug"

    def test_short_prefix(self):
        """Test p:name short prefix format."""
        cleaned, project = parse_project_specifier("p:myapp fix the bug")
        assert project == "myapp"
        assert cleaned == "fix the bug"

    def test_project_flag(self):
        """Test --project flag format."""
        cleaned, project = parse_project_specifier("fix the bug --project myapp")
        assert project == "myapp"
        assert cleaned == "fix the bug"

    def test_short_flag(self):
        """Test -p flag format."""
        cleaned, project = parse_project_specifier("fix the bug -p myapp")
        assert project == "myapp"
        assert cleaned == "fix the bug"

    def test_no_project(self):
        """Test message without project specifier."""
        cleaned, project = parse_project_specifier("what projects do I have?")
        assert project is None
        assert cleaned == "what projects do I have?"

    def test_case_insensitive_prefix(self):
        """Test that prefix is case-insensitive."""
        cleaned, project = parse_project_specifier("PROJECT:myapp fix bug")
        assert project == "myapp"
        assert cleaned == "fix bug"

    def test_case_insensitive_flag(self):
        """Test that flag is case-insensitive."""
        cleaned, project = parse_project_specifier("fix bug --PROJECT myapp")
        assert project == "myapp"
        assert cleaned == "fix bug"

    def test_project_with_hyphens(self):
        """Test project name with hyphens."""
        cleaned, project = parse_project_specifier("p:my-cool-app add tests")
        assert project == "my-cool-app"
        assert cleaned == "add tests"

    def test_project_with_underscores(self):
        """Test project name with underscores."""
        cleaned, project = parse_project_specifier("project:my_cool_app add tests")
        assert project == "my_cool_app"
        assert cleaned == "add tests"

    def test_combined_with_model_flag(self):
        """Test project specifier combined with model flag."""
        cleaned, project = parse_project_specifier("p:myapp fix bug --model opus")
        assert project == "myapp"
        assert cleaned == "fix bug --model opus"


class TestParseModelFlag:
    """Tests for parse_model_flag function."""

    def test_model_flag_long(self):
        """Test --model flag."""
        cleaned, model = parse_model_flag("fix bug --model opus")
        assert model == "claude-opus-4.8"
        assert cleaned == "fix bug"

    def test_model_flag_short(self):
        """Test -m flag."""
        cleaned, model = parse_model_flag("fix bug -m haiku")
        assert model == "claude-haiku-4.5"
        assert cleaned == "fix bug"

    def test_no_model(self):
        """Test message without model flag."""
        cleaned, model = parse_model_flag("fix the bug please")
        assert model is None
        assert cleaned == "fix the bug please"

    def test_unknown_model(self):
        """Test unknown model name."""
        cleaned, model = parse_model_flag("fix bug --model gpt4")
        assert model is None
        assert cleaned == "fix bug --model gpt4"

    def test_full_model_name(self):
        """Test full model name."""
        cleaned, model = parse_model_flag("fix bug --model claude-sonnet-4.6")
        assert model == "claude-sonnet-4.6"
        assert cleaned == "fix bug"

    def test_sonnet_shorthand_resolves_to_46(self):
        """Test --model sonnet resolves to claude-sonnet-4.6 (updated alias)."""
        cleaned, model = parse_model_flag("fix bug --model sonnet")
        assert model == "claude-sonnet-4.6"
        assert cleaned == "fix bug"

    def test_opus_shorthand_resolves_to_48(self):
        """Test --model opus resolves to claude-opus-4.8."""
        cleaned, model = parse_model_flag("fix bug --model opus")
        assert model == "claude-opus-4.8"
        assert cleaned == "fix bug"

    def test_claude_sonnet_45_alias_maps_to_46(self):
        """Test claude-sonnet-4.5 backwards compat alias maps to 4.6."""
        cleaned, model = parse_model_flag("fix bug --model claude-sonnet-4.5")
        assert model == "claude-sonnet-4.6"
        assert cleaned == "fix bug"

    def test_empty_prompt_after_flag(self):
        """Test that extracting flag from entire message yields empty string."""
        cleaned, model = parse_model_flag("--model haiku")
        assert model == "claude-haiku-4.5"
        assert cleaned == ""

    def test_flag_at_start_of_message(self):
        """Test flag at beginning of message."""
        cleaned, model = parse_model_flag("--model sonnet fix the bug")
        assert model == "claude-sonnet-4.6"
        assert cleaned == "fix the bug"

    def test_multiple_flags_first_matched(self):
        """Test that only the first --model flag is matched."""
        cleaned, model = parse_model_flag("fix bug --model haiku --model opus")
        assert model == "claude-haiku-4.5"
