# LLM Provider Abstraction

## Problem Statement

Gluon Agent currently hardcodes AWS Bedrock as its LLM backend. Model IDs in `models_config.py` are Bedrock-specific (e.g., `global.anthropic.claude-opus-4-6-v1`), the `witness.py` module directly instantiates `AnthropicBedrock`, and the documentation assumes AWS credentials throughout.

This works well for corporate environments with Bedrock billing and security controls, but individual users with a standard Anthropic subscription (Claude Pro/Max) want to use Claude Code directly without AWS intermediation.

### Goals

1. Allow users to switch between Bedrock and direct Anthropic (Claude CLI) as their LLM provider via a single configuration setting.
2. Preserve all existing Bedrock functionality — model resolution, fallback tiers, cost tracking, the witness classifier.
3. Introduce a clean provider abstraction that isolates all provider-specific logic behind an interface.
4. Follow established project patterns: Pydantic models, store settings, StrEnum, ABC-based extensibility.

### Non-Goals

- Supporting non-Anthropic LLMs (OpenAI, Gemini, etc.). The abstraction should be clean enough that this is possible later, but it is not a design target.
- Changing the Claude Agent SDK integration. The SDK talks to the Claude CLI subprocess regardless of provider — the provider abstraction affects *model ID resolution*, *direct API calls* (witness), and *cost tracking behaviour*, not the SDK execution path itself.

## Architecture

### Where Bedrock was coupled (before this change)

| Location | What it does | Provider-specific? |
|---|---|---|
| `models_config.py` | Maps `ModelTier` → Bedrock model IDs | **Yes** — IDs are `global.anthropic.claude-*` |
| `models_config.py` | `get_model_id()` passthrough for full Bedrock IDs | **Yes** — checks `global.anthropic.` prefix |
| `models_config.py` | `get_fallback_model_id()` | **Yes** — returns Bedrock IDs |
| `agent.py` | `GluonAgent.__init__` calls `get_model_id()` | Indirect — consumes Bedrock IDs |
| `agent.py` | `_build_options()` sets `options.model` | Indirect — passes Bedrock ID to SDK |
| `agent.py` | `_build_options()` sets `options.fallback_model` | Indirect — passes Bedrock fallback ID |
| `core.py` | `Orchestrator.execute()` calls `get_model_id()` | Indirect |
| `chat_agent.py` | `get_model_id()` for chat model resolution | Indirect |
| `witness.py` | `anthropic.AsyncAnthropicBedrock()` direct API call | **Yes** — hardcoded Bedrock client |
| `cli.py` | Loads `.env.local` for "AWS Bedrock configuration" | **Yes** — comment and intent |

### How model IDs flow

```
User selects "opus-4.6"
    → models_config.get_model_id("opus-4.6")
    → get_provider().get_model_id("opus-4.6")
    → "global.anthropic.claude-opus-4-6-v1"       (Bedrock)
      or "claude-opus-4-6"                (Anthropic)
    → GluonAgent(model=...)
    → _build_options() → ClaudeAgentOptions(model=...)
    → Claude SDK subprocess → Claude CLI → provider API
```

### Cost tracking

Cost data comes from the Claude SDK's `ResultMessage.total_cost_usd`. This is reported by the CLI regardless of provider — the CLI knows whether it's talking to Bedrock or Anthropic directly. Cost tracking in `AgentResult`, `Session`, `ExecutionRun`, and the store is provider-agnostic and needs no changes.

The one exception: users on a Claude subscription (Pro/Max) get unlimited usage within their plan, so cost figures may be zero or meaningless. The UI should handle this gracefully (display costs when available, omit when zero/null).

## Design

### Provider enum and configuration

```python
# llm_provider.py

class LLMProvider(StrEnum):
    """Supported LLM providers."""
    BEDROCK = "bedrock"
    ANTHROPIC = "anthropic"
```

The active provider is resolved via `get_provider()` with this precedence:

1. Explicit argument (if provided)
2. `GLUON_LLM_PROVIDER` environment variable
3. Stored setting in database (`llm_provider` key in `settings` table)
4. Default: `"bedrock"`

### Provider interface (ABC)

The ABC defines four abstract methods that each provider must implement (`is_full_model_id`, `create_api_client`, `name`, `supports_cost_tracking`). Shared logic for model ID resolution, fallback chains, and model descriptions lives as concrete methods on the base class, avoiding duplication across providers:

```python
# src/gluon/llm_provider.py

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

        Handles tier enums, tier name strings ("sonnet"), UI aliases
        ("claude-opus-4.6"), and full model ID passthrough. Shared across
        all providers — only is_full_model_id() varies.
        """
        ...

    def get_fallback_model_id(self, model: str) -> str | None:
        """Get the fallback model ID for graceful degradation.

        Resolves the input to a ModelTier, looks up the fallback tier in
        FALLBACK_TIERS, and returns the provider-specific model ID.
        """
        ...

    def describe_models(self) -> str:
        """Get a human-readable description of available models."""
        ...
```

### Bedrock implementation

```python
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
        return True  # Bedrock reports per-request costs

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
```

### Anthropic (direct) implementation

```python
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
        return True  # API reports costs; subscription users may see $0

    def is_full_model_id(self, model_id: str) -> bool:
        # Full Anthropic model IDs start with "claude-" and use dashes (e.g., "claude-opus-4-6")
        # UI aliases use dots (e.g., "claude-opus-4.6") — these must NOT match
        if not model_id.startswith("claude-"):
            return False
        return "." not in model_id

    def create_api_client(self) -> Any:
        import anthropic
        return anthropic.AsyncAnthropic()  # Uses ANTHROPIC_API_KEY env var
```

### Provider registry and factory

```python
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
                f"Unknown provider: {provider}. "
                f"Available: {', '.join(p.value for p in LLMProvider)}"
            ) from None

    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown provider: {provider}. "
            f"Available: {', '.join(p.value for p in LLMProvider)}"
        )
    return cls()
```

### Backwards-compatible `models_config.py`

The existing public API (`get_model_id`, `get_fallback_model_id`, `describe_models`) is preserved as thin wrappers that delegate to the active provider. All existing callers continue to work without changes:

```python
# models_config.py (updated)

from gluon.llm_provider import get_provider

def get_model_id(tier: ModelTier | str) -> str:
    """Get the full model ID for a given tier.

    Delegates to the active LLM provider.
    """
    return get_provider().get_model_id(tier)

def get_fallback_model_id(model: str) -> str | None:
    return get_provider().get_fallback_model_id(model)

def describe_models() -> str:
    return get_provider().describe_models()
```

`MODEL_IDS` is preserved as a module-level dict via `_ModelIDsProxy` — a lazy proxy that delegates to the active provider's `MODELS` dict on each access. This means `MODEL_IDS[ModelTier.SONNET]` returns the correct provider-specific ID without callers needing to know about the provider abstraction.

## Files Changed

| File | Change |
|---|---|
| `src/gluon/llm_provider.py` | **New** — ABC, `BedrockProvider`, `AnthropicProvider`, factory |
| `src/gluon/models_config.py` | Thin wrappers delegating to provider; `_ModelIDsProxy` for `MODEL_IDS`; `ModelTier`, aliases, `DEFAULT_MODEL` unchanged |
| `src/gluon/witness.py` | Replace hardcoded `AnthropicBedrock()` with `get_provider().create_api_client()` |
| `tests/test_llm_provider.py` | **New** — unit tests for both providers, factory, and cross-provider contract tests |
| `tests/test_models_config.py` | Bedrock passthrough tests replaced with provider-agnostic `test_full_model_id_passthrough` |
| `tests/test_agent_config.py` | `test_full_bedrock_id_passthrough` parametrized by provider |
| `tests/conftest.py` | `llm_provider` fixture for cross-provider test parametrization |
| `docs/LLM-PROVIDER.md` | This document |

| `src/gluon/cli.py` | `gluon provider` command; provider indicator in `gluon status` |
| `src/gluon/web/api.py` | `GET /api/provider` endpoint; provider fields in `/api/status` and `/api/settings` |
| `src/gluon/web/models.py` | `ProviderResponse` model; `llm_provider`/`llm_provider_name` on `StatusResponse` |

| `CLAUDE.md` | Updated models table with both providers; added `GLUON_LLM_PROVIDER` env var |

## Configuration

### Environment variable

```bash
# Use direct Anthropic API (Claude subscription)
export GLUON_LLM_PROVIDER=anthropic

# Use AWS Bedrock (default, current behaviour)
export GLUON_LLM_PROVIDER=bedrock
```

### Store setting

The provider can be set in the `settings` table (`llm_provider` key). The env var takes precedence over the stored setting. When the web dashboard writes to the settings table, new agents will pick up the change without requiring a restart (running agents keep their resolved model ID).

### CLI command (not yet implemented)

```bash
# View current provider
gluon provider

# Switch to Anthropic
gluon provider anthropic

# Switch to Bedrock
gluon provider bedrock
```

### Web dashboard (not yet implemented)

The Settings page already has a general settings section. Add a "LLM Provider" dropdown with options "AWS Bedrock" and "Anthropic (Direct)". Changing it writes to the `settings` table.

### Docker / `.env.local`

```bash
# .env.local
GLUON_LLM_PROVIDER=anthropic
# No AWS credentials needed when using Anthropic directly
```

For Bedrock (existing):
```bash
GLUON_LLM_PROVIDER=bedrock
AWS_REGION=us-east-1
# AWS credentials via ~/.aws or IAM role
```

## Authentication

| Provider | Auth mechanism | What the user needs |
|---|---|---|
| Bedrock | AWS IAM credentials (`~/.aws/credentials` or IAM role) | AWS account with Bedrock model access |
| Anthropic | Claude CLI login (`claude login`) or `ANTHROPIC_API_KEY` env var | Anthropic account (Pro/Max subscription or API key) |

The Claude CLI (`claude` subprocess) handles its own authentication independently. The `LLMProviderConfig.create_api_client()` method is only used for direct API calls (witness classifier, health checks) — not for the main agent execution path.

## Cost Tracking Behaviour

Cost tracking continues to work identically for both providers:

- **Bedrock**: Claude SDK reports per-request costs via `ResultMessage.total_cost_usd`. These are real AWS charges.
- **Anthropic (API key)**: Claude SDK reports per-request costs. These are real Anthropic API charges.
- **Anthropic (subscription)**: Claude SDK may report `$0.00` costs since usage is covered by the subscription. The dashboard should handle this gracefully — show "Included in subscription" or simply omit cost columns when all values are zero.

The `supports_cost_tracking` property on the provider lets the UI decide how to present cost data. No changes to `Session.total_cost_usd`, `ExecutionRun.cost_usd`, or store logic are needed.

## Impact on Existing Components

### Claude Agent SDK (`agent.py`)

The SDK talks to the Claude CLI subprocess. The CLI itself determines whether to route to Bedrock or Anthropic based on its own configuration. When we pass `options.model = "claude-opus-4-6"` (Anthropic model ID) vs `"global.anthropic.claude-opus-4-6-v1"` (Bedrock ID), the CLI routes accordingly.

**No changes** to `GluonAgent`, `_build_options()`, or the execution loop. The only change is that `get_model_id()` now returns different strings depending on the active provider.

### Chat agent (`chat_agent.py`)

Uses `get_model_id()` — automatically picks up provider changes. No code changes needed.

### Orchestrator (`core.py`)

Uses `get_model_id()` — automatically picks up provider changes. No code changes needed.

### Runner (`runner.py`)

Creates `GluonAgent` instances — model IDs flow through `get_model_id()`. No code changes needed.

### Witness (`witness.py`)

Previously hardcoded `anthropic.AsyncAnthropicBedrock()`. Updated to:

```python
from gluon.llm_provider import get_provider
from gluon.models_config import ModelTier

provider = get_provider()
client = provider.create_api_client()
model_id = provider.get_model_id(ModelTier.HAIKU)

response = await client.messages.create(
    model=model_id,
    ...
)
```

### Store / Models

No changes. `ModelTier`, `TaskProfile`, `Session.total_cost_usd`, `ExecutionRun.cost_usd` are all provider-agnostic.

## Test Strategy

### Test file: `tests/test_llm_provider.py`

71 tests across four test classes:

- **`TestBedrockProvider`** — Bedrock-specific model IDs, passthrough, fallbacks, API client creation
- **`TestAnthropicProvider`** — Anthropic-specific model IDs, passthrough, fallbacks, API client creation
- **`TestGetProvider`** — Factory function: env var, store setting, explicit argument, defaults, error handling
- **`TestProviderContract`** — Cross-provider parametrized tests verifying both providers satisfy the same behavioural contract (all tiers resolvable, fallback chains terminate, own IDs pass `is_full_model_id`, aliases resolve consistently)

### Changes to `tests/test_models_config.py`

Three Bedrock-specific passthrough tests replaced with one provider-agnostic version:

```python
def test_full_model_id_passthrough(self):
    """Full model IDs for the active provider pass through unchanged."""
    from gluon.llm_provider import get_provider

    provider = get_provider()
    for model_id in provider.MODELS.values():
        assert get_model_id(model_id) == model_id
```

All other tests (31 of 34) work unchanged — they compare against `MODEL_IDS[tier]` which delegates to the active provider.

### Changes to `tests/test_agent_config.py`

One Bedrock-specific passthrough test parametrized by provider:

```python
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
```

### Test fixture: provider isolation

`conftest.py` provides a shared fixture for tests that need to run under both providers:

```python
@pytest.fixture(params=["bedrock", "anthropic"])
def llm_provider(request, monkeypatch):
    """Parametrize tests across all LLM providers."""
    monkeypatch.setenv("GLUON_LLM_PROVIDER", request.param)
    return request.param
```

### CI considerations

Tests default to `GLUON_LLM_PROVIDER=bedrock` (preserving existing CI behaviour). The `test_llm_provider.py` contract tests explicitly test both providers via parametrization, so CI validates both paths without requiring any env var changes.

## Migration Path

1. **Phase 1** (done): Create `llm_provider.py` with ABC and both implementations. Update `models_config.py` to delegate. Update `witness.py`. Add tests. Default remains `bedrock` — zero behavioural change for existing users.

2. **Phase 2** (done): Add `gluon provider` CLI command. Add `GET /api/provider` endpoint. Add provider indicator to `gluon status` and `StatusResponse`. Expose provider metadata in settings API. Web dashboard Settings page already had provider toggle from Phase 1.

3. **Phase 3** (done): Update `CLAUDE.md` models table and environment variables to document both providers.

All phases are backwards-compatible. Existing Bedrock users see no change unless they explicitly switch providers.
