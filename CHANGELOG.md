# Changelog

All notable changes to Gluon Agent are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_No unreleased changes yet._

## [0.11.0] - 2026-04-26

Headline: **Multi-user authentication is here.** Optional, opt-in via a single feature flag (`GLUON_AUTH_ENABLED=true`), and ships in **all four phases at once** — local password auth, OIDC / SSO, RBAC with three roles, per-row attribution on every action, an admin user-management screen, and self-serve chat-account linking from Telegram and Discord. Single-user installs see zero behaviour change after upgrading.

10 PRs across the D5 theme: design (#82), Phase 1 identity foundation (#83), Phase 2 backend + RBAC (#84), Phase 2 attribution (#85), Phase 2 web UI (#86), Phase 4 admin transport linking (#87), Phase 4 self-serve `/link` (#88), cleanup pass (#89), Phase 3 OIDC SSO (#90), comprehensive documentation (#91).

### Added — D5 Phase 1: identity foundation (#83)

The plumbing that everything else builds on. Inactive until `GLUON_AUTH_ENABLED=true`.

- **`User`, `UserSession`, `AuthProviderConfig` ABC** in `src/gluon/auth.py` and `src/gluon/models.py`
- **`SYSTEM_USER`** singleton with deterministic UUID `00000000-0000-0000-0000-000000000000` — the implicit user under single-user mode. Never persisted; carries `role=admin` because the operator holding the DB file has full authority.
- **`LocalAuthProvider`** — argon2id password hashing with library defaults (~100ms verify time). Includes silent rehash-on-verify upgrade.
- **DB-backed sessions** (not JWT) with rolling TTL — server-side logout works, sessions can be rotated independently of token expiry, no token-revocation list to maintain.
- **`users` and `user_sessions` schema** added via append-only migrations. Existing single-user installs get the tables on next startup but they stay empty.
- **CLI `gluon user *` namespace** — `add / list / show / disable / enable / set-role / set-password`. Works regardless of `GLUON_AUTH_ENABLED` so you can seed users before flipping the flag.

### Added — D5 Phase 2: backend auth + RBAC (#84)

- **`POST /api/auth/login`** — local password endpoint. Sets the `gluon_session` cookie (httpOnly, sameSite=lax, secure on HTTPS).
- **`POST /api/auth/logout`** — server-side session invalidation.
- **`GET /api/auth/me`** — always succeeds; returns `SYSTEM_USER` placeholder when no session, real user when authenticated.
- **`current_user_dep`** — FastAPI `Depends()` injector returning the resolved `User` for the request, or `SYSTEM_USER` when auth is disabled.
- **`require_role(role)`** — RBAC gate via `_role_rank` (admin > operator > viewer).
- **Admin-only user management endpoints** — `GET/POST /api/users`, `PATCH /api/users/{id}`, `DELETE /api/users/{id}` (soft-delete), `POST /api/users/{id}/password`.
- **Three roles**: `admin` (manage users + everything), `operator` (create runs, decide approvals), `viewer` (read-only).
- **Session rotation** — all of a user's sessions are wiped on password change, role change, or disable.

### Added — D5 Phase 2: per-row attribution (#85)

Every action-attributable row now records *who* performed it via a nullable FK to `users(id)`:

- `execution_runs.user_id` — set when a user creates a run from web/bot/CLI
- `orchestrator_tasks.created_by_user_id` — set when a user adds a task to the work queue
- `pending_approvals.decided_by_user_id` — set when a user grants or denies an approval

Columns are nullable (no FK constraint) so pre-auth-era rows remain valid and SYSTEM_USER actions deliberately don't pollute audit trails. The legacy `pending_approvals.decided_by` string column is kept alongside the new FK for transport-side debugging — attribution is *additive*, not replacing.

### Added — D5 Phase 2: web UI (#86)

- **`LoginPage`** — full-screen route shown when `auth_enabled=true` and no session. Auto-detects available providers via `GET /api/auth/providers`.
- **`UserMenu`** — header dropdown with avatar (computed initials), display name + role badge, inline change-password form, sign-out, and admin shortcut.
- **`AdminUsersPage`** at `/admin/users` — admin-only screen with tabular list, role badges, last-seen timestamps, inline edit row (display name, email, role, disabled, chat IDs), admin-side password reset, soft-delete with confirm.
- **`AdminUsersGuard`** — defense-in-depth wrapper that shows a graceful "Access denied" screen for non-admins navigating to the URL directly.
- **`useCurrentUser` hook** — context-backed single source of truth. Exposes `login`, `logout`, `refresh`, `needsLogin`. Wraps the SPA via `<CurrentUserProvider>` in `main.tsx`.
- **`ApiError` class** — `status` and `detail` are first-class so callers branch on HTTP status instead of string-matching.
- **`credentials: 'include'`** added to `fetchJson` so cookies flow on cross-origin dev setups (no-op in production).

### Added — D5 Phase 4: admin-managed transport linking (#87)

Lets the bots resolve a chat user to a Gluon User and record proper attribution on runs and approval decisions made from chat.

- **`bot_core.resolve_user_id_by_chat_id(transport, chat_id)`** and **`resolve_user_attribution(ctx)`** — central helpers for chat-ID → User.id lookup.
- **Telegram approval handler** — looks up the Telegram user, passes `decided_by_user_id` to `decide_approval`, includes `@username` in the decision reason.
- **Discord approval handler** — same pattern via `interaction.user.id` + `display_name`.
- **All 4 Telegram + 3 Discord run-creation sites** now pass `user_id=resolve_*(...)` so chat-initiated runs are attributed too.
- **`PATCH /api/users/{id}` accepts `telegram_user_id` / `discord_user_id`** for admin pre-registration. Returns 409 if the chat ID is already bound to a different user (chat IDs must be unique per platform for unambiguous lookup).
- **AdminUsersPage edit row** gets numeric inputs for both platforms with `0` as the "clear" sentinel.

### Added — D5 Phase 4: self-serve `/link` flow (#88)

Users bind their chat identity from chat without needing admin intervention.

- **`link_codes` table** with partial index `(transport, expires_at) WHERE consumed_at IS NULL`. Consumed codes are kept as audit trail.
- **`LinkCode`** Pydantic model + `LinkCodeError(reason)` exception with stable reason strings (`unknown` / `expired` / `consumed` / `transport_mismatch` / `chat_taken`).
- **`store.create_link_code`** — generates a 10-char code from a 32-char alphabet (no `0/1/I/O`); tears down prior unconsumed codes for the same `(user, transport)` so only one code is ever active at a time.
- **`store.consume_link_code`** — single-transaction validate → conflict-check → bind → stamp consumed.
- **`store.unlink_chat`** + **`store.delete_expired_link_codes`** sweep helper.
- **3 new endpoints**: `POST /api/auth/link-codes`, `GET /api/auth/links`, `DELETE /api/auth/links/{transport}`.
- **Telegram**: `/link <code>` and `/unlink` commands. Without an argument, `/link` shows step-by-step instructions referencing the dashboard.
- **Discord**: `link-account <code>` and `unlink-account` keywords (intentionally distinct from the existing `link <project>` channel-binding command). Works in server-mention and DM contexts.
- **`ConnectedAccountsSection`** in `UserMenu` — per-transport rows with **Linked ✓** state (with unlink button) or a **Link** button that opens a code card with copy-to-clipboard, live mm:ss countdown, and the exact chat command to paste. Auto-polls `/auth/me` every 3s so the UI flips to "Linked" the moment the bot consumes the code.

**Security:** code-only flow (a leaked code can only bind the *original requester's* account); no silent takeover (`chat_taken` error if someone else owns that chat ID); single active code per `(user, transport)`; 10-min TTL with hourly sweep; case + whitespace tolerant on input.

### Added — D5 Phase 3: OIDC / SSO (#90)

OpenID Connect via Authlib's discovery client. Coexists with local — typical setup is OIDC for humans + a few local accounts for automation.

- **`OIDCConfig`** — env-var snapshot with `from_env()` returning `None` when not configured (graceful degradation). **Refuses to load when `AUTO_PROVISION=true` without `DOMAIN_ALLOWLIST`** — the single most security-relevant guardrail.
- **`OIDCAuthProvider`** — wraps Authlib's discovery-driven OAuth client. Lazy fetch of `{issuer}/.well-known/openid-configuration`; ID tokens validated against the provider's JWKS automatically.
- **`resolve_or_provision()`** — three resolution paths: exact `(provider, sub)` match → email-as-placeholder pre-registration with subject swap → opt-in auto-provision (gated by domain allowlist).
- **3 new endpoints**: `GET /api/auth/providers` (feature detection — drives the LoginPage UI), `GET /api/auth/oidc/login` (302 to IdP authorize URL), `GET /api/auth/oidc/callback` (validates token + mints session cookie).
- **`?next=…` post-login redirect constrained to relative paths** — open-redirect-via-IdP attacks refused.
- **`GLUON_LOCAL_AUTH_ENABLED=false`** — OIDC-only mode (disables the password endpoint entirely; CLI still works for first-admin bootstrap).
- **`gluon user add --auth-provider oidc --email alice@…`** — pre-register OIDC users without knowing the IdP's `sub` claim yet.
- **LoginPage** updated to feature-detect: renders password form, "Sign in with X" button, both with a divider when both providers configured, or empty state when neither.
- **Provider recipes** in [`docs/AUTH-OIDC.md`](docs/AUTH-OIDC.md) for Auth0, Google Workspace, AWS Cognito, Microsoft Entra ID.
- **Authlib + itsdangerous + joserfc** added as runtime deps. Starlette `SessionMiddleware` mounted to carry OAuth state+nonce between the redirect and callback.

### Added — D5 cleanup pass (#89)

- **Periodic `_sweep_auth_state` background task** — runs hourly to delete expired `user_sessions` and unconsumed-but-expired `link_codes`. Tunable via `GLUON_AUTH_SWEEP_INTERVAL_SECS`. Mirrors the existing `_cleanup_old_logs` / `_cleanup_old_worktrees` pattern; gracefully cancelled at app shutdown.

### Added — Documentation (#91)

- **New `docs/AUTH.md`** (510 lines, 7 mermaid diagrams) — the canonical auth doc covering the single-flag-rules-everything model, RBAC, three providers, login flow sequence diagrams, attribution data model, transport linking flow, security model, migration story, full env-var reference.
- **`docs/AUTH-OIDC.md`** — provider-specific recipes, troubleshooting matrix, end-to-end flow diagram.
- **6 existing docs updated** — `CLI-REFERENCE.md` (full `gluon user *` section), `WEB-DASHBOARD.md` (auth UI walkthrough), `TELEGRAM-BOT.md` (`/link` + `/unlink`), `DISCORD-BOT.md` (`link-account` + `unlink-account`), `ARCHITECTURE.md` (auth components + SQL DDL for new tables), `API.md` (13 new endpoints across 4 grouped tables).
- **README** — Multi-User Authentication feature section, deep-link to `docs/AUTH.md`, two new entries in the Documentation table, and an 11-row env-var block listing all `GLUON_AUTH_*` / `GLUON_OIDC_*` / `GLUON_LOCAL_AUTH_ENABLED` knobs.

### Backwards compatibility

When `GLUON_AUTH_ENABLED=false` (the default):

- No login UI, no role checks, no API gating
- Attribution columns stay `NULL`
- The bots resolve approvals to `decided_by="telegram:<id>"` only — no Gluon user binding
- The CLI `gluon user *` commands still work (so you can pre-stage users before flipping the flag)

To upgrade an existing single-user install: seed the first admin with `gluon user add me --role admin`, set `GLUON_AUTH_ENABLED=true` in your `.env` / compose file, restart. Pre-existing runs/tasks/approvals keep `user_id=NULL` (they predate the multi-user era). Roll back by setting the flag back to `false` — no data is lost.

### Migration safety

- All schema changes are additive (`ALTER TABLE ADD COLUMN`, `CREATE TABLE IF NOT EXISTS`)
- Existing single-user databases get the auth tables on next startup but they stay empty
- The `pending_approvals.decided_by` legacy string column is preserved alongside the new FK for transport-side audit visibility
- Sessions and link codes are swept hourly so old auth artifacts don't accumulate

### Test coverage

- **165 new tests** added across `test_auth.py`, `test_attribution.py`, `test_transport_attribution.py`, `test_self_serve_link.py`, `test_oidc.py`
- **Total: 2081 passing** (was 1916 at start of D5)
- Coverage: store CRUD round-trips, RBAC enforcement, attribution wiring, transport-bound user resolution, OIDC env validation, OIDC `resolve_or_provision` (all paths), feature-detection endpoint matrix

### Security additions

- **argon2id** with library defaults for local password hashing
- **DB-backed sessions** (not JWT) so server-side logout actually invalidates
- **`sameSite=lax` + `httpOnly`** on session cookies; `secure` auto-elevated on HTTPS
- **Session rotation** on credential change, role change, or disable
- **OIDC ID token validation** via Authlib (signature/iss/aud/nonce/exp); JWKS auto-fetched and cached
- **Auto-provision REFUSED** at config-load time if `DOMAIN_ALLOWLIST` is unset — defense-in-depth
- **Open-redirect refusal** — `?next=…` post-OIDC-login constrained to relative paths
- **No silent chat-account takeover** — `consume_link_code` returns `chat_taken` if a chat ID is already bound to another user
- **Code-only link flow** — only the code travels to the bot; user_id stays server-side. A leaked code can only bind the *original requester's* account.

### Quality

- All migrations idempotent, wrapped in `try/except sqlite3.OperationalError` in the runner
- ruff + ruff format + mypy: clean
- bun tsc + biome + bun build: clean
- Every CI check (8 jobs) passed before each merge

## [0.10.0] - 2026-04-25

Headline: **Multi-cloud LLM backend abstraction** (Bedrock / Anthropic / Vertex / Foundry) +
**Theme C observability** (Timeline, Tool usage, Reasoning threading, Session explorer,
Session cleanup). Plus 31 Dependabot alerts cleared, SDK bump to 0.1.66, and a non-mocked
SDK integration safety net.

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
