# Telegram Bot

Run Gluon as an always-on daemon via Telegram with support for multiple concurrent tasks, natural language interaction, and MCP tools for project management.

## Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow instructions to create your bot
3. Copy the token you receive
4. (Optional) Get your user ID from [@userinfobot](https://t.me/userinfobot)

## Run the Bot

```bash
# Set token via environment variable
export GLUON_TELEGRAM_TOKEN="your-bot-token"

# Optional: restrict to specific users (comma-separated IDs)
export GLUON_TELEGRAM_USERS="123456789,987654321"

# Start the bot
gluon bot

# Or pass token directly
gluon bot --token "your-bot-token" --users "123456789"

# Run multiple transports concurrently
export GLUON_DISCORD_TOKEN="your-discord-token"
export GLUON_DISCORD_GUILD="your-guild-id"
gluon serve --telegram --discord --web
```

## Commands

| Command | Description |
|---------|-------------|
| `/projects [filter]` | List registered projects (optionally filter by name) |
| `/sessions [project]` | List sessions for a project |
| `/run <project> <prompt>` | Run a new coding task |
| `/resume <project> [session_id] [prompt]` | Resume last session or specific session ID |
| `/runs` | List your background runs |
| `/runs all` | List all runs (all users) |
| `/status` | Show overall Gluon status |
| `/cancel [run_id]` | Cancel your latest active run or specific run |
| `/clear` | Clear chat history |
| `/help` | Show help and command reference |

## Natural Language Interface

Instead of commands, chat naturally with the bot. It uses Claude's intelligence and MCP tools to understand your intent:

| What you say | What happens |
|--------------|--------------|
| "Show me my projects" | Lists all registered projects |
| "What's the git status of myapp?" | Shows git branch, uncommitted changes, ahead/behind |
| "Run a task on myapp to fix the login bug" | Starts a coding task with Claude |
| "What tasks are running?" | Lists active background runs with status |
| "Cancel the last task" | Cancels most recent active run |
| "Search the web for React best practices" | Performs web search and summarizes |
| "Read the README in myapp" | Reads file contents from project |
| "Find all Python files in myapp" | Searches for files by pattern |
| "Resume the last session on myapp" | Automatically resumes previous session |
| *(reply to completion)* "Also add tests" | Resumes the session with follow-up prompt |

## Features

- **Multiple concurrent tasks** - Run up to 16 tasks concurrently across multiple projects (configurable)
- **Persistent tracking** - All runs tracked in SQLite, survive bot restarts
- **Chat history** - Maintains per-user conversation history for context awareness
- **Natural language support** - Chat naturally with Claude-powered understanding
- **Reply to resume** - Reply to a completion message to automatically resume that session
- **Real-time updates** - Get progress updates every 2 seconds as tasks execute
- **Tool visualization** - See which tools the agent is using (Read, Bash, Grep, etc.)
- **Session persistence** - Sessions saved across restarts and can be resumed later

## Model Selection

When running tasks via natural language, you can specify the model:

| Model | Best For | Syntax |
|-------|----------|--------|
| `opus-4.6` (default) | Complex reasoning, architecture decisions, large refactors | "Use opus to fix the auth system" |
| `sonnet` | Balanced performance/cost, most tasks | "Run a task on myapp with sonnet" |
| `haiku` | Simple tasks, bug fixes, documentation | "Fix this typo in haiku" |

Example commands:
- "Run a task on myapp with opus: Add comprehensive error handling"
- "Resume the session with sonnet"
- "Use haiku to update the README"

Default is `sonnet` for balanced performance and cost.

## Worktree Isolation

For experimental changes or parallel work, use worktree isolation:

- "Run a task on myapp in a worktree to try a new approach"
- This creates an isolated Git worktree for the task
- Changes don't affect the main branch
- Enables parallel tasks on the same project
- Can create a pull request from the completed run

## Chat Agent Tools

The natural language interface has access to MCP tools:

**Gluon Project & Task Management:**
- `list_projects`, `list_sessions` - View projects and sessions
- `run_task`, `resume_session` - Execute and resume coding tasks
- `list_runs`, `get_run`, `get_logs` - Monitor runs and view logs
- `cancel_run` - Cancel active runs
- `create_pr` - Create PR from worktree run
- `get_git_status`, `git_sync`, `git_push` - Git operations

**Code Exploration:**
- `Read`, `Glob`, `Grep` - Read files and search code
- `Bash`, `BashOutput` - Run shell commands

**Web Research:**
- `WebSearch`, `WebFetch` - Search web and fetch URLs

## Execution Flow

### Command-Based Task (`/run myapp "Fix bug"`)
1. User sends `/run myapp "Fix bug"`
2. Bot checks authorization and concurrency limits
3. Creates ExecutionRun record in database
4. Spawns background asyncio task
5. Agent executes with streaming updates every 2 seconds
6. Final summary with cost and turn count
7. Session saved for later resume

### Natural Language Task (Chat)
1. User sends message like "Fix the login bug in myapp"
2. Bot shows typing indicator
3. Message sent to ChatAgent with conversation history
4. ChatAgent uses MCP tools to understand intent (list_projects, etc.)
5. ChatAgent returns pending task or direct response
6. If task: Execute as background run with callbacks
7. If response: Send message to user

### Reply-to-Resume
1. User replies to a completion message
2. Bot extracts run ID from parent message
3. Creates new run linked to previous session
4. Resumes with new prompt
5. Conversation continues in thread

## Tool Call Visualization

As the agent executes, you see tool calls displayed:

```
🔧 `Read(/path/to/file.ts)`
🔧 `Bash(npm test...)`
🔧 `Grep(pattern)`
🔧 `Edit(/path/to/file.ts)`
```

This shows progress and what the agent is actively doing.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GLUON_TELEGRAM_TOKEN` | Telegram bot token (required) |
| `GLUON_TELEGRAM_USERS` | Comma-separated list of allowed user IDs (optional, allows all if not set) |

## Multi-Transport Mode

The `gluon serve` command runs multiple interfaces concurrently:

```bash
export GLUON_TELEGRAM_TOKEN="your-telegram-token"
export GLUON_DISCORD_TOKEN="your-discord-token"
export GLUON_DISCORD_GUILD="your-guild-id"

# Run Telegram bot, Discord bot, and web dashboard
gluon serve --telegram --discord --web
```

All three interfaces:
- Share the same database and session storage
- Show the same project/run information
- Contribute to the same concurrency limit
- Use the same git background sync

Unified visibility: Tasks started from CLI, Telegram, Discord, or Web all appear in all interfaces.
