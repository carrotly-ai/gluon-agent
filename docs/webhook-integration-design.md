# Webhook Integration Design: Error Routing to Agent Tasks

## Overview

This document outlines a design for capturing webhooks from external platforms (GitHub, Vercel, Sentry) and routing error/failure events back to the originating Gluon agent task. This enables automated error triage where, for example, if a task pushes code that breaks CI or a deployment, the error is automatically routed back to that task for resolution.

## Goals

1. **Capture webhooks** from GitHub (push, PR, CI checks), Vercel (deployment status), and Sentry (errors/issues)
2. **Correlate events** back to the specific Gluon `ExecutionRun` that caused them
3. **Route failures** to the original task agent for automated triage/remediation
4. **Maintain audit trail** of all external events tied to agent runs

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          External Platforms                              │
├─────────────┬─────────────┬─────────────┬──────────────────────────────┤
│   GitHub    │   Vercel    │   Sentry    │   Future: CircleCI, etc.     │
└──────┬──────┴──────┬──────┴──────┬──────┴──────────────┬───────────────┘
       │             │             │                      │
       ▼             ▼             ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Gluon Webhook Gateway                               │
│  POST /api/webhooks/github                                              │
│  POST /api/webhooks/vercel                                              │
│  POST /api/webhooks/sentry/{token}                                      │
├─────────────────────────────────────────────────────────────────────────┤
│  1. Verify signature (HMAC)                                             │
│  2. Parse payload                                                       │
│  3. Correlate to ExecutionRun                                           │
│  4. Store WebhookEvent                                                  │
│  5. Route to task if failure                                            │
└─────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Correlation Engine                               │
│  - Lookup by commit SHA                                                 │
│  - Lookup by PR number                                                  │
│  - Lookup by branch name (gluon-task/{run_id})                         │
│  - Parse commit message trailers                                        │
└─────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Task Router / Auto-Remediation                        │
│  - Resume failed task with error context                                │
│  - Create follow-up task for triage                                     │
│  - Notify via Telegram/Discord                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Correlation Strategy

### Run ID Propagation

Every Gluon `ExecutionRun` has a unique `id` (UUID). We propagate this through:

1. **Branch names**: `gluon-task/{run_id_short}` (e.g., `gluon-task/abc12345`)
2. **Commit trailers**: Append to commit messages
3. **PR metadata**: Include in PR body/description
4. **Database mappings**: Store explicit commit SHA → run_id mappings

### Commit Message Format

When Gluon creates commits, include a trailer:

```
feat: implement user authentication

- Added JWT middleware
- Created login/logout endpoints

Gluon-Run-ID: abc12345-def6-7890-ghij-klmnopqrstuv
```

### Database Schema Additions

```sql
-- Webhook events table
CREATE TABLE webhook_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,           -- 'github', 'vercel', 'sentry'
    event_type TEXT NOT NULL,       -- 'check_run', 'deployment', 'issue'
    event_id TEXT,                  -- External event ID
    run_id TEXT REFERENCES execution_runs(id),  -- Correlated run (nullable)
    project_id TEXT REFERENCES projects(id),
    status TEXT,                    -- 'success', 'failure', 'error', 'pending'
    payload JSONB NOT NULL,         -- Raw webhook payload
    created_at TEXT NOT NULL,
    processed_at TEXT,
    correlation_method TEXT         -- How we found the run: 'branch', 'commit_sha', 'trailer'
);

CREATE INDEX idx_webhook_events_run ON webhook_events(run_id);
CREATE INDEX idx_webhook_events_source ON webhook_events(source, event_type);

-- Commit tracking table (for correlation)
CREATE TABLE git_commits (
    sha TEXT PRIMARY KEY,
    run_id TEXT REFERENCES execution_runs(id),
    project_id TEXT REFERENCES projects(id),
    branch TEXT,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_git_commits_run ON git_commits(run_id);
```

## Webhook Endpoints

### GitHub Webhooks

**Endpoint**: `POST /api/webhooks/github`

**Authentication**: HMAC-SHA256 signature in `X-Hub-Signature-256` header

**Supported Events**:
- `push` - Track commits pushed by agent
- `pull_request` - Track PR creation/updates
- `check_run` - CI check results
- `check_suite` - CI suite completion
- `status` - Commit status updates
- `deployment_status` - GitHub deployment results

**Correlation Flow**:
```python
async def handle_github_webhook(event: str, payload: dict) -> str | None:
    """Return run_id if correlated, None otherwise."""

    # Try branch name first (most reliable for Gluon tasks)
    ref = payload.get("ref", "")
    if "gluon-task/" in ref:
        run_id_short = ref.split("gluon-task/")[-1].split("/")[0]
        run = store.get_run_by_short_id(run_id_short)
        if run:
            return run.id

    # Try commit SHA lookup
    commit_sha = (
        payload.get("after") or  # push event
        payload.get("head_commit", {}).get("id") or
        payload.get("check_run", {}).get("head_sha") or
        payload.get("sha")  # status event
    )
    if commit_sha:
        commit = store.get_commit_by_sha(commit_sha)
        if commit:
            return commit.run_id

    # Try PR number lookup
    pr_number = (
        payload.get("pull_request", {}).get("number") or
        payload.get("number")
    )
    if pr_number:
        run = store.get_run_by_pr_number(pr_number)
        if run:
            return run.id

    # Fallback: parse commit message trailers
    message = payload.get("head_commit", {}).get("message", "")
    if "Gluon-Run-ID:" in message:
        run_id = message.split("Gluon-Run-ID:")[-1].strip().split()[0]
        return run_id

    return None
```

### Vercel Webhooks

**Endpoint**: `POST /api/webhooks/vercel`

**Authentication**: HMAC signature in `X-Vercel-Signature` header

**Supported Events**:
- `deployment.created`
- `deployment.ready`
- `deployment.error`
- `deployment.canceled`

**Payload Fields for Correlation**:
```json
{
  "type": "deployment.error",
  "payload": {
    "deployment": {
      "id": "dpl_...",
      "url": "https://...",
      "meta": {
        "githubCommitSha": "abc123...",
        "githubCommitRef": "gluon-task/abc12345",
        "githubPrId": "123"
      }
    }
  }
}
```

### Sentry Webhooks

**Endpoint**: `POST /api/webhooks/sentry/{token}`

**Authentication**: URL token (Sentry legacy webhooks don't support HMAC)

**Supported Events**:
- Issue created
- Issue resolved
- Error event

**Correlation Challenges**:
Sentry errors don't directly include git metadata. Options:
1. Match by timestamp proximity to recent runs
2. Parse stack traces for file paths that match recent changes
3. Use Sentry's release/commit tracking integration
4. Match by project name

## Implementation Plan

### Phase 1: Webhook Infrastructure

1. Add webhook endpoints to FastAPI app
2. Implement signature verification for GitHub and Vercel
3. Create `webhook_events` and `git_commits` tables
4. Store all incoming webhooks (even uncorrelated)

### Phase 2: Correlation Engine

1. Modify `TaskRunner` to track commits created during runs
2. Implement branch name correlation (highest priority)
3. Implement commit SHA lookup
4. Implement commit message trailer parsing

### Phase 3: Error Routing

1. Detect failure events (CI failed, deployment failed)
2. If correlated to a run, create options:
   - Resume the run with error context injected
   - Create a new "fix" run with the error details
   - Send notification to user with error summary
3. Add UI to show webhook events on run detail page

### Phase 4: Advanced Features

1. Sentry integration with stack trace analysis
2. Auto-remediation: automatically resume failed tasks
3. Error pattern detection across runs
4. Webhook replay/debugging tools

## API Design

### Webhook Event Response

```python
class WebhookEventResponse(BaseModel):
    id: str
    source: str  # 'github', 'vercel', 'sentry'
    event_type: str
    status: str | None
    run_id: str | None
    project_name: str | None
    created_at: datetime
    summary: str  # Human-readable summary
```

### List Webhook Events for Run

```
GET /api/runs/{run_id}/webhooks
```

Returns all webhook events correlated to a specific run.

### Manual Correlation

```
POST /api/webhook-events/{event_id}/correlate
{
  "run_id": "abc123..."
}
```

Manually link an uncorrelated webhook event to a run.

## Configuration

### Environment Variables

```bash
# GitHub
GLUON_GITHUB_WEBHOOK_SECRET=<hmac-secret>

# Vercel
GLUON_VERCEL_WEBHOOK_SECRET=<hmac-secret>

# Sentry (URL token since no HMAC support)
GLUON_SENTRY_WEBHOOK_TOKEN=<random-unguessable-string>
```

### GitHub Webhook Setup

1. Go to repo Settings → Webhooks → Add webhook
2. Payload URL: `https://your-gluon-instance/api/webhooks/github`
3. Content type: `application/json`
4. Secret: Use `GLUON_GITHUB_WEBHOOK_SECRET`
5. Events: Select "Let me select individual events"
   - Push
   - Pull requests
   - Check runs
   - Check suites
   - Statuses
   - Deployments

### Vercel Webhook Setup

1. Go to Project Settings → Git → Deploy Hooks (for incoming)
2. For outgoing webhooks, use Vercel Integration or API

## Security Considerations

1. **Always verify signatures** - Reject requests with invalid HMAC
2. **Use HTTPS only** - Never accept webhooks over HTTP
3. **Rate limiting** - Protect against webhook floods
4. **Payload size limits** - Reject oversized payloads
5. **IP allowlisting** (optional) - Restrict to known provider IPs
6. **Audit logging** - Log all webhook attempts (success and failure)

## Example: Full CI Failure Flow

1. **User creates task**: "Add user registration feature"
2. **Gluon executes**:
   - Creates branch `gluon-task/abc12345`
   - Makes commits with `Gluon-Run-ID: abc12345...` trailer
   - Creates PR #42
   - Stores: `git_commits(sha=xyz789, run_id=abc12345)`
3. **GitHub CI runs** and fails
4. **GitHub sends webhook**:
   ```json
   {
     "action": "completed",
     "check_run": {
       "conclusion": "failure",
       "head_sha": "xyz789",
       "output": {
         "title": "Tests failed",
         "summary": "3 tests failed in auth.test.ts"
       }
     }
   }
   ```
5. **Gluon webhook handler**:
   - Verifies signature ✓
   - Extracts `head_sha: xyz789`
   - Looks up in `git_commits` → finds `run_id: abc12345`
   - Creates `webhook_event` record
   - Detects `conclusion: failure`
6. **Error router**:
   - Fetches full error details from GitHub API
   - Creates follow-up prompt: "The CI failed with: 3 tests failed in auth.test.ts. Please fix the failing tests."
   - Either resumes original run or creates new linked run

## Future Extensions

- **Vercel preview comment**: Auto-comment on PR with preview URL
- **Sentry auto-fix**: Parse stack traces, attempt automatic fixes
- **Multi-repo support**: Track webhooks across multiple repositories
- **Webhook dashboard**: UI for viewing/managing all webhook events
- **Alert rules**: Configure which events trigger auto-remediation
