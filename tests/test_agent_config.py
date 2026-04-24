"""Tests for GluonAgent initialization and _build_options() in agent.py."""

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from gluon.agent import DANGEROUS_PATTERNS, GluonAgent, ImageContent, MultimodalPrompt, find_mcp_config
from gluon.models import (
    GLUON_SYSTEM_PROMPT,
    PLANNING_AUTONOMOUS_PROMPT,
    PLANNING_SYSTEM_PROMPT,
    RALPH_SYSTEM_PROMPT,
)
from gluon.models_config import MODEL_IDS, ModelTier, get_model_id


class TestDangerousPatterns:
    """Tests for DANGEROUS_PATTERNS list."""

    def test_contains_rm_rf_root(self):
        assert "rm -rf /" in DANGEROUS_PATTERNS

    def test_contains_rm_rf_home(self):
        assert "rm -rf ~" in DANGEROUS_PATTERNS

    def test_contains_force_push_main(self):
        assert "git push --force origin main" in DANGEROUS_PATTERNS

    def test_contains_drop_table(self):
        assert "DROP TABLE" in DANGEROUS_PATTERNS

    def test_contains_fork_bomb(self):
        assert ":(){ :|:& };:" in DANGEROUS_PATTERNS

    def test_contains_chmod_777(self):
        assert "chmod 777" in DANGEROUS_PATTERNS


class TestAgentModelResolution:
    """Tests for model resolution during GluonAgent initialization."""

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    def test_tier_string_resolved_to_bedrock_id(self, _mock_cli):
        agent = GluonAgent(model="sonnet")
        assert agent.model == get_model_id("sonnet")

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    def test_haiku_tier_resolved(self, _mock_cli):
        agent = GluonAgent(model="haiku")
        assert agent.model == MODEL_IDS[ModelTier.HAIKU]

    @pytest.mark.parametrize(
        "provider_name,full_id",
        [
            ("bedrock", "global.anthropic.claude-sonnet-4-6"),
            ("anthropic", "claude-sonnet-4-6"),
        ],
    )
    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    def test_full_model_id_passthrough(self, _mock_cli, provider_name, full_id, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", provider_name)
        agent = GluonAgent(model=full_id)
        assert agent.model == full_id

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    def test_invalid_model_used_as_is(self, _mock_cli):
        agent = GluonAgent(model="some-unknown-model")
        assert agent.model == "some-unknown-model"


class TestBuildOptionsSystemPrompt:
    """Tests for system prompt assembly in _build_options()."""

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_system_prompt_includes_gluon_prompt(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet")
        options = agent._build_options(tmp_path)
        append_text = options.system_prompt["append"]
        assert GLUON_SYSTEM_PROMPT in append_text

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_force_planning_appends_planning_prompt(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet", force_planning=True)
        options = agent._build_options(tmp_path)
        append_text = options.system_prompt["append"]
        assert PLANNING_SYSTEM_PROMPT in append_text

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_ralph_mode_appends_ralph_prompt(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet")
        options = agent._build_options(tmp_path, ralph_mode=True)
        append_text = options.system_prompt["append"]
        assert RALPH_SYSTEM_PROMPT in append_text

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_force_planning_plus_ralph_uses_autonomous_prompt(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet", force_planning=True)
        options = agent._build_options(tmp_path, ralph_mode=True)
        append_text = options.system_prompt["append"]
        assert PLANNING_AUTONOMOUS_PROMPT in append_text
        # Should NOT have the interactive planning prompt
        assert PLANNING_SYSTEM_PROMPT not in append_text

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_no_planning_no_ralph(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet")
        options = agent._build_options(tmp_path)
        append_text = options.system_prompt["append"]
        assert PLANNING_SYSTEM_PROMPT not in append_text
        assert RALPH_SYSTEM_PROMPT not in append_text
        assert PLANNING_AUTONOMOUS_PROMPT not in append_text


class TestBuildOptionsEffort:
    """Tests for effort parameter in _build_options()."""

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_effort_passed_through(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet", effort="high")
        options = agent._build_options(tmp_path)
        assert options.effort == "high"

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_no_effort_not_set(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet")
        options = agent._build_options(tmp_path)
        assert not hasattr(options, "effort") or options.effort is None


class TestBuildOptionsThinking:
    """Tests for thinking token configuration in _build_options()."""

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_adaptive_thinking_uses_config(self, _mock_mcp, _mock_cli, tmp_path: Path):
        """Adaptive thinking (-1 sentinel) should use ThinkingConfigAdaptive."""
        agent = GluonAgent(model="sonnet", max_thinking_tokens=-1)
        options = agent._build_options(tmp_path)
        assert options.thinking == {"type": "adaptive"}

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_explicit_thinking_tokens_uses_enabled_config(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet", max_thinking_tokens=16000)
        options = agent._build_options(tmp_path)
        assert options.thinking == {"type": "enabled", "budget_tokens": 16000}

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_default_thinking_is_adaptive(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet")
        options = agent._build_options(tmp_path)
        assert options.thinking == {"type": "adaptive"}

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_zero_thinking_tokens_uses_disabled_config(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet", max_thinking_tokens=0)
        options = agent._build_options(tmp_path)
        assert options.thinking == {"type": "disabled"}


class TestBuildOptionsFallbackModel:
    """Tests for fallback model resolution in _build_options()."""

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_sonnet_has_haiku_fallback(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="sonnet")
        options = agent._build_options(tmp_path)
        assert options.fallback_model == MODEL_IDS[ModelTier.HAIKU]

    @patch("gluon.agent.find_claude_cli", return_value=Path("/usr/bin/claude"))
    @patch("gluon.agent.find_mcp_config", return_value=None)
    def test_haiku_has_no_fallback(self, _mock_mcp, _mock_cli, tmp_path: Path):
        agent = GluonAgent(model="haiku")
        options = agent._build_options(tmp_path)
        assert not hasattr(options, "fallback_model") or options.fallback_model is None


# ---------------------------------------------------------------------------
# ImageContent
# ---------------------------------------------------------------------------

# Minimal valid 1x1 PNG (67 bytes)
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestImageContent:
    def test_png_content_block(self, tmp_path: Path):
        img = tmp_path / "test.png"
        img.write_bytes(TINY_PNG)
        block = ImageContent(path=img).to_content_block()
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/png"
        assert base64.b64decode(block["source"]["data"]) == TINY_PNG

    def test_explicit_media_type(self, tmp_path: Path):
        img = tmp_path / "photo.dat"
        img.write_bytes(TINY_PNG)
        block = ImageContent(path=img, media_type="image/webp").to_content_block()
        assert block["source"]["media_type"] == "image/webp"

    def test_nonexistent_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            ImageContent(path=tmp_path / "missing.png").to_content_block()

    def test_jpeg_extension_guessed(self, tmp_path: Path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(TINY_PNG)  # content doesn't matter for type guessing
        block = ImageContent(path=img).to_content_block()
        assert block["source"]["media_type"] == "image/jpeg"


# ---------------------------------------------------------------------------
# MultimodalPrompt
# ---------------------------------------------------------------------------


class TestMultimodalPrompt:
    def test_text_only(self):
        prompt = MultimodalPrompt(text="hello")
        blocks = prompt.to_content_blocks()
        assert len(blocks) == 1
        assert blocks[0] == {"type": "text", "text": "hello"}

    def test_images_before_text(self, tmp_path: Path):
        img = tmp_path / "a.png"
        img.write_bytes(TINY_PNG)
        prompt = MultimodalPrompt(text="describe", images=[ImageContent(path=img)])
        blocks = prompt.to_content_blocks()
        assert len(blocks) == 2
        assert blocks[0]["type"] == "image"
        assert blocks[1] == {"type": "text", "text": "describe"}

    def test_empty_images_just_text(self):
        prompt = MultimodalPrompt(text="hi", images=[])
        blocks = prompt.to_content_blocks()
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"


# ---------------------------------------------------------------------------
# find_mcp_config
# ---------------------------------------------------------------------------


class TestFindMcpConfig:
    def test_project_level_found(self, tmp_path: Path):
        mcp = tmp_path / ".mcp.json"
        mcp.write_text("{}")
        assert find_mcp_config(tmp_path) == mcp

    def test_host_level_fallback(self, tmp_path: Path):
        host_mcp = tmp_path / ".claude" / ".mcp.json"
        host_mcp.parent.mkdir(parents=True)
        host_mcp.write_text("{}")
        with patch("gluon.agent.MCP_CONFIG_PATHS", [host_mcp]):
            assert find_mcp_config(tmp_path / "no-project") == host_mcp

    def test_no_config_returns_none(self, tmp_path: Path):
        with patch("gluon.agent.MCP_CONFIG_PATHS", []):
            assert find_mcp_config(tmp_path) is None

    def test_project_level_takes_priority(self, tmp_path: Path):
        project_mcp = tmp_path / ".mcp.json"
        project_mcp.write_text("{}")
        host_mcp = tmp_path / ".claude" / ".mcp.json"
        host_mcp.parent.mkdir(parents=True)
        host_mcp.write_text("{}")
        with patch("gluon.agent.MCP_CONFIG_PATHS", [host_mcp]):
            assert find_mcp_config(tmp_path) == project_mcp
