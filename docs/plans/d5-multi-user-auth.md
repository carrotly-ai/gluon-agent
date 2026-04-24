# D5 — Multi-User Auth Design

**Status**: Design (pre-implementation)
**Date**: 2026-04-25
**Owner**: TBD
**Target version**: 0.11.0 or 1.0.0
**Effort estimate (from roadmap)**: 5-8 weeks
**Roadmap ref**: [roadmap-2026-04.md §D5](roadmap-2026-04.md)

---

## TL;DR

Gluon today assumes **one user**. Everything — projects, runs, budgets, approval decisions — is scoped to the single operator holding the database. We want to deploy Gluon to team servers without giving everyone full access, without losing the one-user simplicity for solo deployments, and without rearchitecting the whole app.

This doc proposes the **minimum viable multi-user story**: pluggable auth provider (OIDC or local accounts), `User` as a new first-class model that hangs off everything already scoped, RBAC for the three action categories that matter (spend money, approve destructive actions, change global settings), and a clean opt-in migration that leaves single-user installs untouched.

The hard parts are **not** auth — libraries exist for that. The hard parts are:
1. Attribution across 8 years of existing data (runs, approvals, tasks, settings)
2. Transport identity mapping (Telegram userId ↔ Gluon userId)
3. Not breaking the existing single-user deployment pattern
4. Where the boundary between "owner of a workspace" and "user of a workspace" sits

---

## 1. Goals and non-goals

### Goals
1. **Team deployment.** Deploy Gluon to a shared server where 3-30 engineers can log in, run their own tasks, see their own history, approve their own risky operations.
2. **RBAC for risk.** Per-user controls on the things that have real cost or blast radius: running Opus, approving destructive tool calls, editing global settings.
3. **Audit trail.** Every run, approval, setting change, and budget decision attributable to a specific user. No "some user approved this" rows.
4. **Zero-cost for single-user.** `GLUON_AUTH_ENABLED=false` (default) preserves every existing pattern. Solo operators pull v1.x and nothing changes.
5. **Transport identity unified.** When Alice (Gluon user `alice`) clicks an approval button in Telegram, the system records *Alice* as the approver, not "the Telegram ID 347291".

### Non-goals
- **Not an identity provider.** We don't host logins; we integrate with one (OIDC).
- **Not SSO across Gluon instances.** Each Gluon deploy is its own realm.
- **Not fine-grained ACLs per-project.** Workspace-level role is the finest grain v1 ships. Project-level RBAC is a future add-on if anyone asks.
- **Not user-selectable LLM providers.** The provider stays org-level. RBAC controls who can *change* it, not who uses what.
- **Not a billing integration.** Budgets stay in `Workspace` and `Agent`; attributing spend to a user is a feature, charging them isn't.

---

## 2. Background: what the single-user model looks like today

The database has no concept of a user. The environment has one:

| Concept | Where it lives today | Scoped by |
|---|---|---|
| Projects | `projects` table | — (global) |
| Workspaces | `workspaces` table | — (global) |
| Runs | `execution_runs` table | project_id |
| Approvals | `pending_approvals` table | run_id |
| Settings | `settings` table, env vars | — (global) |
| Agent identities | `agents` table (v0.10.0 addition) | workspace_id |
| Tasks | `orchestrator_tasks` table | project_id |
| Budgets | `Workspace.daily_budget_usd`, `Agent.monthly_budget_usd` | workspace / agent |
| Chat access | `GLUON_TELEGRAM_USERS`, `GLUON_DISCORD_USERS` | transport user ID |

The only current "auth" is the chat-bot allowlist — a set of Telegram/Discord user IDs that are allowed to send commands. There is no association between those IDs and anything in the database; they're a binary gate.

The web dashboard has no auth at all. It binds to `localhost:45866` by default and assumes the operator controls the machine.

---

## 3. User model

### 3.1 The `User` model

```python
class User(BaseModel):
    id: str                      # uuid4
    username: str                # unique, URL-safe
    display_name: str
    email: str | None            # optional; required for OIDC
    auth_provider: AuthProvider  # "local" | "oidc"
    auth_subject: str            # OIDC `sub` claim, or local-account hash
    role: UserRole               # "admin" | "operator" | "viewer"
    created_at: datetime
    last_login_at: datetime | None
    disabled: bool = False
    # Transport identity links (used for approval/chat attribution)
    telegram_user_id: int | None = None
    discord_user_id: int | None = None
```

### 3.2 Roles (v1 — three tiers)

```python
class UserRole(StrEnum):
    ADMIN = "admin"       # All powers
    OPERATOR = "operator" # Default for team members
    VIEWER = "viewer"     # Read-only
```

| Capability | Admin | Operator | Viewer |
|---|---|---|---|
| View own runs | ✅ | ✅ | ✅ |
| View other users' runs | ✅ | ✅ *(v1: yes; v2: configurable)* | ✅ *(same)* |
| Create runs (any model) | ✅ | ✅ | ❌ |
| Approve own pending approvals | ✅ | ✅ | ❌ |
| Approve others' pending approvals | ✅ | ❌ *(except emergency break-glass flag)* | ❌ |
| Edit settings | ✅ | ❌ | ❌ |
| Create/delete workspaces | ✅ | ❌ | ❌ |
| Set budgets | ✅ | ❌ | ❌ |
| Invite/disable users | ✅ | ❌ | ❌ |
| Switch LLM provider | ✅ | ❌ | ❌ |
| Cancel any running run | ✅ | own only | ❌ |

### 3.3 Why not project-level RBAC?

Two reasons:
- **Existing code doesn't model ownership at all.** Every `Project` is a dumb pointer at a filesystem path. Adding owners is invasive.
- **The use case is 95% "whole team sees everything, only admins change config"** — matches operator/viewer split. Workspace-level access (stretch goal for v2) covers the other 5%.

If workspace-level access becomes desired, the migration path is: add `workspace_members(user_id, workspace_id, role)` table, default-populate with everyone on first migration, then let admins prune.

---

## 4. Authentication

### 4.1 Two backends, one interface

```
┌──────────────────────┐
│   Gluon auth layer   │
├──────────────────────┤
│  AuthProvider ABC    │  ← user/password dance happens here
├──────────┬───────────┤
│  Local   │   OIDC    │  ← two concrete backends in v1
└──────────┴───────────┘
```

**Why two?** Solo deployers running Gluon on a Mac mini don't want to stand up Keycloak. Enterprise deployers don't want password hashes in SQLite. Both are legit.

### 4.2 Local accounts (simple deployments)

- `users` table holds `auth_subject = argon2(password + salt)`
- `gluon user add <username> [--role operator]` at the CLI prompts for a password
- On login, password is hashed and compared
- Session cookie (signed, httpOnly, sameSite=Strict, 7-day TTL, rolling)

### 4.3 OIDC (org deployments)

- Standard Authorization Code flow with PKCE (handled by `authlib` or similar)
- Config: `GLUON_OIDC_ISSUER`, `GLUON_OIDC_CLIENT_ID`, `GLUON_OIDC_CLIENT_SECRET`, `GLUON_OIDC_REDIRECT_URI`
- First login with a new `sub` claim auto-provisions a `User` row with `role = operator` (configurable default via `GLUON_OIDC_DEFAULT_ROLE`)
- Admins can bump users to admin after first login
- Same cookie-based session as local accounts after the OIDC dance

### 4.4 Session management

- **Storage:** `sessions` table with `(id, user_id, created_at, expires_at, last_seen_at, ip, user_agent)`.
- **Cookie:** `gluon_session`, signed with a server-side secret, httpOnly/secure/sameSite=Strict.
- **Rotation:** on password change, disable, role change → invalidate all sessions.
- **Rolling TTL:** refresh the cookie's expiry on every request so active users don't get kicked.
- **No JWT in v1.** A DB lookup per request is fine at Gluon's scale. JWTs add complexity (revocation, key rotation) for zero benefit.

### 4.5 CLI authentication

The CLI lives on the operator's machine and historically has had no auth. Two options:
- **Option A (strict):** CLI must log in with `gluon auth login`, gets an API token, stores in `~/.gluon/credentials` (mode 0600). Every CLI call includes the token.
- **Option B (lenient):** CLI operating on a local DB inherits that DB's trust. CLI against a remote Gluon (`GLUON_HOST` set) requires `auth login`.

**Recommendation: Option B.** The single-user pattern (CLI against local SQLite) is too valuable to break. Remote CLI is a new use case where we can add the dance.

---

## 5. RBAC enforcement

### 5.1 Where the check goes

One middleware at the FastAPI layer, one decorator at the CLI layer. Both resolve the current user and attach it to the request.

```python
# FastAPI
@app.post("/api/runs", dependencies=[Depends(require_role("operator"))])
async def create_run(req: CreateRunRequest, user: User = Depends(current_user)):
    ...

# CLI
@app.command("run")
@require_auth()  # no-op when single-user
def run(project: str, prompt: str, ctx: typer.Context):
    user = ctx.obj["user"]  # single-user mode: a default system user
    ...
```

### 5.2 Single-user mode compatibility

When `GLUON_AUTH_ENABLED=false` (default):
- `current_user` returns a built-in `_SYSTEM_USER` with `role=admin`
- `require_role` is a no-op
- CLI skips token lookup
- Existing behavior completely unchanged

This is the critical lever. Multi-user is opt-in. If someone never flips the flag, their deployment never changes.

### 5.3 Chat-bot RBAC

The Telegram/Discord allow-list becomes a **user identity map** instead of a gate:

Before:
```
GLUON_TELEGRAM_USERS=123456,789012  # raw user IDs, anyone in list = full access
```

After (when auth is enabled):
- Users link their Telegram/Discord ID to their Gluon account via `/link` command
- On any chat command, the transport resolves `telegram_user_id` → `User`
- If no link exists, the user is rejected with instructions to `/link`
- All chat commands respect the user's role

The old env var becomes a fallback "initial admin bootstrap" list — anyone whose ID is in `GLUON_TELEGRAM_USERS` gets auto-linked to an admin account on first interaction. After bootstrap, that env var can be unset.

---

## 6. Data-model migration

### 6.1 Tables to modify

All tables that currently represent "a thing one user did" need a `user_id` column. That's:

| Table | New column | Source of truth |
|---|---|---|
| `execution_runs` | `user_id` | User who submitted the run |
| `orchestrator_tasks` | `created_by_user_id` | Admin/operator who created the task |
| `pending_approvals` | `decided_by_user_id` | Already exists as `decided_by` (string); migrate |
| `settings` | `updated_by_user_id` | Audit only; no enforcement change |
| `workspaces` | `created_by_user_id` | Admin who created |
| `agents` | (no change — agents are AI identities, not users) | |

All additions are **nullable** on existing rows. The migration just adds columns.

### 6.2 The "unknown user" problem

Existing rows have no user — they were created under single-user mode. We have two options:
- **A.** Leave `NULL`, display as "system" in UIs. Audit trail is imperfect for pre-migration rows but accurate going forward.
- **B.** On first-time OIDC login / admin user creation, backfill all `NULL` user_ids to that bootstrap admin.

**Recommendation: A.** Option B falsely attributes historical actions. Leave `NULL` meaning "pre-auth era" and label accordingly in the UI.

### 6.3 Migration sequence

```
v0.11.0 (optional prep):
  - Add `users`, `sessions` tables
  - Add nullable `user_id`/`created_by_user_id` columns to all relevant tables
  - No auth enforcement; everything still works in single-user mode
  - `gluon user add` CLI command becomes available (creates local account rows)

v0.12.0 (enable):
  - `GLUON_AUTH_ENABLED=true` starts enforcing auth on web API + remote CLI
  - Existing single-user deployments that DON'T set the flag are untouched
  - Telegram/Discord start requiring `/link` if auth is enabled

v1.0.0 (flip default, if we want):
  - Default `GLUON_AUTH_ENABLED=true` in fresh installs
  - Existing `gluon.db` that has no users and no flag → prompt for first-admin setup on startup (or continue in single-user mode based on a one-time setup choice)
```

Two-release rollout lets users upgrade on their own timeline.

---

## 7. API surface changes

### 7.1 New endpoints

```
POST   /api/auth/login          # username + password (local) or kick off OIDC flow
GET    /api/auth/callback       # OIDC callback
POST   /api/auth/logout         # clear session
GET    /api/auth/me             # current user info

GET    /api/users               # admin only — list
POST   /api/users               # admin only — create (local) or invite
PATCH  /api/users/{id}          # admin only — role change, disable
DELETE /api/users/{id}          # admin only — disable (not hard delete)

POST   /api/users/me/link-transport  # link telegram/discord ID to current user
```

### 7.2 Headers

- Requests must include the `gluon_session` cookie OR an `Authorization: Bearer <api_token>` header
- API tokens: separate `api_tokens` table (`user_id`, `token_hash`, `name`, `last_used`, `expires_at`); user can generate them from the dashboard
- All existing endpoints add an implicit "current user" context; response bodies may optionally include `created_by_user` enrichments

### 7.3 Backward compatibility

- With `GLUON_AUTH_ENABLED=false`, every endpoint behaves exactly as before (no cookie required, no attribution in responses). This is the critical promise for existing users.

---

## 8. Web dashboard changes

### 8.1 New screens

- **Login page** (`/login`) — username/password form + OIDC button
- **User menu** (top-right) — username, logout link, "My API tokens"
- **Admin → Users** page — list users, invite, change role, disable
- **Activity feed gains "by user"** filter — see what each team member is doing

### 8.2 Changed screens

- **Run cards / Run detail** — new "Submitted by" chip showing the user
- **Approval cards** — "Decided by" uses the user's display name, not a raw transport ID
- **Settings page** — locked for non-admins; viewable but not editable (or gated entirely)

### 8.3 Session UX

- Rolling cookie expiry means active users don't get kicked. If expired, a modal appears ("your session expired — sign in again") instead of losing draft prompts.
- On 401 from any API call, redirect through login, then return to the page that kicked.

---

## 9. Transport (Telegram / Discord) changes

### 9.1 Linking flow

```
User: /link 6-digit-code
Bot: ✓ linked to alice@org.example
```

The user generates a code from the web dashboard (`My Account → Link chat account`), pastes it into the bot, and the bot calls an internal `link_transport` API that writes `users.telegram_user_id = <bot_user_id>`. Code is single-use and expires in 10 minutes.

### 9.2 Command enforcement

Every chat command reads the user from the transport user ID → `users` lookup. If no link exists, reply with `"Not linked. Get a code from <dashboard URL>/account/link."`.

Role is enforced the same way as web:
- Viewers can `/status`, `/logs`, `/runs`, etc.
- Operators can `/run`, `/approve` (their own), `/cancel` (their own)
- Only admins can `/settings`, `/user-add`, etc.

### 9.3 Approval attribution

The existing approve/deny buttons in #60 (Discord) and #59 (Telegram) carry the button-presser's transport user ID via the callback. Today that's recorded in `PendingApproval.decided_by` as a string. The migration:

- `decided_by_user_id` (nullable FK) becomes authoritative
- `decided_by` (existing string column) continues to exist as "legacy transport ID" for pre-migration rows, populated as `telegram:<id>` or `discord:<id>` for attribution in single-user mode

This preserves the pre-auth audit trail while adding the post-auth enrichment.

---

## 10. CLI changes

### 10.1 New commands

```bash
gluon auth login [--host HOST]          # interactive; stores token at ~/.gluon/credentials
gluon auth logout
gluon auth whoami
gluon user add <username> [--role …]    # admin-only; local backend
gluon user list
gluon user disable <username>
gluon user set-role <username> <role>
```

### 10.2 Changed commands

None. Every existing command keeps its exact signature. Auth is injected implicitly via the middleware/decorator layer described in §5.

### 10.3 Config resolution

```
GLUON_HOST=<url>           # if set, CLI is in "remote" mode — requires auth login
~/.gluon/credentials       # token file for remote mode; mode 0600
(unset)                     # CLI is in "local" mode — talks to local SQLite directly
```

Local-mode CLI continues to have implicit full trust (it has the DB file — any restriction is theater).

---

## 11. Security posture

### 11.1 Threat model
- **Attacker on the same network** trying to access the web dashboard → auth cookie + HTTPS (via `GLUON_SSL_CERTFILE`, already supported)
- **Malicious chat-bot user** that got added to a server's allowlist somehow → linked-account requirement means their user ID won't resolve to anyone; bot rejects
- **Compromised session cookie** → rotation on password change / role change / disable; manual "logout all sessions" admin action
- **Compromised API token** → tokens have expiry; user can revoke from dashboard
- **Compromised DB file** → password hashes are argon2 (expensive); session IDs are random; API tokens are hashed at rest

### 11.2 Explicitly not addressed in v1
- **Rate limiting on login** (should add, but cheap to add later with a `last_login_attempt_at` column)
- **Password complexity requirements** (minimum 12 chars is the whole policy)
- **MFA** (OIDC delegates this; local accounts don't have it v1)
- **Audit log of admin actions** (the `settings` table's `updated_by_user_id` is a start; full audit log is a v2 project)

---

## 12. Implementation phases

Split the effort over four 1-2 week phases. Each ships independently and provides value even if the next never ships.

### Phase 1 — Identity foundation (Week 1-2)
- `users`, `sessions`, `api_tokens` tables + migrations
- `AuthProvider` ABC + `LocalAuthProvider`
- `gluon user add/list/disable/set-role` CLI commands
- `_SYSTEM_USER` singleton for single-user compat
- All new code paths gated by `GLUON_AUTH_ENABLED` (default false)
- **Ships as v0.11.0**; no user-visible change for single-user installs

### Phase 2 — Web dashboard auth (Week 3-4)
- Login page + cookie middleware
- `/api/auth/*` endpoints
- `/api/users` admin endpoints
- User menu + login flow in the UI
- Admin users page
- Per-request `user_id` attribution on new runs/approvals
- **Ships as v0.11.x**; enables opt-in web auth

### Phase 3 — OIDC + SSO (Week 5-6)
- `OidcAuthProvider`
- Config env vars + dashboard config screen
- Auto-provision on first login
- **Ships as v0.12.0**; enables enterprise-style deployments

### Phase 4 — Transport integration (Week 7-8)
- `/link` flow for Telegram + Discord
- RBAC enforcement in bot commands
- Approval attribution to users instead of raw transport IDs
- **Ships as v0.12.x**; completes the team-deployment story

Phases 1 + 2 alone are a defensible v1 ("Gluon on a team server behind a login page, CLI users are trusted"). Phases 3 + 4 sharpen it but aren't day-one necessary.

---

## 13. Open questions

### Q1. Should sessions be DB-backed or Redis-backed?
**Current:** Gluon already runs Redis (for the event bus). Using it for sessions would give us free cross-process invalidation and easy rate limiting. But one more critical path on Redis = one more operational concern.
**Leaning:** DB-backed. Session lookup is cheap, the event bus is already a Redis dependency; no need to add session-criticality to it.

### Q2. Where does the session secret live?
**Options:**
- Env var `GLUON_SESSION_SECRET` (requires operator to set; losing it invalidates all sessions)
- Auto-generated in `gluon.db` on first run (easier; more opaque)

**Leaning:** Auto-generated, stored in `settings` table, overrideable by env var for deployments that want to rotate deliberately.

### Q3. Should the "viewer" role be a separate user or a capability on operator?
The matrix in §3.2 has `viewer` as its own role. Alternative: don't ship viewer in v1, just admin + operator; anyone who needs read-only access gets operator with no team server-side gating. Simpler.

**Leaning:** Ship viewer. Security teams will ask for it; cost of shipping is tiny.

### Q4. First-admin bootstrap
Three options for the first admin when `GLUON_AUTH_ENABLED=true` flips:
- **A.** Env var `GLUON_BOOTSTRAP_ADMIN_USERNAME`/`PASSWORD` on startup — creates the account
- **B.** First OIDC login is implicitly admin
- **C.** CLI-only: `gluon user add --role admin` must be run before the flag is flipped

**Leaning:** A for local backend (plus warning to change the password), B configurable for OIDC (disabled by default — force admin to be explicitly role-changed), C acceptable fallback.

### Q5. Per-user budgets?
Do we add `User.daily_budget_usd` / `User.monthly_budget_usd`? The Agent model already has these but the semantics are different (Agent = AI identity, User = human).

**Leaning:** Yes, but in Phase 4 or later. Not blocking v1. When added, they compose with workspace budgets — both must be satisfied for a run to start.

### Q6. Rate limiting on login?
A simple `login_attempts(user_id, ip, attempt_at)` table with a "5 in 5 minutes = 15 minute lockout" policy would block casual credential stuffing. Not critical for v1 but easy.

**Leaning:** Skip for v1 — the attack surface of a Gluon instance on a corporate network is narrow. Add in a point release if anyone files the issue.

### Q7. Workspace-level RBAC?
Stated as non-goal in §1. Worth revisiting for v2? Probably yes for larger orgs (different teams, different budgets, different approval policies per workspace).

**Leaning:** Defer. Address when someone with >5 workspaces asks.

---

## 14. Dependencies

| Concern | Library | Notes |
|---|---|---|
| Password hashing | `argon2-cffi` | Standard, fast enough, actively maintained |
| OIDC client | `authlib` | Most mature option in Python ecosystem |
| Session signing | `itsdangerous` | Already transitive via FastAPI; no new dep |
| Cookie handling | FastAPI built-in | — |

All additive. No existing deps are removed or upgraded by the auth work itself.

---

## 15. Breaking changes (in one place)

When `GLUON_AUTH_ENABLED=true` is flipped:
- Web dashboard requires login. Existing bookmarks to `http://host:45866/runs/xyz` redirect to `/login?next=/runs/xyz`.
- Remote CLI (`GLUON_HOST=<url>` set) requires `gluon auth login` first.
- Telegram/Discord allowlist becomes an identity-link requirement; existing users need to `/link` once.
- API tokens replace the implicit "anyone on the server can hit the API" assumption.

When `GLUON_AUTH_ENABLED=false` (default): **zero breaking changes.** The foundation tables exist but behavior is identical to pre-0.11.0.

---

## 16. Acceptance criteria for v1 (Phases 1 + 2)

Minimum to call this shipped:
- [x] Fresh install + `GLUON_AUTH_ENABLED=false`: existing single-user flow works identically to 0.10.x
- [x] Fresh install + `GLUON_AUTH_ENABLED=true` + bootstrap admin: web dashboard requires login; admin can create additional users
- [x] Operator user can create/view/resume their own runs
- [x] Operator user cannot edit global settings (403 from API, hidden in UI)
- [x] Admin user can change another user's role or disable them
- [x] Changing a user's role or disabling them invalidates their active sessions
- [x] `PendingApproval.decided_by_user_id` populated on all approvals made after the flag flip
- [x] Existing runs (pre-flag) show `Submitted by: system` in UIs, not blank
- [x] `gluon doctor` has a new "auth config" check that flags missing bootstrap admin, weak session secret, disabled argon2, etc.
- [x] Migration from 0.10.x → 0.11.x with flag=false is a simple `docker compose pull && up -d` — no manual steps

---

## 17. Inspiration / prior art

- **Clerk** (the SaaS auth used by many Next.js apps) for their "auth middleware wraps everything, default behavior is unchanged" pattern
- **GitLab's self-managed auth** for the local/OIDC switchover
- **PostgREST's JWT-based RBAC** as the opposite of what we want (we're rejecting JWT)
- **Existing Gluon patterns**:
  - Transport allowlist (current) — v1's linked-account flow is a supersets of this
  - Approval system (v0.10.0) — already has a "who decided" column; we're strengthening the attribution, not inventing it
  - Workspace budgets (v0.10.0) — the compose-with-user-budgets idea mirrors how agent + workspace budgets already compose

---

## 18. Out-of-scope, likely-never

- Per-project RBAC (mentioned in §3.3)
- User self-service registration (not sign-up; admins invite)
- SCIM / bulk user provisioning
- Audit export to external SIEM
- Passwordless / magic-link login (OIDC is the answer)
- Browser SSO across multiple Gluon instances

If any of these come up as real user demand, we add a plan doc for it then. Don't pre-build.
