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

### Bot Interfaces

- `gluon bot` - Run Telegram bot interface
- `gluon discord` - Run Discord bot interface
- `gluon serve --telegram --discord` - Run multiple transports concurrently

## Architecture

### High-Level Overview

```mermaid
graph TB
    subgraph Interfaces
        CLI[CLI - gluon]
        TG[Telegram Bot]
        DC[Discord Bot]
    end

    subgraph Core
        BOTCORE[GluonBotCore]
        ORCH[Orchestrator]
        RUNNER[TaskRunner]
        STORE[GluonStore]
        GIT[GitManager]
        CHAT[ChatAgent]
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
        WEB[Web/URLs]
    end

    CLI --> ORCH
    CLI --> RUNNER
    CLI --> GIT
    TG --> BOTCORE
    DC --> BOTCORE
    BOTCORE --> ORCH
    BOTCORE --> RUNNER
    BOTCORE --> CHAT

    CHAT --> ORCH
    CHAT --> SDK
    CHAT -.->|WebSearch/Fetch| WEB

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
    Project ||--o{ ChannelMapping : "mapped by"
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

    ChannelMapping {
        string id PK
        string transport
        string channel_id UK
        string project_id FK
        string project_name
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
    A[Transport Starts] --> B[recover_stale_runs]
    B --> C{Any active runs<br/>from this transport?}
    C -->|Yes| D[Mark as FAILED<br/>'Bot restarted']
    C -->|No| E[Continue]
    D --> E
    E --> F[Start GitManager<br/>background sync]
    F --> G[Initialize Transport]
    G --> H[Ready for Messages]
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
    subgraph "GluonBotCore (shared)"
        SEM[Semaphore<br/>max_concurrent=16]

        subgraph "Active Tasks"
            T1[Task 1<br/>telegram:123]
            T2[Task 2<br/>discord:456]
            T3[Task 3<br/>telegram:789]
        end

        SEM --> T1
        SEM --> T2
        SEM --> T3
    end

    subgraph "SQLite Store"
        DB[(gluon.db)]
        R1[ExecutionRun abc<br/>initiator: telegram:123]
        R2[ExecutionRun def<br/>initiator: discord:456]
        R3[ExecutionRun ghi<br/>initiator: telegram:789]
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

### Transport Layer Architecture

```mermaid
flowchart TB
    subgraph "Transport Layer"
        ABC[Transport ABC<br/>base.py]

        subgraph "Implementations"
            TG[TelegramTransport<br/>telegram.py]
            DC[DiscordTransport<br/>discord.py]
            FUTURE[Future Transports<br/>slack.py, matrix.py...]
        end

        ABC --> TG
        ABC --> DC
        ABC -.-> FUTURE
    end

    subgraph "Core Components"
        CTX[TransportContext<br/>user_id, chat_id, thread_id]
        MSG[TransportMessage<br/>text, metadata]
        RSP[TransportResponse<br/>text, thread_id]
        CAPS[TransportCapabilities<br/>max_length, threading, editing]
    end

    ABC --> CTX
    ABC --> MSG
    ABC --> RSP
    ABC --> CAPS

    subgraph "Shared Logic"
        CORE[GluonBotCore<br/>bot_core.py]
    end

    TG --> CORE
    DC --> CORE

    style ABC fill:#e1bee7
    style CORE fill:#bbdefb
```

### Discord Bot Flow

```mermaid
sequenceDiagram
    participant User
    participant Discord
    participant DiscordTransport
    participant BotCore
    participant Store
    participant Orchestrator

    User->>Discord: @GluonBot Fix the bug
    Discord->>DiscordTransport: on_message event

    DiscordTransport->>DiscordTransport: Check @mention
    DiscordTransport->>DiscordTransport: Check authorization
    DiscordTransport->>DiscordTransport: Resolve project from channel

    alt No project linked
        DiscordTransport-->>Discord: "Link channel with @GluonBot link <project>"
        Discord-->>User: Prompt to link
    else Project found
        DiscordTransport->>Store: create_run(project_id, prompt, initiator)
        Store-->>DiscordTransport: ExecutionRun

        DiscordTransport-->>Discord: "🚀 Starting task on `project`"
        Discord-->>User: Initial message

        DiscordTransport->>Discord: Create thread from message
        Discord-->>DiscordTransport: thread_id

        DiscordTransport->>BotCore: execute_task(ctx, run, project)

        loop Streaming in Thread
            BotCore-->>DiscordTransport: AgentMessage
            DiscordTransport-->>Discord: Send to thread
            Discord-->>User: Progress in thread
        end

        BotCore-->>DiscordTransport: AgentResult
        DiscordTransport->>Store: update_run(status)
        DiscordTransport->>Discord: Edit original message
        Discord-->>User: ✅ Summary (edited)
    end
```

### Discord Channel Mapping

```mermaid
flowchart TD
    subgraph "Channel Resolution"
        MSG[Incoming Message<br/>#myapp channel]
        MSG --> CHECK1{Explicit mapping<br/>in DB?}

        CHECK1 -->|Yes| FOUND[Use mapped project]
        CHECK1 -->|No| CHECK2{Channel name<br/>matches project?}

        CHECK2 -->|Yes| FOUND
        CHECK2 -->|No| CHECK3{Is thread?<br/>Check parent}

        CHECK3 -->|Yes| PARENT[Check parent channel]
        PARENT --> CHECK1
        CHECK3 -->|No| PROMPT[Prompt user to link]
    end

    subgraph "Link Command"
        LINK["@GluonBot link myproject"]
        LINK --> SAVE[Save to channel_mappings table]
        SAVE --> CACHE[Update local cache]
        CACHE --> CONFIRM["✅ Channel linked"]
    end

    style FOUND fill:#c8e6c9
    style PROMPT fill:#ffecb3
```

### Chat Agent Architecture

```mermaid
flowchart TB
    subgraph "Any Transport"
        MSG[User Message]
        HIST[Message History<br/>last 10 per user]
        CTX[TransportContext]
    end

    subgraph "GluonBotCore"
        NL[process_natural_language]
    end

    subgraph "GluonChatAgent"
        CHAT[chat method]
        SDK[Claude Agent SDK<br/>Haiku model]
    end

    subgraph "Available Tools"
        subgraph "Gluon MCP Tools"
            PROJ[list_projects<br/>list_sessions<br/>get_status]
            TASK[run_task<br/>resume_session]
            WS[add_workspace<br/>list_workspaces<br/>scan_workspace]
            RUN[list_runs<br/>cancel_run]
            GIT[get_git_status]
        end

        subgraph "Built-in Tools"
            FILE[Read, Glob, Grep]
            EXEC[Bash, BashOutput]
            WEB[WebSearch, WebFetch]
        end
    end

    MSG --> NL
    HIST --> NL
    CTX --> NL
    NL --> CHAT
    CHAT --> SDK
    SDK --> PROJ
    SDK --> TASK
    SDK --> WS
    SDK --> RUN
    SDK --> GIT
    SDK --> FILE
    SDK --> EXEC
    SDK --> WEB

    style WEB fill:#4a90d9
    style FILE fill:#88b04b
    style EXEC fill:#ff9900
    style NL fill:#bbdefb
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
- `/resume <project> [session_id] [prompt]` - Resume session (latest or specific)
- `/runs` - List your background runs
- `/runs all` - List all runs
- `/status` - Show overall status
- `/cancel` - Cancel your latest active run
- `/cancel <run_id>` - Cancel specific run
- `/clear` - Clear chat history
- `/help` - Show help

### Features

- **Multiple concurrent tasks**: Run tasks across multiple projects simultaneously
- **Persistent tracking**: All runs are tracked in SQLite and survive bot restarts
- **Global concurrency limit**: Configurable limit (default: 16) prevents resource exhaustion
- **Natural language support**: Chat naturally instead of using commands
- **Conversation context**: Bot remembers recent messages for follow-up questions
- **Reply to resume**: Reply to a completion message to automatically resume that session
- **Real-time updates**: Get progress updates as tasks execute

### Natural Language Examples

Instead of commands, you can chat naturally:

| What you say | What happens |
|--------------|--------------|
| "Show me my projects" | Lists all registered projects |
| "What's the git status of myapp?" | Shows git branch, uncommitted changes, ahead/behind |
| "Run a task on myapp to fix the login bug" | Starts a coding task with Claude |
| "What tasks are running?" | Lists active background runs |
| "Cancel the last task" | Cancels most recent active run |
| "Search the web for React best practices" | Performs web search |
| "Read the README in myapp" | Reads file contents from project |
| "Find all Python files in myapp" | Searches for files by pattern |
| *(reply to completion)* "Also add tests" | Resumes the session with follow-up prompt |

The chat agent has access to:
- **Gluon tools**: Project management, task execution, run monitoring, git status
- **File tools**: Read, Glob, Grep for exploring project code
- **Shell tools**: Bash, BashOutput for running commands
- **Web tools**: WebSearch, WebFetch for internet lookups

## Discord Bot

Run Gluon as a Discord bot with channel-based project mapping and threaded task output.

### Installation

Discord support is an optional dependency:

```bash
pip install 'gluon-agent[discord]'
# or for all optional features
pip install 'gluon-agent[all]'
```

### Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a New Application
3. Go to "Bot" tab and click "Add Bot"
4. Copy the bot token
5. Enable "MESSAGE CONTENT INTENT" in Bot settings
6. Go to "OAuth2" → "URL Generator"
7. Select scopes: `bot`, `applications.commands`
8. Select permissions: `Send Messages`, `Create Public Threads`, `Read Message History`
9. Copy the generated URL and open it to invite the bot to your server

### Run the Bot

```bash
# Set required environment variables
export GLUON_DISCORD_TOKEN="your-bot-token"
export GLUON_DISCORD_GUILD="your-guild-id"

# Optional: restrict to specific users (comma-separated Discord user IDs)
export GLUON_DISCORD_USERS="123456789,987654321"

# Start the bot
gluon discord

# Or pass options directly
gluon discord --token "token" --guild 123456789
```

### Bot Commands

Mention the bot (`@GluonBot`) with a command:

- `@GluonBot link <project>` - Link this channel to a project
- `@GluonBot projects` - List registered projects
- `@GluonBot runs` - List your runs
- `@GluonBot status` - Show overall status
- `@GluonBot cancel [run_id]` - Cancel a run
- `@GluonBot <any task>` - Execute task on the linked project

### Channel-Project Mapping

Discord channels map to projects in two ways:

1. **Auto-match**: Channel name matches project name (e.g., `#myapp` → `myapp` project)
2. **Explicit link**: Use `@GluonBot link <project>` to bind any channel

Once linked, all @mentions execute tasks on that project automatically.

### Threaded Output

Task execution creates Discord threads for organized output:
- Initial message shows task started
- Thread contains streaming progress
- Original message is edited with final summary (✅ or ❌)

```mermaid
flowchart LR
    subgraph "Discord Channel: #myapp"
        MSG1["🚀 Starting task on myapp<br/>Run: abc12345<br/>Status: Running..."]
        MSG1 --> THREAD

        subgraph THREAD["Thread: Run abc12345"]
            P1["Agent: Reading files..."]
            P2["Agent: Making changes..."]
            P3["Agent: Running tests..."]
            P1 --> P2 --> P3
        end
    end

    subgraph "After Completion"
        MSG2["✅ myapp - abc12345<br/><i>Fix the login bug...</i>"]
    end

    MSG1 -.->|edited| MSG2

    style MSG1 fill:#fff9c4
    style MSG2 fill:#c8e6c9
    style THREAD fill:#e3f2fd
```

## Multi-Transport Mode

Run both Telegram and Discord simultaneously with a shared bot core:

```bash
# Set all required environment variables
export GLUON_TELEGRAM_TOKEN="telegram-token"
export GLUON_TELEGRAM_USERS="123456789"
export GLUON_DISCORD_TOKEN="discord-token"
export GLUON_DISCORD_GUILD="987654321"
export GLUON_DISCORD_USERS="111222333"

# Run both transports
gluon serve --telegram --discord
```

### Multi-Transport Architecture

```mermaid
flowchart TB
    subgraph "gluon serve Process"
        CLI[CLI: gluon serve]

        subgraph "Transports (Concurrent)"
            TG[TelegramTransport]
            DC[DiscordTransport]
        end

        subgraph "Shared State"
            CORE[GluonBotCore]
            STORE[(SQLite Store)]
            GIT[GitManager]
            SEM[Semaphore<br/>max_concurrent=16]
        end

        CLI --> TG
        CLI --> DC

        TG --> CORE
        DC --> CORE

        CORE --> STORE
        CORE --> GIT
        CORE --> SEM
    end

    subgraph "External Services"
        TGAPI[Telegram API]
        DCAPI[Discord API]
        BEDROCK[AWS Bedrock]
    end

    TG <--> TGAPI
    DC <--> DCAPI
    CORE -.-> BEDROCK

    style CORE fill:#bbdefb
    style STORE fill:#fff9c4
    style SEM fill:#ffccbc
```

### Cross-Platform Visibility

```mermaid
sequenceDiagram
    participant TGUser as Telegram User
    participant TG as Telegram Transport
    participant Core as GluonBotCore
    participant DC as Discord Transport
    participant DCUser as Discord User

    TGUser->>TG: /run myapp "Fix bug"
    TG->>Core: execute_task(initiator="telegram:123")
    Core->>Core: Store run in SQLite

    Note over Core: Run executes...

    DCUser->>DC: @GluonBot runs
    DC->>Core: format_runs_list()
    Core-->>DC: All runs (including telegram:123)
    DC-->>DCUser: Shows run from Telegram user

    Note over Core: Run completes

    Core-->>TG: AgentResult
    TG-->>TGUser: ✅ Complete
```

### Features

- **Shared project, session, and run state** - All transports read/write to the same SQLite database
- **Shared git background sync** - Single GitManager instance fetches for all transports
- **Shared concurrency limits** - Global semaphore prevents overload across all platforms
- **Cross-platform run visibility** - Users on any platform can see runs from other platforms

## Configuration

### Environment Variables

**Telegram:**
- `GLUON_TELEGRAM_TOKEN` - Telegram bot token
- `GLUON_TELEGRAM_USERS` - Comma-separated list of allowed Telegram user IDs

**Discord:**
- `GLUON_DISCORD_TOKEN` - Discord bot token
- `GLUON_DISCORD_GUILD` - Discord guild (server) ID
- `GLUON_DISCORD_USERS` - Comma-separated list of allowed Discord user IDs

**AWS Bedrock:**
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
    subgraph "Bot Process (any transport)"
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

- **Background**: Bot transports (Telegram, Discord) periodically fetch all projects (every 5 minutes) to maintain status awareness

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
- **Single-process concurrency**: The semaphore only limits concurrency within a single process. Multiple CLI `--background` invocations each run in separate processes. However, `gluon serve` runs all transports in a single process with shared concurrency limits.
- **Fire-and-forget execution**: The Claude Agent SDK sends the prompt once via `client.query()` and then only receives responses.
- **Discord requires optional dependency**: Install with `pip install 'gluon-agent[discord]'` to enable Discord support.
