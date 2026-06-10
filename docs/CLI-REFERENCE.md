# CLI Reference

Reference for the `gluon` command-line interface. This document covers the main
command groups but may lag the code — run `gluon --help` (or `gluon <command>
--help`) for the authoritative, always-current list of commands and options.

## Project Management

```bash
gluon project add <name> <path>    # Register a project
gluon project list                  # List all projects
gluon project remove <name>         # Remove a project
```

### Path Format

Project paths support environment variable expansion for portability across different environments (host, Docker, CI/CD):

```bash
# Environment variables with ${} syntax
gluon project add myapp "${HOME}/projects/myapp"
gluon project add myapp "${WORKSPACE_ROOT}/myapp"

# Home directory shorthand
gluon project add myapp "~/projects/myapp"

# Absolute paths (work on current environment)
gluon project add myapp /Users/mcutler/projects/myapp
```

**Why use environment variables?**
- **Portability**: Same command works on host and Docker
- **Example**: `${HOME}` → `/Users/mcutler` on Mac, `/home/gluon` in Docker
- **CI/CD**: Works with environment-specific variables
- **Flexibility**: No need to hardcode absolute paths

### Examples

```bash
# Register using environment variables (works everywhere)
gluon project add myapp "${HOME}/projects/myapp"

# Register using home shorthand
gluon project add myapp "~/projects/myapp"

# Register using absolute path (current environment only)
gluon project add myapp /Users/mcutler/projects/myapp

# List all registered projects
gluon project list

# Remove a project (does not delete files)
gluon project remove myapp
```

## Workspace Management

Workspaces are directories containing multiple projects. Gluon auto-discovers projects by scanning for markers like `package.json`, `pyproject.toml`, `.git`, etc.

```bash
gluon workspace add <name> <path>   # Register workspace, auto-discover projects
gluon workspace list                # List workspaces
gluon workspace scan [name]         # Re-scan workspace(s) for new projects
gluon workspace projects <name>     # List projects in workspace
gluon workspace remove <name>       # Remove workspace
```

### Path Format

Like projects, workspace paths support environment variable expansion:

```bash
gluon workspace add myworkspace "${HOME}/workspaces"
gluon workspace add myworkspace "~/workspaces"
gluon workspace add myworkspace /Users/mcutler/workspaces
```

### Examples

```bash
# Register workspace with environment variables (portable)
gluon workspace add work "${HOME}/workspaces/company"

# Use home shorthand
gluon workspace add work ~/workspaces/company

# Re-scan to find newly created projects
gluon workspace scan work

# List projects discovered in workspace
gluon workspace projects work
```

## Task Execution

```bash
gluon run <project> <prompt>              # Execute task (foreground)
gluon run <project> <prompt> --background # Execute task in background
gluon run <project> <prompt> --model <tier>  # Specify model tier
gluon run <project> <prompt> --worktree   # Run in isolated git worktree
gluon run <project> <prompt> --ralph      # Enable Ralph Loop (autonomous mode)
gluon run <project> <prompt> --profile <name>  # Use a task profile
gluon resume <project> [prompt]           # Resume last session
gluon recover <run_id>                    # Recover from context overflow
gluon sessions [project]                  # List sessions
gluon status                              # Show overall status
```

> **Note**: All tasks (foreground and background) are tracked and visible in `gluon runs` and the web dashboard. Logs are written to `~/.gluon/logs/{run_id}/` for all executions.

### Run Flags

| Flag | Short | Description |
|------|-------|-------------|
| `--background` | `-b` | Run in background, return immediately |
| `--model <tier>` | `-m` | Model tier: `opus-4.8`/`opus-4.7`/`opus-4.6`/`sonnet`/`haiku` |
| `--worktree` | `-w` | Execute in an isolated git worktree branch |
| `--ralph` | `-r` | Enable [Ralph Loop](RALPH-LOOP.md) (autonomous mode) |
| `--max-loops <n>` | | Max loop iterations in Ralph mode (default: 50) |
| `--max-calls <n>` | | Max API calls per hour in Ralph mode (default: 100) |
| `--max-cost <usd>` | | Cost cap in USD for Ralph mode |
| `--profile <name>` | `-P` | Task profile: `quick`/`standard`/`deep`/`planning` |
| `--thinking <level>` | | Thinking budget: `none`/`low`/`medium`/`high`/`ultrathink` |
| `--planning` | | Force planning mode (wait for user approval before execution) |
| `--new` | `-n` | Force a new session instead of resuming |
| `--quiet` | `-q` | Only show final result |

### Model Tiers

| Tier | Model | Use Case |
|------|-------|----------|
| `haiku` | claude-haiku-4.5 | Fast, economical tasks |
| `sonnet` | claude-sonnet-4.6 | Balanced performance |
| `opus-4.6` | claude-opus-4.6 | Previous generation Opus |
| `opus-4.7` | claude-opus-4.7 | Previous generation Opus |
| `opus-4.8` | claude-opus-4.8 | Complex, demanding tasks (latest, default) |
| `opus` | claude-opus-4.8 | Alias for opus-4.8 |

### Task Profiles

Pre-configured combinations of model, thinking budget, and planning mode:

| Profile | Model | Thinking | Planning | Best For |
|---------|-------|----------|----------|----------|
| `quick` | Haiku | Adaptive (effort: low) | No | Typos, simple fixes |
| `standard` | Sonnet | Adaptive (effort: medium) | No | Most coding tasks (default) |
| `deep` | Opus 4.8 | Adaptive (effort: high) | No | Complex refactoring |
| `planning` | Opus 4.8 | Adaptive (effort: high) | Yes | Multi-step tasks requiring approval |

> Profiles use an **adaptive** thinking budget (`max_thinking_tokens` unset — the CLI
> paces itself by effort level), not a fixed token count. Override per-run with
> `--thinking <none|low|medium|high|ultrathink>`.

### Examples

```bash
# Run a task in foreground (streaming output)
gluon run myapp 'Fix the login bug'

# Run in background and return immediately
gluon run myapp 'Implement user authentication' --background

# Use a specific model
gluon run myapp 'Complex refactor' --model opus

# Run in isolated git worktree
gluon run myapp 'Add dark mode' --background --worktree

# Autonomous execution with Ralph Loop
gluon run myapp 'Implement OAuth' --ralph --max-loops 50 --max-cost 10

# Use a task profile
gluon run myapp 'Fix typo' --profile quick
gluon run myapp 'Redesign database schema' --profile deep

# Force planning mode
gluon run myapp 'Refactor auth system' --planning

# Resume the last session with a follow-up
gluon resume myapp 'Also add tests'

# Resume with empty prompt (continues previous context)
gluon resume myapp

# Resume with specific model
gluon resume myapp --model opus

# Recover a run that failed due to context overflow
gluon recover abc12345

# Recover with a fresh session (creates new run linked to failed one)
gluon recover abc12345 --fresh

# Recover without waiting for completion
gluon recover abc12345 --no-wait
```

## Run Management

All tasks (foreground and background) are tracked as ExecutionRuns and visible via these commands:

```bash
gluon runs                     # List all runs (foreground + background)
gluon runs --active            # Show only active runs
gluon runs --project <name>    # Filter by project
gluon runs --limit 20          # Limit number of runs (default: 20)
gluon logs <run_id>            # View logs for a run
gluon logs <run_id> --follow   # Tail logs in real-time
gluon logs <run_id> --tail 50  # Show last 50 lines
gluon logs <run_id> --stream stderr  # View stderr
gluon logs <run_id> --stream messages  # View JSONL messages
gluon cancel <run_id>          # Cancel a running task
```

### Run Status

| Status | Description |
|--------|-------------|
| `PENDING` | Task queued, not yet started |
| `RUNNING` | Task currently executing |
| `COMPLETED` | Task finished successfully |
| `FAILED` | Task encountered an error |
| `CANCELLED` | Task was cancelled by user |

### Examples

```bash
# Start a background task
gluon run myapp 'Implement feature X' --background
# Output: Task submitted: abc12345

# Check status of all runs
gluon runs

# Show only active runs
gluon runs --active
gluon runs -a

# Filter by project
gluon runs --project myapp
gluon runs -p myapp

# View logs
gluon logs abc12345

# Follow logs in real-time
gluon logs abc12345 --follow
gluon logs abc12345 -f

# View last 50 lines
gluon logs abc12345 --tail 50
gluon logs abc12345 -n 50

# View stderr logs
gluon logs abc12345 --stream stderr
gluon logs abc12345 -s stderr

# View structured messages
gluon logs abc12345 --stream messages
gluon logs abc12345 -s messages

# Cancel if needed
gluon cancel abc12345
```

## Git Management

```bash
gluon git status [project]    # Show git status for project(s)
gluon git fetch [project]     # Fetch latest from remote
gluon git sync <project>      # Commit uncommitted changes, fetch, fast-forward
gluon git push <project>      # Commit and push changes
```

### Examples

```bash
# Check git status for all projects
gluon git status

# Check specific project
gluon git status myapp

# Sync before starting work
gluon git sync myapp

# Push changes after completion
gluon git push myapp
```

## User Management

Multi-user authentication is opt-in via `GLUON_AUTH_ENABLED=true`. The `gluon user *` commands work **regardless of that flag** so you can seed users before flipping it. See [AUTH.md](AUTH.md) for the full auth model.

```bash
# Create a local password user
gluon user add alice                                    # prompts for password
gluon user add alice --role admin --email alice@org.example
gluon user add alice --display-name "Alice Cooper"

# Create an OIDC pre-registered user (D5 Phase 3)
# Use --email when you don't yet know the IdP's `sub` claim — Gluon swaps
# the email-as-placeholder for the real sub on first OIDC login.
gluon user add alice --auth-provider oidc --email alice@org.example --role admin

# Use --auth-subject when you DO know the sub (e.g. from an IdP directory export)
gluon user add alice --auth-provider oidc --auth-subject 'auth0|abc123' --email alice@org.example

# Inspect users
gluon user list                       # active users only
gluon user list --include-disabled    # include soft-deleted accounts
gluon user show alice                 # detailed view of one user

# Modify users
gluon user set-role alice operator    # change role: admin / operator / viewer
gluon user set-password alice         # reset password (admins can change anyone's)
gluon user disable alice              # soft-delete + invalidate sessions
gluon user enable alice               # restore a disabled user
```

### Roles

| Role | Permissions |
|---|---|
| `admin` | Manage users, edit roles, reset passwords, + everything below |
| `operator` | Create runs, decide approvals, use the dashboard, + everything below |
| `viewer` | Read-only on runs, projects, attribution |

### Examples

```bash
# Bootstrap the first admin (still in single-user mode is fine — flag flip later)
gluon user add me --role admin

# Pre-register your team for OIDC SSO before turning on the flag
for user in alice bob carol; do
  gluon user add "$user" --auth-provider oidc --email "${user}@org.example" --role operator
done

# Promote a user
gluon user set-role bob admin

# Off-board: disable preserves attribution links (don't hard-delete users
# who have created runs, decided approvals, etc.)
gluon user disable carol
```

## Bot Interfaces

```bash
gluon bot                              # Run Telegram bot
gluon bot --token <token>              # Pass token directly
gluon bot --users <id1>,<id2>          # Restrict to specific user IDs
gluon discord                          # Run Discord bot
gluon discord --token <token>          # Pass token directly
gluon discord --guild <id>             # Discord guild (server) ID
gluon discord --users <id1>,<id2>      # Restrict to specific user IDs
gluon serve --telegram --discord       # Run multiple transports
gluon serve --telegram --discord --web # Run all interfaces
gluon serve --web-port 8080            # Custom web port (default: 45866)
```

### Examples

```bash
# Start Telegram bot (token from environment)
export GLUON_TELEGRAM_TOKEN="your-token"
gluon bot

# Start Discord bot
export GLUON_DISCORD_TOKEN="your-token"
export GLUON_DISCORD_GUILD="your-guild-id"
gluon discord

# Run everything together
gluon serve --telegram --discord --web
```

## Web Dashboard

```bash
gluon web                          # Start web server on default port
gluon web --port <port>            # Specify port (default: 45866)
gluon web -p <port>                # Short form for port
gluon web --host <host>            # Bind address (default: 0.0.0.0)
gluon web -h <host>                # Short form for host
gluon web --reload                 # Enable auto-reload for development
gluon web -r                        # Short form for reload
gluon web --no-browser             # Don't open browser automatically
```

### Examples

```bash
# Start web dashboard
gluon web

# Use custom port
gluon web --port 8080

# Development mode with auto-reload
gluon web --reload
```

## MCP Server Management

```bash
gluon mcp status               # Show registered MCP servers and their status
```

### Examples

```bash
# Check MCP server status
gluon mcp status
# Output:
# MCP Servers:
#   perplexity: http (http://localhost:8080/sse) - connected
#   context7: sse (http://localhost:8081/sse) - connected
```

MCP servers are automatically registered from `~/.claude/.mcp.json` when running in Docker. See [DOCKER.md](DOCKER.md#mcp-server-auto-registration) for details.

## Ralph Loop ([docs](RALPH-LOOP.md))

Monitor and manage autonomous Ralph Loop runs.

```bash
gluon ralph status <run_id>                # Show ralph loop status for a run
gluon ralph iterations <run_id>            # Show iteration history
gluon ralph iterations <run_id> --limit 10 # Limit iterations shown
gluon ralph runs                           # List all ralph-enabled runs
gluon ralph runs --active                  # Show only active ralph runs
gluon ralph runs --project <name>          # Filter by project
```

### Ralph Loop Flags

| Command | Flags | Description |
|---------|-------|-------------|
| `ralph iterations` | `--limit, -l` | Max iterations to show (default: 20) |
| `ralph runs` | `--project, -p` | Filter by project name/ID |
| `ralph runs` | `--active, -a` | Show only active ralph runs |
| `ralph runs` | `--limit, -l` | Max runs to show (default: 20) |

### Examples

```bash
# Check ralph loop status (circuit state, loop count, cost)
gluon ralph status abc12345

# View iteration history (duration, tokens, cost per iteration)
gluon ralph iterations abc12345

# View more iterations
gluon ralph iterations abc12345 --limit 50
gluon ralph iterations abc12345 -l 50

# List all ralph runs
gluon ralph runs

# List only active ralph runs
gluon ralph runs --active
gluon ralph runs -a

# Filter ralph runs by project
gluon ralph runs --project myapp
gluon ralph runs -p myapp
```

## Supervisor Daemon

The supervisor daemon polls runs in REVIEW status and auto-resumes based on supervision policies.

```bash
gluon supervisor start                     # Start daemon (background)
gluon supervisor start --foreground        # Start in foreground
gluon supervisor start -f                  # Short form for foreground
gluon supervisor start --poll-interval 60  # Custom poll interval (seconds)
gluon supervisor start -i 60               # Short form for poll-interval
gluon supervisor stop                      # Stop daemon
gluon supervisor status                    # Check daemon status
gluon supervisor logs                      # View daemon logs (last 50 lines)
gluon supervisor logs --follow             # Tail daemon logs live
gluon supervisor logs -f                   # Short form for follow
gluon supervisor logs --lines 100          # Show last N lines
gluon supervisor logs -n 100               # Short form for lines
```

### Examples

```bash
# Start supervisor for auto-resume
gluon supervisor start

# Check if supervisor is running
gluon supervisor status

# View recent supervisor activity
gluon supervisor logs --lines 50

# Stop supervisor
gluon supervisor stop
```

## Supervision

Inspect and manage supervision decisions for individual runs.

```bash
gluon supervision status <run_id>          # Show supervision status
gluon supervision logs <run_id>            # Show decision audit trail
gluon supervision disable <run_id>         # Disable auto-resume for a run
gluon supervision evaluate <run_id>        # Manually evaluate a run for auto-resume
```

### Examples

```bash
# Check supervision config and status for a run
gluon supervision status abc12345

# View supervision decision history
gluon supervision logs abc12345

# View more decision history
gluon supervision logs abc12345 --limit 50
gluon supervision logs abc12345 -l 50

# Disable auto-resume with a reason
gluon supervision disable abc12345 --reason "Needs manual review"
gluon supervision disable abc12345 -r "Needs manual review"

# Disable with default reason
gluon supervision disable abc12345

# Manually trigger evaluation
gluon supervision evaluate abc12345
```

## Webhook Configuration

Configure webhooks for GitHub/GitLab event triggers.

```bash
gluon webhook list                         # List all webhooks
gluon webhook add                          # Add a webhook
gluon webhook remove <id>                  # Remove a webhook
gluon webhook enable <id>                  # Enable a webhook
gluon webhook disable <id>                 # Disable a webhook
```

### Webhook Flags

| Flag | Description |
|------|-------------|
| `--handler` | Handler type: `github`, `gitlab` |
| `--project` | Project name or ID |
| `--events` | Comma-separated events: `push`, `pull_request`, `merge_request` |
| `--branches` | Optional branch filter (default: all) |
| `--secret` | Optional webhook secret for validation |

### Examples

```bash
# Add a GitHub webhook for a project
gluon webhook add --handler github --project myapp --events push,pull_request

# Add with branch filter and secret
gluon webhook add --handler github --project myapp --events push --branches main --secret mysecret

# List configured webhooks
gluon webhook list

# Disable temporarily
gluon webhook disable abc12345

# Enable a disabled webhook
gluon webhook enable abc12345

# Remove a webhook
gluon webhook remove abc12345
```

## Maintenance

```bash
gluon cleanup                  # Clean up old log files
gluon cleanup --dry-run        # Preview what would be deleted
gluon cleanup -n               # Short form for dry-run
gluon stats                    # Show disk usage for ~/.gluon
gluon version                  # Show version
```

### Cleanup Retention Policies

| Category | Retention |
|----------|-----------|
| Orphan logs (no DB record) | Deleted immediately |
| Archived runs | 30 days after completion |
| Failed runs | 7 days after completion |
| Completed runs (non-archived) | 30 days after completion |

### Cleanup Flags

| Flag | Short | Description |
|------|-------|-------------|
| `--dry-run` | `-n` | Preview deletions without removing files |

### Examples

```bash
# Preview cleanup
gluon cleanup --dry-run

# Run cleanup
gluon cleanup

# Check disk usage
gluon stats
```

## Global Options

```bash
gluon --help       # Show help
gluon version      # Show version
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 130 | Interrupted (Ctrl+C) |
