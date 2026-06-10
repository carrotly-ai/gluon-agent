# Gluon Agent — Security Remediation Plan (pre-public-release)

**Created:** 2026-06-10 · **Source:** [security-findings-2026-06-10.md](security-findings-2026-06-10.md) · **Gate:** must be complete before wider public release.

## Goal

Make a network-exposed, multi-user Gluon deployment safe. Today the auth *primitives* are solid (argon2id, OIDC, role model, `SYSTEM_USER` no-op when auth is off) but the *enforcement* is wired into only ~12 of 159 API routes, and a couple of endpoints/config choices leak secrets or invite CSRF. This plan closes that gap **without breaking single-user mode** (`GLUON_AUTH_ENABLED=false` must stay a no-op).

## Guiding approach — fail-closed, defense-in-depth

The audit's bug is *opt-in* protection: a route is exposed unless someone remembered to add a `Depends`. We flip that to **opt-out**:

1. **Auth middleware (default-deny authentication).** One middleware rejects any request without a valid session, *except* an explicit anonymous allowlist (login, OIDC, the signature-gated webhook, health/version, static SPA). A newly-added route is protected automatically. When `GLUON_AUTH_ENABLED=false` the middleware short-circuits to a no-op (single-user mode unchanged).
2. **Per-route role dependencies for RBAC.** Add `Depends(require_operator)` / `Depends(require_admin)` to mutating/config routes for the viewer/operator/admin granularity. If one is ever missed, the route still requires *authentication* (from the middleware) — a small residual, not today's anonymous-destructive hole.

> Why both: middleware alone can't cleanly know each route's *required role* (Starlette middleware runs before routing, so `scope["route"]` isn't reliably available). Middleware gives fail-closed authentication; per-route deps give roles. Together they're belt-and-suspenders.

The role hierarchy already exists: `_role_rank` = `admin 3 > operator 2 > viewer 1`; `make_require_role(min)` 403s anyone below `min` and is a no-op when auth is disabled (`src/gluon/auth.py`).

## Proposed role matrix

Derived from `docs/AUTH.md` (viewer = read-only · operator = everything except admin screens · admin = + user/secret/config management). **Confirm this before implementing** — it's the one real design decision.

| Tier | Routes | Dependency |
|---|---|---|
| **Anonymous** | `POST /api/auth/login`, `/api/auth/logout`, `GET /api/auth/providers`, `/api/auth/oidc/login`, `/api/auth/oidc/callback`, `POST /api/webhooks/github` (HMAC-gated), `GET /api/version`, `GET /api/health`, static SPA | none (middleware allowlist) |
| **Viewer** (any authenticated user) | all `GET /api/*` reads not listed below; `GET /api/auth/me`, `/api/auth/links` | `current_user_dep` (via middleware) |
| **Operator** | run mutations (`cancel`/`resume`/**`merge`**/**`create-pr`**/`fork`/`archive`/`snooze`/`recover`/`stop-loop`/`status`/`supervision/*`/`queue*`/`attachments`); project mutations (`create`/`delete`/`git/sync`/`rebase*`/**`force-push`**); `branches/*`; `queue/*`; `tasks/*` mutations; `schedules/*` mutations; `questions/:id/answer`; `workspaces/:id/scan\|clone`; merge-queue mutations | `Depends(require_operator)` |
| **Admin** | `users/*`; `settings/*`; `webhooks` CRUD (not the receiver); `workspaces` create/delete + `:id/env-vars*` + `:id/budget` + `:id/settings*`; `provider/*`; `vercel/test` | `Depends(require_admin)` |

---

## Phase 1 — Release blockers (P0 + P1)

> **PR:** `fix(security): enforce auth/RBAC on all API routes; stop secret leakage; lock down CORS`
> Nothing ships publicly until this phase is merged.

- [x] **[P0] App-wide fail-closed authentication middleware** — `src/gluon/web/api.py` (`create_app`)
  - **Do:** add an HTTP middleware that, when `is_auth_enabled()`, resolves the session cookie → user and 401s if absent/invalid, *unless* `request.url.path` matches the anonymous allowlist (compiled prefix/regex set). Short-circuit (pass-through) when auth is disabled. Attach the resolved user to `request.state` so route deps can reuse it.
  - **Done when:** with `GLUON_AUTH_ENABLED=true`, every non-allowlisted route returns 401 without a session cookie; the allowlist routes still work anonymously; with auth disabled, all routes behave exactly as today (SYSTEM_USER).
- [x] **[P0] Role dependencies on mutating + admin routes** — `src/gluon/web/api.py`
  - **Do:** add `require_operator = make_require_role(store, UserRole.OPERATOR)` and reuse `require_admin`; attach `Depends(require_operator)`/`Depends(require_admin)` per the role matrix above to every POST/PUT/DELETE/PATCH route (and admin GETs like `/api/settings`, `/api/users`).
  - **Done when:** a `viewer` gets 403 on a representative mutating route (e.g. `DELETE /api/projects/{id}`) and on every admin route; an `operator` gets 403 on admin routes but 200 on operational ones.
- [x] **[P0] Regression tests for the gate** — `tests/test_api_authz.py` (new)
  - **Do:** parametrized tests asserting (a) anonymous → 401 on a sample of each tier's routes; (b) viewer → 403 on operator+admin routes; (c) operator → 403 on admin routes, 200 on operator routes; (d) admin → 200; (e) `GLUON_AUTH_ENABLED=false` → all routes reachable as SYSTEM_USER (single-user mode intact). Include the worst offenders: delete-project, force-push, settings PUT, env-vars, webhooks.
  - **Done when:** the suite fails on the current code and passes after Phase 1.
- [x] **[P1] `GET /api/settings` — gate + redact secrets** — `src/gluon/web/api.py:3316`
  - **Do:** add `Depends(require_admin)`; redact secret-looking keys (`*secret*`, `*token*`, `*password*`, `*_key`, `*_secret`) to `"********"` (or a `has_value: bool`) before returning. Never round-trip secret values. Apply the same redaction to any other settings/env-var read path.
  - **Done when:** the endpoint requires admin and never returns a real secret value; a test asserts `github_webhook_secret` comes back redacted.
- [x] **[P1] Lock down CORS** — `src/gluon/web/api.py:283`
  - **Do:** replace `allow_origins=["*"]` with an explicit allowlist from `GLUON_ALLOWED_ORIGINS` (comma-separated; default `http://localhost:45866`). **Keep `allow_credentials=True`** (decision locked) so a separately-hosted front-end origin can still send the session cookie — but it now only works for allowlisted origins.
  - **Done when:** a request with an unlisted `Origin` is not granted `Access-Control-Allow-Origin`; the dashboard origin still works with credentials.

## Phase 2 — Hardening (P2 + P3)

> **PR:** `fix(security): input validation, secure cookies, token handling`

- [ ] **[P2] Validate git refs at the boundary** — `src/gluon/web/api.py` branch routes + `src/gluon/git_manager.py`
  - **Do:** reject branch/ref names not matching `^(?!-)[A-Za-z0-9._/-]+$` (no leading dash) in the branch/rebase/force-push request models (Pydantic validator); for `git branch` ops also pass a `--` separator before user refs. (`git checkout <branch>` can't take `--` — rely on the validator there.)
  - **Done when:** a branch named `--upload-pack=…` is rejected with 422 before reaching git.
- [ ] **[P3] `Secure` flag on the local-login session cookie** — `src/gluon/web/api.py:1912`
  - **Do:** `secure=request.url.scheme == "https"`, mirroring the OIDC callback path.
  - **Done when:** over HTTPS the session cookie carries `Secure`.
- [ ] **[P3] Pass the Vercel token via env, not argv** — `src/gluon/web/api.py:3340`
  - **Do:** invoke `vercel whoami` with `env={**os.environ, "VERCEL_TOKEN": token}` instead of `--token=` in argv; gate the route behind admin (Phase 1 covers the gate).
  - **Done when:** the token no longer appears in `/proc/<pid>/cmdline`.

## Phase 3 — Release-readiness hardening (recommended)

> **PR:** `feat(security): auth-required posture, rate limiting, security headers, audit log`
> Not strictly blocking, but expected for a public service.

- [ ] **Refuse silent insecure default** (decision locked: refuse) — `src/gluon/cli.py` (`gluon web`) / `create_app`
  - **Do:** if the server binds a non-loopback host with `GLUON_AUTH_ENABLED=false`, **fail to start** with a clear error unless `GLUON_INSECURE_OK=1` is set. Loopback binds and auth-enabled binds start normally.
  - **Done when:** `gluon web --host 0.0.0.0` with auth disabled and no override exits non-zero with an explanatory message.
- [ ] **Rate-limit auth endpoints** — `POST /api/auth/login`, `/api/auth/link-codes`
  - **Do:** per-IP throttle (e.g. 5/min) to blunt password/link-code brute force.
- [ ] **Security headers** — middleware
  - **Do:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` (or CSP `frame-ancestors`), `Referrer-Policy`, and HSTS when served over HTTPS.
- [ ] **Audit log of privileged actions** — reuse the activity log
  - **Do:** record `(user_id, action, target, ts)` for destructive/admin routes (delete project, force-push, merge, settings/env-var/webhook/user changes) so multi-user actions are attributable.
- [ ] **Verify env-var read paths don't leak** — `workspaces/:id/env-vars` GET
  - **Do:** confirm values are masked (keys only) on read; the WS-7/audit noted the masked-keys pattern — make sure it's applied consistently.

---

## Acceptance criteria (release gate)

1. With `GLUON_AUTH_ENABLED=true`: no route outside the anonymous allowlist is reachable without a valid session; the role matrix is enforced (viewer can't mutate, operator can't admin).
2. No endpoint returns a secret value; CORS is origin-restricted.
3. `GLUON_AUTH_ENABLED=false` (single-user) behaves identically to today — full test suite green in both modes.
4. New `tests/test_api_authz.py` covers anonymous/viewer/operator/admin × representative routes and both auth modes.
5. `docs/AUTH.md` updated to state the enforcement is now app-wide + the role matrix.

## Effort / sequencing

- **Phase 1** is the bulk: 1 middleware + ~80 per-route role decorators (mechanical once the matrix is agreed) + the authz test suite + settings redaction + CORS. ~1 focused PR.
- **Phase 2** is small and independent.
- **Phase 3** can land incrementally after release if time-boxed, but rate-limiting + the insecure-default warning are cheap and worth doing in the first pass.

## Decisions (locked 2026-06-10)

1. **Write-op role:** `merge` / `create-pr` / `force-push` are **operator** actions (not admin). Reflected in the matrix above.
2. **CORS:** origin allowlist via `GLUON_ALLOWED_ORIGINS`, **keep `allow_credentials=True`**.
3. **Public posture:** a non-localhost bind with `GLUON_AUTH_ENABLED=false` **refuses to start** unless `GLUON_INSECURE_OK=1`.

Phase 1 is fully specified and ready to implement.
