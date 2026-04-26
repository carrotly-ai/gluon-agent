# OIDC Authentication (D5 Phase 3)

Gluon supports **OpenID Connect** as a third auth backend alongside the built-in `local` (password) provider. Pair it with Auth0, AWS Cognito, Google Workspace, Microsoft Entra ID, Okta, Keycloak, or any spec-compliant IdP.

## When to use OIDC

- You already have an SSO provider for your team
- You want to centralize off-boarding (revoke at the IdP, all access stops)
- You need MFA / conditional access — let the IdP handle it
- You want password-less login for humans, with a few `local` service accounts kept for automation

## Single feature flag still rules everything

`GLUON_AUTH_ENABLED=false` (the default) **disables both `local` and `oidc`**. Single-user installs see zero behaviour change after upgrading. Set it to `true` to turn on the multi-user model — local + OIDC then both available, with each user bound to one provider.

## Required environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `GLUON_AUTH_ENABLED` | Yes | Must be `true`. |
| `GLUON_OIDC_ISSUER` | Yes | Discovery URL — Gluon fetches `{issuer}/.well-known/openid-configuration`. Trailing slash is normalized off. |
| `GLUON_OIDC_CLIENT_ID` | Yes | From the IdP's app/client registration. |
| `GLUON_OIDC_CLIENT_SECRET` | Yes | From the IdP. **Never commit this.** |
| `GLUON_OIDC_REDIRECT_URI` | Yes | Must match what's registered with the IdP, e.g. `https://gluon.example.com/api/auth/oidc/callback`. |
| `GLUON_OIDC_PROVIDER_NAME` | No (default `OIDC`) | Display name shown on the login button. Set to `Google`, `Auth0`, etc. |
| `GLUON_OIDC_SCOPES` | No (default `openid profile email`) | Space-separated. The default works for every provider. |
| `GLUON_OIDC_AUTO_PROVISION` | No (default `false`) | When `true`, auto-create new users on first login. **Requires** `GLUON_OIDC_DOMAIN_ALLOWLIST`. |
| `GLUON_OIDC_DOMAIN_ALLOWLIST` | When `AUTO_PROVISION=true` | Comma-separated email-domain restriction. Without this, auto-provision is refused at startup. |
| `GLUON_OIDC_DEFAULT_ROLE` | No (default `viewer`) | Role assigned to auto-provisioned users. Promote individuals via the admin UI. |
| `GLUON_LOCAL_AUTH_ENABLED` | No (default `true`) | Set to `false` to disable the password endpoint entirely (OIDC-only mode). |
| `GLUON_OIDC_SESSION_SECRET` | No (auto-generated per process) | Signs the short-lived OAuth state cookie. Set in multi-replica deployments so all replicas can verify each other's redirects. |

## Two onboarding modes

### Strict mode (default — admin pre-registers users)

Safest. Admin runs:

```bash
# By email — Gluon uses email as a placeholder `sub` until first login,
# then swaps in the real OIDC sub.
gluon user add alice --auth-provider oidc --email alice@example.com --role admin

# Or by exact sub — if you have it from the IdP's directory:
gluon user add alice --auth-provider oidc --auth-subject 'auth0|abc123' \
    --email alice@example.com --role admin
```

When alice clicks "Sign in with X" for the first time, Gluon matches her by email, persists the real `sub`, and creates her session.

### Auto-provision mode (opt-in — for trusted IdPs)

Set `GLUON_OIDC_AUTO_PROVISION=true` and `GLUON_OIDC_DOMAIN_ALLOWLIST=example.com,other.org`. New users in those domains get a Gluon account on first login at the `GLUON_OIDC_DEFAULT_ROLE` (default `viewer`). Promote individuals via the admin UI.

## End-to-end flow

```
Browser            Gluon                        IdP
  │                  │                           │
  │  GET /board      │                           │
  ├─────────────────▶│                           │
  │  ◀─ LoginPage    │  GET /api/auth/providers │
  ├─────────────────▶│                           │
  │  ◀─ {oidc:{…}}   │                           │
  │                  │                           │
  │  click "Sign in" │                           │
  │  → /api/auth/oidc/login                      │
  ├─────────────────▶│                           │
  │  ◀─ 302 to IdP authorize URL (state+nonce in cookie)
  │                                              │
  │  user authenticates …                        │
  │  ◀──────────────────────────────────────────┤
  │  302 /api/auth/oidc/callback?code=…&state=…  │
  ├─────────────────▶│                           │
  │                  │ exchange code → ID token  │
  │                  │──────────────────────────▶│
  │                  │ ◀───── JWKS validation ──│
  │                  │ resolve_or_provision(sub) │
  │                  │ create UserSession        │
  │  ◀─ 302 / + Set-Cookie: gluon_session=…      │
  │  authenticated.  │                           │
```

## Provider-specific recipes

### Google Workspace

```bash
GLUON_AUTH_ENABLED=true
GLUON_OIDC_ISSUER=https://accounts.google.com
GLUON_OIDC_CLIENT_ID=<your client id>.apps.googleusercontent.com
GLUON_OIDC_CLIENT_SECRET=<your client secret>
GLUON_OIDC_REDIRECT_URI=https://gluon.example.com/api/auth/oidc/callback
GLUON_OIDC_PROVIDER_NAME=Google
GLUON_OIDC_AUTO_PROVISION=true
GLUON_OIDC_DOMAIN_ALLOWLIST=yourcompany.com
```

In Google Cloud Console → APIs & Services → Credentials, create an "OAuth 2.0 Client ID" (Web application). Authorized redirect URI must be the exact `GLUON_OIDC_REDIRECT_URI`.

### Auth0

```bash
GLUON_OIDC_ISSUER=https://your-tenant.us.auth0.com
GLUON_OIDC_CLIENT_ID=<auth0 client id>
GLUON_OIDC_CLIENT_SECRET=<auth0 client secret>
GLUON_OIDC_REDIRECT_URI=https://gluon.example.com/api/auth/oidc/callback
GLUON_OIDC_PROVIDER_NAME=Auth0
```

In Auth0 → Applications → create a Regular Web App. Callback URL must match `GLUON_OIDC_REDIRECT_URI` exactly.

### AWS Cognito

```bash
# Note: Cognito's issuer URL includes the user pool ID
GLUON_OIDC_ISSUER=https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX
GLUON_OIDC_CLIENT_ID=<cognito app client id>
GLUON_OIDC_CLIENT_SECRET=<cognito app client secret>
GLUON_OIDC_REDIRECT_URI=https://gluon.example.com/api/auth/oidc/callback
GLUON_OIDC_PROVIDER_NAME=Cognito
```

Enable the "Authorization code grant" flow on the Cognito app client. Allowed callback URL must match.

### Microsoft Entra ID (Azure AD)

```bash
GLUON_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
GLUON_OIDC_CLIENT_ID=<application (client) id>
GLUON_OIDC_CLIENT_SECRET=<client secret>
GLUON_OIDC_REDIRECT_URI=https://gluon.example.com/api/auth/oidc/callback
GLUON_OIDC_PROVIDER_NAME=Microsoft
```

In the Entra ID portal → App registrations → register a new app, add the redirect URI under "Web", and create a client secret under "Certificates & secrets".

## Bootstrap order

1. Set the `GLUON_OIDC_*` env vars and `GLUON_AUTH_ENABLED=true`
2. Restart Gluon
3. Use the CLI (which works regardless of `GLUON_AUTH_ENABLED`) to seed your first OIDC admin:
   ```bash
   gluon user add me --auth-provider oidc --email me@example.com --role admin
   ```
4. Open the dashboard, click "Sign in with [Provider]", auth at the IdP
5. You're in as admin — manage users via `/admin/users`

## Coexistence: local + OIDC together

A typical mature setup uses **OIDC for humans, local for service accounts**:

- Humans log in via SSO → off-boarding is a single click in the IdP
- A handful of `gluon user add` local accounts exist for CI / scripts that can't do an OAuth dance

The login page shows both options whenever both are configured. Each user is permanently bound to one provider via their `auth_provider` column — no provider-switching after creation.

## OIDC-only mode (no local fallback)

For maximum SSO purity, disable the password endpoint entirely:

```bash
GLUON_LOCAL_AUTH_ENABLED=false
```

The login page hides the password form. The CLI still works (it doesn't use the password endpoint), so you can still seed the first OIDC admin.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Login button doesn't appear | `GLUON_AUTH_ENABLED=false` or one of the four required `GLUON_OIDC_*` vars unset. Check `GET /api/auth/providers`. |
| 503 from `/api/auth/oidc/login` | Same as above. |
| Redirect loop | `GLUON_OIDC_REDIRECT_URI` doesn't match what's registered with the IdP. Must be **byte-for-byte** identical. |
| `?oidc_error=not_authorized` after login | Strict mode + user wasn't pre-registered, or auto-provision + email outside allowlist. |
| `?oidc_error=token_exchange` | IdP rejected the code exchange — usually wrong client secret or clock skew. Check Gluon logs. |
| `?oidc_error=missing_sub` | IdP returned an ID token without a `sub` claim. Either misconfigured (rare) or the user denied the `openid` scope. |

## Security notes

- ID tokens are validated against the provider's JWKS — no manual key handling.
- The OAuth state cookie (`secret_key=GLUON_OIDC_SESSION_SECRET`) carries the CSRF state and nonce for ~10 minutes; it's cleared after the callback.
- Gluon session cookies (`gluon_session=…`) are still server-side DB rows with rolling TTL — not JWTs. Logout invalidates server-side, not just client-side.
- The `next` post-login redirect is constrained to relative paths (`/`-prefixed); open-redirect attacks via `?next=https://evil.com` are refused.
- Sessions and link codes are swept hourly by the existing periodic task added in #89.
