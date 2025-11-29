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

- `gluon project add <name> <path>` - Register project
- `gluon project list` - List projects
- `gluon project remove <name>` - Remove project
- `gluon run <project> <prompt>` - Execute task
- `gluon resume <project> [prompt]` - Resume session
- `gluon sessions [project]` - List sessions
- `gluon status` - Show status
- `gluon bot` - Run Telegram bot interface

## Telegram Bot

Run Gluon as an always-on daemon via Telegram:

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
- `/status` - Show overall status
- `/cancel` - Cancel current task
- `/help` - Show help

