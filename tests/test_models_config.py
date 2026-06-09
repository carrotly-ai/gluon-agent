"""Tests for model ID resolution and fallbacks in models_config.py."""

import pytest

from gluon.models_config import (
    FALLBACK_TIERS,
    MODEL_ALIASES,
    MODEL_IDS,
    ModelTier,
    describe_models,
    get_fallback_model_id,
    get_model_id,
)


class TestGetModelId:
    """Tests for get_model_id() resolution."""

    @pytest.mark.parametrize("tier", list(ModelTier))
    def test_each_model_tier_returns_provider_id(self, tier: ModelTier):
        result = get_model_id(tier)
        assert result == MODEL_IDS[tier]

    def test_tier_name_haiku(self):
        assert get_model_id("haiku") == MODEL_IDS[ModelTier.HAIKU]

    def test_tier_name_sonnet(self):
        assert get_model_id("sonnet") == MODEL_IDS[ModelTier.SONNET]

    def test_tier_name_opus_46(self):
        assert get_model_id("opus-4.6") == MODEL_IDS[ModelTier.OPUS_46]

    def test_tier_name_opus_48(self):
        assert get_model_id("opus-4.8") == MODEL_IDS[ModelTier.OPUS_48]

    def test_tier_name_opus_47(self):
        assert get_model_id("opus-4.7") == MODEL_IDS[ModelTier.OPUS_47]

    def test_ui_alias_claude_opus_46(self):
        assert get_model_id("claude-opus-4.6") == MODEL_IDS[ModelTier.OPUS_46]

    def test_ui_alias_claude_opus_48(self):
        assert get_model_id("claude-opus-4.8") == MODEL_IDS[ModelTier.OPUS_48]

    def test_ui_alias_claude_opus_47(self):
        assert get_model_id("claude-opus-4.7") == MODEL_IDS[ModelTier.OPUS_47]

    def test_ui_alias_claude_sonnet_46(self):
        assert get_model_id("claude-sonnet-4.6") == MODEL_IDS[ModelTier.SONNET]

    def test_ui_alias_claude_sonnet_45_maps_to_sonnet(self):
        """claude-sonnet-4.5 should map to the same tier as claude-sonnet-4.6 (backwards compat)."""
        assert get_model_id("claude-sonnet-4.5") == MODEL_IDS[ModelTier.SONNET]

    def test_ui_alias_claude_haiku_45(self):
        assert get_model_id("claude-haiku-4.5") == MODEL_IDS[ModelTier.HAIKU]

    def test_full_model_id_passthrough(self):
        """Full model IDs for the active provider pass through unchanged."""
        from gluon.llm_provider import get_provider

        provider = get_provider()
        for model_id in provider.MODELS.values():
            assert get_model_id(model_id) == model_id

    def test_bedrock_regional_prefixes_passthrough_on_bedrock(self, monkeypatch):
        """Bedrock regional prefixes (us., apac., global.) pass through on the Bedrock provider."""
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "bedrock")
        for bedrock_id in [
            "global.anthropic.claude-sonnet-4-6",
            "us.anthropic.claude-sonnet-4-6",
            "apac.anthropic.claude-3-7-sonnet-20250219-v1:0",
        ]:
            assert get_model_id(bedrock_id) == bedrock_id

    def test_case_insensitive_haiku(self):
        assert get_model_id("HAIKU") == MODEL_IDS[ModelTier.HAIKU]

    def test_case_insensitive_sonnet(self):
        assert get_model_id("Sonnet") == MODEL_IDS[ModelTier.SONNET]

    def test_case_insensitive_alias(self):
        assert get_model_id("Claude-Opus-4.6") == MODEL_IDS[ModelTier.OPUS_46]

    def test_invalid_model_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid model"):
            get_model_id("gpt-4")

    def test_invalid_model_error_includes_valid_options(self):
        with pytest.raises(ValueError) as exc_info:
            get_model_id("nonexistent")
        error_msg = str(exc_info.value)
        # Should mention valid tier names
        assert "haiku" in error_msg
        assert "sonnet" in error_msg


class TestGetFallbackModelId:
    """Tests for get_fallback_model_id() fallback chain."""

    def test_opus_46_falls_back_to_sonnet(self):
        result = get_fallback_model_id(ModelTier.OPUS_46)
        assert result == MODEL_IDS[ModelTier.SONNET]

    def test_opus_48_falls_back_to_sonnet(self):
        result = get_fallback_model_id(ModelTier.OPUS_48)
        assert result == MODEL_IDS[ModelTier.SONNET]

    def test_opus_47_falls_back_to_sonnet(self):
        result = get_fallback_model_id(ModelTier.OPUS_47)
        assert result == MODEL_IDS[ModelTier.SONNET]

    def test_sonnet_falls_back_to_haiku(self):
        result = get_fallback_model_id(ModelTier.SONNET)
        assert result == MODEL_IDS[ModelTier.HAIKU]

    def test_haiku_falls_back_to_none(self):
        result = get_fallback_model_id(ModelTier.HAIKU)
        assert result is None

    def test_string_tier_name(self):
        result = get_fallback_model_id("opus-4.6")
        assert result == MODEL_IDS[ModelTier.SONNET]

    def test_full_model_id_input(self):
        model_id = MODEL_IDS[ModelTier.SONNET]
        result = get_fallback_model_id(model_id)
        assert result == MODEL_IDS[ModelTier.HAIKU]

    def test_ui_alias_input(self):
        result = get_fallback_model_id("claude-opus-4.8")
        assert result == MODEL_IDS[ModelTier.SONNET]

    def test_unknown_model_returns_none(self):
        result = get_fallback_model_id("gpt-4-turbo")
        assert result is None


class TestDescribeModels:
    """Tests for describe_models() output."""

    def test_returns_non_empty_string(self):
        result = describe_models()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_all_tier_names(self):
        result = describe_models()
        for tier in ModelTier:
            assert tier.value in result


class TestModelAliasesConsistency:
    """Tests for MODEL_ALIASES mapping consistency."""

    def test_sonnet_45_and_46_map_to_same_tier(self):
        assert MODEL_ALIASES["claude-sonnet-4.5"] == MODEL_ALIASES["claude-sonnet-4.6"]

    def test_opus_alias_maps_to_opus_48(self):
        assert MODEL_ALIASES["opus"] == ModelTier.OPUS_48

    def test_all_aliases_map_to_valid_tiers(self):
        for alias, tier in MODEL_ALIASES.items():
            assert tier in MODEL_IDS, f"Alias '{alias}' maps to tier not in MODEL_IDS"

    def test_fallback_chain_has_entry_for_all_tiers(self):
        for tier in ModelTier:
            assert tier in FALLBACK_TIERS
