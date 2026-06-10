# Gluon Agent — Security Findings (WS-11 critic pass)

**Date:** 2026-06-10 · **Scope:** the four categories the main code-quality audit did not cover — API input-validation/authz, auth & OIDC, `git_manager.py`, secrets handling.

> These were produced by the WS-11 security-critic pass of the [remediation plan](remediation-plan-2026-06-10.md). **None of these are auto-fixed.** Every item touches authorization, CORS, secret handling, or input validation — categories that change security policy and behavior, so per the project's "stop and ask before changing auth/authz/validation logic" rule they are left for the owner to review and action (ideally as their own tracked tickets). Each carries a concrete recommended fix.

## Overall risk: HIGH (for multi-user / network-exposed deployments)

The codebase has **strong cryptographic and injection hygiene** — parameterized SQL, no `shell=True`/`os.system`/`eval`, argon2id password hashing, constant-time webhook HMAC, Authlib-managed OIDC with enforced domain allowlist. The exposure is the **authorization layer**: it is wired into only ~8% of routes, so the documented role model is effectively unenforced, and a wildcard-credentialed CORS policy plus a secret-leaking settings endpoint compound it once the dashboard leaves `localhost`.

> Context: `GLUON_AUTH_ENABLED` defaults **off** (single-user mode), so a default localhost deployment is not multi-user-exploitable. These findings bite when auth is enabled (multi-user) and/or the dashboard is network-exposed.

## Findings

### P0 — Mutating/destructive API routes have no auth or role enforcement — **FIXED (Phase 1)**
**src/gluon/web/api.py** — ~140 of 159 routes. There is no app-wide auth dependency (`create_app`); only ~12 routes attach `Depends(current_user_dep)`/`require_admin`. Even with `GLUON_AUTH_ENABLED=true`, destructive endpoints are reachable with **no session cookie**, and the `viewer`/`operator`/`admin` role model (docs/AUTH.md) is unenforced. Ungated examples: `DELETE /api/projects/{id}`, `POST .../force-push`, `DELETE /api/branches/{name}`, `PUT /api/settings/{key}`, `PUT/DELETE /api/workspaces/{id}/env-vars`, `POST .../clone|merge|create-pr|rebase`, `POST/DELETE /api/webhooks`.
- **Recommended fix (owner decision):** add an app-wide auth dependency (`FastAPI(dependencies=[Depends(current_user_dep)])` or a router-level dependency), then layer `make_require_role(store, UserRole.OPERATOR)` on mutating routes and `UserRole.ADMIN` on settings/webhook/env-var routes; leave only `/api/auth/*`, the signature-gated GitHub webhook, and health/version anonymous. One wiring change, not 140 edits. **Needs a decision on the exact per-route role matrix.**

### P1 — Unauthenticated `/api/settings` returns all DB-stored secrets in plaintext — **FIXED (Phase 1)**
**src/gluon/web/api.py:3316** → `store.get_all_settings()` returns every key/value unredacted with no auth. The GitHub webhook secret can be stored here (`github_webhook_secret`), so anyone reaching the dashboard port can read it (defeating the webhook HMAC) plus any operator-set tokens.
- **Recommended fix:** gate behind admin auth (per P0) **and** redact secret-looking keys (`*secret*`, `*token*`, `*password*`, `*_key`) to `"********"`. Never round-trip secret values to clients.

### P1 — CORS allows any origin with credentials — **FIXED (Phase 1)**
**src/gluon/web/api.py:283** — `allow_origins=["*"]` + `allow_credentials=True`. Any site the operator visits can make credentialed cross-origin requests and read responses; combined with P0 this enables drive-by destructive actions. (Starlette reflects the Origin, so the browser `*`+credentials rejection doesn't save you.)
- **Recommended fix:** explicit origin allowlist (e.g. `GLUON_ALLOWED_ORIGINS`, default `http://localhost:45866`); set `allow_credentials=False` if not needed cross-origin.

### P2 — git branch ops: argument injection via leading-dash names — **FIXED (Phase 2)**
**src/gluon/git_manager.py** — `delete_branch`/`rename_branch`/`change_base_branch` pass user branch names as positional git args, so a name beginning with `-` is parsed as a git option. No *shell* injection (uniform `create_subprocess_exec`), impact bounded, but unvalidated input → subprocess arg.
- **Recommended fix:** for `git branch` ops add a `--` separator before user refs; for `git checkout <branch>` (`--` would mean "paths follow"), reject branch names matching a leading `-` (validate `^[A-Za-z0-9._/-]+$` and no leading dash) at the API boundary.

### P3 — Local-login session cookie omits `Secure` under HTTPS — **FIXED (Phase 2)**
**src/gluon/web/api.py:1912** sets the session cookie without `secure=`. The OIDC callback already does `secure=request.url.scheme == "https"`.
- **Recommended fix:** mirror the OIDC path on the local-login cookie.

### P3 — Vercel token passed as a CLI argument (process-list exposure) — **FIXED (Phase 2)**
**src/gluon/web/api.py:3340** — `["vercel","whoami",f"--token={token}"]` exposes the token in `/proc/<pid>/cmdline`. Endpoint also ungated.
- **Recommended fix:** pass via subprocess `env=` (`VERCEL_TOKEN`) and gate behind admin auth.

## Verified clean (no action)
- **SQL injection:** all queries parameterized; the few f-string queries interpolate only fixed `col = ?` fragments with bound params.
- **Command injection:** no `shell=True`/`os.system`/`eval`/f-string shell commands; `clone` uses a strict `^https://github\.com/...$` regex + arg-list (SSRF constrained to github.com).
- **Webhook auth:** HMAC-SHA256 with `hmac.compare_digest`, enforced (401 on missing/invalid).
- **Password hashing:** argon2id with rehash-on-login and a 12-char minimum.
- **Sessions / link codes:** UUID4 session IDs; link codes use `secrets.choice` (~50 bits), 10-min TTL, single-use.
- **OIDC:** Authlib-managed state+nonce; ID-token signature/issuer/audience/nonce validated; domain allowlist enforced for auto-provision; open-redirect guarded.
- **Secrets logging:** no tokens/secrets/passwords logged in the request path.
