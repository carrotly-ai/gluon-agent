"""Tests for LLM provider abstraction in llm_provider.py."""

from unittest.mock import patch

import pytest

from gluon.llm_provider import (
    AnthropicProvider,
    BedrockProvider,
    LLMProvider,
    LLMProviderConfig,
    get_provider,
)
from gluon.models_config import MODEL_ALIASES, ModelTier

# ---------------------------------------------------------------------------
# BedrockProvider
# ---------------------------------------------------------------------------


class TestBedrockProvider:
    """Tests for BedrockProvider implementation."""

    def test_name(self):
        assert BedrockProvider().name == "AWS Bedrock"

    def test_supports_cost_tracking(self):
        assert BedrockProvider().supports_cost_tracking is True

    @pytest.mark.parametrize("tier", list(ModelTier))
    def test_get_model_id_all_tiers(self, tier: ModelTier):
        provider = BedrockProvider()
        model_id = provider.get_model_id(tier)
        assert isinstance(model_id, str)
        assert len(model_id) > 0

    def test_get_model_id_tier_string(self):
        provider = BedrockProvider()
        assert provider.get_model_id("sonnet") == provider.MODELS[ModelTier.SONNET]

    def test_get_model_id_ui_alias(self):
        provider = BedrockProvider()
        assert provider.get_model_id("claude-opus-4.6") == provider.MODELS[ModelTier.OPUS_46]

    def test_get_model_id_case_insensitive(self):
        provider = BedrockProvider()
        assert provider.get_model_id("HAIKU") == provider.MODELS[ModelTier.HAIKU]

    def test_get_model_id_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid model"):
            BedrockProvider().get_model_id("gpt-4")

    def test_full_bedrock_id_global_passthrough(self):
        bedrock_id = "global.anthropic.claude-sonnet-4-6"
        assert BedrockProvider().get_model_id(bedrock_id) == bedrock_id

    def test_full_bedrock_id_us_passthrough(self):
        bedrock_id = "us.anthropic.claude-sonnet-4-6"
        assert BedrockProvider().get_model_id(bedrock_id) == bedrock_id

    def test_full_bedrock_id_apac_passthrough(self):
        bedrock_id = "apac.anthropic.claude-3-7-sonnet-20250219-v1:0"
        assert BedrockProvider().get_model_id(bedrock_id) == bedrock_id

    def test_is_full_model_id_global(self):
        assert BedrockProvider().is_full_model_id("global.anthropic.claude-sonnet-4-6")

    def test_is_full_model_id_us(self):
        assert BedrockProvider().is_full_model_id("us.anthropic.claude-opus-4-6-v1")

    def test_is_full_model_id_rejects_anthropic_id(self):
        assert not BedrockProvider().is_full_model_id("claude-sonnet-4-6")

    def test_is_full_model_id_rejects_random_string(self):
        assert not BedrockProvider().is_full_model_id("gpt-4")

    def test_fallback_opus_46_to_sonnet(self):
        provider = BedrockProvider()
        fallback = provider.get_fallback_model_id(provider.MODELS[ModelTier.OPUS_46])
        assert fallback == provider.MODELS[ModelTier.SONNET]

    def test_fallback_opus_45_to_sonnet(self):
        provider = BedrockProvider()
        fallback = provider.get_fallback_model_id(provider.MODELS[ModelTier.OPUS_45])
        assert fallback == provider.MODELS[ModelTier.SONNET]

    def test_fallback_sonnet_to_haiku(self):
        provider = BedrockProvider()
        fallback = provider.get_fallback_model_id(provider.MODELS[ModelTier.SONNET])
        assert fallback == provider.MODELS[ModelTier.HAIKU]

    def test_fallback_haiku_is_none(self):
        provider = BedrockProvider()
        assert provider.get_fallback_model_id(provider.MODELS[ModelTier.HAIKU]) is None

    def test_fallback_unknown_returns_none(self):
        assert BedrockProvider().get_fallback_model_id("gpt-4-turbo") is None

    def test_describe_models_contains_all_tiers(self):
        result = BedrockProvider().describe_models()
        for tier in ModelTier:
            assert tier.value in result

    def test_create_api_client_returns_bedrock_client(self):
        import anthropic

        with patch.object(anthropic, "AsyncAnthropicBedrock") as mock_cls:
            BedrockProvider().create_api_client()
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    """Tests for AnthropicProvider implementation."""

    def test_name(self):
        assert AnthropicProvider().name == "Anthropic"

    def test_supports_cost_tracking(self):
        assert AnthropicProvider().supports_cost_tracking is True

    @pytest.mark.parametrize("tier", list(ModelTier))
    def test_get_model_id_all_tiers(self, tier: ModelTier):
        provider = AnthropicProvider()
        model_id = provider.get_model_id(tier)
        assert model_id.startswith("claude-")

    def test_get_model_id_tier_string(self):
        provider = AnthropicProvider()
        assert provider.get_model_id("sonnet") == provider.MODELS[ModelTier.SONNET]

    def test_get_model_id_ui_alias(self):
        provider = AnthropicProvider()
        assert provider.get_model_id("claude-opus-4.6") == provider.MODELS[ModelTier.OPUS_46]

    def test_get_model_id_case_insensitive(self):
        provider = AnthropicProvider()
        assert provider.get_model_id("HAIKU") == provider.MODELS[ModelTier.HAIKU]

    def test_get_model_id_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid model"):
            AnthropicProvider().get_model_id("gpt-4")

    def test_full_anthropic_id_passthrough(self):
        anthropic_id = "claude-sonnet-4-6"
        assert AnthropicProvider().get_model_id(anthropic_id) == anthropic_id

    def test_is_full_model_id_claude_prefix(self):
        assert AnthropicProvider().is_full_model_id("claude-opus-4-6")

    def test_is_full_model_id_rejects_bedrock_id(self):
        assert not AnthropicProvider().is_full_model_id("global.anthropic.claude-sonnet-4-6")

    def test_is_full_model_id_rejects_random_string(self):
        assert not AnthropicProvider().is_full_model_id("gpt-4")

    def test_fallback_opus_46_to_sonnet(self):
        provider = AnthropicProvider()
        fallback = provider.get_fallback_model_id(provider.MODELS[ModelTier.OPUS_46])
        assert fallback == provider.MODELS[ModelTier.SONNET]

    def test_fallback_opus_45_to_sonnet(self):
        provider = AnthropicProvider()
        fallback = provider.get_fallback_model_id(provider.MODELS[ModelTier.OPUS_45])
        assert fallback == provider.MODELS[ModelTier.SONNET]

    def test_fallback_sonnet_to_haiku(self):
        provider = AnthropicProvider()
        fallback = provider.get_fallback_model_id(provider.MODELS[ModelTier.SONNET])
        assert fallback == provider.MODELS[ModelTier.HAIKU]

    def test_fallback_haiku_is_none(self):
        provider = AnthropicProvider()
        assert provider.get_fallback_model_id(provider.MODELS[ModelTier.HAIKU]) is None

    def test_fallback_unknown_returns_none(self):
        assert AnthropicProvider().get_fallback_model_id("gpt-4-turbo") is None

    def test_describe_models_contains_all_tiers(self):
        result = AnthropicProvider().describe_models()
        for tier in ModelTier:
            assert tier.value in result

    def test_create_api_client_returns_anthropic_client(self):
        import anthropic

        with patch.object(anthropic, "AsyncAnthropic") as mock_cls:
            AnthropicProvider().create_api_client()
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


class TestGetProvider:
    """Tests for get_provider() factory function."""

    def test_default_is_bedrock(self, monkeypatch):
        monkeypatch.delenv("GLUON_LLM_PROVIDER", raising=False)
        monkeypatch.setattr("gluon.llm_provider._read_provider_setting", lambda: None)
        provider = get_provider()
        assert isinstance(provider, BedrockProvider)

    def test_env_var_anthropic(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "anthropic")
        provider = get_provider()
        assert isinstance(provider, AnthropicProvider)

    def test_env_var_bedrock(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "bedrock")
        provider = get_provider()
        assert isinstance(provider, BedrockProvider)

    def test_env_var_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "ANTHROPIC")
        provider = get_provider()
        assert isinstance(provider, AnthropicProvider)

    def test_explicit_argument_overrides_env(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "bedrock")
        provider = get_provider("anthropic")
        assert isinstance(provider, AnthropicProvider)

    def test_enum_argument(self):
        provider = get_provider(LLMProvider.ANTHROPIC)
        assert isinstance(provider, AnthropicProvider)

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("openai")

    def test_store_setting_used_when_no_env_var(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GLUON_LLM_PROVIDER", raising=False)
        from gluon.store import GluonStore

        store = GluonStore(tmp_path / "test.db")
        store.set_setting("llm_provider", "anthropic")
        monkeypatch.setattr("gluon.llm_provider._read_provider_setting", lambda: store.get_setting("llm_provider"))
        provider = get_provider()
        assert isinstance(provider, AnthropicProvider)

    def test_env_var_overrides_store_setting(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "bedrock")
        from gluon.store import GluonStore

        store = GluonStore(tmp_path / "test.db")
        store.set_setting("llm_provider", "anthropic")
        monkeypatch.setattr("gluon.llm_provider._read_provider_setting", lambda: store.get_setting("llm_provider"))
        provider = get_provider()
        assert isinstance(provider, BedrockProvider)

    def test_no_store_setting_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("GLUON_LLM_PROVIDER", raising=False)
        monkeypatch.setattr("gluon.llm_provider._read_provider_setting", lambda: None)
        provider = get_provider()
        assert isinstance(provider, BedrockProvider)


# ---------------------------------------------------------------------------
# Cross-provider contract tests
# ---------------------------------------------------------------------------


class TestProviderContract:
    """Verify both providers satisfy the same behavioural contract."""

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_implements_abc(self, provider_cls):
        provider = provider_cls()
        assert isinstance(provider, LLMProviderConfig)

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_all_tiers_resolvable(self, provider_cls):
        provider = provider_cls()
        for tier in ModelTier:
            model_id = provider.get_model_id(tier)
            assert isinstance(model_id, str)
            assert len(model_id) > 0

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_fallback_chain_terminates(self, provider_cls):
        """Fallback chain must eventually return None (no infinite loops)."""
        provider = provider_cls()
        for tier in ModelTier:
            model_id = provider.MODELS[tier]
            seen = {model_id}
            current = model_id
            while True:
                fallback = provider.get_fallback_model_id(current)
                if fallback is None:
                    break
                assert fallback not in seen, f"Circular fallback: {fallback}"
                seen.add(fallback)
                current = fallback

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_own_model_ids_pass_is_full_check(self, provider_cls):
        """Provider's own model IDs should pass its is_full_model_id() check."""
        provider = provider_cls()
        for tier in ModelTier:
            model_id = provider.MODELS[tier]
            assert provider.is_full_model_id(model_id), f"{model_id} not recognised as full ID"

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_aliases_resolve_consistently(self, provider_cls):
        """UI aliases must resolve to a model ID in the provider's MODELS values."""
        provider = provider_cls()
        valid_ids = set(provider.MODELS.values())
        for alias in MODEL_ALIASES:
            resolved = provider.get_model_id(alias)
            assert resolved in valid_ids, f"Alias '{alias}' resolved to unknown ID: {resolved}"

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_name_is_non_empty_string(self, provider_cls):
        provider = provider_cls()
        assert isinstance(provider.name, str)
        assert len(provider.name) > 0

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_supports_cost_tracking_is_bool(self, provider_cls):
        provider = provider_cls()
        assert isinstance(provider.supports_cost_tracking, bool)

    @pytest.mark.parametrize("provider_cls", [BedrockProvider, AnthropicProvider])
    def test_describe_models_is_non_empty(self, provider_cls):
        result = provider_cls().describe_models()
        assert isinstance(result, str)
        assert len(result) > 0
