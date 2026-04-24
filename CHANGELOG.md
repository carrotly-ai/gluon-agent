# Changelog

All notable changes to Gluon Agent are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — LLM provider abstraction

Gluon now runs against **four** Claude backends, selectable via the
`GLUON_LLM_PROVIDER` env var, the `gluon provider` CLI command, or the web
dashboard Settings page. Every feature (cost tracking, witness, approvals,
budgets, session resume) works identically across all four.

| Backend | Credentials |
|---|---|
| AWS Bedrock *(default)* | AWS env / `~/.aws` |
| Anthropic Direct | `ANTHROPIC_API_KEY` or `claude login` |
| Google Vertex AI | `ANTHROPIC_VERTEX_PROJECT_ID` + `CLOUD_ML_REGION` + GCP ADC |
| Microsoft Foundry | `ANTHROPIC_FOUNDRY_RESOURCE` + (API key or Entra ID) |

- `LLMProviderConfig` ABC + four concrete providers in `src/gluon/llm_provider.py`
- `runner_env()` method on each provider contributes the matching `CLAUDE_CODE_USE_*` flag and credential env to the Claude Code subprocess
- `gluon provider <name>` CLI command with per-provider config hints
- `gluon status` now shows active provider + resolution source
- `GET /api/provider` endpoint
- Web dashboard Settings → Preferences: 4-button provider segmented control
- `gluon doctor` includes an `LLM Provider Config` check that verifies the active provider's required env vars are set
- Docker: mounts `~/.config/gcloud` (Vertex ADC) and `~/.azure` (Foundry Entra ID) read-only; passes through all Vertex/Foundry/model-pinning env vars
- `.env.example` documents all four provider flows
- `docs/LLM-PROVIDER.md` full user guide
- `anthropic[vertex]>=0.39.0` promoted from latent runtime dep to explicit dep
- `TestProviderRealSdkIntegration` — 5 non-mocked tests that construct real SDK clients and verify `runner_env()` plumbing propagates through `subprocess.run()`, catching breaks that mocks hide

### Added — Theme C observability

A coherent three-view observability surface on the Run Detail dialog:

- **Tools tab** — Per-run tool usage breakdown. Sorted by frequency with percentage-of-total fill bars and color-coded categories. Honest footnote: the SDK doesn't attribute cost per tool call, so we show frequency as the directional signal rather than faking dollar attribution.
- **Reasoning threading** — Each tool call's expanded card now surfaces the assistant's preceding "why" as a **Reasoning** section with a lightbulb icon. Collapsed cards show a small lightbulb indicator when reasoning is available.
- **Timeline tab** — MVP replay viewer. Horizontal strip with one dot per tool call positioned proportionally by timestamp. Click a dot to focus; prev/next buttons or keyboard shortcuts (`←`/`→`/`Home`/`End`) step through. Detail card shows full inputs + reasoning.

All three share a data path: parsed client-side from the `messages.jsonl` already fetched for the Messages tab. No new backend endpoints.

### Added — External contribution

- Richer error output when a web-dashboard or chat-bot dependency isn't installed (shows the missing module name). Contributed by @gcalderhead.

### Changed

- `claude-agent-sdk>=0.1.65` → `>=0.1.66` (pure internal bump — updated bundled Claude CLI to 2.1.119, no API changes)

### Security

- **31 Dependabot alerts cleared:**
  - Backend (17): PyJWT 2.10.1 → 2.12.1, python-multipart 0.0.22 → 0.0.26, python-dotenv 1.2.1 → 1.2.2, cryptography → 46.0.7, plus minor bumps across pydantic/rich/ruff/starlette/uvicorn/redis/websockets
  - Frontend (14): vite, lodash, minimatch, rollup, postcss, picomatch upgrades via `npm audit fix`
- **4 additional high-severity advisories cleared via `npm overrides`:** serialize-javascript pinned to `^7.0.5` through `workbox-build → @rollup/plugin-terser`, avoiding a breaking downgrade of `vite-plugin-pwa` to 0.19.8.
- Additional postcss `<8.5.10` XSS advisory cleared post-merge.
- `npm audit` now reports **0 vulnerabilities**.

### Fixed

- CI mypy errors in `approvals.py` (3 `arg-type` errors — narrowed `input_data.get(...)` from `object` to `str`)
- CI mypy errors in `scheduler.py` (croniter missing stubs + `get_next(datetime)` returning `Any`)
- Flaky `test_build_approval_view_has_two_persistent_buttons` in Discord test suite (converted to `@pytest.mark.anyio async` so `discord.ui.View.__init__` has a guaranteed event loop)
- Ruff E501 regression in two `console.print` f-strings that crossed the 120-char limit after the web-dashboard dep-error messages were expanded

---

## [0.9.1] - 2026-02

Baseline for this CHANGELOG. See git history for changes prior to this version.
