# LLM Provider Guide

Gluon supports **four** Claude backends — AWS Bedrock, Google Vertex AI, Microsoft Foundry (Azure AI Foundry), and the direct Anthropic API / Claude CLI subscription. Pick whichever your organisation already pays for; every feature (cost tracking, witness, approvals, budgets, session resume) works identically across all four.

This document covers:

- [TL;DR by provider](#tldr-by-provider)
- [How to switch providers](#how-to-switch-providers)
- [Resolution order](#resolution-order)
- [Provider reference](#provider-reference)
  - [AWS Bedrock](#aws-bedrock)
  - [Anthropic Direct](#anthropic-direct)
  - [Google Vertex AI](#google-vertex-ai)
  - [Microsoft Foundry](#microsoft-foundry)
- [Model pinning](#model-pinning)
- [Architecture](#architecture)
- [Cost tracking notes](#cost-tracking-notes)
- [Troubleshooting](#troubleshooting)

---

## TL;DR by provider

| Backend | `GLUON_LLM_PROVIDER` | Key env vars | Auth |
|---|---|---|---|
| AWS Bedrock (default) | `bedrock` | `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK` | AWS creds / IAM role |
| Anthropic Direct | `anthropic` | `ANTHROPIC_API_KEY` | API key or `claude login` |
| Google Vertex AI | `vertex` | `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION` | ADC (`gcloud auth application-default login`) or service-account key |
| Microsoft Foundry | `foundry` | `ANTHROPIC_FOUNDRY_RESOURCE` | API key OR Entra ID (`az login`) |

## How to switch providers

Three ways, any of which works:

```bash
# 1. CLI (persists to the DB)
gluon provider bedrock
gluon provider anthropic
gluon provider vertex
gluon provider foundry

# 2. Environment variable (overrides the DB setting for that process)
export GLUON_LLM_PROVIDER=vertex

# 3. Web dashboard Settings → Preferences → LLM Provider
```

Run `gluon provider` with no argument to see the current provider, its source (env / DB / default), and the model mappings it resolves to. Gluon also exposes this at `GET /api/provider`.

## Resolution order

1. Explicit argument to `get_provider(...)` in code
2. `GLUON_LLM_PROVIDER` environment variable
3. `llm_provider` setting in the Gluon database
4. Default: `bedrock`

## Provider reference

### AWS Bedrock

Default provider. Uses your AWS Bedrock billing.

**Required env:**
- `AWS_REGION` — e.g. `us-east-1`, `eu-west-1`
- `AWS_BEARER_TOKEN_BEDROCK` (short-lived token) **or** standard AWS creds (`AWS_ACCESS_KEY_ID` / `AWS_PROFILE` / IAM role)

**Optional:**
- `ANTHROPIC_BEDROCK_BASE_URL` — route through a corporate LLM gateway
- `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1` — when the gateway handles AWS auth

**Model ID scheme:** `global.anthropic.claude-<model>-<tier>-v<N>` (e.g. `global.anthropic.claude-opus-4-6-v1`). Supports regional prefixes `global.` / `us.` / `apac.`.

**Docker:** `~/.aws` is mounted read-only at `~/.aws` inside the container.

### Anthropic Direct

Direct Anthropic API calls, or reuse a Claude CLI subscription (Pro / Team / Enterprise).

**Required env (either):**
- `ANTHROPIC_API_KEY` — your Anthropic console key
- **OR** an active `claude login` session at `~/.claude` (mounted into the container)

**Optional:**
- `ANTHROPIC_BASE_URL` — point at a gateway

**Model ID scheme:** `claude-<model>-<tier>` (dashed, no dots, no `@date`), e.g. `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`.

### Google Vertex AI

Claude models hosted on Vertex AI. Billed through your Google Cloud account.

**Required env:**
- `ANTHROPIC_VERTEX_PROJECT_ID` — your GCP project ID
- `CLOUD_ML_REGION` — `global` (recommended), or a multi-region (`us`, `eu`), or a specific region (`us-east5`, `europe-west1`, `asia-southeast1`, etc.)

**Auth:** Application Default Credentials (ADC).
- On the host: `gcloud auth application-default login`
- The docker-compose mounts `~/.config/gcloud` read-only, so ADC flows automatically.
- Service-account alternative: set `GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json`.

**Optional:**
- `ANTHROPIC_VERTEX_BASE_URL` — LLM gateway
- `CLAUDE_CODE_SKIP_VERTEX_AUTH=1` — when the gateway handles GCP auth
- `VERTEX_REGION_CLAUDE_HAIKU_4_5=us-east5` — per-model region override when `CLOUD_ML_REGION=global` doesn't carry a given model (see [Vertex Model Garden](https://console.cloud.google.com/vertex-ai/model-garden))

**Model ID scheme:** `claude-<model>-<tier>` optionally suffixed `@<YYYYMMDD>` for dated pins. Example: `claude-haiku-4-5@20251001`.

**One-time setup:**
1. Enable the Vertex AI API: `gcloud services enable aiplatform.googleapis.com`
2. Request model access in [Model Garden](https://console.cloud.google.com/vertex-ai/model-garden) for each Claude model you want (24-48h approval)
3. Grant `roles/aiplatform.user` on the project (or the narrower `aiplatform.endpoints.predict` permission)
4. `gcloud auth application-default login` on the host
5. `gluon provider vertex` to switch Gluon to Vertex

### Microsoft Foundry

Claude on Microsoft Foundry (Azure AI Foundry, née Azure AI Studio). Billed through your Azure subscription.

**Required env:**
- `ANTHROPIC_FOUNDRY_RESOURCE` — your Azure resource name, **or**
- `ANTHROPIC_FOUNDRY_BASE_URL` — full URL (`https://{resource}.services.ai.azure.com/anthropic`)

**Auth (either):**
- `ANTHROPIC_FOUNDRY_API_KEY` — API key from the Foundry portal → Endpoints and keys, or
- Microsoft Entra ID — leave the key unset and the Azure SDK default credential chain picks up `az login` / managed identity / workload identity (the docker-compose mounts `~/.azure`)

**Optional:**
- `CLAUDE_CODE_SKIP_FOUNDRY_AUTH=1` — when a gateway handles Azure auth
- `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_FEDERATED_TOKEN_FILE` — Entra ID service-principal / workload-identity auth

**Model ID scheme:** `claude-<model>-<tier>` — matches the deployment name you create in the Foundry portal (dashed, no dots, no `@date`).

**One-time setup:**
1. In the [Microsoft Foundry portal](https://ai.azure.com/), create a resource and note the resource name
2. Create deployments for each Claude model you want — name them `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5` (or adjust via [model pinning](#model-pinning))
3. Grant the `Azure AI User` + `Cognitive Services User` roles (or a custom role with `Microsoft.CognitiveServices/accounts/providers/*`)
4. Auth: `az login` on the host, or copy an API key to `ANTHROPIC_FOUNDRY_API_KEY`
5. `gluon provider foundry` to switch Gluon to Foundry

## Model pinning

For multi-user / team deployments, pin specific model versions so a new Anthropic release doesn't silently change behaviour mid-sprint. These env vars work across all three cloud providers (Bedrock, Vertex, Foundry):

```bash
export ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-7
export ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-4-6
export ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4-5
# Override everything:
export ANTHROPIC_MODEL=claude-opus-4-7
```

Without pinning, the `opus` / `sonnet` / `haiku` aliases resolve to whichever version the CLI currently considers the default for that backend — which may not yet be enabled in your Vertex / Foundry deployment when Anthropic ships an update.

## Architecture

### Code layout

```
src/gluon/
├── llm_provider.py       # LLMProviderConfig ABC + 4 concrete classes
├── models_config.py      # MODEL_IDS / get_model_id — delegates to active provider
└── witness.py            # Uses provider.create_api_client() for health classifier
```

### Provider interface

```python
class LLMProviderConfig(ABC):
    MODELS: dict[ModelTier, str]           # tier → provider-specific model ID

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def supports_cost_tracking(self) -> bool: ...

    @abstractmethod
    def is_full_model_id(self, model_id: str) -> bool: ...

    @abstractmethod
    def create_api_client(self) -> Any: ...   # async Anthropic SDK client

    def runner_env(self) -> dict[str, str]:   # default: {}
        """Env vars to export into the Claude Code subprocess."""
```

Shared logic on the base class: `get_model_id(tier)`, `get_fallback_model_id(model)`, `describe_models()`. Each concrete provider overrides the four abstract methods plus `runner_env()`.

### Subprocess environment

When Gluon spawns the Claude Code subprocess (for actual inference), it merges `provider.runner_env()` into the subprocess environment. This is what tells Claude Code which backend to route inference through:

| Provider | Flag exported |
|---|---|
| Bedrock | `CLAUDE_CODE_USE_BEDROCK=1` |
| Anthropic | *(no flag — direct API is Claude Code's default)* |
| Vertex | `CLAUDE_CODE_USE_VERTEX=1` |
| Foundry | `CLAUDE_CODE_USE_FOUNDRY=1` |

Plus each provider forwards its required env vars (project IDs, resource names, credentials) — see the matching class in `llm_provider.py`.

### Cost tracking

Cost data comes from the Claude SDK's `ResultMessage.total_cost_usd`. This is reported by the CLI regardless of provider — the CLI knows which backend it's talking to. Cost tracking in `AgentResult`, `Session`, `ExecutionRun`, and the store is provider-agnostic and needs no changes.

**Exception:** Claude CLI subscription users (Pro/Max/Team) may see `$0.00` or `null` costs because their usage is covered by the subscription. The UI handles this by hiding cost displays when they're zero/null.

## Troubleshooting

### `gluon doctor` surfaces config issues

The `LLM Provider Config` doctor check verifies the active provider's required env vars are set. Run `gluon doctor` after switching providers to catch missing config before a run fails.

### Vertex: "model not found" / 404

- Confirm the model is enabled in [Model Garden](https://console.cloud.google.com/vertex-ai/model-garden).
- Try `CLOUD_ML_REGION=global` (widest availability).
- For models that don't support global endpoints, set a specific region, e.g. `VERTEX_REGION_CLAUDE_HAIKU_4_5=us-east5`.

### Vertex: "Failed to get ADC"

Run `gcloud auth application-default login` on the host. In Docker, `~/.config/gcloud` is mounted read-only — the login step has to happen outside the container.

### Foundry: "Failed to get token from azureADTokenProvider"

Either set `ANTHROPIC_FOUNDRY_API_KEY` (simpler) or run `az login` on the host so the Azure credential chain can pick it up (`~/.azure` is mounted read-only). If you're running in Azure with managed identity or workload identity, the SDK picks it up automatically.

### Bedrock: "UnauthorizedOperation" / "expired token"

If using `AWS_BEARER_TOKEN_BEDROCK`, it's short-lived — refresh it. For longer-lived access use IAM user access keys or a role with Bedrock permissions.

### Session cost shows `$0.00` on Anthropic Direct

Expected if you're on a Claude CLI subscription (Pro/Max/Team/Enterprise). Subscription usage doesn't report per-request cost.

## References

- [Anthropic SDK Python — `AnthropicBedrock` / `AnthropicVertex` / `AnthropicFoundry`](https://github.com/anthropics/anthropic-sdk-python)
- [Claude Code: Bedrock / Vertex / Foundry docs](https://code.claude.com/docs/en/bedrock-vertex-proxies)
- [Claude on Vertex AI (Anthropic)](https://platform.claude.com/docs/en/api/claude-on-vertex-ai)
- [Claude Code on Google Vertex AI](https://code.claude.com/docs/en/google-vertex-ai)
- [Claude Code on Microsoft Foundry](https://code.claude.com/docs/en/microsoft-foundry)
