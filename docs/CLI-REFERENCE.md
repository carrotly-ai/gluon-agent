# CLI Reference

Complete reference for the `gluon` command-line interface.

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
gluon resume <project> [prompt]           # Resume last session
gluon sessions [project]                  # List sessions
gluon status                              # Show overall status
```

> **Note**: All tasks (foreground and background) are tracked and visible in `gluon runs` and the web dashboard. Logs are written to `~/.gluon/logs/{run_id}/` for all executions.

### Model Tiers

| Tier | Model | Use Case |
|------|-------|----------|
| `haiku` | claude-haiku-4.5 | Fast, economical tasks |
| `sonnet` | claude-sonnet-4.5 | Balanced performance (default) |
| `opus` | claude-opus-4.5 | Complex, demanding tasks |

### Examples

```bash
# Run a task in foreground (streaming output)
gluon run myapp 'Fix the login bug'

# Run in background and return immediately
gluon run myapp 'Implement user authentication' --background

# Use a specific model
gluon run myapp 'Complex refactor' --model opus

# Resume the last session with a follow-up
gluon resume myapp 'Also add tests'

# Resume with empty prompt (continues previous context)
gluon resume myapp
```

## Run Management

All tasks (foreground and background) are tracked as ExecutionRuns and visible via these commands:

```bash
gluon runs                     # List all runs (foreground + background)
gluon runs --active            # Show only active runs
gluon runs --project <name>    # Filter by project
gluon logs <run_id>            # View logs for a run
gluon logs <run_id> --follow   # Tail logs in real-time
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

# View logs
gluon logs abc12345

# Follow logs in real-time
gluon logs abc12345 --follow

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

## Bot Interfaces

```bash
gluon bot                              # Run Telegram bot
gluon bot --token <token>              # Pass token directly
gluon discord                          # Run Discord bot
gluon discord --token <token> --guild <id>
gluon serve --telegram --discord       # Run multiple transports
gluon serve --telegram --discord --web # Run all interfaces
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
gluon web                      # Start web server on default port
gluon web --port <port>        # Specify port (default: 45866)
```

### Examples

```bash
# Start web dashboard
gluon web

# Use custom port
gluon web --port 8080
```

## Global Options

```bash
gluon --help       # Show help
gluon --version    # Show version
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 130 | Interrupted (Ctrl+C) |
