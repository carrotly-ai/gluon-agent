"""Tests for LLM provider abstraction in llm_provider.py."""

from unittest.mock import patch

import pytest

from gluon.llm_provider import (
    AnthropicProvider,
    BedrockProvider,
    FoundryProvider,
    LLMProvider,
    LLMProviderConfig,
    VertexProvider,
    get_provider,
    get_provider_source,
)
from gluon.models_config import MODEL_ALIASES, ModelTier

ALL_PROVIDERS = [BedrockProvider, AnthropicProvider, VertexProvider, FoundryProvider]

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
# get_provider_source()
# ---------------------------------------------------------------------------


class TestGetProviderSource:
    """Tests for get_provider_source() resolution labelling."""

    def test_source_env_var(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "anthropic")
        assert get_provider_source() == "environment variable"

    def test_source_database(self, monkeypatch):
        monkeypatch.delenv("GLUON_LLM_PROVIDER", raising=False)
        monkeypatch.setattr("gluon.llm_provider._read_provider_setting", lambda: "anthropic")
        assert get_provider_source() == "database setting"

    def test_source_default(self, monkeypatch):
        monkeypatch.delenv("GLUON_LLM_PROVIDER", raising=False)
        monkeypatch.setattr("gluon.llm_provider._read_provider_setting", lambda: None)
        assert get_provider_source() == "default"

    def test_env_var_takes_priority_over_db(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "bedrock")
        monkeypatch.setattr("gluon.llm_provider._read_provider_setting", lambda: "anthropic")
        assert get_provider_source() == "environment variable"


# ---------------------------------------------------------------------------
# Cross-provider contract tests
# ---------------------------------------------------------------------------


class TestProviderContract:
    """Verify both providers satisfy the same behavioural contract."""

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_implements_abc(self, provider_cls):
        provider = provider_cls()
        assert isinstance(provider, LLMProviderConfig)

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_all_tiers_resolvable(self, provider_cls):
        provider = provider_cls()
        for tier in ModelTier:
            model_id = provider.get_model_id(tier)
            assert isinstance(model_id, str)
            assert len(model_id) > 0

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
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

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_own_model_ids_pass_is_full_check(self, provider_cls):
        """Provider's own model IDs should pass its is_full_model_id() check."""
        provider = provider_cls()
        for tier in ModelTier:
            model_id = provider.MODELS[tier]
            assert provider.is_full_model_id(model_id), f"{model_id} not recognised as full ID"

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_aliases_resolve_consistently(self, provider_cls):
        """UI aliases must resolve to a model ID in the provider's MODELS values."""
        provider = provider_cls()
        valid_ids = set(provider.MODELS.values())
        for alias in MODEL_ALIASES:
            resolved = provider.get_model_id(alias)
            assert resolved in valid_ids, f"Alias '{alias}' resolved to unknown ID: {resolved}"

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_name_is_non_empty_string(self, provider_cls):
        provider = provider_cls()
        assert isinstance(provider.name, str)
        assert len(provider.name) > 0

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_supports_cost_tracking_is_bool(self, provider_cls):
        provider = provider_cls()
        assert isinstance(provider.supports_cost_tracking, bool)

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_describe_models_is_non_empty(self, provider_cls):
        result = provider_cls().describe_models()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# VertexProvider
# ---------------------------------------------------------------------------


class TestVertexProvider:
    """Tests for VertexProvider implementation."""

    def test_name(self):
        assert VertexProvider().name == "Google Vertex AI"

    def test_supports_cost_tracking(self):
        assert VertexProvider().supports_cost_tracking is True

    @pytest.mark.parametrize("tier", list(ModelTier))
    def test_get_model_id_all_tiers(self, tier: ModelTier):
        provider = VertexProvider()
        model_id = provider.get_model_id(tier)
        assert isinstance(model_id, str)
        assert len(model_id) > 0

    def test_get_model_id_tier_string(self):
        provider = VertexProvider()
        assert provider.get_model_id("sonnet") == provider.MODELS[ModelTier.SONNET]

    def test_get_model_id_ui_alias(self):
        provider = VertexProvider()
        assert provider.get_model_id("claude-opus-4.6") == provider.MODELS[ModelTier.OPUS_46]

    def test_get_model_id_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid model"):
            VertexProvider().get_model_id("gpt-4")

    def test_haiku_uses_dated_variant(self):
        """Vertex publishes Haiku 4.5 as ``claude-haiku-4-5@20251001``."""
        assert VertexProvider().MODELS[ModelTier.HAIKU] == "claude-haiku-4-5@20251001"

    def test_sonnet_is_undated_alias(self):
        assert VertexProvider().MODELS[ModelTier.SONNET] == "claude-sonnet-4-6"

    def test_full_vertex_id_passthrough(self):
        vertex_id = "claude-opus-4-6"
        assert VertexProvider().get_model_id(vertex_id) == vertex_id

    def test_full_vertex_id_with_date_passthrough(self):
        vertex_id = "claude-haiku-4-5@20251001"
        assert VertexProvider().get_model_id(vertex_id) == vertex_id

    def test_is_full_model_id_accepts_dated(self):
        assert VertexProvider().is_full_model_id("claude-haiku-4-5@20251001")

    def test_is_full_model_id_accepts_undated(self):
        assert VertexProvider().is_full_model_id("claude-opus-4-6")

    def test_is_full_model_id_rejects_ui_alias(self):
        """UI aliases (claude-opus-4.6) have dots — must not be treated as full IDs."""
        assert not VertexProvider().is_full_model_id("claude-opus-4.6")

    def test_is_full_model_id_rejects_bedrock_id(self):
        assert not VertexProvider().is_full_model_id("global.anthropic.claude-sonnet-4-6")

    def test_create_api_client_requires_project_id(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_VERTEX_PROJECT_ID"):
            VertexProvider().create_api_client()

    def test_create_api_client_calls_async_vertex(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project")
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
        import anthropic

        with patch.object(anthropic, "AsyncAnthropicVertex") as mock_cls:
            VertexProvider().create_api_client()
            mock_cls.assert_called_once_with(project_id="my-project", region="us-east5")

    def test_create_api_client_defaults_to_global_region(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project")
        monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
        import anthropic

        with patch.object(anthropic, "AsyncAnthropicVertex") as mock_cls:
            VertexProvider().create_api_client()
            mock_cls.assert_called_once_with(project_id="my-project", region="global")

    def test_runner_env_sets_use_vertex_flag(self):
        env = VertexProvider().runner_env()
        assert env["CLAUDE_CODE_USE_VERTEX"] == "1"

    def test_runner_env_forwards_project_id(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-proj")
        monkeypatch.setenv("CLOUD_ML_REGION", "europe-west1")
        env = VertexProvider().runner_env()
        assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "my-proj"
        assert env["CLOUD_ML_REGION"] == "europe-west1"

    def test_runner_env_skips_unset_vars(self, monkeypatch):
        for k in (
            "ANTHROPIC_VERTEX_PROJECT_ID",
            "CLOUD_ML_REGION",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GCLOUD_PROJECT",
        ):
            monkeypatch.delenv(k, raising=False)
        env = VertexProvider().runner_env()
        # Only the USE flag should be present
        assert env == {"CLAUDE_CODE_USE_VERTEX": "1"}


# ---------------------------------------------------------------------------
# FoundryProvider
# ---------------------------------------------------------------------------


class TestFoundryProvider:
    """Tests for FoundryProvider implementation (Microsoft Foundry / Azure)."""

    def test_name(self):
        assert FoundryProvider().name == "Microsoft Foundry"

    def test_supports_cost_tracking(self):
        assert FoundryProvider().supports_cost_tracking is True

    @pytest.mark.parametrize("tier", list(ModelTier))
    def test_get_model_id_all_tiers(self, tier: ModelTier):
        provider = FoundryProvider()
        model_id = provider.get_model_id(tier)
        assert isinstance(model_id, str)
        assert len(model_id) > 0

    def test_get_model_id_tier_string(self):
        provider = FoundryProvider()
        assert provider.get_model_id("sonnet") == provider.MODELS[ModelTier.SONNET]

    def test_get_model_id_ui_alias(self):
        provider = FoundryProvider()
        assert provider.get_model_id("claude-opus-4.6") == provider.MODELS[ModelTier.OPUS_46]

    def test_model_ids_use_undated_deployment_names(self):
        """Foundry deployment names are dashed, no dots, no @date suffix."""
        for _tier, model_id in FoundryProvider().MODELS.items():
            assert model_id.startswith("claude-")
            assert "." not in model_id
            assert "@" not in model_id

    def test_full_foundry_id_passthrough(self):
        foundry_id = "claude-opus-4-7"
        assert FoundryProvider().get_model_id(foundry_id) == foundry_id

    def test_is_full_model_id_accepts_dashed(self):
        assert FoundryProvider().is_full_model_id("claude-opus-4-7")

    def test_is_full_model_id_rejects_ui_alias(self):
        assert not FoundryProvider().is_full_model_id("claude-opus-4.6")

    def test_is_full_model_id_rejects_vertex_dated(self):
        """Foundry IDs never carry the @date suffix (that's Vertex)."""
        assert not FoundryProvider().is_full_model_id("claude-haiku-4-5@20251001")

    def test_create_api_client_requires_resource_or_base_url(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_FOUNDRY_RESOURCE", raising=False)
        monkeypatch.delenv("ANTHROPIC_FOUNDRY_BASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_FOUNDRY_RESOURCE"):
            FoundryProvider().create_api_client()

    def test_create_api_client_with_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "sk-test")
        import anthropic

        with patch.object(anthropic, "AsyncAnthropicFoundry") as mock_cls:
            FoundryProvider().create_api_client()
            mock_cls.assert_called_once_with(resource="my-resource", api_key="sk-test")

    def test_create_api_client_without_api_key_uses_entra(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
        monkeypatch.delenv("ANTHROPIC_FOUNDRY_API_KEY", raising=False)
        import anthropic

        with patch.object(anthropic, "AsyncAnthropicFoundry") as mock_cls:
            FoundryProvider().create_api_client()
            # No api_key passed → SDK falls back to Azure default credential chain
            mock_cls.assert_called_once_with(resource="my-resource")

    def test_create_api_client_with_base_url(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_FOUNDRY_RESOURCE", raising=False)
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", "https://gw.example/anthropic")
        import anthropic

        with patch.object(anthropic, "AsyncAnthropicFoundry") as mock_cls:
            FoundryProvider().create_api_client()
            mock_cls.assert_called_once_with(base_url="https://gw.example/anthropic")

    def test_runner_env_sets_use_foundry_flag(self):
        env = FoundryProvider().runner_env()
        assert env["CLAUDE_CODE_USE_FOUNDRY"] == "1"

    def test_runner_env_forwards_resource_and_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-res")
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "sk-123")
        env = FoundryProvider().runner_env()
        assert env["ANTHROPIC_FOUNDRY_RESOURCE"] == "my-res"
        assert env["ANTHROPIC_FOUNDRY_API_KEY"] == "sk-123"


# ---------------------------------------------------------------------------
# get_provider() coverage for the new providers
# ---------------------------------------------------------------------------


class TestGetProviderVertexFoundry:
    """Ensure the factory and enum recognise vertex + foundry."""

    def test_env_var_vertex(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "vertex")
        assert isinstance(get_provider(), VertexProvider)

    def test_env_var_foundry(self, monkeypatch):
        monkeypatch.setenv("GLUON_LLM_PROVIDER", "foundry")
        assert isinstance(get_provider(), FoundryProvider)

    def test_enum_vertex(self):
        assert isinstance(get_provider(LLMProvider.VERTEX), VertexProvider)

    def test_enum_foundry(self):
        assert isinstance(get_provider(LLMProvider.FOUNDRY), FoundryProvider)

    def test_llm_provider_enum_has_four_values(self):
        assert {p.value for p in LLMProvider} == {"bedrock", "anthropic", "vertex", "foundry"}


# ---------------------------------------------------------------------------
# runner_env() contract
# ---------------------------------------------------------------------------


class TestRunnerEnv:
    """All providers must return a dict from runner_env() and obey the USE_* contract."""

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDERS)
    def test_returns_dict(self, provider_cls):
        env = provider_cls().runner_env()
        assert isinstance(env, dict)
        for k, v in env.items():
            assert isinstance(k, str) and isinstance(v, str)

    @pytest.mark.parametrize(
        "provider_cls,expected_flag",
        [
            (BedrockProvider, "CLAUDE_CODE_USE_BEDROCK"),
            (VertexProvider, "CLAUDE_CODE_USE_VERTEX"),
            (FoundryProvider, "CLAUDE_CODE_USE_FOUNDRY"),
        ],
    )
    def test_cloud_providers_set_use_flag(self, provider_cls, expected_flag):
        env = provider_cls().runner_env()
        assert env.get(expected_flag) == "1"

    def test_anthropic_provider_sets_no_use_flag(self):
        """Direct Anthropic API is Claude Code's default — no USE_* flag needed."""
        env = AnthropicProvider().runner_env()
        assert not any(k.startswith("CLAUDE_CODE_USE_") for k in env)


# ---------------------------------------------------------------------------
# Integration smoke — real `anthropic` SDK, no mocks
#
# The provider-unit tests above all mock the SDK clients to avoid network
# dependencies. That keeps them fast but also means a future `anthropic`
# release that renames, moves, or removes one of the client classes (or
# changes the `__init__` signature we pass through) would slip past CI and
# break at runtime on a real run.
#
# These tests import the real classes and actually call `create_api_client()`
# with dummy credentials. They don't make any network calls — they just
# construct the client object — but that's enough to catch the kind of break
# that mocks hide: missing exports, renamed ctor params, incompatible SDK
# versions.
# ---------------------------------------------------------------------------


class TestProviderRealSdkIntegration:
    """Exercise `create_api_client()` against the real `anthropic` package."""

    def test_bedrock_instantiates_real_client(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        from anthropic import AsyncAnthropicBedrock

        client = BedrockProvider().create_api_client()
        assert isinstance(client, AsyncAnthropicBedrock)

    def test_anthropic_instantiates_real_client(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")
        from anthropic import AsyncAnthropic

        client = AnthropicProvider().create_api_client()
        assert isinstance(client, AsyncAnthropic)

    def test_vertex_instantiates_real_client(self, monkeypatch):
        """Requires the `anthropic[vertex]` extra — verifies google-cloud-aiplatform ships."""
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
        monkeypatch.setenv("CLOUD_ML_REGION", "global")
        from anthropic import AsyncAnthropicVertex

        client = VertexProvider().create_api_client()
        assert isinstance(client, AsyncAnthropicVertex)

    def test_foundry_instantiates_real_client_with_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "test-resource")
        monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "test-key")
        from anthropic import AsyncAnthropicFoundry

        client = FoundryProvider().create_api_client()
        assert isinstance(client, AsyncAnthropicFoundry)

    def test_runner_env_propagates_via_subprocess(self, monkeypatch):
        """Sanity: env vars emitted by runner_env() actually reach a spawned subprocess.

        `runner.py` merges `provider.runner_env()` into the subprocess env when
        it detaches the run worker. If that plumbing ever regresses (e.g. the
        returned dict gets accidentally filtered somewhere), the Claude Code
        subprocess would route inference through the wrong backend without any
        CI signal. This test runs a subprocess with the merged env and asserts
        it observes the expected flag.
        """
        import subprocess
        import sys

        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "smoke-project")
        monkeypatch.setenv("CLOUD_ML_REGION", "europe-west1")

        merged_env = {**__import__("os").environ, **VertexProvider().runner_env()}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os; "
                    "print(os.environ.get('CLAUDE_CODE_USE_VERTEX'), "
                    "os.environ.get('ANTHROPIC_VERTEX_PROJECT_ID'), "
                    "os.environ.get('CLOUD_ML_REGION'))"
                ),
            ],
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"subprocess failed: {result.stderr}"
        assert result.stdout.strip() == "1 smoke-project europe-west1"
