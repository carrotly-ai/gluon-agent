"""Model configuration for Gluon Agent."""

from enum import Enum


class ModelTier(str, Enum):
    """Model tiers for different task complexities."""

    OPUS_46 = "opus-4.6"
    OPUS_45 = "opus-4.5"
    SONNET = "sonnet"
    HAIKU = "haiku"


# Model ID mappings to AWS Bedrock model IDs
MODEL_IDS = {
    ModelTier.OPUS_46: "global.anthropic.claude-opus-4-6-v1",
    ModelTier.OPUS_45: "global.anthropic.claude-opus-4-5-20251101-v1:0",
    ModelTier.SONNET: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ModelTier.HAIKU: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# UI model name aliases (maps UI names to tier names)
MODEL_ALIASES = {
    "claude-opus-4.6": ModelTier.OPUS_46,
    "claude-opus-4.5": ModelTier.OPUS_45,
    "opus": ModelTier.OPUS_46,
    "claude-sonnet-4.5": ModelTier.SONNET,
    "claude-haiku-4.5": ModelTier.HAIKU,
}

# Default model for general tasks
DEFAULT_MODEL = ModelTier.SONNET


def get_model_id(tier: ModelTier | str) -> str:
    """
    Get the full model ID for a given tier.

    Args:
        tier: Model tier (opus/sonnet/haiku), UI name (claude-opus-4.5), or ModelTier enum

    Returns:
        Full AWS Bedrock model ID

    Raises:
        ValueError: If the tier is invalid
    """
    if isinstance(tier, str):
        # Check if it's already a full Bedrock model ID
        if (
            tier.startswith("global.anthropic.")
            or tier.startswith("us.anthropic.")
            or tier.startswith("apac.anthropic.")
        ):
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

    return MODEL_IDS[tier]


def describe_models() -> str:
    """
    Get a human-readable description of available models.

    Returns:
        Formatted string describing all available models
    """
    return """Available models:
- opus-4.6 : Claude Opus 4.6 - Latest, most capable (default opus)
- opus-4.5 : Claude Opus 4.5 - Previous generation
- sonnet   : Claude Sonnet 4.5 - Balanced performance (default)
- haiku    : Claude Haiku 4.5 - Fast and efficient, for simple tasks"""
