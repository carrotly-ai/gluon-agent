"""Tests for parse_channel_topic() from Discord transport."""


from gluon.transport.discord import parse_channel_topic


class TestParseChannelTopic:
    def test_none_topic(self):
        result = parse_channel_topic(None)
        assert result == {"project": None, "model": None}

    def test_empty_string(self):
        result = parse_channel_topic("")
        assert result == {"project": None, "model": None}

    def test_project_long_flag(self):
        result = parse_channel_topic("--project myproject")
        assert result["project"] == "myproject"

    def test_project_short_flag(self):
        result = parse_channel_topic("-p myapp")
        assert result["project"] == "myapp"

    def test_model_haiku(self):
        result = parse_channel_topic("--model haiku")
        assert result["model"] == "claude-haiku-4.5"

    def test_model_sonnet(self):
        result = parse_channel_topic("--model sonnet")
        assert result["model"] == "claude-sonnet-4.6"

    def test_model_opus(self):
        result = parse_channel_topic("--model opus")
        assert result["model"] == "claude-opus-4.5"

    def test_unknown_model_returns_none(self):
        result = parse_channel_topic("--model unknown-model")
        assert result["model"] is None

    def test_combined_project_and_model(self):
        result = parse_channel_topic("--project foo --model haiku")
        assert result["project"] == "foo"
        assert result["model"] == "claude-haiku-4.5"

    def test_short_forms(self):
        result = parse_channel_topic("-p bar -m opus")
        assert result["project"] == "bar"
        assert result["model"] == "claude-opus-4.5"

    def test_extra_text_around_flags(self):
        result = parse_channel_topic("Dev channel --model haiku for testing")
        assert result["model"] == "claude-haiku-4.5"
        assert result["project"] is None

    def test_case_insensitive_flags(self):
        result = parse_channel_topic("--MODEL sonnet")
        assert result["model"] == "claude-sonnet-4.6"

    def test_backwards_compat_claude_sonnet_45(self):
        result = parse_channel_topic("--model claude-sonnet-4.5")
        assert result["model"] == "claude-sonnet-4.6"

    def test_malformed_flag_no_value(self):
        # --model at end with nothing after it — regex won't match \S+
        result = parse_channel_topic("--model")
        assert result["model"] is None

    def test_full_model_name_passthrough(self):
        result = parse_channel_topic("--model claude-haiku-4.5")
        assert result["model"] == "claude-haiku-4.5"

    def test_regular_topic_no_flags(self):
        result = parse_channel_topic("This is a regular channel topic")
        assert result == {"project": None, "model": None}
