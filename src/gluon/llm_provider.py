"""LLM provider abstraction for Gluon Agent.

Provides a provider interface (ABC) with implementations for:

- **AWS Bedrock** (``BedrockProvider``) — default, unchanged historical behaviour
- **Direct Anthropic API / Claude CLI subscription** (``AnthropicProvider``)
- **Google Vertex AI** (``VertexProvider``)
- **Microsoft Foundry / Azure AI Foundry** (``FoundryProvider``)

The active provider determines:

- Model ID resolution (tier → provider-specific ID)
- Fallback chain for graceful degradation
- Direct API client creation (used by the witness health classifier)
- Environment variables that must be exported into the Claude Code subprocess
  so it routes inference through the correct backend (``runner_env()``)

Provider selection order:

1. Explicit argument to :func:`get_provider`
2. ``GLUON_LLM_PROVIDER`` environment variable
3. Stored setting in the database (``llm_provider``)
4. Default: ``"bedrock"``
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from gluon.models_config import FALLBACK_TIERS, MODEL_ALIASES, ModelTier


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    BEDROCK = "bedrock"
    ANTHROPIC = "anthropic"
    VERTEX = "vertex"
    FOUNDRY = "foundry"


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

    def runner_env(self) -> dict[str, str]:
        """Environment variables to inject into the Claude Code subprocess.

        Claude Code reads ``CLAUDE_CODE_USE_BEDROCK`` / ``CLAUDE_CODE_USE_VERTEX`` /
        ``CLAUDE_CODE_USE_FOUNDRY`` to decide which backend to route inference
        through — so whichever provider is active, we must export the matching
        flag (plus any provider-specific config) when we spawn the CLI.

        Base class returns an empty dict (used by :class:`AnthropicProvider`,
        which needs no special flag — direct API is the Claude Code default).
        Subclasses override to contribute the right env.

        Returns:
            Dict of env var name → value to merge into the subprocess env.
            Empty values are skipped so callers never set a var to ``""``.
        """
        return {}

    def get_model_id(self, tier: ModelTier | str) -> str:
        """Resolve a model tier or alias to this provider's model ID.

        Args:
            tier: Model tier (opus/sonnet/haiku), UI name (claude-opus-4.8),
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
- opus-4.8 : Claude Opus 4.8 - Latest, most capable (default)
- opus-4.7 : Claude Opus 4.7 - Previous generation
- opus-4.6 : Claude Opus 4.6 - Previous generation
- sonnet   : Claude Sonnet 4.6 - Balanced performance
- haiku    : Claude Haiku 4.5 - Fast and efficient, for simple tasks"""


def _passthrough(env: dict[str, str], keys: tuple[str, ...]) -> None:
    """Copy present, non-empty env vars from os.environ into ``env`` in-place."""
    for k in keys:
        v = os.environ.get(k)
        if v:
            env[k] = v


# ---------------------------------------------------------------------------
# Bedrock — AWS
# ---------------------------------------------------------------------------


class BedrockProvider(LLMProviderConfig):
    """AWS Bedrock provider."""

    MODELS = {
        ModelTier.OPUS_48: "global.anthropic.claude-opus-4-8",
        ModelTier.OPUS_47: "global.anthropic.claude-opus-4-7",
        ModelTier.OPUS_46: "global.anthropic.claude-opus-4-6-v1",
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

    def runner_env(self) -> dict[str, str]:
        env: dict[str, str] = {"CLAUDE_CODE_USE_BEDROCK": "1"}
        _passthrough(
            env,
            (
                "AWS_REGION",
                "AWS_PROFILE",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_BEARER_TOKEN_BEDROCK",
                "ANTHROPIC_BEDROCK_BASE_URL",
                "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
            ),
        )
        return env


# ---------------------------------------------------------------------------
# Anthropic — direct API / Claude CLI subscription
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProviderConfig):
    """Direct Anthropic API / Claude CLI subscription provider."""

    MODELS = {
        ModelTier.OPUS_48: "claude-opus-4-8",
        ModelTier.OPUS_47: "claude-opus-4-7",
        ModelTier.OPUS_46: "claude-opus-4-6",
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
        # UI aliases use dots (e.g., "claude-opus-4.6") — these must NOT match.
        # Bedrock / Vertex IDs are also "claude-..." shaped but include dots or @ suffixes.
        if not model_id.startswith("claude-"):
            return False
        return "." not in model_id and "@" not in model_id

    def create_api_client(self) -> Any:
        import anthropic

        return anthropic.AsyncAnthropic()

    def runner_env(self) -> dict[str, str]:
        # Direct API is Claude Code's default — no USE_* flag needed. We only
        # forward the API key so the CLI can reach the Anthropic API (if one
        # isn't already configured via ``claude login``).
        env: dict[str, str] = {}
        _passthrough(env, ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"))
        return env


# ---------------------------------------------------------------------------
# Google Vertex AI
# ---------------------------------------------------------------------------


class VertexProvider(LLMProviderConfig):
    """Google Vertex AI provider.

    Authentication uses Google Cloud Application Default Credentials (ADC).
    Run ``gcloud auth application-default login`` on the host, or provide a
    service account key via ``GOOGLE_APPLICATION_CREDENTIALS``.

    Configuration:

    - ``ANTHROPIC_VERTEX_PROJECT_ID`` — your GCP project ID (required)
    - ``CLOUD_ML_REGION`` — ``global``, ``us``, ``eu``, or a specific region
      like ``us-east5`` / ``europe-west1`` (default ``global``)

    Model IDs on Vertex use a ``claude-<model>-<tier>`` scheme optionally
    suffixed with ``@<YYYYMMDD>`` for pinning to a specific dated version.
    """

    MODELS = {
        # Opus and Sonnet resolve to "latest" aliases on Vertex (no @date)
        ModelTier.OPUS_48: "claude-opus-4-8",
        ModelTier.OPUS_47: "claude-opus-4-7",
        ModelTier.OPUS_46: "claude-opus-4-6",
        ModelTier.SONNET: "claude-sonnet-4-6",
        # Haiku 4.5 on Vertex is published as the dated variant
        ModelTier.HAIKU: "claude-haiku-4-5@20251001",
    }

    @property
    def name(self) -> str:
        return "Google Vertex AI"

    @property
    def supports_cost_tracking(self) -> bool:
        return True  # Reported via GCP Billing + Vertex usage metadata

    def is_full_model_id(self, model_id: str) -> bool:
        """Vertex IDs look like ``claude-opus-4-6`` or ``claude-haiku-4-5@20251001``.

        They start with ``claude-``, use dashes (not dots), and may include an
        ``@DATE`` suffix. We exclude IDs containing dots to avoid confusion
        with our UI aliases such as ``claude-opus-4.6``.
        """
        if not model_id.startswith("claude-"):
            return False
        return "." not in model_id

    def create_api_client(self) -> Any:
        from anthropic import AsyncAnthropicVertex

        project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
        if not project_id:
            raise RuntimeError(
                "ANTHROPIC_VERTEX_PROJECT_ID is not set. Configure it via your "
                "environment, .env, or `gluon provider vertex` then re-run."
            )
        region = os.environ.get("CLOUD_ML_REGION", "global")
        return AsyncAnthropicVertex(project_id=project_id, region=region)

    def runner_env(self) -> dict[str, str]:
        env: dict[str, str] = {"CLAUDE_CODE_USE_VERTEX": "1"}
        _passthrough(
            env,
            (
                "ANTHROPIC_VERTEX_PROJECT_ID",
                "CLOUD_ML_REGION",
                # ADC / service-account auth
                "GOOGLE_APPLICATION_CREDENTIALS",
                "GOOGLE_CLOUD_PROJECT",
                "GCLOUD_PROJECT",
                # Gateway / proxy overrides
                "ANTHROPIC_VERTEX_BASE_URL",
                "CLAUDE_CODE_SKIP_VERTEX_AUTH",
                # Per-model region overrides (for when CLOUD_ML_REGION=global
                # doesn't carry a particular model)
                "VERTEX_REGION_CLAUDE_HAIKU_4_5",
                "VERTEX_REGION_CLAUDE_4_5_HAIKU",
                "VERTEX_REGION_CLAUDE_4_6_SONNET",
                "VERTEX_REGION_CLAUDE_4_5_SONNET",
                "VERTEX_REGION_CLAUDE_4_6_OPUS",
                "VERTEX_REGION_CLAUDE_4_7_OPUS",
                "VERTEX_REGION_CLAUDE_4_8_OPUS",
            ),
        )
        return env


# ---------------------------------------------------------------------------
# Microsoft Foundry (Azure AI Foundry, née Azure AI Studio)
# ---------------------------------------------------------------------------


class FoundryProvider(LLMProviderConfig):
    """Microsoft Foundry (Azure) provider.

    Authentication is either:

    - API key: set ``ANTHROPIC_FOUNDRY_API_KEY``, or
    - Microsoft Entra ID: leave the key unset and the Azure SDK default
      credential chain will pick up ``az login`` / managed identity / etc.

    Configuration:

    - ``ANTHROPIC_FOUNDRY_RESOURCE`` — your Azure resource name, **or**
    - ``ANTHROPIC_FOUNDRY_BASE_URL`` — full URL
      (``https://{resource}.services.ai.azure.com/anthropic``)

    Model IDs on Foundry match the deployment names you create in the
    Foundry portal (default: ``claude-opus-4-8``, ``claude-sonnet-4-6``,
    ``claude-haiku-4-5``).
    """

    MODELS = {
        ModelTier.OPUS_48: "claude-opus-4-8",
        ModelTier.OPUS_47: "claude-opus-4-7",
        ModelTier.OPUS_46: "claude-opus-4-6",
        ModelTier.SONNET: "claude-sonnet-4-6",
        ModelTier.HAIKU: "claude-haiku-4-5",
    }

    @property
    def name(self) -> str:
        return "Microsoft Foundry"

    @property
    def supports_cost_tracking(self) -> bool:
        return True  # Reported via Azure Cost Management

    def is_full_model_id(self, model_id: str) -> bool:
        """Foundry IDs match the dashed deployment name (no dots, no @date)."""
        if not model_id.startswith("claude-"):
            return False
        return "." not in model_id and "@" not in model_id

    def create_api_client(self) -> Any:
        from anthropic import AsyncAnthropicFoundry

        resource = os.environ.get("ANTHROPIC_FOUNDRY_RESOURCE")
        base_url = os.environ.get("ANTHROPIC_FOUNDRY_BASE_URL")
        api_key = os.environ.get("ANTHROPIC_FOUNDRY_API_KEY")

        if not resource and not base_url:
            raise RuntimeError(
                "Neither ANTHROPIC_FOUNDRY_RESOURCE nor ANTHROPIC_FOUNDRY_BASE_URL "
                "is set. Configure one via your environment, .env, or "
                "`gluon provider foundry` then re-run."
            )

        kwargs: dict[str, Any] = {}
        if resource:
            kwargs["resource"] = resource
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        # If api_key is unset the SDK falls back to the Azure default credential
        # chain (az login, managed identity, etc.) — nothing to do here.

        return AsyncAnthropicFoundry(**kwargs)

    def runner_env(self) -> dict[str, str]:
        env: dict[str, str] = {"CLAUDE_CODE_USE_FOUNDRY": "1"}
        _passthrough(
            env,
            (
                "ANTHROPIC_FOUNDRY_RESOURCE",
                "ANTHROPIC_FOUNDRY_API_KEY",
                "ANTHROPIC_FOUNDRY_BASE_URL",
                "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
                # Entra ID envs that the Azure default credential chain reads
                "AZURE_CLIENT_ID",
                "AZURE_TENANT_ID",
                "AZURE_CLIENT_SECRET",
                "AZURE_FEDERATED_TOKEN_FILE",
            ),
        )
        return env


# ---------------------------------------------------------------------------
# Provider registry and factory
# ---------------------------------------------------------------------------

_PROVIDERS: dict[LLMProvider, type[LLMProviderConfig]] = {
    LLMProvider.BEDROCK: BedrockProvider,
    LLMProvider.ANTHROPIC: AnthropicProvider,
    LLMProvider.VERTEX: VertexProvider,
    LLMProvider.FOUNDRY: FoundryProvider,
}


def _read_provider_setting() -> str | None:
    """Read llm_provider setting from the store."""
    from gluon.store import GluonStore

    try:
        return GluonStore().get_setting("llm_provider")
    except Exception:
        return None


def get_provider_source() -> str:
    """Return a human-readable label for where the active provider was resolved from.

    Returns one of: "environment variable", "database setting", "default".
    """
    if os.environ.get("GLUON_LLM_PROVIDER"):
        return "environment variable"
    if _read_provider_setting():
        return "database setting"
    return "default"


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
