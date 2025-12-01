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

### Git Management

- `gluon git status [project]` - Show git status for project(s)
- `gluon git fetch [project]` - Fetch latest from remote
- `gluon git sync <project>` - Commit, fetch, and fast-forward
- `gluon git push <project>` - Commit and push changes

### Telegram Bot

- `gluon bot` - Run Telegram bot interface

## Architecture

### High-Level Overview

```mermaid
graph TB
    subgraph Interfaces
        CLI[CLI - gluon]
        TG[Telegram Bot]
    end

    subgraph Core
        ORCH[Orchestrator]
        RUNNER[TaskRunner]
        STORE[GluonStore]
        GIT[GitManager]
    end

    subgraph Execution
        AGENT[GluonAgent]
        SDK[Claude Agent SDK]
        CLAUDE[Claude CLI]
    end

    subgraph Storage
        DB[(SQLite DB)]
        LOGS[Log Files]
    end

    subgraph External
        BEDROCK[AWS Bedrock]
        REMOTE[Git Remote]
    end

    CLI --> ORCH
    CLI --> RUNNER
    CLI --> GIT
    TG --> ORCH
    TG --> RUNNER

    ORCH --> STORE
    ORCH --> AGENT
    ORCH --> GIT
    RUNNER --> STORE
    RUNNER --> AGENT

    GIT --> STORE
    GIT -.->|fetch/push| REMOTE

    AGENT --> SDK
    SDK --> CLAUDE

    STORE --> DB
    RUNNER --> LOGS

    CLAUDE -.->|spawns| BEDROCK
```

### Data Model

```mermaid
erDiagram
    Workspace ||--o{ Project : contains
    Project ||--o{ Session : has
    Project ||--o{ ExecutionRun : has
    Session ||--o| ExecutionRun : "created by"

    Workspace {
        string id PK
        string name UK
        string path
        int scan_depth
        bool auto_discover
    }

    Project {
        string id PK
        string name UK
        string path
        string workspace_id FK
        bool git_is_repo
        string git_branch
        string git_remote
        int git_commits_ahead
        int git_commits_behind
    }

    Session {
        string id PK
        string project_id FK
        string claude_session_id
        string status
        float total_cost_usd
        int total_turns
    }

    ExecutionRun {
        string id PK
        string project_id FK
        string session_id FK
        int pid
        string status
        string prompt
        string initiator
        string log_path
    }
```

### CLI Execution Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant Orchestrator
    participant GluonAgent
    participant ClaudeSDK
    participant ClaudeCLI

    User->>CLI: gluon run myapp "Fix bug"
    CLI->>Orchestrator: execute(project, prompt)
    Orchestrator->>Orchestrator: Get/Create Session
    Orchestrator->>GluonAgent: execute(working_dir, prompt)
    GluonAgent->>ClaudeSDK: ClaudeSDKClient(options)
    ClaudeSDK->>ClaudeCLI: spawn subprocess
    GluonAgent->>ClaudeSDK: client.query(prompt)

    loop Streaming Response
        ClaudeCLI-->>ClaudeSDK: messages
        ClaudeSDK-->>GluonAgent: AssistantMessage/ResultMessage
        GluonAgent-->>Orchestrator: AgentMessage
        Orchestrator-->>CLI: AgentMessage
        CLI-->>User: Display output
    end

    ClaudeSDK-->>GluonAgent: ResultMessage (final)
    GluonAgent-->>Orchestrator: AgentResult
    Orchestrator->>Orchestrator: Update Session
    Orchestrator-->>CLI: AgentResult
    CLI-->>User: Display summary
```

### Background Execution Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant TaskRunner
    participant Store
    participant GluonAgent
    participant LogFiles

    User->>CLI: gluon run myapp "Task" --background
    CLI->>Store: create_run(project_id, prompt)
    Store-->>CLI: ExecutionRun (PENDING)
    CLI-->>User: "Task submitted: abc123"

    CLI->>TaskRunner: submit(project_id, prompt)
    TaskRunner->>TaskRunner: asyncio.create_task()

    Note over TaskRunner: Background execution starts

    TaskRunner->>Store: update_run(RUNNING)
    TaskRunner->>GluonAgent: execute(working_dir, prompt)

    loop Execution
        GluonAgent-->>TaskRunner: AgentMessage
        TaskRunner->>LogFiles: Write to stdout.log
        TaskRunner->>LogFiles: Write to messages.jsonl
    end

    GluonAgent-->>TaskRunner: AgentResult
    TaskRunner->>Store: update_run(COMPLETED/FAILED)

    Note over User: Later...
    User->>CLI: gluon logs abc123
    CLI->>TaskRunner: get_logs(run_id)
    TaskRunner->>LogFiles: Read logs
    LogFiles-->>CLI: Log content
    CLI-->>User: Display logs
```

### Telegram Bot Flow

```mermaid
sequenceDiagram
    participant User
    participant Telegram
    participant GluonBot
    participant Store
    participant Orchestrator
    participant GluonAgent

    User->>Telegram: /run myapp "Fix bug"
    Telegram->>GluonBot: Update (command)

    GluonBot->>GluonBot: Check authorization
    GluonBot->>Store: list_active_runs()
    Store-->>GluonBot: active_runs

    alt Concurrency limit reached
        GluonBot-->>Telegram: "Max concurrent runs reached"
        Telegram-->>User: Error message
    else Under limit
        GluonBot->>Store: create_run(project_id, prompt, initiator)
        Store-->>GluonBot: ExecutionRun
        GluonBot-->>Telegram: "Task started: abc123"
        Telegram-->>User: Confirmation

        GluonBot->>GluonBot: asyncio.create_task(_execute_task_with_runner)

        loop Execution with Semaphore
            GluonAgent-->>GluonBot: AgentMessage
            GluonBot-->>Telegram: Progress update
            Telegram-->>User: Status message
        end

        GluonAgent-->>GluonBot: AgentResult
        GluonBot->>Store: update_run(status)
        GluonBot-->>Telegram: "Complete" or "Failed"
        Telegram-->>User: Final result
    end
```

### Bot Startup Recovery

```mermaid
flowchart TD
    A[Bot Starts] --> B[_recover_stale_runs]
    B --> C{Any active runs<br/>from telegram:*?}
    C -->|Yes| D[Mark as FAILED<br/>'Bot restarted']
    C -->|No| E[Continue]
    D --> E
    E --> F[build_application]
    F --> G[Start Polling]
    G --> H[Ready for Commands]
```

### Agent Execution Detail

```mermaid
flowchart LR
    subgraph GluonAgent
        A[execute] --> B[Build Options]
        B --> C[ClaudeSDKClient]
    end

    subgraph ClaudeSDK
        C --> D[query prompt]
        D --> E[receive_response]
    end

    subgraph "Claude CLI Process"
        E --> F[claude CLI]
        F --> G[AWS Bedrock API]
    end

    G --> H{Model}
    H --> I[claude-opus-4.5]
    H --> J[claude-sonnet-4.5]
    H --> K[claude-haiku-4.5]

    style G fill:#ff9900
    style I fill:#6b5b95
    style J fill:#88b04b
    style K fill:#92a8d1
```

### Concurrency Model

```mermaid
flowchart TB
    subgraph "Telegram Bot Process"
        SEM[Semaphore<br/>max_concurrent=5]

        subgraph "Active Tasks"
            T1[Task 1<br/>run_id: abc]
            T2[Task 2<br/>run_id: def]
            T3[Task 3<br/>run_id: ghi]
        end

        SEM --> T1
        SEM --> T2
        SEM --> T3
    end

    subgraph "SQLite Store"
        DB[(gluon.db)]
        R1[ExecutionRun abc<br/>RUNNING]
        R2[ExecutionRun def<br/>RUNNING]
        R3[ExecutionRun ghi<br/>RUNNING]
    end

    T1 -.-> R1
    T2 -.-> R2
    T3 -.-> R3

    subgraph "Log Files"
        L1[~/.gluon/logs/abc/]
        L2[~/.gluon/logs/def/]
        L3[~/.gluon/logs/ghi/]
    end

    T1 --> L1
    T2 --> L2
    T3 --> L3
```

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

Gluon uses Claude models via AWS Bedrock:

| Tier | Model | Use Case |
|------|-------|----------|
| `haiku` | claude-haiku-4.5 | Fast, economical tasks |
| `sonnet` | claude-sonnet-4.5 | Balanced performance (default) |
| `opus` | claude-opus-4.5 | Complex, demanding tasks |

```bash
gluon run myapp 'Fix bug' --model haiku
gluon run myapp 'Complex refactor' --model sonnet
gluon run myapp 'Architecture review' --model opus
```

## Git Synchronization

Gluon automatically keeps projects synchronized with remote Git repositories to prevent conflicts when multiple Claude instances work on the same codebase.

### Pre-task Sync Flow

```mermaid
flowchart TD
    A[pre_task_sync] --> B{Is git repo?}
    B -->|No| Z[Return success - skip]
    B -->|Yes| C{Has uncommitted?}

    C -->|Yes| D[git add -A]
    D --> E[git commit -m 'gluon: auto-commit']
    C -->|No| F[git fetch origin]
    E --> F

    F --> G{Behind remote?}
    G -->|No| Z2[Return success]
    G -->|Yes| H{Diverged?}
    H -->|Yes| X[FAIL: Manual merge needed]
    H -->|No| I[git pull --ff-only]
    I --> Z2
```

### Post-task Sync Flow

```mermaid
flowchart TD
    A[post_task_sync] --> B{Is git repo?}
    B -->|No| Z[Return success - skip]
    B -->|Yes| C{Has changes?}

    C -->|No| Z
    C -->|Yes| D[git add -A]
    D --> E[git commit -m message]
    E --> F{Has remote?}

    F -->|No| Z2[Return success - local only]
    F -->|Yes| G[git push]

    G --> H{Push success?}
    H -->|Yes| Z3[Return success]
    H -->|No| I[git pull --rebase]
    I --> J[git push retry]
    J --> K{Success?}
    K -->|Yes| Z3
    K -->|No| X[FAIL: Push rejected]
```

### Background Sync

```mermaid
flowchart LR
    subgraph "Telegram Bot"
        LOOP[Background Loop]
        LOOP -->|every 5 min| FETCH
    end

    subgraph "For Each Project"
        FETCH[git fetch] --> STATUS[Update GitStatus]
        STATUS --> DB[(SQLite)]
    end

    STATUS --> WARN{Diverged?}
    WARN -->|Yes| LOG[Log Warning]
```

### Automatic Behavior

- **Pre-task**: Before running a task, Gluon will:
  1. Auto-commit any uncommitted changes
  2. Fetch from remote
  3. Fast-forward if behind (fails if diverged)

- **Post-task**: After successful task completion:
  1. Stage and commit all changes
  2. Push to remote

- **Background**: The Telegram bot periodically fetches all projects (every 5 minutes) to maintain status awareness

### Configuration

```bash
# Environment variables
GLUON_GIT_ENABLED=true              # Enable/disable git sync (default: true)
GLUON_GIT_SYNC_INTERVAL=300         # Background fetch interval in seconds
GLUON_GIT_AUTO_COMMIT=true          # Auto-commit before/after tasks
GLUON_GIT_AUTO_PUSH=true            # Auto-push after tasks
GLUON_GIT_COMMIT_PREFIX="gluon:"    # Prefix for auto-commit messages
```

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Not a git repo | Skip git operations, proceed normally |
| No remote configured | Commit locally only |
| Diverged branches | **FAIL** pre-task with error |
| Push rejected | Pull with rebase, retry once |
| Network error (fetch) | **WARN** but proceed |

## File Structure

```
~/.gluon/
├── gluon.db          # SQLite database (projects, sessions, runs)
└── logs/
    └── <run_id>/     # Per-run log directories
        ├── stdout.log
        ├── stderr.log
        └── messages.jsonl
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

## Limitations

- **No interactive STDIN**: Once a task starts, you cannot send additional input to the running Claude process. To continue work, wait for completion and use `resume` with a new prompt.
- **Single-process concurrency**: The semaphore only limits concurrency within a single process. Multiple CLI `--background` invocations each run in separate processes.
- **Fire-and-forget execution**: The Claude Agent SDK sends the prompt once via `client.query()` and then only receives responses.
