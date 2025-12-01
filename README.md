# Gluon Agent

AI orchestrator for managing multiple Claude Code agents across projects.

## Installation

```bash
uv venv
uv pip install -e '.[dev]'
```

## Quick Start

```bash
# Register a project
gluon project add myapp /path/to/myapp

# Run a task
gluon run myapp 'Fix the authentication bug'

# Resume session
gluon resume myapp 'Also add logging'
```

## Commands

### Project Management

- `gluon project add <name> <path>` - Register project
- `gluon project list` - List projects
- `gluon project remove <name>` - Remove project

### Workspace Management

- `gluon workspace add <name> <path>` - Register workspace and auto-discover projects
- `gluon workspace list` - List workspaces
- `gluon workspace scan [name]` - Scan workspace(s) for new projects
- `gluon workspace projects <name>` - List projects in workspace
- `gluon workspace remove <name>` - Remove workspace

### Task Execution

- `gluon run <project> <prompt>` - Execute task (foreground)
- `gluon run <project> <prompt> --background` - Execute task in background
- `gluon resume <project> [prompt]` - Resume last session
- `gluon sessions [project]` - List sessions
- `gluon status` - Show status

### Background Runs

- `gluon runs` - List all background execution runs
- `gluon runs --active` - Show only active runs
- `gluon logs <run_id>` - View logs for a run
- `gluon logs <run_id> --follow` - Tail logs in real-time
- `gluon cancel <run_id>` - Cancel a running task

### Telegram Bot

- `gluon bot` - Run Telegram bot interface

## Background Execution

Gluon supports running tasks in the background with persistent tracking:

```bash
# Start a background task
gluon run myapp 'Implement user authentication' --background
# Output: Task submitted: abc12345

# Check status
gluon runs
# Shows all runs with status (pending/running/completed/failed)

# View logs
gluon logs abc12345

# Follow logs in real-time
gluon logs abc12345 --follow

# Cancel if needed
gluon cancel abc12345
```

Background runs are tracked in SQLite and persist across restarts. Logs are stored at `~/.gluon/logs/<run_id>/`.

## Telegram Bot

Run Gluon as an always-on daemon via Telegram with support for multiple concurrent tasks.

### Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow instructions to create your bot
3. Copy the token you receive

4. (Optional) Get your user ID from [@userinfobot](https://t.me/userinfobot)

### Run the Bot

```bash
# Set token via environment variable
export GLUON_TELEGRAM_TOKEN="your-bot-token"

# Optional: restrict to specific users (comma-separated IDs)
export GLUON_TELEGRAM_USERS="123456789,987654321"

# Start the bot
gluon bot

# Or pass token directly
gluon bot --token "your-bot-token" --users "123456789"
```

### Bot Commands

- `/projects` - List registered projects
- `/sessions [project]` - List sessions
- `/run <project> <prompt>` - Run a task
- `/resume <project> [prompt]` - Resume last session
- `/runs` - List your background runs
- `/runs all` - List all runs
- `/status` - Show overall status
- `/cancel` - Cancel your latest active run
- `/cancel <run_id>` - Cancel specific run
- `/help` - Show help

### Features

- **Multiple concurrent tasks**: Run tasks across multiple projects simultaneously
- **Persistent tracking**: All runs are tracked in SQLite and survive bot restarts
- **Global concurrency limit**: Configurable limit (default: 5) prevents resource exhaustion
- **Natural language support**: Chat naturally instead of using commands
- **Real-time updates**: Get progress updates as tasks execute

## Configuration

### Environment Variables

- `GLUON_TELEGRAM_TOKEN` - Telegram bot token
- `GLUON_TELEGRAM_USERS` - Comma-separated list of allowed Telegram user IDs
- AWS credentials for Bedrock models (see `.env.local.example`)

### Model Selection

Use the `--model` flag to select a model tier:

```bash
gluon run myapp 'Fix bug' --model haiku    # Fast, economical
gluon run myapp 'Complex refactor' --model sonnet  # Balanced (default)
gluon run myapp 'Architecture review' --model opus  # Most capable
```

## Development

```bash
# Run tests
pytest

# Run linter
ruff check src/ tests/

# Format code
ruff format src/ tests/
```

## Architecture

```
~/.gluon/
├── gluon.db          # SQLite database (projects, sessions, runs)
└── logs/
    └── <run_id>/     # Per-run log directories
        ├── stdout.log
        ├── stderr.log
        └── messages.jsonl
```
