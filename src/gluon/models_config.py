"""Model configuration for Gluon Agent.

Model tier definitions, aliases, and fallback chains are provider-agnostic.
Model ID resolution delegates to the active LLM provider (Bedrock or Anthropic).
"""

from enum import StrEnum


class ModelTier(StrEnum):
    """Model tiers for different task complexities."""

    OPUS_48 = "opus-4.8"
    OPUS_47 = "opus-4.7"
    OPUS_46 = "opus-4.6"
    SONNET = "sonnet"
    HAIKU = "haiku"


# UI model name aliases (maps UI names to tier names)
MODEL_ALIASES = {
    "claude-opus-4.8": ModelTier.OPUS_48,
    "claude-opus-4.7": ModelTier.OPUS_47,
    "claude-opus-4.6": ModelTier.OPUS_46,
    "opus": ModelTier.OPUS_48,
    "claude-sonnet-4.6": ModelTier.SONNET,
    "claude-sonnet-4.5": ModelTier.SONNET,
    "claude-haiku-4.5": ModelTier.HAIKU,
}

# Fallback tier mapping: model → next tier down for graceful degradation
# When primary model hits rate limits or is unavailable, SDK falls back automatically
FALLBACK_TIERS: dict[ModelTier, ModelTier | None] = {
    ModelTier.OPUS_48: ModelTier.SONNET,
    ModelTier.OPUS_47: ModelTier.SONNET,
    ModelTier.OPUS_46: ModelTier.SONNET,
    ModelTier.SONNET: ModelTier.HAIKU,
    ModelTier.HAIKU: None,  # No fallback for cheapest model
}

# Default model for general tasks
DEFAULT_MODEL = ModelTier.OPUS_48


def _get_model_ids() -> dict[ModelTier, str]:
    """Get model ID mappings from the active LLM provider.

    Returns a dict mapping ModelTier → provider-specific model ID string.
    This is evaluated lazily to avoid circular imports at module load time.
    """
    from gluon.llm_provider import get_provider

    return dict(get_provider().MODELS)


class _ModelIDsProxy:
    """Lazy proxy for MODEL_IDS that delegates to the active provider.

    Behaves like a dict but resolves model IDs from the active provider
    on each access, so switching GLUON_LLM_PROVIDER takes effect immediately.
    """

    def __getitem__(self, key: ModelTier) -> str:
        return _get_model_ids()[key]

    def __contains__(self, key: object) -> bool:
        return key in _get_model_ids()

    def __iter__(self):
        return iter(_get_model_ids())

    def items(self):
        return _get_model_ids().items()

    def keys(self):
        return _get_model_ids().keys()

    def values(self):
        return _get_model_ids().values()

    def get(self, key: ModelTier, default=None):
        return _get_model_ids().get(key, default)

    def __repr__(self) -> str:
        return repr(_get_model_ids())


# Model ID mappings — delegates to the active LLM provider
MODEL_IDS: dict[ModelTier, str] = _ModelIDsProxy()  # type: ignore[assignment]


def get_model_id(tier: ModelTier | str) -> str:
    """Get the full model ID for a given tier.

    Delegates to the active LLM provider for model ID resolution.

    Args:
        tier: Model tier (opus/sonnet/haiku), UI name (claude-opus-4.8), or ModelTier enum

    Returns:
        Full provider-specific model ID

    Raises:
        ValueError: If the tier is invalid
    """
    from gluon.llm_provider import get_provider

    return get_provider().get_model_id(tier)


def get_fallback_model_id(model: str) -> str | None:
    """Get the fallback model ID for graceful degradation.

    Maps a model (tier name, alias, or full model ID) to its fallback tier's
    model ID. Returns None if no fallback exists.

    Args:
        model: Model tier, UI name, or full model ID

    Returns:
        Fallback model ID, or None if no fallback
    """
    from gluon.llm_provider import get_provider

    return get_provider().get_fallback_model_id(model)


def describe_models() -> str:
    """Get a human-readable description of available models.

    Returns:
        Formatted string describing all available models
    """
    from gluon.llm_provider import get_provider

    return get_provider().describe_models()
