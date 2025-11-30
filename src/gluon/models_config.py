"""Model configuration for Gluon Agent."""

from enum import Enum


class ModelTier(str, Enum):
    """Model tiers for different task complexities."""

    OPUS = "opus"
    SONNET = "sonnet"
    HAIKU = "haiku"


# Model ID mappings to AWS Bedrock model IDs
MODEL_IDS = {
    ModelTier.OPUS: "global.anthropic.claude-opus-4-5-20251101-v1:0",
    ModelTier.SONNET: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ModelTier.HAIKU: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# Default model for general tasks
DEFAULT_MODEL = ModelTier.SONNET


def get_model_id(tier: ModelTier | str) -> str:
    """
    Get the full model ID for a given tier.

    Args:
        tier: Model tier (opus/sonnet/haiku) or ModelTier enum

    Returns:
        Full AWS Bedrock model ID

    Raises:
        ValueError: If the tier is invalid
    """
    if isinstance(tier, str):
        try:
            tier = ModelTier(tier.lower())
        except ValueError:
            raise ValueError(
                f"Invalid model tier: {tier}. Must be one of: {', '.join(t.value for t in ModelTier)}"
            )

    return MODEL_IDS[tier]


def describe_models() -> str:
    """
    Get a human-readable description of available models.

    Returns:
        Formatted string describing all available models
    """
    return """Available models:
- opus   : Claude Opus 4.5 - Most capable, for complex tasks
- sonnet : Claude Sonnet 4.5 - Balanced performance (default)
- haiku  : Claude Haiku 4.5 - Fast and efficient, for simple tasks"""
