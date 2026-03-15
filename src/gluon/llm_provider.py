"""LLM provider abstraction for Gluon Agent.

Provides a provider interface (ABC) with implementations for AWS Bedrock
and direct Anthropic API. The active provider determines model ID resolution,
fallback chains, and API client creation.

Provider selection order:
1. Explicit argument to get_provider()
2. GLUON_LLM_PROVIDER environment variable
3. Stored setting in database (llm_provider)
4. Default: "bedrock"
"""

import os
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from gluon.models_config import FALLBACK_TIERS, MODEL_ALIASES, ModelTier


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    BEDROCK = "bedrock"
    ANTHROPIC = "anthropic"


class LLMProviderConfig(ABC):
    """Provider-specific model resolution and API client creation.

    This is NOT an LLM client — it is a configuration/factory that adapts
    Gluon's model tier system to a specific provider's model ID scheme
    and API client.
    """

    # Subclasses must define MODELS mapping ModelTier → provider-specific model ID
    MODELS: dict[ModelTier, str]

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for display."""
        ...

    @property
    @abstractmethod
    def supports_cost_tracking(self) -> bool:
        """Whether this provider reports meaningful cost data."""
        ...

    @abstractmethod
    def is_full_model_id(self, model_id: str) -> bool:
        """Check if a string is already a full model ID for this provider."""
        ...

    @abstractmethod
    def create_api_client(self) -> Any:
        """Create a direct API client (for witness, health checks, etc.)."""
        ...

    def get_model_id(self, tier: ModelTier | str) -> str:
        """Resolve a model tier or alias to this provider's model ID.

        Args:
            tier: Model tier (opus/sonnet/haiku), UI name (claude-opus-4.5),
                  or ModelTier enum

        Returns:
            Full provider-specific model ID

        Raises:
            ValueError: If the tier is invalid
        """
        if isinstance(tier, str):
            # Check if it's already a full model ID for this provider
            if self.is_full_model_id(tier):
                return tier

            tier_lower = tier.lower()

            # Check UI aliases first (e.g., "claude-haiku-4.5")
            if tier_lower in MODEL_ALIASES:
                tier = MODEL_ALIASES[tier_lower]
            else:
                # Try as tier name (e.g., "haiku")
                try:
                    tier = ModelTier(tier_lower)
                except ValueError:
                    raise ValueError(
                        f"Invalid model: {tier}. Must be one of: "
                        f"{', '.join(t.value for t in ModelTier)} or "
                        f"{', '.join(MODEL_ALIASES.keys())}"
                    )

        return self.MODELS[tier]

    def get_fallback_model_id(self, model: str) -> str | None:
        """Get the fallback model ID for graceful degradation.

        Args:
            model: Model tier, UI name, or full model ID

        Returns:
            Fallback model ID, or None if no fallback
        """
        # Resolve to a ModelTier
        tier: ModelTier | None = None

        if isinstance(model, ModelTier):
            tier = model
        else:
            model_lower = model.lower()
            # Check if it's a full model ID - reverse lookup
            for t, provider_id in self.MODELS.items():
                if provider_id == model:
                    tier = t
                    break

            if tier is None:
                # Check UI aliases
                if model_lower in MODEL_ALIASES:
                    tier = MODEL_ALIASES[model_lower]
                else:
                    try:
                        tier = ModelTier(model_lower)
                    except ValueError:
                        return None

        fallback_tier = FALLBACK_TIERS.get(tier)
        if fallback_tier is None:
            return None

        return self.MODELS[fallback_tier]

    def describe_models(self) -> str:
        """Get a human-readable description of available models."""
        return """Available models:
- opus-4.6 : Claude Opus 4.6 - Latest, most capable (default opus)
- opus-4.5 : Claude Opus 4.5 - Previous generation
- sonnet   : Claude Sonnet 4.6 - Balanced performance (default)
- haiku    : Claude Haiku 4.5 - Fast and efficient, for simple tasks"""


class BedrockProvider(LLMProviderConfig):
    """AWS Bedrock provider — existing behaviour, unchanged."""

    MODELS = {
        ModelTier.OPUS_46: "global.anthropic.claude-opus-4-6-v1",
        ModelTier.OPUS_45: "global.anthropic.claude-opus-4-5-20251101-v1:0",
        ModelTier.SONNET: "global.anthropic.claude-sonnet-4-6",
        ModelTier.HAIKU: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    }

    @property
    def name(self) -> str:
        return "AWS Bedrock"

    @property
    def supports_cost_tracking(self) -> bool:
        return True

    def is_full_model_id(self, model_id: str) -> bool:
        return (
            model_id.startswith("global.anthropic.")
            or model_id.startswith("us.anthropic.")
            or model_id.startswith("apac.anthropic.")
        )

    def create_api_client(self) -> Any:
        import anthropic

        return anthropic.AsyncAnthropicBedrock(
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
        )


class AnthropicProvider(LLMProviderConfig):
    """Direct Anthropic API / Claude CLI subscription provider."""

    MODELS = {
        ModelTier.OPUS_46: "claude-opus-4-6",
        ModelTier.OPUS_45: "claude-opus-4-5-20251101",
        ModelTier.SONNET: "claude-sonnet-4-6",
        ModelTier.HAIKU: "claude-haiku-4-5-20251001",
    }

    @property
    def name(self) -> str:
        return "Anthropic"

    @property
    def supports_cost_tracking(self) -> bool:
        return True

    def is_full_model_id(self, model_id: str) -> bool:
        # Full Anthropic model IDs start with "claude-" and use dashes (e.g., "claude-opus-4-6")
        # UI aliases use dots (e.g., "claude-opus-4.6") — these must NOT match
        if not model_id.startswith("claude-"):
            return False
        return "." not in model_id

    def create_api_client(self) -> Any:
        import anthropic

        return anthropic.AsyncAnthropic()


# ---------------------------------------------------------------------------
# Provider registry and factory
# ---------------------------------------------------------------------------

_PROVIDERS: dict[LLMProvider, type[LLMProviderConfig]] = {
    LLMProvider.BEDROCK: BedrockProvider,
    LLMProvider.ANTHROPIC: AnthropicProvider,
}


def _read_provider_setting() -> str | None:
    """Read llm_provider setting from the store."""
    from gluon.store import GluonStore

    try:
        return GluonStore().get_setting("llm_provider")
    except Exception:
        return None


def get_provider(provider: LLMProvider | str | None = None) -> LLMProviderConfig:
    """Get the active LLM provider configuration.

    Resolution order:
    1. Explicit argument (if provided)
    2. GLUON_LLM_PROVIDER environment variable
    3. Stored setting in database (llm_provider)
    4. Default: bedrock
    """
    if provider is None:
        provider = os.environ.get("GLUON_LLM_PROVIDER") or _read_provider_setting() or "bedrock"

    if isinstance(provider, str):
        try:
            provider = LLMProvider(provider.lower())
        except ValueError:
            raise ValueError(
                f"Unknown provider: {provider}. Available: {', '.join(p.value for p in LLMProvider)}"
            ) from None

    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(f"Unknown provider: {provider}. Available: {', '.join(p.value for p in LLMProvider)}")
    return cls()
